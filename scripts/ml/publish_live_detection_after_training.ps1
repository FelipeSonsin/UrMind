# Publishes the live-detection browser model only AFTER the YOLOX run has exited.
# It never touches the running training: while it is alive this script only polls
# (one process query every PollSeconds). Then, fail-closed:
#   1. official export (app.ml.serving export, with PyTorch x ONNX parity)
#   2. browser manifest (app.ml.browser_model, status EXPERIMENTAL)
#   3. frontend build (copies public/models into dist)
# No Frozen Test, no registration in the database, no promotion, no deploy.
# Authorization: docs/LIVE_DETECTION.md, section "Autorizacao de uso e distribuicao".
param(
  [int]$PollSeconds = 120,
  [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$RunDir = Join-Path $Root 'models\checkpoints\experimental_20260925_rdd4_10h_r1'
$Contract = Join-Path $Root 'datasets\metadata\yolox_model_experimental_20260925_10h.json'
$Python = Join-Path $Root 'backend\.venv\Scripts\python.exe'
$Serving = Join-Path $Root 'models\serving'
$Log = Join-Path $RunDir 'live_detection_publish.log'
$AuthorizationRef = 'docs/LIVE_DETECTION.md#autorizacao-de-uso-e-distribuicao-25092026'
$env:PYTHONIOENCODING = 'utf-8'

function Write-Log([string]$Message) {
  "$(Get-Date -Format o) $Message" | Out-File -FilePath $Log -Append -Encoding utf8
}
function Get-TrainingProcess {
  Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'app\.ml\.training --run|run_yolox_experimental\.py' }
}
function Invoke-Step([string]$Name, [string[]]$Arguments) {
  Write-Log "START $Name"
  # PS 5.1 turns native stderr (Python logging) into terminating errors under 'Stop';
  # here stderr is just log text and the exit code decides.
  $ErrorActionPreference = 'Continue'
  Push-Location (Join-Path $Root 'backend')
  try {
    & $Python @Arguments 2>&1 | ForEach-Object { "$_" } | Out-File -FilePath $Log -Append -Encoding utf8
    $code = $LASTEXITCODE
  } finally {
    Pop-Location
  }
  Write-Log "END $Name exit=$code"
  return $code
}

$training = @(Get-TrainingProcess)
if ($CheckOnly) {
  Write-Output "training_processes=$($training.Count) run_dir=$(Test-Path $RunDir) python=$(Test-Path $Python) contract=$(Test-Path $Contract)"
  exit 0
}
Write-Log "WAIT training processes=$($training.Count)"
while (@(Get-TrainingProcess).Count -gt 0) { Start-Sleep -Seconds $PollSeconds }

$state = Get-Content (Join-Path $RunDir 'run_state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Log "TRAINING_EXITED status=$($state.status)"
if (-not (Test-Path (Join-Path $RunDir 'best.pt'))) {
  Write-Log 'BLOCKED best.pt ausente: sem checkpoint selecionado em VALIDATION, nada publicado'
  exit 2
}

$modelId = (Get-Content $Contract -Raw -Encoding UTF8 | ConvertFrom-Json).model_id
$startedAt = Get-Date
$exportArgs = @('-m', 'app.ml.serving', 'export', '--checkpoint', 'best', '--contract', $Contract)
$code = Invoke-Step 'export' $exportArgs
if ($code -ne 0) {
  # Metrics from the training run may be missing; recompute on VALIDATION (never TEST).
  $code = Invoke-Step 'export --recompute-validation' ($exportArgs + '--recompute-validation')
}
if ($code -ne 0) {
  Write-Log 'BLOCKED export falhou (paridade, VALIDATION ou contrato): nada publicado'
  exit 3
}

$record = Get-ChildItem $Serving -Filter "$modelId-*.json" |
  Where-Object { $_.LastWriteTime -ge $startedAt } |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $record) {
  Write-Log 'BLOCKED registro de export nao encontrado'
  exit 4
}
$code = Invoke-Step 'browser_model' @(
  '-m', 'app.ml.browser_model', '--manifest', $record.FullName, '--status', 'EXPERIMENTAL',
  '--authorize-use', '--authorize-distribution', '--authorization-ref', $AuthorizationRef
)
if ($code -ne 0) {
  Write-Log 'BLOCKED gerador do manifesto recusou o export'
  exit 5
}

Write-Log 'START frontend build'
$ErrorActionPreference = 'Continue'
Push-Location (Join-Path $Root 'frontend')
try {
  & npm.cmd run build 2>&1 | ForEach-Object { "$_" } | Out-File -FilePath $Log -Append -Encoding utf8
  Write-Log "END frontend build exit=$LASTEXITCODE"
} finally {
  Pop-Location
}
Write-Log 'DONE modelo experimental publicado em frontend/public/models (sem deploy)'

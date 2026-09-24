param(
    [double]$RequiredAvailableGiB = 1.5,
    [int]$GateTimeoutMinutes = 120
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $project "backend\.venv\Scripts\python.exe"
$contract = Join-Path $project "datasets\metadata\yolox_quality_rebuild.json"
$pretrained = Join-Path $project "models\pretrained\yolox_s.pth"
$checkpointDir = Join-Path $project "models\checkpoints\yolox_s_quality_rebuild"
$null = New-Item -ItemType Directory -Path $checkpointDir -Force
$stdout = Join-Path $checkpointDir "training.stdout.log"
$stderr = Join-Path $checkpointDir "training.stderr.log"
$launcherLog = Join-Path $checkpointDir "launcher.log"
$requiredBytes = [uint64]($RequiredAvailableGiB * 1GB)
$deadline = (Get-Date).AddMinutes($GateTimeoutMinutes)

Add-Content -LiteralPath $launcherLog -Encoding UTF8 -Value (
    "{0} quality-rebuild launcher active; required_available_bytes={1}" -f `
        (Get-Date -Format o), $requiredBytes
)

$passed = $false
while ((Get-Date) -lt $deadline) {
    $os = Get-CimInstance Win32_OperatingSystem
    $availableBytes = [uint64]$os.FreePhysicalMemory * 1KB
    if ($availableBytes -ge $requiredBytes) {
        $passed = $true
        break
    }
    Add-Content -LiteralPath $launcherLog -Encoding UTF8 -Value (
        "{0} ram_gate waiting available_bytes={1} required_bytes={2}" -f `
            (Get-Date -Format o), $availableBytes, $requiredBytes
    )
    Start-Sleep -Seconds 30
}

if (-not $passed) {
    Add-Content -LiteralPath $launcherLog -Encoding UTF8 -Value (
        "{0} ram_gate failed; training not started" -f (Get-Date -Format o)
    )
    exit 2
}

Add-Content -LiteralPath $launcherLog -Encoding UTF8 -Value (
    "{0} ram_gate passed available_bytes={1}; starting canonical quality-rebuild trainer" -f `
        (Get-Date -Format o), $availableBytes
)

$arguments = @(
    "-u", "-B", "-m", "app.ml.training", "--run",
    "--contract", ('"' + $contract + '"'),
    "--pretrained", ('"' + $pretrained + '"')
)
$process = Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory (Join-Path $project "backend") `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru `
    -Wait

Add-Content -LiteralPath $launcherLog -Encoding UTF8 -Value (
    "{0} trainer exited code={1}" -f (Get-Date -Format o), $process.ExitCode
)
exit $process.ExitCode

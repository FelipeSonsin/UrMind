[CmdletBinding()]
param(
    [switch]$DryRun,
    [int]$Port = 8000,
    [string]$OutputDirectory = (Join-Path $env:LOCALAPPDATA 'UrMind/fair'),
    [string]$TunnelName,
    [string]$PublicUrl,
    [ValidateRange(0,3600)][int]$RunSeconds = 0
)
$ErrorActionPreference = 'Stop'
$projectDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$backendDirectory = Join-Path $projectDirectory 'backend'
$frontendDirectory = Join-Path $projectDirectory 'frontend'
$python = Join-Path $backendDirectory '.venv/Scripts/python.exe'
$outputPath = [IO.Path]::GetFullPath($OutputDirectory)
if ($outputPath.StartsWith($projectDirectory + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or $outputPath -eq $projectDirectory) {
    throw 'Logs e QR devem ficar fora do repositorio.'
}
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Porta invalida.' }
if ([bool]$TunnelName -ne [bool]$PublicUrl) { throw 'Tunel nomeado exige -TunnelName e -PublicUrl HTTPS.' }
if ($PublicUrl -and ($PublicUrl -notmatch '^https://[a-zA-Z0-9.-]+/?$')) { throw 'PublicUrl deve ser uma origem HTTPS.' }
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$tunnel = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $tunnel) {
    $localTunnel = Join-Path $env:LOCALAPPDATA 'UrMind/tools/cloudflared-2026.9.3.exe'
    if (Test-Path -LiteralPath $localTunnel) {
        if ((Get-FileHash -LiteralPath $localTunnel -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'f096265ec2fcbe9bb6e2d64268db167ced3fcbb83d894bdb9e2fcdb26f2ea7e2') {
            throw 'cloudflared local nao corresponde ao SHA256 registrado.'
        }
        $tunnel = Get-Command $localTunnel
    }
}
Push-Location $backendDirectory
try {
    & $python -c @'
from pathlib import Path
from urllib.parse import urlparse
from dotenv import dotenv_values
import importlib.util
b=dotenv_values('.env'); f=dotenv_values('../frontend/.env.local')
p='impmeitwtusjtwjouggy'
ok=(urlparse(b.get('SUPABASE_URL','')).hostname==p+'.supabase.co' and urlparse(f.get('VITE_SUPABASE_URL','')).hostname==p+'.supabase.co' and p in b.get('DATABASE_POOLER_URL',''))
if not ok: raise SystemExit('Ambiente nao confirmado como Urmind DEV; nenhum valor exibido.')
for key in ('SUPABASE_PUBLISHABLE_KEY','SUPABASE_SECRET_KEY','DATABASE_POOLER_URL'):
    if not b.get(key): raise SystemExit('Configuracao obrigatoria ausente: '+key)
if not f.get('VITE_SUPABASE_PUBLISHABLE_KEY'): raise SystemExit('VITE_SUPABASE_PUBLISHABLE_KEY ausente')
print('ENVIRONMENT_ALIGNMENT=impm...ggy; arquivos .env somente leitura')
print('QR_DEPENDENCY='+('AVAILABLE' if importlib.util.find_spec('qrcode') else 'MISSING: pip install qrcode[pil]'))
'@
    if ($LASTEXITCODE -ne 0) { throw 'Validacao do ambiente falhou.' }
} finally { Pop-Location }
if ($DryRun) {
    if (-not $tunnel) { Write-Warning 'cloudflared nao instalado: tunel HTTPS ainda indisponivel.' }
    Write-Output 'DRY_RUN: nenhuma API, Worker, imagem, QR ou tunel iniciado; nenhum Auth alterado.'
    return
}
if (-not $tunnel) { throw 'Instale cloudflared pelo canal oficial antes de iniciar.' }
& $python -c 'import qrcode'
if ($LASTEXITCODE -ne 0) { throw 'Dependencia QR ausente; instale qrcode[pil] no ambiente backend.' }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw 'Porta em uso; nenhum processo existente sera encerrado.' }
$runDirectory = Join-Path $outputPath ([Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
$previousApi = $env:VITE_API_BASE_URL
$previousServe = $env:SERVE_FRONTEND_DIR
$apiProcess = $null
$workerProcess = $null
$tunnelProcess = $null
try {
    $env:VITE_API_BASE_URL = '/api/v1'
    Push-Location $frontendDirectory
    try {
        & $npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Build falhou.' }
    } finally { Pop-Location }
    $env:SERVE_FRONTEND_DIR = Join-Path $frontendDirectory 'dist'
    $apiProcess = Start-Process -FilePath $python -ArgumentList @('-m','app','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $backendDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDirectory 'api.log') -RedirectStandardError (Join-Path $runDirectory 'api-error.log')
    $deadline = (Get-Date).AddSeconds(45)
    $ready = $false
    while ((Get-Date) -lt $deadline -and -not $apiProcess.HasExited) {
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 3
            $readiness = Invoke-RestMethod "http://127.0.0.1:$Port/api/v1/ready" -TimeoutSec 3
            if ($readiness.status -eq 'ready' -and $health.database -eq 'connected') { $ready = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw 'API nao ficou ready; consulte os logs locais.' }
    $workerProcess = Start-Process -FilePath $python -ArgumentList @('-m','app.worker') -WorkingDirectory $backendDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDirectory 'worker.log') -RedirectStandardError (Join-Path $runDirectory 'worker-error.log')
    $tunnelArgs = if ($TunnelName) { @('tunnel','run',$TunnelName) } else { @('tunnel','--url',"http://127.0.0.1:$Port") }
    $tunnelLog = Join-Path $runDirectory 'tunnel-error.log'
    $tunnelProcess = Start-Process -FilePath $tunnel.Source -ArgumentList $tunnelArgs -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDirectory 'tunnel.log') -RedirectStandardError $tunnelLog
    $deadline = (Get-Date).AddSeconds(60)
    while (-not $PublicUrl -and (Get-Date) -lt $deadline -and -not $tunnelProcess.HasExited) {
        if (Test-Path -LiteralPath $tunnelLog) {
            $logText = Get-Content -LiteralPath $tunnelLog -Raw
            if (-not [string]::IsNullOrEmpty($logText)) {
                $match = [regex]::Match($logText, 'https://[a-z0-9-]+\.trycloudflare\.com')
                if ($match.Success) { $PublicUrl = $match.Value }
            }
        }
        if (-not $PublicUrl) { Start-Sleep -Seconds 1 }
    }
    if (-not $PublicUrl) { throw 'Tunel nao forneceu URL HTTPS.' }
    & $python -c 'import qrcode,sys;qrcode.make(sys.argv[1]).save(sys.argv[2])' ($PublicUrl.TrimEnd('/') + '/#/') (Join-Path $runDirectory 'fair_qr.png')
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao gerar QR.' }
    Write-Output "URL: $PublicUrl/#/"
    Write-Output "QR/logs locais: $runDirectory"
    Write-Output 'Adicione manualmente esta URL nas Redirect URLs do Supabase DEV. Ctrl+C encerra somente os processos deste script.'
    $stopAt = if ($RunSeconds) { (Get-Date).AddSeconds($RunSeconds) } else { [DateTime]::MaxValue }
    while (-not $apiProcess.HasExited -and -not $workerProcess.HasExited -and -not $tunnelProcess.HasExited) {
        if ((Get-Date) -ge $stopAt) { Write-Output 'Sessao de verificacao encerrada conforme RunSeconds.'; return }
        Start-Sleep -Seconds 2
    }
    throw 'Um servico encerrou. A sessao sera desligada; corrija a causa nos logs e execute novamente.'
} finally {
    foreach ($process in @($tunnelProcess,$workerProcess,$apiProcess)) {
        if ($null -ne $process -and -not $process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
    }
    $env:VITE_API_BASE_URL = $previousApi
    $env:SERVE_FRONTEND_DIR = $previousServe
}

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Start', 'Stop', 'Status')][string]$Action,
    [string]$StateDirectory = (Join-Path $env:LOCALAPPDATA 'UrMind/worker'),
    [ValidateRange(5, 600)][int]$TimeoutSeconds = 45,
    [switch]$Force
)
# Supervised local Worker for the pilot (API on Render, Worker on this machine).
# start_fair.ps1 remains the all-in-one fair launcher; this script manages only
# the Worker. State and logs stay outside the repository. No secret is printed.
$ErrorActionPreference = 'Stop'
$projectDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$backendDirectory = Join-Path $projectDirectory 'backend'
$python = Join-Path $backendDirectory '.venv/Scripts/python.exe'
$stateDirectory = [IO.Path]::GetFullPath($StateDirectory)
if ($stateDirectory.StartsWith($projectDirectory, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'O estado do Worker deve ficar fora do repositorio.'
}
$pidFile = Join-Path $stateDirectory 'worker.pid.json'
$heartbeatFile = Join-Path $stateDirectory 'heartbeat.json'
$stopFile = Join-Path $stateDirectory 'stop.request'

function Get-WorkerProcesses {
    $all = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'python.exe' -and $_.CommandLine -match '(^|\s)-m\s+app\.worker(\s|$)'
    })
    # The venv launcher spawns a child interpreter: count each tree once.
    # Callers wrap this in @(...): PowerShell unrolls a one-element array.
    $ids = @($all | ForEach-Object { $_.ProcessId })
    @($all | Where-Object { $ids -notcontains $_.ParentProcessId })
}

function Get-Heartbeat {
    if (-not (Test-Path -LiteralPath $heartbeatFile)) { return $null }
    try { Get-Content -LiteralPath $heartbeatFile -Raw | ConvertFrom-Json } catch { $null }
}

function Get-OwnedProcess {
    if (-not (Test-Path -LiteralPath $pidFile)) { return $null }
    $record = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $live = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    # A recycled PID is not our Worker: the start time must match exactly.
    if ($null -ne $live -and $live.StartTime.ToString('o') -eq $record.start_time) { return $live }
    return $null
}

switch ($Action) {
    'Status' {
        $processes = @(Get-WorkerProcesses)
        $heartbeat = Get-Heartbeat
        $age = if ($heartbeat) {
            [int]((Get-Date).ToUniversalTime() - [DateTime]::Parse($heartbeat.heartbeat_at).ToUniversalTime()).TotalSeconds
        } else { $null }
        [pscustomobject]@{
            workers_running          = $processes.Count
            supervised_pid           = if (Get-OwnedProcess) { (Get-OwnedProcess).Id } else { $null }
            heartbeat_age_seconds    = $age
            heartbeat_pid_alive      = [bool]($heartbeat -and (Get-Process -Id $heartbeat.pid -ErrorAction SilentlyContinue))
            heartbeat_stale          = ($null -eq $age -or $age -gt 60 -or -not ($heartbeat -and (Get-Process -Id $heartbeat.pid -ErrorAction SilentlyContinue)))
            jobs_processed           = $heartbeat.jobs_processed
            iteration_errors         = $heartbeat.iteration_errors
            last_job_at              = $heartbeat.last_job_at
            last_error               = $heartbeat.last_error
            model_version_id         = $heartbeat.model_version_id
            inference_profile_sha256 = $heartbeat.inference_profile_sha256
            logs                     = $stateDirectory
        } | Format-List
    }
    'Start' {
        # Check-then-start must be atomic across concurrent invocations.
        $mutex = New-Object System.Threading.Mutex($false, 'Local\UrMindWorkerStart')
        if (-not $mutex.WaitOne(0)) { throw 'Outro Start do Worker esta em andamento.' }
        try {
        $existing = @(Get-WorkerProcesses)
        if ($existing.Count -gt 0) {
            throw "Ja existe Worker ativo (PID $($existing.ProcessId -join ', ')). Use -Action Status ou -Action Stop; nenhum segundo Worker foi iniciado."
        }
        New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
        Remove-Item -LiteralPath $stopFile, $heartbeatFile -ErrorAction SilentlyContinue
        $previous = $env:URMIND_WORKER_STATE_DIR
        try {
            $env:URMIND_WORKER_STATE_DIR = $stateDirectory
            $process = Start-Process -FilePath $python -ArgumentList @('-m', 'app.worker') `
                -WorkingDirectory $backendDirectory -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $stateDirectory 'worker.log') `
                -RedirectStandardError (Join-Path $stateDirectory 'worker-error.log')
        } finally { $env:URMIND_WORKER_STATE_DIR = $previous }
        @{ pid = $process.Id; start_time = $process.StartTime.ToString('o') } |
            ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while ((Get-Date) -lt $deadline -and -not $process.HasExited) {
            if (Get-Heartbeat) { Write-Output "Worker supervisionado ativo: PID $($process.Id); logs em $stateDirectory"; return }
            Start-Sleep -Seconds 1
        }
        if ($process.HasExited) { throw "Worker encerrou ao iniciar; consulte $stateDirectory/worker-error.log" }
        throw 'Worker iniciou mas nao produziu heartbeat no prazo; verifique os logs.'
        } finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
    }
    'Stop' {
        $owned = Get-OwnedProcess
        if ($null -eq $owned) {
            $others = @(Get-WorkerProcesses)
            if ($others.Count -gt 0) {
                throw 'Ha Worker ativo nao iniciado por este script; encerre-o no terminal de origem.'
            }
            Write-Output 'Nenhum Worker supervisionado em execucao.'
            return
        }
        Set-Content -LiteralPath $stopFile -Value (Get-Date).ToString('o') -Encoding utf8
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while ((Get-Date) -lt $deadline -and -not $owned.HasExited) {
            Start-Sleep -Seconds 1
            $owned.Refresh()
        }
        if (-not $owned.HasExited) {
            if (-not $Force) { throw 'Worker nao encerrou entre jobs no prazo; repita com -Force para encerrar a arvore deste PID.' }
            & taskkill.exe /PID $owned.Id /T /F 2>$null | Out-Null
        }
        Remove-Item -LiteralPath $stopFile, $pidFile -ErrorAction SilentlyContinue
        Write-Output 'Worker supervisionado encerrado.'
    }
}

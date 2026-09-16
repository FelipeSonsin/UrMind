[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repositoryRoot "backend"
$metadataPath = Join-Path $backendRoot "ml-stack.json"
$modelMetadataPath = Join-Path $repositoryRoot "datasets\metadata\yolox_model_v1.json"
$lockPath = Join-Path $backendRoot "requirements-ml-cu128.txt"
$yoloxRoot = Join-Path $backendRoot "third_party\YOLOX"
$junctionPath = Join-Path $backendRoot ".venv"
$metadata = Get-Content -Raw -LiteralPath $metadataPath | ConvertFrom-Json
$modelMetadata = Get-Content -Raw -LiteralPath $modelMetadataPath | ConvertFrom-Json

function Assert-LastExitCode([string]$operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$operation falhou com exit code $LASTEXITCODE."
    }
}

function Get-NormalizedPath([string]$path, [string]$basePath = $repositoryRoot) {
    $absolutePath = if ([System.IO.Path]::IsPathRooted($path)) {
        [System.IO.Path]::GetFullPath($path)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $basePath $path))
    }
    if (Test-Path -LiteralPath $absolutePath) {
        $absolutePath = (Resolve-Path -LiteralPath $absolutePath).Path
    }
    return $absolutePath.TrimEnd([System.IO.Path]::DirectorySeparatorChar)
}

function Get-ExistingEnvironmentTarget([System.IO.FileSystemInfo]$environmentItem) {
    if (-not $environmentItem.PSIsContainer) {
        throw "backend/.venv existe, mas não é um diretório. Nenhum ambiente foi criado."
    }
    if ($environmentItem.LinkType -in @("Junction", "SymbolicLink")) {
        if (-not $environmentItem.Target -or -not $environmentItem.Target[0]) {
            throw "backend/.venv é um link sem target resolvível. Nenhum ambiente foi criado."
        }
        return Get-NormalizedPath ([string]$environmentItem.Target[0]) $environmentItem.Parent.FullName
    }
    return Get-NormalizedPath $environmentItem.FullName
}

function Test-EnvironmentStack([string]$targetPath) {
    $candidatePython = Join-Path $targetPath "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $candidatePython -PathType Leaf)) {
        return $false
    }

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $candidatePython -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" *> $null
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
        & $candidatePython -m pip --version *> $null
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
        & $candidatePython -m pip check *> $null
        if ($LASTEXITCODE -ne 0) {
            return $false
        }

    $validationCode = @'
import importlib.metadata as md
import json
import sys
from pathlib import Path

metadata = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
yolox_root = Path(sys.argv[3]).resolve()
import cv2
import dvc
import mlflow
import pycocotools
import torch
import torchvision
import yolox
from app.ml.yolox_model import instantiate_model

assert sys.version_info[:2] == (3, 12)
assert md.version("pip") == metadata["pip"]
assert md.version("setuptools") == metadata["setuptools"]
assert md.version("wheel") == metadata["wheel"]
assert torch.__version__ == metadata["torch"]
assert torchvision.__version__ == metadata["torchvision"]
assert torch.version.cuda == metadata["pytorch_cuda_runtime"]
assert md.version("opencv-python") == metadata["opencv_python"]
assert md.version("pycocotools") == metadata["pycocotools"]
assert md.version("mlflow-skinny") == metadata["mlflow_skinny"]
assert md.version("dvc") == metadata["dvc"]
assert Path(yolox.__file__).resolve().is_relative_to(yolox_root)
assert torch.cuda.is_available()
model = instantiate_model()
assert model.backbone is not None
assert model.head is not None
'@
        $encodedValidation = [System.Convert]::ToBase64String(
            [System.Text.Encoding]::UTF8.GetBytes($validationCode)
        )
        & $candidatePython -B -c "import base64,sys;exec(base64.b64decode(sys.argv[1]))" $encodedValidation $metadataPath $yoloxRoot *> $null
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Find-Python312 {
    $candidates = [System.Collections.Generic.List[string]]::new()
    if ($env:URMIND_PYTHON312) {
        $candidates.Add($env:URMIND_PYTHON312)
    }

    if ($env:LOCALAPPDATA) {
        $defaultUserInstall = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
        $candidates.Add($defaultUserInstall)
    }

    foreach ($registryRoot in @("HKCU:\Software\Python\PythonCore", "HKLM:\Software\Python\PythonCore")) {
        foreach ($installPath in Get-ItemProperty "$registryRoot\3.12*\InstallPath" -ErrorAction SilentlyContinue) {
            if ($installPath.ExecutablePath) {
                $candidates.Add($installPath.ExecutablePath)
            }
        }
    }

    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $launcherPython = & $launcher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $launcherPython) {
            $candidates.Add($launcherPython.Trim())
        }
    }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            continue
        }
        $version = & $candidate -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
        if ($LASTEXITCODE -eq 0 -and $version.Trim() -eq "3.12") {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "Python 3.12 oficial não encontrado. Instale-o ou defina URMIND_PYTHON312."
}

git -C $repositoryRoot submodule update --init --recursive -- backend/third_party/YOLOX
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao inicializar o submodule YOLOX."
}
$actualCommit = (git -C $yoloxRoot rev-parse HEAD).Trim()
Assert-LastExitCode "Leitura do commit YOLOX"
if ($actualCommit -ne $modelMetadata.source_commit) {
    throw "Commit YOLOX divergente: esperado $($modelMetadata.source_commit), obtido $actualCommit."
}

$environmentItem = Get-Item -LiteralPath $junctionPath -Force -ErrorAction SilentlyContinue
$requestedTarget = if ($env:URMIND_VENV_TARGET) {
    Get-NormalizedPath $env:URMIND_VENV_TARGET
} else {
    $null
}

if ($environmentItem) {
    $venvTarget = Get-ExistingEnvironmentTarget $environmentItem
    if ($requestedTarget -and -not $venvTarget.Equals(
        $requestedTarget, [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "URMIND_VENV_TARGET diverge do ambiente ativo em $venvTarget. Nenhum ambiente foi criado."
    }
    if (-not (Test-EnvironmentStack $venvTarget)) {
        throw "backend/.venv aponta para um ambiente inválido em $venvTarget. Nenhum ambiente alternativo foi criado."
    }
    Write-Output "REUSE_EXISTING_ENVIRONMENT: $venvTarget"
} else {
    $venvTarget = $requestedTarget
    if (-not $venvTarget) {
        if (-not $env:LOCALAPPDATA) {
            throw "LOCALAPPDATA não está definido; use URMIND_VENV_TARGET."
        }
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            $repositoryBytes = [System.Text.Encoding]::UTF8.GetBytes(
                (Get-NormalizedPath $repositoryRoot).ToLowerInvariant()
            )
            $repositoryHash = [System.BitConverter]::ToString(
                $sha256.ComputeHash($repositoryBytes)
            ).Replace("-", "").Substring(0, 12).ToLowerInvariant()
        } finally {
            $sha256.Dispose()
        }
        $venvTarget = Get-NormalizedPath (
            Join-Path $env:LOCALAPPDATA "UrMind\venvs\$repositoryHash-py312"
        )
    }

    if (Test-Path -LiteralPath $venvTarget) {
        if (-not (Test-EnvironmentStack $venvTarget)) {
            throw "O target existente $venvTarget não é uma stack UrMind válida. Nenhum ambiente foi criado."
        }
        New-Item -ItemType Junction -Path $junctionPath -Target $venvTarget | Out-Null
        Write-Output "REUSE_EXISTING_ENVIRONMENT: $venvTarget"
    } else {
        $python312 = Find-Python312
        New-Item -ItemType Directory -Path (Split-Path -Parent $venvTarget) -Force | Out-Null
        try {
            & $python312 -m venv $venvTarget
            Assert-LastExitCode "Criação do ambiente virtual"
            $venvPython = Join-Path $venvTarget "Scripts\python.exe"
            & $venvPython -m pip install "pip==$($metadata.pip)" "setuptools==$($metadata.setuptools)" "wheel==$($metadata.wheel)"
            Assert-LastExitCode "Instalação das ferramentas de build"
            & $venvPython -m pip install -e "${backendRoot}[dev,mlops]" -r (Join-Path $repositoryRoot "scripts\datasets\requirements.txt")
            Assert-LastExitCode "Instalação das dependências base e de desenvolvimento"
            & $venvPython -m pip install --no-deps -r $lockPath
            Assert-LastExitCode "Instalação do lock ML"
            & $venvPython -m pip install --no-deps -e $backendRoot
            Assert-LastExitCode "Instalação editável do projeto"
            if (-not (Test-EnvironmentStack $venvTarget)) {
                throw "A stack recém-instalada não passou na validação."
            }
            New-Item -ItemType Junction -Path $junctionPath -Target $venvTarget | Out-Null
            Write-Output "CREATED_OFFICIAL_ENVIRONMENT: $venvTarget"
        } catch {
            if (Test-Path -LiteralPath $venvTarget) {
                Remove-Item -LiteralPath $venvTarget -Recurse -Force
            }
            throw
        }
    }
}

Write-Output "UrMind YOLOX foundation ready at $junctionPath"

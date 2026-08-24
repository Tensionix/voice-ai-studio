[CmdletBinding()]
param(
    [ValidateSet("auto", "directml", "cuda", "cpu")]
    [string]$Provider = "auto",
    [switch]$SkipPayload
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$InstallDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $PSCommandPath }
$Root = (Resolve-Path (Join-Path $InstallDir "..")).Path
$Python = Join-Path $Root "runtime\python.exe"
$Tools = Join-Path $Root "Tools\gigaam"
$Models = Join-Path $Root "models"
$HfHome = Join-Path $Models "huggingface"
$Tmp = Join-Path $InstallDir "download\tmp"
$Marker = Join-Path $Tools "audion-gigaam-onnx-pack.txt"
$StepTotal = 7

function Write-Step {
    param(
        [int]$Step,
        [string]$Label
    )
    Write-Host ("[audion-step] {0}/{1} {2}" -f $Step, $StepTotal, $Label)
}

function Invoke-Pip {
    param(
        [string[]]$Packages,
        [string]$WheelDir
    )
    $args = @("install", "--progress-bar", "raw", "--no-warn-script-location", "--upgrade")
    $hasWheelCache = $false
    if ($WheelDir -and (Test-Path -LiteralPath $WheelDir)) {
        $hasWheelCache = @(Get-ChildItem -LiteralPath $WheelDir -Filter "*.whl" -File -ErrorAction SilentlyContinue).Count -gt 0
    }
    if ($hasWheelCache) {
        Write-Host "Using local wheel cache: $WheelDir"
        $args += @("--no-index", "--find-links", $WheelDir)
    }
    $args += $Packages
    & $Python -m pip @args
    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed: $($Packages -join ' ')"
    }
}

function Reset-OnnxRuntimePackages {
    $packages = @(
        "onnxruntime",
        "onnxruntime-directml",
        "onnxruntime-gpu",
        "nvidia-cublas-cu12",
        "nvidia-cuda-nvrtc-cu12",
        "nvidia-cuda-runtime-cu12",
        "nvidia-cudnn-cu12",
        "nvidia-cufft-cu12",
        "nvidia-curand-cu12",
        "nvidia-nvjitlink-cu12"
    )
    & $Python -m pip uninstall -y @packages
}

function Invoke-PythonCode {
    param(
        [string]$Name,
        [string]$Code
    )
    New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
    $script = Join-Path $Tmp $Name
    Set-Content -LiteralPath $script -Encoding UTF8 -Value $Code
    & $Python $script
    if ($LASTEXITCODE -ne 0) {
        throw "Python helper failed: $script"
    }
}

function Get-GpuNames {
    $names = @()
    try {
        if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
            $names += (& nvidia-smi -L 2>$null)
        }
    } catch {
    }
    try {
        $names += Get-CimInstance Win32_VideoController -ErrorAction Stop |
            Select-Object -ExpandProperty Name
    } catch {
    }
    return @($names | Where-Object { $_ } | Select-Object -Unique)
}

function Get-ProjectEdition {
    $settingsFile = Join-Path $Root "config\app_settings.yaml"
    if (Test-Path -LiteralPath $settingsFile) {
        try {
            $editionLine = Get-Content -LiteralPath $settingsFile |
                Where-Object { $_ -match "^\s*edition\s*:" } |
                Select-Object -First 1
            if ($editionLine -match ":\s*(studio|pro|plus|cuda)\s*$") {
                return "studio"
            }
            if ($editionLine -match ":\s*live\s*$") {
                return "live"
            }
        } catch {
        }
    }
    $rootName = (Split-Path -Leaf $Root).ToLowerInvariant()
    if ($rootName -match "studio|pro|plus|cuda") {
        return "studio"
    }
    return "live"
}

function Resolve-ProviderPackage {
    param([string]$Requested)
    $selected = $Requested.ToLowerInvariant()
    if ($selected -eq "auto") {
        $gpuText = ((Get-GpuNames) -join " ").ToLowerInvariant()
        $edition = Get-ProjectEdition
        if ($IsWindows -or $env:OS -like "*Windows*") {
            if ($edition -eq "studio" -and $gpuText -match "nvidia") {
                $selected = "cuda"
            } else {
                $selected = "directml"
            }
        } else {
            $selected = "cpu"
        }
    }

    switch ($selected) {
        "directml" {
            return [pscustomobject]@{
                Kind = "directml"
                Package = @("onnxruntime-directml==1.24.4", "numpy==2.3.5")
                Providers = @("DmlExecutionProvider", "CPUExecutionProvider")
            }
        }
        "cuda" {
            return [pscustomobject]@{
                Kind = "cuda"
                Package = @(
                    "onnxruntime-gpu[cuda,cudnn]==1.22.0",
                    "nvidia-cuda-runtime-cu12==12.9.79",
                    "nvidia-cuda-nvrtc-cu12==12.9.86",
                    "nvidia-cublas-cu12==12.9.2.10",
                    "nvidia-cufft-cu12==11.4.1.4",
                    "nvidia-curand-cu12==10.3.10.19",
                    "nvidia-nvjitlink-cu12==12.9.86",
                    "nvidia-cudnn-cu12==9.24.0.43"
                )
                Providers = @("CUDAExecutionProvider", "CPUExecutionProvider")
            }
        }
        default {
            return [pscustomobject]@{
                Kind = "cpu"
                Package = @("onnxruntime", "numpy==2.3.5")
                Providers = @("CPUExecutionProvider")
            }
        }
    }
}

function Get-FallbackProvider {
    param([string]$FailedKind)
    if (($IsWindows -or $env:OS -like "*Windows*") -and $FailedKind -ne "directml") {
        return "directml"
    }
    return "cpu"
}

function Test-GigaAMPayloadCache {
    $snapshotDir = Join-Path $HfHome "hub\models--istupakov--gigaam-v3-onnx\snapshots"
    return (Test-Path -LiteralPath $snapshotDir)
}

function Install-OnnxRuntimeProvider {
    param($ProviderSpec)
    Reset-OnnxRuntimePackages
    $wheelDir = Join-Path $InstallDir ("wheels\" + $ProviderSpec.Kind)
    Invoke-Pip -Packages @($ProviderSpec.Package) -WheelDir $wheelDir
}

function Verify-OnnxRuntimeProvider {
    param($ProviderSpec)
    $providerCsv = ($ProviderSpec.Providers -join ",")
$verifyCode = @"
import ctypes
import contextlib
import importlib.util
import io
import os
from pathlib import Path

import onnxruntime as ort

available = set(ort.get_available_providers())
desired = [item.strip() for item in "$providerCsv".split(",") if item.strip()]

def quiet_preload_site_packages(preload):
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            preload(directory="")
        print("NVIDIA site-packages preload: OK")
    except TypeError:
        pass
    except Exception as exc:
        print("NVIDIA site-packages preload warning:", exc)

if "CUDAExecutionProvider" in desired:
    preload = getattr(ort, "preload_dlls", None)
    if callable(preload):
        try:
            preload(cuda=True, cudnn=True, msvc=True)
            print("CUDA/cuDNN/MSVC preload: OK")
        except Exception as exc:
            print("CUDA/cuDNN/MSVC preload failed:", exc)
        quiet_preload_site_packages(preload)

def provider_loadable(provider):
    if provider not in available:
        return False
    provider_dlls = {
        "CUDAExecutionProvider": "onnxruntime_providers_cuda.dll",
    }
    dll_name = provider_dlls.get(provider)
    if dll_name:
        dll = Path(ort.__file__).resolve().parent / "capi" / dll_name
        try:
            ctypes.WinDLL(str(dll))
        except OSError as exc:
            print(f"{provider} DLL failed:", exc)
            return False
    return True

loadable = [provider for provider in desired if provider_loadable(provider)]
if "CPUExecutionProvider" in available and "CPUExecutionProvider" not in loadable:
    loadable.append("CPUExecutionProvider")

accelerators = [item for item in desired if item != "CPUExecutionProvider"]
print("onnx_asr:", "OK" if importlib.util.find_spec("onnx_asr") else "missing")
print("onnxruntime:", ort.__version__)
print("providers:", ", ".join(sorted(available)))
print("loadable providers:", ", ".join(loadable))
if accelerators and not any(item in loadable for item in accelerators):
    raise SystemExit("No requested accelerator provider is loadable: " + ", ".join(accelerators))
"@
    Invoke-PythonCode -Name "verify_gigaam_onnx.py" -Code $verifyCode
}

Write-Host "[Audion Voice AI] Installing GigaAM ONNX pack..."
Write-Host "Root: $Root"
Write-Host ""

Write-Step 1 "Check portable Python runtime"
if (-not (Test-Path -LiteralPath $Python)) {
    $fallback = Get-Command python -ErrorAction SilentlyContinue
    if (-not $fallback) {
        throw "Portable Python was not found at $Python and no system python is on PATH."
    }
    $Python = $fallback.Source
}
& $Python --version
if ($LASTEXITCODE -ne 0) {
    throw "Python runtime check failed."
}

New-Item -ItemType Directory -Force -Path $Tools, $Models, $HfHome, $Tmp | Out-Null
$env:HF_HOME = $HfHome
$env:HF_HUB_CACHE = Join-Path $HfHome "hub"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"

Write-Step 2 "Upgrade pip"
Invoke-Pip -Packages @("pip")

Write-Step 3 "Install onnx-asr + Hugging Face Hub"
$commonWheelDir = Join-Path $InstallDir "wheels\common"
Invoke-Pip -Packages @("onnx-asr>=0.11.0", "huggingface-hub>=1.0") -WheelDir $commonWheelDir

$providerSpec = Resolve-ProviderPackage $Provider
$attemptedProviders = @()
while ($true) {
    $attemptedProviders += $providerSpec.Kind
    Write-Step 4 "Install ONNX Runtime provider: $($providerSpec.Kind)"
    try {
        Install-OnnxRuntimeProvider $providerSpec
        Write-Step 5 "Verify ONNX Runtime providers"
        Verify-OnnxRuntimeProvider $providerSpec
        break
    } catch {
        if ($providerSpec.Kind -eq "cpu") {
            throw
        }
        Write-Host "Provider failed: $($providerSpec.Kind)"
        Write-Host $_
        $fallbackProvider = Get-FallbackProvider $providerSpec.Kind
        if ($attemptedProviders -contains $fallbackProvider) {
            $fallbackProvider = "cpu"
        }
        Write-Host "Falling back to $fallbackProvider."
        $providerSpec = Resolve-ProviderPackage $fallbackProvider
    }
}
$providerCsv = ($providerSpec.Providers -join ",")

if (-not $SkipPayload) {
    Write-Step 6 "Preload GigaAM v3 payloads"
    $preloadCode = @"
import ctypes
import contextlib
import io
import os
from pathlib import Path

os.environ["HF_HOME"] = r"$HfHome"
os.environ["HF_HUB_CACHE"] = str(Path(r"$HfHome") / "hub")
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import onnx_asr
import onnxruntime as ort

desired = [item.strip() for item in "$providerCsv".split(",") if item.strip()]
def quiet_preload_site_packages(preload):
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            preload(directory="")
        print("NVIDIA site-packages preload: OK")
    except TypeError:
        pass
    except Exception as exc:
        print("NVIDIA site-packages preload warning:", exc)

if "CUDAExecutionProvider" in desired:
    preload = getattr(ort, "preload_dlls", None)
    if callable(preload):
        try:
            preload(cuda=True, cudnn=True, msvc=True)
            print("CUDA/cuDNN/MSVC preload: OK")
        except Exception as exc:
            print("CUDA/cuDNN/MSVC preload failed:", exc)
        quiet_preload_site_packages(preload)
available = set(ort.get_available_providers())
def provider_loadable(provider):
    if provider not in available:
        return False
    provider_dlls = {
        "CUDAExecutionProvider": "onnxruntime_providers_cuda.dll",
    }
    dll_name = provider_dlls.get(provider)
    if dll_name:
        dll = Path(ort.__file__).resolve().parent / "capi" / dll_name
        try:
            ctypes.WinDLL(str(dll))
        except OSError as exc:
            print(f"{provider} DLL failed:", exc)
            return False
    return True

providers = [item for item in desired if provider_loadable(item)]
if "CPUExecutionProvider" in available and "CPUExecutionProvider" not in providers:
    providers.append("CPUExecutionProvider")

def onnx_asr_kwargs():
    return {"providers": list(providers)} if providers else {}

print("selected providers:", ", ".join(providers) or "onnxruntime default")
for model_name in ("gigaam-v3-e2e-ctc", "gigaam-v3-e2e-rnnt"):
    print(f"GigaAM payload: {model_name}")
    onnx_asr.load_model(model_name, **onnx_asr_kwargs())
print("GigaAM payloads OK")
"@
    Invoke-PythonCode -Name "preload_gigaam_payloads.py" -Code $preloadCode
} else {
    Write-Step 6 "Skip GigaAM payload preload"
}

Write-Step 7 "Write install marker"
$providerList = $providerSpec.Providers -join ";"
$packageList = $providerSpec.Package -join ";"
$payloadPreloaded = ((-not $SkipPayload) -or (Test-GigaAMPayloadCache))
Set-Content -LiteralPath $Marker -Encoding UTF8 -Value @(
    "kind=$($providerSpec.Kind)",
    "package=$packageList",
    "providers=$providerList",
    "hf_home=$HfHome",
    "payload_preloaded=$payloadPreloaded",
    "installed_at=$((Get-Date).ToString('s'))"
)

Write-Host "Marker: $Marker"
Write-Host "HF_HOME: $HfHome"
Write-Host "GigaAM ONNX pack ready."

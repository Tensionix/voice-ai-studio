[CmdletBinding()]
param(
    [string[]]$Provider = @(),
    [switch]$IncludeCuda
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$InstallDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $PSCommandPath }
$Root = (Resolve-Path (Join-Path $InstallDir "..")).Path
$WheelRoot = Join-Path $Root "wheelhouse"
$Tmp = Join-Path $InstallDir "download\tmp\wheel-cache"
$PortablePython = Join-Path $Root "runtime\python.exe"

function Get-ProjectEdition {
    $settingsFile = Join-Path $Root "config\app_settings.yaml"
    if (Test-Path -LiteralPath $settingsFile) {
        try {
            $editionLine = Get-Content -LiteralPath $settingsFile |
                Where-Object { $_ -match "^\s*edition\s*:" } |
                Select-Object -First 1
            if ($editionLine -match ":\s*(studio|pro|plus|cuda)\s*$") { return "studio" }
            if ($editionLine -match ":\s*live\s*$") { return "live" }
        } catch {
        }
    }
    $rootName = (Split-Path -Leaf $Root).ToLowerInvariant()
    if ($rootName -match "studio|pro|plus|cuda") { return "studio" }
    return "live"
}

function Get-PythonCommand {
    if (Test-Path -LiteralPath $PortablePython) {
        return @($PortablePython)
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @($py.Source, "-3.12")
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }
    throw "Python was not found. Run Build_Portable_Env.cmd first or install Python 3.12."
}

function Invoke-Python {
    param([string[]]$Arguments)
    $cmd = @(Get-PythonCommand)
    $exe = $cmd[0]
    $prefix = @()
    if ($cmd.Count -gt 1) {
        $prefix = $cmd[1..($cmd.Count - 1)]
    }
    & $exe @prefix @Arguments
    if ($LASTEXITCODE -ne 0) {
        $allArgs = @($prefix) + @($Arguments)
        throw "Python command failed: $exe $($allArgs -join ' ')"
    }
}

function Write-Step {
    param(
        [int]$Step,
        [int]$Total,
        [string]$Label
    )
    Write-Host ("[audion-step] {0}/{1} {2}" -f $Step, $Total, $Label)
}

function Assert-ProjectChild {
    param([string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $rootFull = ([System.IO.Path]::GetFullPath($Root)).TrimEnd("\") + "\"
    if (-not $full.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to write outside project root: $full"
    }
}

function Package-Spec {
    param([string]$Kind)
    switch ($Kind) {
        "live" {
            return @("sounddevice==0.5.5", "websockets==16.1")
        }
        "common" {
            return @("onnx-asr>=0.11.0", "huggingface-hub>=1.0")
        }
        "directml" {
            return @("onnxruntime-directml==1.24.4", "numpy==2.3.5")
        }
        "cuda" {
            return @(
                "onnxruntime-gpu[cuda,cudnn]==1.22.0",
                "nvidia-cuda-runtime-cu12==12.9.79",
                "nvidia-cuda-nvrtc-cu12==12.9.86",
                "nvidia-cublas-cu12==12.9.2.10",
                "nvidia-cufft-cu12==11.4.1.4",
                "nvidia-curand-cu12==10.3.10.19",
                "nvidia-nvjitlink-cu12==12.9.86",
                "nvidia-cudnn-cu12==9.24.0.43"
            )
        }
        default {
            return @("onnxruntime", "numpy==2.3.5")
        }
    }
}

function Normalize-ProviderList {
    param([string[]]$Values)
    $valid = @("live", "common", "directml", "cpu", "cuda")
    $items = @()
    foreach ($value in $Values) {
        if ([string]::IsNullOrWhiteSpace($value)) { continue }
        foreach ($part in ($value -split "[,;]")) {
            $kind = $part.Trim().ToLowerInvariant()
            if (-not $kind) { continue }
            if ($kind -notin $valid) {
                throw "Unknown provider '$kind'. Valid providers: $($valid -join ', ')"
            }
            if ($items -notcontains $kind) {
                $items += $kind
            }
        }
    }
    return $items
}

function Rebuild-ProviderCache {
    param([string]$Kind)
    $packages = Package-Spec $Kind
    $target = Join-Path $WheelRoot $Kind
    $stage = Join-Path $Tmp ($Kind + "-" + (Get-Date -Format "yyyyMMdd-HHmmss"))

    Assert-ProjectChild $target
    Assert-ProjectChild $stage

    if (Test-Path -LiteralPath $stage) {
        Remove-Item -LiteralPath $stage -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $stage | Out-Null

    Write-Host ""
    Write-Host ("[wheel-cache] {0}: {1}" -f $Kind, ($packages -join ", "))
    $downloadArgs = @(
        "-m", "pip", "download",
        "--progress-bar", "raw",
        "--only-binary=:all:",
        "--dest", $stage
    ) + $packages
    Invoke-Python -Arguments $downloadArgs

    $wheels = @(Get-ChildItem -LiteralPath $stage -Filter "*.whl" -File -ErrorAction SilentlyContinue)
    if ($wheels.Count -eq 0) {
        throw "No wheels downloaded for provider '$Kind'."
    }

    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Get-ChildItem -LiteralPath $target -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
    Copy-Item -Path (Join-Path $stage "*") -Destination $target -Force
    Remove-Item -LiteralPath $stage -Recurse -Force

    $total = (Get-ChildItem -LiteralPath $target -Filter "*.whl" -File | Measure-Object Length -Sum).Sum
    Write-Host ("[wheel-cache] {0}: {1} wheel(s), {2:n1} MB" -f $Kind, $wheels.Count, ($total / 1MB))
}

$edition = Get-ProjectEdition
$Provider = Normalize-ProviderList $Provider
if (-not $Provider -or $Provider.Count -eq 0) {
    $Provider = @("live", "common", "directml", "cpu")
    if ($IncludeCuda -or $edition -eq "studio") {
        $Provider += "cuda"
    }
} elseif ($IncludeCuda -and "cuda" -notin $Provider) {
    $Provider += "cuda"
}

New-Item -ItemType Directory -Force -Path $WheelRoot, $Tmp | Out-Null

Write-Host "[Audion Voice AI] Rebuild wheel cache"
Write-Host "Root: $Root"
Write-Host "Edition: $edition"
Write-Host "Providers: $($Provider -join ', ')"
Write-Host ""

$stepTotal = 1 + $Provider.Count
Write-Step 1 $stepTotal "Check Python and pip"
Invoke-Python -Arguments @("-m", "pip", "--version")
$step = 1
foreach ($kind in $Provider) {
    $step += 1
    Write-Step $step $stepTotal "Download wheel cache: $kind"
    Rebuild-ProviderCache $kind
}

Write-Host ""
Write-Host "Wheel cache ready: $WheelRoot"

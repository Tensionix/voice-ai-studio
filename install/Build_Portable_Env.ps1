<#
    Audion Voice AI - portable Python environment builder.

    Downloads the CPython 3.12 embeddable package into .\runtime, enables pip +
    site-packages, then installs the base (API-mode) requirements. Local models
    and GPU diarization remain opt-in through their dedicated installer steps.
#>
[CmdletBinding()]
param(
    [string]$PythonVersion = "3.12.10",
    [ValidateSet('None', 'BtbN', 'Gyan')]
    [string]$FFmpegSource = 'None'
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $Root "runtime"
$Downloads = Join-Path $PSScriptRoot "download"
$Requirements = Join-Path $PSScriptRoot "requirements_full.in"

New-Item -ItemType Directory -Force -Path $RuntimeDir, $Downloads | Out-Null

# Portable philosophy: keep ALL build state inside the project. Redirect pip's
# cache and the build temp dir here so the online build never writes to
# %LOCALAPPDATA% / %TEMP%. The only thing that leaves the folder is network I/O.
$PipCache = Join-Path $Downloads "pip-cache"
$BuildTmp = Join-Path $Downloads "tmp"
New-Item -ItemType Directory -Force -Path $PipCache, $BuildTmp | Out-Null
$env:PIP_CACHE_DIR = $PipCache
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:TMP = $BuildTmp
$env:TEMP = $BuildTmp

$embedName = "python-$PythonVersion-embed-amd64.zip"
$embedUrl = "https://www.python.org/ftp/python/$PythonVersion/$embedName"
$embedZip = Join-Path $Downloads $embedName

if (-not (Test-Path (Join-Path $RuntimeDir "python.exe"))) {
    Write-Host "Downloading $embedUrl"
    Invoke-WebRequest -Uri $embedUrl -OutFile $embedZip
    Write-Host "Extracting embeddable Python to $RuntimeDir"
    Expand-Archive -Path $embedZip -DestinationPath $RuntimeDir -Force
}

# Enable site + pip in the embeddable distribution by editing the ._pth file.
$pthFile = Get-ChildItem -Path $RuntimeDir -Filter "python*._pth" | Select-Object -First 1
if ($pthFile) {
    $content = Get-Content $pthFile.FullName
    $content = $content -replace "^#\s*import site", "import site"
    if ($content -notcontains "import site") { $content += "import site" }
    if ($content -notcontains "Lib\site-packages") { $content += "Lib\site-packages" }
    Set-Content -Path $pthFile.FullName -Value $content -Encoding ASCII
}

$python = Join-Path $RuntimeDir "python.exe"

# Bootstrap pip.
$pipReady = $false
try {
    & $python -m pip --version 2>$null
    $pipReady = ($LASTEXITCODE -eq 0)
} catch {
    $pipReady = $false
}
if (-not $pipReady) {
    $getPip = Join-Path $Downloads "get-pip.py"
    Write-Host "Bootstrapping pip"
    # A dropped connection here used to kill the whole project build.
    $getPipOk = $false
    foreach ($getPipTry in 1..5) {
        $getPipTmp = "$($getPip).part"
        try {
            Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPipTmp -TimeoutSec 120 -UseBasicParsing
            $getPipSize = (Get-Item -LiteralPath $getPipTmp).Length
            if ($getPipSize -lt 1000000) { throw "truncated body: $getPipSize bytes" }
            Move-Item -LiteralPath $getPipTmp -Destination $getPip -Force
            $getPipOk = $true
            break
        } catch {
            Write-Host "  get-pip.py attempt $getPipTry failed: $($_.Exception.Message)"
            Remove-Item -LiteralPath $getPipTmp -Force -ErrorAction SilentlyContinue
            if ($getPipTry -lt 5) { Start-Sleep -Seconds (3 * $getPipTry) }
        }
    }
    if (-not $getPipOk) { throw "Could not download get-pip.py after 5 attempts - the network dropped every time." }
    & $python $getPip --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw "get-pip.py failed with exit code $LASTEXITCODE" }
}

Write-Host "Installing base requirements (cache + temp kept in-project)"
& $python -m pip install --cache-dir $PipCache --progress-bar raw --upgrade pip
& $python -m pip install --cache-dir $PipCache --progress-bar raw --no-warn-script-location -r $Requirements

Write-Host ""
Write-Host "Portable environment ready:" -ForegroundColor Green
& $python --version
& $python (Join-Path $Root "system_core\main.py")

if ($FFmpegSource -ne 'None') {
    $ffmpegScriptName = if ($FFmpegSource -eq 'Gyan') {
        'Install-Portable-FFmpeg-Gyan.cmd'
    } else {
        'Install-Portable-FFmpeg-BtbN.cmd'
    }
    $ffmpegInstaller = Join-Path $PSScriptRoot $ffmpegScriptName
    Write-Host "Installing optional FFmpeg provider: $FFmpegSource"
    & $env:ComSpec /d /c "call `"$ffmpegInstaller`" /NOPAUSE"
    if ($LASTEXITCODE -ne 0) { throw "FFmpeg $FFmpegSource installer failed (exit $LASTEXITCODE)" }
}

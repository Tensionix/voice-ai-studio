[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$InstallDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $PSCommandPath }
$Root = (Resolve-Path (Join-Path $InstallDir "..")).Path
$Marker = Join-Path $Root "Tools\gigaam\audion-gigaam-onnx-pack.txt"
$Installer = Join-Path $InstallDir "Install-GigaAM-ONNX.ps1"

if (-not (Test-Path -LiteralPath $Marker)) {
    Write-Host "GigaAM ONNX marker not found; provider restore skipped."
    exit 0
}

$provider = "auto"
try {
    $line = Get-Content -LiteralPath $Marker -ErrorAction Stop |
        Where-Object { $_ -match "^kind=" } |
        Select-Object -First 1
    if ($line) {
        $value = ($line -replace "^kind=", "").Trim().ToLowerInvariant()
        if ($value -in @("directml", "cuda", "cpu")) {
            $provider = $value
        }
    }
} catch {
}

Write-Host "Restoring GigaAM ONNX Runtime provider: $provider"
& powershell -NoProfile -ExecutionPolicy Bypass -File $Installer -Provider $provider -SkipPayload
exit $LASTEXITCODE

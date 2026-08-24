[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Models = Join-Path $Root "models"
$Assets = @(
    [pscustomobject]@{
        Label = "Large V2 model"
        Url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v2.bin"
        FileName = "ggml-large-v2.bin"
    }
)

function Format-Size {
    param([double]$Bytes)
    if ($Bytes -ge 1GB) { return ("{0:N2} GB" -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ("{0:N1} MB" -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ("{0:N1} KB" -f ($Bytes / 1KB)) }
    return ("{0:N0} B" -f $Bytes)
}

function Format-Eta {
    param([double]$Seconds)
    if ($Seconds -lt 0 -or [double]::IsInfinity($Seconds) -or [double]::IsNaN($Seconds)) {
        return ""
    }
    $whole = [int][Math]::Round($Seconds)
    $span = [TimeSpan]::FromSeconds($whole)
    if ($span.TotalHours -ge 1) {
        return "{0}:{1:D2}:{2:D2}" -f [int]$span.TotalHours, $span.Minutes, $span.Seconds
    }
    return "{0}:{1:D2}" -f $span.Minutes, $span.Seconds
}

function Download-FileWithProgress {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (Test-Path -LiteralPath $Destination) {
        $existingDone = (Get-Item -LiteralPath $Destination).Length
        if ($existingDone -gt 0) {
            Write-Host "$Label already exists: $Destination ($(Format-Size $existingDone))"
            return
        }
    }

    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null

    $temp = "$Destination.part"
    $existing = 0L
    if (Test-Path -LiteralPath $temp) {
        $existing = [int64](Get-Item -LiteralPath $temp).Length
    }

    Add-Type -AssemblyName System.Net.Http
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $true
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromHours(8)

    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, $Url)
    $request.Headers.UserAgent.ParseAdd("Audion-Voice-AI")
    if ($existing -gt 0) {
        $request.Headers.Range = [System.Net.Http.Headers.RangeHeaderValue]::new($existing, $null)
        Write-Host "$Label resume: $(Format-Size $existing)"
    } else {
        Write-Host "$Label download started..."
    }

    $response = $client.SendAsync(
        $request,
        [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead
    ).GetAwaiter().GetResult()

    if ($existing -gt 0 -and [int]$response.StatusCode -eq 200) {
        Write-Host "$Label server ignored resume; restarting clean download."
        $existing = 0L
        if (Test-Path -LiteralPath $temp) {
            Remove-Item -LiteralPath $temp -Force
        }
    }

    $null = $response.EnsureSuccessStatusCode()
    $remaining = $response.Content.Headers.ContentLength
    $total = $null
    $range = $response.Content.Headers.ContentRange
    if ($range -and $range.Length) {
        $total = [int64]$range.Length
    } elseif ($remaining -ne $null) {
        $total = [int64]$remaining + $existing
    }

    $inputStream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
    $mode = if ($existing -gt 0) { [System.IO.FileMode]::Append } else { [System.IO.FileMode]::Create }
    $outputStream = [System.IO.FileStream]::new(
        $temp,
        $mode,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::Read
    )

    try {
        $buffer = New-Object byte[] (1024 * 1024)
        $done = $existing
        $lastDone = $done
        $lastTick = Get-Date
        while (($read = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $outputStream.Write($buffer, 0, $read)
            $done += $read
            $now = Get-Date
            $elapsed = ($now - $lastTick).TotalSeconds
            if ($elapsed -ge 2.0) {
                $speed = [Math]::Max(0, ($done - $lastDone) / [Math]::Max($elapsed, 0.1))
                $totalText = if ($total) { Format-Size $total } else { "unknown" }
                $pctText = if ($total) { "{0:N1}%" -f (($done / $total) * 100.0) } else { "?" }
                $etaText = if ($total -and $speed -gt 0) { Format-Eta (($total - $done) / $speed) } else { "" }
                Write-Host ("{0}: {1} / {2} ({3}) @ {4}/s {5}" -f $Label, (Format-Size $done), $totalText, $pctText, (Format-Size $speed), $etaText).TrimEnd()
                $lastDone = $done
                $lastTick = $now
            }
        }
    } finally {
        $outputStream.Dispose()
        $inputStream.Dispose()
        $response.Dispose()
        $client.Dispose()
        $handler.Dispose()
    }

    $finalSize = [int64](Get-Item -LiteralPath $temp).Length
    if ($total -and $finalSize -lt $total) {
        throw "$Label download incomplete: $(Format-Size $finalSize) / $(Format-Size $total)"
    }
    Move-Item -LiteralPath $temp -Destination $Destination -Force
    Write-Host "$Label download complete: $Destination ($(Format-Size $finalSize))"
}

Write-Host "[Audion Voice AI] Installing optional whisper.cpp Large V2 model..."
Write-Host "Root: $Root"
Write-Host ""

New-Item -ItemType Directory -Force -Path $Models | Out-Null

foreach ($asset in $Assets) {
    $destination = Join-Path $Models $asset.FileName
    Download-FileWithProgress -Url $asset.Url -Destination $destination -Label $asset.Label
}

Write-Host ""
Write-Host "Optional whisper.cpp Large V2 model ready:"
foreach ($asset in $Assets) {
    $path = Join-Path $Models $asset.FileName
    if (Test-Path -LiteralPath $path) {
        Write-Host ("  {0}: {1}" -f $asset.FileName, (Format-Size (Get-Item -LiteralPath $path).Length))
    }
}

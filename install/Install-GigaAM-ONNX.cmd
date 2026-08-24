@echo off
setlocal
chcp 65001 >nul

set "HERE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%Install-GigaAM-ONNX.ps1" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo [Audion Voice AI] GigaAM ONNX pack install did not complete. Exit code: %RC%
  goto DONE
)

echo.
echo [Audion Voice AI] GigaAM ONNX pack installed.

:DONE
call "%~dp0_pause_if_needed.cmd"
exit /b %RC%

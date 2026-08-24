@echo off
rem Restore GigaAM ONNX Runtime provider for Intel/AMD/NVIDIA Windows GPUs via DirectML.
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
echo Restoring Audion Voice AI for Intel/AMD DirectML GPU...
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%Install-GigaAM-ONNX.ps1" -Provider directml
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" (
  echo [Audion Voice AI] DirectML provider restored.
) else (
  echo [Audion Voice AI] DirectML provider restore failed. Exit code: %RC%
)
call "%HERE%_pause_if_needed.cmd"
exit /b %RC%

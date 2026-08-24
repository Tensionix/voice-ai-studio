@echo off
rem Restore GigaAM ONNX Runtime provider for NVIDIA RTX/CUDA machines.
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
echo Restoring Audion Voice AI for NVIDIA RTX/CUDA GPU...
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%Install-GigaAM-ONNX.ps1" -Provider cuda
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" (
  echo [Audion Voice AI] CUDA provider restored.
) else (
  echo [Audion Voice AI] CUDA provider restore failed. Exit code: %RC%
)
call "%HERE%_pause_if_needed.cmd"
exit /b %RC%

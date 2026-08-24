@echo off
setlocal
chcp 65001 >nul

set "HERE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%Rebuild-Wheel-Cache.ps1" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo [ERROR] Wheel cache rebuild failed with exit code %RC%.
)
call "%~dp0_pause_if_needed.cmd"
exit /b %RC%

@echo off
rem Build the portable Python environment via PowerShell.
chcp 65001 >nul
setlocal
set "HERE=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%Build_Portable_Env.ps1" %*
set "RC=%ERRORLEVEL%"

:DONE
call "%~dp0_pause_if_needed.cmd"
endlocal & exit /b %RC%

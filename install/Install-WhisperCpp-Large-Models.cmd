@echo off
rem Opt-in: download optional whisper.cpp Large V2 GGML model.
chcp 65001 >nul
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-WhisperCpp-Large-Models.ps1"
set "RC=%ERRORLEVEL%"
call "%~dp0_pause_if_needed.cmd"
endlocal & exit /b %RC%

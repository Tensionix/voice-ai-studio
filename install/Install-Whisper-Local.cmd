@echo off
rem Compatibility stub. Faster-Whisper/CTranslate2 is no longer a product backend.
chcp 65001 >nul
echo.
echo [RETIRED] Faster-Whisper/CTranslate2 is not installed by Audion Voice AI.
echo Use builder steps [09] WHISPER.CPP CUBLAS and [10] WHISPER.CPP LARGE V2 instead.
echo No changes were made.
echo.
call "%~dp0_pause_if_needed.cmd"
exit /b 2

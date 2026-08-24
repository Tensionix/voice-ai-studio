@echo off
if /I "%AUDION_NO_PAUSE%"=="1" exit /b 0
if /I "%~1"=="/NOPAUSE" exit /b 0
echo.
echo Press any key to continue...
if not defined AUDION_NO_PAUSE pause >nul
exit /b 0

@echo off
rem Audion Voice AI - CLI pipeline launcher (iteration 1)
rem Usage: launcher_cli.cmd "C:\path\to\file_or_folder" [options]
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "PYEXE=%ROOT%runtime\python.exe"

if not exist "%PYEXE%" (
    where py >nul 2>nul && (set "PYEXE=py -3") || set "PYEXE=python"
)

"%PYEXE%" "%ROOT%system_core\cli.py" %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%

@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

for %%A in ("%SCRIPT_DIR%") do set "HERE=%%~nxA"

set "ROOT=%SCRIPT_DIR%"
if /I "%HERE%"=="install" for %%A in ("%SCRIPT_DIR%\..") do set "ROOT=%%~fA"

call :MK "%ROOT%\input"
call :MK "%ROOT%\output"
call :MK "%ROOT%\logs"
call :MK "%ROOT%\report"
call :MK "%ROOT%\workspace"
call :MK "%ROOT%\release"
call :MK "%ROOT%\runtime"
call :MK "%ROOT%\Tools"
call :MK "%ROOT%\Tools\ffmpeg"
call :MK "%ROOT%\Tools\gigaam"
call :MK "%ROOT%\Tools\whispercpp"
call :MK "%ROOT%\models"
call :MK "%ROOT%\models\huggingface"
call :MK "%ROOT%\install\download"
call :MK "%ROOT%\install\download\pip-cache"
call :MK "%ROOT%\install\download\tmp"
call :MK "%ROOT%\install\download\whispercpp"
call :MK "%ROOT%\wheelhouse"
call :MK "%ROOT%\wheelhouse\common"
call :MK "%ROOT%\wheelhouse\directml"
call :MK "%ROOT%\wheelhouse\cpu"
call :MK "%ROOT%\wheelhouse\cuda"
call :MK "%ROOT%\wheelhouse\live"

call "%~dp0_pause_if_needed.cmd"
exit /b 0

:MK
if not exist "%~1\" mkdir "%~1" >nul 2>nul
goto :eof

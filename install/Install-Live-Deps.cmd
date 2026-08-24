@echo off
rem Live microphone/runtime deps. Uses the shipped wheel cache first so the GUI
rem can repair a first-run installation without Internet access on Windows 10.
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
set "ROOT=%~dp0.."
set "PYEXE=%ROOT%\runtime\python.exe"
set "WHEELDIR=%~dp0wheels\live"
set "VERIFY=%~dp0Verify-Live-Deps.py"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"

if not exist "%PYEXE%" (
  echo [ERROR] Portable Python was not found: %PYEXE%
  echo Run Build_Portable_Env.cmd first.
  set "RC=2"
  goto :finish
)

echo [audion-step] 1/3 Check portable Python and pip
"%PYEXE%" -m pip --version
if errorlevel 1 (
  echo [ERROR] pip is missing from the portable runtime.
  set "RC=3"
  goto :finish
)

set "OFFLINE_READY=1"
if not exist "%WHEELDIR%\sounddevice-*.whl" set "OFFLINE_READY=0"
if not exist "%WHEELDIR%\websockets-*.whl" set "OFFLINE_READY=0"
if not exist "%WHEELDIR%\cffi-*.whl" set "OFFLINE_READY=0"
if not exist "%WHEELDIR%\pycparser-*.whl" set "OFFLINE_READY=0"

echo [audion-step] 2/3 Install sounddevice and websockets
set "RC=1"
if "%OFFLINE_READY%"=="1" (
  echo Using the bundled Windows wheel cache: %WHEELDIR%
  "%PYEXE%" -m pip install --progress-bar raw --no-warn-script-location --only-binary=:all: --no-index --find-links="%WHEELDIR%" --upgrade --force-reinstall "sounddevice>=0.5" "websockets>=13"
  set "RC=!ERRORLEVEL!"
) else (
  echo Bundled Live wheel cache is incomplete; trying the package index.
)

if not "%RC%"=="0" (
  echo Offline install was unavailable or failed. Trying the package index...
  "%PYEXE%" -m pip install --progress-bar raw --no-warn-script-location --only-binary=:all: --upgrade --force-reinstall "sounddevice>=0.5" "websockets>=13"
  set "RC=!ERRORLEVEL!"
)
if not "%RC%"=="0" goto :failed

echo [audion-step] 3/3 Verify PortAudio and Live imports
"%PYEXE%" "%VERIFY%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto :failed

echo.
echo Live dependencies are ready. Audion will check the Windows default recording
echo and communications devices whenever microphone capture starts.
goto :finish

:failed
echo.
echo [ERROR] Live dependency installation failed with exit code %RC%.
echo See the installer log above. You can retry from Maintenance in the GUI.

:finish
call "%~dp0_pause_if_needed.cmd"
endlocal & exit /b %RC%

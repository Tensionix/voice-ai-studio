@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

title Audion Voice AI Studio - Builder

set "BASE_DIR=%~dp0"
if "%BASE_DIR:~-1%"=="\" set "BASE_DIR=%BASE_DIR:~0,-1%"
cd /d "%BASE_DIR%" || exit /b 1

set "FZF_EXE=%BASE_DIR%\system_core\fzf.exe"
set "RUNTIME_DIR=%BASE_DIR%\._runtime"
set "MENU_FILE=%RUNTIME_DIR%\builder_menu.txt"
set "RES_FILE=%RUNTIME_DIR%\builder_menu_res.txt"

if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%" >nul 2>nul

:MAIN
cls
echo ======================================================================
echo   AUDION VOICE AI STUDIO - BUILDER
echo ======================================================================
echo Root: %BASE_DIR%
echo Edition: Studio staged installers
echo.
echo Recommended staged install order:
echo.

set "SCRIPT_COUNT=14"
set "SCRIPT_01=init_folders.cmd"
set "SCRIPT_02=Build_Portable_Env.cmd"
set "SCRIPT_03=launcher-tools-update_fzf.cmd"
set "SCRIPT_04=Install-Portable-FFmpeg-BtbN.cmd"
set "SCRIPT_05=Install-Portable-FFmpeg-Gyan.cmd"
set "SCRIPT_06=Install-Live-Deps.cmd"
set "SCRIPT_07=Rebuild-Wheel-Cache.cmd"
set "SCRIPT_08=Install-GigaAM-ONNX.cmd"
set "SCRIPT_09=Install-Live-Vulkan.cmd"
set "SCRIPT_10=Install-WhisperCpp-Large-Models.cmd"
set "SCRIPT_11=Install-Diarization-GPU.cmd"
set "SCRIPT_12=Restore-GigaAM-DirectML.cmd"
set "SCRIPT_13=Restore-GigaAM-CUDA.cmd"
set "SCRIPT_14=verify_portable_env.cmd"

if exist "%FZF_EXE%" goto FZF_MENU
goto FALLBACK_MENU

:FZF_MENU
>"%MENU_FILE%" echo [01] PREPARE FOLDERS                     ^| step_01          ^| init project folders
>>"%MENU_FILE%" echo [02] PYTHON RUNTIME                      ^| step_02          ^| portable Python runtime + base GUI/API deps
>>"%MENU_FILE%" echo [03] FZF RUNNER TOOL                     ^| step_03          ^| install/update system_core\fzf.exe
>>"%MENU_FILE%" echo [04] FFMPEG BTBN AUTO-FALLBACK           ^| step_04          ^| Driver-aware Stable branch; Gyan provider fallback
>>"%MENU_FILE%" echo [05] FFMPEG GYAN STABLE                  ^| step_05          ^| optional explicit Gyan provider
>>"%MENU_FILE%" echo [06] LIVE DEPS                           ^| step_06          ^| Live mic/Realtime deps
>>"%MENU_FILE%" echo [07] WHEEL CACHE                         ^| step_07          ^| GigaAM common + DirectML/CPU/CUDA wheels
>>"%MENU_FILE%" echo [08] GIGAAM ONNX                         ^| step_08          ^| onnx-asr + provider + payloads
>>"%MENU_FILE%" echo [09] WHISPER.CPP CUBLAS                  ^| step_09          ^| full CUDA/cuBLAS pack, server, payloads and Turbo
>>"%MENU_FILE%" echo [10] WHISPER.CPP LARGE V2                ^| step_10          ^| primary Russian file model
>>"%MENU_FILE%" echo [11] CUDA / PYANNOTE                     ^| step_11          ^| optional speaker diarization
>>"%MENU_FILE%" echo [12] RESTORE INTEL/AMD GPU               ^| step_12          ^| restore GigaAM ONNX provider to DirectML
>>"%MENU_FILE%" echo [13] RESTORE RTX CUDA                    ^| step_13          ^| restore GigaAM ONNX provider to CUDA
>>"%MENU_FILE%" echo [14] VERIFY                              ^| step_14          ^| verify final portable environment
>>"%MENU_FILE%" echo.
>>"%MENU_FILE%" echo [70] CLEAN INSTALL CACHE                 ^| cleanup          ^| clean install\download
>>"%MENU_FILE%" echo [71] VERIFY                              ^| verify           ^| verify portable environment
>>"%MENU_FILE%" echo [72] COLLECT LICENSES                    ^| collect_licenses ^| collect third-party licenses
>>"%MENU_FILE%" echo [73] PRUNE LICENSES                      ^| prune_licenses   ^| prune stale license folders
>>"%MENU_FILE%" echo [74] DEDUP LICENSES                      ^| dedupe_licenses  ^| deduplicate license files
>>"%MENU_FILE%" echo [75] CMD ENCODING CHECK                  ^| cmd_encoding     ^| check/fix CMD UTF-8 no-BOM CRLF
>>"%MENU_FILE%" echo.
>>"%MENU_FILE%" echo [94] OPEN licenses                       ^| open_licenses    ^| explorer licenses
>>"%MENU_FILE%" echo [95] OPEN install                        ^| open_install     ^| explorer install
>>"%MENU_FILE%" echo [96] OPEN runtime                        ^| open_runtime     ^| explorer runtime
>>"%MENU_FILE%" echo [97] OPEN Tools                          ^| open_tools       ^| explorer Tools
>>"%MENU_FILE%" echo [98] OPEN models                         ^| open_models      ^| explorer models
>>"%MENU_FILE%" echo [99] OPEN release                        ^| open_release     ^| explorer release
>>"%MENU_FILE%" echo.
>>"%MENU_FILE%" echo [00] EXIT                                ^| exit             ^| close builder

type "%MENU_FILE%" | "%FZF_EXE%" --prompt="audion@voice-studio > " --pointer=">" --header="Pick step:" --layout=reverse --border="rounded" --info=hidden --margin=1,2 > "%RES_FILE%"

set "RAW="
for /f "usebackq tokens=2 delims=|" %%a in ("%RES_FILE%") do (
  if not defined RAW set "RAW=%%a"
)
if not defined RAW goto MAIN

call :TRIM RAW

if /I "%RAW%"=="step_01" set "RAW=01"
if /I "%RAW%"=="step_02" set "RAW=02"
if /I "%RAW%"=="step_03" set "RAW=03"
if /I "%RAW%"=="step_04" set "RAW=04"
if /I "%RAW%"=="step_05" set "RAW=05"
if /I "%RAW%"=="step_06" set "RAW=06"
if /I "%RAW%"=="step_07" set "RAW=07"
if /I "%RAW%"=="step_08" set "RAW=08"
if /I "%RAW%"=="step_09" set "RAW=09"
if /I "%RAW%"=="step_10" set "RAW=10"
if /I "%RAW%"=="step_11" set "RAW=11"
if /I "%RAW%"=="step_12" set "RAW=12"
if /I "%RAW%"=="step_13" set "RAW=13"
if /I "%RAW%"=="step_14" set "RAW=14"
goto ROUTE_CHOICE

:FALLBACK_MENU
echo [01] prepare folders
echo [02] portable Python runtime + base GUI/API deps
echo [03] install/update FZF runner
echo [04] FFmpeg Stable ^(driver-aware branch; Gyan provider fallback^)
echo [05] FFmpeg Gyan Stable ^(optional explicit provider^)
echo [06] Live mic/Realtime deps
echo [07] Download/Rebuild wheel cache ^(GigaAM common + DirectML/CPU/CUDA^)
echo [08] GigaAM ONNX pack ^(onnx-asr + provider + payloads^)
echo [09] whisper.cpp CUDA/cuBLAS pack ^(server + payloads + Turbo^)
echo [10] whisper.cpp Large V2 ^(primary Russian file model^)
echo [11] CUDA/pyannote ^(optional speaker diarization^)
echo [12] restore Audion Voice AI for Intel/AMD GPU ^(DirectML^)
echo [13] restore Audion Voice AI for RTX CUDA ^(GigaAM ONNX CUDA^)
echo [14] verify final portable environment

if "%SCRIPT_COUNT%"=="0" echo   [none]
echo.
echo Maintenance:
echo.
echo [70] clean install\download
echo [71] verify portable environment
echo [72] collect third-party licenses
echo [73] prune stale license folders
echo [74] deduplicate license files
echo [75] check/fix CMD encoding
echo.
echo Folders:
echo.
echo [94] open licenses
echo [95] open install
echo [96] open runtime
echo [97] open Tools
echo [98] open models
echo [99] open release
echo.
echo [00] exit
echo.

set "RAW="
set /p RAW="Select step number, script name, or id: "
call :TRIM RAW

:ROUTE_CHOICE
if not defined RAW goto MAIN

if /I "%RAW%"=="00" exit /b 0
if /I "%RAW%"=="0" exit /b 0
if /I "%RAW%"=="exit" exit /b 0
if /I "%RAW%"=="step_01" goto STEP_01
if /I "%RAW%"=="step_02" goto STEP_02
if /I "%RAW%"=="step_03" goto STEP_03
if /I "%RAW%"=="step_04" goto STEP_04
if /I "%RAW%"=="step_05" goto STEP_05
if /I "%RAW%"=="step_06" goto STEP_06
if /I "%RAW%"=="step_07" goto STEP_07
if /I "%RAW%"=="step_08" goto STEP_08
if /I "%RAW%"=="step_09" goto STEP_09
if /I "%RAW%"=="step_10" goto STEP_10
if /I "%RAW%"=="step_11" goto STEP_11
if /I "%RAW%"=="step_12" goto STEP_12
if /I "%RAW%"=="step_13" goto STEP_13
if /I "%RAW%"=="step_14" goto STEP_14
if /I "%RAW%"=="step_15" goto STEP_15
if /I "%RAW%"=="update_fzf" goto UPDATE_FZF
if /I "%RAW%"=="cleanup" goto STEP_70
if /I "%RAW%"=="70" goto CLEANUP
if /I "%RAW%"=="cleanup" goto CLEANUP
if /I "%RAW%"=="clean_download" goto CLEANUP
if /I "%RAW%"=="download_cache" goto CLEANUP
if /I "%RAW%"=="71" goto VERIFY
if /I "%RAW%"=="verify" goto VERIFY
if /I "%RAW%"=="72" goto COLLECT_LICENSES
if /I "%RAW%"=="collect_licenses" goto COLLECT_LICENSES
if /I "%RAW%"=="licenses" goto COLLECT_LICENSES
if /I "%RAW%"=="73" goto PRUNE_LICENSES
if /I "%RAW%"=="prune_licenses" goto PRUNE_LICENSES
if /I "%RAW%"=="74" goto DEDUPE_LICENSES
if /I "%RAW%"=="dedupe_licenses" goto DEDUPE_LICENSES
if /I "%RAW%"=="75" goto CMD_ENCODING
if /I "%RAW%"=="cmd_encoding" goto CMD_ENCODING
if /I "%RAW%"=="Check-CmdEncoding" goto CMD_ENCODING
if /I "%RAW%"=="Check-CmdEncoding.cmd" goto CMD_ENCODING
if /I "%RAW%"=="3" goto UPDATE_FZF
if /I "%RAW%"=="03" goto UPDATE_FZF
if /I "%RAW%"=="fzf" goto UPDATE_FZF
if /I "%RAW%"=="update_fzf" goto UPDATE_FZF
if /I "%RAW%"=="launcher-tools-update_fzf" goto UPDATE_FZF
if /I "%RAW%"=="launcher-tools-update_fzf.cmd" goto UPDATE_FZF
if /I "%RAW%"=="94" goto OPEN_LICENSES
if /I "%RAW%"=="open_licenses" goto OPEN_LICENSES
if /I "%RAW%"=="95" goto OPEN_INSTALL
if /I "%RAW%"=="open_install" goto OPEN_INSTALL
if /I "%RAW%"=="96" goto OPEN_RUNTIME
if /I "%RAW%"=="open_runtime" goto OPEN_RUNTIME
if /I "%RAW%"=="97" goto OPEN_TOOLS
if /I "%RAW%"=="open_tools" goto OPEN_TOOLS
if /I "%RAW%"=="98" goto OPEN_MODELS
if /I "%RAW%"=="open_models" goto OPEN_MODELS
if /I "%RAW%"=="99" goto OPEN_RELEASE
if /I "%RAW%"=="open_release" goto OPEN_RELEASE

set "SEL=0%RAW%"
set "SEL=!SEL:~-2!"
set "SCRIPT_NAME=!SCRIPT_%SEL%!"
if defined SCRIPT_NAME (
  call :RUN_SCRIPT "%BASE_DIR%\install\!SCRIPT_NAME!"
  goto MAIN
)

for /f "delims=" %%F in ('dir /a-d /b /on "%BASE_DIR%\install\*.cmd" 2^>nul') do (
  if /I "%RAW%"=="%%~nF" (
    call :RUN_SCRIPT "%BASE_DIR%\install\%%F"
    goto MAIN
  )
  if /I "%RAW%"=="%%F" (
    call :RUN_SCRIPT "%BASE_DIR%\install\%%F"
    goto MAIN
  )
)

echo.
echo [ERROR] Unknown builder step: %RAW%
echo.
if not defined AUDION_NO_PAUSE pause
goto MAIN

:STEP_01
call "%BASE_DIR%\install\init_folders.cmd"
goto MAIN

:STEP_02
call "%BASE_DIR%\install\Build_Portable_Env.cmd"
goto MAIN

:STEP_03
call "%BASE_DIR%\install\launcher-tools-update_fzf.cmd"
goto MAIN

:STEP_04
call "%BASE_DIR%\install\Install-Portable-FFmpeg-BtbN.cmd"
goto MAIN

:STEP_05
call "%BASE_DIR%\install\Install-Portable-FFmpeg-Gyan.cmd"
goto MAIN

:STEP_06
call "%BASE_DIR%\install\Install-Live-Deps.cmd"
goto MAIN

:STEP_07
call "%BASE_DIR%\install\Rebuild-Wheel-Cache.cmd"
goto MAIN

:STEP_08
call "%BASE_DIR%\install\Install-GigaAM-ONNX.cmd"
goto MAIN

:STEP_09
call "%BASE_DIR%\install\Install-Live-Vulkan.cmd"
goto MAIN

:STEP_10
call "%BASE_DIR%\install\Install-WhisperCpp-Large-Models.cmd"
goto MAIN

:STEP_11
call "%BASE_DIR%\install\Install-Diarization-GPU.cmd"
goto MAIN

:STEP_12
call "%BASE_DIR%\install\Restore-GigaAM-DirectML.cmd"
goto MAIN

:STEP_13
call "%BASE_DIR%\install\Restore-GigaAM-CUDA.cmd"
goto MAIN

:STEP_14
call "%BASE_DIR%\install\verify_portable_env.cmd"
goto MAIN

:STEP_15
call "%BASE_DIR%\install\Install-Portable-FFmpeg-Gyan.cmd"
goto MAIN

:STEP_70
set "DOWNLOAD_DIR=%BASE_DIR%\install\download"
for %%D in ("%DOWNLOAD_DIR%") do set "DOWNLOAD_DIR=%%~fD"
set "EXPECTED_DIR=%BASE_DIR%\install\download"
for %%D in ("%EXPECTED_DIR%") do set "EXPECTED_DIR=%%~fD"
if /I not "%DOWNLOAD_DIR%"=="%EXPECTED_DIR%" exit /b 1
if not exist "%DOWNLOAD_DIR%\" mkdir "%DOWNLOAD_DIR%" >nul 2>nul
del /f /q "%DOWNLOAD_DIR%\*" >nul 2>nul
for /d %%D in ("%DOWNLOAD_DIR%\*") do rd /s /q "%%~fD"
goto MAIN

:CLEANUP
call :CLEAN_DOWNLOAD
goto MAIN

:VERIFY
call :RUN_SCRIPT "%BASE_DIR%\install\verify_portable_env.cmd"
goto MAIN

:COLLECT_LICENSES
call :RUN_SCRIPT "%BASE_DIR%\system_core\license\Run-Collect-ThirdPartyLicenses.cmd" /NOPAUSE
goto MAIN

:PRUNE_LICENSES
call :RUN_SCRIPT "%BASE_DIR%\system_core\license\Run-Prune-Stale-ThirdPartyLicenses.cmd" /NOPAUSE
goto MAIN

:DEDUPE_LICENSES
call :RUN_SCRIPT "%BASE_DIR%\system_core\license\Run-Deduplicate-ThirdPartyLicenses.cmd" /NOPAUSE
goto MAIN

:CMD_ENCODING
call :RUN_SCRIPT "%BASE_DIR%\install\Check-CmdEncoding.cmd" -Fix
goto MAIN

:UPDATE_FZF
call :RUN_SCRIPT "%BASE_DIR%\install\launcher-tools-update_fzf.cmd"
goto MAIN

:OPEN_LICENSES
call :OPEN_DIR "%BASE_DIR%\licenses"
goto MAIN

:OPEN_INSTALL
call :OPEN_DIR "%BASE_DIR%\install"
goto MAIN

:OPEN_RUNTIME
call :OPEN_DIR "%BASE_DIR%\runtime"
goto MAIN

:OPEN_TOOLS
call :OPEN_DIR "%BASE_DIR%\Tools"
goto MAIN

:OPEN_MODELS
call :OPEN_DIR "%BASE_DIR%\models"
goto MAIN

:OPEN_RELEASE
call :OPEN_DIR "%BASE_DIR%\release"
goto MAIN

:RUN_SCRIPT
if not exist "%~1" (
  echo.
  echo [ERROR] Script not found:
  echo   %~1
  echo.
  if not defined AUDION_NO_PAUSE pause
  exit /b 1
)
if /I "%~nx1"=="Install-Diarization-GPU.cmd" (
  echo.
  echo [CUDA] This is the heavy Studio/RTX step. Run it after the base/local packs.
  echo It downloads torch CUDA and pyannote dependencies for optional diarization.
  echo Run only on the CUDA workstation after base/FFmpeg/Live/local packs are installed.
  echo.
  choice /C YN /N /M "Continue with CUDA/pyannote install? [Y/N]: "
  if errorlevel 2 exit /b 1
)
echo.
echo [RUN] %~nx1
echo.
set "RUN_OLD_NO_PAUSE=%AUDION_NO_PAUSE%"
set "AUDION_NO_PAUSE=1"
call "%~1" %~2
set "RUN_RC=%ERRORLEVEL%"
if defined RUN_OLD_NO_PAUSE (set "AUDION_NO_PAUSE=%RUN_OLD_NO_PAUSE%") else set "AUDION_NO_PAUSE="
if "%RUN_RC%"=="0" (
  echo.
  echo [OK] Command completed successfully.
) else (
  echo.
  echo [ERROR] Command failed with exit code %RUN_RC%.
)
echo.
if not defined AUDION_NO_PAUSE pause
exit /b %RUN_RC%

:OPEN_DIR
if not exist "%~1" mkdir "%~1" >nul 2>nul
start "" explorer "%~1"
exit /b 0

:CLEAN_DOWNLOAD
set "DOWNLOAD_DIR=%BASE_DIR%\install\download"
for %%D in ("%DOWNLOAD_DIR%") do set "DOWNLOAD_DIR=%%~fD"
set "EXPECTED_DIR=%BASE_DIR%\install\download"
for %%D in ("%EXPECTED_DIR%") do set "EXPECTED_DIR=%%~fD"

if /I not "%DOWNLOAD_DIR%"=="%EXPECTED_DIR%" (
  echo.
  echo [ERROR] Refusing to clean unexpected path:
  echo   %DOWNLOAD_DIR%
  if not defined AUDION_NO_PAUSE pause
  exit /b 1
)

if not exist "%DOWNLOAD_DIR%\" mkdir "%DOWNLOAD_DIR%" >nul 2>nul

echo.
echo [CLEAN] install download cache
echo   %DOWNLOAD_DIR%
echo.

del /f /q "%DOWNLOAD_DIR%\*" >nul 2>nul
for /d %%D in ("%DOWNLOAD_DIR%\*") do rd /s /q "%%~fD"

echo [OK] install\download cleaned.
if not defined AUDION_NO_PAUSE pause
exit /b 0

:TRIM
for /f "tokens=* delims= " %%z in ("!%~1!") do set "%~1=%%z"
:TRIM_R
if "!%~1:~-1!"==" " set "%~1=!%~1:~0,-1!" & goto TRIM_R
goto :eof

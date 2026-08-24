@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

title Audion Voice AI - Cleanup

set "BASE_DIR=%~dp0"
if "%BASE_DIR:~-1%"=="\" set "BASE_DIR=%BASE_DIR:~0,-1%"
cd /d "%BASE_DIR%" || exit /b 1

set "BASE_PREFIX=%BASE_DIR%\"
set "BASE_PREFIX_WORK=%BASE_PREFIX%"
set /a BASE_PREFIX_LEN=0
:base_prefix_len_loop
if defined BASE_PREFIX_WORK (
  set "BASE_PREFIX_WORK=!BASE_PREFIX_WORK:~1!"
  set /a BASE_PREFIX_LEN+=1
  goto base_prefix_len_loop
)
set "BASE_PREFIX_WORK="

set "AUTO_YES=0"
set "DRY_RUN=0"
set "ERROR_COUNT=0"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="/?" goto usage
if /I "%~1"=="-h" goto usage
if /I "%~1"=="--help" goto usage
if /I "%~1"=="/Y" set "AUTO_YES=1" & shift & goto parse_args
if /I "%~1"=="/YES" set "AUTO_YES=1" & shift & goto parse_args
if /I "%~1"=="-Y" set "AUTO_YES=1" & shift & goto parse_args
if /I "%~1"=="-YES" set "AUTO_YES=1" & shift & goto parse_args
if /I "%~1"=="--yes" set "AUTO_YES=1" & shift & goto parse_args
if /I "%~1"=="/DRYRUN" set "DRY_RUN=1" & shift & goto parse_args
if /I "%~1"=="/DRY-RUN" set "DRY_RUN=1" & shift & goto parse_args
if /I "%~1"=="--dry-run" set "DRY_RUN=1" & shift & goto parse_args
echo [ERROR] Unknown argument: %~1
echo.
goto usage_error

:args_done
call :CheckMarker "%BASE_DIR%\system_core\main.py" || goto abort
call :CheckMarker "%BASE_DIR%\system_core\ui\app.py" || goto abort
call :CheckMarker "%BASE_DIR%\config\app_settings.yaml" || goto abort

echo ======================================================================
echo   AUDION VOICE AI - LEAN SOURCE CLEANUP
echo ======================================================================
echo Root:
echo   %BASE_DIR%
echo.
echo This script returns the project to a clean source/workspace shape.
echo It keeps source code, configs, docs, tests, launchers and install scripts.
echo It clears install\wheels too; rebuild it from builder_main.cmd when needed.
echo It clears reproducible runtime payloads and generated working data:
echo   - runtime, Tools, models, install\download and install\wheels contents
echo   - system_core\fzf.exe and system_core\_fzf_tmp
echo   - portable FFmpeg, GigaAM ONNX payloads, whisper.cpp/Vulkan, Python/pip caches
echo   - input, output, logs, report, work, workspace, release contents
echo   - all folders named .runtime, _runtime, ._runtime, __pycache__
echo   - pytest/mypy/ruff caches, root build/dist folders
echo   - generated *.pyc, *.pyo, *.tmp, *.bak, *.log, Thumbs.db, desktop.ini
echo.
echo Protected: config, Docs, tests, system_core source, launchers, install scripts.
if "%DRY_RUN%"=="1" echo Dry-run: ON. Nothing will be deleted.
echo.

if "%AUTO_YES%"=="1" goto clean
choice /C YNQ /N /M "Proceed with project cleanup? [Y/N/Q]: "
if errorlevel 3 (
  echo.
  echo [QUIT] Nothing was deleted.
  call :WAIT_KEY
  exit /b 0
)
if errorlevel 2 (
  echo.
  echo [CANCELLED] Nothing was deleted.
  call :WAIT_KEY
  exit /b 0
)
if errorlevel 1 goto clean
echo.
echo [CANCELLED] Nothing was deleted.
call :WAIT_KEY
exit /b 0

:clean
echo.
echo [CLEAN] Starting project cleanup...
echo.

call :RemoveNamedDirs ".runtime"
call :RemoveNamedDirs "_runtime"
call :RemoveNamedDirs "._runtime"
call :RemoveNamedDirs "__pycache__"
call :RemoveNamedDirs ".pytest_cache"
call :RemoveNamedDirs ".mypy_cache"
call :RemoveNamedDirs ".ruff_cache"
call :RemoveDir "%BASE_DIR%\.runtime"
call :RemoveDir "%BASE_DIR%\_runtime"
call :RemoveDir "%BASE_DIR%\._runtime"
call :RemoveDir "%BASE_DIR%\.pytest_cache"
call :RemoveDir "%BASE_DIR%\.mypy_cache"
call :RemoveDir "%BASE_DIR%\.ruff_cache"

call :ClearDir "%BASE_DIR%\input"
call :ClearDir "%BASE_DIR%\output"
call :ClearDir "%BASE_DIR%\logs"
call :ClearDir "%BASE_DIR%\report"
call :ClearDir "%BASE_DIR%\work"
call :ClearDir "%BASE_DIR%\workspace"
call :ClearDir "%BASE_DIR%\release"
call :ClearDir "%BASE_DIR%\runtime"
call :ClearDir "%BASE_DIR%\Tools"
call :ClearDir "%BASE_DIR%\models"
call :ClearDir "%BASE_DIR%\install\download"
call :ClearDir "%BASE_DIR%\install\wheels"

call :RemoveDir "%BASE_DIR%\build"
call :RemoveDir "%BASE_DIR%\dist"
call :RemoveDir "%BASE_DIR%\.devlibs"
call :RemoveDir "%BASE_DIR%\.venv"
call :RemoveDir "%BASE_DIR%\venv"
call :RemoveDir "%BASE_DIR%\system_core\_fzf_tmp"
call :RemoveDir "%BASE_DIR%\system_core\_ffmpeg_tmp"
call :RemoveDir "%BASE_DIR%\system_core\_ffmpeg_btbn_tmp"
call :RemoveDir "%BASE_DIR%\system_core\7zip"
call :RemoveFile "%BASE_DIR%\system_core\fzf.exe"

call :RemoveFilesByPattern "*.pyc"
call :RemoveFilesByPattern "*.pyo"
call :RemoveGeneratedFilesByPattern "*.tmp"
call :RemoveGeneratedFilesByPattern "*.bak"
call :RemoveGeneratedFilesByPattern "*.log"
call :RemoveFilesByPattern "Thumbs.db"
call :RemoveFilesByPattern "desktop.ini"

if exist "%BASE_DIR%\install\init_folders.cmd" (
  if "%DRY_RUN%"=="1" (
    echo [DRY] call install\init_folders.cmd
  ) else (
    set "OLD_AUDION_NO_PAUSE=%AUDION_NO_PAUSE%"
    set "AUDION_NO_PAUSE=1"
    call "%BASE_DIR%\install\init_folders.cmd" >nul 2>nul
    if defined OLD_AUDION_NO_PAUSE (set "AUDION_NO_PAUSE=%OLD_AUDION_NO_PAUSE%") else set "AUDION_NO_PAUSE="
  )
)

echo.
if not "%ERROR_COUNT%"=="0" goto cleanup_failed
if "%DRY_RUN%"=="1" goto dry_run_done
echo [OK] Cleanup finished.
echo [OK] runtime, Tools, models and generated work folders were recreated empty.
goto cleanup_done

:dry_run_done
echo [OK] Dry run finished. Nothing was deleted.

:cleanup_done
if not "%AUTO_YES%"=="1" call :WAIT_KEY
exit /b 0

:cleanup_failed
echo [ERROR] Cleanup finished with %ERROR_COUNT% error(s).
echo Close GUI, terminals, Python processes, and try again.
if not "%AUTO_YES%"=="1" call :WAIT_KEY
exit /b 1

:abort
echo.
echo [ABORTED] Cleanup refused to run outside the expected project root.
if not "%AUTO_YES%"=="1" call :WAIT_KEY
exit /b 1

:usage
echo Usage:
echo   cleanup_project.cmd [/Y] [/DRYRUN]
echo.
echo Options:
echo   /Y, /YES, --yes       Run without confirmation.
echo   /DRYRUN, --dry-run    Print actions without deleting anything.
exit /b 0

:usage_error
call :WAIT_KEY
exit /b 1

:CheckMarker
if exist "%~1" exit /b 0
echo [ERROR] Project marker not found:
echo   %~1
exit /b 1

:RemoveNamedDirs
set "DIR_NAME=%~1"
echo [DIRS] Removing folders named %DIR_NAME%
call :RemoveNamedDirsIn "%BASE_DIR%\config" "%DIR_NAME%"
call :RemoveNamedDirsIn "%BASE_DIR%\Docs" "%DIR_NAME%"
call :RemoveNamedDirsIn "%BASE_DIR%\GitHub" "%DIR_NAME%"
call :RemoveNamedDirsIn "%BASE_DIR%\install" "%DIR_NAME%"
call :RemoveNamedDirsIn "%BASE_DIR%\system_core" "%DIR_NAME%"
call :RemoveNamedDirsIn "%BASE_DIR%\tests" "%DIR_NAME%"
exit /b 0

:RemoveNamedDirsIn
set "SCAN_ROOT=%~1"
set "DIR_NAME=%~2"
if not exist "%SCAN_ROOT%\" exit /b 0
for /f "delims=" %%D in ('dir /ad /b /s "%SCAN_ROOT%" 2^>nul') do (
  if /I "%%~nxD"=="%DIR_NAME%" (
    if "%DRY_RUN%"=="1" (
      echo   [DRY] rmdir /s /q "%%~fD"
    ) else (
      echo   rmdir "%%~fD"
      attrib -r -s -h "%%~fD\*" /s /d >nul 2>nul
      rd /s /q "%%~fD" >nul 2>nul
      if exist "%%~fD\" ( echo   [ERROR] Could not remove directory: %%~fD & set /a ERROR_COUNT+=1 )
    )
  )
)
exit /b 0

:ClearDir
set "TARGET_DIR=%~1"
call :AssertInside "%TARGET_DIR%" || (
  set /a ERROR_COUNT+=1
  exit /b 1
)
if not exist "%TARGET_DIR%\" (
  if "%DRY_RUN%"=="1" (
    echo [DRY] mkdir "%TARGET_DIR%"
  ) else (
    echo [CREATE] %TARGET_DIR%
    md "%TARGET_DIR%" >nul 2>nul
    if not exist "%TARGET_DIR%\" call :MarkError "Could not create directory: %TARGET_DIR%"
  )
  exit /b 0
)
echo [CLEAR] %TARGET_DIR%
for /f "delims=" %%I in ('dir /a /b "%TARGET_DIR%" 2^>nul') do (
    if exist "%TARGET_DIR%\%%I\" (
      if "%DRY_RUN%"=="1" (
        echo   [DRY] rmdir /s /q "%TARGET_DIR%\%%I"
      ) else (
        echo   rmdir "%TARGET_DIR%\%%I"
        attrib -r -s -h "%TARGET_DIR%\%%I\*" /s /d >nul 2>nul
        rd /s /q "%TARGET_DIR%\%%I" >nul 2>nul
        if exist "%TARGET_DIR%\%%I\" ( echo   [ERROR] Could not remove directory: %TARGET_DIR%\%%I & set /a ERROR_COUNT+=1 )
      )
    ) else (
      if /I "%%~nxI"==".gitkeep" (
        echo   keep "%TARGET_DIR%\%%I"
      ) else (
      if "%DRY_RUN%"=="1" (
        echo   [DRY] del "%TARGET_DIR%\%%I"
      ) else (
        echo   del "%TARGET_DIR%\%%I"
        attrib -r -s -h "%TARGET_DIR%\%%I" >nul 2>nul
        del /f /q "%TARGET_DIR%\%%I" >nul 2>nul
        if exist "%TARGET_DIR%\%%I" ( echo   [ERROR] Could not remove file: %TARGET_DIR%\%%I & set /a ERROR_COUNT+=1 )
      )
      )
    )
)
exit /b 0

:RemoveDir
set "TARGET=%~1"
if not exist "%TARGET%\" exit /b 0
call :AssertInside "%TARGET%" || (
  set /a ERROR_COUNT+=1
  exit /b 1
)
if "%DRY_RUN%"=="1" (
  echo   [DRY] rmdir /s /q "%TARGET%"
  exit /b 0
)
echo   rmdir "%TARGET%"
attrib -r -s -h "%TARGET%\*" /s /d >nul 2>nul
rd /s /q "%TARGET%" >nul 2>nul
if exist "%TARGET%\" call :MarkError "Could not remove directory: %TARGET%"
exit /b 0

:RemoveFile
set "TARGET=%~1"
if not exist "%TARGET%" exit /b 0
call :AssertInside "%TARGET%" || (
  set /a ERROR_COUNT+=1
  exit /b 1
)
if "%DRY_RUN%"=="1" (
  echo   [DRY] del "%TARGET%"
  exit /b 0
)
echo   del "%TARGET%"
attrib -r -s -h "%TARGET%" >nul 2>nul
del /f /q "%TARGET%" >nul 2>nul
if exist "%TARGET%" call :MarkError "Could not remove file: %TARGET%"
exit /b 0

:RemoveFilesByPattern
set "PATTERN=%~1"
echo [FILES] Removing %PATTERN%
call :RemoveFilesByPatternIn "%BASE_DIR%\config" "%PATTERN%"
call :RemoveFilesByPatternIn "%BASE_DIR%\Docs" "%PATTERN%"
call :RemoveFilesByPatternIn "%BASE_DIR%\GitHub" "%PATTERN%"
call :RemoveFilesByPatternIn "%BASE_DIR%\install" "%PATTERN%"
call :RemoveFilesByPatternIn "%BASE_DIR%\system_core" "%PATTERN%"
call :RemoveFilesByPatternIn "%BASE_DIR%\tests" "%PATTERN%"
exit /b 0

:RemoveFilesByPatternIn
set "SCAN_ROOT=%~1"
set "PATTERN=%~2"
if not exist "%SCAN_ROOT%\" exit /b 0
for /f "delims=" %%F in ('dir /a-d /b /s "%SCAN_ROOT%\%PATTERN%" 2^>nul') do (
  if "%DRY_RUN%"=="1" (
    echo   [DRY] del "%%~fF"
  ) else (
    echo   del "%%~fF"
    attrib -r -s -h "%%~fF" >nul 2>nul
    del /f /q "%%~fF" >nul 2>nul
    if exist "%%~fF" ( echo   [ERROR] Could not remove file: %%~fF & set /a ERROR_COUNT+=1 )
  )
)
exit /b 0

:RemoveGeneratedFilesByPattern
set "PATTERN=%~1"
echo [FILES] Removing generated %PATTERN%
for /f "delims=" %%F in ('dir /a-d /b "%BASE_DIR%\%PATTERN%" 2^>nul') do (
  call :IsProtectedSourceFile "%BASE_DIR%\%%~nxF"
  if errorlevel 1 (
    if "%DRY_RUN%"=="1" (
      echo   [DRY] del "%BASE_DIR%\%%~nxF"
    ) else (
      echo   del "%BASE_DIR%\%%~nxF"
      attrib -r -s -h "%BASE_DIR%\%%~nxF" >nul 2>nul
      del /f /q "%BASE_DIR%\%%~nxF" >nul 2>nul
      if exist "%BASE_DIR%\%%~nxF" ( echo   [ERROR] Could not remove file: %BASE_DIR%\%%~nxF & set /a ERROR_COUNT+=1 )
    )
  ) else (
    echo   keep "%BASE_DIR%\%%~nxF"
  )
)
exit /b 0

:IsProtectedSourceFile
set "FILE_ABS=%~f1"
set "REL_PATH=!FILE_ABS:%BASE_PREFIX%=!"
if /I "!REL_PATH:~0,7!"=="config\" exit /b 0
if /I "!REL_PATH:~0,5!"=="Docs\" exit /b 0
if /I "!REL_PATH:~0,7!"=="GitHub\" exit /b 0
if /I "!REL_PATH:~0,8!"=="install\" exit /b 0
if /I "!REL_PATH:~0,12!"=="system_core\" exit /b 0
if /I "!REL_PATH:~0,6!"=="tests\" exit /b 0
exit /b 1

:AssertInside
set "TARGET_ABS=%~f1"
if /I "%TARGET_ABS%"=="%BASE_DIR%" (
  echo [SKIP] Refusing to clean project root directly.
  exit /b 1
)
set "TARGET_HEAD=!TARGET_ABS:~0,%BASE_PREFIX_LEN%!"
if /I "!TARGET_HEAD!"=="!BASE_PREFIX!" exit /b 0
echo [SKIP] Outside project root: %TARGET_ABS%
exit /b 1

:MarkError
echo   [ERROR] %~1
set /a ERROR_COUNT+=1
exit /b 0

:WAIT_KEY
echo Press any key to continue . . .
if not defined AUDION_NO_PAUSE pause >nul
goto :eof

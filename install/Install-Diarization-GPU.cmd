@echo off
rem Opt-in: GPU diarization stack (torch CUDA + pyannote.audio). Heavy (~2.5 GB).
rem Requires a CUDA GPU and a HuggingFace token in config/api_key_huggingface.txt
rem (accept the pyannote/speaker-diarization-3.1 model license on HuggingFace first).
chcp 65001 >nul
setlocal
set "ROOT=%~dp0.."
set "PYEXE=%ROOT%\runtime\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

echo Installing torch (CUDA) + pyannote.audio ...
echo This is a large download and only makes sense on a machine with a CUDA GPU.
"%PYEXE%" -m pip install --progress-bar raw --no-warn-script-location --index-url https://download.pytorch.org/whl/cu128 "torch==2.7.1+cu128" "torchaudio==2.7.1+cu128"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto DONE
"%PYEXE%" -m pip install --progress-bar raw --no-warn-script-location --constraint "%ROOT%\install\constraints_cuda.txt" "pyannote.audio>=3.1,<4.0"
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" if exist "%ROOT%\Tools\gigaam\audion-gigaam-onnx-pack.txt" (
  echo Restoring GigaAM ONNX provider after CUDA/pyannote install...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\install\Restore-GigaAM-ONNX-Provider.ps1"
  set "RC=%ERRORLEVEL%"
)

echo.
echo Done. Enable diarization to use it; CUDA transcription itself uses resident whisper.cpp/cuBLAS.

:DONE
call "%~dp0_pause_if_needed.cmd"
endlocal & exit /b %RC%

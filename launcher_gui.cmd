@echo off
rem Audion Voice AI - GUI launcher (PySide6, iteration 2).
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "PYEXE=%ROOT%runtime\pythonw.exe"

rem Prefer the windowed interpreter (no console); fall back to python.exe / system.
if not exist "%PYEXE%" set "PYEXE=%ROOT%runtime\python.exe"
if not exist "%PYEXE%" (
    where pythonw >nul 2>nul && (set "PYEXE=pythonw") || (
        where py >nul 2>nul && (set "PYEXE=py -3") || set "PYEXE=python"
    )
)

start "" "%PYEXE%" "%ROOT%system_core\ui\app.py" %*
endlocal

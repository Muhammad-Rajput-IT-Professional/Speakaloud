@echo off
setlocal enabledelayedexpansion
title Speakaloud
cd /d "%~dp0"

REM 1. Locate Python executable
set "PY_CMD="

where python >nul 2>&1
if not errorlevel 1 set "PY_CMD=python"

if "%PY_CMD%"=="" (
    where py >nul 2>&1
    if not errorlevel 1 set "PY_CMD=py"
)

if "%PY_CMD%"=="" (
    for %%V in (Python314-arm64 Python314 Python313-arm64 Python313 Python312 Python311 Python310) do (
        if exist "%LocalAppData%\Programs\Python\%%V\python.exe" (
            set "PY_CMD=%LocalAppData%\Programs\Python\%%V\python.exe"
        )
    )
)

if "%PY_CMD%"=="" goto :NO_PYTHON

REM 2. Verify dependencies
"%PY_CMD%" -c "import customtkinter, pymupdf, sounddevice, soundfile, PIL, edge_tts" >nul 2>&1
if errorlevel 1 goto :INSTALL_DEPS

:LAUNCH
echo Launching Speakaloud...
start "" "%PY_CMD%" desktop_app.py
exit /b 0

:INSTALL_DEPS
echo ======================================================================
echo   Speakaloud - First-Time Dependency Setup
echo ======================================================================
echo Installing required libraries (this only runs once)...
echo.
"%PY_CMD%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install dependencies. Please check your internet connection.
    pause
    exit /b 1
)
goto :LAUNCH

:NO_PYTHON
echo ======================================================================
echo   Speakaloud - Python Setup Required
echo ======================================================================
echo Python was not found on this computer.
echo.
where winget >nul 2>&1
if not errorlevel 1 (
    echo Attempting automatic Python installation via Windows Package Manager...
    winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
    if not errorlevel 1 (
        echo.
        echo Python installed successfully! Launching Speakaloud...
        start "" "%~f0"
        exit /b 0
    )
)
echo Please install Python 3.10+ from: https://www.python.org/downloads/
echo (Make sure to check "Add python.exe to PATH" during installation)
start https://www.python.org/downloads/
echo.
pause
exit /b 1

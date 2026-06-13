@echo off
setlocal

cd /d "%~dp0"
title AirMic Desktop Dev

where python >nul 2>nul
if errorlevel 1 (
    echo [AirMic Dev] Python was not found in PATH.
    pause
    exit /b 1
)

python -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo [AirMic Dev] PySide6 is missing. Installing requirements.txt ...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [AirMic Dev] Failed to install requirements.
        pause
        exit /b 1
    )
)

echo [AirMic Dev] Watching app/core/services for changes...
python -m tools.dev_runner

echo.
echo [AirMic Dev] Dev runner exited with code %ERRORLEVEL%
pause

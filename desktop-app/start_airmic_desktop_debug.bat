@echo off
setlocal

cd /d "%~dp0"
title AirMic Desktop Debug

echo [AirMic] Starting debug mode...
echo.

where python
if errorlevel 1 (
    echo [AirMic] Python was not found in PATH.
    pause
    exit /b 1
)

echo [AirMic] Checking PySide6 ...
python -c "import PySide6; print('PySide6 OK')"
if errorlevel 1 (
    echo [AirMic] Missing dependency. Installing requirements.txt ...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [AirMic] Failed to install requirements.
        pause
        exit /b 1
    )
)

echo.
echo [AirMic] Launching GUI ...
python -m app.main
echo.
echo [AirMic] GUI exited with code %ERRORLEVEL%
pause

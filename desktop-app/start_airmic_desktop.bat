@echo off
setlocal

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [AirMic] Python was not found in PATH.
    echo Please install Python 3 and make sure "python" works in cmd.
    pause
    exit /b 1
)

python -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo [AirMic] PySide6 is missing. Installing requirements.txt ...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [AirMic] Failed to install requirements.
        pause
        exit /b 1
    )
)

python -m app.main
set EXIT_CODE=%ERRORLEVEL%

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [AirMic] App exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%

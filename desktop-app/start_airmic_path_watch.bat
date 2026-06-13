@echo off
setlocal

cd /d "%~dp0"

set "SCRIPT=%~dp0tools\diagnostics\watch_airmic_paths.ps1"

if not exist "%SCRIPT%" (
    echo [AirMic] Diagnostic script not found:
    echo %SCRIPT%
    pause
    exit /b 1
)

powershell -NoLogo -NoExit -ExecutionPolicy Bypass -File "%SCRIPT%"

exit /b %ERRORLEVEL%

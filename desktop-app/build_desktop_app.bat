@echo off
setlocal

cd /d "%~dp0"
python build_desktop_app.py
if errorlevel 1 (
    echo.
    echo [AirMic Build] 打包失败。
    pause
    exit /b 1
)

echo.
echo [AirMic Build] 打包完成：dist\AirMicDesktop
echo [AirMic Build] 压缩包：dist\AirMicDesktop-win64.zip
pause

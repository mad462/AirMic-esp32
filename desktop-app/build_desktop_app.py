from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from textwrap import dedent

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parent
DIST_ROOT = PROJECT_ROOT / "dist"
BUILD_ROOT = PROJECT_ROOT / "build_pyinstaller"
SPEC_PATH = PROJECT_ROOT / "AirMicDesktop.spec"
ARCHIVE_PATH = DIST_ROOT / "AirMicDesktop-win64.zip"
ICON_SOURCE = PROJECT_ROOT / "icon.png"
ICON_ICO = PROJECT_ROOT / "build_pyinstaller_icon.ico"
VBS_LAUNCHER = DIST_ROOT / "启动 AirMic 桌面端.vbs"


def run(command: list[str]) -> None:
    completed = subprocess.run(command, cwd=str(PROJECT_ROOT))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def write_spec() -> None:
    spec_text = """# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

PROJECT_ROOT = Path.cwd().resolve()

hiddenimports = collect_submodules("sounddevice")

a = Analysis(
    ["app/main.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=[
        (str(PROJECT_ROOT / "tools" / "audio_probe" / "bin" / "AirMicAudioProbe_v5.exe"), "tools/audio_probe/bin"),
        (str(PROJECT_ROOT / "tools" / "audio_probe" / "bin" / "AirMicAudioProbe_status.exe"), "tools/audio_probe/bin"),
        (str(PROJECT_ROOT / "tools" / "audio_probe" / "bin" / "NAudio.dll"), "tools/audio_probe/bin"),
    ],
    datas=[
        (str(PROJECT_ROOT / "app" / "assets"), "app/assets"),
        (str(PROJECT_ROOT / "icon.png"), "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AirMicDesktop",
    icon=str(PROJECT_ROOT / "build_pyinstaller_icon.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AirMicDesktop",
)
"""
    SPEC_PATH.write_text(spec_text, encoding="utf-8")


def ensure_ico_icon() -> None:
    if not ICON_SOURCE.exists():
        raise FileNotFoundError(f"未找到应用图标文件：{ICON_SOURCE}")
    with Image.open(ICON_SOURCE) as image:
        rgba = image.convert("RGBA")
        rgba.save(
            ICON_ICO,
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )


def write_launcher(dist_dir: Path) -> None:
    launcher = dist_dir / "启动 AirMic 桌面端.bat"
    launcher.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n"
        "start \"\" \"AirMicDesktop\\AirMicDesktop.exe\"\r\n",
        encoding="utf-8",
    )


def write_vbs_launcher(dist_dir: Path) -> None:
    launcher = dist_dir / "启动 AirMic 桌面端.vbs"
    launcher.write_text(
        dedent(
            """
            Set shell = CreateObject("WScript.Shell")
            Set fso = CreateObject("Scripting.FileSystemObject")
            baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
            exePath = fso.BuildPath(baseDir, "AirMicDesktop\\AirMicDesktop.exe")
            shell.Run Chr(34) & exePath & Chr(34), 1, False
            """
        ).strip()
        + "\r\n",
        encoding="utf-8",
    )


def write_shortcut_launcher(dist_dir: Path) -> None:
    shortcut_path = dist_dir / "启动 AirMic 桌面端.lnk"
    script = dedent(
        f"""
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut('{shortcut_path}')
        $shortcut.TargetPath = '{dist_dir / "AirMicDesktop" / "AirMicDesktop.exe"}'
        $shortcut.WorkingDirectory = '{dist_dir / "AirMicDesktop"}'
        $shortcut.IconLocation = '{dist_dir / "AirMicDesktop" / "AirMicDesktop.exe"},0'
        $shortcut.Save()
        """
    ).strip()
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        cwd=str(PROJECT_ROOT),
    )


def write_distribution_readme(dist_dir: Path) -> None:
    readme = dist_dir / "README-分发说明.txt"
    readme.write_text(
        "AirMic 桌面端分发说明\r\n"
        "\r\n"
        "1. 启动方式\r\n"
        "- 推荐双击：启动 AirMic 桌面端.lnk\r\n"
        "- 也可以双击：启动 AirMic 桌面端.vbs\r\n"
        "- 或双击：启动 AirMic 桌面端.bat\r\n"
        "- 或直接运行：AirMicDesktop\\AirMicDesktop.exe\r\n"
        "\r\n"
        "2. 分发时要带走哪些文件\r\n"
        "- 需要携带整个 dist 目录或 AirMicDesktop-win64.zip 的完整内容。\r\n"
        "- 不能只拷贝 AirMicDesktop.exe。\r\n"
        "- 图标、音频探针、Qt 运行库都在 AirMicDesktop\\_internal 下面。\r\n"
        "\r\n"
        "3. 快捷键 / Tone 配置持久化\r\n"
        "- 应用不会把配置写回程序目录。\r\n"
        "- 配置会写到当前 Windows 用户目录：%APPDATA%\\AirMic\\desktop-app-settings.json\r\n"
        "- 升级应用不会覆盖这个配置文件，不同 Windows 用户也互相独立。\r\n"
        "\r\n"
        "4. 串口与设备\r\n"
        "- 默认优先选择上次使用的串口；首次启动会回退到可用串口或 COM10。\r\n"
        "- 如果目标机器不是 COM10，可以在界面里重新选择。\r\n"
        "\r\n"
        "5. 常见问题\r\n"
        "- 如果启动后没有监听到设备，请确认 Windows 已连接 ESP32-AirMic-HFP。\r\n"
        "- 如果界面能开但没有识别音频，请确认录音设备里能看到 Hands-Free 麦克风。\r\n"
        "- 如果升级后快捷键丢失，请检查 %APPDATA%\\AirMic\\desktop-app-settings.json 是否被清理。\r\n",
        encoding="utf-8",
    )


def write_debug_launcher(dist_dir: Path) -> None:
    launcher = dist_dir / "启动 AirMic 桌面端-调试.bat"
    launcher.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n"
        "\"AirMicDesktop\\AirMicDesktop.exe\"\r\n"
        "echo.\r\n"
        "echo [AirMic] App exited with code %errorlevel%.\r\n"
        "pause\r\n",
        encoding="utf-8",
    )


def write_zip_archive(dist_dir: Path) -> None:
    if ARCHIVE_PATH.exists():
        ARCHIVE_PATH.unlink()
    with ZipFile(ARCHIVE_PATH, "w", compression=ZIP_DEFLATED) as archive:
        for file_path in sorted(dist_dir.rglob("*")):
            if file_path == ARCHIVE_PATH:
                continue
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(dist_dir))


def main() -> int:
    if BUILD_ROOT.exists():
        shutil.rmtree(BUILD_ROOT)
    if DIST_ROOT.exists():
        shutil.rmtree(DIST_ROOT)

    ensure_ico_icon()
    write_spec()
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST_ROOT),
            "--workpath",
            str(BUILD_ROOT),
            str(SPEC_PATH),
        ]
    )
    write_launcher(DIST_ROOT)
    write_vbs_launcher(DIST_ROOT)
    write_shortcut_launcher(DIST_ROOT)
    write_debug_launcher(DIST_ROOT)
    write_distribution_readme(DIST_ROOT)
    write_zip_archive(DIST_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    """
    返回应用运行根目录。

    开发模式下：
    - 以 desktop-app 目录为根。

    PyInstaller 打包后：
    - 以解包后的运行时资源目录为根。
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def app_asset_path(*parts: str) -> Path:
    return app_root().joinpath("app", "assets", *parts)


def tool_path(*parts: str) -> Path:
    return app_root().joinpath("tools", *parts)


def project_file_path(*parts: str) -> Path:
    return app_root().joinpath(*parts)


def app_icon_path() -> Path:
    return app_root().joinpath("icon.png")

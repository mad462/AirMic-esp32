from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


def _default_diagnostics_log_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "AirMic" / "desktop-app-diagnostics.log"
    return Path.home() / ".airmic" / "desktop-app-diagnostics.log"


class DiagnosticsLogService:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_diagnostics_log_path()

    def append(self, level: str, message: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {level.upper()} {message}\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _default_settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "AirMic" / "desktop-app-settings.json"
    return Path.home() / ".airmic" / "desktop-app-settings.json"


class SettingsService:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_settings_path()

    def load(self) -> dict[str, Any]:
        try:
            if not self.path.exists():
                return {}
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

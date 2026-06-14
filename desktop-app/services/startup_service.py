from __future__ import annotations

from pathlib import Path
import sys

try:
    import winreg
except Exception:  # pragma: no cover - non-Windows test env
    winreg = None


RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "AirMicDesktop"


class StartupService:
    def __init__(self, executable_path: Path | None = None, launch_script_path: Path | None = None) -> None:
        self.executable_path = (executable_path or Path(sys.executable)).resolve()
        default_script = Path(__file__).resolve().parents[1] / "app" / "main.py"
        self.launch_script_path = (launch_script_path or default_script).resolve()
        exe_name = self.executable_path.name.lower()
        self._is_frozen = bool(getattr(sys, "frozen", False)) or (
            self.executable_path.suffix.lower() == ".exe" and exe_name not in {"python.exe", "pythonw.exe"}
        )

    def is_enabled(self) -> bool:
        if winreg is None:
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
                value, _value_type = winreg.QueryValueEx(key, RUN_VALUE_NAME)
        except FileNotFoundError:
            return False
        except OSError:
            return False
        return str(value).strip() == self._run_command()

    def set_enabled(self, enabled: bool) -> bool:
        if winreg is None:
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, self._run_command())
                else:
                    try:
                        winreg.DeleteValue(key, RUN_VALUE_NAME)
                    except FileNotFoundError:
                        pass
        except OSError:
            return False
        return self.is_enabled() == enabled

    def _run_command(self) -> str:
        if self._is_frozen:
            return f'"{self.executable_path}"'
        pythonw_path = self.executable_path.with_name("pythonw.exe")
        return f'"{pythonw_path}" "{self.launch_script_path}"'
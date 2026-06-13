from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from core.models.app_state import AudioDeviceStatusSnapshot


STATUS_LINE_RE = re.compile(r"^STATUS\t(.+)$")
HINT_LINE_RE = re.compile(r"^HINT\t(.+)$")


def _unescape_status_value(value: str) -> str:
    return value.replace("\\t", "\t").replace("\\\\", "\\")


@dataclass(frozen=True)
class AudioStatusWatchEvent:
    snapshot: AudioDeviceStatusSnapshot
    raw_text: str
    hint_kind: str = ""
    hint_operation: str = ""
    hint_name: str = ""


def parse_status_watch_line(text: str) -> AudioStatusWatchEvent | None:
    stripped = text.strip()
    hint_match = HINT_LINE_RE.match(stripped)
    if hint_match:
        values: dict[str, str] = {}
        for part in hint_match.group(1).split("\t"):
            if "=" not in part:
                continue
            key, raw_value = part.split("=", 1)
            values[key] = _unescape_status_value(raw_value)

        return AudioStatusWatchEvent(
            snapshot=AudioDeviceStatusSnapshot(),
            raw_text=stripped,
            hint_kind=values.get("kind", "").strip().lower(),
            hint_operation=values.get("operation", "").strip().lower(),
            hint_name=values.get("name", "").strip(),
        )

    match = STATUS_LINE_RE.match(stripped)
    if not match:
        return None

    values: dict[str, str] = {}
    for part in match.group(1).split("\t"):
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        values[key] = _unescape_status_value(raw_value)

    default_comm = values.get("default_comm", "").strip()
    default_multi = values.get("default_multi", "").strip()
    current_input_name = ""
    if default_comm and default_comm != "<none>":
        current_input_name = default_comm
    elif default_multi and default_multi != "<none>":
        current_input_name = default_multi

    detected_airmic_input_name = values.get("airmic", "").strip()
    detected_airmic_input_active = values.get("airmic_state", "").strip() == "Active"
    device_present_text = values.get("device_present", "").strip().lower()
    bt_connected_text = values.get("bt_connected", "").strip().lower()
    if bt_connected_text in {"true", "false"}:
        detected_airmic_device_present = bt_connected_text == "true"
    elif device_present_text in {"true", "false"}:
        detected_airmic_device_present = device_present_text == "true"
    else:
        detected_airmic_device_present = bool(detected_airmic_input_active)
    has_any_available_input = values.get("any_input", "").strip().lower() == "true"

    if detected_airmic_input_active and not current_input_name:
        current_input_name = detected_airmic_input_name
    if detected_airmic_input_name and not detected_airmic_input_active and current_input_name == detected_airmic_input_name:
        current_input_name = ""

    return AudioStatusWatchEvent(
        snapshot=AudioDeviceStatusSnapshot(
            current_input_name=current_input_name,
            detected_airmic_input_name=detected_airmic_input_name,
            detected_airmic_input_active=detected_airmic_input_active,
            detected_airmic_device_present=detected_airmic_device_present,
            has_any_available_input=has_any_available_input,
        ),
        raw_text=stripped,
    )


class AudioStatusWatchService:
    def __init__(self, project_root: Path, watch_exe: Path | None = None, name_filter: str = "ESP32-AirMic-HFP") -> None:
        self.project_root = project_root
        self.watch_exe = watch_exe or (project_root / "tools" / "audio_probe" / "bin" / "AirMicAudioProbe_status.exe")
        self.name_filter = name_filter
        self.process: subprocess.Popen[str] | None = None
        self.thread: threading.Thread | None = None
        self.stop_requested = threading.Event()
        self._latest_snapshot: AudioDeviceStatusSnapshot | None = None

    def build_command(self) -> list[str]:
        return [
            str(self.watch_exe),
            "--watch-status",
            "--name",
            self.name_filter,
        ]

    def latest_snapshot(self) -> AudioDeviceStatusSnapshot | None:
        return self._latest_snapshot

    def consume_output_lines(self, lines: Iterable[str]) -> list[AudioStatusWatchEvent]:
        events: list[AudioStatusWatchEvent] = []
        for line in lines:
            event = parse_status_watch_line(line)
            if event is None:
                continue
            self._latest_snapshot = event.snapshot
            events.append(event)
        return events

    def start(
        self,
        emit: Callable[[AudioStatusWatchEvent], None],
        emit_log: Callable[[str], None] | None = None,
    ) -> bool:
        if self.thread and self.thread.is_alive():
            return False
        self.stop_requested.clear()
        self.thread = threading.Thread(
            target=self._thread_main,
            args=(emit, emit_log),
            name="airmic-audio-status-watch",
            daemon=True,
        )
        self.thread.start()
        return True

    def stop(self) -> None:
        self.stop_requested.set()
        proc = self.process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _thread_main(
        self,
        emit: Callable[[AudioStatusWatchEvent], None],
        emit_log: Callable[[str], None] | None,
    ) -> None:
        if not self.watch_exe.exists():
            if emit_log:
                emit_log(f"audio status watcher not found: {self.watch_exe}")
            return

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = subprocess.Popen(
                self.build_command(),
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as exc:
            if emit_log:
                emit_log(f"audio status watcher start failed: {exc}")
            self.process = None
            return

        proc = self.process
        assert proc is not None
        assert proc.stdout is not None

        try:
            for line in proc.stdout:
                if self.stop_requested.is_set():
                    break
                event = parse_status_watch_line(line)
                if event is None:
                    if emit_log:
                        emit_log(line.strip())
                    continue
                self._latest_snapshot = event.snapshot
                emit(event)
        finally:
            if self.stop_requested.is_set() and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            self.process = None

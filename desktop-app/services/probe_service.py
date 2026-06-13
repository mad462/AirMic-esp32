from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


PROBE_EVENT_TONE = "tone"
PROBE_EVENT_RMS = "rms"
PROBE_EVENT_LOG = "log"

DEFAULT_PROBE_NAME = "ESP32-AirMic-HFP"
DEFAULT_MIN_AUDIO_TIMEOUT_MS = 4000

TONE_LINE_RE = re.compile(r"\b(TONE|VAD)\s+(START|STOP|A|B|C)\b.*")
PROBE_RMS_RE = re.compile(r"RMS\s+([0-9.]+)\s+peak\s+([0-9.]+)\s+nonzero\s+(\d+)/(\d+)")


@dataclass(frozen=True)
class ProbeEvent:
    event_kind: str
    raw_text: str
    tone_source: str = ""
    tone_event: str = ""
    tone_rms: float = 0.0
    tone_peak: float = 0.0


class ProbeWatchdog:
    def __init__(self, clock: Callable[[], float] | None = None, min_audio_timeout_s: float = 4.0) -> None:
        self.clock = clock or time.monotonic
        self.min_audio_timeout_s = min_audio_timeout_s
        self.probe_started_at = 0.0
        self.capture_started_at: float | None = None
        self.last_audio_line_at = 0.0

    def mark_probe_started(self) -> None:
        now = self.clock()
        self.probe_started_at = now
        self.capture_started_at = None
        self.last_audio_line_at = now

    def mark_capture_started(self) -> None:
        self.capture_started_at = self.clock()

    def mark_audio_line(self) -> None:
        self.last_audio_line_at = self.clock()

    def should_restart_for_no_audio(self) -> bool:
        if self.capture_started_at is None:
            return False
        return (self.clock() - self.last_audio_line_at) >= self.min_audio_timeout_s


def parse_probe_output_line(text: str) -> ProbeEvent | None:
    stripped = text.strip()
    if not stripped:
        return None

    tone_match = TONE_LINE_RE.search(stripped)
    if tone_match:
        return ProbeEvent(
            event_kind=PROBE_EVENT_TONE,
            raw_text=stripped,
            tone_source=tone_match.group(1),
            tone_event=tone_match.group(2),
        )

    rms_match = PROBE_RMS_RE.search(stripped)
    if rms_match:
        return ProbeEvent(
            event_kind=PROBE_EVENT_RMS,
            raw_text=stripped,
            tone_rms=float(rms_match.group(1)),
            tone_peak=float(rms_match.group(2)),
        )

    if (
        "Active endpoint" in stripped
        or "Using device" in stripped
        or "Capturing" in stripped
        or "Waiting" in stripped
        or "No audio callbacks" in stripped
        or "[Unplugged]" in stripped
        or "[NotPresent]" in stripped
        or stripped.startswith("CANDIDATE ")
    ):
        return ProbeEvent(event_kind=PROBE_EVENT_LOG, raw_text=stripped)

    return None


class ProbeService:
    def __init__(
        self,
        project_root: Path,
        probe_exe: Path | None = None,
        probe_name: str = DEFAULT_PROBE_NAME,
        min_audio_timeout_ms: int = DEFAULT_MIN_AUDIO_TIMEOUT_MS,
    ) -> None:
        self.project_root = project_root
        self.probe_exe = probe_exe or (project_root / "tools" / "audio_probe" / "bin" / "AirMicAudioProbe_v5.exe")
        self.probe_name = probe_name
        self.min_audio_timeout_ms = min_audio_timeout_ms
        self.watchdog = ProbeWatchdog(min_audio_timeout_s=min_audio_timeout_ms / 1000.0)
        self.process: subprocess.Popen[str] | None = None
        self.thread: threading.Thread | None = None
        self.stop_requested = threading.Event()

    def build_command(self) -> list[str]:
        return [
            str(self.probe_exe),
            "--all",
            "--name",
            self.probe_name,
            "--wait-active",
            "--seconds",
            "0",
            "--min-audio-timeout-ms",
            str(self.min_audio_timeout_ms),
        ]

    def consume_output_lines(self, lines: Iterable[str]) -> list[ProbeEvent]:
        events: list[ProbeEvent] = []
        self.watchdog.mark_probe_started()
        for line in lines:
            event = parse_probe_output_line(line)
            if event is None:
                continue
            if event.event_kind in (PROBE_EVENT_TONE, PROBE_EVENT_RMS):
                self.watchdog.mark_audio_line()
            elif "Capturing" in event.raw_text:
                self.watchdog.mark_capture_started()
            events.append(event)
        return events

    def pump_output_lines(self, lines: Iterable[str], emit: Callable[[ProbeEvent], None]) -> None:
        self.watchdog.mark_probe_started()
        for line in lines:
            event = parse_probe_output_line(line)
            if event is None:
                continue
            if event.event_kind in (PROBE_EVENT_TONE, PROBE_EVENT_RMS):
                self.watchdog.mark_audio_line()
            if "Capturing" in event.raw_text:
                self.watchdog.mark_capture_started()
            emit(event)

    def start(self, emit: Callable[[ProbeEvent], None], emit_log: Callable[[str], None] | None = None) -> bool:
        if self.thread and self.thread.is_alive():
            if emit_log:
                emit_log("tone monitor already running")
            return False
        self.stop_requested.clear()
        self.thread = threading.Thread(
            target=self._thread_main,
            args=(emit, emit_log),
            name="airmic-probe-service",
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

    def _thread_main(self, emit: Callable[[ProbeEvent], None], emit_log: Callable[[str], None] | None) -> None:
        if emit_log:
            emit_log("starting HFP tone probe")
        if not self.probe_exe.exists():
            if emit_log:
                emit_log(f"tone probe not found: {self.probe_exe}")
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
                emit_log(f"tone probe start failed: {exc}")
            self.process = None
            return

        proc = self.process
        assert proc is not None
        assert proc.stdout is not None
        try:
            self.pump_output_lines(self._iter_stdout(proc.stdout), emit=emit)
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

    def _iter_stdout(self, stream: Iterable[str]) -> Iterable[str]:
        for line in stream:
            if self.stop_requested.is_set():
                break
            yield line

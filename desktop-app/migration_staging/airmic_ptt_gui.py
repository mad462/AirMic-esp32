import asyncio
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox
from tkinter import ttk
from typing import Callable

import numpy as np
import serial
import sounddevice as sd
from bleak import BleakClient, BleakScanner
from serial.tools import list_ports

from airmic_ptt_bridge import DEFAULT_CHAR_UUID, DEFAULT_DEVICE_NAME, KEY_SPECS, is_right_alt_down, send_combo


SCAN_TIMEOUT_S = 5.0
RETRY_DELAY_S = 2.0
UI_POLL_MS = 80
MIC_BLOCKSIZE = 1024
MIC_SAMPLE_RATE = 8000
MIC_DEVICE_KEYWORDS = ("airmic", "hands-free", "hands free", "headset", "hfp", "esp32")
DEFAULT_SERIAL_PORT = "COM10"
SERIAL_BAUD = 115200
SERIAL_MIC_RE = re.compile(r"mic peak=(\d+)\s+rms=(\d+)")
SERIAL_TX_MIC_RE = re.compile(r"tx_peak=(\d+)\s+tx_rms=(\d+)")
SERIAL_HFP_CONN_RE = re.compile(r"HFP connection state=([a-z0-9_]+)")
SERIAL_HFP_AUDIO_RE = re.compile(r"HFP audio state=([a-z0-9_]+)")
SERIAL_CFG_RE = re.compile(r"(cfg .*|audio config .*)")
SERIAL_SR_RESULT_RE = re.compile(r"sr\s+(on|off)\s+result:\s+([A-Z0-9_]+)")
SERIAL_CFG_STATUS_RE = re.compile(
    r"cfg gain_q8=(\d+)\s+gain=([0-9.]+)x\s+gate=(\d+)\s+tone_q8=(\d+)\s+tone=([0-9.]+)x\s+shift=(\d+)\s+sr=(on|off)\s+sr_init=(yes|no)"
)
SERIAL_RECORD_MODE_STATUS_RE = re.compile(r"record mode=(always|ptt)")
PTT_EVENT_START = 1
PTT_EVENT_STOP = 2
PTT_EVENT_ACTIVE = 3
PTT_ADV_COMPANY_ID = 0xFFFF
PTT_ADV_MAGIC = b"AM"
PTT_ADV_COMPANY_PREFIX = bytes((PTT_ADV_COMPANY_ID & 0xff, (PTT_ADV_COMPANY_ID >> 8) & 0xff))
STATE_IDLE_GATT = "IDLE_GATT"
STATE_ACTIVE_SCANNING = "ACTIVE_SCANNING"
POST_STOP_RECONNECT_DELAY_S = 3.0
ACTIVE_HEARTBEAT_TIMEOUT_S = 6.0
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TONE_PROBE_EXE = PROJECT_ROOT / "pc_audio_bridge_cs" / "bin" / "AirMicAudioProbe_v5.exe"
TONE_PROBE_NAME = "ESP32-AirMic-HFP"
TONE_LINE_RE = re.compile(r"\b(TONE|VAD)\s+(START|STOP)\b.*")
PROBE_RMS_RE = re.compile(r"RMS\s+([0-9.]+)\s+peak\s+([0-9.]+)\s+nonzero\s+(\d+)/(\d+)")
SERIAL_TONE_RE = re.compile(r"(serial command:.*|queued HFP tone.*|injecting HFP tone.*|finished HFP tone.*|tone autotest.*)")


@dataclass
class BridgeConfig:
    name: str = DEFAULT_DEVICE_NAME
    char_uuid: str = DEFAULT_CHAR_UUID
    scan_timeout_s: float = SCAN_TIMEOUT_S
    retry_delay_s: float = RETRY_DELAY_S
    active_heartbeat_timeout_s: float = ACTIVE_HEARTBEAT_TIMEOUT_S


@dataclass
class PttEvent:
    button_id: int
    event_type: int
    session_id: int | None = None
    seq: int | None = None
    source: str = "GATT"

    @property
    def is_start(self) -> bool:
        return self.event_type == PTT_EVENT_START

    @property
    def is_stop(self) -> bool:
        return self.event_type == PTT_EVENT_STOP

    @property
    def is_active(self) -> bool:
        return self.event_type == PTT_EVENT_ACTIVE

    @property
    def action(self) -> str:
        if self.is_start:
            return "start"
        if self.is_stop:
            return "stop"
        if self.is_active:
            return "active"
        return f"event {self.event_type}"


@dataclass
class ActiveShortcut:
    keys: list[str]
    session_id: int | None
    seq: int | None = None


DEFAULT_BUTTON_BINDINGS = {
    0: ["right_alt"],
    1: ["left_ctrl", "left_win"],
    2: ["right_alt"],
    3: ["left_ctrl", "left_win"],
}

SHORTCUT_PRESETS = {
    "右 Alt": ["right_alt"],
    "左 Alt": ["left_alt"],
    "Ctrl + Win": ["left_ctrl", "left_win"],
    "禁用": [],
}

RECORD_MODE_LABEL_PTT = "按下才录音"
RECORD_MODE_LABEL_ALWAYS = "持续录音（调试）"
SR_MODE_LABEL_LEGACY = "旧链路（稳定）"
SR_MODE_LABEL_ESP_SR = "ESP-SR 降噪实验"

BUTTON_LABELS = {
    0: "GPIO0 / BOOT",
    1: "GPIO5",
    2: "GPIO18",
    3: "GPIO19",
}


def clamp_int(value: int, min_value: int, max_value: int) -> int:
    return min(max_value, max(min_value, int(value)))


def sanitize_audio_config_values(
    gain_q8: int, noise_gate: int, tone_gain_q8: int, sample_shift: int
) -> tuple[int, int, int, int]:
    return (
        clamp_int(gain_q8, 64, 4096),
        clamp_int(noise_gate, 0, 6000),
        clamp_int(tone_gain_q8, 64, 768),
        clamp_int(sample_shift, 8, 18),
    )


def build_audio_config_commands(gain_q8: int, noise_gate: int, tone_gain_q8: int, sample_shift: int = 14) -> list[str]:
    gain_q8, noise_gate, tone_gain_q8, sample_shift = sanitize_audio_config_values(
        gain_q8, noise_gate, tone_gain_q8, sample_shift
    )
    return [
        f"cfg gain {gain_q8}",
        f"cfg gate {noise_gate}",
        f"cfg tone {tone_gain_q8}",
        f"cfg shift {sample_shift}",
    ]


def should_restart_probe_after_exit(
    stop_requested: bool, restart_requested: bool, probe_exit_code: int | None
) -> bool:
    return not stop_requested and not restart_requested and probe_exit_code is not None


def choose_default_serial_port(ports: list[str], preferred: str = DEFAULT_SERIAL_PORT) -> str:
    normalized = [port.strip() for port in ports if port and port.strip()]
    if preferred in normalized:
        return preferred
    return normalized[0] if normalized else ""


def parse_probe_tone_line(text: str) -> tuple[str, str] | None:
    match = TONE_LINE_RE.search(text)
    if not match:
        return None
    return match.group(1), match.group(2)


def build_record_mode_command(label: str) -> str:
    return "mode record always" if label == RECORD_MODE_LABEL_ALWAYS else "mode record ptt"


def build_sr_mode_command(label: str) -> str:
    return "sr on" if label == SR_MODE_LABEL_ESP_SR else "sr off"


class ToneShortcutController:
    def __init__(
        self,
        keys: list[str] | None = None,
        sender: Callable[[list[str], bool, str], None] = send_combo,
        clock: Callable[[], float] = time.monotonic,
        min_same_event_interval_s: float = 0.75,
        send_mode: str = "scan",
        trigger_style: str = "hold",
    ):
        self.keys = list(keys if keys is not None else SHORTCUT_PRESETS["右 Alt"])
        self.sender = sender
        self.send_mode = send_mode
        self.trigger_style = "tap" if trigger_style == "tap" else "hold"
        self.clock = clock
        self.min_same_event_interval_s = min_same_event_interval_s
        self.active_keys: list[str] = []
        self.last_tone_event = ""
        self.last_tone_event_ts = -999.0

    @property
    def is_down(self) -> bool:
        return bool(self.active_keys)

    @property
    def label(self) -> str:
        if not self.keys:
            return "禁用"
        return " + ".join(KEY_SPECS[key]["label"] for key in self.keys)

    def set_keys(self, keys: list[str]) -> None:
        if self.is_down:
            self.release_all()
        self.keys = list(keys)

    def set_send_mode(self, mode: str) -> None:
        if self.is_down:
            self.release_all()
        self.send_mode = "vk" if mode == "vk" else "scan"

    def set_trigger_style(self, style: str) -> None:
        if self.is_down:
            self.release_all()
        self.trigger_style = "tap" if style == "tap" else "hold"

    def handle_tone(self, tone_event: str) -> str:
        normalized = tone_event.strip().upper()
        now = self.clock()
        if (
            normalized in ("START", "STOP")
            and normalized == self.last_tone_event
            and now - self.last_tone_event_ts < self.min_same_event_interval_s
        ):
            return f"tone {normalized} debounced"
        if normalized in ("START", "STOP"):
            self.last_tone_event = normalized
            self.last_tone_event_ts = now
        if normalized == "START":
            return self.press()
        if normalized == "STOP":
            return self.release()
        return f"忽略未知音频编码: {tone_event}"

    def press(self) -> str:
        if not self.keys:
            return "收到 START：快捷键已禁用"
        if self.is_down:
            return f"收到 START：{self._label_for(self.active_keys)} 已经触发"
        if self.trigger_style == "tap":
            self.sender(self.keys, True, self.send_mode)
            self.sender(self.keys, False, self.send_mode)
            self.active_keys = list(self.keys)
            return f"收到 START：触发 {self._label_for(self.active_keys)}"
        self.sender(self.keys, True, self.send_mode)
        self.active_keys = list(self.keys)
        return f"收到 START：按下 {self._label_for(self.active_keys)}"

    def release(self) -> str:
        if not self.is_down:
            return "收到 STOP：快捷键本来就是松开"
        keys = list(self.active_keys)
        if self.trigger_style == "tap":
            self.sender(keys, True, self.send_mode)
            self.sender(keys, False, self.send_mode)
            self.active_keys = []
            return f"收到 STOP：触发 {self._label_for(keys)}"
        self.sender(keys, False, self.send_mode)
        self.active_keys = []
        return f"收到 STOP：释放 {self._label_for(keys)}"

    def release_all(self) -> str:
        if not self.is_down:
            return "快捷键已经全部释放"
        if self.trigger_style == "tap":
            self.active_keys = []
            return "快捷键状态已复位"
        return self.release()

    def _label_for(self, keys: list[str]) -> str:
        if not keys:
            return "禁用"
        return " + ".join(KEY_SPECS[key]["label"] for key in keys)


class ToneProbeWatchdog:
    def __init__(self, clock: Callable[[], float] = time.monotonic, no_audio_timeout_s: float = 4.0):
        self.clock = clock
        self.no_audio_timeout_s = no_audio_timeout_s
        self.capture_started_ts: float | None = None
        self.audio_seen = False

    def mark_probe_started(self) -> None:
        self.capture_started_ts = None
        self.audio_seen = False

    def mark_capture_started(self) -> None:
        self.capture_started_ts = self.clock()
        self.audio_seen = False

    def mark_audio_line(self) -> None:
        self.audio_seen = True

    def should_restart_for_no_audio(self) -> bool:
        if self.capture_started_ts is None or self.audio_seen:
            return False
        return self.clock() - self.capture_started_ts >= self.no_audio_timeout_s


class GuiBridgeWorker:
    def __init__(self, config: BridgeConfig, events: queue.Queue):
        self.config = config
        self.events = events
        self.stop_event: asyncio.Event | None = None
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.active_buttons: dict[int, ActiveShortcut] = {}
        self.bindings = {button_id: list(keys) for button_id, keys in DEFAULT_BUTTON_BINDINGS.items()}
        self.state = STATE_IDLE_GATT
        self.active_voice_session: int | None = None
        self.active_voice_last_seen = 0.0
        self.seen_events: set[tuple[int, int | None, int | None]] = set()
        self.enter_active_scan = False
        self.cooldown_until = 0.0
        self.closing = False

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._thread_main, name="airmic-ble-worker", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.closing = True
        if self.loop and self.stop_event:
            self.loop.call_soon_threadsafe(self.stop_event.set)
        try:
            self._release_all()
        except Exception as exc:
            self._emit("error", f"释放快捷键失败：{exc}")

    def reconnect(self) -> None:
        if self.loop and self.stop_event:
            self.loop.call_soon_threadsafe(self.stop_event.set)

    def _emit(self, kind: str, message: str = "", **fields) -> None:
        self.events.put({"kind": kind, "message": message, **fields})

    def _thread_main(self) -> None:
        asyncio.run(self._run_forever())

    async def _run_forever(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._emit("status", "worker started", running=True)
        while not self.closing:
            self.stop_event = asyncio.Event()
            self.enter_active_scan = False
            try:
                now = time.monotonic()
                if now < self.cooldown_until:
                    delay = self.cooldown_until - now
                    self._emit("status", f"cooldown before GATT reconnect {delay:.1f}s")
                    await asyncio.sleep(delay)
                await self._connect_once(self.stop_event)
            except Exception as exc:
                self._release_all()
                self._emit("error", f"BLE bridge error: {exc}")
            finally:
                self._emit(
                    "connection",
                    "disconnected",
                    connected=False,
                    notify_enabled=False,
                    address="-",
                )

            if self.closing:
                break
            if self.stop_event and self.stop_event.is_set():
                self._emit("status", "manual reconnect requested")
                await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(self.config.retry_delay_s)
        self._release_all()
        self._emit("status", "worker stopped", running=False)

    async def _connect_once(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            self.state = STATE_IDLE_GATT
            self._emit("scan", f"scanning for {self.config.name}", scanning=True)
            found = await self._find_device()
            self._emit("scan", "scan complete", scanning=False)
            if found is None:
                self._emit("connection", "device not found, retrying", connected=False, address="-")
                await self._sleep_or_stop(stop_event, self.config.retry_delay_s)
                continue

            self._emit(
                "connection",
                f"connecting {found.address}",
                connected=False,
                address=found.address,
            )

            async with BleakClient(found, disconnected_callback=self._on_disconnect) as client:
                self._emit(
                    "connection",
                    "connected, subscribing notifications",
                    connected=True,
                    notify_enabled=False,
                    address=found.address,
                )

                def on_notify(_, data: bytearray):
                    if not data:
                        return
                    event = self._parse_ptt_event(data, source="GATT")
                    if event is None:
                        self._emit("error", f"unknown PTT payload: {bytes(data).hex(' ')}")
                        return
                    try:
                        should_disconnect = self._handle_ptt_event(event)
                        if should_disconnect and self.loop and self.stop_event:
                            self.enter_active_scan = True
                            self.loop.call_soon_threadsafe(self.stop_event.set)
                    except Exception as exc:
                        self._emit("error", f"shortcut send failed: {exc}")
                        return

                await client.start_notify(self.config.char_uuid, on_notify)
                self._emit("connection", "notifications enabled", connected=True, notify_enabled=True)
                await stop_event.wait()
                await client.stop_notify(self.config.char_uuid)
                if self.enter_active_scan and self.state == STATE_ACTIVE_SCANNING and self.active_voice_session is not None:
                    self.enter_active_scan = False
                    stop_event.clear()
                    self._emit("status", "GATT disconnected after START; scanning ADV STOP")
                    await self._scan_active_voice(stop_event)

    async def _scan_active_voice(self, stop_event: asyncio.Event) -> None:
        def on_adv(_device, adv):
            if self.active_voice_session is None:
                return
            try:
                event = self._parse_adv_event(adv)
                if event is None:
                    return
                self._handle_ptt_event(event)
            except Exception as exc:
                self._emit("error", f"ADV shortcut handling failed: {exc}")

        scanner = BleakScanner(detection_callback=on_adv)
        await scanner.start()
        try:
            while not self.closing and not stop_event.is_set() and self.active_voice_session is not None:
                if time.monotonic() - self.active_voice_last_seen >= max(2.0, self.config.active_heartbeat_timeout_s):
                    session_id = self.active_voice_session
                    self._emit("alt", f"ADV heartbeat timeout session={session_id}; releasing keys",
                               alt_down=bool(self.active_buttons))
                    self._stop_button(0, session_id, reason="ADV heartbeat timeout")
                    self.active_voice_session = None
                    self.state = STATE_IDLE_GATT
                    self.cooldown_until = time.monotonic() + POST_STOP_RECONNECT_DELAY_S
                    break
                await asyncio.sleep(0.1)
        finally:
            await scanner.stop()
        if self.active_voice_session is None:
            self.state = STATE_IDLE_GATT
            self.cooldown_until = time.monotonic() + POST_STOP_RECONNECT_DELAY_S

    async def _find_device(self):
        devices = await BleakScanner.discover(timeout=self.config.scan_timeout_s, return_adv=True)
        nearby = []
        for device, adv in devices.values():
            display_name = adv.local_name or device.name or ""
            if display_name == self.config.name:
                self._emit("scan", f"found {display_name} {device.address}", address=device.address)
                return device
            if "AirMic" in display_name or "ESP32" in display_name:
                nearby.append(f"{display_name} {device.address}")
        if nearby:
            self._emit("scan", "nearby: " + ", ".join(nearby))
        return None

    async def _sleep_or_stop(self, stop_event: asyncio.Event, seconds: float) -> None:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _on_disconnect(self, _client) -> None:
        self._emit("connection", "BLE disconnected",
                   connected=False, notify_enabled=False)

    def _parse_ptt_event(self, data: bytearray, source: str) -> PttEvent | None:
        if len(data) >= 4:
            button_id = int(data[0])
            event_type = int(data[1])
            session_id = int(data[2])
            seq = int(data[3])
            if event_type not in (PTT_EVENT_START, PTT_EVENT_STOP, PTT_EVENT_ACTIVE):
                return None
            return PttEvent(button_id=button_id, event_type=event_type, session_id=session_id, seq=seq, source=source)
        if len(data) == 3:
            button_id = int(data[0])
            event_type = int(data[1])
            session_id = int(data[2])
            if event_type not in (PTT_EVENT_START, PTT_EVENT_STOP):
                return None
            return PttEvent(button_id=button_id, event_type=event_type, session_id=session_id, source=source)
        if len(data) == 2:
            button_id = int(data[0])
            pressed = data[1] != 0
            return PttEvent(
                button_id=button_id,
                event_type=PTT_EVENT_START if pressed else PTT_EVENT_STOP,
                session_id=None,
                source=source,
            )
        if len(data) == 1:
            pressed = data[0] != 0
            return PttEvent(
                button_id=0,
                event_type=PTT_EVENT_START if pressed else PTT_EVENT_STOP,
                session_id=None,
                source=source,
            )
        return None

    def _parse_adv_event(self, adv) -> PttEvent | None:
        manufacturer_data = getattr(adv, "manufacturer_data", {}) or {}
        for _company_id, value in manufacturer_data.items():
            event = self._parse_adv_payload(bytes(value))
            if event is not None:
                return event
        service_data = getattr(adv, "service_data", {}) or {}
        for value in service_data.values():
            event = self._parse_adv_payload(bytes(value))
            if event is not None:
                return event
        return None

    def _parse_adv_payload(self, raw: bytes) -> PttEvent | None:
        if len(raw) >= 6 and raw[0:2] == PTT_ADV_MAGIC:
            return self._parse_ptt_event(bytearray(raw[2:6]), source="ADV")
        if len(raw) >= 8 and raw[0:2] == PTT_ADV_COMPANY_PREFIX and raw[2:4] == PTT_ADV_MAGIC:
            return self._parse_ptt_event(bytearray(raw[4:8]), source="ADV")
        return None

    def _is_duplicate(self, event: PttEvent) -> bool:
        key = (event.button_id, event.session_id, event.seq)
        if event.seq is None:
            return False
        if key in self.seen_events:
            return True
        self.seen_events.add(key)
        if len(self.seen_events) > 128:
            self.seen_events = set(list(self.seen_events)[-64:])
        return False

    def _handle_ptt_event(self, event: PttEvent) -> bool:
        self._emit("ptt",
                   f"{event.source} {event.action.upper()} button={event.button_id} event={event.event_type} session={event.session_id if event.session_id is not None else '-'} seq={event.seq if event.seq is not None else '-'}",
                   button_id=event.button_id,
                   pressed=event.is_start,
                   event_type=event.event_type,
                   session_id=event.session_id,
                   seq=event.seq,
                   source=event.source)

        if self._is_duplicate(event):
            self._emit("alt", f"duplicate ignored {event.source} button={event.button_id} session={event.session_id} seq={event.seq}",
                       alt_down=bool(self.active_buttons))
            return False

        if self.active_voice_session is not None:
            if event.button_id != 0:
                self._emit("alt", f"{event.source} button {event.button_id} ignored while voice session {self.active_voice_session} active",
                           alt_down=bool(self.active_buttons))
                return False
            if event.session_id != self.active_voice_session:
                self._emit("alt", f"{event.source} session mismatch got={event.session_id} active={self.active_voice_session}; ignored",
                           alt_down=bool(self.active_buttons))
                return False
            self.active_voice_last_seen = time.monotonic()
            if event.is_active:
                self._emit("alt", f"ADV ACTIVE session {event.session_id} seq {event.seq}",
                           alt_down=bool(self.active_buttons))
                return False
            if event.is_stop:
                self._stop_button(event.button_id, event.session_id, reason=f"{event.source} STOP")
                self.active_voice_session = None
                self.state = STATE_IDLE_GATT
                self.cooldown_until = time.monotonic() + POST_STOP_RECONNECT_DELAY_S
                return False
            return False

        if event.is_active:
            self._emit("alt", f"{event.source} ACTIVE without voice session ignored",
                       alt_down=bool(self.active_buttons))
            return False
        if event.is_start:
            self._start_button(event.button_id, event.session_id, event.seq, source=event.source)
            if event.button_id == 0:
                self.active_voice_session = event.session_id
                self.active_voice_last_seen = time.monotonic()
                self.state = STATE_ACTIVE_SCANNING
                return event.source == "GATT"
            return False
        if event.is_stop:
            if event.button_id == 0:
                self._emit("alt", f"{event.source} STOP without active voice session ignored session={event.session_id} seq={event.seq}",
                           alt_down=bool(self.active_buttons))
                return False
            self._stop_button(event.button_id, event.session_id, reason=f"{event.source} STOP")
        return False

    def _start_button(self, button_id: int, session_id: int | None, seq: int | None, source: str) -> None:
        keys = self.bindings.get(button_id, ["right_alt"])
        if not keys:
            self._emit("alt", f"{source} button {button_id} start session {session_id if session_id is not None else '-'} seq {seq if seq is not None else '-'}: disabled",
                       alt_down=bool(self.active_buttons))
            return

        if button_id in self.active_buttons:
            self._stop_button(button_id, session_id, reason="restart")

        self._release_other_buttons(button_id)
        send_combo(keys, True)
        self.active_buttons[button_id] = ActiveShortcut(keys=keys, session_id=session_id, seq=seq)

        time.sleep(0.02)
        observed = is_right_alt_down()
        label = " + ".join(KEY_SPECS[key]["label"] for key in keys)
        self._emit("alt", f"{source} START button {button_id} session {session_id if session_id is not None else '-'} seq {seq if seq is not None else '-'}: {label}",
                   alt_down=bool(self.active_buttons), right_alt_down=observed)

    def _stop_button(self, button_id: int, session_id: int | None, reason: str) -> None:
        active = self.active_buttons.pop(button_id, None)
        keys = active.keys if active is not None else self.bindings.get(button_id, ["right_alt"])
        if active is None:
            self._emit("alt", f"button {button_id} {reason} session {session_id if session_id is not None else '-'}: already up",
                       alt_down=bool(self.active_buttons))
            return
        if active.session_id is not None and session_id is not None and active.session_id != session_id:
            self.active_buttons[button_id] = active
            self._emit("alt", f"button {button_id} {reason} session mismatch active={active.session_id} got={session_id}; ignored",
                       alt_down=bool(self.active_buttons))
            return
        send_combo(keys, False)
        label = " + ".join(KEY_SPECS[key]["label"] for key in keys)
        self._emit("alt", f"button {button_id} {reason} session {session_id if session_id is not None else '-'} up: {label}",
                   alt_down=bool(self.active_buttons), right_alt_down=is_right_alt_down())

    def _release_other_buttons(self, keep_button_id: int) -> None:
        for button_id in list(self.active_buttons):
            if button_id != keep_button_id:
                self._stop_button(button_id, None, reason="preempt")

    def _release_all(self) -> None:
        for button_id, active in list(self.active_buttons.items()):
            send_combo(active.keys, False)
            self.active_buttons.pop(button_id, None)
        self.active_voice_session = None
        self.active_voice_last_seen = 0.0
        self.state = STATE_IDLE_GATT
        self.cooldown_until = time.monotonic() + POST_STOP_RECONNECT_DELAY_S


class MicMonitorWorker:
    def __init__(self, events: queue.Queue, serial_port_getter: Callable[[], str]):
        self.events = events
        self.serial_port_getter = serial_port_getter
        self.thread: threading.Thread | None = None
        self.stop_requested = threading.Event()
        self.stream: sd.InputStream | None = None
        self.device_index: int | None = None
        self.device_name = "-"
        self.last_level_log_ts = 0.0

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_requested.clear()
        self.thread = threading.Thread(target=self._thread_main, name="airmic-mic-monitor", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_requested.set()
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def rescan(self) -> None:
        self.stop()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.5)
        self.thread = None
        self.start()

    def _emit(self, kind: str, message: str = "", **fields) -> None:
        self.events.put({"kind": kind, "message": message, **fields})

    def _thread_main(self) -> None:
        while not self.stop_requested.is_set():
            try:
                device_index, device_name = self._pick_input_device()
                if device_index is None:
                    endpoint_name = self._find_windows_hfp_endpoint()
                    if endpoint_name:
                        serial_port = self.serial_port_getter().strip() or "未选择串口"
                        self._emit(
                            "mic",
                            f"Windows HFP input detected: {endpoint_name}; Python level monitor uses serial logs",
                            mic_device=f"{endpoint_name} + {serial_port} logs",
                            mic_state="hfp ok / serial level",
                        )
                    else:
                        serial_port = self.serial_port_getter().strip() or "未选择串口"
                        self._emit(
                            "mic",
                            "Windows HFP input not detected by PnP; using serial mic logs",
                            mic_device=serial_port,
                            mic_state="serial",
                        )
                    self._read_serial_mic_logs()
                    continue

                self.device_index = device_index
                self.device_name = device_name
                self._emit("mic", f"monitoring mic: {device_name}", mic_device=device_name, mic_state="opening")
                self._open_stream(device_index)
                self._emit("mic", "mic monitor active", mic_state="active")

                while not self.stop_requested.wait(0.2):
                    pass
            except Exception as exc:
                serial_port = self.serial_port_getter().strip() or "未选择串口"
                self._emit("mic", f"Windows mic monitor error: {exc}; using serial mic logs",
                           mic_device=serial_port, mic_state="serial")
                self._read_serial_mic_logs()
            finally:
                if self.stream is not None:
                    try:
                        self.stream.stop()
                        self.stream.close()
                    except Exception:
                        pass
                    self.stream = None
        self._emit("mic", "mic monitor stopped", mic_state="stopped")

    def _pick_input_device(self) -> tuple[int | None, str]:
        devices = sd.query_devices()
        inputs = []
        for index, info in enumerate(devices):
            if int(info.get("max_input_channels", 0)) <= 0:
                continue
            name = str(info.get("name", ""))
            hostapi = sd.query_hostapis(info["hostapi"])["name"]
            full_name = f"{name} [{hostapi}]"
            if "wdm-ks" in hostapi.lower() and any(key in name.lower() for key in MIC_DEVICE_KEYWORDS):
                continue
            inputs.append((index, full_name, name.lower()))

        for keyword in MIC_DEVICE_KEYWORDS:
            for index, full_name, lowered in sorted(inputs, key=self._device_sort_key):
                if keyword in lowered:
                    return index, full_name

        return None, "-"

    def _find_windows_hfp_endpoint(self) -> str | None:
        command = (
            "Get-PnpDevice -Class AudioEndpoint | "
            "Where-Object { $_.FriendlyName -match 'ESP32-AirMic-HFP|AirMic|ESP32' -and $_.InstanceId -match '\\{0\\.0\\.1\\.' } | "
            "Select-Object -First 1 -ExpandProperty FriendlyName"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            return None
        name = result.stdout.strip()
        return name or None

    def _device_sort_key(self, item) -> tuple[int, int]:
        _, full_name, lowered = item
        host_priority = 0
        if "wasapi" in full_name.lower():
            host_priority = 0
        elif "mme" in full_name.lower():
            host_priority = 1
        elif "directsound" in full_name.lower():
            host_priority = 2
        elif "wdm-ks" in full_name.lower():
            host_priority = 9
        name_priority = 0 if ("airmic" in lowered or "esp32" in lowered) else 1
        return (name_priority, host_priority)

    def _open_stream(self, device_index: int) -> None:
        device_info = sd.query_devices(device_index)
        default_rate = int(float(device_info.get("default_samplerate") or MIC_SAMPLE_RATE))
        samplerate = MIC_SAMPLE_RATE if default_rate >= MIC_SAMPLE_RATE else default_rate

        def callback(indata, frames, time_info, status):
            del frames, time_info
            if status:
                self._emit("mic", f"mic stream status: {status}")
            samples = np.asarray(indata, dtype=np.float32)
            if samples.size == 0:
                return
            peak = float(np.max(np.abs(samples)))
            rms = float(np.sqrt(np.mean(np.square(samples))))
            self._emit("mic_level", mic_peak=peak, mic_rms=rms)

        self.stream = sd.InputStream(
            device=device_index,
            channels=1,
            samplerate=samplerate,
            blocksize=MIC_BLOCKSIZE,
            dtype="float32",
            callback=callback,
        )
        self.stream.start()

    def _read_serial_mic_logs(self) -> None:
        serial_port = self.serial_port_getter().strip()
        if not serial_port:
            self._emit("mic", "serial mic log open failed: 未选择串口", mic_state="error")
            if self.stop_requested.wait(2.0):
                return
            return
        try:
            ser = serial.Serial()
            ser.port = serial_port
            ser.baudrate = SERIAL_BAUD
            ser.timeout = 0.25
            ser.dtr = False
            ser.rts = False
            ser.open()
        except Exception as exc:
            self._emit("mic", f"serial mic log open failed: {exc}", mic_state="error")
            if self.stop_requested.wait(2.0):
                return
            return

        self._emit("mic", f"monitoring ESP32 serial mic logs on {serial_port}",
                   mic_device=f"{serial_port} serial mic logs", mic_state="serial")
        try:
            while not self.stop_requested.is_set():
                line = ser.readline()
                if not line:
                    continue
                text = line.decode("utf-8", errors="replace")
                match = SERIAL_TX_MIC_RE.search(text) or SERIAL_MIC_RE.search(text)
                if match:
                    peak = int(match.group(1))
                    rms = int(match.group(2))
                    self._emit(
                        "mic_level",
                        mic_peak=min(1.0, peak / 32768.0),
                        mic_rms=min(1.0, rms / 32768.0),
                        mic_peak_raw=peak,
                        mic_rms_raw=rms,
                    )
                    now = time.time()
                    if now - self.last_level_log_ts >= 5.0 or peak >= 1200:
                        self._emit("mic", f"serial mic level peak={peak} rms={rms}", mic_state="hfp ok / serial level")
                        self.last_level_log_ts = now
                    continue

                match = SERIAL_HFP_CONN_RE.search(text)
                if match:
                    self._emit("hfp", f"HFP connection {match.group(1)}", hfp_slc=match.group(1))
                    continue

                match = SERIAL_HFP_AUDIO_RE.search(text)
                if match:
                    self._emit("hfp", f"HFP audio {match.group(1)}", hfp_audio=match.group(1))
                    continue
        finally:
            ser.close()


class ToneLabWorker:
    def __init__(self, events: queue.Queue, serial_port_getter: Callable[[], str]):
        self.events = events
        self.serial_port_getter = serial_port_getter
        self.thread: threading.Thread | None = None
        self.serial_thread: threading.Thread | None = None
        self.stop_requested = threading.Event()
        self.serial_lock = threading.Lock()
        self.serial_port: serial.Serial | None = None
        self.probe_proc: subprocess.Popen | None = None
        self.running = False
        self.restart_probe_requested = threading.Event()
        self.probe_watchdog = ToneProbeWatchdog()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            self._emit("tone", "tone monitor already running", tone_lab_running=True)
            return
        self.stop_requested.clear()
        self.thread = threading.Thread(target=self._thread_main, name="airmic-tone-lab", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_requested.set()
        self._close_serial()
        if self.probe_proc and self.probe_proc.poll() is None:
            try:
                self.probe_proc.terminate()
                self.probe_proc.wait(timeout=2)
            except Exception:
                try:
                    self.probe_proc.kill()
                except Exception:
                    pass

    def send(self, command: str) -> None:
        serial_port = self.serial_port_getter().strip()
        if not serial_port:
            self._emit("tone", "未选择串口，命令未发送")
            return
        with self.serial_lock:
            ser = self.serial_port
        if ser is None or not ser.is_open:
            self._emit("tone", f"opening tone serial on {serial_port} for command channel")
            if not self._open_serial():
                self._emit("tone", "tone serial unavailable; command not sent")
                return
        with self.serial_lock:
            ser = self.serial_port
            if ser is None or not ser.is_open:
                self._emit("tone", "tone serial unavailable after open attempt")
                return
            try:
                ser.write(command.encode("ascii") + b"\n")
                ser.flush()
                self._emit("tone", f"sent tone command: {command}")
            except Exception as exc:
                self._emit("tone", f"tone command send failed: {exc}")

    def send_line(self, line: str) -> None:
        self.send(line)

    def restart_probe(self, reason: str = "") -> None:
        if not self.running:
            return
        self.restart_probe_requested.set()
        suffix = f": {reason}" if reason else ""
        self._emit("tone", f"tone probe restart requested{suffix}")
        proc = self.probe_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _emit(self, kind: str, message: str = "", **fields) -> None:
        self.events.put({"kind": kind, "message": message, **fields})

    def _thread_main(self) -> None:
        self.running = True
        self._emit("tone", "tone monitor starting", tone_lab_running=True)

        try:
            if self._open_serial():
                self._emit("tone", "tone serial pre-opened; waiting for ESP32 to settle")
                time.sleep(1.2)
            self.serial_thread = threading.Thread(target=self._serial_reader, name="airmic-tone-serial", daemon=True)
            self.serial_thread.start()
            while not self.stop_requested.is_set():
                self.restart_probe_requested.clear()
                probe_exit_code = self._run_probe()
                if self.stop_requested.is_set():
                    break
                if self.restart_probe_requested.is_set():
                    time.sleep(0.4)
                    continue
                if should_restart_probe_after_exit(
                    stop_requested=self.stop_requested.is_set(),
                    restart_requested=self.restart_probe_requested.is_set(),
                    probe_exit_code=probe_exit_code,
                ):
                    self._emit("tone", f"tone probe exited code={probe_exit_code}; restarting")
                    time.sleep(0.8)
                    continue
                break
        finally:
            self.running = False
            self._close_serial()
            self._emit("tone", "tone monitor stopped", tone_lab_running=False)

    def _open_serial(self) -> bool:
        with self.serial_lock:
            existing = self.serial_port
            if existing is not None and existing.is_open:
                return True
        port_name = self.serial_port_getter().strip()
        if not port_name:
            self._emit("tone", "未选择串口，无法打开命令通道")
            return False
        try:
            ser = serial.Serial()
            ser.port = port_name
            ser.baudrate = SERIAL_BAUD
            ser.timeout = 0.25
            ser.dtr = False
            ser.rts = False
            ser.open()
        except Exception as exc:
            self._emit("tone", f"tone serial unavailable: {exc}; HFP probe continues")
            return False

        with self.serial_lock:
            self.serial_port = ser
        self._emit("tone", f"tone serial open on {port_name}; command channel ready")
        return True

    def _close_serial(self) -> None:
        with self.serial_lock:
            ser = self.serial_port
            self.serial_port = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    def _serial_reader(self) -> None:
        while not self.stop_requested.is_set():
            with self.serial_lock:
                ser = self.serial_port
            if ser is None or not ser.is_open:
                time.sleep(0.2)
                continue

            try:
                raw = ser.readline()
            except Exception as exc:
                self._emit("tone", f"tone serial read failed: {exc}")
                self._close_serial()
                time.sleep(0.5)
                continue
            if not raw:
                continue

            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue

            tone_match = SERIAL_TONE_RE.search(text)
            hfp_conn = SERIAL_HFP_CONN_RE.search(text)
            hfp_audio = SERIAL_HFP_AUDIO_RE.search(text)
            sr_result = SERIAL_SR_RESULT_RE.search(text)
            cfg_status = SERIAL_CFG_STATUS_RE.search(text)
            record_mode_status = SERIAL_RECORD_MODE_STATUS_RE.search(text)
            mic_match = SERIAL_TX_MIC_RE.search(text) or SERIAL_MIC_RE.search(text)

            if tone_match:
                self._emit("tone", f"ESP32 {tone_match.group(1)}")
            elif sr_result:
                self._emit(
                    "cfg_status",
                    f"ESP32 {text}",
                    sr_requested=sr_result.group(1),
                    sr_result=sr_result.group(2),
                )
            elif cfg_status:
                self._emit(
                    "cfg_status",
                    f"ESP32 {text}",
                    gain_q8=int(cfg_status.group(1)),
                    noise_gate=int(cfg_status.group(3)),
                    tone_q8=int(cfg_status.group(4)),
                    sample_shift=int(cfg_status.group(6)),
                    sr_enabled=(cfg_status.group(7) == "on"),
                    sr_initialized=(cfg_status.group(8) == "yes"),
                )
            elif record_mode_status:
                self._emit("cfg_status", f"ESP32 {text}", record_mode=record_mode_status.group(1))
            elif SERIAL_CFG_RE.search(text):
                self._emit("tone", f"ESP32 {text}")
            elif hfp_conn:
                self._emit("hfp", f"HFP connection {hfp_conn.group(1)}", hfp_slc=hfp_conn.group(1))
                self._emit("tone", f"ESP32 HFP connection {hfp_conn.group(1)}")
            elif hfp_audio:
                self._emit("hfp", f"HFP audio {hfp_audio.group(1)}", hfp_audio=hfp_audio.group(1))
                self._emit("tone", f"ESP32 HFP audio {hfp_audio.group(1)}")
            elif mic_match:
                peak = int(mic_match.group(1))
                rms = int(mic_match.group(2))
                if peak >= 1200:
                    self._emit("tone", f"ESP32 mic peak={peak} rms={rms}")
            elif "reset reason=" in text or "heap8=" in text:
                self._emit("tone", f"ESP32 {text}")
            elif "ESP32 AirMic HFP HF demo booting" in text:
                self._emit("tone", f"ESP32 {text}")

    def _run_probe(self) -> int | None:
        if not TONE_PROBE_EXE.exists():
            self._emit("tone", f"tone probe not found: {TONE_PROBE_EXE}")
            while not self.stop_requested.wait(0.2):
                pass
            return None

        args = [
            str(TONE_PROBE_EXE),
            "--all",
            "--name",
            TONE_PROBE_NAME,
            "--wait-active",
            "--seconds",
            "0",
            "--min-audio-timeout-ms",
            "4000",
        ]
        self.probe_watchdog.mark_probe_started()
        self._emit("tone", "starting HFP tone probe")
        try:
            self.probe_proc = subprocess.Popen(
                args,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as exc:
            self._emit("tone", f"tone probe start failed: {exc}")
            while not self.stop_requested.wait(0.2):
                pass
            return None

        proc = self.probe_proc
        assert proc is not None
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if self.stop_requested.is_set():
                    break
                text = line.strip()
                if not text:
                    continue
                if self.restart_probe_requested.is_set():
                    break

                tone_match = parse_probe_tone_line(text)
                rms_match = PROBE_RMS_RE.search(text)
                if tone_match:
                    self.probe_watchdog.mark_audio_line()
                    tone_source, tone_event = tone_match
                    self._emit("tone", f"PC {text}", tone_event=tone_event, tone_source=tone_source)
                elif rms_match:
                    self.probe_watchdog.mark_audio_line()
                    rms = float(rms_match.group(1))
                    peak = float(rms_match.group(2))
                    self._emit("tone_level", tone_rms=rms, tone_peak=peak)
                    if peak >= 0.02:
                        self._emit("tone", f"PC {text}")
                elif (
                    "Active endpoint" in text
                    or "Using device" in text
                    or "Capturing" in text
                    or "Waiting" in text
                    or "No audio callbacks" in text
                    or "[Unplugged]" in text
                    or "[NotPresent]" in text
                    or text.startswith("CANDIDATE ")
                ):
                    self._emit("tone", f"PC {text}")
                    if "Capturing" in text:
                        self.probe_watchdog.mark_capture_started()
                    if "No audio callbacks" in text:
                        self.restart_probe("probe reported no audio callbacks")
                        break
                if self.probe_watchdog.should_restart_for_no_audio():
                    self.restart_probe("no audio received after capture start")
                    break
        finally:
            if (self.stop_requested.is_set() or self.restart_probe_requested.is_set()) and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            exit_code = proc.poll()
            if exit_code is None:
                try:
                    exit_code = proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    exit_code = proc.poll()
            self.probe_proc = None
        return exit_code


class AirMicPttApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AirMic 语音按键控制台")
        self.geometry("1080x620")
        self.minsize(940, 560)

        self.events: queue.Queue = queue.Queue()
        self.worker = GuiBridgeWorker(BridgeConfig(), self.events)
        self.available_serial_ports = self._list_serial_ports()
        self.serial_port_var = tk.StringVar(value=choose_default_serial_port(self.available_serial_ports))
        self.tone_worker = ToneLabWorker(self.events, self._get_selected_serial_port)
        self.shortcut_controller = ToneShortcutController(trigger_style="tap")

        self.device_var = tk.StringVar(value=TONE_PROBE_NAME)
        self.address_var = tk.StringVar(value="-")
        self.scan_var = tk.StringVar(value="空闲")
        self.ble_var = tk.StringVar(value="未连接")
        self.notify_var = tk.StringVar(value="关闭")
        self.ptt_var = tk.StringVar(value="松开")
        self.alt_var = tk.StringVar(value="松开")
        self.hfp_slc_var = tk.StringVar(value="-")
        self.hfp_audio_var = tk.StringVar(value="-")
        self.tone_lab_var = tk.StringVar(value="已停止")
        self.tone_peak_var = tk.StringVar(value="0.000")
        self.tone_rms_var = tk.StringVar(value="0.000")
        self.tone_event_var = tk.StringVar(value="-")
        self.tone_shortcut_var = tk.StringVar(value="右 Alt")
        self.tone_trigger_style_var = tk.StringVar(value="点按切换")
        self.key_send_mode_var = tk.StringVar(value="扫描码")
        self.audio_gain_q8_var = tk.IntVar(value=1024)
        self.audio_gate_var = tk.IntVar(value=0)
        self.audio_tone_q8_var = tk.IntVar(value=256)
        self.audio_shift_var = tk.IntVar(value=11)
        self.record_mode_var = tk.StringVar(value=RECORD_MODE_LABEL_PTT)
        self.sr_mode_var = tk.StringVar(value=SR_MODE_LABEL_LEGACY)
        self.audio_gain_label_var = tk.StringVar(value="4.00x")
        self.audio_tone_label_var = tk.StringVar(value="1.00x")
        self.audio_gate_label_var = tk.StringVar(value="0")
        self.audio_shift_label_var = tk.StringVar(value="11")
        self.device_audio_status_var = tk.StringVar(value="设备状态：未知")
        self.shortcut_failsafe_ms_var = tk.IntVar(value=12000)
        self.shortcut_generation = 0
        self._audio_sync_guard = False
        self.binding_vars: dict[int, tk.StringVar] = {}
        self.heartbeat_timeout_var = tk.StringVar(value=str(int(ACTIVE_HEARTBEAT_TIMEOUT_S)))
        self.last_event_var = tk.StringVar(value="-")
        self.pending_sr_expectation: str | None = None

        for var in (
            self.audio_gain_q8_var,
            self.audio_gate_var,
            self.audio_tone_q8_var,
            self.audio_shift_var,
        ):
            var.trace_add("write", self._on_audio_var_changed)

        self._build_ui()
        self._update_audio_config_labels()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(UI_POLL_MS, self._drain_events)
        self.after(300, self._auto_start_tone_monitor)
        self._log("界面已启动：后台监听将自动启动")

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        header = ttk.Frame(self, padding=(16, 14, 16, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        title = ttk.Label(header, text="AirMic 语音按键控制台", font=("Segoe UI", 18, "bold"))
        title.grid(row=0, column=0, sticky="w")

        device = ttk.Label(header, textvariable=self.device_var, font=("Segoe UI", 10))
        device.grid(row=1, column=0, sticky="w", pady=(2, 0))

        buttons = ttk.Frame(header)
        buttons.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Label(buttons, text="串口").grid(row=0, column=0, padx=(0, 6))
        serial_combo = ttk.Combobox(buttons, textvariable=self.serial_port_var, values=self.available_serial_ports, width=8, state="readonly")
        serial_combo.grid(row=0, column=1, padx=(0, 6))
        ttk.Button(buttons, text="刷新串口", command=self._refresh_serial_ports).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(buttons, text="蓝牙设置", command=self._open_bluetooth_settings).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(buttons, text="录音设备", command=self._open_recording_panel).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(buttons, text="重连耳机音频", command=lambda: self._request_hfp_audio_reconnect("手动请求")).grid(row=0, column=5, padx=(0, 8))
        ttk.Button(buttons, text="移除 AirMic", command=self._forget_airmic).grid(row=0, column=6, padx=(0, 8))
        ttk.Button(buttons, text="释放按键", command=self._release_alt).grid(row=0, column=7)

        status_grid = ttk.Frame(self, padding=(16, 4, 16, 10))
        status_grid.grid(row=1, column=0, sticky="ew")
        for col in range(4):
            status_grid.columnconfigure(col, weight=1)

        self._status_card(status_grid, 0, "HFP 连接", self.hfp_slc_var)
        self._status_card(status_grid, 1, "HFP 音频", self.hfp_audio_var)
        self._status_card(status_grid, 2, "最近编码", self.tone_event_var)
        self._status_card(status_grid, 3, "快捷键", self.alt_var)

        tone_frame = ttk.LabelFrame(self, text="HFP 音频编码监听", padding=(16, 10))
        tone_frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 10))
        for col in range(8):
            tone_frame.columnconfigure(col, weight=1 if col in (1, 3, 5, 7) else 0)

        ttk.Label(tone_frame, text="监听").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(tone_frame, textvariable=self.tone_lab_var, font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w")
        ttk.Label(tone_frame, text="PC 峰值").grid(row=0, column=2, sticky="w", padx=(18, 8))
        ttk.Label(tone_frame, textvariable=self.tone_peak_var, font=("Segoe UI", 10, "bold")).grid(row=0, column=3, sticky="w")
        ttk.Label(tone_frame, text="PC RMS").grid(row=0, column=4, sticky="w", padx=(18, 8))
        ttk.Label(tone_frame, textvariable=self.tone_rms_var, font=("Segoe UI", 10, "bold")).grid(row=0, column=5, sticky="w")
        ttk.Label(tone_frame, text="最近编码").grid(row=0, column=6, sticky="w", padx=(18, 8))
        ttk.Label(tone_frame, textvariable=self.tone_event_var, font=("Segoe UI", 10, "bold")).grid(row=0, column=7, sticky="w")

        tone_buttons = ttk.Frame(tone_frame)
        tone_buttons.grid(row=1, column=0, columnspan=8, sticky="ew", pady=(10, 0))
        ttk.Button(tone_buttons, text="重启监听", command=self._start_tone_lab).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(tone_buttons, text="停止监听", command=self._stop_tone_lab).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(tone_buttons, text="发送 START", command=lambda: self._send_tone_command("s")).grid(row=0, column=2, padx=(10, 8))
        ttk.Button(tone_buttons, text="发送 STOP", command=lambda: self._send_tone_command("e")).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(tone_buttons, text="START + STOP", command=lambda: self._send_tone_command("b")).grid(row=0, column=4, padx=(0, 8))

        shortcut_frame = ttk.Frame(tone_frame)
        shortcut_frame.grid(row=2, column=0, columnspan=8, sticky="ew", pady=(10, 0))
        ttk.Label(shortcut_frame, text="触发快捷键").grid(row=0, column=0, sticky="w", padx=(0, 8))
        shortcut_combo = ttk.Combobox(
            shortcut_frame,
            textvariable=self.tone_shortcut_var,
            values=list(SHORTCUT_PRESETS.keys()),
            width=18,
            state="readonly",
        )
        shortcut_combo.grid(row=0, column=1, sticky="w")
        shortcut_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_tone_shortcut())
        ttk.Button(shortcut_frame, text="测试快捷键", command=self._test_shortcut).grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Label(shortcut_frame, text="触发方式").grid(row=0, column=3, sticky="w", padx=(18, 8))
        trigger_combo = ttk.Combobox(
            shortcut_frame,
            textvariable=self.tone_trigger_style_var,
            values=["点按切换", "按住"],
            width=10,
            state="readonly",
        )
        trigger_combo.grid(row=0, column=4, sticky="w")
        trigger_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_trigger_style())
        ttk.Label(shortcut_frame, text="发送模式").grid(row=0, column=5, sticky="w", padx=(18, 8))
        mode_combo = ttk.Combobox(
            shortcut_frame,
            textvariable=self.key_send_mode_var,
            values=["扫描码", "虚拟键"],
            width=10,
            state="readonly",
        )
        mode_combo.grid(row=0, column=6, sticky="w")
        mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_key_send_mode())
        ttk.Label(shortcut_frame, text="最长保持(ms)").grid(row=0, column=7, sticky="w", padx=(18, 8))
        ttk.Spinbox(
            shortcut_frame,
            from_=3000,
            to=60000,
            increment=1000,
            textvariable=self.shortcut_failsafe_ms_var,
            width=8,
        ).grid(row=0, column=8, sticky="w")

        audio_cfg = ttk.LabelFrame(tone_frame, text="ESP32 音频参数", padding=(10, 8))
        audio_cfg.grid(row=3, column=0, columnspan=8, sticky="ew", pady=(10, 0))
        audio_cfg.columnconfigure(1, weight=1)
        audio_cfg.columnconfigure(5, weight=1)

        ttk.Label(audio_cfg, text="麦克风增益").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Scale(audio_cfg, from_=64, to=4096, variable=self.audio_gain_q8_var,
                  command=lambda _v: self._update_audio_config_labels()).grid(row=0, column=1, sticky="ew")
        gain_spin = ttk.Spinbox(audio_cfg, from_=64, to=4096, increment=64, textvariable=self.audio_gain_q8_var, width=8)
        gain_spin.grid(row=0, column=2, sticky="w", padx=(8, 8))
        gain_spin.bind("<FocusOut>", lambda _e: self._update_audio_config_labels())
        gain_spin.bind("<Return>", lambda _e: self._update_audio_config_labels())
        ttk.Label(audio_cfg, textvariable=self.audio_gain_label_var, width=8).grid(row=0, column=3, sticky="w", padx=(0, 18))

        ttk.Label(audio_cfg, text="噪声门").grid(row=0, column=4, sticky="w", padx=(0, 8))
        ttk.Scale(audio_cfg, from_=0, to=6000, variable=self.audio_gate_var,
                  command=lambda _v: self._update_audio_config_labels()).grid(row=0, column=5, sticky="ew")
        gate_spin = ttk.Spinbox(audio_cfg, from_=0, to=6000, increment=50, textvariable=self.audio_gate_var, width=8)
        gate_spin.grid(row=0, column=6, sticky="w", padx=(8, 8))
        gate_spin.bind("<FocusOut>", lambda _e: self._update_audio_config_labels())
        gate_spin.bind("<Return>", lambda _e: self._update_audio_config_labels())
        ttk.Label(audio_cfg, textvariable=self.audio_gate_label_var, width=8).grid(row=0, column=7, sticky="w")

        ttk.Label(audio_cfg, text="编码音量").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Scale(audio_cfg, from_=64, to=768, variable=self.audio_tone_q8_var,
                  command=lambda _v: self._update_audio_config_labels()).grid(row=1, column=1, sticky="ew", pady=(8, 0))
        tone_spin = ttk.Spinbox(audio_cfg, from_=64, to=768, increment=16, textvariable=self.audio_tone_q8_var, width=8)
        tone_spin.grid(row=1, column=2, sticky="w", padx=(8, 8), pady=(8, 0))
        tone_spin.bind("<FocusOut>", lambda _e: self._update_audio_config_labels())
        tone_spin.bind("<Return>", lambda _e: self._update_audio_config_labels())
        ttk.Label(audio_cfg, textvariable=self.audio_tone_label_var, width=8).grid(row=1, column=3, sticky="w", padx=(0, 18), pady=(8, 0))

        ttk.Label(audio_cfg, text="采样右移").grid(row=1, column=4, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Scale(audio_cfg, from_=8, to=18, variable=self.audio_shift_var,
                  command=lambda _v: self._update_audio_config_labels()).grid(row=1, column=5, sticky="ew", pady=(8, 0))
        shift_spin = ttk.Spinbox(audio_cfg, from_=8, to=18, increment=1, textvariable=self.audio_shift_var, width=8)
        shift_spin.grid(row=1, column=6, sticky="w", padx=(8, 8), pady=(8, 0))
        shift_spin.bind("<FocusOut>", lambda _e: self._update_audio_config_labels())
        shift_spin.bind("<Return>", lambda _e: self._update_audio_config_labels())
        ttk.Label(audio_cfg, textvariable=self.audio_shift_label_var, width=8).grid(row=1, column=7, sticky="w", pady=(8, 0))

        ttk.Label(audio_cfg, text="支持拖动和直接输入；右移越小越大声，过小会削波").grid(row=2, column=0, columnspan=8, sticky="w", pady=(8, 0))

        ttk.Label(audio_cfg, text="录音门控").grid(row=3, column=0, sticky="w", pady=(8, 0))
        record_mode_combo = ttk.Combobox(
            audio_cfg,
            textvariable=self.record_mode_var,
            values=[RECORD_MODE_LABEL_PTT, RECORD_MODE_LABEL_ALWAYS],
            width=18,
            state="readonly",
        )
        record_mode_combo.grid(row=3, column=1, sticky="w", pady=(8, 0))
        record_mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_record_mode())
        ttk.Label(audio_cfg, text="调底噪时切到持续录音，正常使用切回按下才录音").grid(row=3, column=2, columnspan=6, sticky="w", pady=(8, 0))

        ttk.Label(audio_cfg, text="音频算法").grid(row=4, column=0, sticky="w", pady=(8, 0))
        sr_mode_combo = ttk.Combobox(
            audio_cfg,
            textvariable=self.sr_mode_var,
            values=[SR_MODE_LABEL_LEGACY, SR_MODE_LABEL_ESP_SR],
            width=18,
            state="readonly",
        )
        sr_mode_combo.grid(row=4, column=1, sticky="w", pady=(8, 0))
        sr_mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_sr_mode())
        ttk.Label(audio_cfg, text="默认先走旧链路；只在做降噪实验时再打开 ESP-SR").grid(row=4, column=2, columnspan=6, sticky="w", pady=(8, 0))

        audio_buttons = ttk.Frame(audio_cfg)
        audio_buttons.grid(row=5, column=0, columnspan=8, sticky="ew", pady=(8, 0))
        ttk.Button(audio_buttons, text="应用到设备", command=self._apply_audio_config_to_device).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(audio_buttons, text="读取当前值", command=self._show_audio_config).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(audio_buttons, text="恢复默认", command=self._reset_audio_config).grid(row=0, column=2)
        ttk.Label(audio_cfg, textvariable=self.device_audio_status_var).grid(row=6, column=0, columnspan=8, sticky="w", pady=(8, 0))

        log_frame = ttk.Frame(self, padding=(16, 0, 16, 16))
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.rowconfigure(1, weight=1)
        log_frame.columnconfigure(0, weight=1)

        last = ttk.Label(log_frame, textvariable=self.last_event_var)
        last.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.log_text = tk.Text(log_frame, height=18, wrap="word", state="disabled", font=("Consolas", 10))
        self.log_text.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def _status_card(self, parent: ttk.Frame, col: int, label: str, variable: tk.StringVar) -> None:
        frame = ttk.Frame(parent, padding=(10, 8))
        frame.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 6, 0))
        ttk.Label(frame, text=label, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=variable, font=("Segoe UI", 11, "bold")).grid(row=1, column=0, sticky="w")

    def _preset_name_for_keys(self, keys: list[str]) -> str:
        for name, preset_keys in SHORTCUT_PRESETS.items():
            if preset_keys == keys:
                return name
        return "右 Alt"

    def _update_binding(self, button_id: int) -> None:
        preset_name = self.binding_vars[button_id].get()
        self.worker.bindings[button_id] = list(SHORTCUT_PRESETS[preset_name])
        self._log(f"{BUTTON_LABELS[button_id]} mapped to {preset_name}")

    def _update_heartbeat_timeout(self) -> None:
        try:
            value = float(self.heartbeat_timeout_var.get())
        except ValueError:
            value = ACTIVE_HEARTBEAT_TIMEOUT_S
        value = min(30.0, max(2.0, value))
        self.heartbeat_timeout_var.set(str(int(value)))
        self.worker.config.active_heartbeat_timeout_s = value
        self._log(f"ADV heartbeat timeout set to {int(value)}s")

    def _reconnect(self) -> None:
        self._log("manual reconnect")
        self.worker.reconnect()

    def _release_alt(self) -> None:
        self.shortcut_generation += 1
        message = self.shortcut_controller.release_all()
        self.worker._release_all()
        self.alt_var.set("松开" if not self.shortcut_controller.is_down else "按下")
        self._log(f"手动释放：{message}")

    def _auto_start_tone_monitor(self) -> None:
        if self.tone_worker.running:
            return
        self._log("自动启动后台监听")
        self._start_tone_lab()

    def _start_tone_lab(self) -> None:
        selected = self._get_selected_serial_port() or "未选择"
        self._log(f"正在启动音频编码监听，当前命令串口：{selected}")
        self.tone_worker.start()

    def _stop_tone_lab(self) -> None:
        self._log("正在停止音频编码监听")
        self.tone_worker.stop()

    def _send_tone_command(self, command: str) -> None:
        labels = {"s": "START", "e": "STOP", "b": "START+STOP"}
        self._log(f"手动发送编码命令：{labels.get(command, command)}")
        self.tone_worker.send(command)

    def _update_tone_shortcut(self) -> None:
        self.shortcut_generation += 1
        preset_name = self.tone_shortcut_var.get()
        self.shortcut_controller.set_keys(SHORTCUT_PRESETS[preset_name])
        self.alt_var.set("松开")
        self._log(f"触发快捷键已切换为：{preset_name}")

    def _update_key_send_mode(self) -> None:
        self.shortcut_generation += 1
        label = self.key_send_mode_var.get()
        self.shortcut_controller.set_send_mode("vk" if label == "虚拟键" else "scan")
        self.alt_var.set("松开")
        self._log(f"快捷键发送模式已切换为：{label}")

    def _update_trigger_style(self) -> None:
        self.shortcut_generation += 1
        label = self.tone_trigger_style_var.get()
        self.shortcut_controller.set_trigger_style("tap" if label == "点按切换" else "hold")
        self.alt_var.set("松开")
        self._log(f"快捷键触发方式已切换为：{label}")

    def _test_shortcut(self) -> None:
        self._log("开始测试快捷键")
        down_message = self.shortcut_controller.press()
        self.alt_var.set("按下" if self.shortcut_controller.is_down else "松开")
        self._log(down_message)
        if self.shortcut_controller.trigger_style == "hold":
            self.after(600, self._release_test_shortcut)

    def _release_test_shortcut(self) -> None:
        up_message = self.shortcut_controller.release()
        self.shortcut_generation += 1
        self.alt_var.set("按下" if self.shortcut_controller.is_down else "松开")
        self._log(up_message)

    def _on_audio_var_changed(self, *_args) -> None:
        if self._audio_sync_guard:
            return
        self.after_idle(self._update_audio_config_labels)

    def _update_audio_config_labels(self) -> None:
        gain_q8, gate, tone_q8, shift = sanitize_audio_config_values(
            self.audio_gain_q8_var.get(),
            self.audio_gate_var.get(),
            self.audio_tone_q8_var.get(),
            self.audio_shift_var.get(),
        )
        self._audio_sync_guard = True
        try:
            self.audio_gain_q8_var.set(gain_q8)
            self.audio_gate_var.set(gate)
            self.audio_tone_q8_var.set(tone_q8)
            self.audio_shift_var.set(shift)
        finally:
            self._audio_sync_guard = False
        self.audio_gain_label_var.set(f"{gain_q8 / 256.0:.2f}x")
        self.audio_gate_label_var.set(str(gate))
        self.audio_tone_label_var.set(f"{tone_q8 / 256.0:.2f}x")
        self.audio_shift_label_var.set(str(shift))

    def _send_audio_config_line(self, line: str) -> None:
        self._log(f"发送音频参数命令：{line}")
        self.tone_worker.send_line(line)

    def _apply_audio_config(self) -> None:
        self._update_audio_config_labels()
        commands = build_audio_config_commands(
            self.audio_gain_q8_var.get(),
            self.audio_gate_var.get(),
            self.audio_tone_q8_var.get(),
            self.audio_shift_var.get(),
        )
        for command in commands:
            self._send_audio_config_line(command)

    def _apply_audio_config_to_device(self) -> None:
        self._update_audio_config_labels()
        self.pending_sr_expectation = "on" if self.sr_mode_var.get() == SR_MODE_LABEL_ESP_SR else "off"
        self.device_audio_status_var.set("设备状态：等待设备确认...")
        commands = [build_sr_mode_command(self.sr_mode_var.get())]
        commands.extend(build_audio_config_commands(
            self.audio_gain_q8_var.get(),
            self.audio_gate_var.get(),
            self.audio_tone_q8_var.get(),
            self.audio_shift_var.get(),
        ))
        commands.extend(["cfg show", "sr show"])
        self._ensure_tone_monitor_for_config(commands)

    def _show_audio_config(self) -> None:
        self.device_audio_status_var.set("设备状态：正在读取...")
        self._ensure_tone_monitor_for_config(["cfg show", "sr show"])

    def _reset_audio_config(self) -> None:
        self.audio_gain_q8_var.set(1024)
        self.audio_gate_var.set(0)
        self.audio_tone_q8_var.set(256)
        self.audio_shift_var.set(11)
        self.record_mode_var.set(RECORD_MODE_LABEL_PTT)
        self.sr_mode_var.set(SR_MODE_LABEL_LEGACY)
        self._update_audio_config_labels()
        self._ensure_tone_monitor_for_config([
            "cfg reset",
            build_sr_mode_command(self.sr_mode_var.get()),
            build_record_mode_command(self.record_mode_var.get()),
        ])

    def _apply_record_mode(self) -> None:
        label = self.record_mode_var.get()
        command = build_record_mode_command(label)
        self._log(f"录音门控模式切换为：{label}")
        self._ensure_tone_monitor_for_config([command])

    def _apply_sr_mode(self) -> None:
        label = self.sr_mode_var.get()
        self._log(f"已选择音频算法：{label}，点“应用到设备”后生效")

    def _ensure_tone_monitor_for_config(self, commands: list[str]) -> None:
        serial_port = self._get_selected_serial_port()
        if not serial_port:
            self._log("配置未发送：请先选择串口")
            return
        if not self.tone_worker.running:
            self._log("后台监听未运行，已自动启动后再发送配置")
            self.tone_worker.start()
            self.after(150, lambda: self._send_audio_config_commands(commands))
            return
        self._send_audio_config_commands(commands)

    def _send_audio_config_commands(self, commands: list[str]) -> None:
        for index, command in enumerate(commands):
            self.after(index * 220, lambda cmd=command: self._send_audio_config_line(cmd))

    def _handle_tone_shortcut(self, tone_event: str, tone_source: str = "TONE") -> None:
        try:
            message = self.shortcut_controller.handle_tone(tone_event)
        except Exception as exc:
            self.alt_var.set("error")
            self._log(f"快捷键发送失败：{exc}")
            return
        if tone_source == "VAD":
            message = message.replace("收到 STOP", "收到 VAD STOP").replace("收到 START", "收到 VAD START")
        self.alt_var.set("按下" if self.shortcut_controller.is_down else "松开")
        self._log(message)
        normalized = tone_event.strip().upper()
        if (
            normalized == "START"
            and self.shortcut_controller.is_down
            and self.shortcut_controller.trigger_style == "hold"
        ):
            self._schedule_shortcut_failsafe()
        elif normalized == "STOP":
            self.shortcut_generation += 1

    def _schedule_shortcut_failsafe(self) -> None:
        self.shortcut_generation += 1
        generation = self.shortcut_generation
        try:
            timeout_ms = int(self.shortcut_failsafe_ms_var.get())
        except (TypeError, ValueError):
            timeout_ms = 12000
        timeout_ms = min(60000, max(3000, timeout_ms))
        self.shortcut_failsafe_ms_var.set(timeout_ms)
        self.after(timeout_ms, lambda: self._failsafe_release_shortcut(generation))

    def _failsafe_release_shortcut(self, generation: int) -> None:
        if generation != self.shortcut_generation or not self.shortcut_controller.is_down:
            return
        message = self.shortcut_controller.release_all()
        self.shortcut_generation += 1
        self.alt_var.set("松开")
        self._log(f"超过最长保持时间，自动释放快捷键：{message}")

    def _open_bluetooth_settings(self) -> None:
        self._log("正在打开 Windows 蓝牙设置")
        subprocess.Popen(["cmd", "/c", "start", "ms-settings:bluetooth"], shell=False)

    def _open_recording_panel(self) -> None:
        self._log("正在打开 Windows 录音设备面板")
        subprocess.Popen(["control", "mmsys.cpl,,1"], shell=False)

    def _request_hfp_audio_reconnect(self, reason: str, auto: bool = False) -> None:
        slc_state = self.hfp_slc_var.get()
        if slc_state in ("-", "disconnected"):
            self._log(f"{reason}：当前 HFP 控制链路未连接，暂时无法重连耳机音频")
            return
        if self.tone_worker.running and self.tone_worker.serial_port is not None:
            prefix = "自动" if auto else "手动"
            self._log(f"{prefix}请求重连耳机音频")
            self.tone_worker.send_line("audio connect")
            return
        self._log(f"{reason}：串口控制未就绪，尝试按需打开串口重连耳机音频")
        self.tone_worker.send_line("audio connect")

    def _list_serial_ports(self) -> list[str]:
        ports = sorted(port.device for port in list_ports.comports())
        return ports

    def _get_selected_serial_port(self) -> str:
        return self.serial_port_var.get().strip()

    def _refresh_serial_ports(self) -> None:
        current = self._get_selected_serial_port()
        ports = self._list_serial_ports()
        self.available_serial_ports = ports
        selected = current if current in ports else choose_default_serial_port(ports)
        self.serial_port_var.set(selected)
        self._log(f"串口列表已刷新：{', '.join(ports) if ports else '未找到串口'}")

    def _forget_airmic(self) -> None:
        if not messagebox.askyesno("移除 AirMic", "要从 Windows 尝试移除 AirMic 蓝牙/音频设备吗？"):
            return
        self._log("正在尝试移除 AirMic 配对设备")
        self.worker.stop()
        command = (
            "$devices = Get-PnpDevice | Where-Object { $_.FriendlyName -match 'ESP32-AirMic|AirMic' }; "
            "foreach ($d in $devices) { "
            "  try { Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction SilentlyContinue | Out-Null } catch {}; "
            "  try { Remove-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction SilentlyContinue | Out-Null } catch {}; "
            "}; "
            "$devices | Select-Object Status,Class,FriendlyName"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=12,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.stdout.strip():
                self._log("移除命令输出：" + " | ".join(result.stdout.splitlines()))
            if result.stderr.strip():
                self._log("移除命令警告：" + " | ".join(result.stderr.splitlines()))
        except Exception as exc:
            self._log(f"移除命令失败：{exc}")
        self._open_bluetooth_settings()
        self._log("如果 AirMic 仍然存在，请在 Windows 蓝牙设置里手动移除")

    def _on_close(self) -> None:
        self.worker.stop()
        self.tone_worker.stop()
        self.after(150, self.destroy)

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
        self.after(UI_POLL_MS, self._drain_events)

    def _handle_event(self, event: dict) -> None:
        kind = event.get("kind", "")
        message = event.get("message", "")

        if "address" in event:
            self.address_var.set(event["address"])
        if kind == "scan" and "scanning" in event:
            self.scan_var.set("扫描中" if event["scanning"] else "空闲")
        if kind == "connection":
            self.ble_var.set("已连接" if event.get("connected") else "未连接")
            self.notify_var.set("开启" if event.get("notify_enabled") else "关闭")
        if kind == "ptt":
            self.ptt_var.set("按下" if event.get("pressed") else "松开")
        if kind == "alt":
            self.alt_var.set("按下" if event.get("alt_down") else "松开")
        if kind == "hfp":
            if "hfp_slc" in event:
                self.hfp_slc_var.set(event["hfp_slc"])
            if "hfp_audio" in event:
                self.hfp_audio_var.set(event["hfp_audio"])
                if event["hfp_audio"] == "connected_cvsd":
                    self.tone_worker.restart_probe("HFP audio connected_cvsd")
                elif event["hfp_audio"] == "disconnected":
                    message = self.shortcut_controller.release_all()
                    self.shortcut_generation += 1
                    self.alt_var.set("松开")
                    self._log(f"HFP 音频断开，自动释放快捷键：{message}")
                    self._log("HFP 音频断开：本轮先不自动发 audio connect，避免把复位/断链原因搅乱")
        if kind == "tone":
            if "tone_lab_running" in event:
                self.tone_lab_var.set("运行中" if event["tone_lab_running"] else "已停止")
            if "tone_event" in event:
                tone_source = event.get("tone_source", "TONE")
                tone_label = event["tone_event"] if tone_source == "TONE" else f"{tone_source} {event['tone_event']}"
                self.tone_event_var.set(tone_label)
                self._handle_tone_shortcut(event["tone_event"], tone_source=tone_source)
        if kind == "tone_level":
            peak = float(event.get("tone_peak", 0.0))
            rms = float(event.get("tone_rms", 0.0))
            self.tone_peak_var.set(f"{peak:.4f}")
            self.tone_rms_var.set(f"{rms:.4f}")
        if kind == "cfg_status":
            self._handle_cfg_status_event(event)
        if kind == "error":
            self.ble_var.set("错误")

        if message:
            self._log(message)

    def _handle_cfg_status_event(self, event: dict) -> None:
        if "record_mode" in event:
            record_mode = event["record_mode"]
            self.record_mode_var.set(RECORD_MODE_LABEL_ALWAYS if record_mode == "always" else RECORD_MODE_LABEL_PTT)

        if "sr_requested" in event:
            requested = event["sr_requested"]
            result = event.get("sr_result", "")
            if result != "ESP_OK":
                self.device_audio_status_var.set(f"设备状态：{requested.upper()} 失败（{result}）")
                self.pending_sr_expectation = None
            return

        if "gain_q8" in event:
            self._audio_sync_guard = True
            try:
                self.audio_gain_q8_var.set(int(event["gain_q8"]))
                self.audio_gate_var.set(int(event["noise_gate"]))
                self.audio_tone_q8_var.set(int(event["tone_q8"]))
                self.audio_shift_var.set(int(event["sample_shift"]))
            finally:
                self._audio_sync_guard = False
            self._update_audio_config_labels()

        if "sr_enabled" in event:
            sr_enabled = bool(event["sr_enabled"])
            sr_initialized = bool(event.get("sr_initialized", False))
            self.sr_mode_var.set(SR_MODE_LABEL_ESP_SR if sr_enabled else SR_MODE_LABEL_LEGACY)

            if sr_enabled and sr_initialized:
                self.device_audio_status_var.set("设备状态：ESP-SR 已启用")
            elif sr_enabled and not sr_initialized:
                self.device_audio_status_var.set("设备状态：ESP-SR 请求中，但初始化未完成")
            else:
                self.device_audio_status_var.set("设备状态：旧链路（稳定）")

            if self.pending_sr_expectation == "on":
                if sr_enabled and sr_initialized:
                    self._log("设备确认：ESP-SR 已成功启用")
                else:
                    self._log("设备确认：ESP-SR 未启用成功，当前仍是旧链路")
                self.pending_sr_expectation = None
            elif self.pending_sr_expectation == "off":
                if not sr_enabled:
                    self._log("设备确认：旧链路已生效")
                else:
                    self._log("设备确认：设备仍未退出 ESP-SR")
                self.pending_sr_expectation = None

    def _log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        self.last_event_var.set(line)
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def main() -> int:
    if sys.platform != "win32":
        print("This GUI bridge currently supports Windows only.", file=sys.stderr)
        return 2
    app = AirMicPttApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

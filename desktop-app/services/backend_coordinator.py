from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot

from core.models.app_state import (
    AudioDeviceStatusSnapshot,
    AudioTuningSnapshot,
    DeviceCommandResult,
    DeviceOption,
    SerialPortStatusSnapshot,
    ServiceStatusSnapshot,
    TONE_SLOT_START,
    TONE_SLOT_A,
    TONE_SLOT_B,
    TONE_SLOT_C,
)
from core.shortcut.presets import (
    ACTION_PRESETS_BY_ID,
    DEFAULT_VOICE_MODEL_ID,
    KEY_OPTIONS,
    VOICE_MODEL_PRESETS_BY_ID,
    format_key_chord,
    get_action_preset_by_id,
    get_voice_model_preset_by_id,
)
from app.runtime_paths import app_root, tool_path
from services.probe_service import ProbeEvent
from services.shortcut_service import ShortcutService
from services.probe_service import ProbeService
from services.audio_status_watch_service import AudioStatusWatchEvent, AudioStatusWatchService
from services.settings_service import SettingsService
from services.startup_service import StartupService
from services.diagnostics_log_service import DiagnosticsLogService

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - import guard for non-Windows test envs
    sd = None

try:
    import serial
except Exception:  # pragma: no cover - import guard for non-Windows test envs
    serial = None

try:
    from serial.tools import list_ports
except Exception:  # pragma: no cover - import guard for non-Windows test envs
    list_ports = None


SERIAL_CFG_STATUS_RE = re.compile(
    r"cfg gain_q8=(\d+)\s+gain=([0-9.]+)x\s+gate=(\d+)\s+tone_q8=(\d+)\s+tone=([0-9.]+)x\s+shift=(\d+)(?:\s+sr=(on|off)\s+sr_init=(yes|no))?"
)
SERIAL_RECORD_MODE_STATUS_RE = re.compile(r"record mode=(always|ptt)")
PROBE_LIST_LINE_RE = re.compile(r"^\d+:\s+(.+?)\s+\[(Active|Disabled|NotPresent|Unplugged)\]$")
PROBE_DEFAULT_INPUT_RE = re.compile(
    r"^DEFAULT_INPUT\s+\((Communications|Multimedia)\):\s+(.+?)(?:\s+\[(Active|Disabled|NotPresent|Unplugged)\])?$"
)
PROBE_AIRMIC_STATE_LINE_RE = re.compile(r"^\d{2}:\d{2}:\d{2}:\s+(.+?)\s+\[(Active|Disabled|NotPresent|Unplugged)\]$")


def _is_airmic_input_name(name: str) -> bool:
    lowered = name.strip().lower()
    return "esp32-airmic-hfp" in lowered or ("airmic" in lowered and "hands-free" in lowered)


@dataclass(frozen=True)
class LogEntry:
    timestamp: str
    level: str
    message: str


@dataclass
class BackendStatus:
    state: str = "stopped"
    summary: str = "后台监听尚未启动"
    detail: str = "点击“重启监听”后，会自动拉起音频编码监听。"
    listener_phase: str = "stopped"
    current_voice_model_id: str = DEFAULT_VOICE_MODEL_ID
    tone_action_map: dict[str, str] = field(
        default_factory=lambda: {
            TONE_SLOT_START: "voice_model_default",
            TONE_SLOT_A: "disabled",
            TONE_SLOT_B: "disabled",
            TONE_SLOT_C: "disabled",
        }
    )
    custom_tone_action_map: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            TONE_SLOT_START: (),
            TONE_SLOT_A: (),
            TONE_SLOT_B: (),
            TONE_SLOT_C: (),
        }
    )
    command_port: str = "COM10"
    detected_airmic_input: str = "耳机 (ESP32-AirMic-HFP Hands-Free)"
    detected_airmic_input_active: bool = False
    detected_airmic_device_present: bool = False
    has_any_available_input: bool = False
    current_input_name: str = ""
    selected_input_device_id: str = "airmic_hfp"
    selected_output_device_id: str = "system_default_output"
    last_tone: str = "暂无"
    tuning: AudioTuningSnapshot = field(default_factory=AudioTuningSnapshot)
    serial_port_state: str = "unknown"
    serial_port_detail: str = ""
    device_record_mode: str = "ptt"
    startup_enabled: bool = False


class BackendCoordinator(QObject):
    statusChanged = Signal(object)
    runtimeSnapshotReady = Signal(object)
    audioStatusWatchEventReady = Signal(object)
    probeEventReady = Signal(object)
    probeLogReady = Signal(str)

    def __init__(
        self,
        shortcut_sender: Callable[[list[str], bool, str], None] | None = None,
        project_root: Path | None = None,
        serial_port_provider: Callable[[], list[str]] | None = None,
        audio_status_provider: Callable[[], AudioDeviceStatusSnapshot] | None = None,
        serial_status_provider: Callable[[str, list[str]], SerialPortStatusSnapshot] | None = None,
        device_command_sender: Callable[[str, list[str]], DeviceCommandResult] | None = None,
        settings_service: SettingsService | None = None,
        startup_service: StartupService | None = None,
        diagnostics_log_service: DiagnosticsLogService | None = None,
    ) -> None:
        super().__init__()
        self.status = BackendStatus()
        self._settings_service = settings_service or SettingsService()
        self._startup_service = startup_service or StartupService()
        self._diagnostics_log_service = diagnostics_log_service or DiagnosticsLogService()
        self._restore_persisted_settings()
        self.status.startup_enabled = self._startup_service.is_enabled()
        self.logs: list[LogEntry] = []
        self._listeners: list[Callable[[BackendStatus], None]] = []
        self.shortcut_service = ShortcutService(sender=shortcut_sender)
        resolved_root = project_root or app_root()
        self.probe_service = ProbeService(project_root=resolved_root)
        self.audio_status_watch_service = AudioStatusWatchService(project_root=resolved_root)
        self._serial_port_provider = serial_port_provider or self._list_serial_ports
        self._audio_status_provider = audio_status_provider or self._read_audio_device_status
        self._serial_status_provider = serial_status_provider or self._probe_serial_port_status
        self._device_command_sender = device_command_sender or self._send_device_commands
        self.input_devices = [
            DeviceOption("airmic_hfp", "AirMic HFP 麦克风", "当前推荐输入设备"),
            DeviceOption("system_default_input", "系统默认麦克风", "跟随 Windows 默认输入"),
        ]
        self.output_devices = [
            DeviceOption("system_default_output", "系统默认扬声器", "跟随 Windows 默认输出"),
            DeviceOption("airmic_hfp_speaker", "AirMic HFP 耳机", "用于检查回放或语音通路"),
        ]
        self.command_ports: list[DeviceOption] = []
        self._debug_serial_enabled = False
        self._runtime_refresh_inflight = False
        self._last_probe_event_at = time.monotonic()
        self._last_shortcut_keepalive_at = time.monotonic()
        self._last_probe_recovery_at = 0.0
        self._stuck_shortcut_timeout_s = 1.4
        self._shortcut_recording_slot: str | None = None
        self.runtimeSnapshotReady.connect(self._handle_runtime_snapshot_ready, Qt.QueuedConnection)
        self.audioStatusWatchEventReady.connect(self._handle_audio_status_watch_event_ready, Qt.QueuedConnection)
        self.probeEventReady.connect(self._handle_probe_event_ready, Qt.QueuedConnection)
        self.probeLogReady.connect(self._handle_probe_log_ready, Qt.QueuedConnection)
        self.refresh_runtime_state()

    def subscribe(self, callback: Callable[[BackendStatus], None]) -> None:
        self._listeners.append(callback)
        owner = getattr(callback, "__self__", None)
        if isinstance(owner, QObject):
            self.statusChanged.connect(callback, Qt.QueuedConnection)
        callback(self.status)

    def service_snapshot(self) -> ServiceStatusSnapshot:
        return ServiceStatusSnapshot(
            state=self.status.state,
            summary=self.status.summary,
            detail=self.status.detail,
        )

    def current_voice_model_label(self) -> str:
        return get_voice_model_preset_by_id(self.status.current_voice_model_id).display_name

    def current_start_action_label(self) -> str:
        voice_model = get_voice_model_preset_by_id(self.status.current_voice_model_id)
        return get_action_preset_by_id(voice_model.action_preset_id).chord_label

    def current_start_action_display_text(self) -> str:
        return self._format_binding_for_console(self._current_start_keys())

    def tone_action_display_text(self, slot_id: str) -> str:
        keys = self._keys_for_slot(slot_id)
        if not keys:
            return "点击录制"
        return self._format_binding_for_console(keys)

    def listener_status_text(self) -> str:
        return {
            "stopped": "未启动",
            "starting": "启动中",
            "waiting_hfp": "等待HFP",
            "monitoring": "监听中",
            "error": "异常",
            "running": "正常",
        }.get(self.status.listener_phase, "异常")

    def device_status_text(self) -> str:
        if self.has_active_device_status():
            return "在线"
        return "离线"

    def system_input_display_text(self) -> str:
        text = self.status.current_input_name.strip()
        if not text:
            if self.status.has_any_available_input:
                return "未选择"
            return "无麦克风"
        if _is_airmic_input_name(text):
            return "AirMic HFP麦克风"
        return text

    def serial_status_text(self) -> str:
        return {
            "open": "在线",
            "busy": "占用",
            "missing": "未连接",
            "unknown": "未知",
        }.get(self.status.serial_port_state, "未知")

    def mic_gain_display_text(self) -> str:
        return f"{self.status.tuning.mic_gain_q8 / 256.0:.2f}X"

    def tone_gain_display_text(self) -> str:
        multiplier = self.status.tuning.tone_gain_q8 / 256.0
        if float(multiplier).is_integer():
            return f"{int(multiplier)}X"
        return f"{multiplier:.2f}X"

    def sample_shift_display_text(self) -> str:
        return str(self.status.tuning.sample_shift_bits)

    def noise_gate_display_text(self) -> str:
        return str(self.status.tuning.noise_gate)

    def log_text(self) -> str:
        return "\n".join(f"[{item.timestamp}] {item.level} {item.message}" for item in self.logs)

    def start(self) -> None:
        self.refresh_runtime_state()
        self.start_audio_status_watch()
        self.status.state = "running"
        self.status.listener_phase = "starting"
        self.status.summary = "后台监听服务运行中"
        self.status.detail = "等待 HFP 音频回调与编码事件；当前为桌面应用骨架状态。"
        self._append_log("INFO", "后台监听服务已启动。")
        self._append_diagnostic("service", f"started state={self.status.state} listener={self.status.listener_phase}")
        self._notify()

    def stop(self) -> None:
        self.stop_probe_monitor()
        self.stop_audio_status_watch()
        self.status.state = "stopped"
        self.status.listener_phase = "stopped"
        self.status.summary = "后台监听已停止"
        self.status.detail = "快捷键和串口命令通道已进入空闲状态。"
        self._append_log("INFO", "后台监听服务已停止。")
        self._append_diagnostic("service", f"stopped state={self.status.state} listener={self.status.listener_phase}")
        self._notify()

    def restart(self) -> None:
        self.stop_probe_monitor()
        self.stop_audio_status_watch()
        self.refresh_runtime_state()
        self.start_audio_status_watch()
        self.status.state = "running"
        self.status.listener_phase = "starting"
        self.status.summary = "后台监听服务运行中"
        self.status.detail = "监听进程已重启，等待设备与音频链路就绪。"
        self._append_log("INFO", "后台监听服务已重启。")
        self._append_diagnostic("service", f"restarted state={self.status.state} listener={self.status.listener_phase}")
        self._notify()
        self.start_probe_monitor()

    def set_voice_model(self, preset_id: str) -> None:
        self.status.current_voice_model_id = preset_id
        self._persist_shortcut_settings()
        preset = get_voice_model_preset_by_id(preset_id)
        action = get_action_preset_by_id(preset.action_preset_id)
        self._append_log("INFO", f"主语音模型切换为：{preset.display_name}（{action.chord_label}）。")
        self._notify()

    def set_tone_action(self, slot_id: str, action_preset_id: str) -> None:
        self.status.tone_action_map[slot_id] = action_preset_id
        self.status.custom_tone_action_map[slot_id] = ()
        self._persist_shortcut_settings()
        action = get_action_preset_by_id(action_preset_id)
        self._append_log("INFO", f"{slot_id} 映射已更新为：{action.display_name}。")
        self._notify()

    def set_custom_tone_action(self, slot_id: str, keys: tuple[str, ...]) -> None:
        self.status.tone_action_map[slot_id] = "custom"
        self.status.custom_tone_action_map[slot_id] = tuple(keys)
        self._persist_shortcut_settings()
        self._append_log("INFO", f"{slot_id} 自定义映射已更新为：{format_key_chord(keys)}。")
        self._notify()

    def set_input_device(self, device_id: str) -> None:
        self.status.selected_input_device_id = device_id
        device = next(item for item in self.input_devices if item.device_id == device_id)
        self._append_log("INFO", f"输入设备切换为：{device.display_name}。")
        self._notify()

    def set_output_device(self, device_id: str) -> None:
        self.status.selected_output_device_id = device_id
        device = next(item for item in self.output_devices if item.device_id == device_id)
        self._append_log("INFO", f"输出设备切换为：{device.display_name}。")
        self._notify()

    def set_command_port(self, port_id: str) -> None:
        self.status.command_port = port_id
        self.refresh_runtime_state()
        self._append_log("INFO", f"命令串口已切换为：{port_id}。")
        self._notify()

    def set_startup_enabled(self, enabled: bool) -> None:
        applied = self._startup_service.set_enabled(enabled)
        self.status.startup_enabled = enabled if applied else self._startup_service.is_enabled()
        if applied:
            self._append_log("INFO", f"开机启动已{'开启' if enabled else '关闭'}。")
            self._append_diagnostic("startup", f"enabled={self.status.startup_enabled}")
        else:
            self._append_log("WARN", f"开机启动切换失败，当前状态：{'开启' if self.status.startup_enabled else '关闭'}。")
            self._append_diagnostic("startup", f"apply_failed enabled={self.status.startup_enabled}")
        self._notify()

    def refresh_runtime_state(self) -> None:
        serial_state, serial_detail, command_ports, audio_snapshot = self._collect_runtime_state_snapshot()
        self._apply_runtime_state_snapshot(serial_state, serial_detail, command_ports, audio_snapshot)

    def manual_check_device_status(self) -> None:
        self.refresh_runtime_state()
        self._append_log("INFO", f"已手动检查设备状态：{self.device_status_text()}。")
        self._notify()

    def _collect_runtime_state_snapshot(
        self,
    ) -> tuple[str, str, list[DeviceOption], AudioDeviceStatusSnapshot]:
        if self._debug_serial_enabled:
            port_names = self._serial_port_provider()
            command_ports = [DeviceOption(port_name, port_name) for port_name in port_names]
            if not self.status.command_port:
                self.status.command_port = self._choose_default_serial_port(port_names)
            serial_snapshot = self._serial_status_provider(self.status.command_port, port_names)
            serial_state = serial_snapshot.state
            serial_detail = serial_snapshot.detail
        else:
            command_ports = []
            serial_state = "missing"
            serial_detail = "调试台未打开"

        audio_snapshot = self._audio_status_provider()
        return serial_state, serial_detail, command_ports, audio_snapshot

    def _apply_runtime_state_snapshot(
        self,
        serial_state: str,
        serial_detail: str,
        command_ports: list[DeviceOption],
        audio_snapshot: AudioDeviceStatusSnapshot,
    ) -> None:
        self.command_ports = command_ports
        self.status.serial_port_state = serial_state
        self.status.serial_port_detail = serial_detail
        self.status.detected_airmic_input = audio_snapshot.detected_airmic_input_name.strip()
        self.status.detected_airmic_input_active = audio_snapshot.detected_airmic_input_active
        self.status.detected_airmic_device_present = audio_snapshot.detected_airmic_device_present
        self.status.has_any_available_input = audio_snapshot.has_any_available_input
        self.status.current_input_name = audio_snapshot.current_input_name.strip()
        if audio_snapshot.current_input_name.strip():
            self.status.selected_input_device_id = "system_default_input"
        else:
            self.status.selected_input_device_id = ""

    def tick_runtime_state(self) -> None:
        self._maybe_force_release_stuck_shortcut()
        self._maybe_recover_probe_monitor()
        if self._runtime_refresh_inflight:
            return
        self._runtime_refresh_inflight = True

        def worker() -> None:
            try:
                snapshot = self._collect_runtime_state_snapshot()
            except Exception as exc:
                self.runtimeSnapshotReady.emit(("error", str(exc)))
                return
            self.runtimeSnapshotReady.emit(("ok", snapshot))

        threading.Thread(target=worker, name="airmic-runtime-refresh", daemon=True).start()

    def _handle_runtime_snapshot_ready(self, payload: object) -> None:
        self._runtime_refresh_inflight = False
        if not isinstance(payload, tuple) or not payload:
            return
        kind = payload[0]
        if kind == "error":
            self._append_log("WARN", f"运行态刷新失败：{payload[1]}")
            self._notify()
            return
        if kind == "ok":
            serial_state, serial_detail, command_ports, audio_snapshot = payload[1]
            self._apply_runtime_state_snapshot(serial_state, serial_detail, command_ports, audio_snapshot)
            self._notify()

    def has_active_device_status(self) -> bool:
        return bool(
            self.status.detected_airmic_device_present
            or self.status.detected_airmic_input_active
        )

    def has_active_system_input(self) -> bool:
        return _is_airmic_input_name(self.status.current_input_name)

    def refresh_device_config(self) -> None:
        self.refresh_runtime_state()
        if not self._debug_serial_enabled:
            self._append_log("INFO", "调试台未打开，暂不访问设备串口。")
            self._notify()
            return
        if not self.status.command_port:
            self._append_log("WARN", "未选择命令串口，无法读取设备配置。")
            self._notify()
            return

        result = self._device_command_sender(self.status.command_port, ["cfg show"])
        if not result.ok:
            error_text = result.error or "设备未返回配置。"
            self._append_log("WARN", f"读取设备配置失败：{error_text}")
            self._notify()
            return

        if result.tuning is not None:
            self.status.tuning = result.tuning
        if result.record_mode:
            self.status.device_record_mode = result.record_mode
        for line in result.lines:
            self._append_log("ESP32", line)
        self._append_log("INFO", f"已从设备回读当前配置：{self.mic_gain_display_text()} / shift {self.sample_shift_display_text()}")
        self._notify()

    def update_tuning(self, tuning: AudioTuningSnapshot) -> None:
        self.status.tuning = tuning
        self._append_log(
            "INFO",
            (
                "音频参数已更新："
                f"gain_q8={tuning.mic_gain_q8}, "
                f"shift={tuning.sample_shift_bits}, "
                f"gate={tuning.noise_gate}, "
                f"tone_q8={tuning.tone_gain_q8}"
            ),
        )
        self._notify()
        self._push_tuning_to_device()

    def simulate_tone(self, tone_label: str) -> None:
        self.status.last_tone = tone_label
        self._handle_tone_label(tone_label)
        self._append_log("EVENT", f"最近编码事件：{tone_label}")
        self._notify()

    def handle_probe_event(self, event: dict[str, object]) -> None:
        event_kind = str(event.get("event_kind", ""))
        raw_text = str(event.get("raw_text", ""))
        now = time.monotonic()
        self._last_probe_event_at = now

        if event_kind == "tone":
            tone_source = str(event.get("tone_source", "TONE")).upper()
            tone_event = str(event.get("tone_event", "")).upper()
            if tone_event:
                self.status.last_tone = f"{tone_source} {tone_event}"
                tone_label = {
                    "START": "Start Tone",
                    "STOP": "Stop Tone",
                    "A": "Tone A",
                    "B": "Tone B",
                    "C": "Tone C",
                }.get(tone_event)
                if tone_label:
                    before_snapshot = self.shortcut_service.diagnostic_snapshot()
                    before_active = self.shortcut_service.active_keys
                    expected_keys = self._current_start_keys() if tone_event == "START" else self._keys_for_slot({
                        "A": TONE_SLOT_A,
                        "B": TONE_SLOT_B,
                        "C": TONE_SLOT_C,
                    }.get(tone_event, ""))
                    self._handle_tone_label(tone_label)
                    after_active = self.shortcut_service.active_keys
                    if tone_event in ("START", "STOP"):
                        self._last_shortcut_keepalive_at = now
                    elif after_active and expected_keys and after_active == expected_keys and before_active == expected_keys:
                        self._last_shortcut_keepalive_at = now
                    elif after_active != before_active:
                        self._last_shortcut_keepalive_at = now
                    self._append_diagnostic("tone", f"{tone_source} {tone_event} before={before_snapshot} after={self.shortcut_service.diagnostic_snapshot()}")
            if raw_text:
                self._append_log("PROBE", raw_text)
            self._notify()
            return

        if event_kind == "rms":
            peak = float(event.get("tone_peak", 0.0))
            rms = float(event.get("tone_rms", 0.0))
            if peak >= 0.02:
                self._last_shortcut_keepalive_at = now
                self._append_log("PROBE", f"PC RMS {rms:.6f} peak {peak:.6f}")
                self._notify()
            return

        if event_kind == "log" and raw_text:
            self._update_listener_phase_from_log(raw_text)
            self._append_log("PROBE", raw_text)
            self._notify()

    def release_shortcuts(self) -> None:
        released = self.shortcut_service.release()
        if released:
            self._append_log("ACTION", "已释放当前快捷键。")
        else:
            self._append_log("ACTION", "快捷键本来就是松开状态。")
        self._append_diagnostic("shortcut", self.shortcut_service.diagnostic_snapshot())
        self._notify()

    def test_current_shortcut(self) -> None:
        keys = self._current_start_keys()
        if not keys:
            self._append_log("ACTION", "当前主语音模型未绑定快捷键，测试已跳过。")
            self._notify()
            return
        self.shortcut_service.press(keys)
        self._append_log("ACTION", f"已测试快捷键：{self.current_start_action_display_text()}。")
        self._append_diagnostic("shortcut", self.shortcut_service.diagnostic_snapshot())
        self._notify()

    def test_tone_action(self, slot_id: str) -> None:
        if self._shortcut_recording_slot and slot_id != self._shortcut_recording_slot:
            self._append_log("INFO", f"正在录制 {self._shortcut_recording_slot} 快捷键，已忽略 {slot_id} 测试。")
            self._notify()
            return
        keys = self._keys_for_slot(slot_id)
        label = self.tone_action_display_text(slot_id)
        tone_name = {
            TONE_SLOT_START: "Start Tone",
            TONE_SLOT_A: "A Tone",
            TONE_SLOT_B: "B Tone",
            TONE_SLOT_C: "C Tone",
        }.get(slot_id, slot_id)
        if not keys:
            self._append_log("ACTION", f"{tone_name} 尚未绑定快捷键。")
            self._notify()
            return
        self.shortcut_service.tap(keys)
        self._append_log("ACTION", f"已测试 {tone_name}：{label}。")
        self._append_diagnostic("shortcut", self.shortcut_service.diagnostic_snapshot())
        self._notify()

    def set_shortcut_recording_slot(self, slot_id: str | None) -> None:
        normalized = slot_id or None
        if self._shortcut_recording_slot == normalized:
            return
        self._shortcut_recording_slot = normalized
        if normalized:
            self._append_log("INFO", f"开始录制 {normalized} 快捷键，已临时屏蔽其它 Tone 动作。")
            self._append_diagnostic("shortcut", f"recording_slot={normalized}")
        else:
            self._append_log("INFO", "快捷键录制结束，已恢复全部 Tone 动作。")
            self._append_diagnostic("shortcut", "recording_slot=<none>")
        self._notify()

    def start_probe_preview(self) -> None:
        command = self.probe_service.build_command()
        self._append_log("PROBE", f"准备启动 probe：{' '.join(command)}")
        self._notify()

    def start_probe_monitor(self) -> None:
        self._append_log("PROBE", "正在启动音频编码监听。")
        self._append_diagnostic("probe", f"start requested process_alive={bool(self.probe_service.process and self.probe_service.process.poll() is None)}")
        self.status.listener_phase = "starting"
        self._last_probe_event_at = time.monotonic()
        started = self.probe_service.start(self._handle_probe_event_object, emit_log=self._handle_probe_log_text)
        if started:
            self.status.state = "running"
            self.status.summary = "后台监听服务运行中"
            self.status.detail = "音频探针线程已启动，等待 HFP 端点进入 Active。"
        else:
            self._append_log("PROBE", "音频编码监听已经在运行。")
        self._notify()

    def stop_probe_monitor(self) -> None:
        self.probe_service.stop()
        if self.status.state != "stopped":
            self.status.listener_phase = "stopped"
        self._append_log("PROBE", "已请求停止音频编码监听。")
        self._append_diagnostic("probe", "stop requested")
        self._notify()

    def _maybe_force_release_stuck_shortcut(self) -> None:
        if not self.shortcut_service.is_pressed:
            return
        if (time.monotonic() - self._last_shortcut_keepalive_at) < self._stuck_shortcut_timeout_s:
            return
        if self.shortcut_service.release():
            timeout_ms = int(self._stuck_shortcut_timeout_s * 1000)
            self._append_log("WARN", f"自动释放卡住的快捷键：超过 {timeout_ms}ms 没有新的探针事件。")
            self._append_diagnostic("shortcut", f"auto_release {self.shortcut_service.diagnostic_snapshot()}")
            self._notify()
    def _maybe_recover_probe_monitor(self) -> None:
        if self.status.state != "running":
            return

        now = time.monotonic()
        if now - self._last_probe_recovery_at < 8.0:
            return

        if not self.probe_service.is_running():
            self._recover_probe_monitor("探针线程/进程已退出，但设备状态仍在线")
            return

        if (
            self.status.listener_phase == "monitoring"
            and self.has_active_device_status()
            and (now - self._last_probe_event_at) >= 10.0
        ):
            self._recover_probe_monitor("10 秒无探针事件，疑似监听链路卡住")

    def _recover_probe_monitor(self, reason: str) -> None:
        self._last_probe_recovery_at = time.monotonic()
        if self.shortcut_service.is_pressed:
            self.shortcut_service.release()
            self._append_log("WARN", "自动恢复前已释放当前快捷键，避免按键卡住。")
        self._append_log("WARN", f"自动重启音频探针：{reason}。")
        self.stop_probe_monitor()
        self.start_probe_monitor()

    def start_audio_status_watch(self) -> None:
        started = self.audio_status_watch_service.start(
            emit=self._handle_audio_status_watch_event_object,
            emit_log=self._handle_audio_status_watch_log_text,
        )
        if started:
            self._append_log("PROBE", "已启动音频状态事件监听。")
            self._notify()

    def stop_audio_status_watch(self) -> None:
        self.audio_status_watch_service.stop()

    def open_bluetooth_settings(self) -> None:
        self._open_windows_command(["explorer.exe", "ms-settings:bluetooth"], "已打开 Windows 蓝牙设置。", "打开 Windows 蓝牙设置失败")
        self._notify()

    def open_recording_panel(self) -> None:
        self._open_windows_command(["control.exe", "mmsys.cpl,,1"], "已打开 Windows 声音输入面板。", "打开 Windows 声音输入面板失败")
        self._notify()

    def open_log_window(self) -> None:
        self._append_log("ACTION", "日志窗口已打开。")
        self._notify()

    def enter_debug_mode(self) -> None:
        if self._debug_serial_enabled:
            self.refresh_runtime_state()
            self._notify()
            return
        self._debug_serial_enabled = True
        self.refresh_runtime_state()
        self._append_log("INFO", "已进入调试台：串口命令通道已启用。")
        self._append_diagnostic("service", f"debug_mode=on serial={self.status.serial_port_state}")
        self._notify()

    def leave_debug_mode(self) -> None:
        if not self._debug_serial_enabled:
            return
        self._debug_serial_enabled = False
        self.command_ports = []
        self.status.serial_port_state = "missing"
        self.status.serial_port_detail = "调试台未打开"
        self._append_log("INFO", "已退出调试台：串口命令通道已释放。")
        self._append_diagnostic("service", "debug_mode=off")
        self._notify()

    def _append_diagnostic(self, category: str, message: str) -> None:
        try:
            self._diagnostics_log_service.append(category, message)
        except Exception as exc:
            self._append_log("WARN", f"写入本地诊断日志失败：{exc}")

    def _append_log(self, level: str, message: str) -> None:
        self.logs.append(
            LogEntry(
                timestamp=datetime.now().strftime("%H:%M:%S"),
                level=level,
                message=message,
            )
        )
        self.logs = self.logs[-200:]
        if level in {"ACTION", "WARN", "ERROR", "EVENT"}:
            self._append_diagnostic(level.lower(), message)

    def _choose_default_serial_port(self, ports: list[str], preferred: str = "COM10") -> str:
        normalized = [port.strip() for port in ports if port and port.strip()]
        if preferred in normalized:
            return preferred
        return normalized[0] if normalized else ""

    def _list_serial_ports(self) -> list[str]:
        if list_ports is None:
            return []
        try:
            return sorted(port.device for port in list_ports.comports())
        except Exception:
            return []

    def _probe_serial_port_status(self, port_name: str, available_ports: list[str]) -> SerialPortStatusSnapshot:
        normalized_ports = {port.strip() for port in available_ports if port and port.strip()}
        if not port_name:
            return SerialPortStatusSnapshot(port_name=port_name, state="missing", detail="未选择串口")
        if port_name not in normalized_ports:
            return SerialPortStatusSnapshot(port_name=port_name, state="missing", detail="串口不存在")
        if serial is None:
            return SerialPortStatusSnapshot(port_name=port_name, state="unknown", detail="pyserial 不可用")

        probe = None
        try:
            probe = serial.Serial(port=port_name, baudrate=115200, timeout=0.15, write_timeout=0.15)
            return SerialPortStatusSnapshot(port_name=port_name, state="open", detail="串口可访问")
        except PermissionError:
            return SerialPortStatusSnapshot(port_name=port_name, state="busy", detail="串口被占用")
        except Exception as exc:
            message = str(exc).lower()
            if "access is denied" in message or "拒绝访问" in str(exc):
                return SerialPortStatusSnapshot(port_name=port_name, state="busy", detail=str(exc))
            return SerialPortStatusSnapshot(port_name=port_name, state="unknown", detail=str(exc))
        finally:
            if probe is not None:
                try:
                    probe.close()
                except Exception:
                    pass

    def _send_device_commands(self, port_name: str, commands: list[str]) -> DeviceCommandResult:
        if serial is None:
            return DeviceCommandResult(ok=False, error="pyserial 不可用")
        if not port_name:
            return DeviceCommandResult(ok=False, error="未选择命令串口")

        ser = None
        captured_lines: list[str] = []
        tuning: AudioTuningSnapshot | None = None
        record_mode = ""

        try:
            ser = serial.Serial(port=port_name, baudrate=115200, timeout=0.2, write_timeout=0.2)
            time.sleep(0.12)
            ser.reset_input_buffer()
            for command in commands:
                ser.write((command.strip() + "\n").encode("utf-8"))
                ser.flush()

            deadline = time.monotonic() + 1.6
            saw_cfg = False
            while time.monotonic() < deadline:
                raw = ser.readline()
                if not raw:
                    continue
                text = raw.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                captured_lines.append(text)

                cfg_match = SERIAL_CFG_STATUS_RE.search(text)
                if cfg_match:
                    tuning = AudioTuningSnapshot(
                        mic_gain_q8=int(cfg_match.group(1)),
                        noise_gate=int(cfg_match.group(3)),
                        tone_gain_q8=int(cfg_match.group(4)),
                        sample_shift_bits=int(cfg_match.group(6)),
                    )
                    saw_cfg = True

                record_match = SERIAL_RECORD_MODE_STATUS_RE.search(text)
                if record_match:
                    record_mode = record_match.group(1)

                if saw_cfg and ("cfg show" in " ".join(commands) or len(captured_lines) >= 1):
                    break

            if tuning is None:
                return DeviceCommandResult(
                    ok=False,
                    lines=tuple(captured_lines),
                    error="未收到 cfg 状态回显",
                )

            return DeviceCommandResult(
                ok=True,
                tuning=tuning,
                record_mode=record_mode,
                lines=tuple(captured_lines),
            )
        except Exception as exc:
            return DeviceCommandResult(ok=False, lines=tuple(captured_lines), error=str(exc))
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass

    def _read_audio_device_status(self) -> AudioDeviceStatusSnapshot:
        watch_snapshot = self.audio_status_watch_service.latest_snapshot()
        if watch_snapshot is not None:
            return watch_snapshot
        probe_snapshot = self._read_audio_device_status_from_probe()
        sound_snapshot = self._read_audio_device_status_from_sounddevice()
        if probe_snapshot is None:
            return sound_snapshot

        probe_current_input = probe_snapshot.current_input_name.strip()
        sound_current_input = sound_snapshot.current_input_name.strip()
        probe_detected_name = probe_snapshot.detected_airmic_input_name.strip()
        sound_detected_name = sound_snapshot.detected_airmic_input_name.strip()

        current_input_name = probe_current_input or sound_current_input
        detected_airmic_input_name = probe_detected_name or sound_detected_name
        detected_airmic_input_active = probe_snapshot.detected_airmic_input_active
        detected_airmic_device_present = probe_snapshot.detected_airmic_device_present
        has_any_available_input = probe_snapshot.has_any_available_input or sound_snapshot.has_any_available_input
        if probe_detected_name and not probe_snapshot.detected_airmic_input_active and _is_airmic_input_name(current_input_name):
            current_input_name = ""
        if detected_airmic_input_active and not current_input_name:
            current_input_name = detected_airmic_input_name

        return AudioDeviceStatusSnapshot(
            current_input_name=current_input_name,
            detected_airmic_input_name=detected_airmic_input_name,
            detected_airmic_input_active=detected_airmic_input_active,
            detected_airmic_device_present=detected_airmic_device_present,
            has_any_available_input=has_any_available_input,
        )

    def _read_audio_device_status_from_probe(self) -> AudioDeviceStatusSnapshot | None:
        probe_bin_dir = tool_path("audio_probe", "bin")
        probe_exe = None
        for candidate_name in ("AirMicAudioProbe_status.exe", "AirMicAudioProbe.exe"):
            candidate = probe_bin_dir / candidate_name
            if candidate.exists():
                probe_exe = candidate
                break
        if probe_exe is None:
            return None

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                [str(probe_exe), "--default-input", "--list", "--all"],
                cwd=str(self.probe_service.project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                creationflags=creationflags,
            )
        except Exception:
            return None

        if completed.returncode != 0:
            return None

        has_any_available_input = False
        current_input_name = ""
        current_input_role = ""
        detected_airmic_input_name = ""
        detected_airmic_input_active = False
        detected_airmic_device_present = False

        for line in completed.stdout.splitlines():
            default_match = PROBE_DEFAULT_INPUT_RE.match(line.strip())
            if default_match:
                role = default_match.group(1).strip()
                friendly_name = default_match.group(2).strip()
                if friendly_name != "<none>" and (not current_input_name or role == "Communications" or current_input_role != "Communications"):
                    current_input_name = friendly_name
                    current_input_role = role
                continue
            match = PROBE_LIST_LINE_RE.match(line.strip())
            if not match:
                continue
            friendly_name = match.group(1).strip()
            state = match.group(2).strip()
            lowered = friendly_name.lower()
            if state == "Active":
                has_any_available_input = True
            if "esp32-airmic-hfp" not in lowered and ("airmic" not in lowered or "hands-free" not in lowered):
                continue
            detected_airmic_input_name = friendly_name
            if state == "Active":
                detected_airmic_input_active = True
                current_input_name = friendly_name
                break

        return AudioDeviceStatusSnapshot(
            current_input_name=current_input_name,
            detected_airmic_input_name=detected_airmic_input_name,
            detected_airmic_input_active=detected_airmic_input_active,
            detected_airmic_device_present=detected_airmic_device_present,
            has_any_available_input=has_any_available_input,
        )

    def _read_audio_device_status_from_sounddevice(self) -> AudioDeviceStatusSnapshot:
        if sd is None:
            return AudioDeviceStatusSnapshot()
        try:
            devices = sd.query_devices()
            default_input_index, _default_output_index = sd.default.device
        except Exception:
            return AudioDeviceStatusSnapshot()

        current_input_name = ""
        detected_airmic_input_name = ""
        detected_airmic_input_active = False

        for index, device in enumerate(devices):
            max_input = int(device.get("max_input_channels", 0) or 0)
            if max_input <= 0:
                continue
            name = str(device.get("name", "")).strip()
            if index == default_input_index:
                current_input_name = name
            lowered = name.lower()
            if "esp32-airmic-hfp" in lowered or ("airmic" in lowered and "hands-free" in lowered):
                detected_airmic_input_name = name
                if index == default_input_index and default_input_index >= 0:
                    detected_airmic_input_active = True

        return AudioDeviceStatusSnapshot(
            current_input_name=current_input_name,
            detected_airmic_input_name=detected_airmic_input_name,
            detected_airmic_input_active=detected_airmic_input_active,
            detected_airmic_device_present=False,
            has_any_available_input=any(int(device.get("max_input_channels", 0) or 0) > 0 for device in devices),
        )

    def _push_tuning_to_device(self) -> None:
        if not self._debug_serial_enabled:
            self._append_log("INFO", "调试台未打开，参数暂不发送到设备。")
            return
        if self.status.serial_port_state not in {"open", "busy", "unknown"}:
            return
        if not self.status.command_port:
            return

        commands = [
            f"cfg gain {self.status.tuning.mic_gain_q8}",
            f"cfg gate {self.status.tuning.noise_gate}",
            f"cfg tone {self.status.tuning.tone_gain_q8}",
            f"cfg shift {self.status.tuning.sample_shift_bits}",
        ]
        result = self._device_command_sender(self.status.command_port, commands)
        if result.ok:
            self._append_log("INFO", "音频参数已发送到设备。")
        else:
            error_text = result.error or "设备未确认参数。"
            self._append_log("WARN", f"发送音频参数失败：{error_text}")

    def _update_listener_phase_from_log(self, raw_text: str) -> None:
        lowered = raw_text.lower()
        endpoint_state_match = PROBE_AIRMIC_STATE_LINE_RE.match(raw_text.strip())
        if endpoint_state_match:
            friendly_name = endpoint_state_match.group(1).strip()
            endpoint_state = endpoint_state_match.group(2).strip()
            if _is_airmic_input_name(friendly_name):
                self.status.detected_airmic_input = friendly_name
                if endpoint_state == "Active":
                    self.status.detected_airmic_input_active = True
                    self.status.current_input_name = friendly_name
                elif endpoint_state in {"Unplugged", "NotPresent", "Disabled"}:
                    self.status.detected_airmic_input_active = False
                    if _is_airmic_input_name(self.status.current_input_name):
                        self.status.current_input_name = ""
        if "waiting for matching wasapi capture endpoint" in lowered:
            self.status.listener_phase = "waiting_hfp"
            self.status.summary = "后台监听等待 HFP 音频端点"
            self.status.detail = "探针已启动，正在等待 ESP32-AirMic-HFP 输入端点变为 Active。"
            return
        if "capturing. press ctrl+c to stop." in lowered:
            self.status.listener_phase = "monitoring"
            self.status.summary = "后台监听服务运行中"
            self.status.detail = "探针已经开始捕获 HFP 音频流，等待编码事件。"
            return
        if "no audio callbacks" in lowered or "tone probe start failed" in lowered or "tone probe not found" in lowered:
            self.status.listener_phase = "error"
            self.status.summary = "后台监听异常"
            self.status.detail = raw_text

    def _notify(self) -> None:
        for callback in list(self._listeners):
            owner = getattr(callback, "__self__", None)
            if not isinstance(owner, QObject):
                callback(self.status)
        self.statusChanged.emit(self.status)

    def _restore_persisted_settings(self) -> None:
        persisted = self._settings_service.load()

        voice_model_id = persisted.get("voice_model_id")
        if isinstance(voice_model_id, str) and voice_model_id in VOICE_MODEL_PRESETS_BY_ID:
            self.status.current_voice_model_id = voice_model_id

        tone_action_map = persisted.get("tone_action_map")
        if isinstance(tone_action_map, dict):
            for slot_id in (TONE_SLOT_START, TONE_SLOT_A, TONE_SLOT_B, TONE_SLOT_C):
                action_id = tone_action_map.get(slot_id)
                if slot_id == TONE_SLOT_START and action_id == "voice_model_default":
                    self.status.tone_action_map[slot_id] = action_id
                    continue
                if isinstance(action_id, str) and action_id in ACTION_PRESETS_BY_ID:
                    self.status.tone_action_map[slot_id] = action_id

        custom_tone_action_map = persisted.get("custom_tone_action_map")
        if isinstance(custom_tone_action_map, dict):
            for slot_id in (TONE_SLOT_START, TONE_SLOT_A, TONE_SLOT_B, TONE_SLOT_C):
                keys = custom_tone_action_map.get(slot_id)
                if not isinstance(keys, list):
                    continue
                if not keys or not all(
                    isinstance(key, str) and (key in KEY_OPTIONS or (len(key) == 1 and key.isalnum()))
                    for key in keys
                ):
                    continue
                normalized_keys = tuple(keys)
                self.status.tone_action_map[slot_id] = "custom"
                self.status.custom_tone_action_map[slot_id] = normalized_keys

    def _persist_shortcut_settings(self) -> None:
        self._settings_service.save(
            {
                "voice_model_id": self.status.current_voice_model_id,
                "tone_action_map": dict(self.status.tone_action_map),
                "custom_tone_action_map": {
                    slot_id: list(keys)
                    for slot_id, keys in self.status.custom_tone_action_map.items()
                },
            }
        )

    def _handle_probe_event_object(self, event: ProbeEvent) -> None:
        self.probeEventReady.emit(event)

    @Slot(object)
    def _handle_probe_event_ready(self, event: object) -> None:
        if not isinstance(event, ProbeEvent):
            return
        self.handle_probe_event(
            {
                "event_kind": event.event_kind,
                "raw_text": event.raw_text,
                "tone_source": event.tone_source,
                "tone_event": event.tone_event,
                "tone_rms": event.tone_rms,
                "tone_peak": event.tone_peak,
            }
        )

    def _handle_probe_log_text(self, text: str) -> None:
        self.probeLogReady.emit(text)

    @Slot(str)
    def _handle_probe_log_ready(self, text: str) -> None:
        self._append_log("PROBE", text)
        self._notify()

    def _handle_audio_status_watch_event_object(self, event: AudioStatusWatchEvent) -> None:
        self.audioStatusWatchEventReady.emit(event)

    def _handle_audio_status_watch_event_ready(self, event: object) -> None:
        if not isinstance(event, AudioStatusWatchEvent):
            return
        if event.hint_kind == "pnp" and event.hint_operation in {"delete", "remove"} and _is_airmic_input_name(event.hint_name):
            self.status.detected_airmic_input = event.hint_name.strip() or self.status.detected_airmic_input
            self.status.detected_airmic_input_active = False
            self.status.detected_airmic_device_present = False
            if _is_airmic_input_name(self.status.current_input_name):
                self.status.current_input_name = ""
            self._append_log("PROBE", event.raw_text)
            self._notify()
            return
        self.status.detected_airmic_input = event.snapshot.detected_airmic_input_name.strip()
        self.status.detected_airmic_input_active = event.snapshot.detected_airmic_input_active
        self.status.detected_airmic_device_present = event.snapshot.detected_airmic_device_present
        self.status.has_any_available_input = event.snapshot.has_any_available_input
        self.status.current_input_name = event.snapshot.current_input_name.strip()
        self._append_log("PROBE", event.raw_text)
        self._notify()

    def _handle_audio_status_watch_log_text(self, text: str) -> None:
        self._append_log("PROBE", text)
        self._notify()

    def _current_start_keys(self) -> tuple[str, ...]:
        return self._keys_for_slot(TONE_SLOT_START)

    def _keys_for_slot(self, slot_id: str) -> tuple[str, ...]:
        if slot_id == TONE_SLOT_START and self.status.tone_action_map[slot_id] == "voice_model_default":
            voice_model = get_voice_model_preset_by_id(self.status.current_voice_model_id)
            action = get_action_preset_by_id(voice_model.action_preset_id)
            return action.keys
        if self.status.tone_action_map[slot_id] == "custom":
            return self.status.custom_tone_action_map[slot_id]
        action = get_action_preset_by_id(self.status.tone_action_map[slot_id])
        return action.keys

    def _handle_tone_label(self, tone_label: str) -> None:
        normalized = tone_label.strip().lower()
        if normalized == "start tone":
            self._handle_press_tone(TONE_SLOT_START, "Start Tone", is_start=True)
            return
        if normalized == "stop tone":
            if self.shortcut_service.release():
                self._append_log("ACTION", "收到 STOP：已释放当前快捷键。")
            else:
                self._append_log("ACTION", "收到 STOP：快捷键本来就是松开。")
            self._append_diagnostic("shortcut", self.shortcut_service.diagnostic_snapshot())
            return
        if normalized == "tone a":
            self._handle_press_tone(TONE_SLOT_A, "Tone A")
            return
        if normalized == "tone b":
            self._handle_press_tone(TONE_SLOT_B, "Tone B")
            return
        if normalized == "tone c":
            self._handle_press_tone(TONE_SLOT_C, "Tone C")

    def _handle_press_tone(self, slot_id: str, label: str, is_start: bool = False) -> None:
        if self._shortcut_recording_slot and self._shortcut_recording_slot != slot_id:
            self._append_log("INFO", f"录制快捷键中，已忽略 {label}。")
            self._append_diagnostic("shortcut", f"ignored_aux={slot_id} recording_slot={self._shortcut_recording_slot}")
            return
        keys = self._keys_for_slot(slot_id)
        if not keys:
            self._append_log("ACTION", f"{label} 当前未绑定动作。")
            return
        if self.shortcut_service.is_pressed:
            if self.shortcut_service.active_keys == keys:
                prefix = "start" if is_start else f"keepalive_aux={slot_id}"
                self._append_diagnostic("shortcut", f"{prefix} active={self.shortcut_service.diagnostic_snapshot()}")
                return
            self._append_log("INFO", f"已有其它按键按住，已忽略 {label}。")
            self._append_diagnostic("shortcut", f"ignored_aux={slot_id} active={self.shortcut_service.diagnostic_snapshot()}")
            return
        self._release_shortcut_before_new_event(label)
        triggered = self.shortcut_service.press(keys)
        if is_start:
            display = self.current_start_action_display_text()
            if triggered:
                self._append_log("ACTION", f"收到 START：触发 {display}。")
            else:
                self._append_log("ACTION", f"收到 START：{display} 已经按下。")
            self._append_diagnostic("shortcut", f"start {self.shortcut_service.diagnostic_snapshot()}")
            return
        if self.status.tone_action_map[slot_id] == "custom":
            self._append_log("ACTION", f"{label} 已触发：{format_key_chord(keys)}。")
            self._append_diagnostic("shortcut", f"{slot_id} press {self.shortcut_service.diagnostic_snapshot()}")
            return
        action = get_action_preset_by_id(self.status.tone_action_map[slot_id])
        self._append_log("ACTION", f"{label} 已触发：{action.chord_label}。")
        self._append_diagnostic("shortcut", f"{slot_id} press {self.shortcut_service.diagnostic_snapshot()}")

    def _release_shortcut_before_new_event(self, incoming_label: str) -> None:
        if not self.shortcut_service.is_pressed:
            return
        snapshot = self.shortcut_service.diagnostic_snapshot()
        if self.shortcut_service.release():
            self._append_log("WARN", f"检测到残留按键，处理 {incoming_label} 前已先释放。")
            self._append_diagnostic("shortcut", f"pre_release incoming={incoming_label} {snapshot}")

    def _format_binding_for_console(self, keys: tuple[str, ...]) -> str:
        if not keys:
            return "点击录制"
        label_map = {
            "right_alt": "右 Alt",
            "left_alt": "左 Alt",
            "left_ctrl": "左 Ctrl",
            "right_ctrl": "右 Ctrl",
            "left_win": "左 Win",
            "right_win": "右 Win",
            "shift": "Shift",
            "space": "Space",
            "enter": "Enter",
            "backspace": "Backspace",
            "tab": "Tab",
        }
        parts: list[str] = []
        for key in keys:
            if len(key) == 1 and key.isalpha():
                parts.append(key.upper())
            else:
                parts.append(label_map.get(key, key))
        return " + ".join(parts)

    def _open_windows_command(self, command: list[str], success_message: str, failure_prefix: str) -> None:
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
            self._append_log("ACTION", success_message)
        except Exception as exc:
            self._append_log("WARN", f"{failure_prefix}: {exc}")




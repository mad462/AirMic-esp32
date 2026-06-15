import unittest
from pathlib import Path
import threading
import time
from tempfile import TemporaryDirectory
from unittest import mock

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from core.models.app_state import (
    AudioDeviceStatusSnapshot,
    AudioTuningSnapshot,
    DeviceCommandResult,
    SerialPortStatusSnapshot,
    TONE_SLOT_A,
    TONE_SLOT_B,
    TONE_SLOT_START,
    tone_slot_ids,
)
from core.shortcut.presets import DEFAULT_VOICE_MODEL_ID, VOICE_MODEL_OPTIONS, get_voice_model_preset_by_id
from services.backend_coordinator import BackendCoordinator
from services.settings_service import SettingsService
from services.audio_status_watch_service import AudioStatusWatchEvent
from services.probe_service import ProbeEvent
from services.startup_service import StartupService
from services.diagnostics_log_service import DiagnosticsLogService


class AppStateTest(unittest.TestCase):
    def test_start_slot_is_first_and_fixed(self):
        self.assertEqual(tone_slot_ids()[0], TONE_SLOT_START)

    def test_default_preset_exists(self):
        preset = get_voice_model_preset_by_id(DEFAULT_VOICE_MODEL_ID)
        self.assertEqual(preset.display_name, "千问 App")
        self.assertTrue(any(item.preset_id == DEFAULT_VOICE_MODEL_ID for item in VOICE_MODEL_OPTIONS))


class BackendCoordinatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_coordinator(self, **kwargs) -> BackendCoordinator:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        settings_service = kwargs.pop("settings_service", SettingsService(Path(temp_dir.name) / "airmic-settings.json"))
        return BackendCoordinator(settings_service=settings_service, **kwargs)

    def test_start_moves_service_to_running_and_logs(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))
        watch_starts: list[str] = []
        coordinator.audio_status_watch_service.start = lambda emit, emit_log=None: watch_starts.append("started") or True

        coordinator.start()

        self.assertEqual(coordinator.status.state, "running")
        self.assertIn("后台监听服务运行中", coordinator.status.summary)
        self.assertTrue(coordinator.logs)
        self.assertEqual(watch_starts, ["started"])

    def test_restart_keeps_service_running_and_adds_log(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))
        stops: list[str] = []
        starts: list[str] = []
        coordinator.audio_status_watch_service.stop = lambda: stops.append("stopped")
        coordinator.audio_status_watch_service.start = lambda emit, emit_log=None: starts.append("started") or True
        coordinator.start()
        first_log_count = len(coordinator.logs)

        coordinator.restart()

        self.assertEqual(coordinator.status.state, "running")
        self.assertGreater(len(coordinator.logs), first_log_count)
        self.assertTrue(any("已重启" in item.message for item in coordinator.logs))
        self.assertEqual(stops, ["stopped"])
        self.assertEqual(starts[-1:], ["started"])

    def test_simulate_start_tone_triggers_current_voice_shortcut(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []

        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )

        coordinator.simulate_tone("Start Tone")

        self.assertEqual(
            sent_actions,
            [(("right_alt",), True, "scan")],
        )
        self.assertEqual(coordinator.status.last_tone, "Start Tone")

    def test_duplicate_start_tone_does_not_release_and_repress_same_shortcut(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )

        coordinator.simulate_tone("Start Tone")
        coordinator.simulate_tone("Start Tone")

        self.assertEqual(sent_actions, [(("right_alt",), True, "scan")])
        self.assertTrue(any("已经按下" in item.message for item in coordinator.logs))

    def test_simulate_tone_a_presses_aux_mapping_until_release(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )
        coordinator.set_tone_action("tone_a", "ctrl_win")
        sent_actions.clear()

        coordinator.simulate_tone("Tone A")

        self.assertEqual(
            sent_actions,
            [
                (("left_ctrl", "left_win"), True, "scan"),
            ],
        )

    def test_simulate_tone_a_uses_custom_recorded_shortcut(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = BackendCoordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )

        coordinator.set_custom_tone_action("tone_a", ("right_alt", "space"))
        sent_actions.clear()

        coordinator.simulate_tone("Tone A")

        self.assertEqual(
            sent_actions,
            [
                (("right_alt", "space"), True, "scan"),
                (("right_alt", "space"), False, "scan"),
            ],
        )

    def test_simulate_tone_a_uses_recorded_modifier_combo_shortcut(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = BackendCoordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )

        coordinator.set_custom_tone_action("tone_a", ("left_ctrl", "left_win"))
        sent_actions.clear()

        coordinator.simulate_tone("Tone A")

        self.assertEqual(
            sent_actions,
            [
                (("left_ctrl", "left_win"), True, "scan"),
                (("left_ctrl", "left_win"), False, "scan"),
            ],
        )

    def test_persists_voice_model_and_custom_tone_actions(self):
        with TemporaryDirectory() as temp_dir:
            settings_service = SettingsService(Path(temp_dir) / "airmic-settings.json")
            coordinator = BackendCoordinator(
                settings_service=settings_service,
                device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
            )

            coordinator.set_voice_model("custom_api")
            coordinator.set_custom_tone_action("start", ("right_ctrl", "space"))
            coordinator.set_custom_tone_action("tone_a", ("left_ctrl", "a"))
            coordinator.set_tone_action("tone_b", "ctrl_win")

            restored = BackendCoordinator(
                settings_service=settings_service,
                device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
            )

            self.assertEqual(restored.status.current_voice_model_id, "custom_api")
            self.assertEqual(restored.status.tone_action_map["start"], "custom")
            self.assertEqual(restored.status.custom_tone_action_map["start"], ("right_ctrl", "space"))
            self.assertEqual(restored.status.tone_action_map["tone_a"], "custom")
            self.assertEqual(restored.status.custom_tone_action_map["tone_a"], ("left_ctrl", "a"))
            self.assertEqual(restored.status.tone_action_map["tone_b"], "ctrl_win")

    def test_reads_startup_enabled_state_from_startup_service(self):
        startup_service = mock.Mock(spec=StartupService)
        startup_service.is_enabled.return_value = True

        coordinator = BackendCoordinator(
            startup_service=startup_service,
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        self.assertTrue(coordinator.status.startup_enabled)
        startup_service.is_enabled.assert_called_once()

    def test_updates_startup_enabled_state_and_logs_result(self):
        startup_service = mock.Mock(spec=StartupService)
        startup_service.is_enabled.return_value = False
        startup_service.set_enabled.return_value = True

        coordinator = BackendCoordinator(
            startup_service=startup_service,
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        coordinator.set_startup_enabled(True)

        self.assertTrue(coordinator.status.startup_enabled)
        startup_service.set_enabled.assert_called_once_with(True)
        self.assertTrue(any("开机启动已开启" in entry.message for entry in coordinator.logs))

    def test_ignores_invalid_persisted_shortcut_settings_and_keeps_defaults(self):
        with TemporaryDirectory() as temp_dir:
            settings_service = SettingsService(Path(temp_dir) / "airmic-settings.json")
            settings_service.save(
                {
                    "voice_model_id": "missing_model",
                    "tone_action_map": {
                        "tone_a": "missing_action",
                        "tone_b": "custom",
                    },
                    "custom_tone_action_map": {
                        "tone_b": ["left_ctrl", "missing_key"],
                    },
                }
            )

            coordinator = BackendCoordinator(
                settings_service=settings_service,
                device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
            )

            self.assertEqual(coordinator.status.current_voice_model_id, DEFAULT_VOICE_MODEL_ID)
            self.assertEqual(coordinator.status.tone_action_map["tone_a"], "disabled")
            self.assertEqual(coordinator.status.tone_action_map["tone_b"], "disabled")
            self.assertEqual(coordinator.status.custom_tone_action_map["tone_b"], ())

    def test_stop_tone_releases_active_shortcut(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )

        coordinator.simulate_tone("Start Tone")
        coordinator.simulate_tone("Stop Tone")

        self.assertEqual(
            sent_actions,
            [
                (("right_alt",), True, "scan"),
                (("right_alt",), False, "scan"),
            ],
        )

    def test_test_tone_action_can_preview_start_tone(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )

        coordinator.test_tone_action(TONE_SLOT_START)

        self.assertEqual(
            sent_actions,
            [
                (("right_alt",), True, "scan"),
                (("right_alt",), False, "scan"),
            ],
        )

    def test_test_tone_action_can_preview_custom_start_tone(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )
        coordinator.set_custom_tone_action(TONE_SLOT_START, ("right_ctrl", "space"))
        sent_actions.clear()

        coordinator.test_tone_action(TONE_SLOT_START)

        self.assertEqual(
            sent_actions,
            [
                (("right_ctrl", "space"), True, "scan"),
                (("right_ctrl", "space"), False, "scan"),
            ],
        )

    def test_test_tone_action_can_preview_custom_aux_tone(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = BackendCoordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )
        coordinator.set_custom_tone_action(TONE_SLOT_A, ("left_ctrl", "space"))
        sent_actions.clear()

        coordinator.test_tone_action(TONE_SLOT_A)

        self.assertEqual(
            sent_actions,
            [
                (("left_ctrl", "space"), True, "scan"),
                (("left_ctrl", "space"), False, "scan"),
            ],
        )

    def test_formats_tuning_values_for_debug_panel(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))

        self.assertEqual(coordinator.mic_gain_display_text(), "16.00X")
        self.assertEqual(coordinator.tone_gain_display_text(), "1X")
        self.assertEqual(coordinator.sample_shift_display_text(), "11")
        self.assertEqual(coordinator.noise_gate_display_text(), "0")

    def test_refresh_runtime_state_uses_real_serial_ports_and_audio_snapshot(self):
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: ["COM7", "COM10"],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="麦克风 (Realtek(R) Audio)",
                detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                detected_airmic_input_active=True,
                has_any_available_input=True,
            ),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="open",
                detail="ready",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        coordinator.refresh_runtime_state()

        self.assertEqual([item.device_id for item in coordinator.command_ports], ["COM7", "COM10"])
        self.assertEqual(coordinator.serial_status_text(), "在线")
        self.assertEqual(coordinator.device_status_text(), "在线")
        self.assertEqual(coordinator.system_input_display_text(), "麦克风 (Realtek(R) Audio)")

    def test_refresh_runtime_state_marks_serial_offline_when_selected_port_missing(self):
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: ["COM7"],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="麦克风 (USB Audio Device)",
                detected_airmic_input_name="",
            ),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="missing",
                detail="not found",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )
        coordinator.status.command_port = "COM10"

        coordinator.refresh_runtime_state()

        self.assertEqual(coordinator.serial_status_text(), "未连接")
        self.assertEqual(coordinator.device_status_text(), "离线")

    def test_periodic_runtime_refresh_picks_up_device_removal(self):
        snapshots = iter(
            [
                AudioDeviceStatusSnapshot(
                    current_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                    detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                    detected_airmic_input_active=True,
                    has_any_available_input=True,
                ),
                AudioDeviceStatusSnapshot(
                    current_input_name="麦克风 (Realtek(R) Audio)",
                    detected_airmic_input_name="",
                    detected_airmic_input_active=False,
                    has_any_available_input=True,
                ),
            ]
        )
        ports = iter([["COM10"], []])

        coordinator = BackendCoordinator(
            serial_port_provider=lambda: next(ports),
            audio_status_provider=lambda: next(snapshots),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="open" if available_ports else "missing",
                detail="ready" if available_ports else "removed",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        self.assertEqual(coordinator.device_status_text(), "在线")
        self.assertEqual(coordinator.serial_status_text(), "未连接")

        coordinator.enter_debug_mode()
        self.assertEqual(coordinator.serial_status_text(), "在线")

        coordinator.tick_runtime_state()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and coordinator.device_status_text() != "离线":
            self.app.processEvents()
            time.sleep(0.02)

        self.assertEqual(coordinator.device_status_text(), "离线")
        self.assertEqual(coordinator.system_input_display_text(), "麦克风 (Realtek(R) Audio)")

    def test_detected_airmic_name_without_active_state_counts_as_offline(self):
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: ["COM10"],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                detected_airmic_input_active=False,
                has_any_available_input=True,
            ),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="open",
                detail="ready",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        coordinator.refresh_runtime_state()

        self.assertEqual(coordinator.device_status_text(), "离线")

    def test_active_airmic_current_input_shows_airmic_label(self):
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: ["COM10"],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                detected_airmic_input_active=True,
                has_any_available_input=True,
            ),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="open",
                detail="ready",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        coordinator.refresh_runtime_state()

        self.assertEqual(coordinator.device_status_text(), "在线")
        self.assertEqual(coordinator.system_input_display_text(), "AirMic HFP麦克风")

    def test_no_available_microphone_shows_no_microphone(self):
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: [],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="",
                detected_airmic_input_name="",
                detected_airmic_input_active=False,
                has_any_available_input=False,
            ),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="missing",
                detail="not found",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        coordinator.refresh_runtime_state()

        self.assertEqual(coordinator.system_input_display_text(), "无麦克风")

    def test_unplugged_airmic_probe_snapshot_stays_offline_even_if_name_persists(self):
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: ["COM10"],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="",
                detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                detected_airmic_input_active=False,
                has_any_available_input=True,
            ),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="open",
                detail="ready",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        coordinator.refresh_runtime_state()

        self.assertEqual(coordinator.device_status_text(), "离线")
        self.assertEqual(coordinator.system_input_display_text(), "未选择")

    def test_device_status_ignores_serial_presence_when_hfp_not_active(self):
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: ["COM10"],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="麦克风 (Realtek(R) Audio)",
                detected_airmic_input_name="",
                detected_airmic_input_active=False,
                has_any_available_input=True,
            ),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="open",
                detail="ready",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        coordinator.refresh_runtime_state()

        self.assertEqual(coordinator.device_status_text(), "离线")

    def test_runtime_refresh_does_not_probe_serial_until_debug_mode_enabled(self):
        serial_status_calls: list[tuple[str, list[str]]] = []

        coordinator = BackendCoordinator(
            serial_port_provider=lambda: ["COM10"],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="麦克风 (Realtek(R) Audio)",
                detected_airmic_input_name="",
                detected_airmic_input_active=False,
                has_any_available_input=True,
            ),
            serial_status_provider=lambda port_name, available_ports: serial_status_calls.append(
                (port_name, list(available_ports))
            ) or SerialPortStatusSnapshot(
                port_name=port_name,
                state="open",
                detail="ready",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        self.assertEqual(serial_status_calls, [])
        self.assertEqual(coordinator.command_ports, [])
        self.assertEqual(coordinator.serial_status_text(), "未连接")

        coordinator.enter_debug_mode()

        self.assertEqual(serial_status_calls, [("COM10", ["COM10"])])
        self.assertEqual([item.device_id for item in coordinator.command_ports], ["COM10"])
        self.assertEqual(coordinator.serial_status_text(), "在线")

    def test_leaving_debug_mode_clears_serial_runtime_state(self):
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: ["COM10"],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="麦克风 (Realtek(R) Audio)",
                detected_airmic_input_name="",
                detected_airmic_input_active=False,
                has_any_available_input=True,
            ),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="open",
                detail="ready",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        coordinator.enter_debug_mode()
        self.assertEqual(coordinator.serial_status_text(), "在线")

        coordinator.leave_debug_mode()

        self.assertEqual(coordinator.command_ports, [])
        self.assertEqual(coordinator.serial_status_text(), "未连接")
        self.assertEqual(coordinator.status.serial_port_detail, "调试台未打开")

    def test_refresh_device_config_skips_serial_when_debug_mode_closed(self):
        sent_commands: list[tuple[str, list[str]]] = []
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: ["COM10"],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="麦克风 (Realtek(R) Audio)",
                detected_airmic_input_name="",
                detected_airmic_input_active=False,
                has_any_available_input=True,
            ),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="open",
                detail="ready",
            ),
            device_command_sender=lambda port_name, commands: sent_commands.append((port_name, list(commands))) or DeviceCommandResult(
                ok=True,
                tuning=AudioTuningSnapshot(),
            ),
        )

        coordinator.refresh_device_config()
        self.assertEqual(sent_commands, [])

        coordinator.enter_debug_mode()
        sent_commands.clear()
        coordinator.refresh_device_config()

        self.assertEqual(sent_commands, [("COM10", ["cfg show"])])

    def test_probe_snapshot_keeps_windows_default_input_when_airmic_not_active(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))
        probe_snapshot = AudioDeviceStatusSnapshot(
            current_input_name="",
            detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
            detected_airmic_input_active=False,
            has_any_available_input=True,
        )
        sound_snapshot = AudioDeviceStatusSnapshot(
            current_input_name="麦克风 (Realtek(R) Audio)",
            detected_airmic_input_name="",
            detected_airmic_input_active=False,
            has_any_available_input=True,
        )

        coordinator._read_audio_device_status_from_probe = lambda: probe_snapshot  # type: ignore[method-assign]
        coordinator._read_audio_device_status_from_sounddevice = lambda: sound_snapshot  # type: ignore[method-assign]

        snapshot = coordinator._read_audio_device_status()

        self.assertEqual(snapshot.current_input_name, "麦克风 (Realtek(R) Audio)")
        self.assertEqual(snapshot.detected_airmic_input_name, "耳机 (ESP32-AirMic-HFP Hands-Free)")
        self.assertFalse(snapshot.detected_airmic_input_active)
        self.assertTrue(snapshot.has_any_available_input)

    def test_probe_inactive_airmic_overrides_stale_sounddevice_airmic_active_state(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))
        probe_snapshot = AudioDeviceStatusSnapshot(
            current_input_name="麦克风 (Realtek(R) Audio)",
            detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
            detected_airmic_input_active=False,
            has_any_available_input=True,
        )
        sound_snapshot = AudioDeviceStatusSnapshot(
            current_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
            detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
            detected_airmic_input_active=True,
            has_any_available_input=True,
        )

        coordinator._read_audio_device_status_from_probe = lambda: probe_snapshot  # type: ignore[method-assign]
        coordinator._read_audio_device_status_from_sounddevice = lambda: sound_snapshot  # type: ignore[method-assign]

        snapshot = coordinator._read_audio_device_status()

        self.assertEqual(snapshot.current_input_name, "麦克风 (Realtek(R) Audio)")
        self.assertEqual(snapshot.detected_airmic_input_name, "耳机 (ESP32-AirMic-HFP Hands-Free)")
        self.assertFalse(snapshot.detected_airmic_input_active)

    def test_non_airmic_default_input_is_reported_but_not_marked_active(self):
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: ["COM10"],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="麦克风 (Realtek(R) Audio)",
                detected_airmic_input_name="",
                detected_airmic_input_active=False,
                has_any_available_input=True,
            ),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="open",
                detail="ready",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        coordinator.refresh_runtime_state()

        self.assertEqual(coordinator.system_input_display_text(), "麦克风 (Realtek(R) Audio)")
        self.assertFalse(coordinator.has_active_system_input())

    def test_device_offline_but_windows_still_has_other_microphone(self):
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: [],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="麦克风 (Realtek(R) Audio)",
                detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                detected_airmic_input_active=False,
                has_any_available_input=True,
            ),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="missing",
                detail="removed",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        coordinator.refresh_runtime_state()

        self.assertEqual(coordinator.device_status_text(), "离线")
        self.assertEqual(coordinator.system_input_display_text(), "麦克风 (Realtek(R) Audio)")

    def test_device_stays_offline_before_explicit_presence_confirmation(self):
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: ["COM10"],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="麦克风 (Realtek(R) Audio)",
                detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                detected_airmic_input_active=False,
                has_any_available_input=True,
            ),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="open",
                detail="ready",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        coordinator.refresh_runtime_state()

        self.assertEqual(coordinator.device_status_text(), "离线")
        self.assertEqual(coordinator.system_input_display_text(), "麦克风 (Realtek(R) Audio)")

    def test_manual_device_check_refreshes_runtime_state_and_logs_result(self):
        snapshots = iter(
            [
                AudioDeviceStatusSnapshot(
                    current_input_name="麦克风 (Realtek(R) Audio)",
                    detected_airmic_input_name="",
                    detected_airmic_input_active=False,
                    detected_airmic_device_present=False,
                    has_any_available_input=True,
                ),
                AudioDeviceStatusSnapshot(
                    current_input_name="麦克风 (Realtek(R) Audio)",
                    detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                    detected_airmic_input_active=False,
                    detected_airmic_device_present=True,
                    has_any_available_input=True,
                ),
            ]
        )
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: [],
            audio_status_provider=lambda: next(snapshots),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="missing",
                detail="missing",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        self.assertEqual(coordinator.device_status_text(), "离线")

        coordinator.manual_check_device_status()

        self.assertEqual(coordinator.device_status_text(), "在线")
        self.assertTrue(any("手动检查设备状态" in entry.message for entry in coordinator.logs))

    def test_probe_status_parser_reads_default_input_and_airmic_activity(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))
        probe_output = "\n".join(
            [
                "DEFAULT_INPUT (Communications): 麦克风 (Realtek(R) Audio) [Active]",
                "DEFAULT_INPUT (Multimedia): 麦克风 (Realtek(R) Audio) [Active]",
                "0: 耳机 (ESP32-AirMic-HFP Hands-Free) [Unplugged]",
                "1: 麦克风 (Realtek(R) Audio) [Active]",
            ]
        )

        with mock.patch("services.backend_coordinator.Path.exists", return_value=True), mock.patch(
            "services.backend_coordinator.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout=probe_output),
        ):
            snapshot = coordinator._read_audio_device_status_from_probe()

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.current_input_name, "麦克风 (Realtek(R) Audio)")
        self.assertEqual(snapshot.detected_airmic_input_name, "耳机 (ESP32-AirMic-HFP Hands-Free)")
        self.assertFalse(snapshot.detected_airmic_input_active)
        self.assertTrue(snapshot.has_any_available_input)

    def test_serial_status_text_marks_busy_port(self):
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: ["COM10"],
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="busy",
                detail="access denied",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        coordinator.refresh_runtime_state()

        self.assertEqual(coordinator.serial_status_text(), "占用")

    def test_handle_probe_log_updates_listener_state_to_waiting(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))

        coordinator.handle_probe_event(
            {
                "event_kind": "log",
                "raw_text": "Waiting for matching WASAPI capture endpoint to become Active...",
            }
        )

        self.assertEqual(coordinator.listener_status_text(), "等待HFP")

    def test_handle_probe_log_updates_listener_state_to_monitoring(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))

        coordinator.handle_probe_event(
            {
                "event_kind": "log",
                "raw_text": "Capturing. Press Ctrl+C to stop.",
            }
        )

        self.assertEqual(coordinator.listener_status_text(), "监听中")

    def test_handle_probe_log_updates_listener_state_to_error(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))

        coordinator.handle_probe_event(
            {
                "event_kind": "log",
                "raw_text": "No audio callbacks within 4000 ms; exiting for restart.",
            }
        )

        self.assertEqual(coordinator.listener_status_text(), "异常")

    def test_handle_probe_log_marks_device_offline_immediately_when_endpoint_unplugged(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))
        coordinator.status.detected_airmic_input = "耳机 (ESP32-AirMic-HFP Hands-Free)"
        coordinator.status.detected_airmic_input_active = True
        coordinator.status.current_input_name = "耳机 (ESP32-AirMic-HFP Hands-Free)"
        coordinator.status.has_any_available_input = True

        coordinator.handle_probe_event(
            {
                "event_kind": "log",
                "raw_text": "23:59:59: 耳机 (ESP32-AirMic-HFP Hands-Free) [Unplugged]",
            }
        )

        self.assertEqual(coordinator.device_status_text(), "离线")
        self.assertEqual(coordinator.system_input_display_text(), "未选择")

    def test_audio_status_watch_event_updates_runtime_status_immediately(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))

        coordinator._handle_audio_status_watch_event_ready(
            AudioStatusWatchEvent(
                snapshot=AudioDeviceStatusSnapshot(
                    current_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                    detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                    detected_airmic_input_active=True,
                    detected_airmic_device_present=True,
                    has_any_available_input=True,
                ),
                raw_text="STATUS\tdefault_comm=耳机 (ESP32-AirMic-HFP Hands-Free)\tdefault_multi=<none>\tairmic=耳机 (ESP32-AirMic-HFP Hands-Free)\tairmic_state=Active\tany_input=true",
            )
        )

        self.assertEqual(coordinator.device_status_text(), "在线")
        self.assertEqual(coordinator.system_input_display_text(), "AirMic HFP麦克风")

    def test_device_status_shows_online_when_device_present_but_hfp_input_not_active(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))

        coordinator._handle_audio_status_watch_event_ready(
            AudioStatusWatchEvent(
                snapshot=AudioDeviceStatusSnapshot(
                    current_input_name="麦克风 (Realtek(R) Audio)",
                    detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                    detected_airmic_input_active=False,
                    detected_airmic_device_present=True,
                    has_any_available_input=True,
                ),
                raw_text="STATUS\tdefault_comm=<none>\tdefault_multi=麦克风 (Realtek(R) Audio)\tairmic=耳机 (ESP32-AirMic-HFP Hands-Free)\tairmic_state=Unplugged\tany_input=true\tbt_connected=true",
            )
        )

        self.assertEqual(coordinator.device_status_text(), "在线")
        self.assertEqual(coordinator.system_input_display_text(), "麦克风 (Realtek(R) Audio)")


    def test_device_status_stays_online_when_presence_arrives_before_friendly_name(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))

        coordinator._handle_audio_status_watch_event_ready(
            AudioStatusWatchEvent(
                snapshot=AudioDeviceStatusSnapshot(
                    current_input_name="麦克风 (Realtek(R) Audio)",
                    detected_airmic_input_name="",
                    detected_airmic_input_active=False,
                    detected_airmic_device_present=True,
                    has_any_available_input=True,
                ),
                raw_text="STATUS\tdefault_comm=<none>\tdefault_multi=麦克风 (Realtek(R) Audio)\tairmic=<none>\tairmic_state=Unplugged\tany_input=true\tdevice_present=true\tbt_connected=true",
            )
        )

        self.assertEqual(coordinator.device_status_text(), "在线")
        self.assertEqual(coordinator.system_input_display_text(), "麦克风 (Realtek(R) Audio)")

    def test_device_status_stays_offline_for_stale_hfp_endpoint_without_bt_connection(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))

        coordinator._handle_audio_status_watch_event_ready(
            AudioStatusWatchEvent(
                snapshot=AudioDeviceStatusSnapshot(
                    current_input_name="麦克风 (Realtek(R) Audio)",
                    detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                    detected_airmic_input_active=False,
                    detected_airmic_device_present=False,
                    has_any_available_input=True,
                ),
                raw_text="STATUS\tdefault_comm=<none>\tdefault_multi=麦克风 (Realtek(R) Audio)\tairmic=耳机 (ESP32-AirMic-HFP Hands-Free)\tairmic_state=Unplugged\tany_input=true\tbt_connected=false",
            )
        )

        self.assertEqual(coordinator.device_status_text(), "离线")
        self.assertEqual(coordinator.system_input_display_text(), "麦克风 (Realtek(R) Audio)")

    def test_audio_status_watch_pnp_delete_hint_marks_device_offline_immediately(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))
        coordinator.status.detected_airmic_input = "耳机 (ESP32-AirMic-HFP Hands-Free)"
        coordinator.status.detected_airmic_input_active = True
        coordinator.status.detected_airmic_device_present = True
        coordinator.status.has_any_available_input = True
        coordinator.status.current_input_name = "耳机 (ESP32-AirMic-HFP Hands-Free)"

        coordinator._handle_audio_status_watch_event_ready(
            AudioStatusWatchEvent(
                snapshot=AudioDeviceStatusSnapshot(
                    current_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                    detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                    detected_airmic_input_active=True,
                    detected_airmic_device_present=True,
                    has_any_available_input=True,
                ),
                raw_text="HINT\tkind=pnp\toperation=delete\tname=耳机 (ESP32-AirMic-HFP Hands-Free)",
                hint_kind="pnp",
                hint_operation="delete",
                hint_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
            )
        )

        self.assertEqual(coordinator.device_status_text(), "离线")
        self.assertEqual(coordinator.system_input_display_text(), "未选择")

    def test_tick_runtime_state_returns_quickly_while_refresh_runs_in_background(self):
        coordinator = BackendCoordinator(
            serial_port_provider=lambda: ["COM10"],
            audio_status_provider=lambda: (
                time.sleep(0.2) or AudioDeviceStatusSnapshot(
                    current_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                    detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                    detected_airmic_input_active=True,
                    has_any_available_input=True,
                )
            ),
            serial_status_provider=lambda port_name, available_ports: (
                time.sleep(0.2) or SerialPortStatusSnapshot(
                    port_name=port_name,
                    state="open",
                    detail="ready",
                )
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )
        coordinator.enter_debug_mode()
        self.app.processEvents()

        start = time.monotonic()
        coordinator.tick_runtime_state()
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 0.08)

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and coordinator.device_status_text() != "在线":
            self.app.processEvents()
            time.sleep(0.02)

        self.assertEqual(coordinator.device_status_text(), "在线")

    def test_refresh_device_config_reads_back_real_tuning(self):
        coordinator = BackendCoordinator(
            device_command_sender=lambda port_name, commands: DeviceCommandResult(
                ok=True,
                tuning=AudioTuningSnapshot(
                    mic_gain_q8=2048,
                    sample_shift_bits=12,
                    noise_gate=120,
                    tone_gain_q8=384,
                ),
                record_mode="ptt",
                lines=("cfg gain_q8=2048 gain=8.00x gate=120 tone_q8=384 tone=1.50x shift=12 sr=off sr_init=no",),
            )
        )

        coordinator.refresh_device_config()

        self.assertEqual(coordinator.status.tuning.mic_gain_q8, 2048)
        self.assertEqual(coordinator.status.tuning.sample_shift_bits, 12)
        self.assertEqual(coordinator.status.tuning.noise_gate, 120)
        self.assertEqual(coordinator.status.tuning.tone_gain_q8, 384)
        self.assertTrue(any("cfg gain_q8=2048" in item.message for item in coordinator.logs))

    def test_handle_probe_tone_event_updates_last_tone_and_triggers_shortcut(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )

        coordinator.handle_probe_event(
            {
                "event_kind": "tone",
                "raw_text": "PC TONE START at 5.860s score=9036.1",
                "tone_source": "TONE",
                "tone_event": "START",
            }
        )

        self.assertEqual(coordinator.status.last_tone, "TONE START")
        self.assertEqual(sent_actions, [(("right_alt",), True, "scan")])

    def test_handle_probe_start_tone_uses_custom_start_mapping(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )
        coordinator.set_custom_tone_action(TONE_SLOT_START, ("right_ctrl", "space"))
        sent_actions.clear()

        coordinator.handle_probe_event(
            {
                "event_kind": "tone",
                "raw_text": "PC TONE START at 5.860s score=9036.1",
                "tone_source": "TONE",
                "tone_event": "START",
            }
        )

        self.assertEqual(sent_actions, [(("right_ctrl", "space"), True, "scan")])

    def test_handle_probe_start_tone_single_right_ctrl_uses_right_ctrl_mode(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )
        coordinator.set_custom_tone_action(TONE_SLOT_START, ("right_ctrl",))
        sent_actions.clear()

        coordinator.handle_probe_event(
            {
                "event_kind": "tone",
                "raw_text": "PC TONE START at 5.860s score=9036.1",
                "tone_source": "TONE",
                "tone_event": "START",
            }
        )

        self.assertEqual(sent_actions, [(("right_ctrl",), True, "right_ctrl")])

    def test_handle_probe_start_tone_is_ignored_while_shortcut_recording_is_active(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )
        coordinator.set_shortcut_recording_slot("tone_a")

        coordinator.handle_probe_event(
            {
                "event_kind": "tone",
                "raw_text": "PC TONE START at 5.860s score=9036.1",
                "tone_source": "TONE",
                "tone_event": "START",
            }
        )

        self.assertEqual(sent_actions, [])

    def test_handle_probe_tone_a_event_triggers_custom_aux_shortcut(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )
        coordinator.set_custom_tone_action(TONE_SLOT_A, ("left_ctrl", "a"))
        sent_actions.clear()

        coordinator.handle_probe_event(
            {
                "event_kind": "tone",
                "raw_text": "PC TONE A at 6.120s score=4210.3",
                "tone_source": "TONE",
                "tone_event": "A",
            }
        )

        self.assertEqual(coordinator.status.last_tone, "TONE A")
        self.assertEqual(
            sent_actions,
            [
                (("left_ctrl", "a"), True, "scan"),
            ],
        )

    def test_aux_tone_is_ignored_while_start_shortcut_is_held(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )
        coordinator.set_custom_tone_action(TONE_SLOT_A, ("left_ctrl", "a"))
        sent_actions.clear()

        coordinator.simulate_tone("Start Tone")
        coordinator.simulate_tone("Tone A")

        self.assertEqual(sent_actions, [(("right_alt",), True, "scan")])
        self.assertTrue(any("已有其它按键按住" in item.message for item in coordinator.logs))

    def test_start_tone_force_releases_stale_shortcut_before_pressing_new_one(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )
        coordinator.set_custom_tone_action(TONE_SLOT_START, ("right_ctrl", "space"))
        coordinator.shortcut_service.press(("right_alt",))
        sent_actions.clear()

        coordinator.simulate_tone("Start Tone")

        self.assertEqual(
            sent_actions,
            [
                (("right_alt",), False, "scan"),
                (("right_ctrl", "space"), True, "scan"),
            ],
        )
        self.assertTrue(any("检测到残留按键" in item.message for item in coordinator.logs))

    def test_stop_tone_releases_pressed_shortcut_before_other_aux_events(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )
        coordinator.set_custom_tone_action(TONE_SLOT_A, ("left_ctrl", "a"))
        coordinator.simulate_tone("Start Tone")
        sent_actions.clear()

        coordinator.simulate_tone("Stop Tone")
        coordinator.simulate_tone("Tone A")

        self.assertEqual(
            sent_actions,
            [
                (("right_alt",), False, "scan"),
                (("left_ctrl", "a"), True, "scan"),
            ],
        )

    def test_handle_probe_aux_tone_is_ignored_while_shortcut_recording_is_active(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = BackendCoordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )
        coordinator.set_custom_tone_action(TONE_SLOT_A, ("left_ctrl", "a"))
        coordinator.set_custom_tone_action("tone_b", ("b",))
        sent_actions.clear()

        coordinator.set_shortcut_recording_slot(TONE_SLOT_A)
        coordinator.handle_probe_event(
            {
                "event_kind": "tone",
                "raw_text": "PC TONE B at 6.120s score=4210.3",
                "tone_source": "TONE",
                "tone_event": "B",
            }
        )

        self.assertEqual(sent_actions, [])
        self.assertTrue(any("录制快捷键中，已忽略 Tone B" in item.message for item in coordinator.logs))

    def test_probe_preview_logs_command(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))

        coordinator.start_probe_preview()

        self.assertIn("AirMicAudioProbe_v5.exe", coordinator.logs[-1].message)

    def test_start_probe_monitor_invokes_probe_service(self):
        coordinator = BackendCoordinator(
            project_root=Path(r"D:\FUCKIDF\AirMic\desktop-app"),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )
        started: list[str] = []

        coordinator.probe_service.start = lambda emit, emit_log=None: started.append("started") or True

        coordinator.start_probe_monitor()

        self.assertEqual(started, ["started"])
        self.assertIn("正在启动音频编码监听", coordinator.logs[-1].message)

    def test_probe_emit_path_updates_logs(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))

        coordinator._handle_probe_event_object(
            ProbeEvent(
                event_kind="log",
                raw_text="Capturing. Press Ctrl+C to stop.",
            )
        )

        self.assertIn("Capturing", coordinator.logs[-1].message)

    def test_tick_runtime_state_restarts_probe_when_probe_thread_died_but_device_still_online(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = BackendCoordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode)),
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                detected_airmic_input_active=True,
                detected_airmic_device_present=True,
                has_any_available_input=True,
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )
        coordinator.status.state = "running"
        coordinator.status.listener_phase = "monitoring"
        coordinator.shortcut_service.press(("right_alt",))

        calls: list[str] = []
        coordinator.probe_service.is_running = lambda: False  # type: ignore[method-assign]
        coordinator.stop_probe_monitor = lambda: calls.append("stop")  # type: ignore[method-assign]
        coordinator.start_probe_monitor = lambda: calls.append("start")  # type: ignore[method-assign]

        coordinator.tick_runtime_state()

        self.assertEqual(calls, ["stop", "start"])
        self.assertEqual(sent_actions[-1], (("right_alt",), False, "scan"))
        self.assertFalse(coordinator.shortcut_service.is_pressed)
        self.assertTrue(any("自动重启音频探针" in entry.message for entry in coordinator.logs))

    def test_tick_runtime_state_restarts_probe_when_monitoring_has_no_probe_events_for_too_long(self):
        coordinator = BackendCoordinator(
            audio_status_provider=lambda: AudioDeviceStatusSnapshot(
                current_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                detected_airmic_input_active=True,
                detected_airmic_device_present=True,
                has_any_available_input=True,
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )
        coordinator.status.state = "running"
        coordinator.status.listener_phase = "monitoring"
        coordinator._last_probe_event_at = time.monotonic() - 20.0
        coordinator._last_probe_recovery_at = 0.0

        calls: list[str] = []
        coordinator.probe_service.is_running = lambda: True  # type: ignore[method-assign]
        coordinator.stop_probe_monitor = lambda: calls.append("stop")  # type: ignore[method-assign]
        coordinator.start_probe_monitor = lambda: calls.append("start")  # type: ignore[method-assign]

        coordinator.tick_runtime_state()

        self.assertEqual(calls, ["stop", "start"])
        self.assertTrue(any("10 秒无探针事件" in entry.message for entry in coordinator.logs))


    def test_tick_runtime_state_force_releases_stuck_shortcut_after_idle_timeout(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = BackendCoordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode)),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )
        coordinator.status.state = "running"
        coordinator.status.listener_phase = "monitoring"
        coordinator.shortcut_service.press(("right_alt",))
        coordinator._last_probe_event_at = time.monotonic() - 3.0
        coordinator._last_shortcut_keepalive_at = time.monotonic() - 3.0

        coordinator.tick_runtime_state()

        self.assertFalse(coordinator.shortcut_service.is_pressed)
        self.assertIn((("right_alt",), False, "scan"), sent_actions)
        self.assertTrue(any("自动释放卡住的快捷键" in entry.message for entry in coordinator.logs))

    def test_ignored_aux_tone_does_not_refresh_stuck_release_timer(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )
        coordinator.set_custom_tone_action(TONE_SLOT_A, ("a",))
        coordinator.set_custom_tone_action(TONE_SLOT_B, ("b",))
        coordinator.simulate_tone("Tone A")
        sent_actions.clear()
        coordinator._last_shortcut_keepalive_at = time.monotonic() - 3.0

        coordinator.handle_probe_event(
            {
                "event_kind": "tone",
                "raw_text": "PC TONE B at 6.120s score=4210.3",
                "tone_source": "TONE",
                "tone_event": "B",
            }
        )
        coordinator.tick_runtime_state()

        self.assertFalse(coordinator.shortcut_service.is_pressed)
        self.assertIn((("a",), False, "scan"), sent_actions)

    def test_tick_runtime_state_does_not_release_recently_pressed_shortcut(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = BackendCoordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode)),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )
        coordinator.shortcut_service.press(("right_alt",))
        coordinator._last_probe_event_at = time.monotonic()
        coordinator._last_shortcut_keepalive_at = time.monotonic()

        coordinator.tick_runtime_state()

        self.assertTrue(coordinator.shortcut_service.is_pressed)

    def test_same_aux_tone_refreshes_keepalive_without_repressing_shortcut(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        coordinator = self._make_coordinator(
            shortcut_sender=lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))
        )
        coordinator.set_custom_tone_action(TONE_SLOT_B, ("right_ctrl",))
        coordinator.handle_probe_event(
            {
                "event_kind": "tone",
                "raw_text": "PC TONE B at 6.120s score=4210.3",
                "tone_source": "TONE",
                "tone_event": "B",
            }
        )
        sent_actions.clear()
        coordinator._last_shortcut_keepalive_at = time.monotonic() - 1.1

        coordinator.handle_probe_event(
            {
                "event_kind": "tone",
                "raw_text": "PC TONE B at 6.420s score=4220.1",
                "tone_source": "TONE",
                "tone_event": "B",
            }
        )
        coordinator.tick_runtime_state()

        self.assertTrue(coordinator.shortcut_service.is_pressed)
        self.assertEqual(sent_actions, [])

    def test_probe_event_object_is_marshaled_back_to_qt_thread(self):
        coordinator = self._make_coordinator()
        handled_threads: list[object] = []
        original = coordinator.handle_probe_event

        def tracked_handler(event: dict[str, object]) -> None:
            handled_threads.append(threading.current_thread())
            original(event)

        coordinator.handle_probe_event = tracked_handler  # type: ignore[method-assign]

        worker = threading.Thread(
            target=lambda: coordinator._handle_probe_event_object(
                ProbeEvent(
                    event_kind="tone",
                    raw_text="PC TONE B at 6.120s score=4210.3",
                    tone_source="TONE",
                    tone_event="B",
                )
            )
        )
        worker.start()
        worker.join()

        self.assertEqual(handled_threads, [])
        self.app.processEvents()

        self.assertEqual(len(handled_threads), 1)
        self.assertIs(handled_threads[0], threading.main_thread())
    def test_qt_subscriber_receives_status_from_background_thread(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))
        received_states: list[str] = []

        class Receiver(QObject):
            def on_status(self, status):
                received_states.append(status.state)

        receiver = Receiver()
        coordinator.subscribe(receiver.on_status)
        received_states.clear()

        worker = threading.Thread(target=coordinator.start)
        worker.start()
        worker.join()
        self.app.processEvents()

        self.assertIn("running", received_states)


if __name__ == "__main__":
    unittest.main()







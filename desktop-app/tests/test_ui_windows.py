import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from app.styles.scaling import DesignScaleContext
from app.windows.frameless import FramelessDraggableWindow
from app.windows.main_window import MainWindow
from services.backend_coordinator import BackendStatus
from core.models.app_state import AudioDeviceStatusSnapshot, DeviceCommandResult, SerialPortStatusSnapshot
from services.backend_coordinator import BackendCoordinator
from services.settings_service import SettingsService


class MainWindowUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.scale = DesignScaleContext(scale_factor=1.0, use_design_scaling=True, ui_scale_multiplier=1.5)

    def _make_window(self) -> MainWindow:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        coordinator = BackendCoordinator(
            settings_service=SettingsService(Path(temp_dir.name) / "airmic-settings.json"),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )
        window = MainWindow(scale=self.scale, coordinator=coordinator)
        self.addCleanup(window.close)
        return window

    def test_main_window_matches_console_layout_labels(self):
        window = self._make_window()

        self.assertEqual(window.windowTitle(), "AirMic 控制台")
        self.assertEqual(window.width(), 900)
        self.assertEqual(window.height(), 600)
        self.assertEqual(window.minimumWidth(), 900)
        self.assertTrue(window.windowFlags() & Qt.FramelessWindowHint)
        self.assertEqual(window.title_label.text(), "AirMic 控制台")
        self.assertEqual(window.service_status_display.title_label.text(), "监听服务")
        self.assertEqual(window.device_status_display.title_label.text(), "设备状态")
        self.assertEqual(window.system_input_display.title_label.text(), "系统输入")
        self.assertIn(window.system_input_display.value_label.objectName(), {"statusValue", "statusValueMuted"})
        self.assertFalse(window.debug_button.icon().isNull())
        self.assertFalse(window.hide_button.icon().isNull())
        self.assertTrue(window.runtime_refresh_timer.isActive())

    def test_status_row_stays_on_one_line(self):
        scale = DesignScaleContext(scale_factor=2.0, use_design_scaling=True, ui_scale_multiplier=1.5)
        window = self._make_window()
        window.show()
        self.app.processEvents()

        self.assertLessEqual(abs(window.service_status_button.y() - window.device_status_button.y()), 4)
        self.assertLessEqual(abs(window.system_input_button.y() - window.service_status_button.y()), 4)
        self.assertGreaterEqual(window.system_input_button.x(), window.device_status_button.x())

    def test_start_tone_and_aux_tones_are_all_recordable(self):
        window = self._make_window()

        self.assertEqual(window.tone_rows["start"].value_button.text(), "右 Alt")
        self.assertTrue(window.tone_rows["start"].value_button.isEnabled())
        self.assertEqual(window.tone_rows["tone_a"].value_button.text(), "点击录制")
        self.assertTrue(window.tone_rows["tone_a"].test_button.isEnabled())

    def test_recorded_shortcut_updates_tone_row_and_test_button_uses_it(self):
        sent_actions: list[tuple[tuple[str, ...], bool, str]] = []
        original_interval = MainWindow.TEST_SHORTCUT_COUNTDOWN_MS
        MainWindow.TEST_SHORTCUT_COUNTDOWN_MS = 20
        window = self._make_window()
        window.coordinator.shortcut_service.sender = lambda keys, down, mode: sent_actions.append((tuple(keys), down, mode))

        try:
            record_button = window.tone_rows["tone_a"].value_button
            assert hasattr(record_button, "recordingCompleted")
            record_button.click()
            self.app.processEvents()
            record_button.recordingCompleted.emit(("left_ctrl", "left_win"))
            self.app.processEvents()

            self.assertEqual(window.tone_rows["tone_a"].value_button.text(), "左 Ctrl + 左 Win")

            window.tone_rows["tone_a"].test_button.click()
            import time
            deadline = time.time() + 1.0
            while time.time() < deadline and not sent_actions:
                self.app.processEvents()
                time.sleep(0.02)

            self.assertEqual(
                sent_actions,
                [
                    (("left_ctrl", "left_win"), True, "scan"),
                    (("left_ctrl", "left_win"), False, "scan"),
                ],
            )
        finally:
            MainWindow.TEST_SHORTCUT_COUNTDOWN_MS = original_interval

    def test_test_button_shows_countdown_before_triggering_action(self):
        calls: list[str] = []
        original_interval = MainWindow.TEST_SHORTCUT_COUNTDOWN_MS
        MainWindow.TEST_SHORTCUT_COUNTDOWN_MS = 20
        try:
            window = MainWindow(scale=self.scale)
            self.addCleanup(window.close)
            window.coordinator.test_tone_action = lambda slot_id: calls.append(slot_id)  # type: ignore[method-assign]
            button = window.tone_rows["tone_a"].test_button

            button.click()
            self.app.processEvents()
            self.assertEqual(button.text(), "3")
            self.assertFalse(button.isEnabled())

            import time
            deadline = time.time() + 1.0
            while time.time() < deadline and not calls:
                self.app.processEvents()
                time.sleep(0.02)

            self.assertEqual(calls, ["tone_a"])
            self.assertTrue(button.isEnabled())
            self.assertEqual(button.text(), "")
            self.assertFalse(button.icon().isNull())
        finally:
            MainWindow.TEST_SHORTCUT_COUNTDOWN_MS = original_interval

    def test_record_button_captures_letter_shortcut_from_keyboard(self):
        window = self._make_window()
        window.show()
        self.app.processEvents()

        record_button = window.tone_rows["tone_a"].value_button
        record_button.click()
        self.app.processEvents()

        QTest.keyClick(record_button, Qt.Key_A)

        import time
        deadline = time.time() + 1.0
        while time.time() < deadline and window.tone_rows["tone_a"].value_button.text() != "A":
            self.app.processEvents()
            time.sleep(0.02)

        self.assertEqual(window.tone_rows["tone_a"].value_button.text(), "A")

    def test_record_button_captures_ctrl_plus_a_from_keyboard(self):
        window = self._make_window()
        window.show()
        self.app.processEvents()

        record_button = window.tone_rows["tone_a"].value_button
        record_button.click()
        self.app.processEvents()

        QTest.keyClick(record_button, Qt.Key_A, Qt.ControlModifier)

        import time
        deadline = time.time() + 1.0
        while time.time() < deadline and window.tone_rows["tone_a"].value_button.text() != "左 Ctrl + A":
            self.app.processEvents()
            time.sleep(0.02)

        self.assertEqual(window.tone_rows["tone_a"].value_button.text(), "左 Ctrl + A")

    def test_recorded_right_ctrl_shortcut_keeps_side_prefix_in_ui(self):
        window = self._make_window()
        window.show()
        self.app.processEvents()

        record_button = window.tone_rows["tone_a"].value_button
        assert hasattr(record_button, "recordingCompleted")
        record_button.click()
        self.app.processEvents()
        record_button.recordingCompleted.emit(("right_ctrl", "a"))

        import time
        deadline = time.time() + 1.0
        while time.time() < deadline and window.tone_rows["tone_a"].value_button.text() != "右 Ctrl + A":
            self.app.processEvents()
            time.sleep(0.02)

        self.assertEqual(window.tone_rows["tone_a"].value_button.text(), "右 Ctrl + A")

    def test_background_shortcut_update_is_handled_on_ui_thread(self):
        import threading
        import time

        window = self._make_window()
        window.show()
        self.app.processEvents()

        main_thread_id = threading.get_ident()
        called_threads: list[int] = []
        original = window.coordinator.set_custom_tone_action

        def capture(slot_id: str, keys: tuple[str, ...]) -> None:
            called_threads.append(threading.get_ident())
            original(slot_id, keys)

        window.coordinator.set_custom_tone_action = capture  # type: ignore[method-assign]

        record_button = window.tone_rows["tone_a"].value_button
        record_button.click()
        self.app.processEvents()

        worker = threading.Thread(target=lambda: record_button.recordingCompleted.emit(("left_ctrl", "a")))
        worker.start()
        worker.join(timeout=1.0)

        deadline = time.time() + 1.0
        while time.time() < deadline and not called_threads:
            self.app.processEvents()
            time.sleep(0.02)

        self.assertTrue(called_threads)
        self.assertEqual(called_threads[-1], main_thread_id)
        self.assertEqual(window.tone_rows["tone_a"].value_button.text(), "左 Ctrl + A")

    def test_recording_one_tone_disables_other_aux_tone_controls(self):
        window = self._make_window()
        window.show()
        self.app.processEvents()

        tone_start_button = window.tone_rows["start"].value_button
        tone_start_test = window.tone_rows["start"].test_button
        tone_a_button = window.tone_rows["tone_a"].value_button
        tone_b_button = window.tone_rows["tone_b"].value_button
        tone_c_button = window.tone_rows["tone_c"].value_button
        tone_b_test = window.tone_rows["tone_b"].test_button
        tone_c_test = window.tone_rows["tone_c"].test_button

        tone_a_button.click()
        self.app.processEvents()

        self.assertFalse(tone_start_button.isEnabled())
        self.assertFalse(tone_start_test.isEnabled())
        self.assertTrue(tone_a_button.isEnabled())
        self.assertFalse(tone_b_button.isEnabled())
        self.assertFalse(tone_c_button.isEnabled())
        self.assertFalse(tone_b_test.isEnabled())
        self.assertFalse(tone_c_test.isEnabled())

    def test_recording_start_tone_disables_aux_tone_controls(self):
        window = self._make_window()
        window.show()
        self.app.processEvents()

        tone_start_button = window.tone_rows["start"].value_button
        tone_a_button = window.tone_rows["tone_a"].value_button
        tone_b_button = window.tone_rows["tone_b"].value_button
        tone_c_button = window.tone_rows["tone_c"].value_button

        tone_start_button.click()
        self.app.processEvents()

        self.assertTrue(tone_start_button.isEnabled())
        self.assertFalse(tone_a_button.isEnabled())
        self.assertFalse(tone_b_button.isEnabled())
        self.assertFalse(tone_c_button.isEnabled())

    def test_finishing_recording_restores_other_aux_tone_controls(self):
        window = self._make_window()
        window.show()
        self.app.processEvents()

        tone_a_button = window.tone_rows["tone_a"].value_button
        tone_b_button = window.tone_rows["tone_b"].value_button
        tone_b_test = window.tone_rows["tone_b"].test_button

        tone_a_button.click()
        self.app.processEvents()
        QTest.keyClick(tone_a_button, Qt.Key_A)
        self.app.processEvents()

        self.assertTrue(tone_b_button.isEnabled())
        self.assertTrue(tone_b_test.isEnabled())

    def test_close_hides_window_instead_of_destroying_it(self):
        window = self._make_window()
        window.show()
        self.app.processEvents()

        window.hide_to_tray()
        self.app.processEvents()

        self.assertFalse(window.isVisible())
        self.assertFalse(window._quit_requested)


    def test_quit_from_tray_stops_timers_and_backend(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))
        stop_calls: list[str] = []
        leave_calls: list[str] = []
        coordinator.stop = lambda: stop_calls.append("stop")  # type: ignore[method-assign]
        coordinator.leave_debug_mode = lambda: leave_calls.append("leave")  # type: ignore[method-assign]
        window = MainWindow(scale=self.scale, coordinator=coordinator)
        self.addCleanup(window.close)

        self.assertTrue(window.runtime_refresh_timer.isActive())
        window.quit_from_tray()
        self.app.processEvents()

        self.assertFalse(window.runtime_refresh_timer.isActive())
        self.assertFalse(window.startup_probe_timer.isActive())
        self.assertEqual(stop_calls, ["stop"])
        self.assertEqual(leave_calls, ["leave"])

    def test_tray_menu_shows_status_restart_and_quit_entries(self):
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
                detail="missing",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )
        window = MainWindow(scale=self.scale, coordinator=coordinator)
        self.addCleanup(window.close)

        actions = [action.text() for action in window.tray_icon.contextMenu().actions()]

        self.assertEqual(actions, ["状态: 未启动 / 离线 / 无麦克风", "重启后台", "退出后台"])

    def test_tray_status_entry_updates_with_main_status(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))
        window = MainWindow(scale=self.scale, coordinator=coordinator)
        self.addCleanup(window.close)

        coordinator.status.listener_phase = "monitoring"
        coordinator.status.detected_airmic_input = "耳机 (ESP32-AirMic-HFP Hands-Free)"
        coordinator.status.detected_airmic_device_present = True
        coordinator.status.detected_airmic_input_active = True
        coordinator.status.current_input_name = "耳机 (ESP32-AirMic-HFP Hands-Free)"
        window._refresh_from_status(coordinator.status)

        status_action = window.tray_icon.contextMenu().actions()[0]
        self.assertEqual(status_action.text(), "状态: 监听中 / 在线 / AirMic HFP麦克风")

    def test_clicking_device_status_runs_manual_device_check(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))
        called: list[str] = []
        coordinator.manual_check_device_status = lambda: called.append("checked")  # type: ignore[method-assign]
        window = MainWindow(scale=self.scale, coordinator=coordinator)
        self.addCleanup(window.close)

        window.device_status_button.click()
        self.app.processEvents()

        self.assertEqual(called, ["checked"])

    def test_device_status_uses_clickable_status_link_button(self):
        window = self._make_window()

        self.assertTrue(window.device_status_button.property("statusLink"))
        self.assertEqual(window.device_status_button.cursor().shape(), Qt.PointingHandCursor)

    def test_tray_menu_uses_app_styling_hooks(self):
        window = self._make_window()

        menu = window.tray_icon.contextMenu()
        self.assertEqual(menu.objectName(), "trayMenu")

    def test_frameless_window_prefers_system_drag_when_available(self):
        class FakeHandle:
            def __init__(self) -> None:
                self.called = False

            def startSystemMove(self) -> bool:
                self.called = True
                return True

        window = FramelessDraggableWindow()
        self.addCleanup(window.close)
        fake_handle = FakeHandle()
        window.windowHandle = lambda: fake_handle  # type: ignore[method-assign]

        self.assertTrue(window._try_start_system_move())
        self.assertTrue(fake_handle.called)

    def test_debug_window_uses_debug_console_labels(self):
        window = self._make_window()

        debug_window = window.log_window
        self.assertEqual(debug_window.windowTitle(), "AirMic 调试台")
        self.assertEqual(debug_window.width(), 900)
        self.assertGreaterEqual(debug_window.height(), 860)
        self.assertTrue(debug_window.windowFlags() & Qt.FramelessWindowHint)
        self.assertEqual(debug_window.title_label.text(), "AirMic 调试台")
        self.assertEqual(debug_window.port_prefix_label.text(), "端口")
        self.assertEqual(debug_window.serial_status_prefix_label.text(), "设备串口状态")
        self.assertFalse(debug_window.close_button.icon().isNull())

    def test_debug_window_value_column_uses_borderless_line_edits(self):
        window = self._make_window()

        debug_window = window.log_window
        self.assertIsInstance(debug_window.gain_row.value_input, QLineEdit)
        self.assertEqual(debug_window.gain_row.value_input.objectName(), "debugValueInput")
        self.assertEqual(debug_window.gain_row.slider.maximum(), 4096)

    def test_debug_window_contains_startup_toggle(self):
        window = self._make_window()

        debug_window = window.log_window
        self.assertEqual(debug_window.startup_checkbox.text(), "开机启动")
        self.assertFalse(debug_window.startup_checkbox.isChecked())

    def test_debug_slider_sits_close_to_value_input(self):
        window = self._make_window()
        debug_window = window.log_window
        debug_window.show()
        self.app.processEvents()

        gap = debug_window.gain_row.value_input.x() - (
            debug_window.gain_row.slider.x() + debug_window.gain_row.slider.width()
        )
        self.assertLessEqual(gap, 16)

    def test_debug_value_input_uses_fixed_narrow_width(self):
        window = self._make_window()
        debug_window = window.log_window

        self.assertLessEqual(debug_window.gain_row.value_input.maximumWidth(), 80)

    def test_main_window_starts_probe_monitor_on_launch_without_reading_device_config(self):
        coordinator = BackendCoordinator(device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"))
        started: list[str] = []
        refreshed: list[str] = []
        coordinator.start_probe_monitor = lambda: started.append("started")
        coordinator.refresh_device_config = lambda: refreshed.append("refresh")

        window = MainWindow(scale=self.scale, coordinator=coordinator)
        self.addCleanup(window.close)
        self.app.processEvents()

        self.assertEqual(started, ["started"])
        self.assertEqual(refreshed, [])

    def test_opening_debug_window_enables_serial_runtime(self):
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
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=True),
        )
        config_reads: list[str] = []
        coordinator.refresh_device_config = lambda: config_reads.append("refresh")
        window = MainWindow(scale=self.scale, coordinator=coordinator)
        self.addCleanup(window.close)

        self.assertEqual(coordinator.command_ports, [])

        window._show_log_window()
        self.app.processEvents()

        self.assertEqual([item.device_id for item in coordinator.command_ports], ["COM10"])
        self.assertEqual(config_reads, ["refresh"])

    def test_closing_debug_window_releases_serial_runtime(self):
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
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=True),
        )
        window = MainWindow(scale=self.scale, coordinator=coordinator)
        self.addCleanup(window.close)

        window._show_log_window()
        self.app.processEvents()
        self.assertEqual([item.device_id for item in coordinator.command_ports], ["COM10"])

        window.log_window.close()
        self.app.processEvents()

        self.assertEqual(coordinator.command_ports, [])
        self.assertEqual(coordinator.serial_status_text(), "未连接")

    def test_hiding_debug_window_releases_serial_runtime(self):
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
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=True),
        )
        window = MainWindow(scale=self.scale, coordinator=coordinator)
        self.addCleanup(window.close)

        window._show_log_window()
        self.app.processEvents()
        self.assertEqual([item.device_id for item in coordinator.command_ports], ["COM10"])

        window.log_window.hide()
        self.app.processEvents()

        self.assertEqual(coordinator.command_ports, [])
        self.assertEqual(coordinator.serial_status_text(), "未连接")

    def test_runtime_timer_refreshes_status_without_user_click(self):
        audio_snapshots = iter(
            [
                AudioDeviceStatusSnapshot(
                    current_input_name="麦克风 (Realtek(R) Audio)",
                    detected_airmic_input_name="",
                    detected_airmic_input_active=False,
                ),
                AudioDeviceStatusSnapshot(
                    current_input_name="麦克风 (Realtek(R) Audio)",
                    detected_airmic_input_name="",
                    detected_airmic_input_active=False,
                ),
                AudioDeviceStatusSnapshot(
                    current_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                    detected_airmic_input_name="耳机 (ESP32-AirMic-HFP Hands-Free)",
                    detected_airmic_input_active=True,
                ),
            ]
        )
        port_snapshots = iter([[], [], ["COM10"]])

        coordinator = BackendCoordinator(
            serial_port_provider=lambda: next(port_snapshots),
            audio_status_provider=lambda: next(audio_snapshots),
            serial_status_provider=lambda port_name, available_ports: SerialPortStatusSnapshot(
                port_name=port_name,
                state="open" if available_ports else "missing",
                detail="ready" if available_ports else "missing",
            ),
            device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
        )

        original_interval = MainWindow.RUNTIME_REFRESH_MS
        MainWindow.RUNTIME_REFRESH_MS = 50
        try:
            window = MainWindow(scale=self.scale, coordinator=coordinator)
            self.addCleanup(window.close)
            window.show()
            self.app.processEvents()

            self.assertEqual(window.device_status_display.value_label.text(), "离线")

            import time
            deadline = time.time() + 1.0
            while time.time() < deadline and window.device_status_display.value_label.text() != "在线":
                self.app.processEvents()
                time.sleep(0.02)

            self.assertEqual(window.device_status_display.value_label.text(), "在线")
            self.assertEqual(window.system_input_display.value_label.text(), "AirMic HFP麦克风")
        finally:
            MainWindow.RUNTIME_REFRESH_MS = original_interval

    def test_inactive_status_values_use_muted_style(self):
        window = self._make_window()

        window.coordinator.status = BackendStatus(
            listener_phase="error",
            detected_airmic_input="",
            detected_airmic_input_active=False,
            current_input_name="",
            serial_port_state="missing",
        )
        window._refresh_from_status(window.coordinator.status)

        self.assertEqual(window.device_status_display.value_label.objectName(), "statusValueMuted")
        self.assertEqual(window.system_input_display.value_label.objectName(), "statusValueMuted")


if __name__ == "__main__":
    unittest.main()

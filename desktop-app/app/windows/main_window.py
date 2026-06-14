from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCursor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMainWindow,
    QPushButton,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.models.app_state import TONE_SLOT_A, TONE_SLOT_B, TONE_SLOT_C, TONE_SLOT_START
from app.runtime_paths import app_asset_path, app_icon_path
from app.styles.scaling import DesignScaleContext
from app.widgets.shortcut_recorder import ShortcutRecorderButton
from app.windows.frameless import FramelessDraggableWindow
from services.backend_coordinator import BackendCoordinator, BackendStatus
from app.windows.log_window import LogWindow


@dataclass
class ToneRowWidgets:
    name_label: QLabel
    value_button: QPushButton
    test_button: QToolButton


class StatusDisplay(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None, spacing: int = 4) -> None:
        super().__init__(parent)
        self.title = title
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(spacing)
        layout.setAlignment(Qt.AlignVCenter)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("statusTitle")
        self.value_label = QLabel("", self)
        self.value_label.setObjectName("statusValue")

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_value_text(self, text: str) -> None:
        self.value_label.setText(text)

    def set_value_active(self, active: bool) -> None:
        self.value_label.setObjectName("statusValue" if active else "statusValueMuted")
        self.value_label.style().unpolish(self.value_label)
        self.value_label.style().polish(self.value_label)


class MainWindow(FramelessDraggableWindow):
    RUNTIME_REFRESH_MS = 1500
    TEST_SHORTCUT_COUNTDOWN_MS = 1000

    def __init__(self, scale: DesignScaleContext | None = None, coordinator: BackendCoordinator | None = None) -> None:
        super().__init__()
        self.scale = scale or DesignScaleContext()
        self.coordinator = coordinator or BackendCoordinator()
        self.log_window = LogWindow(self.coordinator, scale=self.scale)
        self.tone_rows: dict[str, ToneRowWidgets] = {}
        self._test_countdown_timers: dict[str, QTimer] = {}
        self._test_countdown_remaining: dict[str, int] = {}
        self._recording_slot: str | None = None
        self._gear_icon = QIcon(str(app_asset_path("icons", "gear-six.svg")))
        self._close_icon = QIcon(str(app_asset_path("icons", "x.svg")))
        self._play_icon = QIcon(str(app_asset_path("icons", "play-circle.svg")))
        self._app_icon = QIcon(str(app_icon_path()))
        self._quit_requested = False
        self._tray_message_shown = False
        if not self._app_icon.isNull():
            self.setWindowIcon(self._app_icon)
        self.tray_icon = self._create_tray_icon()
        self.runtime_refresh_timer = QTimer(self)
        self.runtime_refresh_timer.setInterval(self.RUNTIME_REFRESH_MS)
        self.runtime_refresh_timer.timeout.connect(self.coordinator.tick_runtime_state)
        self.startup_probe_timer = QTimer(self)
        self.startup_probe_timer.setSingleShot(True)
        self.startup_probe_timer.timeout.connect(self._finish_startup_probe)

        self.setWindowTitle("AirMic 控制台")
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        main_width = self.scale.scale_design_px(600)
        main_height = self.scale.scale_design_px(400)
        self.resize(main_width, main_height)
        self.setMinimumSize(main_width, main_height)

        frame_margin = self.scale.scale_value(14)

        root = QWidget(self)
        root.setObjectName("mainRoot")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(frame_margin, frame_margin, frame_margin, frame_margin)
        outer.setSpacing(0)

        self.console_card = QFrame(root)
        self.console_card.setObjectName("consoleCard")
        card_layout = QVBoxLayout(self.console_card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        card_layout.addWidget(self._build_header())
        card_layout.addWidget(self._build_status_row())
        card_layout.addWidget(self._make_divider())
        card_layout.addWidget(self._build_tone_row("Start Tone", TONE_SLOT_START, fixed=True))
        card_layout.addWidget(self._make_divider())
        card_layout.addWidget(self._build_tone_row("A Tone", TONE_SLOT_A))
        card_layout.addWidget(self._make_divider())
        card_layout.addWidget(self._build_tone_row("B Tone", TONE_SLOT_B))
        card_layout.addWidget(self._make_divider())
        card_layout.addWidget(self._build_tone_row("C Tone", TONE_SLOT_C))

        outer.addWidget(self.console_card, 1)
        self.setCentralWidget(root)
        self.apply_window_chrome(
            self.console_card,
            blur_radius=self.scale.scale_value(30),
            y_offset=self.scale.scale_value(8),
            alpha=52,
        )

        self.coordinator.subscribe(self._refresh_from_status)
        self.coordinator.start()
        self.runtime_refresh_timer.start()
        self.startup_probe_timer.start(0)

    def _build_header(self) -> QWidget:
        wrapper = QWidget(self.console_card)
        wrapper.setProperty("dragHandle", True)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(
            self.scale.scale_value(22),
            self.scale.scale_value(18),
            self.scale.scale_value(16),
            self.scale.scale_value(10),
        )
        layout.setSpacing(self.scale.scale_value(10))

        self.title_label = QLabel("AirMic 控制台", wrapper)
        self.title_label.setObjectName("consoleTitle")

        self.debug_button = QToolButton(wrapper)
        self.debug_button.setIcon(self._gear_icon)
        self.debug_button.setIconSize(self.debug_button.iconSize().expandedTo(self.debug_button.sizeHint()))
        self.debug_button.setCursor(Qt.PointingHandCursor)
        self.debug_button.setAutoRaise(True)
        self.debug_button.setObjectName("debugButton")
        self.debug_button.setProperty("noWindowDrag", True)
        self.debug_button.setIconSize(QSize(self.scale.scale_value(16), self.scale.scale_value(16)))
        self.debug_button.clicked.connect(self._show_log_window)

        self.hide_button = QToolButton(wrapper)
        self.hide_button.setIcon(self._close_icon)
        self.hide_button.setCursor(Qt.PointingHandCursor)
        self.hide_button.setAutoRaise(True)
        self.hide_button.setObjectName("closeButton")
        self.hide_button.setProperty("noWindowDrag", True)
        self.hide_button.setIconSize(QSize(self.scale.scale_value(16), self.scale.scale_value(16)))
        self.hide_button.clicked.connect(self.hide_to_tray)

        layout.addWidget(self.title_label)
        layout.addStretch(1)
        layout.addWidget(self.debug_button, 0, Qt.AlignTop)
        layout.addWidget(self.hide_button, 0, Qt.AlignTop)
        return wrapper

    def _build_status_row(self) -> QWidget:
        wrapper = QWidget(self.console_card)
        wrapper.setProperty("dragHandle", True)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(
            self.scale.scale_value(22),
            self.scale.scale_value(4),
            self.scale.scale_value(22),
            self.scale.scale_value(12),
        )
        layout.setSpacing(self.scale.scale_value(14))

        self.service_status_button = QPushButton(wrapper)
        self.service_status_button.setFlat(True)
        self.service_status_button.setCursor(Qt.PointingHandCursor)
        self.service_status_button.setProperty("statusLink", True)
        self.service_status_button.setProperty("noWindowDrag", True)
        self.service_status_button.setMinimumWidth(self.scale.scale_value(140))
        self.service_status_button.setMinimumHeight(self.scale.scale_value(32))
        self.service_status_button.clicked.connect(self.coordinator.restart)
        self.service_status_display = StatusDisplay(
            "监听服务",
            self.service_status_button,
            spacing=self.scale.scale_value(6),
        )
        service_layout = QHBoxLayout(self.service_status_button)
        service_layout.setContentsMargins(0, 0, 0, 0)
        service_layout.addWidget(self.service_status_display)

        self.device_status_button = QPushButton(wrapper)
        self.device_status_button.setFlat(True)
        self.device_status_button.setCursor(Qt.PointingHandCursor)
        self.device_status_button.setProperty("statusLink", True)
        self.device_status_button.setProperty("noWindowDrag", True)
        self.device_status_button.setMinimumWidth(self.scale.scale_value(140))
        self.device_status_button.setMinimumHeight(self.scale.scale_value(32))
        self.device_status_button.clicked.connect(self.coordinator.manual_check_device_status)
        self.device_status_display = StatusDisplay(
            "设备状态",
            self.device_status_button,
            spacing=self.scale.scale_value(6),
        )
        device_layout = QHBoxLayout(self.device_status_button)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setAlignment(Qt.AlignVCenter)
        device_layout.addWidget(self.device_status_display)

        self.system_input_button = QPushButton(wrapper)
        self.system_input_button.setFlat(True)
        self.system_input_button.setCursor(Qt.PointingHandCursor)
        self.system_input_button.setProperty("statusLink", True)
        self.system_input_button.setProperty("noWindowDrag", True)
        self.system_input_button.setMinimumWidth(self.scale.scale_value(190))
        self.system_input_button.setMinimumHeight(self.scale.scale_value(32))
        self.system_input_button.clicked.connect(self.coordinator.open_recording_panel)
        self.system_input_display = StatusDisplay(
            "系统输入",
            self.system_input_button,
            spacing=self.scale.scale_value(6),
        )
        system_layout = QHBoxLayout(self.system_input_button)
        system_layout.setContentsMargins(0, 0, 0, 0)
        system_layout.setAlignment(Qt.AlignVCenter)
        system_layout.addWidget(self.system_input_display)

        layout.addWidget(self.service_status_button, 0)
        layout.addWidget(self.device_status_button, 0)
        layout.addWidget(self.system_input_button, 0)
        layout.addStretch(1)
        return wrapper

    def _build_tone_row(self, title: str, slot_id: str, fixed: bool = False) -> QWidget:
        wrapper = QWidget(self.console_card)
        wrapper.setProperty("dragHandle", True)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(
            self.scale.scale_value(22),
            self.scale.scale_value(12),
            self.scale.scale_value(14),
            self.scale.scale_value(12),
        )
        layout.setSpacing(self.scale.scale_value(10))

        name_label = QLabel(title, wrapper)
        name_label.setObjectName("toneName")
        name_label.setMinimumWidth(self.scale.scale_value(92))

        if fixed:
            value_button = QPushButton("right Alt", wrapper)
            value_button.setEnabled(False)
            value_button.setCursor(QCursor(Qt.ArrowCursor))
            value_button.setFlat(True)
            value_button.setObjectName("toneValueStatic")
            value_button.setProperty("noWindowDrag", True)
        else:
            value_button = ShortcutRecorderButton(wrapper)
            value_button.setObjectName("toneRecordButton")
            value_button.setProperty("noWindowDrag", True)
            value_button.shortcutRecorded.connect(
                lambda keys, tone_slot=slot_id: self.coordinator.set_custom_tone_action(tone_slot, tuple(keys)),
                Qt.QueuedConnection,
            )
            value_button.recordingStateChanged.connect(
                lambda recording, tone_slot=slot_id: self._handle_recording_state_changed(tone_slot, recording),
                Qt.QueuedConnection,
            )

        test_button = QToolButton(wrapper)
        test_button.setCursor(Qt.PointingHandCursor)
        test_button.setAutoRaise(True)
        test_button.setObjectName("toneTestButton")
        test_button.setProperty("noWindowDrag", True)
        test_button.setIcon(self._play_icon)
        test_button.setIconSize(QSize(self.scale.scale_value(16), self.scale.scale_value(16)))
        test_button.clicked.connect(lambda _checked=False, tone_slot=slot_id: self._start_test_shortcut_countdown(tone_slot))

        layout.addWidget(name_label)
        layout.addWidget(value_button, 1)
        layout.addWidget(test_button)

        self.tone_rows[slot_id] = ToneRowWidgets(name_label=name_label, value_button=value_button, test_button=test_button)
        return wrapper

    def _make_divider(self) -> QFrame:
        line = QFrame(self.console_card)
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("consoleDivider")
        return line

    def _show_log_window(self) -> None:
        self.coordinator.enter_debug_mode()
        self.coordinator.refresh_device_config()
        self.coordinator.open_log_window()
        self.log_window.show()
        self.log_window.raise_()
        self.log_window.activateWindow()

    def _finish_startup_probe(self) -> None:
        self.coordinator.start_probe_monitor()

    def _create_tray_icon(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(self)
        tray_icon = self.windowIcon()
        if tray_icon.isNull():
            tray_icon = self._app_icon if not self._app_icon.isNull() else self._gear_icon
        tray.setIcon(tray_icon)
        tray.setToolTip("AirMic 控制台")

        menu = QMenu()
        menu.setObjectName("trayMenu")
        self.tray_status_action = QAction("状态: 未启动 / 离线 / 无麦克风", menu)
        self.tray_status_action.setEnabled(False)
        menu.addAction(self.tray_status_action)
        self.tray_restart_action = QAction("重启后台", menu)
        self.tray_restart_action.triggered.connect(self._restart_from_tray)
        menu.addAction(self.tray_restart_action)
        self.tray_quit_action = QAction("退出后台", menu)
        self.tray_quit_action.triggered.connect(self.quit_from_tray)
        menu.addAction(self.tray_quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._handle_tray_activated)
        tray.show()
        return tray

    def _handle_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.show_from_tray()

    def _restart_from_tray(self) -> None:
        self.coordinator.restart()
        self.runtime_refresh_timer.start()
        self.show_from_tray()

    def hide_to_tray(self) -> None:
        self.hide()
        if self.log_window.isVisible():
            self.log_window.hide()
        if not self._tray_message_shown and self.tray_icon.supportsMessages():
            self.tray_icon.showMessage(
                "AirMic 仍在后台监听",
                "控制台已隐藏到系统托盘，监听服务会继续运行。",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
            self._tray_message_shown = True

    def show_from_tray(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def quit_from_tray(self) -> None:
        self._quit_requested = True
        self.runtime_refresh_timer.stop()
        self.startup_probe_timer.stop()
        self.tray_icon.hide()
        self.log_window.close()
        self.coordinator.stop()
        app = QApplication.instance()
        self.close()
        if app is not None:
            QTimer.singleShot(0, app.quit)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._quit_requested:
            super().closeEvent(event)
            return
        event.ignore()
        self.hide_to_tray()

    def _refresh_from_status(self, status: BackendStatus) -> None:
        listener_text = self.coordinator.listener_status_text()
        device_text = self.coordinator.device_status_text()
        system_input_text = self.coordinator.system_input_display_text()

        self.service_status_display.set_value_text(listener_text)
        self.service_status_display.set_value_active(listener_text not in {"未启动", "异常"})
        self.device_status_display.set_value_text(device_text)
        self.device_status_display.set_value_active(self.coordinator.has_active_device_status())
        self.system_input_display.set_value_text(system_input_text)
        self.system_input_display.set_value_active(self.coordinator.has_active_system_input())
        self.tray_status_action.setText(f"状态: {listener_text} / {device_text} / {system_input_text}")

        for slot_id, row in self.tone_rows.items():
            display_text = self.coordinator.tone_action_display_text(slot_id)
            if isinstance(row.value_button, ShortcutRecorderButton):
                if status.tone_action_map.get(slot_id) == "custom":
                    keys = tuple(status.custom_tone_action_map.get(slot_id, ()))
                else:
                    keys = ()
                row.value_button.set_shortcut(keys, display_text)
            else:
                row.value_button.setText(display_text)
        self._apply_recording_lock_state()

    def _handle_recording_state_changed(self, slot_id: str, recording: bool) -> None:
        if recording:
            self._recording_slot = slot_id
            self.coordinator.set_shortcut_recording_slot(slot_id)
        elif self._recording_slot == slot_id:
            self._recording_slot = None
            self.coordinator.set_shortcut_recording_slot(None)
        self._apply_recording_lock_state()

    def _apply_recording_lock_state(self) -> None:
        active_slot = self._recording_slot
        for slot_id, row in self.tone_rows.items():
            if slot_id == TONE_SLOT_START:
                row.test_button.setEnabled(active_slot is None)
                continue
            is_active_row = active_slot == slot_id
            allow_row = active_slot is None or is_active_row
            row.test_button.setEnabled(allow_row and self._test_countdown_remaining.get(slot_id, 0) == 0)
            if isinstance(row.value_button, ShortcutRecorderButton):
                row.value_button.setEnabled(allow_row)

    def _start_test_shortcut_countdown(self, slot_id: str) -> None:
        if self._recording_slot and self._recording_slot != slot_id:
            return
        button = self.tone_rows[slot_id].test_button
        if self._test_countdown_remaining.get(slot_id, 0) > 0:
            return
        self._test_countdown_remaining[slot_id] = 3
        button.setEnabled(False)
        button.setIcon(QIcon())
        button.setText("3")
        timer = self._test_countdown_timers.get(slot_id)
        if timer is None:
            timer = QTimer(self)
            timer.timeout.connect(lambda tone_slot=slot_id: self._advance_test_shortcut_countdown(tone_slot))
            self._test_countdown_timers[slot_id] = timer
        timer.start(self.TEST_SHORTCUT_COUNTDOWN_MS)

    def _advance_test_shortcut_countdown(self, slot_id: str) -> None:
        remaining = self._test_countdown_remaining.get(slot_id, 0) - 1
        button = self.tone_rows[slot_id].test_button
        if remaining > 0:
            self._test_countdown_remaining[slot_id] = remaining
            button.setText(str(remaining))
            return

        timer = self._test_countdown_timers.get(slot_id)
        if timer is not None:
            timer.stop()
        self._test_countdown_remaining[slot_id] = 0
        button.setEnabled(True)
        button.setText("")
        button.setIcon(self._play_icon)
        self.coordinator.test_tone_action(slot_id)
        self._apply_recording_lock_state()

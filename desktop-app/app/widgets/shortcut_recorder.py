from __future__ import annotations

from PySide6.QtCore import Qt, Signal, Qt as QtCoreQt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QPushButton, QWidget

from services.global_shortcut_recorder import GlobalShortcutRecorder, ShortcutRecordingState


class ShortcutRecorderButton(QPushButton):
    shortcutRecorded = Signal(tuple)
    recordingStateChanged = Signal(bool)
    recordingCompleted = Signal(tuple)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("点击录制", parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("recording", False)
        self.setFlat(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._recording = False
        self._shortcut_keys: tuple[str, ...] = ()
        self._display_text = "点击录制"
        self._recorder = GlobalShortcutRecorder(self.recordingCompleted.emit)
        self._recording_state = ShortcutRecordingState()

        self.clicked.connect(self._handle_click)
        self.recordingCompleted.connect(self._finish_recording, QtCoreQt.QueuedConnection)

    @property
    def shortcut_keys(self) -> tuple[str, ...]:
        return self._shortcut_keys

    def set_display_text(self, text: str) -> None:
        self._display_text = text
        if not self._recording:
            self.setText(text)

    def set_shortcut(self, keys: tuple[str, ...], text: str) -> None:
        self._shortcut_keys = tuple(keys)
        self.set_display_text(text)

    def cancel_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self._recorder.stop()
        self._recording_state = ShortcutRecordingState()
        self._apply_recording_style(False)
        self.recordingStateChanged.emit(False)
        self.setText(self._display_text)

    def _handle_click(self) -> None:
        if self._recording:
            self.cancel_recording()
            return
        self._recording = True
        self._recorder.reset()
        self._recording_state = ShortcutRecordingState()
        self._apply_recording_style(True)
        self.recordingStateChanged.emit(True)
        self.setText("请按下组合键...")
        self.setFocus(Qt.MouseFocusReason)
        started = self._recorder.start()
        if not started:
            self._recording = False
            self._apply_recording_style(False)
            self.recordingStateChanged.emit(False)
            self.setText("录制器已在运行")

    def _finish_recording(self, recorded: tuple[str, ...]) -> None:
        if not self._recording:
            return
        self._recording = False
        self._shortcut_keys = tuple(recorded)
        self._recorder.stop()
        self._recording_state = ShortcutRecordingState()
        self._apply_recording_style(False)
        self.recordingStateChanged.emit(False)
        self.set_display_text(self._format_shortcut_text(recorded))
        self.shortcutRecorded.emit(recorded)

    def _apply_recording_style(self, recording: bool) -> None:
        self.setProperty("recording", recording)
        self.style().unpolish(self)
        self.style().polish(self)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return

        key_id = self._map_qt_key_event(event)
        if key_id:
            recorded = self._recording_state.handle_keydown(key_id)
            if recorded:
                self._finish_recording(recorded)
            event.accept()
            return

        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if not self._recording:
            super().keyReleaseEvent(event)
            return

        key_id = self._map_qt_key_event(event)
        if key_id:
            recorded = self._recording_state.handle_keyup(key_id)
            if recorded:
                self._finish_recording(recorded)
            event.accept()
            return

        event.accept()

    def _map_qt_key_event(self, event: QKeyEvent) -> str | None:
        key = event.key()
        if key == Qt.Key_Control:
            return "left_ctrl"
        if key == Qt.Key_Alt:
            return "left_alt"
        if key == Qt.Key_Shift:
            return "shift"
        if key == Qt.Key_Meta:
            return "left_win"
        if key == Qt.Key_Space:
            return "space"
        if key == Qt.Key_Return or key == Qt.Key_Enter:
            return "enter"
        if key == Qt.Key_Backspace:
            return "backspace"
        if key == Qt.Key_Tab:
            return "tab"
        if Qt.Key_A <= key <= Qt.Key_Z:
            return chr(ord("a") + (key - Qt.Key_A))
        if Qt.Key_0 <= key <= Qt.Key_9:
            return chr(ord("0") + (key - Qt.Key_0))
        return None

    def _format_shortcut_text(self, keys: tuple[str, ...]) -> str:
        label_map = {
            "left_ctrl": "Ctrl",
            "right_ctrl": "Ctrl",
            "left_alt": "Alt",
            "right_alt": "Alt",
            "left_win": "Win",
            "right_win": "Win",
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
        return "+".join(parts) if parts else "点击录制"

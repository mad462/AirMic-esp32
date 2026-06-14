from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStyleFactory,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.models.app_state import AudioTuningSnapshot
from app.runtime_paths import app_asset_path, app_icon_path
from app.styles.scaling import DesignScaleContext
from app.windows.frameless import FramelessDraggableWindow
from services.backend_coordinator import BackendCoordinator, BackendStatus


class ValueSliderRow(QWidget):
    def __init__(
        self,
        label_text: str,
        minimum: int,
        maximum: int,
        step: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.label = QLabel(label_text, self)
        self.label.setObjectName("debugFieldLabel")

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setMinimum(minimum)
        self.slider.setMaximum(maximum)
        self.slider.setSingleStep(step)
        self.slider.setPageStep(step)
        self.slider.setStyle(QStyleFactory.create("Fusion"))
        self.slider.setObjectName("debugSlider")
        self.slider.setProperty("noWindowDrag", True)

        self.value_input = QLineEdit(self)
        self.value_input.setObjectName("debugValueInput")
        value_width = 72
        self.value_input.setFixedWidth(value_width)
        self.value_input.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.value_input.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_input.setProperty("noWindowDrag", True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.label)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value_input)

    def set_value(self, value: int, display_text: str) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.value_input.setText(display_text)


class LogWindow(FramelessDraggableWindow):
    def __init__(self, coordinator: BackendCoordinator, scale: DesignScaleContext | None = None) -> None:
        super().__init__()
        self.scale = scale or DesignScaleContext()
        self.coordinator = coordinator
        self._close_icon = QIcon(str(app_asset_path("icons", "x.svg")))
        self._app_icon = QIcon(str(app_icon_path()))
        if not self._app_icon.isNull():
            self.setWindowIcon(self._app_icon)
        self.setWindowTitle("AirMic 调试台")
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        debug_width = self.scale.scale_design_px(600)
        debug_height = self.scale.scale_design_px(900)
        debug_min_height = self.scale.scale_design_px(860)
        self.resize(debug_width, debug_height)
        self.setMinimumSize(debug_width, debug_min_height)

        frame_margin = self.scale.scale_value(14)

        root = QWidget(self)
        root.setObjectName("debugRoot")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(frame_margin, frame_margin, frame_margin, frame_margin)
        outer.setSpacing(0)

        self.debug_card = QFrame(root)
        self.debug_card.setObjectName("debugCard")
        layout = QVBoxLayout(self.debug_card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_serial_row())
        layout.addWidget(self._make_divider())
        layout.addWidget(self._build_debug_content(), 1)

        outer.addWidget(self.debug_card, 1)
        self.setCentralWidget(root)
        self.apply_window_chrome(
            self.debug_card,
            blur_radius=self.scale.scale_value(30),
            y_offset=self.scale.scale_value(8),
            alpha=52,
        )

        self.coordinator.subscribe(self._refresh_from_status)

    def _build_header(self) -> QWidget:
        wrapper = QWidget(self.debug_card)
        wrapper.setProperty("dragHandle", True)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(
            self.scale.scale_value(18),
            self.scale.scale_value(14),
            self.scale.scale_value(10),
            self.scale.scale_value(10),
        )
        layout.setSpacing(self.scale.scale_value(6))

        self.title_label = QLabel("AirMic 调试台", wrapper)
        self.title_label.setObjectName("debugTitle")

        self.close_button = QToolButton(wrapper)
        self.close_button.setIcon(self._close_icon)
        self.close_button.setIconSize(self.close_button.iconSize().expandedTo(self.close_button.sizeHint()))
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setAutoRaise(True)
        self.close_button.setObjectName("closeButton")
        self.close_button.setProperty("noWindowDrag", True)
        self.close_button.setIconSize(QSize(self.scale.scale_value(16), self.scale.scale_value(16)))
        self.close_button.clicked.connect(self.close)

        layout.addWidget(self.title_label)
        layout.addStretch(1)
        layout.addWidget(self.close_button, 0, Qt.AlignTop)
        return wrapper

    def _build_serial_row(self) -> QWidget:
        wrapper = QWidget(self.debug_card)
        wrapper.setProperty("dragHandle", True)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(
            self.scale.scale_value(18),
            0,
            self.scale.scale_value(18),
            self.scale.scale_value(8),
        )
        layout.setSpacing(self.scale.scale_value(6))

        self.port_prefix_label = QLabel("端口", wrapper)
        self.port_prefix_label.setObjectName("debugMetaLabel")

        self.port_combo = QComboBox(wrapper)
        self.port_combo.setObjectName("debugPortCombo")
        self.port_combo.setProperty("noWindowDrag", True)
        for item in self.coordinator.command_ports:
            self.port_combo.addItem(item.device_id, item.device_id)
        self.port_combo.currentIndexChanged.connect(
            lambda: self.coordinator.set_command_port(self.port_combo.currentData())
        )

        self.serial_status_prefix_label = QLabel("设备串口状态", wrapper)
        self.serial_status_prefix_label.setObjectName("debugMetaLabel")

        self.serial_status_value_label = QLabel("", wrapper)
        self.serial_status_value_label.setObjectName("debugMetaValue")

        self.startup_checkbox = QCheckBox("开机启动", wrapper)
        self.startup_checkbox.setObjectName("debugStartupCheckbox")
        self.startup_checkbox.setProperty("noWindowDrag", True)
        self.startup_checkbox.toggled.connect(self.coordinator.set_startup_enabled)

        layout.addWidget(self.port_prefix_label)
        layout.addWidget(self.port_combo)
        layout.addWidget(self.serial_status_prefix_label)
        layout.addWidget(self.serial_status_value_label)
        layout.addWidget(self.startup_checkbox)
        layout.addStretch(1)
        return wrapper

    def _build_debug_content(self) -> QWidget:
        wrapper = QWidget(self.debug_card)
        wrapper.setProperty("dragHandle", True)
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(
            self.scale.scale_value(18),
            self.scale.scale_value(10),
            self.scale.scale_value(18),
            self.scale.scale_value(12),
        )
        layout.setSpacing(self.scale.scale_value(10))

        section_title = QLabel("麦克风设置", wrapper)
        section_title.setObjectName("debugSectionTitle")
        layout.addWidget(section_title)

        self.gain_row = ValueSliderRow("麦克增益", 256, 4096, 64, wrapper)
        self.shift_row = ValueSliderRow("采样右移", 8, 16, 1, wrapper)
        self.gate_row = ValueSliderRow("噪声门限", 0, 4000, 10, wrapper)
        self.tone_row = ValueSliderRow("编码音量", 64, 768, 8, wrapper)

        for row in (self.gain_row, self.shift_row, self.gate_row, self.tone_row):
            layout.addWidget(row)

        self.gain_row.slider.valueChanged.connect(self._emit_tuning_preview)
        self.shift_row.slider.valueChanged.connect(self._emit_tuning_preview)
        self.gate_row.slider.valueChanged.connect(self._emit_tuning_preview)
        self.tone_row.slider.valueChanged.connect(self._emit_tuning_preview)
        self.gain_row.value_input.editingFinished.connect(lambda: self._apply_text_input(self.gain_row, "gain"))
        self.shift_row.value_input.editingFinished.connect(lambda: self._apply_text_input(self.shift_row, "shift"))
        self.gate_row.value_input.editingFinished.connect(lambda: self._apply_text_input(self.gate_row, "gate"))
        self.tone_row.value_input.editingFinished.connect(lambda: self._apply_text_input(self.tone_row, "tone"))

        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        self.send_button = QPushButton("发送到设备", wrapper)
        self.send_button.setObjectName("primaryButton")
        self.send_button.setProperty("noWindowDrag", True)
        self.send_button.clicked.connect(self._send_tuning)

        self.reset_button = QPushButton("默认参数", wrapper)
        self.reset_button.setObjectName("secondaryButton")
        self.reset_button.setProperty("noWindowDrag", True)
        self.reset_button.clicked.connect(self._reset_tuning_defaults)

        button_row.addWidget(self.send_button)
        button_row.addWidget(self.reset_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.log_view = QPlainTextEdit(wrapper)
        self.log_view.setObjectName("debugLogView")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.log_view.setProperty("noWindowDrag", True)
        layout.addWidget(self.log_view, 1)

        return wrapper

    def _make_divider(self) -> QFrame:
        line = QFrame(self.debug_card)
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("debugDivider")
        return line

    def _current_tuning_from_controls(self) -> AudioTuningSnapshot:
        return AudioTuningSnapshot(
            mic_gain_q8=self.gain_row.slider.value(),
            sample_shift_bits=self.shift_row.slider.value(),
            noise_gate=self.gate_row.slider.value(),
            tone_gain_q8=self.tone_row.slider.value(),
        )

    def _send_tuning(self) -> None:
        self.coordinator.update_tuning(self._current_tuning_from_controls())

    def _reset_tuning_defaults(self) -> None:
        self.coordinator.update_tuning(AudioTuningSnapshot())

    def _emit_tuning_preview(self) -> None:
        tuning = self._current_tuning_from_controls()
        self.gain_row.value_input.setText(f"{tuning.mic_gain_q8 / 256.0:.2f}X")
        self.shift_row.value_input.setText(str(tuning.sample_shift_bits))
        self.gate_row.value_input.setText(str(tuning.noise_gate))
        tone_multiplier = tuning.tone_gain_q8 / 256.0
        if float(tone_multiplier).is_integer():
            self.tone_row.value_input.setText(f"{int(tone_multiplier)}X")
        else:
            self.tone_row.value_input.setText(f"{tone_multiplier:.2f}X")

    def _apply_text_input(self, row: ValueSliderRow, kind: str) -> None:
        text = row.value_input.text().strip().upper().replace("X", "")
        try:
            numeric = float(text) if "." in text else int(text)
        except ValueError:
            self._refresh_from_status(self.coordinator.status)
            return

        if kind in {"gain", "tone"}:
            slider_value = int(round(float(numeric) * 256))
        else:
            slider_value = int(round(float(numeric)))
        slider_value = max(row.slider.minimum(), min(row.slider.maximum(), slider_value))
        row.slider.setValue(slider_value)

    def _refresh_from_status(self, status: BackendStatus) -> None:
        self.serial_status_value_label.setText(self.coordinator.serial_status_text())
        self.startup_checkbox.blockSignals(True)
        self.startup_checkbox.setChecked(status.startup_enabled)
        self.startup_checkbox.blockSignals(False)
        self.log_view.setPlainText(self.coordinator.log_text())
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())
        self.startup_checkbox.blockSignals(True)
        self.startup_checkbox.setChecked(status.startup_enabled)
        self.startup_checkbox.blockSignals(False)

        self.port_combo.blockSignals(True)
        current_ids = [self.port_combo.itemData(index) for index in range(self.port_combo.count())]
        next_ids = [item.device_id for item in self.coordinator.command_ports]
        if current_ids != next_ids:
            self.port_combo.clear()
            for item in self.coordinator.command_ports:
                self.port_combo.addItem(item.device_id, item.device_id)
        port_index = self.port_combo.findData(status.command_port)
        if port_index >= 0:
            self.port_combo.setCurrentIndex(port_index)
        self.port_combo.blockSignals(False)

        self.gain_row.set_value(status.tuning.mic_gain_q8, self.coordinator.mic_gain_display_text())
        self.shift_row.set_value(status.tuning.sample_shift_bits, self.coordinator.sample_shift_display_text())
        self.gate_row.set_value(status.tuning.noise_gate, self.coordinator.noise_gate_display_text())
        self.tone_row.set_value(status.tuning.tone_gain_q8, self.coordinator.tone_gain_display_text())

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self.coordinator.leave_debug_mode()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.coordinator.leave_debug_mode()
        super().closeEvent(event)

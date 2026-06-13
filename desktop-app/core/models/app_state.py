from __future__ import annotations

from dataclasses import dataclass


TONE_SLOT_START = "start"
TONE_SLOT_A = "tone_a"
TONE_SLOT_B = "tone_b"
TONE_SLOT_C = "tone_c"


@dataclass(frozen=True)
class ToneSlotDefinition:
    slot_id: str
    display_name: str
    description: str
    configurable: bool


@dataclass(frozen=True)
class DeviceOption:
    device_id: str
    display_name: str
    detail: str = ""


@dataclass(frozen=True)
class AudioTuningSnapshot:
    mic_gain_q8: int = 4096
    sample_shift_bits: int = 11
    noise_gate: int = 0
    tone_gain_q8: int = 256


@dataclass(frozen=True)
class AudioDeviceStatusSnapshot:
    current_input_name: str = ""
    detected_airmic_input_name: str = ""
    detected_airmic_input_active: bool = False
    detected_airmic_device_present: bool = False
    has_any_available_input: bool = False


@dataclass(frozen=True)
class SerialPortStatusSnapshot:
    port_name: str = ""
    state: str = "unknown"
    detail: str = ""


@dataclass(frozen=True)
class DeviceCommandResult:
    ok: bool = False
    tuning: AudioTuningSnapshot | None = None
    record_mode: str = ""
    lines: tuple[str, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class ServiceStatusSnapshot:
    state: str
    summary: str
    detail: str


TONE_SLOT_DEFINITIONS = (
    ToneSlotDefinition(
        slot_id=TONE_SLOT_START,
        display_name="Start Tone",
        description="主语音触发音，跟随当前语音模型预设。",
        configurable=False,
    ),
    ToneSlotDefinition(
        slot_id=TONE_SLOT_A,
        display_name="Tone A",
        description="扩展载波 A，可映射到辅助快捷键。",
        configurable=True,
    ),
    ToneSlotDefinition(
        slot_id=TONE_SLOT_B,
        display_name="Tone B",
        description="扩展载波 B，可映射到辅助快捷键。",
        configurable=True,
    ),
    ToneSlotDefinition(
        slot_id=TONE_SLOT_C,
        display_name="Tone C",
        description="扩展载波 C，可映射到辅助快捷键。",
        configurable=True,
    ),
)


def tone_slot_ids() -> list[str]:
    return [item.slot_id for item in TONE_SLOT_DEFINITIONS]


def configurable_tone_slots() -> list[ToneSlotDefinition]:
    return [item for item in TONE_SLOT_DEFINITIONS if item.configurable]

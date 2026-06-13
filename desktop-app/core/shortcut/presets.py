from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeyOption:
    key_id: str
    display_name: str


@dataclass(frozen=True)
class ShortcutActionPreset:
    preset_id: str
    display_name: str
    keys: tuple[str, ...]
    description: str

    @property
    def chord_label(self) -> str:
        if not self.keys:
            return "未绑定"
        return " + ".join(KEY_OPTIONS[key].display_name for key in self.keys)


@dataclass(frozen=True)
class VoiceModelPreset:
    preset_id: str
    display_name: str
    action_preset_id: str
    description: str


KEY_OPTIONS = {
    "right_alt": KeyOption("right_alt", "右 Alt"),
    "left_alt": KeyOption("left_alt", "左 Alt"),
    "left_ctrl": KeyOption("left_ctrl", "左 Ctrl"),
    "right_ctrl": KeyOption("right_ctrl", "右 Ctrl"),
    "left_win": KeyOption("left_win", "左 Win"),
    "right_win": KeyOption("right_win", "右 Win"),
    "shift": KeyOption("shift", "Shift"),
    "space": KeyOption("space", "空格"),
    "enter": KeyOption("enter", "回车"),
    "backspace": KeyOption("backspace", "退格"),
    "tab": KeyOption("tab", "Tab"),
}


ACTION_PRESET_OPTIONS = (
    ShortcutActionPreset(
        preset_id="disabled",
        display_name="禁用",
        keys=(),
        description="不发送任何快捷键。",
    ),
    ShortcutActionPreset(
        preset_id="right_alt",
        display_name="右 Alt",
        keys=("right_alt",),
        description="适合千问 App 默认语音输入。",
    ),
    ShortcutActionPreset(
        preset_id="left_alt",
        display_name="左 Alt",
        keys=("left_alt",),
        description="保留给自定义输入法或调试动作。",
    ),
    ShortcutActionPreset(
        preset_id="ctrl_win",
        display_name="Ctrl + Win",
        keys=("left_ctrl", "left_win"),
        description="适合微信语音输入法。",
    ),
)


VOICE_MODEL_OPTIONS = (
    VoiceModelPreset(
        preset_id="qwen_app",
        display_name="千问 App",
        action_preset_id="right_alt",
        description="默认主语音模型，Start Tone 触发右 Alt。",
    ),
    VoiceModelPreset(
        preset_id="wechat_voice_input",
        display_name="微信语音输入法",
        action_preset_id="right_alt",
        description="Start Tone 触发右 Alt。",
    ),
    VoiceModelPreset(
        preset_id="custom_api",
        display_name="自定义 API",
        action_preset_id="disabled",
        description="预留给后续自定义接入，不默认发送快捷键。",
    ),
)


DEFAULT_VOICE_MODEL_ID = "qwen_app"

ACTION_PRESETS_BY_ID = {item.preset_id: item for item in ACTION_PRESET_OPTIONS}
VOICE_MODEL_PRESETS_BY_ID = {item.preset_id: item for item in VOICE_MODEL_OPTIONS}


def get_action_preset_by_id(preset_id: str) -> ShortcutActionPreset:
    return ACTION_PRESETS_BY_ID[preset_id]


def get_voice_model_preset_by_id(preset_id: str) -> VoiceModelPreset:
    return VOICE_MODEL_PRESETS_BY_ID[preset_id]


def format_key_chord(keys: tuple[str, ...] | list[str]) -> str:
    normalized = tuple(keys)
    if not normalized:
        return "未绑定"
    return " + ".join(KEY_OPTIONS.get(key, KeyOption(key, key)).display_name for key in normalized)

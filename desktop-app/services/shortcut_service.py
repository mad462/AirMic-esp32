from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable, Iterable


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_SCANCODE = 0x0008
VK_RMENU = 0xA5
VK_LMENU = 0xA4
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_SPACE = 0x20
VK_RETURN = 0x0D
VK_BACK = 0x08
VK_TAB = 0x09
SC_RIGHT_ALT = 0x38
SC_LEFT_ALT = 0x38
SC_LEFT_CTRL = 0x1D
SC_RIGHT_CTRL = 0x1D
SC_LEFT_WIN = 0x5B
SC_RIGHT_WIN = 0x5C
SC_SPACE = 0x39
SC_RETURN = 0x1C
SC_BACK = 0x0E
SC_TAB = 0x0F
ULONG_PTR = wintypes.WPARAM


KEY_SPECS = {
    "right_alt": {"label": "右 Alt", "vk": VK_RMENU, "scan": SC_RIGHT_ALT, "extended": True},
    "left_alt": {"label": "左 Alt", "vk": VK_LMENU, "scan": SC_LEFT_ALT, "extended": False},
    "left_ctrl": {"label": "左 Ctrl", "vk": VK_LCONTROL, "scan": SC_LEFT_CTRL, "extended": False},
    "right_ctrl": {"label": "右 Ctrl", "vk": VK_RCONTROL, "scan": SC_RIGHT_CTRL, "extended": True},
    "left_win": {"label": "左 Win", "vk": VK_LWIN, "scan": SC_LEFT_WIN, "extended": True},
    "right_win": {"label": "右 Win", "vk": VK_RWIN, "scan": SC_RIGHT_WIN, "extended": True},
    "space": {"label": "空格", "vk": VK_SPACE, "scan": SC_SPACE, "extended": False},
    "enter": {"label": "回车", "vk": VK_RETURN, "scan": SC_RETURN, "extended": False},
    "backspace": {"label": "退格", "vk": VK_BACK, "scan": SC_BACK, "extended": False},
    "tab": {"label": "Tab", "vk": VK_TAB, "scan": SC_TAB, "extended": False},
}

LETTER_SCAN_CODES = {
    "a": 0x1E, "b": 0x30, "c": 0x2E, "d": 0x20, "e": 0x12, "f": 0x21,
    "g": 0x22, "h": 0x23, "i": 0x17, "j": 0x24, "k": 0x25, "l": 0x26,
    "m": 0x32, "n": 0x31, "o": 0x18, "p": 0x19, "q": 0x10, "r": 0x13,
    "s": 0x1F, "t": 0x14, "u": 0x16, "v": 0x2F, "w": 0x11, "x": 0x2D,
    "y": 0x15, "z": 0x2C,
}

DIGIT_SCAN_CODES = {
    "0": 0x0B, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05,
    "5": 0x06, "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A,
}

for key_name, scan_code in LETTER_SCAN_CODES.items():
    KEY_SPECS[key_name] = {
        "label": key_name.upper(),
        "vk": ord(key_name.upper()),
        "scan": scan_code,
        "extended": False,
    }

for key_name, scan_code in DIGIT_SCAN_CODES.items():
    KEY_SPECS[key_name] = {
        "label": key_name,
        "vk": ord(key_name),
        "scan": scan_code,
        "extended": False,
    }


if ctypes.sizeof(ctypes.c_void_p) > ctypes.sizeof(wintypes.WPARAM):
    ULONG_PTR = ctypes.c_ulonglong


user32 = ctypes.WinDLL("user32", use_last_error=True)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

def _key_label(key_name: str) -> str:
    spec = KEY_SPECS.get(key_name)
    if spec is None:
        return key_name
    return str(spec["label"])



def send_key(key_name: str, down: bool, mode: str = "scan") -> None:
    spec = KEY_SPECS[key_name]
    use_vk = mode in {"vk", "right_ctrl"}
    flags = KEYEVENTF_EXTENDEDKEY if use_vk else KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY
    if not spec["extended"]:
        flags &= ~KEYEVENTF_EXTENDEDKEY
    if not down:
        flags |= KEYEVENTF_KEYUP

    if mode == "right_ctrl":
        flags |= KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY
        event = INPUT(
            type=INPUT_KEYBOARD,
            u=INPUT_UNION(ki=KEYBDINPUT(0x11, 0x1D, flags, 0, 0)),
        )
    else:
        event = INPUT(
            type=INPUT_KEYBOARD,
            u=INPUT_UNION(ki=KEYBDINPUT(spec["vk"] if use_vk else 0, 0 if use_vk else spec["scan"], flags, 0, 0)),
        )
    sent = user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
    if sent != 1:
        raise ctypes.WinError(ctypes.get_last_error())


def send_combo(keys: Iterable[str], down: bool, mode: str = "scan") -> None:
    key_list = list(keys)
    ordered = key_list if down else list(reversed(key_list))
    for key in ordered:
        send_key(key, down, mode=mode)


class ShortcutService:
    def __init__(
        self,
        sender: Callable[[list[str], bool, str], None] | None = None,
        send_mode: str = "scan",
    ) -> None:
        self.sender = sender or send_combo
        self.send_mode = send_mode
        self._active_keys: tuple[str, ...] = ()
        self._active_mode = send_mode

    @property
    def active_keys(self) -> tuple[str, ...]:
        return self._active_keys

    @property
    def is_pressed(self) -> bool:
        return bool(self._active_keys)

    def _get_async_key_state(self, key_name: str) -> int:
        spec = KEY_SPECS[key_name]
        try:
            return int(user32.GetAsyncKeyState(spec["vk"]))
        except Exception:
            return 0

    def diagnostic_snapshot(self) -> str:
        active = ",".join(_key_label(key) for key in self._active_keys) or "<none>"
        os_down_keys = [key for key in self._active_keys if self._get_async_key_state(key) & 0x8000]
        os_down = ",".join(_key_label(key) for key in os_down_keys) or "<none>"
        return f"active={active} os_down={os_down} mode={self._active_mode}"

    def _resolve_mode(self, keys: tuple[str, ...], requested_mode: str | None = None) -> str:
        mode = requested_mode or self.send_mode
        if len(keys) == 1 and keys[0] == "right_ctrl":
            return "right_ctrl"
        return mode

    def press(self, keys: Iterable[str]) -> bool:
        normalized = tuple(keys)
        if not normalized:
            self._active_keys = ()
            return False
        if self._active_keys == normalized:
            return False
        if self._active_keys:
            self.release()
        resolved_mode = self._resolve_mode(normalized)
        self.sender(list(normalized), True, resolved_mode)
        self._active_keys = normalized
        self._active_mode = resolved_mode
        return True

    def release(self) -> bool:
        if not self._active_keys:
            return False
        self.sender(list(self._active_keys), False, self._active_mode)
        self._active_keys = ()
        self._active_mode = self.send_mode
        return True

    def tap(self, keys: Iterable[str]) -> bool:
        normalized = tuple(keys)
        if not normalized:
            return False
        resolved_mode = self._resolve_mode(normalized)
        self.sender(list(normalized), True, resolved_mode)
        self.sender(list(normalized), False, resolved_mode)
        self._active_keys = ()
        self._active_mode = self.send_mode
        return True

    def release_if_matches(self, keys: Iterable[str]) -> bool:
        normalized = tuple(keys)
        if self._active_keys != normalized:
            return False
        return self.release()


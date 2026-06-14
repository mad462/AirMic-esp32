from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
from typing import Callable


MODIFIER_KEYS = {"left_alt", "right_alt", "left_ctrl", "right_ctrl", "left_win", "right_win", "shift"}
VK_TO_KEY = {
    0xA4: "left_alt",
    0xA5: "right_alt",
    0xA2: "left_ctrl",
    0xA3: "right_ctrl",
    0x5B: "left_win",
    0x5C: "right_win",
    0x10: "shift",
    0x20: "space",
    0x0D: "enter",
    0x08: "backspace",
    0x09: "tab",
}

for vk_code in range(0x41, 0x5B):
    VK_TO_KEY[vk_code] = chr(vk_code).lower()

for vk_code in range(0x30, 0x3A):
    VK_TO_KEY[vk_code] = chr(vk_code)
LLKHF_UP = 0x80
WH_KEYBOARD_LL = 13
WM_QUIT = 0x0012


def map_vk_event_to_key_id(vk_code: int, scan_code: int, flags: int) -> str | None:
    _ = scan_code, flags
    return VK_TO_KEY.get(vk_code)


class ShortcutRecordingState:
    def __init__(self) -> None:
        self.pressed_order: list[str] = []
        self.recorded = False
        self.last_combo: tuple[str, ...] = ()

    def handle_keydown(self, key_id: str) -> tuple[str, ...] | None:
        if key_id not in self.pressed_order:
            self.pressed_order.append(key_id)
        self.last_combo = tuple(self.pressed_order)

        if key_id not in MODIFIER_KEYS:
            self.recorded = True
            return tuple(self.pressed_order)
        return None

    def handle_keyup(self, key_id: str) -> tuple[str, ...] | None:
        should_record = (
            not self.recorded
            and key_id in MODIFIER_KEYS
            and key_id in self.pressed_order
            and len(self.pressed_order) == 1
        )
        recorded = self.last_combo if should_record else None
        if should_record:
            self.recorded = True

        if key_id in self.pressed_order:
            self.pressed_order.remove(key_id)
        return recorded


class GlobalShortcutRecorder:
    def __init__(self, on_recorded: Callable[[tuple[str, ...]], None]) -> None:
        self.on_recorded = on_recorded
        self.state = ShortcutRecordingState()
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._hook_thread_id = 0

    def reset(self) -> None:
        self.state = ShortcutRecordingState()

    def feed_keydown(self, key_id: str) -> None:
        recorded = self.state.handle_keydown(key_id)
        if recorded:
            self.on_recorded(recorded)

    def feed_keyup(self, key_id: str) -> None:
        recorded = self.state.handle_keyup(key_id)
        if recorded:
            self.on_recorded(recorded)

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            if not self._running.is_set():
                try:
                    self._thread.join(timeout=0.2)
                except Exception:
                    pass
                if self._thread.is_alive():
                    return False
                self._thread = None
            else:
                return False
        self.reset()
        self._running.set()
        self._thread = threading.Thread(target=self._thread_main, name="airmic-global-shortcut-recorder", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running.clear()
        if self._hook_thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._hook_thread_id, WM_QUIT, 0, 0)

    def _thread_main(self) -> None:
        self._hook_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", ctypes.c_uint32),
                ("scanCode", ctypes.c_uint32),
                ("flags", ctypes.c_uint32),
                ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        LowLevelKeyboardProc = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p)

        def callback(n_code: int, w_param: int, l_param: int) -> int:
            if n_code >= 0 and self._running.is_set():
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                key_id = map_vk_event_to_key_id(kb.vkCode, kb.scanCode, kb.flags)
                if key_id:
                    is_keyup = bool(kb.flags & LLKHF_UP)
                    if is_keyup:
                        self.feed_keyup(key_id)
                    else:
                        self.feed_keydown(key_id)
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        hook_proc = LowLevelKeyboardProc(callback)
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook_proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            self._running.clear()
            self._hook_thread_id = 0
            return

        msg = wintypes.MSG()
        try:
            while self._running.is_set() and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(hook)
            self._hook_thread_id = 0
            self._running.clear()

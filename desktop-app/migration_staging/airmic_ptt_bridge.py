import argparse
import asyncio
import ctypes
import signal
import sys
from ctypes import wintypes

from bleak import BleakClient, BleakScanner


DEFAULT_DEVICE_NAME = "ESP32-AirMic-PTT"
DEFAULT_CHAR_UUID = "9d7f0002-8f5d-4d9a-8f4a-2c7fd8a10000"
PTT_EVENT_START = 1
PTT_EVENT_STOP = 2
PTT_EVENT_ACTIVE = 3

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_SCANCODE = 0x0008
VK_RMENU = 0xA5
VK_LMENU = 0xA4
VK_LCONTROL = 0xA2
VK_LWIN = 0x5B
SC_RIGHT_ALT = 0x38
SC_LEFT_ALT = 0x38
SC_LEFT_CTRL = 0x1D
SC_LEFT_WIN = 0x5B
ULONG_PTR = wintypes.WPARAM


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
user32.GetAsyncKeyState.argtypes = (wintypes.INT,)
user32.GetAsyncKeyState.restype = wintypes.SHORT


KEY_SPECS = {
    "right_alt": {"label": "右 Alt", "vk": VK_RMENU, "scan": SC_RIGHT_ALT, "extended": True},
    "left_alt": {"label": "左 Alt", "vk": VK_LMENU, "scan": SC_LEFT_ALT, "extended": False},
    "left_ctrl": {"label": "左 Ctrl", "vk": VK_LCONTROL, "scan": SC_LEFT_CTRL, "extended": False},
    "left_win": {"label": "左 Win", "vk": VK_LWIN, "scan": SC_LEFT_WIN, "extended": True},
}


def send_key(key_name: str, down: bool, mode: str = "scan") -> None:
    spec = KEY_SPECS[key_name]
    use_vk = mode == "vk"
    flags = KEYEVENTF_EXTENDEDKEY if use_vk else KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY
    if not spec["extended"]:
        flags &= ~KEYEVENTF_EXTENDEDKEY
    if not down:
        flags |= KEYEVENTF_KEYUP

    event = INPUT(
        type=INPUT_KEYBOARD,
        u=INPUT_UNION(ki=KEYBDINPUT(spec["vk"] if use_vk else 0, 0 if use_vk else spec["scan"], flags, 0, 0)),
    )
    sent = user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
    if sent != 1:
        raise ctypes.WinError(ctypes.get_last_error())


def send_right_alt(down: bool) -> None:
    send_key("right_alt", down)


def send_combo(keys: list[str], down: bool, mode: str = "scan") -> None:
    ordered = keys if down else list(reversed(keys))
    for key in ordered:
        send_key(key, down, mode=mode)


def is_right_alt_down() -> bool:
    return (user32.GetAsyncKeyState(VK_RMENU) & 0x8000) != 0


class PttBridge:
    def __init__(self, verbose: bool):
        self.verbose = verbose
        self.alt_down = False

    def set_ptt(self, pressed: bool) -> None:
        if pressed == self.alt_down:
            if self.verbose:
                print(f"PTT duplicate ignored: {'pressed' if pressed else 'released'}")
            return

        send_right_alt(pressed)
        self.alt_down = pressed
        print(f"Right Alt {'DOWN' if pressed else 'UP'}")

    def release_all(self) -> None:
        if self.alt_down:
            send_right_alt(False)
            self.alt_down = False
            print("Right Alt UP (cleanup)")


def parse_ptt_payload(data: bytearray) -> tuple[int, bool | None, int | None, int | None, int] | None:
    if len(data) >= 4:
        button_id = int(data[0])
        event_type = int(data[1])
        session_id = int(data[2])
        seq = int(data[3])
        if event_type == PTT_EVENT_START:
            return button_id, True, session_id, seq, event_type
        if event_type == PTT_EVENT_STOP:
            return button_id, False, session_id, seq, event_type
        if event_type == PTT_EVENT_ACTIVE:
            return button_id, None, session_id, seq, event_type
        return None
    if len(data) == 3:
        button_id = int(data[0])
        event_type = int(data[1])
        session_id = int(data[2])
        if event_type == PTT_EVENT_START:
            return button_id, True, session_id, None, event_type
        if event_type == PTT_EVENT_STOP:
            return button_id, False, session_id, None, event_type
        return None
    if len(data) == 2:
        return int(data[0]), data[1] != 0, None, None, PTT_EVENT_START if data[1] != 0 else PTT_EVENT_STOP
    if len(data) == 1:
        return 0, data[0] != 0, None, None, PTT_EVENT_START if data[0] != 0 else PTT_EVENT_STOP
    return None


async def find_device(name: str, timeout: float):
    print(f"Scanning BLE devices for '{name}'...")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    nearby = []
    for device, adv in devices.values():
        if device.name == name or adv.local_name == name:
            print(f"Found {name}: {device.address}")
            return device
        display_name = adv.local_name or device.name
        if display_name and ("AirMic" in display_name or "ESP32" in display_name):
            nearby.append(f"{display_name} ({device.address})")
    if nearby:
        print("Nearby ESP32/AirMic BLE devices: " + ", ".join(nearby))
    return None


async def run_bridge(args) -> int:
    bridge = PttBridge(verbose=args.verbose)
    stop_event = asyncio.Event()

    def stop(*_):
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
    except ValueError:
        pass
    if args.duration > 0:
        async def stop_later():
            await asyncio.sleep(args.duration)
            stop_event.set()

        asyncio.create_task(stop_later())

    while not stop_event.is_set():
        try:
            device = args.address
            if device is None:
                found = await find_device(args.name, args.scan_timeout)
                if found is None:
                    print("Device not found, retrying...")
                    await asyncio.sleep(args.retry_delay)
                    continue
                device = found

            print(f"Connecting BLE PTT device: {device}")
            async with BleakClient(device) as client:
                print("Connected. Subscribing to PTT notifications.")

                def on_notify(_, data: bytearray):
                    if not data:
                        return
                    event = parse_ptt_payload(data)
                    if event is None:
                        print(f"Unknown PTT payload: {bytes(data).hex(' ')}")
                        return
                    button_id, pressed, session_id, seq, event_type = event
                    action = "ACTIVE" if pressed is None else ("START" if pressed else "STOP")
                    print(f"PTT button {button_id} {action} event {event_type} session {session_id if session_id is not None else '-'} seq {seq if seq is not None else '-'}")
                    if pressed is None:
                        return
                    if button_id == 0:
                        bridge.set_ptt(pressed)

                await client.start_notify(args.char_uuid, on_notify)
                await stop_event.wait()
                await client.stop_notify(args.char_uuid)
                return 0
        except Exception as exc:
            bridge.release_all()
            if stop_event.is_set():
                break
            print(f"BLE bridge error: {exc}")
            print(f"Retrying in {args.retry_delay:.1f}s...")
            await asyncio.sleep(args.retry_delay)

    bridge.release_all()
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="AirMic BLE PTT to Windows Right Alt bridge")
    parser.add_argument("--name", default=DEFAULT_DEVICE_NAME, help="BLE advertised device name")
    parser.add_argument("--address", default=None, help="BLE address, skips scanning when set")
    parser.add_argument("--char-uuid", default=DEFAULT_CHAR_UUID, help="PTT notification characteristic UUID")
    parser.add_argument("--scan-timeout", type=float, default=5.0, help="BLE scan timeout in seconds")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="Reconnect retry delay in seconds")
    parser.add_argument("--duration", type=float, default=0.0, help="Run for N seconds, 0 means until Ctrl+C")
    parser.add_argument("--verbose", action="store_true", help="Print duplicate PTT events")
    return parser.parse_args()


def main() -> int:
    if sys.platform != "win32":
        print("This bridge currently supports Windows only.", file=sys.stderr)
        return 2

    args = parse_args()
    try:
        return asyncio.run(run_bridge(args))
    finally:
        try:
            send_right_alt(False)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

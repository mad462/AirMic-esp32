import unittest

from core.shortcut.presets import KEY_OPTIONS, format_key_chord, get_action_preset_by_id
from services.shortcut_service import ShortcutService


class ShortcutCatalogTest(unittest.TestCase):
    def test_known_keys_have_labels(self):
        self.assertEqual(KEY_OPTIONS["right_alt"].display_name, "右 Alt")
        self.assertEqual(KEY_OPTIONS["left_win"].display_name, "左 Win")

    def test_wechat_preset_maps_to_ctrl_win(self):
        preset = get_action_preset_by_id("ctrl_win")

        self.assertEqual(preset.keys, ("left_ctrl", "left_win"))

    def test_ctrl_win_preset_is_still_available_for_aux_mapping(self):
        preset = get_action_preset_by_id("ctrl_win")

        self.assertEqual(preset.display_name, "Ctrl + Win")

    def test_format_key_chord_supports_space(self):
        self.assertEqual(format_key_chord(("right_alt", "space")), "右 Alt + 空格")

    def test_known_special_keys_have_labels(self):
        self.assertEqual(KEY_OPTIONS["enter"].display_name, "回车")
        self.assertEqual(KEY_OPTIONS["backspace"].display_name, "退格")
        self.assertEqual(KEY_OPTIONS["tab"].display_name, "Tab")
        self.assertEqual(KEY_OPTIONS["right_ctrl"].display_name, "右 Ctrl")
        self.assertEqual(KEY_OPTIONS["right_win"].display_name, "右 Win")
        self.assertEqual(KEY_OPTIONS["shift"].display_name, "Shift")

    def test_shortcut_service_can_press_and_release_combo(self):
        sent_events: list[tuple[tuple[str, ...], bool, str]] = []
        service = ShortcutService(sender=lambda keys, down, mode: sent_events.append((tuple(keys), down, mode)))

        service.press(("left_ctrl", "left_win"))
        service.release()

        self.assertEqual(
            sent_events,
            [
                (("left_ctrl", "left_win"), True, "scan"),
                (("left_ctrl", "left_win"), False, "scan"),
            ],
        )

    def test_shortcut_service_accepts_space_combo(self):
        sent_events: list[tuple[tuple[str, ...], bool, str]] = []
        service = ShortcutService(sender=lambda keys, down, mode: sent_events.append((tuple(keys), down, mode)))

        service.tap(("right_alt", "space"))

        self.assertEqual(
            sent_events,
            [
                (("right_alt", "space"), True, "scan"),
                (("right_alt", "space"), False, "scan"),
            ],
        )

    def test_shortcut_service_uses_right_ctrl_specific_mode_for_single_right_ctrl(self):
        sent_events: list[tuple[tuple[str, ...], bool, str]] = []
        service = ShortcutService(sender=lambda keys, down, mode: sent_events.append((tuple(keys), down, mode)))

        service.press(("right_ctrl",))
        service.release()

        self.assertEqual(
            sent_events,
            [
                (("right_ctrl",), True, "right_ctrl"),
                (("right_ctrl",), False, "right_ctrl"),
            ],
        )

    def test_shortcut_service_diagnostic_snapshot_includes_active_keys(self):
        service = ShortcutService(sender=lambda keys, down, mode: None)
        service.press(("right_alt",))
        service._get_async_key_state = lambda key_name: 0x8000 if key_name == "right_alt" else 0  # type: ignore[method-assign]

        snapshot = service.diagnostic_snapshot()

        self.assertIn("active=右 Alt", snapshot)
        self.assertIn("os_down=右 Alt", snapshot)
        self.assertIn("mode=scan", snapshot)


if __name__ == "__main__":
    unittest.main()

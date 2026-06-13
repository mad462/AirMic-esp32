import unittest

from services.global_shortcut_recorder import ShortcutRecordingState


class ShortcutRecordingStateTest(unittest.TestCase):
    def test_records_single_modifier_on_release(self):
        state = ShortcutRecordingState()

        self.assertIsNone(state.handle_keydown("right_alt"))
        recorded = state.handle_keyup("right_alt")

        self.assertEqual(recorded, ("right_alt",))

    def test_records_modifier_combo_when_all_keys_released(self):
        state = ShortcutRecordingState()

        self.assertIsNone(state.handle_keydown("left_ctrl"))
        self.assertIsNone(state.handle_keydown("left_win"))
        self.assertIsNone(state.handle_keyup("left_win"))
        recorded = state.handle_keyup("left_ctrl")

        self.assertEqual(recorded, ("left_ctrl", "left_win"))

    def test_records_modifier_plus_regular_key_on_keydown(self):
        state = ShortcutRecordingState()

        self.assertIsNone(state.handle_keydown("left_ctrl"))
        recorded = state.handle_keydown("space")

        self.assertEqual(recorded, ("left_ctrl", "space"))


if __name__ == "__main__":
    unittest.main()

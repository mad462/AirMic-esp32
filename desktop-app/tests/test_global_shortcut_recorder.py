import unittest
from unittest import mock

from services.global_shortcut_recorder import (
    GlobalShortcutRecorder,
    ShortcutRecordingState,
    map_vk_event_to_key_id,
)
from services.shortcut_service import KEY_SPECS


class ShortcutRecordingStateTest(unittest.TestCase):
    def test_records_modifier_plus_key_combo(self):
        state = ShortcutRecordingState()

        self.assertIsNone(state.handle_keydown("right_alt"))
        recorded = state.handle_keydown("space")

        self.assertEqual(recorded, ("right_alt", "space"))

    def test_records_single_modifier_on_release(self):
        state = ShortcutRecordingState()

        self.assertIsNone(state.handle_keydown("right_alt"))
        recorded = state.handle_keyup("right_alt")

        self.assertEqual(recorded, ("right_alt",))

    def test_distinguishes_left_and_right_alt(self):
        state = ShortcutRecordingState()

        self.assertIsNone(state.handle_keydown("left_alt"))
        recorded = state.handle_keydown("space")

        self.assertEqual(recorded, ("left_alt", "space"))


class GlobalShortcutRecorderTest(unittest.TestCase):
    def test_manual_feed_emits_recorded_shortcut(self):
        recorded_results: list[tuple[str, ...]] = []
        recorder = GlobalShortcutRecorder(on_recorded=lambda keys: recorded_results.append(keys))

        recorder.feed_keydown("right_alt")
        recorder.feed_keydown("space")

        self.assertEqual(recorded_results, [("right_alt", "space")])

    def test_vk_mapping_distinguishes_right_alt(self):
        key_id = map_vk_event_to_key_id(vk_code=0xA5, scan_code=0x38, flags=0x01)

        self.assertEqual(key_id, "right_alt")

    def test_vk_mapping_distinguishes_right_ctrl(self):
        key_id = map_vk_event_to_key_id(vk_code=0xA3, scan_code=0x1D, flags=0x01)

        self.assertEqual(key_id, "right_ctrl")

    def test_vk_mapping_maps_space(self):
        key_id = map_vk_event_to_key_id(vk_code=0x20, scan_code=0x39, flags=0x00)

        self.assertEqual(key_id, "space")

    def test_vk_mapping_maps_letter_a(self):
        key_id = map_vk_event_to_key_id(vk_code=0x41, scan_code=0x1E, flags=0x00)

        self.assertEqual(key_id, "a")

    def test_manual_feed_records_ctrl_plus_a_combo(self):
        recorded_results: list[tuple[str, ...]] = []
        recorder = GlobalShortcutRecorder(on_recorded=lambda keys: recorded_results.append(keys))

        recorder.feed_keydown("left_ctrl")
        recorder.feed_keydown("a")

        self.assertEqual(recorded_results, [("left_ctrl", "a")])

    def test_shortcut_service_defines_letter_a_for_playback(self):
        self.assertIn("a", KEY_SPECS)

    def test_shortcut_service_defines_digit_1_for_playback(self):
        self.assertIn("1", KEY_SPECS)

    def test_start_recovers_from_stale_stopped_thread(self):
        recorded_results: list[tuple[str, ...]] = []
        recorder = GlobalShortcutRecorder(on_recorded=lambda keys: recorded_results.append(keys))

        class FakeStaleThread:
            def __init__(self) -> None:
                self.join_called = False
                self.alive = True

            def is_alive(self) -> bool:
                return self.alive

            def join(self, timeout=None) -> None:
                self.join_called = True
                self.alive = False

        stale_thread = FakeStaleThread()
        new_thread = mock.Mock()
        recorder._thread = stale_thread  # type: ignore[assignment]
        recorder._running.clear()

        with mock.patch("services.global_shortcut_recorder.threading.Thread", return_value=new_thread):
            started = recorder.start()

        self.assertTrue(started)
        self.assertTrue(stale_thread.join_called)
        new_thread.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path

from services.audio_status_watch_service import (
    AudioStatusWatchService,
    parse_status_watch_line,
)


class AudioStatusWatchParserTest(unittest.TestCase):
    def test_parse_status_watch_line_reads_active_airmic_snapshot(self):
        event = parse_status_watch_line(
            "STATUS\tdefault_comm=耳机 (ESP32-AirMic-HFP Hands-Free)\tdefault_multi=<none>\tairmic=耳机 (ESP32-AirMic-HFP Hands-Free)\tairmic_state=Active\tany_input=true\tbt_connected=true"
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.snapshot.current_input_name, "耳机 (ESP32-AirMic-HFP Hands-Free)")
        self.assertEqual(event.snapshot.detected_airmic_input_name, "耳机 (ESP32-AirMic-HFP Hands-Free)")
        self.assertTrue(event.snapshot.detected_airmic_input_active)
        self.assertTrue(event.snapshot.has_any_available_input)
        self.assertTrue(event.snapshot.detected_airmic_device_present)

    def test_parse_status_watch_line_clears_stale_airmic_default_when_unplugged(self):
        event = parse_status_watch_line(
            "STATUS\tdefault_comm=耳机 (ESP32-AirMic-HFP Hands-Free)\tdefault_multi=<none>\tairmic=耳机 (ESP32-AirMic-HFP Hands-Free)\tairmic_state=Unplugged\tany_input=true\tbt_connected=true"
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.snapshot.current_input_name, "")
        self.assertFalse(event.snapshot.detected_airmic_input_active)
        self.assertTrue(event.snapshot.detected_airmic_device_present)

    def test_parse_status_watch_line_marks_device_absent_when_no_airmic_present(self):
        event = parse_status_watch_line(
            "STATUS\tdefault_comm=<none>\tdefault_multi=麦克风 (Realtek(R) Audio)\tairmic=<none>\tairmic_state=NotPresent\tany_input=true\tbt_connected=false"
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertFalse(event.snapshot.detected_airmic_device_present)

    def test_parse_status_watch_line_does_not_mark_device_online_for_unplugged_endpoint_without_bt_connection(self):
        event = parse_status_watch_line(
            "STATUS\tdefault_comm=<none>\tdefault_multi=麦克风 (Realtek(R) Audio)\tairmic=耳机 (ESP32-AirMic-HFP Hands-Free)\tairmic_state=Unplugged\tany_input=true\tdevice_present=true\tbt_connected=false"
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertFalse(event.snapshot.detected_airmic_device_present)

    def test_parse_status_watch_line_reads_pnp_removal_hint(self):
        event = parse_status_watch_line(
            "HINT\tkind=pnp\toperation=delete\tname=耳机 (ESP32-AirMic-HFP Hands-Free)"
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.hint_kind, "pnp")
        self.assertEqual(event.hint_operation, "delete")
        self.assertEqual(event.hint_name, "耳机 (ESP32-AirMic-HFP Hands-Free)")


class AudioStatusWatchServiceTest(unittest.TestCase):
    def test_build_command_uses_watch_status_mode(self):
        service = AudioStatusWatchService(
            project_root=Path(r"D:\FUCKIDF\AirMic\desktop-app"),
            watch_exe=Path(r"D:\FUCKIDF\AirMic\desktop-app\tools\audio_probe\bin\AirMicAudioProbe_status.exe"),
        )

        command = service.build_command()

        self.assertEqual(command[0], r"D:\FUCKIDF\AirMic\desktop-app\tools\audio_probe\bin\AirMicAudioProbe_status.exe")
        self.assertIn("--watch-status", command)
        self.assertIn("--name", command)

    def test_consume_output_lines_updates_latest_snapshot(self):
        service = AudioStatusWatchService(project_root=Path(r"D:\FUCKIDF\AirMic\desktop-app"))
        lines = [
            "STATUS\tdefault_comm=<none>\tdefault_multi=麦克风 (Realtek(R) Audio)\tairmic=耳机 (ESP32-AirMic-HFP Hands-Free)\tairmic_state=Unplugged\tany_input=true",
            "STATUS\tdefault_comm=耳机 (ESP32-AirMic-HFP Hands-Free)\tdefault_multi=麦克风 (Realtek(R) Audio)\tairmic=耳机 (ESP32-AirMic-HFP Hands-Free)\tairmic_state=Active\tany_input=true",
        ]

        events = service.consume_output_lines(lines)

        self.assertEqual(len(events), 2)
        latest = service.latest_snapshot()
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertTrue(latest.detected_airmic_input_active)
        self.assertEqual(latest.current_input_name, "耳机 (ESP32-AirMic-HFP Hands-Free)")


if __name__ == "__main__":
    unittest.main()

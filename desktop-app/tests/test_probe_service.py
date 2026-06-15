import unittest
from pathlib import Path
from unittest import mock

from services.probe_service import (
    PROBE_EVENT_LOG,
    PROBE_EVENT_RMS,
    PROBE_EVENT_TONE,
    ProbeService,
    ProbeWatchdog,
    parse_probe_output_line,
)


class ProbeParserTest(unittest.TestCase):
    def test_parse_tone_start_line(self):
        event = parse_probe_output_line("TONE START at 5.860s score=9036.1")

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_kind, PROBE_EVENT_TONE)
        self.assertEqual(event.tone_source, "TONE")
        self.assertEqual(event.tone_event, "START")

    def test_parse_tone_a_line(self):
        event = parse_probe_output_line("TONE A at 6.120s score=4210.3")

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_kind, PROBE_EVENT_TONE)
        self.assertEqual(event.tone_source, "TONE")
        self.assertEqual(event.tone_event, "A")

    def test_parse_tone_stop_line(self):
        event = parse_probe_output_line("TONE STOP at 7.120s score=5210.3")

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_kind, PROBE_EVENT_TONE)
        self.assertEqual(event.tone_source, "TONE")
        self.assertEqual(event.tone_event, "STOP")

    def test_parse_vad_stop_line(self):
        event = parse_probe_output_line("VAD STOP at 17.970s silenceMs=510")

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_kind, PROBE_EVENT_TONE)
        self.assertEqual(event.tone_source, "VAD")
        self.assertEqual(event.tone_event, "STOP")

    def test_parse_rms_line(self):
        event = parse_probe_output_line("RMS 0.031006 peak 0.088867 nonzero 320/320")

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_kind, PROBE_EVENT_RMS)
        self.assertAlmostEqual(event.tone_rms, 0.031006)
        self.assertAlmostEqual(event.tone_peak, 0.088867)

    def test_parse_status_line(self):
        event = parse_probe_output_line("Capturing. Press Ctrl+C to stop.")

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_kind, PROBE_EVENT_LOG)
        self.assertIn("Capturing", event.raw_text)


class ProbeServiceTest(unittest.TestCase):
    def test_build_command_uses_probe_binary_and_defaults(self):
        service = ProbeService(
            project_root=Path(r"D:\FUCKIDF\AirMic\desktop-app"),
            probe_exe=Path(r"D:\FUCKIDF\AirMic\desktop-app\tools\audio_probe\bin\AirMicAudioProbe_v5.exe"),
        )

        command = service.build_command()

        self.assertEqual(command[0], r"D:\FUCKIDF\AirMic\desktop-app\tools\audio_probe\bin\AirMicAudioProbe_v5.exe")
        self.assertIn("--wait-active", command)
        self.assertIn("--min-audio-timeout-ms", command)

    def test_consume_output_lines_emits_parsed_events_in_order(self):
        service = ProbeService(project_root=Path(r"D:\FUCKIDF\AirMic\desktop-app"))
        lines = [
            "Waiting for matching WASAPI capture endpoint to become Active...",
            "Capturing. Press Ctrl+C to stop.",
            "TONE START at 5.860s score=9036.1",
            "RMS 0.031006 peak 0.088867 nonzero 320/320",
        ]

        events = service.consume_output_lines(lines)

        self.assertEqual([event.event_kind for event in events], [PROBE_EVENT_LOG, PROBE_EVENT_LOG, PROBE_EVENT_TONE, PROBE_EVENT_RMS])

    def test_start_cleans_up_stale_probe_processes_before_launching_thread(self):
        service = ProbeService(project_root=Path(r"D:\FUCKIDF\AirMic\desktop-app"))
        cleaned: list[str] = []

        service.cleanup_stale_processes = lambda emit_log=None: cleaned.append("cleanup")  # type: ignore[method-assign]

        class FakeThread:
            def __init__(self, *args, **kwargs):
                self._alive = False

            def start(self):
                return None

            def is_alive(self):
                return self._alive

        with mock.patch("services.probe_service.threading.Thread", return_value=FakeThread()):
            started = service.start(lambda event: None)

        self.assertTrue(started)
        self.assertEqual(cleaned, ["cleanup"])


class ProbeWatchdogTest(unittest.TestCase):
    def test_requests_restart_when_capture_started_but_no_audio_arrives(self):
        clock = iter([0.0, 0.0, 5.2]).__next__
        watchdog = ProbeWatchdog(clock=clock, min_audio_timeout_s=4.0)

        watchdog.mark_probe_started()
        watchdog.mark_capture_started()

        self.assertTrue(watchdog.should_restart_for_no_audio())


class ProbeRuntimeTest(unittest.TestCase):
    def test_pump_process_output_forwards_events_and_restart_hint(self):
        service = ProbeService(project_root=Path(r"D:\FUCKIDF\AirMic\desktop-app"))
        forwarded: list[tuple[str, str]] = []

        lines = [
            "Capturing. Press Ctrl+C to stop.",
            "TONE START at 5.860s score=9036.1",
            "RMS 0.031006 peak 0.088867 nonzero 320/320",
            "No audio callbacks within 4000 ms; exiting for restart.",
        ]

        service.pump_output_lines(
            lines,
            emit=lambda event: forwarded.append((event.event_kind, event.raw_text)),
        )

        self.assertEqual(forwarded[0][0], PROBE_EVENT_LOG)
        self.assertEqual(forwarded[1][0], PROBE_EVENT_TONE)
        self.assertEqual(forwarded[2][0], PROBE_EVENT_RMS)
        self.assertEqual(forwarded[3][1], "No audio callbacks within 4000 ms; exiting for restart.")

    def test_thread_main_reports_probe_exit_code_when_process_ends_unexpectedly(self):
        service = ProbeService(project_root=Path(r"D:\FUCKIDF\AirMic\desktop-app"))
        forwarded: list[tuple[str, str]] = []
        logs: list[str] = []

        class FakeStdout:
            def __iter__(self):
                yield "Capturing. Press Ctrl+C to stop.\n"
                yield "No audio callbacks within 4000 ms; exiting for restart.\n"

        class FakeProcess:
            def __init__(self):
                self.stdout = FakeStdout()
                self.returncode = 17

            def poll(self):
                return self.returncode

            def terminate(self):
                return None

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                return None

        with mock.patch("services.probe_service.Path.exists", return_value=True):
            with mock.patch("services.probe_service.subprocess.Popen", return_value=FakeProcess()):
                service._thread_main(
                    emit=lambda event: forwarded.append((event.event_kind, event.raw_text)),
                    emit_log=logs.append,
                )

        self.assertTrue(any("tone probe exited with code 17" in line for line in logs))


if __name__ == "__main__":
    unittest.main()



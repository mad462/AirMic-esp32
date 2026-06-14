import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.models.app_state import DeviceCommandResult
from services.backend_coordinator import BackendCoordinator
from services.diagnostics_log_service import DiagnosticsLogService


class DiagnosticsLogServiceTest(unittest.TestCase):
    def test_appends_lines_to_file(self):
        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "diagnostics.log"
            service = DiagnosticsLogService(log_path)

            service.append("info", "后台监听已启动")

            text = log_path.read_text(encoding="utf-8")
            self.assertIn("后台监听已启动", text)
            self.assertIn("INFO", text)


class BackendDiagnosticsLogTest(unittest.TestCase):
    def test_backend_coordinator_writes_action_events_to_local_log(self):
        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "diagnostics.log"
            diagnostics = DiagnosticsLogService(log_path)
            coordinator = BackendCoordinator(
                diagnostics_log_service=diagnostics,
                device_command_sender=lambda port_name, commands: DeviceCommandResult(ok=False, error="skip"),
            )

            coordinator.simulate_tone("Start Tone")

            text = log_path.read_text(encoding="utf-8")
            self.assertIn("收到 START", text)
            self.assertIn("Start Tone", text)


if __name__ == "__main__":
    unittest.main()

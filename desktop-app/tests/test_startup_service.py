import unittest
from pathlib import Path
from unittest import mock

from services.startup_service import StartupService


class StartupServiceTest(unittest.TestCase):
    def test_run_command_uses_pythonw_and_main_py_in_dev_mode(self):
        fake_python = Path(r"C:\Python313\python.exe")
        fake_main = Path(r"D:\FUCKIDF\AirMic\desktop-app\app\main.py")

        with mock.patch("services.startup_service.sys.executable", str(fake_python)), mock.patch(
            "services.startup_service.sys.frozen", False, create=True
        ):
            service = StartupService(executable_path=fake_python, launch_script_path=fake_main)

        command = service._run_command()

        self.assertIn("pythonw.exe", command.lower())
        self.assertIn(str(fake_main), command)

    def test_run_command_uses_bundled_exe_when_frozen(self):
        fake_exe = Path(r"D:\FUCKIDF\AirMic\desktop-app\dist\AirMicDesktop.exe")

        with mock.patch("services.startup_service.sys.frozen", True, create=True):
            service = StartupService(executable_path=fake_exe)

        self.assertEqual(service._run_command(), f'"{fake_exe}"')


if __name__ == "__main__":
    unittest.main()
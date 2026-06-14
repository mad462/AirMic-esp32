import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tools.dev_runner import collect_python_files, snapshot_mtimes, has_changes, run_dev_loop


class DevRunnerTest(unittest.TestCase):
    def test_collect_python_files_finds_py_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("print('a')", encoding="utf-8")
            (root / "b.txt").write_text("x", encoding="utf-8")
            sub = root / "sub"
            sub.mkdir()
            (sub / "c.py").write_text("print('c')", encoding="utf-8")

            files = collect_python_files([root])

            self.assertEqual({path.name for path in files}, {"a.py", "c.py"})

    def test_has_changes_detects_mtime_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "watch_me.py"
            file_path.write_text("print('v1')", encoding="utf-8")

            files = [file_path]
            before = snapshot_mtimes(files)
            time.sleep(0.02)
            file_path.write_text("print('v2')", encoding="utf-8")
            after = snapshot_mtimes(files)

            self.assertTrue(has_changes(before, after))

    def test_run_dev_loop_does_not_restart_when_gui_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            for name in ("app", "core", "services"):
                (project_root / name).mkdir()

            class FakeProcess:
                def __init__(self):
                    self.returncode = 0
                    self._poll_calls = 0

                def poll(self):
                    self._poll_calls += 1
                    return 0 if self._poll_calls >= 1 else None

            launches: list[FakeProcess] = []

            def fake_launch(_project_root: Path) -> FakeProcess:
                proc = FakeProcess()
                launches.append(proc)
                return proc

            with mock.patch("tools.dev_runner.launch_app", side_effect=fake_launch), mock.patch(
                "tools.dev_runner.terminate_process"
            ) as terminate_process, mock.patch("tools.dev_runner.time.sleep", side_effect=KeyboardInterrupt):
                exit_code = run_dev_loop(project_root)

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(launches), 1)
            terminate_process.assert_called_once()


if __name__ == "__main__":
    unittest.main()
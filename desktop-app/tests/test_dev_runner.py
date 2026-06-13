import tempfile
import time
import unittest
from pathlib import Path

from tools.dev_runner import collect_python_files, snapshot_mtimes, has_changes


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


if __name__ == "__main__":
    unittest.main()

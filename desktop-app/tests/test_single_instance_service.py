import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QCoreApplication

from services.single_instance_service import SingleInstanceService


class SingleInstanceServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_second_instance_is_rejected(self):
        with TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)

            primary = SingleInstanceService(
                app_id="airmic-test-instance",
                runtime_dir=runtime_dir,
            )
            secondary = SingleInstanceService(
                app_id="airmic-test-instance",
                runtime_dir=runtime_dir,
            )
            try:
                self.assertTrue(primary.acquire_or_activate())
                self.assertFalse(secondary.acquire_or_activate())
            finally:
                secondary.close()
                primary.close()


if __name__ == "__main__":
    unittest.main()

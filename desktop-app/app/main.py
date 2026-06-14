import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.styles.scaling import DesignScaleContext, resolve_screen_scale_factor
from app.styles.theme import build_app_stylesheet
from app.windows.main_window import MainWindow
from services.single_instance_service import SingleInstanceService


APP_ID = "AirMicDesktop"


def _configure_windows_app_identity() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OpenAI.AirMicDesktop")
    except Exception:
        pass


def _activate_existing_window(window: MainWindow) -> None:
    window.show_from_tray()


def main() -> int:
    _configure_windows_app_identity()
    app = QApplication(sys.argv)
    instance_guard = SingleInstanceService(APP_ID, Path.home() / ".airmic")
    if not instance_guard.acquire_or_activate():
        return 0

    screen = app.primaryScreen()
    scale_factor = 1.0
    if screen is not None:
        scale_factor = resolve_screen_scale_factor(
            logical_dpi=screen.logicalDotsPerInch(),
            device_pixel_ratio=screen.devicePixelRatio(),
        )

    scale = DesignScaleContext(
        scale_factor=scale_factor,
        use_design_scaling=True,
        ui_scale_multiplier=1.5,
    )
    app.setStyleSheet(build_app_stylesheet(scale))
    window = MainWindow(scale=scale)
    instance_guard.set_on_activate(lambda: _activate_existing_window(window))
    app.aboutToQuit.connect(instance_guard.close)
    if not window.windowIcon().isNull():
        app.setWindowIcon(window.windowIcon())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

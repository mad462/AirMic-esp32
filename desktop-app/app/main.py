import sys

from PySide6.QtWidgets import QApplication

from app.styles.scaling import DesignScaleContext, resolve_screen_scale_factor
from app.styles.theme import build_app_stylesheet
from app.windows.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
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
    if not window.windowIcon().isNull():
        app.setWindowIcon(window.windowIcon())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

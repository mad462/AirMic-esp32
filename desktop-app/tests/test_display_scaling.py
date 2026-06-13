import unittest

from app.styles.scaling import DesignScaleContext, resolve_screen_scale_factor
from app.styles.theme import build_app_stylesheet


class DesignScaleContextTest(unittest.TestCase):
    def test_ui_scale_multiplier_enlarges_design_size(self):
        ctx = DesignScaleContext(scale_factor=1.0, use_design_scaling=True, ui_scale_multiplier=1.5)

        self.assertEqual(ctx.scale_design_px(600), 900)
        self.assertEqual(ctx.scale_design_px(400), 600)

    def test_prefers_device_pixel_ratio_when_available(self):
        self.assertEqual(resolve_screen_scale_factor(logical_dpi=96.0, device_pixel_ratio=2.0), 2.0)

    def test_falls_back_to_logical_dpi_when_device_pixel_ratio_is_default(self):
        self.assertEqual(resolve_screen_scale_factor(logical_dpi=144.0, device_pixel_ratio=1.0), 1.5)

    def test_design_width_shrinks_under_200_percent_scaling(self):
        ctx = DesignScaleContext(scale_factor=2.0, use_design_scaling=True, ui_scale_multiplier=1.5)

        self.assertEqual(ctx.scale_design_px(600), 450)
        self.assertEqual(ctx.scale_design_px(400), 300)

    def test_design_width_stays_same_when_design_scaling_disabled(self):
        ctx = DesignScaleContext(scale_factor=2.0, use_design_scaling=False)

        self.assertEqual(ctx.scale_design_px(600), 600)

    def test_spacing_and_font_values_are_clamped(self):
        ctx = DesignScaleContext(scale_factor=2.0, use_design_scaling=True)

        self.assertGreaterEqual(ctx.scale_value(1), 1)
        self.assertGreaterEqual(ctx.scale_font(12), 9)

    def test_stylesheet_uses_bold_titles_without_changing_debug_meta_font_size(self):
        ctx = DesignScaleContext(scale_factor=1.0, use_design_scaling=True)
        css = build_app_stylesheet(ctx)

        self.assertIn("font-weight: 700;", css)
        self.assertIn("#debugMetaLabel", css)
        self.assertIn("font-size: 13px;", css)


if __name__ == "__main__":
    unittest.main()

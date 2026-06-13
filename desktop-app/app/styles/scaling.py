from __future__ import annotations

from dataclasses import dataclass


def resolve_screen_scale_factor(logical_dpi: float, device_pixel_ratio: float) -> float:
    if device_pixel_ratio and device_pixel_ratio > 1.0:
        return float(device_pixel_ratio)
    if logical_dpi and logical_dpi > 0:
        return float(logical_dpi) / 96.0
    return 1.0


@dataclass(frozen=True)
class DesignScaleContext:
    scale_factor: float = 1.0
    use_design_scaling: bool = True
    ui_scale_multiplier: float = 1.0

    def effective_factor(self) -> float:
        if not self.use_design_scaling or self.scale_factor <= 0:
            return 1.0
        return self.scale_factor

    def scale_design_px(self, value: int | float) -> int:
        factor = self.effective_factor()
        scaled = (value * self.ui_multiplier()) / factor
        return max(1, int(round(scaled)))

    def scale_value(self, value: int | float, minimum: int = 1) -> int:
        factor = self.effective_factor()
        scaled = (value * self.ui_multiplier()) / factor
        return max(minimum, int(round(scaled)))

    def scale_font(self, value: int | float, minimum: int = 9) -> int:
        return self.scale_value(value, minimum=minimum)

    def ui_multiplier(self) -> float:
        return self.ui_scale_multiplier if self.ui_scale_multiplier > 0 else 1.0

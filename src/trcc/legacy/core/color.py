"""LED color computation — rainbow table + sensor-to-color gradient mapping.

Pure domain logic, zero I/O. Matches FormLED.cs RGBTable and gradient behavior.
"""
from __future__ import annotations


class ColorEngine:
    """Encapsulates all LED color computation.

    - 768-entry RGB rainbow table (FormLED.cs RGBTable[768, 3])
    - Temperature/load → color gradient with smooth interpolation
    """

    # Gradient stops: (value, (R, G, B)) — linearly interpolated between stops.
    TEMP_GRADIENT: list[tuple[float, tuple[int, int, int]]] = [
        (30, (0, 255, 255)),    # Cyan
        (50, (0, 255, 0)),      # Green
        (70, (255, 255, 0)),    # Yellow
        (90, (255, 110, 0)),    # Orange
        (100, (255, 0, 0)),     # Red
    ]

    LOAD_GRADIENT = TEMP_GRADIENT  # Same gradient (0-100%)

    _cached_table: list[tuple[int, int, int]] | None = None

    @staticmethod
    def generate_table() -> list[tuple[int, int, int]]:
        """Generate the 768-entry RGB rainbow lookup table.

        Matches FormLED.cs RGBTable initialization — smooth HSV hue cycle
        through 768 steps covering all rainbow colors.

        The table cycles through:
            0-127:   Red→Yellow     (R=255, G increases 0→255)
            128-255: Yellow→Green   (R decreases 255→0, G=255)
            256-383: Green→Cyan     (G=255, B increases 0→255)
            384-511: Cyan→Blue      (G decreases 255→0, B=255)
            512-639: Blue→Magenta   (R increases 0→255, B=255)
            640-767: Magenta→Red    (B decreases 255→0, R=255)
        """
        table = []
        phase_len = 128  # 768 / 6 phases

        for i in range(768):
            phase = i // phase_len
            offset = i % phase_len
            t = int(255 * offset / (phase_len - 1)) if phase_len > 1 else 0

            match phase:
                case 0:  # Red → Yellow
                    r, g, b = 255, t, 0
                case 1:  # Yellow → Green
                    r, g, b = 255 - t, 255, 0
                case 2:  # Green → Cyan
                    r, g, b = 0, 255, t
                case 3:  # Cyan → Blue
                    r, g, b = 0, 255 - t, 255
                case 4:  # Blue → Magenta
                    r, g, b = t, 0, 255
                case _:  # Magenta → Red
                    r, g, b = 255, 0, 255 - t

            table.append((r, g, b))

        return table

    @classmethod
    def get_table(cls) -> list[tuple[int, int, int]]:
        """Get the cached 768-entry RGB rainbow table."""
        if cls._cached_table is None:
            cls._cached_table = cls.generate_table()
        return cls._cached_table

    @staticmethod
    def _lerp(
        c1: tuple[int, int, int], c2: tuple[int, int, int], t: float,
    ) -> tuple[int, int, int]:
        """Linearly interpolate between two RGB colors (t=0->c1, t=1->c2)."""
        t = max(0.0, min(1.0, t))
        return (
            int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t),
        )

    @staticmethod
    def color_for_value(
        value: float,
        gradient: list[tuple[float, tuple[int, int, int]]],
        high_color: tuple[int, int, int] | None = None,
    ) -> tuple[int, int, int]:
        """Map a sensor value to an RGB color with smooth gradient interpolation.

        Linearly interpolates between adjacent gradient stops.
        Clamps to first/last color outside the gradient range.

        Args:
            value: Sensor reading (temperature C, load %, etc.).
            gradient: List of (threshold, (R, G, B)) stops.
            high_color: Ignored (backward compat). Last gradient stop used instead.
        """
        if value <= gradient[0][0]:
            return gradient[0][1]
        if value >= gradient[-1][0]:
            return gradient[-1][1]

        for i in range(len(gradient) - 1):
            lo_val, lo_color = gradient[i]
            hi_val, hi_color = gradient[i + 1]
            if lo_val <= value <= hi_val:
                t = (value - lo_val) / (hi_val - lo_val)
                return ColorEngine._lerp(lo_color, hi_color, t)

        return gradient[-1][1]

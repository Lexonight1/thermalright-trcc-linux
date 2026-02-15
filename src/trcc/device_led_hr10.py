"""
HR10 2280 Pro Digital — 7-segment display renderer + NVMe temperature daemon.

The HR10 is an NVMe SSD heatsink with a 31-LED 7-segment display driven
by the same HID protocol as other LED devices (VID 0x0416, PID 0x8001).

Display layout (left → right):
    [Digit4] [Digit3] [Digit2] [°] [Digit1] [MB/s] [%]

Wire order per digit: c, d, e, g, b, a, f

Original implementation by Lcstyle (GitHub PR #9).
"""

from __future__ import annotations

import logging
import math
import signal
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

# =========================================================================
# 7-Segment Display Constants
# =========================================================================

LED_COUNT = 31

# Segment encoding: which segments are ON for each character
# Segment names: a=top, b=top-right, c=bottom-right, d=bottom,
#                e=bottom-left, f=top-left, g=middle
CHAR_SEGMENTS: Dict[str, Set[str]] = {
    '0': {'a', 'b', 'c', 'd', 'e', 'f'},
    '1': {'b', 'c'},
    '2': {'a', 'b', 'd', 'e', 'g'},
    '3': {'a', 'b', 'c', 'd', 'g'},
    '4': {'b', 'c', 'f', 'g'},
    '5': {'a', 'c', 'd', 'f', 'g'},
    '6': {'a', 'c', 'd', 'e', 'f', 'g'},
    '7': {'a', 'b', 'c'},
    '8': {'a', 'b', 'c', 'd', 'e', 'f', 'g'},
    '9': {'a', 'b', 'c', 'd', 'f', 'g'},
    '-': {'g'},
    ' ': set(),
    'A': {'a', 'b', 'c', 'e', 'f', 'g'},
    'b': {'c', 'd', 'e', 'f', 'g'},
    'C': {'a', 'd', 'e', 'f'},
    'F': {'a', 'e', 'f', 'g'},
    'H': {'b', 'c', 'e', 'f', 'g'},
    'L': {'d', 'e', 'f'},
    'P': {'a', 'b', 'e', 'f', 'g'},
    'E': {'a', 'd', 'e', 'f', 'g'},
    'r': {'e', 'g'},
    'n': {'c', 'e', 'g'},
    'o': {'c', 'd', 'e', 'g'},
    'S': {'a', 'c', 'd', 'f', 'g'},
}

# Segment wire order within each digit: index 0-6 maps to segment name
WIRE_ORDER = ('c', 'd', 'e', 'g', 'b', 'a', 'f')

# LED indices for each digit's 7 segments (in wire order)
# Digit 1 = rightmost, Digit 4 = leftmost on the physical display
DIGIT_LEDS = (
    (2, 3, 4, 5, 6, 7, 9),         # digit 1 (rightmost) — LED 8 (°) splits a,f
    (10, 11, 12, 13, 14, 15, 16),   # digit 2
    (17, 18, 19, 20, 21, 22, 23),   # digit 3
    (24, 25, 26, 27, 28, 29, 30),   # digit 4 (leftmost)
)

# Indicator LED indices
IND_MBS = 0   # MB/s
IND_PCT = 1   # %
IND_DEG = 8   # ° degree symbol


# =========================================================================
# Hr10Display — 7-segment renderer
# =========================================================================

class Hr10Display:
    """Renders text and metrics onto the HR10's 31-LED 7-segment display."""

    @staticmethod
    def render(
        text: str,
        color: Tuple[int, int, int] = (255, 255, 255),
        indicators: Optional[Set[str]] = None,
    ) -> List[Tuple[int, int, int]]:
        """Render text + indicators onto a 31-LED color array.

        Args:
            text: Up to 4 characters for digits (right-aligned).
            color: RGB tuple for lit segments.
            indicators: Set of indicator names to light: 'mbs', '%', 'deg'.

        Returns:
            List of 31 (R, G, B) tuples ready for LedPacketBuilder.
        """
        off = (0, 0, 0)
        colors: List[Tuple[int, int, int]] = [off] * LED_COUNT
        indicators = indicators or set()

        # Indicator LEDs
        if 'mbs' in indicators:
            colors[IND_MBS] = color
        if '%' in indicators:
            colors[IND_PCT] = color
        if 'deg' in indicators:
            colors[IND_DEG] = color

        # Right-align text across 4 digit positions
        padded = text.rjust(4)[:4]

        for text_pos, ch in enumerate(padded):
            if ch == ' ':
                continue
            segments_on = CHAR_SEGMENTS.get(ch, set())
            digit_idx = 3 - text_pos
            led_indices = DIGIT_LEDS[digit_idx]
            for wire_idx, seg_name in enumerate(WIRE_ORDER):
                if seg_name in segments_on:
                    colors[led_indices[wire_idx]] = color

        return colors

    @staticmethod
    def render_metric(
        value: Optional[float],
        metric: str,
        color: Tuple[int, int, int] = (255, 255, 255),
        temp_unit: str = "F",
    ) -> List[Tuple[int, int, int]]:
        """Render a drive metric value for the HR10 display.

        Args:
            value: Metric value (None shows "---").
            metric: One of "temp", "activity", "read", "write".
            color: RGB tuple for lit segments.
            temp_unit: "C" or "F" for temperature display.

        Returns:
            List of 31 (R, G, B) tuples.
        """
        render = Hr10Display.render

        if value is None:
            return render("---", color, {'deg'} if metric == 'temp' else set())

        if metric == 'temp':
            if temp_unit == 'F':
                value = value * 9 / 5 + 32
            text = f"{value:.0f}{temp_unit}"
            return render(text, color, {'deg'})

        elif metric == 'activity':
            return render(f"{value:.0f}", color, {'%'})

        elif metric in ('read', 'write'):
            return render(f"{value:.0f}", color, {'mbs'})

        return render("---", color)

    @staticmethod
    def get_digit_mask(
        text: str,
        indicators: Optional[Set[str]] = None,
    ) -> List[bool]:
        """Get a boolean mask of which LEDs should be ON for given text.

        Args:
            text: Up to 4 characters (right-aligned).
            indicators: Set of indicator names: 'mbs', '%', 'deg'.

        Returns:
            31-element list of bools.
        """
        colors = Hr10Display.render(text, (255, 255, 255), indicators)
        return [c != (0, 0, 0) for c in colors]

    @staticmethod
    def apply_animation_colors(
        digit_mask: List[bool],
        animation_colors: List[Tuple[int, int, int]],
    ) -> List[Tuple[int, int, int]]:
        """Apply animated colors to only the ON segments of a digit mask.

        Args:
            digit_mask: 31-element list of bools — True where a segment is ON.
            animation_colors: 31 RGB tuples from the animation engine.

        Returns:
            31 RGB tuples — animation color where mask is True, black elsewhere.
        """
        off = (0, 0, 0)
        return [
            animation_colors[i] if digit_mask[i] else off
            for i in range(LED_COUNT)
        ]

    # =====================================================================
    # SSD Thermal Profiles
    # =====================================================================
    # Each profile defines color gradient stops and throttle threshold.
    # Gradient: list of (temp_c, (R, G, B)) — linearly interpolated.
    # throttle_c: temperature where the drive begins thermal throttling.

    SSD_PROFILES: Dict[str, dict] = {
        "samsung-9100-pro": {
            "name": "Samsung 9100 PRO",
            "gradient": [
                (25, (0, 100, 255)),     # Cool blue
                (40, (0, 200, 200)),     # Teal
                (55, (0, 255, 100)),     # Green
                (65, (255, 200, 0)),     # Warm yellow
                (75, (255, 100, 0)),     # Orange
                (80, (255, 0, 0)),       # Red — throttle threshold
            ],
            "throttle_c": 80,
        },
        "samsung-980": {
            "name": "Samsung 980",
            "gradient": [
                (25, (0, 100, 255)),
                (40, (0, 200, 200)),
                (55, (0, 255, 100)),
                (65, (255, 200, 0)),
                (70, (255, 100, 0)),
                (75, (255, 0, 0)),
            ],
            "throttle_c": 75,
        },
        "default": {
            "name": "Generic NVMe",
            "gradient": [
                (25, (0, 100, 255)),
                (40, (0, 200, 200)),
                (55, (0, 255, 100)),
                (65, (255, 200, 0)),
                (75, (255, 100, 0)),
                (80, (255, 0, 0)),
            ],
            "throttle_c": 80,
        },
    }

    # =====================================================================
    # NVMe Temperature Helpers
    # =====================================================================

    @staticmethod
    def find_nvme_hwmon(model_substr: str = "9100") -> Optional[str]:
        """Find the hwmon path for an NVMe drive by model name.

        Scans /sys/class/hwmon/hwmon*/name for "nvme", then checks
        device/model for model_substr.

        Returns:
            hwmon path (e.g. "/sys/class/hwmon/hwmon1"), or None.
        """
        hwmon_base = Path("/sys/class/hwmon")
        if not hwmon_base.exists():
            return None

        nvme_hwmons = []
        for entry in sorted(hwmon_base.iterdir()):
            name_file = entry / "name"
            if not name_file.exists():
                continue
            try:
                name = name_file.read_text().strip()
            except OSError:
                continue
            if name != "nvme":
                continue
            nvme_hwmons.append(entry)

        # Try to match model substring
        for hwmon in nvme_hwmons:
            model_file = hwmon / "device" / "model"
            if model_file.exists():
                try:
                    model = model_file.read_text().strip()
                    if model_substr in model:
                        return str(hwmon)
                except OSError:
                    continue

        # Fallback: first NVMe hwmon
        if nvme_hwmons:
            return str(nvme_hwmons[0])

        return None

    @staticmethod
    def read_temp_celsius(hwmon_path: str) -> Optional[float]:
        """Read temperature in Celsius from hwmon sysfs.

        Args:
            hwmon_path: Path to hwmon directory (e.g. "/sys/class/hwmon/hwmon1").

        Returns:
            Temperature in Celsius, or None on error.
        """
        try:
            raw = Path(hwmon_path, "temp1_input").read_text().strip()
            return int(raw) / 1000.0
        except (OSError, ValueError):
            return None

    @staticmethod
    def breathe_brightness(
        temp_c: float, throttle_c: float, phase: float
    ) -> float:
        """Compute breathe brightness multiplier (0.0-1.0).

        - Below 40C: no breathing, steady 100%
        - 40C-throttle: sine-wave breathe, period decreases from 4s to 0.5s
        - Above throttle: fast blink (0.25s period), sharp on/off

        Args:
            temp_c: Current temperature in Celsius.
            throttle_c: Throttle threshold in Celsius.
            phase: Current time in seconds (monotonic).

        Returns:
            Brightness multiplier 0.0-1.0.
        """
        if temp_c < 40:
            return 1.0

        if temp_c >= throttle_c:
            # Fast blink: 0.25s period, square wave
            period = 0.25
            return 1.0 if (phase % period) < (period / 2) else 0.15

        # Breathe zone: 40C -> throttle_c
        # Period: 4.0s at 40C -> 0.5s at throttle_c
        t = (temp_c - 40.0) / (throttle_c - 40.0)
        period = 4.0 - t * 3.5  # 4.0 -> 0.5
        # Smooth sine breathe (min brightness 30%)
        wave = (math.sin(2 * math.pi * phase / period) + 1.0) / 2.0
        return 0.3 + 0.7 * wave

    @staticmethod
    def select_profile(model_name: str) -> dict:
        """Select the best SSD thermal profile for a given model name."""
        model_lower = model_name.lower()
        if "9100" in model_lower:
            return Hr10Display.SSD_PROFILES["samsung-9100-pro"]
        if "980" in model_lower:
            return Hr10Display.SSD_PROFILES["samsung-980"]
        return Hr10Display.SSD_PROFILES["default"]


# =========================================================================
# HR10 NVMe Temperature Daemon
# =========================================================================

def run_hr10_daemon(
    brightness: int = 100,
    model_substr: str = "9100",
    unit: str = "C",
    verbose: bool = False,
) -> int:
    """Main daemon loop: read NVMe temp → display on HR10.

    Args:
        brightness: Peak LED brightness 0-100.
        model_substr: Substring to match in NVMe model name.
        unit: Temperature display unit — "C" or "F".
        verbose: Print status messages on each update.

    Returns:
        Exit code (0 = clean shutdown, 1 = error).
    """
    from .device_factory import DeviceProtocolFactory
    from .device_led import (
        LED_PID,
        LED_VID,
        ColorEngine,
        LedHidSender,
        LedPacketBuilder,
    )

    # Find NVMe drive
    hwmon_path = Hr10Display.find_nvme_hwmon(model_substr)
    if hwmon_path is None:
        print(f"Error: No NVMe drive found matching '{model_substr}'")
        print("Available hwmon devices:")
        hwmon_base = Path("/sys/class/hwmon")
        if hwmon_base.exists():
            for entry in sorted(hwmon_base.iterdir()):
                name_file = entry / "name"
                if name_file.exists():
                    try:
                        name = name_file.read_text().strip()
                        print(f"  {entry.name}: {name}")
                    except OSError:
                        pass
        return 1

    # Show which drive we found
    model_file = Path(hwmon_path) / "device" / "model"
    model_name = "unknown"
    if model_file.exists():
        try:
            model_name = model_file.read_text().strip()
        except OSError:
            pass
    print(f"NVMe drive: {model_name} ({hwmon_path})")

    # Select thermal profile
    profile = Hr10Display.select_profile(model_name)
    gradient = profile["gradient"]
    throttle_c = profile["throttle_c"]
    print(f"Thermal profile: {profile['name']} (throttle: {throttle_c}°C)")

    # Open USB transport (uses shared helper — no duplication)
    try:
        transport = DeviceProtocolFactory.create_usb_transport(LED_VID, LED_PID)
    except ImportError as e:
        print(f"Error: {e}")
        return 1

    try:
        transport.open()
    except Exception as e:
        print(f"Error: Cannot open HR10 USB device: {e}")
        print("Make sure the device is plugged in and udev rules are set up.")
        return 1

    # Handshake
    sender = LedHidSender(transport)
    try:
        info = sender.handshake()
        style_id = info.style.style_id if info.style else "?"
        print(f"HR10 connected: {info.model_name} (PM={info.pm}, style={style_id})")
    except RuntimeError as e:
        print(f"Error: HR10 handshake failed: {e}")
        print("Try: sudo usbreset 0416:8001 && sleep 2")
        transport.close()
        return 1

    # Signal handling for clean shutdown
    shutdown = False

    def _handle_signal(signum, frame):
        nonlocal shutdown
        shutdown = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    use_f = (unit.upper() == "F")
    unit_label = "°F" if use_f else "°C"
    unit_suffix = "F" if use_f else "C"
    print(f"Displaying temperature in {unit_label} "
          f"(brightness={brightness}%, thermal colors)")
    print("Press Ctrl+C to stop.")

    last_sent_display: Optional[float] = None
    last_send_time = 0.0
    threshold = 2.0
    refresh_interval = 5.0
    start_time = time.monotonic()

    last_temp_c: Optional[float] = None
    last_temp_read = 0.0
    temp_read_interval = 1.0

    try:
        while not shutdown:
            now = time.monotonic()
            phase = now - start_time

            # Read temperature (at most once per second)
            if last_temp_c is None or (now - last_temp_read) >= temp_read_interval:
                temp_c = Hr10Display.read_temp_celsius(hwmon_path)
                if temp_c is not None:
                    last_temp_c = temp_c
                    last_temp_read = now

            if last_temp_c is None:
                time.sleep(0.1)
                continue

            temp_c = last_temp_c
            display_temp = (temp_c * 9.0 / 5.0 + 32.0) if use_f else temp_c

            # Determine if text content has changed
            text_changed = False
            if last_sent_display is None:
                text_changed = True
            elif abs(display_temp - last_sent_display) > threshold:
                text_changed = True
            elif (now - last_send_time) >= refresh_interval:
                text_changed = True

            # Compute thermal color and breathe brightness
            thermal_color = ColorEngine.color_for_value(temp_c, gradient)
            breathe_mult = Hr10Display.breathe_brightness(temp_c, throttle_c, phase)
            effective_brightness = int(brightness * breathe_mult)

            text = f"{display_temp:.0f}{unit_suffix}"
            led_colors = Hr10Display.render(text, thermal_color, {'deg'})

            is_breathing = temp_c >= 40
            should_send = text_changed or is_breathing

            if should_send:
                packet = LedPacketBuilder.build_led_packet(
                    led_colors, brightness=effective_brightness
                )
                sender.send_led_data(packet)
                if text_changed:
                    last_sent_display = display_temp
                    last_send_time = now
                    if verbose:
                        print(
                            f"  {display_temp:.0f}{unit_label} ({temp_c:.1f}°C) "
                            f"color=({thermal_color[0]},{thermal_color[1]},{thermal_color[2]}) "
                            f"bright={effective_brightness}%"
                        )

            # Sleep interval: faster when breathing for smooth animation
            if temp_c >= throttle_c:
                time.sleep(0.05)
            elif temp_c >= 40:
                time.sleep(0.05)
            else:
                time.sleep(1.0)

    except KeyboardInterrupt:
        pass

    print("\nShutting down...")
    transport.close()
    return 0

"""
HR10 NVMe Temperature Daemon.

Continuously reads NVMe drive temperature from sysfs and displays it
on the HR10 2280 Pro Digital 7-segment display via USB HID.

Original implementation by Lcstyle (GitHub PR #9).

Features:
- Temperature-based color gradient (cool blue → warm orange → hot red)
- Breathe animation that speeds up as temperature rises
- Fast red blinking above throttle threshold (~80°C)
- Efficient: skips USB writes when nothing has changed
- Customizable SSD thermal profiles
"""

import math
import signal
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# =========================================================================
# SSD Thermal Profiles
# =========================================================================
# Each profile defines color gradient stops and throttle threshold.
# Gradient: list of (temp_c, (R, G, B)) — linearly interpolated between stops.
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


def celsius_to_f(c: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return c * 9.0 / 5.0 + 32.0


def _lerp_color(
    c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float
) -> Tuple[int, int, int]:
    """Linearly interpolate between two RGB colors (t=0→c1, t=1→c2)."""
    t = max(0.0, min(1.0, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def temp_to_color(
    temp_c: float, gradient: List[Tuple[float, Tuple[int, int, int]]]
) -> Tuple[int, int, int]:
    """Map a temperature to an RGB color using gradient stops.

    Linearly interpolates between adjacent gradient stops.
    Clamps to first/last color outside the gradient range.
    """
    if temp_c <= gradient[0][0]:
        return gradient[0][1]
    if temp_c >= gradient[-1][0]:
        return gradient[-1][1]

    for i in range(len(gradient) - 1):
        t_lo, c_lo = gradient[i]
        t_hi, c_hi = gradient[i + 1]
        if t_lo <= temp_c <= t_hi:
            t = (temp_c - t_lo) / (t_hi - t_lo)
            return _lerp_color(c_lo, c_hi, t)

    return gradient[-1][1]


def breathe_brightness(
    temp_c: float, throttle_c: float, phase: float
) -> float:
    """Compute breathe brightness multiplier (0.0–1.0).

    - Below 40°C: no breathing, steady 100%
    - 40°C–throttle: sine-wave breathe, period decreases from 4s to 0.5s
    - Above throttle: fast blink (0.25s period), sharp on/off

    Args:
        temp_c: Current temperature in Celsius.
        throttle_c: Throttle threshold in Celsius.
        phase: Current time in seconds (monotonic).

    Returns:
        Brightness multiplier 0.0–1.0.
    """
    if temp_c < 40:
        return 1.0

    if temp_c >= throttle_c:
        # Fast blink: 0.25s period, square wave
        period = 0.25
        return 1.0 if (phase % period) < (period / 2) else 0.15

    # Breathe zone: 40°C → throttle_c
    # Period: 4.0s at 40°C → 0.5s at throttle_c
    t = (temp_c - 40.0) / (throttle_c - 40.0)
    period = 4.0 - t * 3.5  # 4.0 → 0.5
    # Smooth sine breathe (min brightness 30%)
    wave = (math.sin(2 * math.pi * phase / period) + 1.0) / 2.0
    return 0.3 + 0.7 * wave


def select_profile(model_name: str) -> dict:
    """Select the best SSD thermal profile for a given model name."""
    model_lower = model_name.lower()
    if "9100" in model_lower:
        return SSD_PROFILES["samsung-9100-pro"]
    if "980" in model_lower:
        return SSD_PROFILES["samsung-980"]
    return SSD_PROFILES["default"]


def run_daemon(
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
    from .hid_device import HIDAPI_AVAILABLE, PYUSB_AVAILABLE
    from .hr10_display import render_display
    from .led_device import (
        LED_PID,
        LED_VID,
        LedHidSender,
        LedPacketBuilder,
    )

    # Find NVMe drive
    hwmon_path = find_nvme_hwmon(model_substr)
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
    profile = select_profile(model_name)
    gradient = profile["gradient"]
    throttle_c = profile["throttle_c"]
    print(f"Thermal profile: {profile['name']} (throttle: {throttle_c}°C)")

    # Open USB transport
    if not PYUSB_AVAILABLE and not HIDAPI_AVAILABLE:
        print("Error: No USB backend. Install pyusb or hidapi.")
        return 1

    if PYUSB_AVAILABLE:
        from .hid_device import PyUsbTransport
        transport = PyUsbTransport(LED_VID, LED_PID)
    else:
        from .hid_device import HidApiTransport
        transport = HidApiTransport(LED_VID, LED_PID)

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
                temp_c = read_temp_celsius(hwmon_path)
                if temp_c is not None:
                    last_temp_c = temp_c
                    last_temp_read = now

            if last_temp_c is None:
                time.sleep(0.1)
                continue

            temp_c = last_temp_c
            display_temp = celsius_to_f(temp_c) if use_f else temp_c

            # Determine if text content has changed
            text_changed = False
            if last_sent_display is None:
                text_changed = True
            elif abs(display_temp - last_sent_display) > threshold:
                text_changed = True
            elif (now - last_send_time) >= refresh_interval:
                text_changed = True

            # Compute thermal color and breathe brightness
            thermal_color = temp_to_color(temp_c, gradient)
            breathe_mult = breathe_brightness(temp_c, throttle_c, phase)
            effective_brightness = int(brightness * breathe_mult)

            text = f"{display_temp:.0f}{unit_suffix}"
            led_colors = render_display(text, thermal_color, {'deg'})

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

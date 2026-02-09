# HR10 NVMe Temperature Daemon

Background daemon that displays NVMe drive temperature on the HR10 2280 Pro Digital 7-segment display with thermal-aware color and breathe animation.

## Features

- **Thermal color gradient**: Blue (cool) → teal → green → yellow → orange → red (hot)
- **Breathe animation**: Pulsing brightness that speeds up as temperature rises
  - Below 40°C: steady, no animation
  - 40°C–throttle: smooth sine-wave breathe, period 4s→0.5s
  - Above throttle (~80°C): fast red blink at 4Hz (warning)
- **SSD thermal profiles**: Built-in profiles for Samsung 9100 PRO (80°C throttle), Samsung 980 (75°C throttle), and a generic default
- **Efficient polling**: Reads sysfs once per second, sends USB only when needed
- **Systemd integration**: Runs as a user service with auto-restart

## Quick Start

```bash
# One-time setup: install udev rules (grants USB access to logged-in user)
trcc setup-udev

# Start manually
trcc hr10-tempd

# Or install as a systemd user service
cp trcc-hr10-tempd.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now trcc-hr10-tempd.service
```

## CLI Options

```
trcc hr10-tempd                    # Auto-detect Samsung 9100 PRO
trcc hr10-tempd --drive "980"      # Target Samsung 980 instead
trcc hr10-tempd --brightness 50    # Lower peak brightness (default: 100)
trcc hr10-tempd -v                 # Verbose: log each temp update
```

## Prerequisites

### 1. USB Backend (pyusb)

The daemon needs `pyusb` to communicate with the HR10 over USB:

```bash
pip install pyusb
```

On Fedora, also ensure `libusb` is available:

```bash
sudo dnf install libusb1
```

**Note:** The `hidapi` Python package (an alternative backend) may have an API incompatibility on some systems — `hid.Device` vs `hid.device` (capitalization). pyusb is the recommended and more reliable backend.

### 2. Udev Rules (USB Permissions)

Without udev rules, the USB device is only accessible by root. Run once:

```bash
trcc setup-udev
```

This installs `/etc/udev/rules.d/99-trcc-lcd.rules` which grants access to the logged-in user via systemd `uaccess` tags.

**After installing udev rules**, you must trigger them for already-connected devices:

```bash
# Option A: Unplug and replug the HR10's USB cable
# Option B: Manually re-trigger udev for the device
sudo udevadm trigger --action=change /dev/bus/usb/$(lsusb -d 0416:8001 | awk '{print $2}')/$(lsusb -d 0416:8001 | awk '{gsub(/:/, "", $4); print $4}')
```

You can verify the ACL was applied:

```bash
getfacl /dev/bus/usb/001/002  # Check for user:<your-username>:rw-
```

### 3. USB Reset (Handshake Requirement)

The HR10 firmware only responds to the HID handshake **once per power cycle**. If the daemon (or any other TRCC tool) has already connected since the last power-on, you need to USB-reset the device:

```bash
sudo usbreset 0416:8001
sleep 2  # Wait for device to re-enumerate
```

The systemd service has `Restart=on-failure` with `RestartSec=5`, so after a USB reset it will automatically recover on the next attempt.

## Systemd Service

The template `trcc-hr10-tempd.service` is provided in the repository root. Install as a **user** service (no root required at runtime):

```bash
cp trcc-hr10-tempd.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now trcc-hr10-tempd.service
```

### Checking Status

```bash
systemctl --user status trcc-hr10-tempd
journalctl --user -u trcc-hr10-tempd -f   # Follow logs
```

### Customizing

Edit `~/.config/systemd/user/trcc-hr10-tempd.service`:

```ini
ExecStart=trcc hr10-tempd --drive "9100" --brightness 75 -v
```

Then reload:

```bash
systemctl --user daemon-reload
systemctl --user restart trcc-hr10-tempd
```

## Troubleshooting

### `Access denied (insufficient permissions)`

**Cause:** Udev rules not installed or not applied to the device.

**Fix:**
```bash
trcc setup-udev
# Then re-trigger for the connected device:
sudo udevadm trigger --action=change /dev/bus/usb/001/002
```

### `module 'hid' has no attribute 'Device'`

**Cause:** The installed `hidapi` Python package uses `hid.device()` (lowercase) while the code expects `hid.Device()` (uppercase). This is a version/packaging mismatch in the cython-hidapi bindings.

**Fix:** Install `pyusb` instead — it's the preferred backend:
```bash
pip install pyusb
```

### `LED handshake failed: bad magic`

**Cause:** The HR10 firmware already responded to a handshake since its last power cycle and won't respond again.

**Fix:**
```bash
sudo usbreset 0416:8001
sleep 2
# Restart the daemon or it will auto-restart via systemd
```

### `No NVMe drive found matching '9100'`

**Cause:** The drive's hwmon name doesn't contain the expected model substring.

**Fix:** Check available hwmon devices:
```bash
for d in /sys/class/hwmon/hwmon*; do
  echo "$d: $(cat $d/name 2>/dev/null) — $(cat $d/device/model 2>/dev/null)"
done
```

Then use `--drive` with a substring that matches your drive's model:
```bash
trcc hr10-tempd --drive "Samsung"
```

## SSD Thermal Profiles

Built-in profiles define the color gradient and throttle threshold per drive model:

| Profile | Throttle | Color Range |
|---------|----------|-------------|
| Samsung 9100 PRO | 80°C | Blue(25°C) → Teal(40°C) → Green(55°C) → Yellow(65°C) → Orange(75°C) → Red(80°C) |
| Samsung 980 | 75°C | Same gradient, shifted 5°C lower |
| Generic NVMe | 80°C | Same as 9100 PRO |

The profile is auto-selected based on the detected drive model name. To add a custom profile, edit `SSD_PROFILES` in `src/trcc/hr10_tempd.py`.

## Architecture

```
sysfs (/sys/class/hwmon/hwmon1/temp1_input)
  ↓ read every 1s
hr10_tempd.run_daemon()
  ↓ temp_to_color() → thermal gradient RGB
  ↓ breathe_brightness() → animation multiplier
  ↓ render_display() → 31-LED color array
  ↓ LedPacketBuilder.build_led_packet() → HID report
  ↓ LedHidSender.send_led_data() → 64-byte USB writes
HR10 2280 Pro Digital (0416:8001)
```

The loop runs at:
- **1s intervals** below 40°C (no animation, minimal CPU)
- **50ms intervals** above 40°C (smooth breathe animation)
- **50ms intervals** above throttle (fast blink warning)

Sysfs temperature reads are rate-limited to once per second regardless of loop speed.

# TRCC Linux

[![Version](https://img.shields.io/badge/version-1.1.0-blue.svg)](https://github.com/thermalright/trcc-linux/releases)
[![License](https://img.shields.io/badge/license-GPL--3.0-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://python.org)

Native Linux port of the Thermalright LCD Control Center (Windows TRCC 2.0.3). Control and customize the LCD displays on Thermalright CPU coolers, AIO pump heads, and fan hubs — entirely from Linux.

Built with PyQt6, matching the original Windows UI pixel-for-pixel.

## Features

- **Full GUI** — 1:1 port of the Windows TRCC interface (1454x800)
- **Local themes** — Browse, apply, delete, and create themes with live preview
- **Cloud themes** — Download themes directly from Thermalright's servers
- **Video/GIF playback** — FFmpeg-based frame extraction with real-time LCD streaming
- **Theme editor** — Overlay text, sensor data, date/time on any background
- **Mask overlays** — Transparent mask layers with drag positioning
- **System info dashboard** — 77+ hardware sensors (CPU, GPU, RAM, disk, network, fans)
- **Sensor customization** — Reassign any sensor to any dashboard slot via picker dialog
- **Screen cast** — Mirror a region of your desktop to the LCD in real-time
- **Multi-device support** — Detect and switch between multiple connected LCDs
- **Multi-resolution** — 240x240, 320x320, 480x480, 640x480 (SCSI/RGB565 protocol)
- **5 starter themes** included per resolution
- **Localization** — English, Chinese (Simplified/Traditional), German, Spanish, French, Portuguese, Russian, Japanese

## Supported Devices

| Device | USB ID |
|--------|--------|
| FROZEN WARFRAME / FROZEN WARFRAME SE | `0402:3922` |
| FROZEN HORIZON PRO / FROZEN MAGIC PRO | `87CD:70DB` |
| FROZEN VISION V2 / CORE VISION / ELITE VISION | `87CD:70DB` |
| LC1 / LC2 / LC3 / LC5 (AIO pump heads) | `0416:5406` |
| AK120 / AX120 / PA120 DIGITAL | `87CD:70DB` |
| Wonder Vision (CZTV) | `87CD:70DB` |

## Install

### System dependencies

**Fedora:**
```bash
sudo dnf install sg3_utils lsscsi usbutils python3-tkinter ffmpeg
```

**Ubuntu / Debian:**
```bash
sudo apt install sg3-utils lsscsi usbutils python3-tk ffmpeg
```

**Arch:**
```bash
sudo pacman -S sg3_utils lsscsi usbutils tk ffmpeg
```

### Python package

```bash
pip install -e .
```

Or install dependencies manually:
```bash
pip install Pillow psutil requests pynvml
pip install PyQt6        # GUI
pip install pystray       # optional: system tray
```

### Device access (udev)

```bash
# Automatic setup (recommended)
sudo PYTHONPATH=src python3 -m trcc.cli setup-udev

# Or manual
echo 'SUBSYSTEM=="scsi_generic", MODE="0666"' | sudo tee /etc/udev/rules.d/99-trcc.rules
sudo udevadm control --reload-rules
```

Unplug and replug the device after setting up udev rules.

## Usage

```bash
trcc gui                  # Launch GUI
trcc gui --decorated      # With window decorations (debugging)
trcc detect               # Show connected devices
trcc detect --all         # Show all SCSI devices
trcc send image.png       # Send image to LCD
trcc test                 # Color cycle test
trcc setup-udev --dry-run # Preview udev rules without applying
trcc version              # Show version info
```

Or run from source:
```bash
PYTHONPATH=src python3 -m trcc.cli gui
```

## Architecture

```
src/trcc/
├── cli.py                    # CLI entry point
├── lcd_driver.py             # SCSI RGB565 frame send
├── device_detector.py        # USB device scan
├── scsi_device.py            # Low-level SCSI commands
├── dc_parser.py / dc_writer.py  # config1.dc overlay format
├── overlay_renderer.py       # PIL-based text/sensor overlay
├── gif_animator.py           # FFmpeg video frame extraction
├── sensor_enumerator.py      # Hardware sensor discovery (hwmon, pynvml, psutil, RAPL)
├── sysinfo_config.py         # Dashboard panel config persistence
├── system_info.py            # Legacy sensor collection
├── cloud_downloader.py       # Cloud theme HTTP fetch
├── paths.py                  # XDG path resolution
├── __version__.py            # Version info
├── core/
│   ├── models.py             # ThemeInfo, DeviceInfo, VideoState
│   └── controllers.py        # GUI-independent MVC controllers
└── qt_components/
    ├── qt_app_mvc.py          # Main window (1454x800)
    ├── uc_device.py           # Device sidebar
    ├── uc_preview.py          # Live preview frame
    ├── uc_theme_local.py      # Local theme browser
    ├── uc_theme_web.py        # Cloud theme browser
    ├── uc_theme_mask.py       # Mask browser
    ├── uc_theme_setting.py    # Overlay editor
    ├── uc_system_info.py      # Sensor dashboard
    ├── uc_sensor_picker.py    # Sensor selection dialog
    ├── uc_about.py            # Settings panel
    └── ...                    # 41 modules total
```

**MVC pattern** — Controllers in `core/` are GUI-independent. Views subscribe via callbacks, making it possible to swap frontends.

**726 GUI assets** extracted from the Windows application, applied via QPalette (not stylesheets) to match the original dark theme exactly.

## Changelog

### v1.1.0
- Fixed overlay element cards to match Windows UCXiTongXianShiSub exactly
- Fixed mask toggle to hide/show instead of destroying mask data
- Added mask reset/clear functionality
- Added font picker dialog for overlay elements
- Fixed font name and style preservation when loading themes
- Fixed disabled overlay elements being re-enabled on property changes
- Fixed 12-hour time format to not show leading zero (2:58 PM instead of 02:58 PM)
- Added `set_mask_visible()` controller method for proper mask toggle
- Added video resume when toggling video display back on

### v1.0.0
- Initial release
- Full GUI port of Windows TRCC 2.0.3
- Local and cloud theme support
- Video/GIF playback with FFmpeg
- Theme editor with overlay elements
- System info dashboard with 77+ sensors
- Screen cast functionality
- Multi-device and multi-resolution support

## Support

If you find this project useful, consider supporting development:

[Buy Me a Coffee](https://buymeacoffee.com/Lexonight1)

## License

GPL-3.0

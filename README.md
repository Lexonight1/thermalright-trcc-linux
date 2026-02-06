# TRCC Linux

[![Version](https://img.shields.io/badge/version-1.1.3-blue.svg)](https://github.com/Lexonight1/thermalright-trcc-linux/releases)
[![License](https://img.shields.io/badge/license-GPL--3.0-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://python.org)
[![PyQt6](https://img.shields.io/badge/GUI-PyQt6-41cd52.svg)](https://www.riverbankcomputing.com/software/pyqt/)

Native Linux port of the Thermalright LCD Control Center (Windows TRCC 2.0.3). Control and customize the LCD displays on Thermalright CPU coolers, AIO pump heads, and fan hubs — entirely from Linux.

Built with PyQt6, matching the original Windows UI pixel-for-pixel. **Feature-complete** — 100% Windows feature parity achieved.

## Features

- **Themes** — Local, cloud, masks, carousel mode, export/import as `.tr` files
- **Media** — Video/GIF playback, video trimmer, image cropper, screen cast (X11 + Wayland)
- **Editor** — Overlay text/sensors/date/time, font picker, dynamic scaling, eyedropper
- **Hardware** — 77+ sensors, customizable dashboard, multi-device with per-device config
- **Display** — 240x240, 320x320, 480x480, 640x480 resolutions, 0°/90°/180°/270° rotation
- **Extras** — 5 starter themes + 120 masks per resolution, 8 languages, system tray, auto-start

## Supported Devices

| Device | USB ID |
|--------|--------|
| FROZEN WARFRAME / FROZEN WARFRAME SE | `0402:3922` |
| FROZEN HORIZON PRO / FROZEN MAGIC PRO | `87CD:70DB` |
| FROZEN VISION V2 / CORE VISION / ELITE VISION | `87CD:70DB` |
| LC1 / LC2 / LC3 / LC5 (AIO pump heads) | `0416:5406` |
| AK120 / AX120 / PA120 DIGITAL | `87CD:70DB` |
| Wonder Vision (CZTV) | `87CD:70DB` |

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux

# 2. Install system deps (Fedora example — see Install Guide for other distros)
sudo dnf install sg3_utils python3-pyqt6 ffmpeg

# 3. Install TRCC
pip install -e .

# 4. Set up device permissions (then unplug & replug USB cable)
sudo trcc setup-udev

# 5. Launch
trcc gui
```

See the **[Install Guide](doc/INSTALL_GUIDE.md)** for per-distro instructions (Fedora, Ubuntu, Debian, Arch, openSUSE, NixOS, Void, Gentoo, SteamOS, Bazzite, and more), optional dependencies, and troubleshooting.

## Usage

```bash
trcc gui                  # Launch GUI
trcc gui --decorated      # With window decorations (debugging)
trcc detect               # Show connected devices
trcc send image.png       # Send image to LCD
trcc test                 # Color cycle test
trcc download themes-320  # Download cloud themes for 320x320
trcc version              # Show version info
```

Or run from source:
```bash
PYTHONPATH=src python3 -m trcc.cli gui
```

## Documentation

| Document | Description |
|----------|-------------|
| [Install Guide](doc/INSTALL_GUIDE.md) | Step-by-step installation for all major distros |
| [Architecture](doc/ARCHITECTURE.md) | Project layout, MVC design, per-device config |
| [Changelog](doc/CHANGELOG.md) | Version history and release notes |
| [Technical Reference](doc/TECHNICAL_REFERENCE.md) | SCSI protocol, FBL codes, DC file formats |
| [Porting Guide](doc/PORTING_GUIDE.md) | How Windows TRCC was ported to Linux |

## Support

If you find this project useful, consider supporting development:

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow?style=for-the-badge&logo=buy-me-a-coffee)](https://buymeacoffee.com/Lexonight1)

## License

GPL-3.0

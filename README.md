# TRCC Linux

[![Version](https://img.shields.io/badge/version-1.1.3-blue.svg)](https://github.com/Lexonight1/thermalright-trcc-linux/releases)
[![License](https://img.shields.io/badge/license-GPL--3.0-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://python.org)
[![PyQt6](https://img.shields.io/badge/GUI-PyQt6-41cd52.svg)](https://www.riverbankcomputing.com/software/pyqt/)

Native Linux port of the Thermalright LCD Control Center (Windows TRCC 2.0.3). Control and customize the LCD displays on Thermalright CPU coolers, AIO pump heads, and fan hubs — entirely from Linux.

## Supported Devices

Any Thermalright LCD cooler/hub that shows up as a USB Mass Storage device with one of these IDs:

| USB ID | Devices |
|--------|---------|
| `87CD:70DB` | FROZEN HORIZON PRO, FROZEN MAGIC PRO, FROZEN VISION V2, CORE VISION, ELITE VISION, AK120, AX120, PA120 DIGITAL, Wonder Vision (CZTV) |
| `0416:5406` | LC1, LC2, LC3, LC5 (AIO pump heads) |
| `0402:3922` | FROZEN WARFRAME, FROZEN WARFRAME SE |

Resolutions: 240x240, 320x320, 480x480, 640x480

> **Not sure?** Plug in your device and run `lsusb` — if you see one of the USB IDs above, it's supported.

## Install

See the **[Install Guide](doc/INSTALL_GUIDE.md)** for step-by-step instructions covering 25+ Linux distributions including Fedora, Ubuntu, Debian, Arch, openSUSE, NixOS, Void, Gentoo, SteamOS, Bazzite, and more.

**Quick start (Fedora):**

```bash
git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
sudo dnf install sg3_utils python3-pyqt6 ffmpeg
pip install -e .
sudo trcc setup-udev    # then unplug & replug USB cable
trcc gui
```

## Support

If this project helped you, consider buying me a coffee:

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow?style=for-the-badge&logo=buy-me-a-coffee)](https://buymeacoffee.com/Lexonight1)

## Features

- **Themes** — Local, cloud, masks, carousel mode, export/import as `.tr` files
- **Media** — Video/GIF playback, video trimmer, image cropper, screen cast (X11 + Wayland)
- **Editor** — Overlay text/sensors/date/time, font picker, dynamic scaling, eyedropper
- **Hardware** — 77+ sensors, customizable dashboard, multi-device with per-device config
- **Display** — 4 resolutions, 0/90/180/270 rotation, 3 brightness levels
- **Extras** — 5 starter themes + 120 masks per resolution, 8 languages, system tray, auto-start

## Usage

```bash
trcc gui                  # Launch GUI
trcc detect               # Show connected devices
trcc send image.png       # Send image to LCD
trcc test                 # Color cycle test
trcc download themes-320  # Download cloud themes
```

## Documentation

| Document | Description |
|----------|-------------|
| [Install Guide](doc/INSTALL_GUIDE.md) | Installation for all major distros |
| [Changelog](doc/CHANGELOG.md) | Version history |
| [Architecture](doc/ARCHITECTURE.md) | Project layout and design |
| [Technical Reference](doc/TECHNICAL_REFERENCE.md) | SCSI protocol and file formats |

## License

GPL-3.0

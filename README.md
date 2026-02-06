# TRCC Linux

[![Version](https://img.shields.io/badge/version-1.1.0-blue.svg)](https://github.com/Lexonight1/thermalright-trcc-linux/releases)
[![License](https://img.shields.io/badge/license-GPL--3.0-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://python.org)
[![PyQt6](https://img.shields.io/badge/GUI-PyQt6-41cd52.svg)](https://www.riverbankcomputing.com/software/pyqt/)

Native Linux port of the Thermalright LCD Control Center (Windows TRCC 2.0.3). Control and customize the LCD displays on Thermalright CPU coolers, AIO pump heads, and fan hubs — entirely from Linux.

Built with PyQt6, matching the original Windows UI pixel-for-pixel. **Feature-complete** — 100% Windows feature parity achieved.

## Features

### Themes
- **Local themes** — Browse, apply, delete, and create themes with live preview
- **Cloud themes** — Download themes from Thermalright's servers with category filtering
- **Mask overlays** — Transparent mask layers with drag positioning, toggle, and reset
- **Carousel mode** — Auto-rotate through up to 6 themes on a configurable timer
- **Theme export/import** — Save and share themes as `.tr` files (background + mask + config)

### Media
- **Video/GIF playback** — FFmpeg-based frame extraction with real-time LCD streaming
- **Video trimmer** — Trim videos and export as `Theme.zt` frame packages
- **Image cropper** — Crop and resize images to fit any LCD resolution
- **Screen cast** — Mirror a region of your desktop to the LCD in real-time (X11 + Wayland/PipeWire)

### Editor
- **Theme editor** — Overlay text, sensor data, date/time on any background
- **Font picker** — Choose font family, size, style, and color for each overlay element
- **Dynamic scaling** — Fonts and coordinates auto-scale across resolutions
- **Fullscreen color picker** — Eyedropper tool to pick any color from your screen

### Hardware
- **System info dashboard** — 77+ hardware sensors (CPU, GPU, RAM, disk, network, fans)
- **Sensor customization** — Reassign any sensor to any dashboard slot via picker dialog
- **Multi-device support** — Detect and switch between multiple connected LCDs
- **Multi-resolution** — 240x240, 320x320, 480x480, 640x480 (SCSI/RGB565 protocol)
- **LCD rotation** — 0/90/180/270 degree display rotation

### General
- **5 starter themes** included per resolution
- **Localization** — English, Chinese (Simplified/Traditional), German, Spanish, French, Portuguese, Russian, Japanese
- **Auto-start** — Optional launch at login via XDG autostart
- **System tray** — Minimize to tray (optional, requires `pystray`)

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
sudo dnf install sg3_utils lsscsi usbutils ffmpeg
```

**Ubuntu / Debian:**
```bash
sudo apt install sg3-utils lsscsi usbutils ffmpeg
```

**Arch:**
```bash
sudo pacman -S sg3_utils lsscsi usbutils ffmpeg
```

### Python package

```bash
pip install -e .
```

Or install dependencies manually:
```bash
pip install Pillow psutil requests       # Core
pip install PyQt6                        # GUI (required)
pip install pystray                      # Optional: system tray
pip install pynvml                       # Optional: NVIDIA GPU sensors
```

#### Wayland screen capture (optional)

For screen casting on GNOME/KDE Wayland (where X11 screen grab is unavailable):

```bash
pip install dbus-python PyGObject
```

Also requires `gstreamer`, `gst-plugins-base`, and `gst-plugins-good` system packages (usually pre-installed on GNOME/KDE).

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
trcc download themes-320  # Download cloud themes for 320x320
trcc version              # Show version info
```

Or run from source:
```bash
PYTHONPATH=src python3 -m trcc.cli gui
```

## Architecture

```
src/trcc/
├── cli.py                       # CLI entry point
├── lcd_driver.py                # SCSI RGB565 frame send
├── device_detector.py           # USB device scan + KNOWN_DEVICES registry
├── device_implementations.py    # Per-device protocol variants
├── scsi_device.py               # Low-level SCSI commands
├── dc_parser.py                 # Parse config1.dc overlay configs
├── dc_writer.py                 # Write config1.dc files
├── overlay_renderer.py          # PIL-based text/sensor overlay rendering
├── gif_animator.py              # FFmpeg video frame extraction
├── sensor_enumerator.py         # Hardware sensor discovery (hwmon, pynvml, psutil, RAPL)
├── sysinfo_config.py            # Dashboard panel config persistence
├── system_info.py               # CPU/GPU/RAM/disk sensor collection
├── cloud_downloader.py          # Cloud theme HTTP fetch
├── theme_downloader.py          # Theme pack download manager
├── theme_io.py                  # Theme export/import (.tr format)
├── paths.py                     # XDG data/config path resolution
├── __version__.py               # Version info
├── core/
│   ├── models.py                # ThemeInfo, DeviceInfo, VideoState, OverlayElement
│   └── controllers.py           # GUI-independent MVC controllers
└── qt_components/
    ├── qt_app_mvc.py            # Main window (1454x800)
    ├── base.py                  # BasePanel, ImageLabel, pil_to_pixmap
    ├── constants.py             # Layout coords, sizes, colors, styles
    ├── assets.py                # Asset loader with lru_cache
    ├── eyedropper.py            # Fullscreen color picker
    ├── screen_capture.py        # X11/Wayland screen grab
    ├── pipewire_capture.py      # PipeWire/Portal Wayland capture
    ├── uc_device.py             # Device sidebar
    ├── uc_preview.py            # Live preview frame
    ├── uc_theme_local.py        # Local theme browser
    ├── uc_theme_web.py          # Cloud theme browser
    ├── uc_theme_mask.py         # Mask browser
    ├── uc_theme_setting.py      # Overlay editor / display mode panels
    ├── uc_image_cut.py          # Image cropper
    ├── uc_video_cut.py          # Video trimmer
    ├── uc_system_info.py        # Sensor dashboard
    ├── uc_sensor_picker.py      # Sensor selection dialog
    ├── uc_info_module.py        # Live system info display
    ├── uc_activity_sidebar.py   # Sensor element picker
    └── uc_about.py              # Settings / about panel
```

**MVC pattern** — Controllers in `core/` are GUI-independent. Views subscribe via callbacks, making it possible to swap frontends.

**726 GUI assets** extracted from the Windows application, applied via QPalette (not stylesheets) to match the original dark theme exactly.

## Changelog

### v1.1.0
- Carousel mode — auto-rotate through up to 6 themes on a timer
- Theme export/import — save/load themes as `.tr` files
- Video trimmer — trim videos and export as `Theme.zt` frame packages
- Image cropper — crop and resize images for any LCD resolution
- Fullscreen color picker — eyedropper tool for picking screen colors
- Dynamic font and coordinate scaling across resolutions
- Font picker dialog for overlay elements
- Mask toggle to hide/show instead of destroying mask data
- Mask reset/clear functionality
- Screen cast with PipeWire/Portal support for Wayland
- Sensor customization dashboard with reassignable sensor slots
- Overlay element cards matching Windows UCXiTongXianShiSub exactly
- Font name and style preservation when loading themes
- Fixed disabled overlay elements being re-enabled on property changes
- Fixed 12-hour time format (2:58 PM instead of 02:58 PM)
- Video resume when toggling video display back on

### v1.0.0
- Initial release
- Full GUI port of Windows TRCC 2.0.3
- Local and cloud theme support
- Video/GIF playback with FFmpeg
- Theme editor with overlay elements
- System info dashboard with 77+ sensors
- Screen cast functionality
- Multi-device and multi-resolution support

## Documentation

- [Install Guide](doc/INSTALL_GUIDE.md) — Detailed installation instructions
- [Porting Guide](doc/PORTING_GUIDE.md) — How Windows TRCC was ported to Linux
- [Technical Reference](doc/TECHNICAL_REFERENCE.md) — Protocol details, FBL codes, DC file formats

## Support

If you find this project useful, consider supporting development:

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow?style=for-the-badge&logo=buy-me-a-coffee)](https://buymeacoffee.com/Lexonight1)

## License

GPL-3.0

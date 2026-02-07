# TRCC Linux

[![Version](https://img.shields.io/badge/version-1.1.3-blue.svg)](https://github.com/Lexonight1/thermalright-trcc-linux/releases)
[![License](https://img.shields.io/badge/license-GPL--3.0-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://python.org)
[![PyQt6](https://img.shields.io/badge/GUI-PyQt6-41cd52.svg)](https://www.riverbankcomputing.com/software/pyqt/)

Native Linux port of the Thermalright LCD Control Center (Windows TRCC 2.0.3). Control and customize the LCD displays on Thermalright CPU coolers, AIO pump heads, and fan hubs — entirely from Linux.

## Supported Devices

Plug in your device and run `lsusb` in a terminal. Find the line with your device — the USB ID is the `xxxx:xxxx` after `ID`:

```
Bus 001 Device 008: ID 0416:5302 Winbond Electronics Corp. USBDISPLAY
                        ^^^^^^^^^
                        this is your USB ID
```

Match it to the tables below.

**SCSI devices** — fully supported:

| USB ID | Devices |
|--------|---------|
| `87CD:70DB` | FROZEN HORIZON PRO, FROZEN MAGIC PRO, FROZEN VISION V2, CORE VISION, ELITE VISION, AK120, AX120, PA120 DIGITAL, Wonder Vision |
| `0416:5406` | LC1, LC2, LC3, LC5 (AIO pump heads) |
| `0402:3922` | FROZEN WARFRAME, FROZEN WARFRAME SE |

Resolutions: 240x240, 320x320, 480x480, 640x480

**HID devices** — on [`hid-protocol-testing`](https://github.com/Lexonight1/thermalright-trcc-linux/tree/hid-protocol-testing) branch, testers wanted:

| USB ID | Devices |
|--------|---------|
| `0416:5302` | USBDISPLAY (HID) |
| `0416:530A` | LCD Display (HID) |
| `0416:53E6` | LCD Display (HID) |
| `0418:5303` | LCD Display (HID) |
| `0418:5304` | LCD Display (HID) |

> **HID device?** Install from the testing branch instead:
> ```bash
> git clone -b hid-protocol-testing https://github.com/Lexonight1/thermalright-trcc-linux.git
> ```
> Already have stable? Switch with: `git fetch origin && git checkout hid-protocol-testing`

## Install

> **New to Linux?** See the **[Install Guide](doc/INSTALL_GUIDE.md)** — it explains every command and covers 25+ distros step by step.

Find your distro, copy the block, paste in terminal. After it finishes: **unplug and replug the USB cable**, then run `trcc gui`.

If your distro doesn't package PyQt6, just drop it from the system install line — `pip install -e .` will pull it from PyPI automatically.

See the **[Install Guide](doc/INSTALL_GUIDE.md)** for troubleshooting, optional deps, immutable distros, and more.

### Fedora / Nobara

```bash
sudo dnf install sg3_utils python3-pyqt6 ffmpeg && git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git && cd thermalright-trcc-linux && pip install --break-system-packages -e . && sudo trcc setup-udev
```

### Ubuntu / Debian / Mint / Pop!_OS

```bash
sudo apt install sg3-utils python3-pyqt6 ffmpeg python3-pip && git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git && cd thermalright-trcc-linux && pip install --break-system-packages -e . && sudo trcc setup-udev
```

### Arch / Manjaro / EndeavourOS / CachyOS / Garuda

```bash
sudo pacman -S sg3_utils python-pyqt6 ffmpeg python-pip && git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git && cd thermalright-trcc-linux && pip install --break-system-packages -e . && sudo trcc setup-udev
```

### openSUSE

```bash
sudo zypper install sg3_utils python3-qt6 ffmpeg python3-pip && git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git && cd thermalright-trcc-linux && pip install --break-system-packages -e . && sudo trcc setup-udev
```

### Void Linux

```bash
sudo xbps-install sg3_utils python3-PyQt6 ffmpeg python3-pip && git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git && cd thermalright-trcc-linux && pip install --break-system-packages -e . && sudo trcc setup-udev
```

### Gentoo

```bash
sudo emerge --ask sg3_utils dev-python/PyQt6 media-video/ffmpeg dev-python/pip && git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git && cd thermalright-trcc-linux && pip install --break-system-packages -e . && sudo trcc setup-udev
```

### Alpine

```bash
sudo apk add sg3_utils py3-pyqt6 ffmpeg py3-pip python3 && git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git && cd thermalright-trcc-linux && pip install --break-system-packages -e . && sudo trcc setup-udev
```

### Bazzite / Fedora Atomic / Aurora / Bluefin

```bash
rpm-ostree install sg3_utils && echo "Reboot now, then run the next block" && systemctl reboot
```
After reboot:
```bash
git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git && cd thermalright-trcc-linux && python3 -m venv ~/trcc-env && source ~/trcc-env/bin/activate && pip install -e . && sudo ~/trcc-env/bin/trcc setup-udev
```
Launch: `source ~/trcc-env/bin/activate && trcc gui`

### SteamOS (Steam Deck)

Switch to Desktop Mode, open Konsole:
```bash
sudo steamos-readonly disable && sudo pacman -S --needed sg3_utils python-pip python-pyqt6 ffmpeg && git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git && cd thermalright-trcc-linux && pip install --break-system-packages -e . && sudo trcc setup-udev && sudo steamos-readonly enable
```

### NixOS

Add to `/etc/nixos/configuration.nix`, then `sudo nixos-rebuild switch`:
```nix
environment.systemPackages = with pkgs; [
  python3 python3Packages.pip python3Packages.pyqt6 python3Packages.pillow
  python3Packages.psutil sg3_utils ffmpeg p7zip
];
services.udev.extraRules = ''
  SUBSYSTEM=="scsi_generic", ATTRS{idVendor}=="87cd", ATTRS{idProduct}=="70db", MODE="0666"
  SUBSYSTEM=="scsi_generic", ATTRS{idVendor}=="0416", ATTRS{idProduct}=="5406", MODE="0666"
  SUBSYSTEM=="scsi_generic", ATTRS{idVendor}=="0402", ATTRS{idProduct}=="3922", MODE="0666"
'';
```
Then:
```bash
git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git && cd thermalright-trcc-linux && pip install --break-system-packages -e .
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
| [HID Testing Guide](https://github.com/Lexonight1/thermalright-trcc-linux/blob/hid-protocol-testing/doc/HID_TESTING.md) | HID device support (testers wanted) |

## License

GPL-3.0

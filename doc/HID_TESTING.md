# HID Device Testing Guide

The HID protocol is implemented with **563 automated tests** but **not tested against real hardware**. I only have a SCSI device (`87CD:70DB`). If you have an HID device, please help test.

## Supported HID Devices

Run `lsusb` and look for your VID:PID:

| VID:PID | lsusb shows | Protocol |
|---------|-------------|----------|
| `0416:5302` | Winbond Electronics Corp. USBDISPLAY | HID Type 2 (LCD) |
| `0418:5303` | ALi Corp. LCD Display | HID Type 3 (LCD) |
| `0418:5304` | ALi Corp. LCD Display | HID Type 3 (LCD) |
| `0416:8001` | Winbond Electronics Corp. LED Controller | HID LED (RGB) |

## How to test

Find your distro, copy the block, paste in terminal. These install TRCC + HID dependencies (libusb, pyusb) in one shot. After it finishes: **unplug and replug the USB cable**, then **open a new terminal**.

> **Already have TRCC installed?** Just run `pip install --break-system-packages pyusb` and then `trcc gui` (HID is auto-detected).

### Fedora / Nobara

```bash
sudo dnf install sg3_utils python3-pyqt6 ffmpeg libusb1-devel && ([ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git) && cd thermalright-trcc-linux && pip install --break-system-packages -e . && pip install --break-system-packages pyusb && trcc setup-udev && trcc install-desktop
```

### Ubuntu / Debian / Mint / Pop!_OS

```bash
sudo apt install sg3-utils python3-pyqt6 ffmpeg python3-pip libusb-1.0-0-dev && ([ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git) && cd thermalright-trcc-linux && pip install --break-system-packages -e . && pip install --break-system-packages pyusb && trcc setup-udev && trcc install-desktop
```

### Arch / Manjaro / EndeavourOS / CachyOS / Garuda

```bash
sudo pacman -S sg3_utils python-pyqt6 ffmpeg python-pip libusb && ([ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git) && cd thermalright-trcc-linux && pip install --break-system-packages -e . && pip install --break-system-packages pyusb && trcc setup-udev && trcc install-desktop
```

### openSUSE

```bash
sudo zypper install sg3_utils python3-qt6 ffmpeg python3-pip libusb-1_0-devel && ([ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git) && cd thermalright-trcc-linux && pip install --break-system-packages -e . && pip install --break-system-packages pyusb && trcc setup-udev && trcc install-desktop
```

### Void Linux

```bash
sudo xbps-install sg3_utils python3-PyQt6 ffmpeg python3-pip libusb-devel && ([ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git) && cd thermalright-trcc-linux && pip install --break-system-packages -e . && pip install --break-system-packages pyusb && trcc setup-udev && trcc install-desktop
```

### Gentoo

```bash
sudo emerge --ask sg3_utils dev-python/PyQt6 media-video/ffmpeg dev-python/pip dev-libs/libusb && ([ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git) && cd thermalright-trcc-linux && pip install --break-system-packages -e . && pip install --break-system-packages pyusb && trcc setup-udev && trcc install-desktop
```

### Alpine

```bash
sudo apk add sg3_utils py3-pyqt6 ffmpeg py3-pip python3 libusb-dev && ([ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git) && cd thermalright-trcc-linux && pip install --break-system-packages -e . && pip install --break-system-packages pyusb && trcc setup-udev && trcc install-desktop
```

### SteamOS (Steam Deck)

Switch to Desktop Mode, open Konsole:
```bash
sudo steamos-readonly disable && sudo pacman -S --needed sg3_utils python-pip python-pyqt6 ffmpeg libusb && ([ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone -b stable https://github.com/Lexonight1/thermalright-trcc-linux.git) && cd thermalright-trcc-linux && pip install --break-system-packages -e . && pip install --break-system-packages pyusb && trcc setup-udev && trcc install-desktop && sudo steamos-readonly enable
```

### Then run

HID devices are auto-detected — no special flags needed:

```bash
trcc detect       # Check if your device is found
trcc gui          # Launch the GUI (HID auto-detected)
```

> **`trcc: command not found`?** Open a new terminal — pip installs to `~/.local/bin` which needs a new shell session to appear on PATH.

## What to report

Open an [issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues) with:

1. Your `lsusb` line (VID:PID and device name)
2. Output of `trcc detect --all`
3. Does the GUI launch and detect the device?
4. Can you send an image to the LCD? Does it display correctly?
5. Your distro and kernel version (`uname -r`)

Even a "it doesn't work" report is helpful — it tells me where the protocol breaks.

## How it works

HID devices use a different protocol than SCSI devices:

- **SCSI** (`87CD:70DB`, `0416:5406`, `0402:3922`) — USB Mass Storage, sends raw RGB565 pixels via `sg_raw`
- **HID Type 2** (`0416:5302`) — USB HID, DA/DB/DC/DD handshake, 512-byte aligned JPEG frames
- **HID Type 3** (`0418:5303`, `0418:5304`) — USB HID, F5 prefix, fixed-size frames with ACK
- **HID LED** (`0416:8001`) — USB HID, 64-byte reports for RGB LED color control

Resolution is auto-detected via the DA/DB/DC/DD handshake — the device reports its screen type, which maps to a resolution (240x240, 320x320, 480x480, etc.).

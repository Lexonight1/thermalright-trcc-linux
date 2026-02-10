# Device Testing Guide

The HID protocol is implemented with **563 automated tests** but **not fully validated with real hardware**. I only have a SCSI device (`87CD:70DB`). If you have an HID device, please help test.

## Supported HID Devices

Run `lsusb` and look for your VID:PID:

| VID:PID | lsusb shows | Protocol | Known Products |
|---------|-------------|----------|----------------|
| `0416:5302` | Winbond Electronics Corp. USBDISPLAY | HID Type 2 (LCD) | Trofeo Vision, AS120 VISION, BA120 VISION, FROZEN WARFRAME PRO |
| `0418:5303` | ALi Corp. LCD Display | HID Type 3 (LCD) | TARAN ARMS |
| `0418:5304` | ALi Corp. LCD Display | HID Type 3 (LCD) | TARAN ARMS |
| `0416:8001` | Winbond Electronics Corp. LED Controller | HID LED (RGB) | AX120 DIGITAL |

## Quick Start

Find your distro, copy the block, paste in terminal. These install TRCC + HID dependencies (libusb, pyusb) in one shot. After it finishes: **unplug and replug the USB cable**, then **open a new terminal**.

> **Already have TRCC installed?** Just run `pip install --break-system-packages pyusb` and then `trcc gui` (HID is auto-detected).

### Fedora / Nobara

```bash
sudo dnf install sg3_utils python3-pyqt6 ffmpeg libusb1-devel && pip install --break-system-packages trcc-linux pyusb && sudo trcc setup-udev && trcc install-desktop
```

### Ubuntu / Debian / Mint / Pop!_OS

```bash
sudo apt install sg3-utils python3-pyqt6 ffmpeg python3-pip libusb-1.0-0-dev && pip install --break-system-packages trcc-linux pyusb && sudo trcc setup-udev && trcc install-desktop
```

### Arch / Manjaro / EndeavourOS / CachyOS / Garuda

```bash
sudo pacman -S sg3_utils python-pyqt6 ffmpeg python-pip libusb && pip install --break-system-packages trcc-linux pyusb && sudo trcc setup-udev && trcc install-desktop
```

### openSUSE

```bash
sudo zypper install sg3_utils python3-qt6 ffmpeg python3-pip libusb-1_0-devel && pip install --break-system-packages trcc-linux pyusb && sudo trcc setup-udev && trcc install-desktop
```

### Void Linux

```bash
sudo xbps-install sg3_utils python3-PyQt6 ffmpeg python3-pip libusb-devel && pip install --break-system-packages trcc-linux pyusb && sudo trcc setup-udev && trcc install-desktop
```

### Gentoo

```bash
sudo emerge --ask sg3_utils dev-python/PyQt6 media-video/ffmpeg dev-python/pip dev-libs/libusb && pip install --break-system-packages trcc-linux pyusb && sudo trcc setup-udev && trcc install-desktop
```

### Alpine

```bash
sudo apk add sg3_utils py3-pyqt6 ffmpeg py3-pip python3 libusb-dev && pip install --break-system-packages trcc-linux pyusb && sudo trcc setup-udev && trcc install-desktop
```

### SteamOS (Steam Deck)

Switch to Desktop Mode, open Konsole:
```bash
sudo steamos-readonly disable && sudo pacman -S --needed sg3_utils python-pip python-pyqt6 ffmpeg libusb && pip install --break-system-packages trcc-linux pyusb && sudo trcc setup-udev && trcc install-desktop && sudo steamos-readonly enable
```

## Step 1: Run Detection

HID devices are auto-detected — no special flags needed:

```bash
trcc detect --all
```

You should see your device listed. Example:

```
* [1] No device path found — USBDISPLAY (HID) [0416:5302] (HID)
```

The "No device path found" is normal for HID devices — they don't use `/dev/sgX` like SCSI devices.

> **`trcc: command not found`?** Open a new terminal — pip installs to `~/.local/bin` which needs a new shell session to appear on PATH.

## Step 2: Run HID Debug

This is the most important step. The `hid-debug` command performs the handshake with your device and shows exactly what it reports:

```bash
trcc hid-debug
```

Example output:

```
HID Debug — Handshake Diagnostic
============================================================

Device: Winbond Electronics Corp. USBDISPLAY
  VID:PID = 0416:5302
  Type = 2
  Implementation = hid_lcd

  Attempting handshake...
  Handshake OK!
  PM byte  = 100 (0x64)
  SUB byte = 0 (0x00)
  FBL      = 100 (0x64)
  Serial   = ABCDEF0123456789
  Resolution = 320x320
  Button image = A1FROZEN WARFRAME PRO
  FBL 100 = known resolution

  Raw handshake response (first 64 bytes):
  0000: da db dc dd 64 00 00 00 00 00 00 00 01 00 00 00  ....d...........
  0010: 10 00 00 00 ab cd ef 01 23 45 67 89 ab cd ef 01  ........#Eg.....
  0020: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................
  0030: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................

============================================================
Copy the output above and paste it in your GitHub issue.
```

**What to look for:**

- **PM byte** — This identifies your product model. Known values: 5, 7, 9, 10, 11, 12, 32, 36, 50-53, 58, 64, 65, 100, 101
- **FBL** — Determines the LCD resolution. If it says "UNKNOWN", we need to add your device's mapping
- **Resolution** — Should match your LCD's actual pixel dimensions
- **Button image** — If it says "unknown PM=X", we need to add your product to the mapping table

If `hid-debug` shows an error like "Missing dependency", install pyusb:

```bash
pip install --break-system-packages pyusb
```

## Step 3: Try the GUI

```bash
trcc gui
```

Check:

1. Does the device show up in the left sidebar?
2. Does it show the correct product name/image, or a generic one?
3. Can you select a theme and send it to the LCD?
4. If "Send failed" appears, check the terminal output for error messages

## Troubleshooting

### "Send failed" in the GUI

This usually means the handshake succeeded but frame transfer failed. The most common causes:

1. **Wrong resolution detected** — The PM byte mapped to the wrong resolution, so frames are the wrong size. Run `trcc hid-debug` and share the output
2. **USB permissions** — Run `trcc setup-udev`, then unplug/replug the USB cable
3. **Missing pyusb** — Run `pip install --break-system-packages pyusb`

### "No HID devices found" in hid-debug

1. Make sure the USB cable is plugged in
2. Run `lsusb` — you should see your device listed
3. Run `trcc setup-udev` and unplug/replug the USB cable
4. Check if another process (like the Windows TRCC in a VM) is holding the USB device

### Device shows wrong name in sidebar

After the HID handshake, the sidebar button updates based on the PM (product mode) byte. If your device shows a wrong name (e.g. "TARAN ARMS" when it's a Trofeo Vision), it means we need to update the PM→product mapping. Run `trcc hid-debug` and share the PM byte.

### GUI opens but device isn't in the sidebar

1. Run `trcc detect --all` — if the device appears there but not in the GUI, it may be a routing issue
2. Run `trcc gui -vv` for debug logging and share the terminal output

## What to Report

Open an [issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues) with:

1. Your product name (e.g. "Trofeo Vision LCD")
2. Output of `lsusb | grep -i "0416\|0418\|87cd"`
3. **Full output of `trcc hid-debug`** (most important!)
4. Output of `trcc detect --all`
5. Does the GUI detect the device? Can you send themes?
6. Your distro and kernel version (`uname -r`)
7. Screenshot of the GUI if possible

Even a "it doesn't work" report is helpful — the `hid-debug` output tells us exactly where the protocol breaks.

## How It Works

HID devices use a different protocol than SCSI devices:

- **SCSI** (`87CD:70DB`, `0416:5406`, `0402:3922`) — USB Mass Storage, sends raw RGB565 pixels via `sg_raw`
- **HID Type 2** (`0416:5302`) — USB HID, DA/DB/DC/DD magic bytes, JPEG frames in 512-byte chunks
- **HID Type 3** (`0418:5303`, `0418:5304`) — USB HID, 0x65/0x66 prefix, fixed-size frames with ACK
- **HID LED** (`0416:8001`) — USB HID, 64-byte reports for RGB LED color/effect control

### Resolution Detection

The handshake response contains a **PM (Product Mode)** byte that identifies the device model. This maps through two tables:

1. **PM → FBL**: `pm_to_fbl()` in `hid_device.py` converts the product mode to an FBL (Feature Byte Length) code
2. **FBL → Resolution**: `fbl_to_resolution()` maps FBL to pixel dimensions

| PM | FBL | Resolution | Products |
|----|-----|------------|----------|
| 5 | 50 | 240x320 | |
| 7 | 64 | 640x480 | |
| 9, 11 | 224 | 854x480 | |
| 10 | 224 | 960x540 | |
| 12 | 224 | 800x480 | |
| 32 | 100 | 320x320 | |
| 36 | — | — | AS120 VISION |
| 50, 51 | — | — | FROZEN WARFRAME |
| 52, 53 | — | — | BA120 VISION |
| 58 | — | — | FROZEN WARFRAME SE |
| 64 | 114 | 1600x720 | |
| 65 | 192 | 1920x462 | |
| 100 | — | — | FROZEN WARFRAME PRO |
| 101 | — | — | ELITE VISION |

If your device reports a PM byte not in this table, we need to add it. The `trcc hid-debug` output tells us exactly what PM byte your device uses.

### All Supported LCD Resolutions

Theme data is bundled for all 15 resolutions. If a resolution's archive isn't included in the install, it downloads automatically from GitHub on first use.

| Resolution | FBL | Notes |
|------------|-----|-------|
| 240x240 | 36, 37 | Small square |
| 240x320 | 50 | Portrait |
| 320x320 | 100, 101, 102 | Most common |
| 360x360 | 54 | Medium square |
| 480x480 | 72 | Large square |
| 640x480 | 64 | VGA |
| 800x480 | 224 (PM=12) | Wide |
| 854x480 | 224 | Wide (default for FBL 224) |
| 960x540 | 224 (PM=10) | Wide |
| 1280x480 | 128 | Trofeo Vision |
| 1600x720 | 114 | Ultrawide |
| 1920x462 | 192 | Ultrawide |
| 480x800 | — | Portrait variant |
| 480x854 | — | Portrait variant |
| 540x960 | — | Portrait variant |

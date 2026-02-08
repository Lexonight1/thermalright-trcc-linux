# HID Device Testing Guide

[![Tests](https://github.com/Lexonight1/thermalright-trcc-linux/actions/workflows/tests.yml/badge.svg?branch=hid-protocol-testing)](https://github.com/Lexonight1/thermalright-trcc-linux/actions/workflows/tests.yml)

The HID protocol is implemented with **187 automated tests** (CI passing on Python 3.10, 3.11, 3.12) but **not tested against real hardware**. I only have a SCSI device (`87CD:70DB`). If you have an HID device, please help test.

## Supported HID Devices

Run `lsusb` and look for your VID:PID:

| VID:PID | lsusb shows | Protocol |
|---------|-------------|----------|
| `0416:5302` | Winbond Electronics Corp. USBDISPLAY | HID Type 2 |
| `0418:5303` | ALi Corp. LCD Display | HID Type 3 |
| `0418:5304` | ALi Corp. LCD Display | HID Type 3 |

## Install (fresh)

Install your distro's dependencies first (same as the stable branch — see [README](../README.md#install)), then clone the testing branch:

```bash
git clone -b hid-protocol-testing https://github.com/Lexonight1/thermalright-trcc-linux.git && cd thermalright-trcc-linux && pip install --break-system-packages -e . && sudo PYTHONPATH=src python3 -m trcc.cli setup-udev && trcc install-desktop
```

## Switch from stable

If you already have the repo cloned from `stable`:

```bash
cd thermalright-trcc-linux && git fetch origin && git checkout hid-protocol-testing && pip install --break-system-packages -e .
```

## Run

```bash
trcc detect       # Check if your device is found
trcc gui          # Launch the GUI
```

## What to report

Open an [issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues) with:

1. Your `lsusb` line (VID:PID and device name)
2. Output of `trcc detect`
3. Does the GUI launch and detect the device?
4. Can you send an image to the LCD? Does it display correctly?
5. Your distro and kernel version (`uname -r`)

Even a "it doesn't work" report is helpful — it tells me where the protocol breaks.

## How it works

HID devices use a different protocol than SCSI devices:

- **SCSI** (`87CD:70DB`, `0416:5406`, `0402:3922`) — USB Mass Storage, sends raw RGB565 pixels via `sg_raw`
- **HID Type 2** (`0416:5302`) — USB HID, DA/DB/DC/DD handshake, 512-byte aligned JPEG frames
- **HID Type 3** (`0418:5303`, `0418:5304`) — USB HID, F5 prefix, fixed-size frames with ACK

Resolution is auto-detected via the DA/DB/DC/DD handshake — the device reports its screen type, which maps to a resolution (240x240, 320x320, 480x480, etc.).

## Switch back to stable

```bash
git checkout stable
```

# Device Testing Guide

All 5 protocols (SCSI, HID, LED, Bulk, LY) are implemented with **5243 automated tests** across 91 test files. Several HID/LED devices have been validated by testers. If you have a device not listed below, please help test.

## Supported HID Devices

Run `lsusb` and look for your VID:PID:

| VID:PID | lsusb shows | Protocol | Known Products |
|---------|-------------|----------|----------------|
| `0416:5302` | Winbond Electronics Corp. USBDISPLAY | HID Type 2 (LCD) | Trofeo Vision, AS120 VISION, BA120 VISION, FROZEN WARFRAME PRO |
| `0418:5303` | ALi Corp. LCD Display | HID Type 3 (LCD) | TARAN ARMS |
| `0418:5304` | ALi Corp. LCD Display | HID Type 3 (LCD) | TARAN ARMS |
| `0416:8001` | Winbond Electronics Corp. LED Controller | HID LED (RGB) | AX120 DIGITAL, PA120 DIGITAL, HR10 2280 PRO DIGITAL, Phantom Spirit 120 Digital EVO, Assassin X 120R Digital ARGB, Peerless Assassin 120 Digital ARGB White |
| `0416:5408` | Winbond Electronics Corp. USBDISPLAY | LY Bulk (LCD) | Trofeo Vision 9.16 LCD |
| `0416:5409` | Winbond Electronics Corp. USBDISPLAY | LY1 Bulk (LCD) | |

## Quick Start

The fastest way to get started:

```bash
pip install trcc-linux pyusb
trcc system setup        # interactive wizard — checks all deps, installs udev rules, desktop entry
```

After setup finishes: **open a new terminal**. (If the device isn't picked up, restart your computer.)

> **Already have TRCC installed?** Just run `pip install pyusb` and then `trcc gui` (HID is auto-detected).

For per-distro manual install commands, see the [Install Guide](GUIDE_INSTALL.md).

## Step 1: Run Detection

HID devices are auto-detected — no special flags needed:

```bash
trcc detect --all
```

You should see your device listed. Example:

```text
* [1] No device path found — USBDISPLAY (HID) [0416:5302] (HID)
```

The "No device path found" is normal for HID devices — they don't use `/dev/sgX` like SCSI devices.

> **`trcc: command not found`?** Open a new terminal — pip installs to `~/.local/bin` which needs a new shell session to appear on PATH.

## Step 2: Run HID Debug

This is the most important step. The `hid-debug` command performs the handshake with your device and shows exactly what it reports:

```bash
trcc system hid-debug
```

Example output:

```text
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

1. **Wrong resolution detected** — The PM byte mapped to the wrong resolution, so frames are the wrong size. Run `trcc system hid-debug` and share the output
2. **USB permissions** — Run `trcc system setup`, then restart your computer if it still isn't detected
3. **Missing pyusb** — Run `pip install --break-system-packages pyusb`

### "No HID devices found" in hid-debug

1. Make sure the USB cable is plugged in
2. Run `lsusb` — you should see your device listed
3. Run `trcc system setup`, then restart your computer if it still isn't detected
4. Check if another process (like the Windows TRCC in a VM) is holding the USB device

### Device shows wrong name in sidebar

After the HID handshake, the sidebar button updates based on the PM (product mode) byte. If your device shows a wrong name (e.g. "TARAN ARMS" when it's a Trofeo Vision), it means we need to update the PM→product mapping. Run `trcc system hid-debug` and share the PM byte.

### GUI opens but device isn't in the sidebar

1. Run `trcc detect --all` — if the device appears there but not in the GUI, it may be a routing issue
2. Run `trcc gui -vv` for debug logging and share the terminal output

## What to Report

The fastest way to report is:

```bash
trcc report
```

This runs `lsusb`, `detect --all`, and `hid-debug` in one command. Copy-paste the entire output into a [GitHub issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues).

**Also include:**

1. Your product name (e.g. "Trofeo Vision LCD")
2. Does the GUI detect the device? Can you send themes?
3. Your distro and kernel version (`uname -r`)
4. Screenshot of the GUI if possible

Even a "it doesn't work" report is helpful — the `trcc report` output tells us exactly where the protocol breaks.

## How It Works

TRCC Linux supports 5 USB protocol types:

- **SCSI** (`87CD:70DB`, `0416:5406`, `0402:3922`) — USB Mass Storage, sends raw RGB565 pixels via `sg_raw`
- **HID Type 2** (`0416:5302`) — USB HID, DA/DB/DC/DD magic bytes, RGB565 frames with 20-byte header (512-byte aligned)
- **HID Type 3** (`0418:5303`, `0418:5304`) — USB HID, 0x65/0x66 prefix, fixed-size frames with ACK
- **HID LED** (`0416:8001`) — USB HID, 64-byte reports for RGB LED color/effect control
- **Bulk** (`87AD:70DB`) — Raw USB vendor-specific protocol via pyusb (GrandVision/Mjolnir Vision/Wonder Vision Pro/Frozen Warframe Pro)
- **LY** (`0416:5408`, `0416:5409`) — USB bulk protocol for LY-type LCDs (Trofeo Vision 9.16 LCD), 512-byte chunked frames with 16-byte headers

### Resolution Detection

The handshake response contains a **PM (Product Mode)** byte that identifies the device model. This maps through two tables:

1. **PM → FBL**: `pm_to_fbl()` in `core/models.py` converts the product mode to an FBL (Feature Byte Length) code
2. **FBL → Resolution**: `fbl_to_resolution()` in `core/models.py` maps FBL to pixel dimensions

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

If your device reports a PM byte not in this table, we need to add it. The `trcc system hid-debug` output tells us exactly what PM byte your device uses.

### FBL → Resolution Table

The FBL (Feature Byte Length) byte determines the LCD resolution. This is the complete mapping from `core/models.py`:

| FBL | Resolution | Encoding | Byte Order | Notes |
|-----|------------|----------|------------|-------|
| 36 | 240x240 | RGB565 | Little-endian | Small square |
| 37 | 240x240 | RGB565 | Little-endian | Small square (alt) |
| 50 | 320x240 | RGB565 | Little-endian | Portrait, PM=5 |
| 51 | 320x240 | RGB565 | Big-endian | Portrait, SPIMode=2 |
| 54 | 360x360 | JPEG | Little-endian | Medium square |
| 64 | 640x480 | RGB565 | Little-endian | VGA, PM=7 |
| 72 | 480x480 | RGB565 | Little-endian | Large square |
| 100 | 320x320 | RGB565 | Big-endian | Most common |
| 101 | 320x320 | RGB565 | Big-endian | FROZEN WARFRAME PRO |
| 102 | 320x320 | RGB565 | Big-endian | ELITE VISION |
| 114 | 1600x720 | JPEG | Little-endian | Ultrawide, PM=64 |
| 128 | 1280x480 | JPEG | Little-endian | Trofeo Vision |
| 192 | 1920x462 | JPEG | Little-endian | Ultrawide, PM=65 |
| 224 | 854x480 | JPEG | Little-endian | Wide (default) |
| 224 | 960x540 | JPEG | Little-endian | Wide, PM=10 |
| 224 | 800x480 | JPEG | Little-endian | Wide, PM=12 |

**FBL 224 disambiguation:** FBL 224 is shared by 3 resolutions. The PM byte determines the actual resolution: PM=10 → 960x540, PM=12 → 800x480, all others → 854x480.

**JPEG mode FBLs:** 54, 114, 128, 192, 224 use JPEG encoding instead of raw RGB565. Header byte[6] = 0x00 (JPEG) vs 0x01 (RGB565).

### PM → FBL Overrides

For most devices, PM=FBL (the PM byte is the FBL directly). These PM values map to a different FBL:

| PM | FBL | Resolution | Products |
|----|-----|------------|----------|
| 5 | 50 | 320x240 | |
| 7 | 64 | 640x480 | |
| 9 | 224 | 854x480 | |
| 10 | 224 | 960x540 | |
| 11 | 224 | 854x480 | |
| 12 | 224 | 800x480 | |
| 32 | 100 | 320x320 | |
| 36 | 36 | 240x240 | AS120 VISION |
| 50 | 50 | 320x240 | FROZEN WARFRAME |
| 51 | 51 | 320x240 | FROZEN WARFRAME |
| 52 | 52 | — | BA120 VISION |
| 53 | 53 | — | BA120 VISION |
| 58 | 58 | — | FROZEN WARFRAME SE |
| 64 | 114 | 1600x720 | |
| 65 | 192 | 1920x462 | |
| 100 | 100 | 320x320 | FROZEN WARFRAME PRO |
| 101 | 101 | 320x320 | ELITE VISION |
| 128 | 128 | 1280x480 | Trofeo Vision |

**Compound key:** PM=1 + SUB=48 → FBL 114 (1600x720), PM=1 + SUB=49 → FBL 192 (1920x462).

### All Supported Theme Resolutions

Theme data is bundled for all 16 resolutions. If a resolution's archive isn't included in the install, it downloads automatically from GitHub on first use.

| Resolution | Source FBLs | Notes |
|------------|-------------|-------|
| 240x240 | 36, 37 | Small square |
| 320x240 | 50, 51 | Portrait |
| 320x320 | 100, 101, 102 | Most common |
| 360x360 | 54 | Medium square |
| 480x480 | 72 | Large square |
| 640x480 | 64 | VGA |
| 800x480 | 224 (PM=12) | Wide |
| 854x480 | 224 | Wide (default for FBL 224) |
| 960x540 | 224 (PM=10) | Wide |
| 1280x480 | 128 | Trofeo Vision |
| 1600x720 | 114 | Ultrawide, split mode |
| 1920x462 | 192 | Ultrawide |
| 480x800 | — | Portrait variant |
| 480x854 | — | Portrait variant |
| 540x960 | — | Portrait variant |

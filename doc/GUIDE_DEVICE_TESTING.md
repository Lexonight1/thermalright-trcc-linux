# Device Testing Guide

All 5 protocols (SCSI, HID, LED, Bulk, LY) are implemented and covered by the automated test suite. Several HID/LED devices have been validated by testers. If you have a device not listed below, please help test.

## Supported HID Devices

Run `lsusb` and look for your VID:PID:

| VID:PID | lsusb shows | Protocol | Known Products |
|---------|-------------|----------|----------------|
| `0416:5302` | Winbond Electronics Corp. USBDISPLAY | HID Type 2 (LCD) | Trofeo Vision, AS120 VISION, BA120 VISION, FROZEN WARFRAME PRO |
| `0418:5303` | AST Research LCD Display | HID Type 3 (LCD) | TARAN ARMS |
| `0418:5304` | AST Research LCD Display | HID Type 3 (LCD) | TARAN ARMS |
| `0416:8001` | Winbond Electronics Corp. LED Controller | HID LED (RGB) | AX120 DIGITAL, PA120 DIGITAL, HR10 2280 PRO DIGITAL, Phantom Spirit 120 Digital EVO, Assassin X 120R Digital ARGB, Peerless Assassin 120 Digital ARGB White |
| `0416:5408` | Winbond Electronics Corp. USBDISPLAY | LY Bulk (LCD) | Trofeo Vision 9.16 LCD |
| `0416:5409` | Winbond Electronics Corp. USBDISPLAY | LY1 Bulk (LCD) | |

## Quick Start

The fastest way to get started:

```bash
pip install trcc-linux   # pyusb comes with it
trcc system setup        # one-time setup — udev rules, SELinux policy (if enforcing), desktop entry; applies without asking (sudo asks for your password)
```

`trcc system setup --dry-run` shows what it would change without changing anything.

After setup finishes: **open a new terminal**. (If the device isn't picked up, restart your computer.)

> **Already have TRCC installed?** Just run `trcc gui` (HID is auto-detected).

For per-distro manual install commands, see the [Install Guide](GUIDE_INSTALL.md).

## Step 1: Run Detection

HID devices are auto-detected — no special flags needed:

```bash
trcc detect
```

You should see your device listed. Example:

```text
1 device(s) found:
  0416:5302  Winbond USBDISPLAY  (wire=hid, resolution=unknown)
      this USB id covers several coolers — 'trcc device connect 0416:5302' names yours
Resolutions above come from the catalog, not the panel.  Run 'trcc device connect <key>' to read the panel's own.
```

`wire=hid` is expected here — HID devices don't use `/dev/sgX` like SCSI devices do. `detect` never talks to the panel: the resolution is the catalog's, printed as `W×H (catalog)`, or `unknown` when one USB ID covers panels of several sizes (as `0416:5302` does). The panel's real resolution comes from the handshake in Step 2.

> **`trcc: command not found`?** Open a new terminal — pip installs to `~/.local/bin` which needs a new shell session to appear on PATH.

## Step 2: Run HID Debug

This is the most important step. The `hid-debug` command performs the handshake with your device and shows what it reports. It takes the device key from Step 1:

```bash
trcc system hid-debug 0416:5302
```

The output has this shape (values depend on your panel):

```text
HID/LCD diagnostic for 0416:5302
============================================================
Handshake: <the connect result>
  resolution: <W>x<H>
  model_id:   <PM byte on HID Type 2; FBL on HID Type 3>
  serial:     <only if the panel reports one>
  sub_byte:   <SUB byte>

Persisted state:
  orientation:     <degrees>
  brightness:      <percent>%
  current_theme:   <theme>
  overlay_enabled: <True/False>

============================================================
Copy the output above into a GitHub issue.
```

If the handshake fails, only the `Handshake:` line is printed and the command exits with status 1.

**What to look for:**

- **resolution** — Should match your LCD's actual pixel dimensions
- **model_id** and **sub_byte** — These identify your product model and select the resolution and the wire rotation
- If the panel reports a byte TRCC does not know, the log gets a WARNING starting `get_profile: UNKNOWN` naming the byte and the size TRCC assumed. `trcc report` (below) includes it — that line is what we need to add your device.

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

1. **Wrong resolution detected** — The PM byte mapped to the wrong resolution, so frames are the wrong size. Run `trcc system hid-debug <key>` and share the output
2. **USB permissions** — Run `trcc system setup`, then restart your computer if it still isn't detected

### hid-debug fails at the handshake

1. Make sure the USB cable is plugged in
2. Run `lsusb` — you should see your device listed
3. Run `trcc system setup`, then restart your computer if it still isn't detected
4. Check if another process (like the Windows TRCC in a VM) is holding the USB device

### Device shows wrong name in sidebar

After the HID handshake, the sidebar button updates based on the PM (product mode) byte. If your device shows a wrong name (e.g. "TARAN ARMS" when it's a Trofeo Vision), it means we need to update the PM→product mapping. Run `trcc system hid-debug <key>` and share its output.

### GUI opens but device isn't in the sidebar

1. Run `trcc detect` — if the device appears there but not in the GUI, it may be a routing issue
2. Run `trcc -vv gui` for debug logging and share the terminal output

## What to Report

The fastest way to report is:

```bash
trcc report
```

That is an alias for `trcc system debug-report`. It gathers the install and
platform details, resolved paths, detected devices, the handshake probe
(PM byte, resolution, serial), sensor readings, powercap state, your
settings, the health checks, and the tail of `~/.trcc/trcc.log` — everything
needed to diagnose a device, in one dump. Copy-paste the entire output into a
[GitHub issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues),
or use `trcc report -o report.txt` to write it to a file.

**Also include:**

1. Your product name (e.g. "Trofeo Vision LCD")
2. Does the GUI detect the device? Can you send themes?
3. Your distro and kernel version (`uname -r`)
4. Screenshot of the GUI if possible

Even a "it doesn't work" report is helpful — the `trcc report` output tells us exactly where the protocol breaks.

## How It Works

TRCC Linux supports 5 USB protocol types:

- **SCSI** (`87CD:70DB`, `0402:3922`) — USB Mass Storage; on Linux, frames go to `/dev/sgN` through the kernel's SG_IO ioctl
- **HID Type 2** (`0416:5302`) — USB HID, DA/DB/DC/DD magic bytes, RGB565 frames with 20-byte header (512-byte aligned)
- **HID Type 3 / F5** (`0418:5303`, `0418:5304`, `0416:5406`) — 16-byte `F5 00` handshake / `F5 01` frame headers, fixed-size frames with ACK; 0x65/0x66 are identity values in the panel's reply, not a prefix (`VALID_IDENTITY` in `adapters/device/_f5.py`)
- **HID LED** (`0416:8001`) — USB HID, 64-byte reports for RGB LED color/effect control
- **Bulk** (`87AD:70DB`) — Raw USB vendor-specific protocol via pyusb (GrandVision/Mjolnir Vision/Wonder Vision Pro/Frozen Warframe Pro)
- **LY** (`0416:5408`, `0416:5409`) — USB bulk protocol for LY-type LCDs (Trofeo Vision 9.16 LCD), 512-byte chunked frames with 16-byte headers

### Resolution Detection

The handshake response contains a **PM (Product Mode)** byte that identifies the device model, and a **SUB** byte. They map through tables in `src/trcc/core/protocol.py`, which is the source of truth — this guide does not copy them:

1. **PM → FBL**: `pm_to_fbl()` returns the FBL (Feature Byte Length) code. By default PM = FBL; the exceptions are `_PM_TO_FBL_OVERRIDES`, and `_PM_SUB_TO_FBL` holds the (PM, SUB) pairs checked first (PM=1 + SUB=48 → FBL 114, 1600x720; PM=1 + SUB=49 → FBL 192, 1920x462).
2. **FBL → profile**: `get_profile()` looks the FBL up in `FBL_PROFILES` — resolution, RGB565 or JPEG (`jpeg`), byte order (`big_endian`), and whether the frame is rotated for the wire. `fbl_to_resolution()` returns just the size.

**Shared FBLs:** FBL 224 and FBL 192 each cover several resolutions, and the PM byte picks one (`_FBL_224_BY_PM`, `_FBL_192_BY_PM`). FBL 224 falls back to 854x480 and FBL 192 to 1920x462 for a PM not in its table — and logs a `get_profile: UNKNOWN PM=…` WARNING when it does. An FBL missing from `FBL_PROFILES` falls back to 320x320 big-endian with the same kind of WARNING.

**JPEG vs RGB565 on HID Type 2:** header byte[6] = 0x00 (JPEG) vs 0x01 (RGB565).

If your device reports a PM byte TRCC does not know, the WARNING in `trcc report` names it, and `trcc system hid-debug <key>` shows the resolution TRCC resolved.

### Theme Resolutions

Theme data is not inside the pip package. When a device connects, TRCC downloads the archives for its resolution from GitHub in the background (`theme{W}{H}.7z`, `web/{W}{H}.7z`, `web/zt{W}{H}.7z` under [`src/trcc/data/`](../src/trcc/data/)). That directory is the list of supported theme resolutions, portrait and split-mode variants included.

# TRCC Linux - Technical Reference

## Overview

Linux port of the Thermalright TRCC application for controlling LCD displays on CPU coolers.

## Supported Devices

The source of truth is `ALL_DEVICES` in `src/trcc/core/registry.py`; these tables mirror it.

| VID    | PID    | Vendor      | Product           |
|--------|--------|-------------|-------------------|
| 0x87CD | 0x70DB | Thermalright| LCD Display       |
| 0x0402 | 0x3922 | Thermalright| LCD Display       |

SCSI devices appear as SCSI Generic (`/dev/sgX`) with vendor "USBLCD".

### Bulk USB Devices

| VID    | PID    | Vendor      | Product           |
|--------|--------|-------------|-------------------|
| 0x87AD | 0x70DB | ChiZhu Tech | GrandVision 360 AIO |

Bulk devices use raw USB vendor-specific transfers via PyUSB. Products: GrandVision 360 AIO, Mjolnir Vision 360, Wonder Vision Pro 360.

### HID Devices

| VID    | PID    | Protocol | Type | Notes |
|--------|--------|----------|------|-------|
| 0x0416 | 0x5302 | HID      | 2 (H)   | LCD display via HID bulk transfer |
| 0x0416 | 0x5406 | HID      | 3 (F5)  | LCD display via F5 bulk transfer |
| 0x0418 | 0x5303 | HID      | 3 (ALi) | LCD display via HID bulk transfer |
| 0x0418 | 0x5304 | HID      | 3 (ALi) | LCD display via HID bulk transfer |

### HID LED Devices

| VID    | PID    | Protocol | Type | Notes |
|--------|--------|----------|------|-------|
| 0x0416 | 0x8001 | LED      | HID 64-byte | RGB LED controller (AX120 DIGITAL, PA120 DIGITAL, Phantom Spirit 120 Digital EVO, Peerless Assassin 120 Digital ARGB White) |

LED devices are distinguished from LCD HID devices by their registry row: `wire=Wire.LED, kind=Kind.LED`.

## Display Resolutions

### FBL (Feature Byte Length) Detection

The Windows app uses FBL values to identify display resolution. The source of truth is `FBL_PROFILES` in `src/trcc/core/protocol.py` (FBLs 192 and 224 are split by PM in `_FBL_192_BY_PM` / `_FBL_224_BY_PM`). FBL mapping:

| FBL | Resolution | Notes |
|-----|------------|-------|
| 36, 37 | 240x240 | Small |
| 49 | 320x240 | Big-endian RGB565 (SPI mode 2), rotate |
| 50 | 320x240 | Rotate |
| 51 | 320x240 | HID Type 2, rotate |
| 52 | 320x240 | Rotate |
| 53 | 320x240 | HID Type 2, rotate |
| 54 | 360x360 | JPEG (RGB565 at SUB 4) |
| 58 | 320x240 | Rotate |
| 59 | 640x172 | RGB565, rotate (the HID route to the 640x172 panel) |
| 60 | 320x176 | Rotate |
| 64 | 640x480 | Rotate |
| 72 | 480x480 | Large square |
| 100-102 | 320x320 | Big-endian RGB565 (default) |
| 114 | 1600x720 | JPEG, rotate |
| 128 | 1280x480 | JPEG, rotate (Trofeo Vision) |
| 129 | 480x480 | Alias for FBL 72 |
| 192 | 1920x462 | JPEG, rotate. PM disambiguates: 68→1280x480, 69→1920x440 |
| 224 | 854x480 | JPEG, rotate. PM disambiguates: 9/11/20→854x480, 10/16→960x540, 12→800x480, 13/17/18→960x320, 15→640x172 |

### PM → FBL Mapping (HID Type 2, Bulk and LY Devices)

HID Type 2, bulk and LY devices don't report FBL directly. Instead, the PM (product mode) byte from the handshake maps to FBL. The source of truth is `_PM_TO_FBL_OVERRIDES` and `_PM_SUB_TO_FBL` in `src/trcc/core/protocol.py`; a PM not listed maps to FBL = PM, except on bulk and LY devices, where it stays on FBL 72 (480x480).

| PM | FBL | Resolution | Notes |
|----|-----|------------|-------|
| 5 | 50 | 320x240 | |
| 7 | 64 | 640x480 | |
| 9 | 224 | 854x480 | |
| 10 | 224 | 960x540 | PM disambiguates FBL 224 |
| 11 | 224 | 854x480 | |
| 12 | 224 | 800x480 | PM disambiguates FBL 224 |
| 13 | 224 | 960x320 | PM disambiguates FBL 224 |
| 14 | 64 | 640x480 | |
| 15 | 224 | 640x172 | PM disambiguates FBL 224 |
| 16 | 224 | 960x540 | PM disambiguates FBL 224 |
| 17 | 224 | 960x320 | PM disambiguates FBL 224 |
| 18 | 224 | 960x320 | PM disambiguates FBL 224 |
| 20 | 224 | 854x480 | |
| 32 | 100 | 320x320 | |
| 50 | 50 | 320x240 | SPI mode 2 |
| 63 | 114 | 1600x720 | |
| 64 | 114 | 1600x720 | |
| 65 | 192 | 1920x462 | |
| 66 | 192 | 1920x462 | |
| 68 | 192 | 1280x480 | PM disambiguates FBL 192 |
| 69 | 192 | 1920x440 | PM disambiguates FBL 192 |
| 1+sub=48 | 114 | 1600x720 | PM=1 with SUB byte variant |
| 1+sub=49 | 192 | 1920x462 | PM=1 with SUB byte variant |

Functions: `pm_to_fbl()` and `fbl_to_resolution()` in `core/protocol.py`.

### Theme Directories & Archives

Stock themes, cloud previews and cloud masks ship as `.7z` archives under `src/trcc/data/` in the repository: `theme{w}{h}.7z` for stock themes, `web/{w}{h}.7z` for cloud previews and `web/zt{w}{h}.7z` for cloud masks. The installer, `adapters/repo/data_install.py`, downloads the archives for a device's resolution from GitHub (`raw.githubusercontent.com`), extracts them with the system `7z` binary, and places them under `~/.trcc/data/` as `theme{w}{h}/`, `web/{w}{h}/` and `web/zt{w}{h}/`. Non-square panels also get the rotated counterpart's previews and masks. A directory that is already populated is not fetched again.

Each theme subdirectory contains:
- `00.png` - Background image (sent to LCD)
- `01.png` - Mask overlay
- `config1.dc` - Theme configuration
- `Theme.png` - Preview thumbnail

Mask-only themes (in `zt*/` directories) omit `00.png`.

## Protocol

### SCSI Commands

On Linux, all communication goes to `/dev/sgX` through the `SG_IO` ioctl, one call per CDB (`LinuxScsiTransport` in `adapters/system/linux.py`). Source: reverse-engineered from `USBLCD.exe` (native C++/MFC) via Ghidra decompilation.

**CDB format (16 bytes):**
```text
bytes[0:3]   = command (LE uint32)
bytes[4:11]  = zeros
bytes[12:15] = data size (LE uint32)
```
These 16 bytes are the SCSI CDB. trcc sends no CRC32 (`ScsiLcd._build_cdb` in `adapters/device/scsi_lcd.py`).

### CDB Byte-Level Structure

The 4-byte command field encodes a structured protocol:

```text
byte[0] = 0xF5   (always — protocol marker)
byte[1] = sub-command:
    0x00 = poll/read
    0x01 = write/send
    0x02 = flash erase
    0x04 = flash info query
    0x05 = flash status query
    0x41 = 'A' (API identification, with bytes[2]='P', bytes[3]='I')
byte[2] = mode (when byte[1]=0x01):
    0x00 = init (send 0xE100 zeros)
    0x01 = raw frame chunk (byte[3] = chunk index)
    0x02 = compressed frame (zlib level 3)
    0x03 = multi-frame carousel (compressed)
    0x04 = display clear
byte[3] = chunk/frame index
```

### Complete SCSI Command Table

| Command (LE) | CDB Bytes | Direction | Size | Purpose |
|---|---|---|---|---|
| `0x000000F5` | F5 00 00 00 | READ | 0xE100 | Poll device status |
| `0x000001F5` | F5 01 00 00 | WRITE | 0xE100 | Initialize display |
| `0x000101F5` | F5 01 01 00 | WRITE | 0x10000 | Frame chunk 0 (64 KiB) |
| `0x010101F5` | F5 01 01 01 | WRITE | 0x10000 | Frame chunk 1 (64 KiB) |
| `0x020101F5` | F5 01 01 02 | WRITE | 0x10000 | Frame chunk 2 (64 KiB) |
| `0x030101F5` | F5 01 01 03 | WRITE | varies | Frame chunk 3 (remainder) |
| `0x000201F5` | F5 01 02 00 | WRITE | varies | Compressed frame (zlib) |
| `0x000301F5` | F5 01 03 xx | WRITE | varies | Multi-frame carousel |
| `0x000401F5` | F5 01 04 00 | WRITE | 0xE100 | Display clear |
| `0x000002F5` | F5 02 00 00 | WRITE | 0x10000 | NOR flash erase (sector) |
| `0x000004F5` | F5 04 00 00 | READ | 0x10 | NOR flash info |
| `0x000005F5` | F5 05 00 00 | READ | 4 | Flash status |
| `0x495041F5` | F5 41 50 49 | READ | 0xC | Device API identification |

**Flash/firmware commands** (0x02, 0x04, 0x05 sub-commands and the byte-level variants F5 00/01/02/04/05) are used by USBLCD.exe's firmware update GUI — not needed for normal display operation.

### Initialization Sequence

```text
1. Poll:  0xF5   READ  0xE100 bytes → check device ready, detect resolution
2. Init:  0x1F5  WRITE 0xE100 zeros → initialize display controller
```

### Poll Response

The 0xE100-byte poll response encodes device state:

| Byte Offset | Value | Meaning |
|---|---|---|
| 0 | `'$'` (0x24) | 240×240 display (mode 1) |
| 0 | `'2'` (0x32) | 320×240 display (mode 2) |
| 0 | `'3'` (0x33) | 320×240 display (mode 2) |
| 0 | `'d'` (0x64) | 320×320 display (mode 3) |
| 0 | `'e'` (0x65) | 320×320 display (mode 3) |
| 4-7 | `0xA1A2A3A4` | Device still booting (wait 3s, re-poll) |

### Frame Transfer

Frame chunks are 64 KiB each (except the last); panels of 76,800 pixels (320×240) or fewer use 0xE100-byte chunks instead. Chunk count depends on resolution:

| Resolution | RGB565 Size | Chunks | Chunk Sizes |
|---|---|---|---|
| 240×240 | 115,200 (0x1C200) | 2 | 0xE100 + 0xE100 |
| 320×240 | 153,600 (0x25800) | 3 | 0xE100 + 0xE100 + 0x9600 |
| 320×320 | 204,800 (0x32000) | 4 | 0x10000 × 3 + 0x2000 |

**Important:** Initialize ONCE, then stream frames without re-init.

### Compressed Frame Transfer

USBLCD.exe supports zlib-compressed frames. trcc uses them only to upload a boot animation to device flash (`ScsiLcd.send_boot_animation`, `trcc display boot-anim`), on 240×240, 240×320, 320×240 and 320×320 panels, with 1-248 frames:

1. **First frame**: Compress RGB565 data with zlib level 3, send via `0x201F5`. CDB `bytes[8:11]` = frame count, `bytes[12:15]` = compressed size. Sleep 500ms.
2. **Multi-frame carousel**: Each frame compressed individually, sent via `0x301F5 | (delay_byte << 24)`, where the delay byte is the dwell time in deciseconds × 10, capped at 250. CDB `bytes[8:11]` = frame index, `bytes[12:15]` = compressed size. Sleep 10ms between frames.

### Display Clear

Send `0x401F5` with 0xE100 bytes to clear the LCD to black.

### Device Identification (API Query)

The `0x495041F5` ("F5API") command reads 12 bytes of device signature:
- CDB: `F5 41 50 49 58 B3 00 00 0C 00 00 00 ...` (with magic bytes 0xB358)
- Response: 12-byte device signature
- `response[2:3]` identifies device variant:
  - `(0x01, 0x39)` and `(0x03, 0x36)` are known variants
  - `response[5] == 0x10` determines sub-type

This query is sent after standard SCSI INQUIRY (CDB 0x12) during device detection.

### What SCSI Does NOT Control

USBLCD.exe contains **no commands** for:
- **Brightness** — handled by TRCC.exe via image pre-processing (gamma/level adjustment before RGB565 conversion)
- **Rotation** — handled by TRCC.exe via image rotation before sending
- **Display on/off** — only display clear (0x401F5) exists; no standby/wake command

For SCSI devices, brightness and rotation are purely software-side operations.

### Pixel Format

RGB565, 2 bytes/pixel. Big-endian on 320×320 panels (FBL 100-102) and on SCSI FBL 51; little-endian on the other SCSI panels (FBL 36, 37, 50):
```python
pixel = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
```

### Windows IPC (TRCC.exe ↔ USBLCD.exe)

On Windows, TRCC.exe and USBLCD.exe communicate via shared memory (`shareMemory_Image`):

| Offset | Size | Purpose |
|---|---|---|
| 0x0000 | 1 | Resolution code from poll (or 0x00 when ready to send) |
| 0x0001 | 1 | Frame count (1=single, N>1=multi-frame carousel) |
| 0x0002 | 1 | Send trigger (TRCC sets 1, USBLCD clears to 0 after send) |
| 0x0003 | 1 | Display clear flag (0x7F = clear) |
| 0x0004-0x0007 | 4 | Boot signature check (0xA1A2A3A4 = booting) |
| 0x257FE-0x257FF | 2 | Resolution echo (0xDC, resolution_code) |
| 0x25800+ | varies | RGB565 image data |

On Linux, this IPC layer is unnecessary — `trcc` talks directly to `/dev/sgX` via `SG_IO`.

## LED Protocol (HID 64-byte Reports)

LED devices communicate via 64-byte HID reports (matching Windows FormLED).

### Handshake

The LED handshake reads a PM (product mode) byte from the device, which maps to an LED style:

The source of truth is `_PM_REGISTRY` in `src/trcc/core/led_protocol.py` (PM → style), with LED counts from `LED_STYLES` in `src/trcc/core/led_models.py`; the Style column is `LEGACY_STYLE_ID`.

| PM | Style | Model | LEDs | Segments |
|----|-------|-------|------|----------|
| 1  | 1     | FROZEN HORIZON PRO | 30 | 10 |
| 2  | 1     | FROZEN MAGIC PRO | 30 | 10 |
| 3  | 1     | AX120 DIGITAL | 30 | 10 |
| 16-22, 24-31 | 2 | PA120 DIGITAL | 84 | 18 |
| 23 | 2     | RK120 DIGITAL | 84 | 18 |
| 32 | 3     | AK120 DIGITAL | 64 | 10 |
| 48 | 5     | LF8 | 93 | 23 |
| 49 | 5     | LF10 (LF8 layout) | 93 | 23 |
| 80 | 6     | LF12 | 124 | 72 |
| 96 | 7     | LF10 | 116 | 12 |
| 112| 9     | LC2 | 61 | 31 |
| 128| 4     | LC1 | 31 | 14 |
| 129| 10    | LF11 | 38 | 17 |
| 144| 11    | LF15 | 93 | 72 |
| 160| 12    | LF13 | 62 | 62 |
| 176| 5     | LF25 (LF8 layout, wire sub-variant 1) | 93 | 23 |
| 208| 8     | CZ1 | 18 | 13 |
| 208| 13    | Magic Qube | 65 | 14 |

The Magic Qube reports PM=208 like the CZ1, so it is not told apart by the PM
byte. It is disambiguated by its handshake header `DC DD AA 01` (the CZ1 answers
with the standard `DA DB DC DD`); the header override is checked before the PM
registry. It is a Linux-added device — absent from the Windows TRCC app —
reverse-engineered and hardware-validated by [@jphilipb](https://github.com/jphilipb).

### LED Packet Format

```text
Bytes 0-3:   Magic (DA DB DC DD)
Bytes 4-11:  Zeros
Byte 12:     Command (0x02 = LED data; 0x01 = handshake)
Bytes 13-15: Zeros
Bytes 16-17: Payload length (LE uint16) = LED count × 3
Bytes 18-19: Zeros
Bytes 20-N:  Per-LED data: [R, G, B] × LED count, each channel × 0.4 (an off LED is 0, 0, 0)
```

The packet goes out as 64-byte HID reports, the last one zero-padded (`Led` in `adapters/device/led.py`).

### LED Effect Modes

The source of truth is `LEDMode` in `src/trcc/core/led_models.py`.

| Mode | Name | Description |
|------|------|-------------|
| 0 | Static | Solid color on all segments |
| 1 | Breathing | Fade in/out cycle |
| 2 | Colorful | 6-phase gradient cycle |
| 3 | Rainbow | 768-entry color table shifted across segments |
| 4 | Temp-linked | Color from CPU/GPU temperature |
| 5 | Load-linked | Color from CPU/GPU load |
| 6 | OpenRGB | The colours OpenRGB sends over DDP (TRCC's own; not in the Windows app) |

## Bulk USB Protocol

Bulk devices (`87AD:70DB`) use raw USB vendor-specific transfers via PyUSB, bypassing the kernel's USB Mass Storage / SCSI stack entirely.

### Handshake

Write a 64-byte request (magic `12 34 56 78`, byte 56 = `0x01`), read 1024 bytes. A reply with `resp[24] == 0` fails the handshake. PM = `resp[24]`, SUB = `resp[36]`. The PM goes through the same PM→FBL table as HID Type 2 (`bulk_profile` in `adapters/device/bulk_lcd.py`); a PM outside that table stays on FBL 72 (480×480).

### Frame Transfer

Each frame is a 64-byte header followed by the payload. Header fields are LE uint32: bytes 0-3 magic `12 34 56 78`, 4-7 command (2 = JPEG, 3 = RGB565), 8-11 width, 12-15 height, 56-59 = 2, 60-63 payload length. Every PM encodes JPEG except PM 32 and 50, which send RGB565 (PM 50 big-endian). The frame goes out in 16 KiB bulk writes, followed by a zero-length packet when its length is a multiple of 512.

### Known Products

| Product | Resolution |
|---------|------------|
| GrandVision 360 AIO | 480×480 |
| Mjolnir Vision 360 | 480×480 |
| Wonder Vision Pro 360 | 480×480 |
| Frozen Warframe Pro | 480×480 |

## LY Bulk Protocol

LY devices (`0416:5408` and `0416:5409`) use a chunked USB bulk protocol distinct from the raw bulk protocol above. Source: reverse-engineered from USBLCDNEW.exe.

### Handshake

Write 2048 bytes (16-byte header `02 FF 00 00 00 00 00 00 01 00 00 00 00 00 00 00` + 2032 zeros), read 512. The reply is valid when `resp[0] == 0x03`, `resp[1] == 0xFF` and `resp[8] == 0x01`. PM and SUB come from the reply (see PID Variants) and resolve through the same `bulk_profile` as bulk devices.

### Frame Transfer

Frames are encoded as on bulk devices (JPEG for every PM except 32 and 50) and split into 512-byte chunks:

```text
Each chunk (512 bytes):
  Bytes 0-15:   Header (16 bytes)
    [0]:        0x01
    [1]:        0xFF
    [2-5]:      total payload length (LE uint32)
    [6-7]:      this chunk's data length (LE uint16: 496, the remainder in the last chunk)
    [8]:        0x01 (LY) / 0x02 (LY1)
    [9-10]:     chunk count (LE uint16)
    [11-12]:    chunk index (LE uint16)
    [13-15]:    zeros
  Bytes 16-511: Data payload (496 bytes)
```

Chunk count = payload length ÷ 496 + 1. On LY the buffer is padded with empty chunks to a multiple of 4. It goes out in 4096-byte writes (on LY a final write under 4096 bytes is 2048), then a 512-byte ACK is read (`adapters/device/ly_lcd.py`).

### PID Variants

| PID | PM Formula | Notes |
|-----|-----------|-------|
| `0x5408` (LY) | `64 + response[20]` (a value ≤ 3 is read as 1) | SUB = `response[22] + 1` |
| `0x5409` (LY1) | `49 + response[20]` | SUB = `response[22]` |

### Known Products

| Product | Resolution |
|---------|------------|
| Trofeo Vision 9.16 LCD | 1920×462 |

---

## Architecture

### Windows TRCC Architecture (Reference)

The original Windows application is organized into these namespaces:

| Namespace | Purpose | Key Files |
|-----------|---------|-----------|
| **TRCC** | Main application shell | `Form1.cs` (main window 1454×800), `FormStart.cs` (splash), `Program.cs`, `UCDevice.cs` (sidebar), `UCAbout.cs` |
| **TRCC.CZTV** | LCD/Color Screen Controller | `FormCZTV.cs` (per-device controller), `FormGetColor.cs` (screen color picker), `FormScreenImage.cs`, `FormScreenshot.cs` |
| **TRCC.DCUserControl** | Reusable UI Components | 50+ `UC*.cs` files for all UI widgets |
| **TRCC.LED** | LED Strip Controller | `FormLED.cs` |
| **TRCC.KVMALED6** | KVM + ARGB LED (6-port) | `FormKVMALED6.cs` |
| **TRCC.Properties** | Resources & Settings | `Resources.cs` (670 embedded bitmaps), `Settings.cs` |

**CZTV** = **C**olor **Z**hong (彩屏) **T**ube/**V**ideo - "Color Screen Display"

### Windows UI Specifications

| Component | Size | Layout |
|-----------|------|--------|
| Main Window | 1454×800 | Borderless, RGB(35,34,39) = `#232227` |
| UCDevice (sidebar) | 180×800 | Left side at (0,0) |
| Content area | 1274×800 | Right side at (180,0) |
| Theme grid | 732×652 | 5 cols, 120×120 thumbnails, 135×150 spacing |
| Overlay grid | ~490×430 | 7×6 fixed grid (42 max), 60×60 elements, 67×66 spacing |
| Color panel | 230×374 | 11 preset colors + font settings |

### Windows Hardware Category Colors

From `UCSystemInfoOptionsOne.cs`:

| Category | Color | RGB |
|----------|-------|-----|
| CPU | `#32C5FF` | RGB(50, 197, 255) cyan |
| GPU | `#44D7B6` | RGB(68, 215, 182) teal |
| MEM | `#6DD401` | RGB(109, 212, 1) lime |
| HDD | `#F7B501` | RGB(247, 181, 1) amber |
| NET | `#FA6401` | RGB(250, 100, 1) orange |
| FAN | `#E02020` | RGB(224, 32, 32) red |

### Windows Resource Naming

670 embedded bitmap resources with localization suffixes:
- (none) = Chinese
- `d` = German, `e` = Spanish, `en` = English, `f` = French
- `p` = Portuguese, `r` = Russian, `tc` = Traditional Chinese, `x` = Japanese

Prefixes: `A0` (startup), `A1` (device images), `A2` (dropdowns), `D0` (device panels), `P` (UI buttons/panels)

### Linux Port Files

Hexagonal architecture (Ports & Adapters). Services are the core hexagon; the CLI, GUI and API are driving adapters. The file tree is deliberately not drawn here — a hand-drawn one drifted out of date. Start from:

- [`REFERENCE_PORTS.md`](REFERENCE_PORTS.md) — generated from the live ABCs: every port, what you implement, and who already implements it.
- `src/trcc/core/registry.py` — `ALL_DEVICES`, the VID:PID → product and wire table.
- `src/trcc/core/protocol.py` — `FBL_PROFILES` and the PM → FBL tables.
- `src/trcc/adapters/device/` — one class per wire: `scsi_lcd.py`, `hid_lcd.py` (F5 bytes in `_f5.py`), `bulk_lcd.py`, `ly_lcd.py`, `led.py`.
- `src/trcc/services/settings.py` — `Settings`, persisted to `trcc.json`.
- `src/trcc/adapters/repo/data_install.py` — theme and mask archive download.
- `src/trcc/ui/cli/`, `src/trcc/ui/api/`, `src/trcc/ui/gui/` — the CLI, REST API and GUI.

### Device Detection Flow

```text
1. Scan USB (PyUSB) for every VID:PID in ALL_DEVICES (Platform.scan_devices, adapters/system/_base.py)
2. SCSI devices: map the USB device to /dev/sgX by walking sysfs (adapters/system/linux.py)
3. Handshake → FBL or PM/SUB byte → resolution (core/protocol.py)
4. Device key: "VID:PID", with "@<usb port>" appended only when two identical devices are plugged in
5. Per-device settings (theme, brightness, orientation) are keyed by that device key in trcc.json
```

## Video Playback

### Windows TRCC Video Implementation

Windows TRCC uses ffmpeg directly via subprocess for video frame extraction (from `FormCZTV.cs` lines 1975-1993):

```csharp
string value = $"ffmpeg -i \"{name}\" -y -r 24 -f image2 \"{ucVideoCut1.allPicAddr}%04d.bmp\"";
Process.Start(new ProcessStartInfo {
    FileName = "cmd.exe",
    Arguments = "/c \"" + value + "\"",
    WindowStyle = ProcessWindowStyle.Hidden,
    CreateNoWindow = true
});
```

**Key parameters:**
- `-r 24` - Extract at 24 frames per second
- `-f image2` - Output as image sequence
- `%04d.bmp` - Sequential numbered BMP files

### Linux Implementation

The Linux port matches Windows behavior by using FFmpeg via subprocess for frame extraction. All frames are preloaded into memory for smooth playback.

## Configuration

Settings are stored in `trcc.json` in the config directory (`~/.trcc/trcc.json` on Linux), owned by `Settings` in `src/trcc/services/settings.py`. The file has three sections:

| Key | Contents |
|-----|----------|
| `app` | Global preferences — language, temperature unit (`"C"` / `"F"`), refresh interval, … (`AppSettings`, `services/settings.py`) |
| `devices` | One entry per LCD, keyed by device key (e.g. `"0402:3922"`) — orientation, brightness (0-100), current theme, mask and overlay state, … (`DeviceSettings`, `core/models.py`) |
| `led_devices` | One entry per LED controller (`LedDeviceSettings`, `core/led_models.py`) |

Each dataclass is written as-is, so its fields are the authoritative list.

## Quick Commands

Device commands take the device `KEY` (its `VID:PID`, from `trcc detect`).
`0402:3922` and `0416:8001` below are examples — substitute your own.

```bash
# Setup
trcc setup                                    # setup wizard (deps, udev, desktop)
trcc detect                                   # list all devices

# Display
trcc display send-image 0402:3922 image.png   # send image to LCD
trcc display color 0402:3922 ff0000           # solid color
trcc display test 0402:3922                   # colour-cycle test
trcc display load-video 0402:3922 clip.mp4    # play video
trcc display screencast 0402:3922 0 0 640 480 # stream a screen region to LCD
trcc display set-brightness 0402:3922 50      # 50% brightness
trcc display set-orientation 0402:3922 90     # rotate display

# Themes
trcc theme list 0402:3922                     # list local themes
trcc display load-theme 0402:3922 ~/.trcc/data/theme320320/Theme1  # load and send a theme directory
trcc theme save 0402:3922 MyTheme             # save current as custom
trcc theme export 0402:3922 003a out.tr       # export to .tr file
trcc theme import 0402:3922 out.tr            # import from .tr file
trcc display apply-mask 0402:3922 mask.png    # apply mask
trcc display mask-visible 0402:3922 off       # hide the mask

# LED
trcc led color 0416:8001 ff0000               # set LED color
trcc led mode 0416:8001 breathing             # set LED effect
trcc led brightness 0416:8001 50              # set LED brightness
trcc led temp-source 0416:8001 cpu            # sensor source for linked modes
trcc led toggle 0416:8001 off                 # turn LEDs off

# Diagnostics
trcc report                                   # full diagnostic report
trcc doctor                                   # check deps and permissions
trcc system hid-debug 0402:3922              # handshake dump
trcc system led-debug --test-colors 0416:8001 # LED diagnostic

# GUI / API
trcc gui                                      # launch GUI
trcc serve                                    # start REST API server
```

> **Uninstalling:** there is no `trcc uninstall` command — it was dropped in
> the rebuild and has not been restored. Remove the package with your package
> manager (or `pip uninstall trcc-linux`), then delete `~/.trcc/`,
> `~/.trcc-user/`, and the files `trcc system setup` installs: the udev rules
> `/etc/udev/rules.d/99-trcc-lcd.rules` and `/etc/udev/rules.d/70-trcc-rgb.rules`,
> `/etc/modules-load.d/trcc-sg.conf`, `/etc/modules-load.d/trcc-rapl.conf`, and
> `/etc/modprobe.d/trcc-lcd.conf` if present.

## Troubleshooting

### Permission denied
```bash
# Install udev rules (preferred — auto-prompts for sudo)
trcc system setup
# Then replug the USB cable

# Or manually:
sudo chmod 666 /dev/sgX
```

### Device not found
```bash
# Check USB connection
lsusb | grep -i "0402\|0416\|0418\|87cd\|87ad"

# Check SCSI mapping
lsscsi -t

# Load sg driver
sudo modprobe sg
```

### Display shows garbage
- Verify resolution matches your LCD (default: 320x320)
- Check pixel format (RGB565; big-endian on 320x320)
- Ensure full frame is sent (204,800 bytes for 320x320)

## See Also

- [PROTOCOL_USBLCDNEW.md](PROTOCOL_USBLCDNEW.md) — USB protocol reverse-engineered from USBLCDNEW (handles `87AD:70DB`, `0416:5302`, `0416:5406`, `0416:5408`, `0416:5409`)
- [PROTOCOL_USBLED.md](PROTOCOL_USBLED.md) — HID LED protocol reverse-engineered from FormLED.cs (handles `0416:8001`)
- [REFERENCE_DEVICES.md](REFERENCE_DEVICES.md) — Full device compatibility list with tester credits

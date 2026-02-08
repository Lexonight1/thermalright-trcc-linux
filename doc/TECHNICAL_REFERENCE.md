# TRCC Linux - Technical Reference

## Overview

Linux port of the Thermalright TRCC application for controlling LCD displays on CPU coolers.

## Supported Devices

| VID    | PID    | Vendor      | Product           |
|--------|--------|-------------|-------------------|
| 0x87CD | 0x70DB | Thermalright| LCD Display       |
| 0x0416 | 0x5406 | ALi Corp    | LCD Display       |
| 0x0402 | 0x3922 | ALi Corp    | USB PRC System    |

SCSI devices appear as SCSI Generic (`/dev/sgX`) with vendor "USBLCD".

### HID Devices (Testing Branch)

| VID    | PID    | Protocol | Type | Notes |
|--------|--------|----------|------|-------|
| 0x0416 | 0x5302 | HID      | 2 (H)   | LCD display via HID bulk transfer |
| 0x0418 | 0x5303 | HID      | 3 (ALi) | LCD display via HID bulk transfer |
| 0x0418 | 0x5304 | HID      | 3 (ALi) | LCD display via HID bulk transfer |

### HID LED Devices (Testing Branch)

| VID    | PID    | Protocol | Type | Notes |
|--------|--------|----------|------|-------|
| 0x0416 | 0x5302 | LED      | HID 64-byte | RGB LED controller (AX120 DIGITAL) |

LED devices are distinguished from LCD HID devices by the `implementation` field (`hid_led`) set during device detection based on the Windows device model registry.

## Display Resolutions

### FBL (Feature Byte Length) Detection

The Windows app uses FBL values to identify display resolution. FBL mapping:

| FBL | Resolution | Notes |
|-----|------------|-------|
| 36, 37 | 240x240 | Small |
| 50 | 240x320 | Portrait |
| 54 | 360x360 | Medium |
| 64 | 640x480 | VGA |
| 72 | 480x480 | Large square |
| 100-102 | 320x320 | Standard (default) |
| 114 | 1600x720 | Ultrawide |
| 128 | 1280x480 | Wide (Trofeo Vision) |
| 192 | 1920x462 | Ultrawide |
| 224 | 854x480/960x540/800x480 | Depends on PM byte |

### PM → FBL Mapping (Type 2 HID Devices)

Type 2 HID devices don't report FBL directly. Instead, the PM (product mode) byte from the handshake maps to FBL:

| PM | FBL | Resolution | Notes |
|----|-----|------------|-------|
| 5 | 50 | 240x320 | |
| 7 | 64 | 640x480 | |
| 9 | 224 | 854x480 | |
| 10 | 224 | 960x540 | Special: PM overrides FBL 224 default |
| 11 | 224 | 854x480 | |
| 12 | 224 | 800x480 | Special: PM overrides FBL 224 default |
| 32 | 100 | 320x320 | |
| 64 | 114 | 1600x720 | |
| 65 | 192 | 1920x462 | |
| 1+sub=48 | 114 | 1600x720 | PM=1 with SUB byte variant |
| 1+sub=49 | 192 | 1920x462 | PM=1 with SUB byte variant |

Functions: `pm_to_fbl()` and `fbl_to_resolution()` in `hid_device.py`.

### Theme Directories & Archives

Theme archives for all 15 LCD resolutions are tracked in git. On first use, `ensure_themes_extracted()` in `paths.py`:

1. Checks the package dir (`src/trcc/data/`) for extracted themes
2. Checks the user dir (`~/.trcc/data/`) for previously extracted themes
3. If no archive found locally, **downloads from GitHub** (`raw.githubusercontent.com`)
4. Extracts via `py7zr` (Python) or system `7z` CLI
5. Falls back to `~/.trcc/data/` if the package dir is read-only

```
src/trcc/data/
├── Theme240240.7z          # All 15 resolutions bundled
├── Theme240320.7z
├── Theme320320.7z
├── Theme360360.7z
├── Theme480480.7z
├── Theme640480.7z
├── Theme800480.7z
├── Theme854480.7z
├── Theme960540.7z
├── Theme1280480.7z         # Trofeo Vision
├── Theme1600720.7z
├── Theme1920462.7z
├── Theme480800.7z          # Portrait variants
├── Theme480854.7z
├── Theme540960.7z
└── Web/
    ├── 240240.7z           # Cloud preview PNGs
    ├── 320320.7z
    ├── zt240240.7z         # Cloud mask themes (000a-023e)
    ├── zt320320.7z
    └── ...
```

Each theme subdirectory contains:
- `00.png` - Background image (sent to LCD)
- `01.png` - Mask overlay
- `config1.dc` - Theme configuration
- `Theme.png` - Preview thumbnail

Mask-only themes (in `zt*/` directories) omit `00.png`.

## Protocol

### SCSI Commands

All communication via `sg_raw` to `/dev/sgX`. Source: reverse-engineered from `USBLCD.exe` (native C++/MFC) via Ghidra decompilation.

**Header format (20 bytes):**
```
bytes[0:3]   = command (LE uint32)
bytes[4:11]  = zeros
bytes[12:15] = data size (LE uint32)
bytes[16:19] = CRC32(bytes[0:15])
```
Only bytes[0:15] are sent as the SCSI CDB (16-byte). The CRC32 is appended but the device firmware ignores it (verified by testing with zeroed CRC — still works).

### CDB Byte-Level Structure

The 4-byte command field encodes a structured protocol:

```
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

```
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

Frame chunks are 64 KiB each (except the last). Chunk count depends on resolution:

| Resolution | RGB565 Size | Chunks | Chunk Sizes |
|---|---|---|---|
| 240×240 | 115,200 (0x1C200) | 2 | 0xE100 + 0xE100 |
| 320×240 | 153,600 (0x25800) | 3 | 0xE100 + 0xE100 + 0x9600 |
| 320×320 | 204,800 (0x32000) | 4 | 0x10000 × 3 + 0x2000 |

**Important:** Initialize ONCE, then stream frames without re-init.

### Compressed Frame Transfer

USBLCD.exe supports zlib-compressed frames (not implemented in trcc-linux):

1. **Single frame**: Compress RGB565 data with zlib level 3, send via `0x201F5`. `byte[8]` = frame count from shared memory.
2. **Multi-frame carousel**: Each frame compressed individually, sent via `0x301F5 | (frame_index << 24)`. Sleep 10ms between frames, 500ms after first compressed send.

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

RGB565 big-endian (2 bytes/pixel):
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

On Linux, this IPC layer is unnecessary — `trcc` talks directly to `/dev/sgX` via `sg_raw`.

## LED Protocol (HID 64-byte Reports)

LED devices communicate via 64-byte HID reports (matching Windows FormLED).

### Handshake

The LED handshake reads a PM (product mode) byte from the device, which maps to an LED style:

| PM | Style | Model | Segments | Zones |
|----|-------|-------|----------|-------|
| 1  | 1     | AX120 DIGITAL | 12 | 4 |

### LED Packet Format

```
Byte 0:     Report ID (0x00)
Byte 1:     Command (0xA0 = LED data)
Byte 2:     Global on/off (0x01 = on, 0x00 = off)
Byte 3:     Brightness (0-100)
Bytes 4-N:  Per-LED data: [R, G, B, on/off] × segment_count
```

### LED Effect Modes

| Mode | Name | Description |
|------|------|-------------|
| 0 | Static | Solid color on all segments |
| 1 | Breathing | Fade in/out cycle |
| 2 | Rainbow | Rotating hue across segments |
| 3 | Cycle | Cycle through preset colors |
| 4 | Wave | Color wave propagation |
| 5 | Flash | Strobe effect |
| 6 | Music | Reactive to audio input (stub) |

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

PyQt6 MVC architecture. Controllers are GUI-independent; views subscribe via callbacks.

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
├── sensor_enumerator.py         # Hardware sensor discovery (hwmon, nvidia-ml-py, psutil, RAPL)
├── sysinfo_config.py            # Dashboard panel config persistence
├── system_info.py               # CPU/GPU/RAM/disk sensor collection
├── cloud_downloader.py          # Cloud theme HTTP fetch
├── theme_downloader.py          # Theme pack download manager
├── theme_io.py                  # Theme export/import (.tr format)
├── binary_reader.py             # Binary data reader (DC parsing helper)
├── paths.py                     # XDG data/config, .7z extraction, on-demand download
├── hid_device.py                # HID USB transport (PyUSB/HIDAPI)
├── led_device.py                # LED RGB protocol (effects, packets, HID sender)
├── device_factory.py            # Protocol factory (SCSI/HID/LED routing)
├── __version__.py               # Version info
├── core/
│   ├── models.py                # ThemeInfo, DeviceInfo, VideoState, OverlayElement
│   └── controllers.py           # FormCZTVController, FormLEDController, MVC controllers
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
    ├── uc_led_control.py        # LED RGB control panel
    ├── uc_activity_sidebar.py   # Sensor element picker
    └── uc_about.py              # Settings / about panel
```

### Device Detection Flow

```
1. lsusb → find known VID:PID
2. lsscsi → map USB to /dev/sgX
3. sysfs → verify USBLCD vendor
4. FBL query → detect resolution (or use default 320x320)
5. Sort by /dev/sgX path, assign 0-based device_index
6. Build device key: "{index}:{vid:04x}_{pid:04x}"
7. Restore per-device config (theme, brightness, rotation)
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

Settings stored in `~/.config/trcc/config.json`.

### Global settings

| Key | Type | Description |
|-----|------|-------------|
| `temp_unit` | int | 0=Celsius, 1=Fahrenheit |
| `resolution` | [int,int] | LCD resolution |

### Per-device settings

Stored under `"devices"` keyed by `"{ordinal}:{vid:04x}_{pid:04x}"` (e.g. `"0:87cd_70db"`).
Ordinal is 0-based index assigned by sorting detected devices by `/dev/sgX` path.

| Key | Type | Description |
|-----|------|-------------|
| `theme_path` | string | Last selected theme directory or video file |
| `brightness_level` | int | 1=25%, 2=50%, 3=100% |
| `rotation` | int | 0, 90, 180, or 270 degrees |

```json
{
  "temp_unit": 0,
  "resolution": [320, 320],
  "devices": {
    "0:87cd_70db": {
      "theme_path": "/home/user/.trcc/data/Theme320320/003a",
      "brightness_level": 2,
      "rotation": 0
    },
    "1:87cd_70db": {
      "theme_path": "/home/user/.trcc/data/Theme320320/001b",
      "brightness_level": 3,
      "rotation": 90
    }
  }
}
```

## Quick Commands

```bash
# Install udev rules + USB storage quirks (required once, auto-prompts for sudo)
trcc setup-udev

# Detect device
trcc detect
trcc detect --all

# Test display (red screen)
trcc test

# Send image to LCD
trcc send image.png

# Display solid color
trcc color ff0000

# Run GUI
PYTHONPATH=src python3 -m trcc.cli gui
# or if installed:
trcc gui
```

## Troubleshooting

### Permission denied
```bash
# Install udev rules (preferred — auto-prompts for sudo)
trcc setup-udev
# Then replug the USB cable

# Or manually:
sudo chmod 666 /dev/sgX
```

### Device not found
```bash
# Check USB connection
lsusb | grep -i "0402\|0416\|87cd"

# Check SCSI mapping
lsscsi -t

# Load sg driver
sudo modprobe sg
```

### Display shows garbage
- Verify resolution matches your LCD (default: 320x320)
- Check pixel format (RGB565 big-endian)
- Ensure full frame is sent (204,800 bytes for 320x320)

## See Also

- [USBLCD_PROTOCOL.md](USBLCD_PROTOCOL.md) — Full SCSI protocol reverse-engineered from USBLCD.exe (handles `0402:3922`)
- [USBLCDNEW_PROTOCOL.md](USBLCDNEW_PROTOCOL.md) — USB bulk protocol reverse-engineered from USBLCDNEW.exe (handles `87CD:70DB`, `0416:5302`, `0416:5406`)

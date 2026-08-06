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

### Bulk USB Devices

| VID    | PID    | Vendor      | Product           |
|--------|--------|-------------|-------------------|
| 0x87AD | 0x70DB | Thermalright| LCD Display       |

Bulk devices use raw USB vendor-specific transfers via PyUSB. Products: GrandVision 360 AIO, Mjolnir Vision 360, Wonder Vision Pro 360.

### HID Devices

| VID    | PID    | Protocol | Type | Notes |
|--------|--------|----------|------|-------|
| 0x0416 | 0x5302 | HID      | 2 (H)   | LCD display via HID bulk transfer |
| 0x0418 | 0x5303 | HID      | 3 (ALi) | LCD display via HID bulk transfer |
| 0x0418 | 0x5304 | HID      | 3 (ALi) | LCD display via HID bulk transfer |

### HID LED Devices

| VID    | PID    | Protocol | Type | Notes |
|--------|--------|----------|------|-------|
| 0x0416 | 0x8001 | LED      | HID 64-byte | RGB LED controller (AX120 DIGITAL, PA120 DIGITAL, Phantom Spirit 120 Digital EVO, Peerless Assassin 120 Digital ARGB White) |

LED devices are distinguished from LCD HID devices by the `implementation` field (`hid_led`) set during device detection based on the Windows device model registry.

## Display Resolutions

### FBL (Feature Byte Length) Detection

The Windows app uses FBL values to identify display resolution. FBL mapping:

| FBL | Resolution | Notes |
|-----|------------|-------|
| 36, 37 | 240x240 | Small |
| 50 | 320x240 | Rotate, SPI mode 2 |
| 51 | 320x240 | HID Type 2, rotate |
| 53 | 320x240 | HID Type 2, rotate |
| 54 | 360x360 | JPEG |
| 58 | 320x240 | Rotate |
| 64 | 640x480 | Rotate |
| 72 | 480x480 | Large square |
| 100-102 | 320x320 | Big-endian RGB565 (default) |
| 114 | 1600x720 | JPEG, rotate |
| 128 | 1280x480 | JPEG, rotate (Trofeo Vision) |
| 129 | 480x480 | Alias for FBL 72 |
| 192 | 1920x462 | JPEG, rotate. PM disambiguates: 68→1280x480, 69→1920x440 |
| 224 | 854x480 | JPEG, rotate. PM disambiguates: 10/16→960x540, 12→800x480, 13/17→960x320, 15→640x172 |

### PM → FBL Mapping (Type 2 HID Devices)

Type 2 HID devices don't report FBL directly. Instead, the PM (product mode) byte from the handshake maps to FBL:

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

Functions: `pm_to_fbl()` and `fbl_to_resolution()` in `core/models.py`.

### Theme Directories & Archives

Theme archives for all 15 LCD resolutions are tracked in git. On first use, `ensure_themes_extracted()` in `adapters/infra/data_repository.py`:

1. Checks the package dir (`src/trcc/data/`) for extracted themes
2. Checks the user dir (`~/.trcc/data/`) for previously extracted themes
3. If no archive found locally, **downloads from GitHub** (`raw.githubusercontent.com`)
4. Extracts via system `7z` CLI
5. Falls back to `~/.trcc/data/` if the package dir is read-only

```text
src/trcc/data/
├── Theme240240.7z          # All 16 resolutions bundled
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
```text
bytes[0:3]   = command (LE uint32)
bytes[4:11]  = zeros
bytes[12:15] = data size (LE uint32)
bytes[16:19] = CRC32(bytes[0:15])
```
Only bytes[0:15] are sent as the SCSI CDB (16-byte). The CRC32 is appended but the device firmware ignores it (verified by testing with zeroed CRC — still works).

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

| PM | Style | Model | LEDs | Segments |
|----|-------|-------|------|----------|
| 1  | 1     | FROZEN HORIZON PRO | 30 | 10 |
| 2  | 1     | FROZEN MAGIC PRO | 30 | 10 |
| 3  | 1     | AX120 DIGITAL | 30 | 10 |
| 16 | 2     | PA120 DIGITAL | 84 | 18 |
| 23 | 2     | RK120 DIGITAL | 84 | 18 |
| 32 | 3     | AK120 DIGITAL | 64 | 10 |
| 48 | 5     | LF8 | 93 | 23 |
| 80 | 6     | LF12 | 124 | 72 |
| 96 | 7     | LF10 | 116 | 12 |
| 112| 9     | LC2 | 61 | 31 |
| 128| 4     | HR10 2280 PRO DIGITAL / LC1 | 31 | 14 |
| 129| 10    | LF11 | 38 | 17 |
| 144| 11    | LF15 | 93 | 72 |
| 160| 12    | LF13 | 62 | 62 |
| 208| 8     | CZ1 | 18 | 13 |
| 208| 13    | Magic Qube | 65 | 14 |

The Magic Qube reports PM=208 like the CZ1, so it is not told apart by the PM
byte. It is disambiguated by its handshake header `DC DD AA 01` (the CZ1 answers
with the standard `DA DB DC DD`); the header override is checked before the PM
registry. It is a Linux-added device — absent from the Windows TRCC app —
reverse-engineered and hardware-validated by [@jphilipb](https://github.com/jphilipb).

### LED Packet Format

```text
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

## Bulk USB Protocol

Bulk devices (`87AD:70DB`) use raw USB vendor-specific transfers via PyUSB, bypassing the kernel's USB Mass Storage / SCSI stack entirely.

### Handshake

Same PM→FBL→resolution pipeline as HID devices. Default resolution: 480×480 if handshake fails.

### Frame Transfer

Frames are sent as raw RGB565 data via USB bulk OUT endpoint. The frame is split into chunks matching the endpoint's max packet size. No SCSI CDB header — just raw pixel data.

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

Same HID-style handshake as other devices. PM byte maps through `pm_to_fbl()` → `fbl_to_resolution()`.

### Frame Transfer

Frames are JPEG-encoded and split into 512-byte chunks:

```text
Each chunk (512 bytes):
  Bytes 0-15:   Header (16 bytes)
    [0]:        0xEF (magic)
    [1]:        chunk type (0x69=data, 0x65=end)
    [2-3]:      chunk index (LE uint16)
    [4-7]:      total data length (LE uint32)
    [8-15]:     padding (zeros)
  Bytes 16-511: Data payload (496 bytes)
```

The last chunk is padded with zeros to fill 512 bytes and marked with type `0x65`.

### PID Variants

| PID | PM Formula | Notes |
|-----|-----------|-------|
| `0x5408` (LY) | `response[4]` | Standard LY |
| `0x5409` (LY1) | `response[4] + 2` | LY1 variant, PM offset |

### Known Products

| Product | Resolution |
|---------|------------|
| Trofeo Vision 9.16 LCD | — |

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

Hexagonal architecture (Ports & Adapters). Services are the core hexagon; CLI, GUI, API, and Setup GUI are driving adapters.

```text
src/trcc/
├── cli/                         # Typer CLI adapter package (8 submodules)
├── api/                         # FastAPI REST adapter package (7 submodules)
│   ├── __init__.py              # App factory, middleware, CORS
│   ├── devices.py               # Device endpoints
│   ├── display.py               # Display endpoints
│   ├── led.py                   # LED endpoints
│   ├── themes.py                # Theme endpoints
│   ├── system.py                # System + perf endpoints
│   ├── i18n.py                  # Language endpoints
│   └── models.py                # Pydantic request/response models
├── ipc.py                       # Unix socket IPC daemon (GUI-as-server)
├── conf.py                      # Settings singleton + persistence helpers
├── __version__.py               # Version info
├── core/                        # Domain layer — pure Python, zero I/O
│   ├── models.py                # Domain constants, dataclasses, enums, resolution pipeline
│   ├── ports.py                 # Device ABC, Renderer ABC, protocol type aliases
│   ├── lcd_device.py            # LCDDevice(Device) — direct methods, delegates to services
│   ├── led_device.py            # LEDDevice(Device) — LED control methods
│   ├── builder.py               # ControllerBuilder — fluent builder with full DI wiring
│   ├── color.py                 # ColorEngine — HSV/RGB conversion, LED color math
│   ├── encoding.py              # Frame encoding helpers
│   ├── led_segment.py           # SegmentDisplay ABC + 10 subclasses (domain data)
│   └── paths.py                 # Path constants
├── services/                    # Application layer — business logic, no framework deps
│   ├── __init__.py              # Re-exports service classes
│   ├── device.py                # DeviceService — detect, select, send_pil, send_rgb565
│   ├── image.py                 # ImageService — facade over active Renderer
│   ├── display.py               # DisplayService — high-level display orchestration
│   ├── led.py                   # LEDService — LED RGB control via LedProtocol
│   ├── led_config.py            # LED config persistence (Memento pattern)
│   ├── led_effects.py           # LEDEffectEngine — strategy pattern for LED effects
│   ├── media.py                 # MediaService — GIF/video frame extraction
│   ├── overlay.py               # OverlayService — overlay rendering
│   ├── renderer.py              # Renderer ABC — Strategy port for compositing backends
│   ├── system.py                # SystemService — system sensor access and monitoring
│   ├── theme.py                 # ThemeService — theme orchestration
│   ├── theme_loader.py          # Theme loading logic
│   ├── theme_persistence.py     # Theme save/export/import
│   └── video_cache.py           # VideoFrameCache — lazy per-frame encoding
├── adapters/
│   ├── device/                  # USB device protocol handlers
│   │   ├── frame.py             # UsbDevice / FrameDevice ABCs
│   │   ├── scsi.py              # SCSI protocol (sg_raw)
│   │   ├── hid.py               # HID USB transport (PyUSB)
│   │   ├── led.py               # LED RGB protocol (effects, HID sender)
│   │   ├── led_kvm.py           # KVM LED backend
│   │   ├── led_segment.py       # Segment display re-export (delegates to core/)
│   │   ├── bulk.py              # Raw USB bulk protocol
│   │   ├── ly.py                # LY USB bulk protocol (0416:5408/5409)
│   │   ├── lcd.py               # SCSI RGB565 frame send
│   │   ├── detector.py          # USB device scan + registries
│   │   ├── factory.py           # Protocol factory (SCSI/HID/LED/Bulk/LY routing)
│   │   └── _usb_helpers.py      # Shared USB utility functions
│   ├── render/                  # Rendering backends (Strategy pattern)
│   │   └── qt.py                # QtRenderer — QImage/QPainter (sole renderer)
│   ├── system/                  # System integration
│   │   ├── sensors.py           # Hardware sensor discovery + collection
│   │   ├── hardware.py          # Hardware info (CPU, GPU, RAM, disk)
│   │   ├── info.py              # Dashboard panel config
│   │   └── config.py            # Dashboard config persistence
│   └── infra/                   # Infrastructure (I/O, files, network)
│       ├── data_repository.py   # User data paths, on-demand download
│       ├── binary_reader.py     # Binary data reader
│       ├── dc_parser.py         # Parse config1.dc overlay configs
│       ├── dc_writer.py         # Write config1.dc files
│       ├── dc_config.py         # DcConfig class
│       ├── font_resolver.py     # Cross-distro font discovery
│       ├── media_player.py      # FFmpeg video frame extraction
│       ├── theme_cloud.py       # Cloud theme HTTP fetch
│       ├── theme_downloader.py  # Theme pack download manager
│       ├── debug_report.py      # Diagnostic report tool
│       └── doctor.py            # Dependency health check + setup wizard
├── install/                     # Standalone setup wizard
│   ├── __init__.py
│   └── gui.py                   # PySide6 setup wizard GUI
└── gui/               # PySide6 GUI adapter
    ├── trcc_app.py              # TRCCApp — thin QMainWindow shell, entry point
    ├── lcd_handler.py           # LCDHandler — one per LCD device (owns LCDDevice, timers)
    ├── metrics_mediator.py      # MetricsMediator — single polling authority for sensors
    ├── base.py                  # BasePanel, BaseThemeBrowser, pil_to_pixmap
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
    ├── uc_led_control.py        # LED RGB control panel (LED styles 1-13)
    ├── uc_screen_led.py         # LED segment visualization (colored circles)
    ├── uc_color_wheel.py        # HSV color wheel for LED hue selection
    ├── uc_activity_sidebar.py   # Sensor element picker
    └── uc_about.py              # Settings / about panel
```

### Device Detection Flow

```text
1. lsusb → find known VID:PID
2. lsscsi → map USB to /dev/sgX
3. sysfs → verify USBLCD vendor
4. FBL query → detect resolution (or use default 320x320)
5. Sort by /dev/sgX path, assign 0-based device_index
6. Build device key: "{index}" (vid_pid stored inside the device dict)
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

Settings stored in `~/.trcc/config.json`.

### Global settings

| Key | Type | Description |
|-----|------|-------------|
| `temp_unit` | int | 0=Celsius, 1=Fahrenheit |
| `resolution` | [int,int] | LCD resolution |

### Per-device settings

Stored under `"devices"` keyed by index-only `"0"`, `"1"`, etc. The `vid_pid` is stored inside each device dict (e.g. `"vid_pid": "87cd_70db"`). Old `"0:vid_pid"` format is auto-migrated on `load_config()`.

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
trcc display load-theme 0402:3922 003a        # load and send theme
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
trcc system hid-debug                         # HID handshake dump
trcc system led-debug --test-colors           # LED diagnostic

# GUI / API
trcc gui                                      # launch GUI
trcc serve                                    # start REST API server
```

> **Uninstalling:** there is no `trcc uninstall` command — it was dropped in
> the rebuild and has not been restored. Remove the package with your package
> manager (or `pip uninstall trcc-linux`), then delete `~/.trcc/`,
> `~/.trcc-user/`, and the udev rule at `/etc/udev/rules.d/99-trcc.rules`.

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

- [USBLCD_PROTOCOL.md](audit/USBLCD_PROTOCOL.md) — Full SCSI protocol reverse-engineered from USBLCD.exe (handles `0402:3922`)
- [PROTOCOL_USBLCDNEW.md](PROTOCOL_USBLCDNEW.md) — USB protocol reverse-engineered from USBLCDNEW.exe (handles `87CD:70DB`, `0416:5302`, `0416:5406`, `87AD:70DB`)
- [PROTOCOL_USBLED.md](PROTOCOL_USBLED.md) — HID LED protocol reverse-engineered from FormLED.cs (handles `0416:8001`)
- [REFERENCE_DEVICES.md](REFERENCE_DEVICES.md) — Full device compatibility list with tester credits

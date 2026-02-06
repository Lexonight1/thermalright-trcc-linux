# TRCC Linux - Technical Reference

## Overview

Linux port of the Thermalright TRCC application for controlling LCD displays on CPU coolers.

## Supported Devices

| VID    | PID    | Vendor      | Product           |
|--------|--------|-------------|-------------------|
| 0x87CD | 0x70DB | Thermalright| LCD Display       |
| 0x0416 | 0x5406 | ALi Corp    | LCD Display       |
| 0x0402 | 0x3922 | ALi Corp    | USB PRC System    |

Devices appear as SCSI Generic (`/dev/sgX`) with vendor "USBLCD".

## Display Resolutions

### FBL (Feature Byte Length) Detection

The Windows app uses FBL values to identify display resolution. FBL mapping:

| FBL | Resolution | Notes |
|-----|------------|-------|
| 36, 37 | 240x240 | Small |
| 54 | 360x360 | Medium |
| 64 | 640x480 | VGA |
| 72 | 480x480 | Large square |
| 100-102 | 320x320 | Standard (default) |
| 114 | 1600x720 | Ultrawide |
| 128 | 1280x480 | Wide |
| 192 | 1920x462 | Ultrawide |
| 224 | 854x480/960x540/800x480 | Depends on pm |

### Theme Directories

Pre-rendered themes organized by resolution in `data/`:
- `Theme320320/` - 320x320 displays (5 themes)
- `Theme480480/` - 480x480 displays
- `Theme240240/` - 240x240 displays
- etc.

Each theme contains:
- `00.png` - Display state 0
- `01.png` - Display state 1
- `config1.dc` - Theme configuration
- `Theme.png` - Preview image

## Protocol

### SCSI Commands

All communication via `sg_raw` to `/dev/sgX`.

**Header format (20 bytes):**
```
cmd(4) + zeros(8) + size(4) + crc32(4)
```

### Initialization Sequence

```
1. Poll:  0xF5   READ  57,600 bytes (check device ready)
2. Init:  0x1F5  WRITE 57,600 bytes (initialize display)
```

### Frame Transfer (320x320 = 204,800 bytes)

```
Chunk 1: 0x101F5    WRITE 65,536 bytes
Chunk 2: 0x10101F5  WRITE 65,536 bytes
Chunk 3: 0x20101F5  WRITE 65,536 bytes
Chunk 4: 0x30101F5  WRITE  8,192 bytes
```

**Important:** Initialize ONCE, then stream frames without re-init.

### Pixel Format

RGB565 big-endian (2 bytes/pixel):
```python
pixel = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
```

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

### Device Detection Flow

```
1. lsusb → find known VID:PID
2. lsscsi → map USB to /dev/sgX
3. sysfs → verify USBLCD vendor
4. FBL query → detect resolution (or use default 320x320)
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

Settings stored in `~/.config/trcc/config.json`:

```json
{
  "selected_device": "/dev/sg0",
  "resolution": "320x320"
}
```

## Quick Commands

```bash
# Install udev rules + USB storage quirks (required once)
sudo trcc setup-udev

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
# Install udev rules (preferred)
sudo trcc setup-udev
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

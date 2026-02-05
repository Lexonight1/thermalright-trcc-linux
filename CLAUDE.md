# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TRCC Linux** - Native Linux port of Thermalright LCD Control Center (Windows TRCC 2.0.3) for controlling LCD displays on Thermalright CPU coolers, AIO pump heads, and fan hubs.

**Repository:** https://github.com/Lexonight1/thermalright-trcc-linux

**Current version: 1.1.0** (see `src/trcc/__version__.py`)

**Status: Feature-complete** — 100% Windows feature parity achieved.

- PyQt6 GUI matching Windows layout exactly (1454x800)
- MVC architecture: GUI-independent controllers with callback-based views
- SCSI protocol via sg_raw, RGB565 pixel format
- Multi-resolution support: 240x240, 320x320, 480x480, 640x480
- Modular component architecture mirroring Windows UC* classes

## Versioning

Version is defined in two places (keep in sync):
- `pyproject.toml` — `version = "X.Y.Z"` (used by pip/hatch)
- `src/trcc/__version__.py` — `__version__ = "X.Y.Z"` (runtime access)

Semantic versioning: MAJOR.MINOR.PATCH
- MAJOR: Breaking changes
- MINOR: New features, backward compatible
- PATCH: Bug fixes only

## Commands

```bash
# Run GUI
PYTHONPATH=src python3 -m trcc.cli gui

# Run with window decorations (for debugging)
PYTHONPATH=src python3 -m trcc.cli gui --decorated

# Device detection
PYTHONPATH=src python3 -m trcc.cli detect
PYTHONPATH=src python3 -m trcc.cli detect --all

# Send image to LCD
PYTHONPATH=src python3 -m trcc.cli send image.png

# Setup udev rules + USB quirks (requires root)
sudo PYTHONPATH=src python3 -m trcc.cli setup-udev
PYTHONPATH=src python3 -m trcc.cli setup-udev --dry-run

# Run tests
pytest tests/

# Download cloud themes
PYTHONPATH=src python3 -m trcc.cli download themes-320
```

## Source Layout

```
src/trcc/
├── cli.py                  # CLI entry point (gui, detect, send, setup-udev, etc.)
├── lcd_driver.py           # SCSI RGB565 frame send
├── device_detector.py      # USB device scan + KNOWN_DEVICES registry
├── device_implementations.py  # Per-device protocol variants
├── scsi_device.py          # Low-level SCSI commands
├── dc_parser.py            # Parse config1.dc overlay configs
├── dc_writer.py            # Write config1.dc files
├── overlay_renderer.py     # PIL-based overlay text/sensor rendering
├── gif_animator.py         # GIF/video frame extraction (FFmpeg)
├── system_info.py          # CPU/GPU/RAM/disk sensor collection
├── cloud_downloader.py     # Cloud theme HTTP fetch
├── theme_downloader.py     # Theme pack download manager
├── paths.py                # XDG data/config path resolution
├── core/
│   ├── models.py           # ThemeInfo, DeviceInfo, VideoState, OverlayElement
│   └── controllers.py      # FormCZTVController, ThemeController, DeviceController, etc.
└── qt_components/
    ├── qt_app_mvc.py       # Main MVC window (1454x800) - PRIMARY entry point
    ├── base.py             # BasePanel, ImageLabel, pil_to_pixmap, set_background_pixmap
    ├── constants.py        # Layout coords, Sizes, Colors, Styles
    ├── assets.py           # Asset loader with lru_cache
    ├── uc_device.py        # Device sidebar (180x800)
    ├── uc_preview.py       # Preview frame (500x560)
    ├── uc_theme_local.py   # Local themes browser
    ├── uc_theme_web.py     # Cloud themes browser
    ├── uc_theme_mask.py    # Cloud masks browser
    ├── uc_theme_setting.py # Overlay editor / display mode panels
    ├── uc_image_cut.py     # Image cropper
    ├── uc_video_cut.py     # Video trimmer
    ├── uc_about.py         # Settings / about panel (auto-start, language, etc.)
    ├── uc_activity_sidebar.py  # Sensor element picker
    ├── uc_info_module.py   # Live system info display
    └── uc_system_info.py   # System info widget
```

### External References

| What | Path |
|------|------|
| **Windows decompiled C#** | `/home/ignorant/Downloads/TRCCCAPEN/TRCC_decompiled/` |
| **GUI assets** | `src/assets/gui/` (PNG button/background images) |
| **Theme data** | `src/data/Theme320320/` (local themes, not tracked in git) |

## Architecture

### MVC Pattern

Controllers in `core/controllers.py` are GUI-independent. Views subscribe via callbacks:
```python
controller.on_preview_update = lambda img: uc_preview.set_preview_image(img)
controller.on_status_update = lambda text: uc_preview.set_status(text)
```

### PyQt6 Layout (Windows Coordinate Matching)

```python
# FormCZTV positions (relative to form_container at x=180)
UCDevice:     (0, 0, 180, 800)      # sidebar
UCPreview:    (16, 88, 500, 560)    # preview + controls
PanelStack:   (532, 128, 732, 652)  # theme panels
ModeButtons:  y=90                   # Local/Masks/Cloud/Settings
BottomBar:    y=680                  # Rotation/Brightness/Save/Export/Import
```

### Key Patterns

**Background Images**: Use `QPalette` + `QBrush(pixmap)` + `setAutoFillBackground(True)`. Never use `setStyleSheet()` on containers — it blocks palette propagation to children.

**Theme Directory Structure**:
```
src/data/Theme320320/
├── Theme1/
│   ├── 00.png         # Background (sent to LCD)
│   ├── 01.png         # Mask overlay
│   ├── config1.dc     # Overlay configuration
│   └── Theme.png      # Preview thumbnail only
```

**Tab Buttons**: Button order (0=Local, 1=Mask, 2=Cloud, 3=Settings) differs from panel order (0=Local, 1=Cloud, 2=Mask, 3=Settings). Mapping: `{0:0, 1:2, 2:1, 3:3}`.

## Critical Rule: Reference Windows C# First

**ALWAYS read the decompiled Windows C# code before implementing or debugging.** The Windows code is the source of truth.

### Process for New Features

1. **Find the Windows class**: `TRCC.DCUserControl/{ClassName}.cs` or `TRCC.CZTV/FormCZTV.cs`
2. **Get coordinates from .resx**: `TRCC.{Namespace}.{ClassName}.resx` contains `Point(x, y)` and `Size(w, h)`
3. **Get images**: `src/assets/gui/{ResourceName}.png`
4. **Check delegate commands**: Search for `delegate*.Invoke(cmd, ...)` to find command values

### Key FormCZTV.cs Methods

| Method | Line | Purpose |
|--------|------|---------|
| `ThemeLocal` | 3582 | Local theme delegate (cmd: 0/1/2=filter, 16=select, 32=delete) |
| `ThemeWeb` | 3991 | Cloud theme delegate (cmd: 0-6=filter, 16=download) |
| `ThemeMask` | 4374 | Mask delegate (cmd: 16=apply) |
| `ThemeSetting` | 4422 | Settings panel commands |
| `buttonBCZT_Click` | 5497 | Save theme (writes config1.dc) |

## Common Pitfalls

- `UCPreview.set_preview_image(img)` - NOT `update_preview()` (doesn't exist)
- Check `VIDEO_AVAILABLE` not `OPENCV_AVAILABLE` for video support
- Don't call `super().mousePressEvent()` in thumbnail classes that override `clicked` signal signature
- RGB565 conversion must use numpy for performance (not pixel-by-pixel loop)
- SCSI send must be threaded to prevent GUI freeze
- DC file `mask_position` is CENTER coords — convert to top-left for PIL paste

## Coding Standards

- **OOP** - Favor inheritance, base classes, and composition for code reuse
- **No magic numbers** - Extract coordinates, sizes, and thresholds into named constants or config
- **DRY** - Extract common logic into base classes or shared utilities; single source of truth
- **KISS** - Simplest solution that works; don't over-abstract
- Follow PEP 8, use type hints
- Match Windows behavior exactly unless improving

## Video Playback Architecture

FFmpeg is the default (matches Windows). Key settings:
- Frame skipping: `_lcd_send_interval = 4` (send every 4th frame to LCD)
- Preload frames into memory (matches Windows Theme.zt pattern)
- Threaded SCSI send with busy flag
- Time-based frame selection with `time.perf_counter()`

## DC File Formats

**config1.dc (0xDD header)**: Overlay element configs, mask position, display settings
**Theme.dc (0xDC header)**: Carousel config — enabled, interval, 6 theme slots, LCD rotation
**Theme.zt (0xDC header)**: Pre-rendered JPEG frames with timing (video trimmer export)
**.tr export**: Magic 0xDD,0xDC,0xDD,0xDC + config + 10240 padding + mask + background/video

## Cloud Theme Servers

- International: `http://www.czhorde.cc/tr/bj{resolution}/`
- China: `http://www.czhorde.com/tr/bj{resolution}/`

Categories by filename prefix: a=Gallery, b=Tech, c=HUD, d=Light, e=Nature, y=Aesthetic

## See Also

- `doc/TECHNICAL_REFERENCE.md` - Protocol details, FBL codes
- `doc/UI_RESOURCE_MAPPING.md` - Windows resource → Linux asset mapping
- `doc/WINDOWS_UI_HIERARCHY.md` - Windows control hierarchy analysis

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TRCC Linux** - Native Linux port of Thermalright LCD Control Center (Windows TRCC 2.0.3) for controlling LCD displays on Thermalright CPU coolers, AIO pump heads, and fan hubs.

**Repository:** https://github.com/Lexonight1/thermalright-trcc-linux

**Current version: 1.1.1** (see `src/trcc/__version__.py`)

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

## Test Suite

623 tests across 21 test files — `QT_QPA_PLATFORM=offscreen pytest tests/`

| Test file | Module covered | Tests |
|-----------|---------------|-------|
| test_dc_parser | dc_parser | 133 |
| test_dc_writer | dc_writer | 18 |
| test_device_detector | device_detector | 17 |
| test_overlay_renderer | overlay_renderer | 25 |
| test_paths | paths | 25 |
| test_sysinfo_config | sysinfo_config | 18 |
| test_device_implementations | device_implementations | 25 |
| test_scsi_device | scsi_device | 18 |
| test_models | core/models | 30 |
| test_theme_io | theme_io | 14 |
| test_gif_animator | gif_animator | 28 |
| test_system_info | system_info | 35 |
| test_sensor_enumerator | sensor_enumerator | 20 |
| test_lcd_driver | lcd_driver | 15 |
| test_cloud_downloader | cloud_downloader | 27 |
| test_theme_downloader | theme_downloader | 30 |
| test_cli | cli | 26 |
| test_controllers | core/controllers | 43 |
| test_qt_constants | qt_components/constants | 24 |
| test_qt_base | qt_components/base | 27 |
| test_qt_widgets | qt_components widgets+assets | 45 |

Qt tests require `QT_QPA_PLATFORM=offscreen` (headless, no display server).

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
├── sensor_enumerator.py    # Hardware sensor discovery (hwmon, nvidia-ml-py, psutil, RAPL)
├── sysinfo_config.py       # Dashboard panel config persistence
├── system_info.py          # CPU/GPU/RAM/disk sensor collection
├── cloud_downloader.py     # Cloud theme HTTP fetch
├── theme_downloader.py     # Theme pack download manager
├── theme_io.py             # Theme export/import (.tr format)
├── paths.py                # XDG data/config, per-device config, .7z extraction
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
    ├── uc_system_info.py   # System info dashboard
    ├── uc_sensor_picker.py # Sensor selection dialog
    ├── eyedropper.py       # Fullscreen color picker
    ├── screen_capture.py   # X11/Wayland screen grab
    └── pipewire_capture.py # PipeWire/Portal Wayland capture
```

### External References

| What | Path |
|------|------|
| **Windows decompiled C#** | `/home/ignorant/Downloads/TRCCCAPEN/TRCC_decompiled/` |
| **GUI assets** | `src/assets/gui/` (PNG button/background images) |
| **Theme data** | `src/data/Theme*.7z` (archives, tracked in git; extracted at runtime) |
| **Mask data** | `src/data/Web/zt*.7z` (cloud mask archives, tracked in git) |

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

**Theme Data (7z Archives)**:

Default themes and cloud masks ship as `.7z` archives in `src/data/`. On resolution detection, `ensure_themes_extracted()` and `ensure_web_masks_extracted()` in `paths.py` extract the matching archive in-place using `py7zr` (with CLI `7z` fallback). Archives are tracked in git; extracted dirs are gitignored.

```
src/data/
├── Theme320320.7z     # → extracts to Theme320320/Theme1..Theme5/
├── Web/zt320320.7z    # → extracts to Web/zt320320/000a..023e/
└── Theme320320/
    └── Theme1/
        ├── 00.png         # Background (sent to LCD)
        ├── 01.png         # Mask overlay
        ├── config1.dc     # Overlay configuration
        └── Theme.png      # Preview thumbnail only
```

To regenerate archives: `python tools/pack_theme_archives.py`

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

## Per-Device Configuration

Devices keyed by ordinal + USB ID: `"{index}:{vid:04x}_{pid:04x}"` (e.g. `"0:87cd_70db"`).
Ordinal assigned by sorting detected devices by `/dev/sgX` path.

**Per-device settings** (persisted to `~/.config/trcc/config.json` under `"devices"` key):
- `theme_path` — last selected theme directory or video path
- `brightness_level` — 1/2/3 (maps to 25%/50%/100%)
- `rotation` — 0/90/180/270
- `carousel` — `{enabled, interval, themes}` slideshow config
- `overlay` — `{enabled, config}` overlay element config dict

**Global settings** (top-level keys): `temp_unit`, `resolution`

Config structure:
```json
{
  "temp_unit": 0,
  "resolution": [320, 320],
  "devices": {
    "0:87cd_70db": {
      "theme_path": "/path/to/Theme320320/003a",
      "brightness_level": 2,
      "rotation": 0,
      "carousel": {
        "enabled": true,
        "interval": 5,
        "themes": ["Theme1", "Theme3"]
      },
      "overlay": {
        "enabled": true,
        "config": {"time_0": {"x": 10, "y": 10, "metric": "time", "....": "..."}}
      }
    }
  }
}
```

Key functions in `paths.py`: `device_config_key()`, `get_device_config()`, `save_device_setting()`.
DeviceInfo dataclass fields: `model`, `vid`, `pid`, `device_index`.

## DC File Formats

**config1.dc (0xDD header)**: Overlay element configs, mask position, display settings
**Theme.dc (0xDC header)**: Carousel config — enabled, interval, 6 theme slots, LCD rotation
**Theme.zt (0xDC header)**: Pre-rendered JPEG frames with timing (video trimmer export)
**.tr export**: Magic 0xDD,0xDC,0xDD,0xDC + config + 10240 padding + mask + background/video

## Cloud Theme Servers

- International: `http://www.czhorde.cc/tr/bj{resolution}/`
- China: `http://www.czhorde.com/tr/bj{resolution}/`

Categories by filename prefix: a=Gallery, b=Tech, c=HUD, d=Light, e=Nature, y=Aesthetic

## Next Steps (Roadmap)

Prioritized list of remaining work:

### 1. ~~More Test Coverage~~ ✓ Done
- ~~test_theme_downloader.py~~ — 30 tests (registry, download, checksum, extract, install, remove)
- ~~test_cli.py~~ — 26 tests (arg parsing, dispatch, settings, color parsing, metrics)
- ~~test_controllers.py~~ — 43 tests (Theme/Device/Video/Overlay/FormCZTV controllers)

### 2. Coverage Reporting
- Add `pytest-cov` to CI workflow (`.github/workflows/tests.yml`)
- Target: measure actual line coverage, add badge to README
- `pip install pytest-cov` → `pytest --cov=trcc --cov-report=term-missing`

### 3. Type Checking
- Run `pyright` or `mypy --strict` across codebase
- Add type hints to functions missing them (system_info, sensor_enumerator especially)
- Consider adding to CI as a separate job

### 4. Stable Branch Sync
- stable is at `26e11a9`, main is at `78f2ddf` (3 commits ahead)
- Fast-forward stable after next milestone

### 5. Version Bump → 1.2.0
- Current: 1.1.1
- Bump when: coverage reporting + type checking are in place
- Update: `pyproject.toml`, `src/trcc/__version__.py`, `doc/CHANGELOG.md`

### 6. Packaging & Release
- Build wheel: `python -m build`
- Create GitHub Release with .whl artifact
- Consider PyPI publish

### 7. README Polish
- Add CI badge (tests passing)
- Add coverage badge
- Screenshot/demo GIF

## See Also

- `doc/INSTALL_GUIDE.md` - Detailed installation instructions for all distros
- `doc/ARCHITECTURE.md` - Project layout, MVC design, per-device config
- `doc/CHANGELOG.md` - Version history and release notes
- `doc/TECHNICAL_REFERENCE.md` - Protocol details, FBL codes, DC file formats
- `doc/PORTING_GUIDE.md` - How to port .NET/WinForms apps to Linux/PyQt6

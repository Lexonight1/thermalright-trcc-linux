# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TRCC Linux** - Native Linux port of Thermalright LCD Control Center (Windows TRCC 2.0.3) for controlling LCD displays on Thermalright CPU coolers, AIO pump heads, and fan hubs.

**Repository:** https://github.com/Lexonight1/thermalright-trcc-linux

**Current version: 1.1.3** (see `src/trcc/__version__.py`)

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

**1209 tests** across 21 test files — **96% coverage** on non-Qt backend (4696 stmts, 121 miss, 1462 branches, 144 partial)

Run per-module: `pytest tests/test_X.py --cov=trcc.X --cov-report=term-missing`

> **Note**: `pytest tests/` may hang during session cleanup due to cli.py subprocess test
> teardown. Run modules individually or with `timeout 60`.

| Test file | Module covered | Tests | Coverage |
|-----------|---------------|-------|----------|
| test_sysinfo_config | sysinfo_config | 18 | 100% |
| test_controllers | core/controllers | 169 | 99% |
| test_device_implementations | device_implementations | 32 | 99% |
| test_cloud_downloader | cloud_downloader | 47 | 98% |
| test_dc_writer | dc_writer | 46 | 98% |
| test_scsi_device | scsi_device | 35 | 97% |
| test_gif_animator | gif_animator | 86 | 96% |
| test_sensor_enumerator | sensor_enumerator | 72 | 96% |
| test_models | core/models | 78 | 96% |
| test_cli | cli | 66 | 95% |
| test_device_detector | device_detector | 60 | 95% |
| test_paths | paths | 54 | 95% |
| test_theme_downloader | theme_downloader | 54 | 95% |
| test_theme_io | theme_io | 17 | 95% |
| test_system_info | system_info | 107 | 94% |
| test_lcd_driver | lcd_driver | 25 | 94% |
| test_dc_parser | dc_parser | 81 | 92% |
| test_overlay_renderer | overlay_renderer | 66 | 92% |
| test_qt_constants | qt_components/constants | 25 | 100% |
| test_qt_base | qt_components/base | 27 | 83% |
| test_qt_widgets | qt_components widgets+assets | 44 | varies |

Qt tests require `QT_QPA_PLATFORM=offscreen` (headless, no display server).

### Testing Patterns & Gotchas

- **Patch at definition site**: Functions imported locally inside methods (e.g., `from ..scsi_device import func` inside a class method) must be patched at the definition module (`trcc.scsi_device.func`), NOT the importing module
- **Lazy imports in methods**: Several modules use lazy imports inside methods (`time` in `system_info.py`, `subprocess` in `device_detector.py`, `parse_dc_file` in `models.py`). Patch the global module (`time.sleep`), not the calling module's namespace
- **Local time import**: `system_info.py` imports `time` locally inside `get_disk_stats()`/`get_network_stats()` → patch `time.time` directly, not `trcc.system_info.time`
- **sorted() on MagicMock**: `sorted(path.glob('*'))` fails because MagicMock lacks `__lt__` → use `PurePosixPath` objects for hwmon test fixtures
- **GIFAnimator constructor**: `__init__(self, gif_path)` — only takes `gif_path`, no `size` parameter
- **dc_writer overlay_config format**: `font` must be dict `{'name': 'Arial'}` not string; `color` must be hex `'#FFFFFF'` not RGB tuple
- **smartctl output parsing**: Code splits lines and checks `part.isdigit()` → mock output needs digits as separate whitespace-delimited tokens
- **cloud_downloader cancel**: `download_category` resets `_cancelled = False` → use `side_effect` to set flag after first download call
- **VideoModel.load lazy imports**: Creates players via lazy import from `..gif_animator` — no `_create_player` method to mock
- **OverlayModel.load_from_dc**: Imports `parse_dc_file` lazily from `..dc_parser` — patch at `trcc.dc_parser.parse_dc_file`
- **theme_downloader real archives**: Tests creating real tar.gz files for `download_pack`/`create_local_pack` — use `tarfile` module with themes inside
- **cli.py session hang**: Test subprocess teardown sometimes hangs pytest session cleanup — use `timeout` wrapper or run individually
- **Remaining uncoverable lines**: Module-level `except ImportError` fallbacks (e.g., psutil in system_info L17-18, format_metric in overlay_renderer L15-22) are impractical to cover

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
├── paths.py                # XDG paths, per-device config, .7z extraction, cross-distro helpers
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
- Check `VIDEO_AVAILABLE` not `FFMPEG_AVAILABLE` for video support
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

## Cross-Distro Compatibility

All platform-specific helpers are centralized in `paths.py` (single source of truth):

- **`require_sg_raw()`** — verifies `sg_raw` is on `$PATH` via `shutil.which()`, raises `FileNotFoundError` with install instructions for 8+ distro families (Fedora, Debian, Arch, openSUSE, Void, Alpine, Gentoo, NixOS)
- **`find_scsi_devices()`** — dynamically scans `/sys/class/scsi_generic/` instead of hardcoded `range(16)`
- **`FONT_SEARCH_DIRS`** — 20+ font directories covering all major distros (Fedora, Debian/Ubuntu, Arch, Void, Alpine, openSUSE, NixOS, Guix, Garuda, Mint)
- **`FONTS_DIR`** — bundled fonts in `src/assets/fonts/` (first in search order)

Consumers import from `paths.py`:
- `scsi_device.py`, `lcd_driver.py` → `require_sg_raw()`
- `device_detector.py` → `find_scsi_devices()` (aliased as `_find_sg_entries`)
- `overlay_renderer.py` → `FONT_SEARCH_DIRS`, `FONTS_DIR`

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
- ~~Baseline 705 tests / 45% → 880 tests / 53%~~ (5 coverage sprints, committed `1fe3ec7`)
- 14 test files expanded across sprints 1-5
- All non-Qt backend modules now 74-97% covered

### 2. ~~CI/CD~~ ✓ Done
- GitHub Actions workflow in `.github/workflows/tests.yml`
- pytest + pyright checks

### 3. ~~Type Checking~~ ✓ Done
- pyright basic mode: 0 errors across full codebase

### 4. ~~Coverage Push to 95%+~~ ✓ Done
- 880 tests → **1209 tests** across 6 coverage sprints
- All 18 non-Qt backend modules now **92-100%** (combined **96%**)
- Modules pushed: gif_animator (50%→96%), overlay_renderer (71%→92%), sensor_enumerator (74%→96%), dc_parser (77%→92%), cli (86%→95%), cloud_downloader (88%→98%), system_info (88%→94%), device_detector (90%→95%), theme_downloader (90%→95%), device_implementations (92%→99%), models (96%→96%), dc_writer (94%→98%), paths (93%→95%), controllers (46%→99%)
- Remaining uncovered lines are module-level `except ImportError` fallbacks (impractical)
- Qt modules excluded (require display server / heavy mocking for diminishing returns)

### 5. ~~Cross-Distro Compatibility~~ ✓ Done
- Centralized all platform helpers in `paths.py` (single source of truth)
- `require_sg_raw()` with install instructions for 8+ distro families
- Dynamic SCSI scan via sysfs (no hardcoded `range(16)`)
- `FONT_SEARCH_DIRS` covering 20+ font paths across all major distros
- `os.system()` → `subprocess.run()` in cli.py
- Install guide covers 25+ Linux distributions

### 6. Linting / Formatting
- Add `ruff` for consistent style across codebase
- Auto-fix and enforce in CI

### 7. Type Annotation Hardening
- Move pyright from basic → strict on key modules
- Add missing type hints (system_info, sensor_enumerator especially)

### 8. Integration Tests
- End-to-end device detection → theme loading → LCD write pipeline (mocked hardware)

### 9. Version Bump → 1.2.0
- Current: 1.1.3
- Coverage target achieved (96% non-Qt backend)
- Cross-distro compatibility done
- Bump when: linting in place
- Update: `pyproject.toml`, `src/trcc/__version__.py`, `doc/CHANGELOG.md`

### 10. Packaging & Release
- Build wheel: `python -m build`
- Verify `pip install .` works cleanly, entry points
- Create GitHub Release with .whl artifact
- Consider PyPI publish

### 11. README Polish
- Add CI badge (tests passing)
- Add coverage badge
- Screenshot/demo GIF

## See Also

- `doc/INSTALL_GUIDE.md` - Detailed installation instructions for all distros
- `doc/ARCHITECTURE.md` - Project layout, MVC design, per-device config
- `doc/CHANGELOG.md` - Version history and release notes
- `doc/TECHNICAL_REFERENCE.md` - Protocol details, FBL codes, DC file formats
- `doc/PORTING_GUIDE.md` - How to port .NET/WinForms apps to Linux/PyQt6

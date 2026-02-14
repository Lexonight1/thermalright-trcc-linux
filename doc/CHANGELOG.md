# Changelog

## v3.0.2

### Bulk Protocol Fix
- **Fixed**: Bulk protocol frame header (correct magic bytes, kernel driver detach, chunked writes)
- **Fixed**: Legacy autostart cleanup — glob desktop files, remove duplicates
- Auto-detect GPU vendor for sensor mapping: NVIDIA > AMD > Intel
- CI: added FastAPI test dependencies (httpx, python-multipart) to dev extras
- 2156 tests across 33 files

## v3.0.1

### Full CLI Parity
- **36 Typer commands** expose all service methods — CLI, GUI, and REST API now have full feature parity
- New theme commands: `theme-save`, `theme-export`, `theme-import`
- New LED commands: `led-color`, `led-mode`, `led-brightness`, `led-off`, `led-sensor`
- New display commands: `brightness`, `rotation`, `screencast`, `mask` (with `--clear`), `overlay`
- New media command: `video` (plays video/GIF/ZT on LCD)
- Theme commands: `theme-list` (local + cloud), `theme-load` (find by name + send)
- All commands follow detect → service → print → return 0/1 pattern
- Backward-compatible module-level aliases for all new commands
- 2148 tests across 31 files

## v3.0.0

### Hexagonal Architecture
- **Services layer** (`src/trcc/services/`) — 8 service classes shared by GUI, CLI, and REST API:
  - `DeviceService` — detect, select, send_pil, send_rgb565
  - `DisplayService` — high-level display orchestration
  - `ImageService` — solid_color, resize, brightness, rotation, byte_order
  - `LEDService` — LED RGB control via LedProtocol
  - `MediaService` — GIF/video frame extraction
  - `OverlayService` — overlay rendering
  - `SystemService` — system sensor access
  - `ThemeService` — theme loading/saving/export/import
- **CLI refactored to Typer** — OOP command classes (DeviceCommands, DisplayCommands, ThemeCommands, LEDCommands, DiagCommands, SystemCommands)
- **REST API adapter** (`api.py`) — FastAPI endpoints for headless/remote control (optional `[api]` extra)
- **Module renames**: `paths`→`data_repository`, `sensor_enumerator`→`system_sensors`, `sysinfo_config`→`system_config`, `cloud_downloader`→`theme_cloud`, `driver_lcd`→`device_lcd`
- Dead code removed: `theme_io.py`, `constants.py`, `device_base.py`
- 2081 tests across 31 files

## v2.0.1

### FBL/PM Resolution Fix
- Fixed PM=36 (240x240) wrong resolution: unified FBL/PM tables into `constants.py` (single source of truth)
- PM=FBL default instead of hardcoded 320x320
- Fixed PyQt6 version in `trcc report`
- CI: add ffmpeg, auto-publish to PyPI on tag push
- Deleted dead shim files

## v2.0.0

### Major Refactor
- **Module renaming**: Consistent `device_*` / `driver_*` naming convention across all backend modules:
  - `bulk_device` → `device_bulk`, `hid_device` → `device_hid`, `led_device` → `device_led`
  - `scsi_device` → `device_scsi`, `lcd_driver` → `driver_lcd`, `kvm_led_device` → `device_kvm_led`
  - `gif_animator` → `media_player`
- **New modules**: `constants.py` (shared constants), `debug_report.py` (diagnostic tool), `device_led_hr10.py` (HR10 LED backend)
- All imports updated across 49 source files and 29 test files
- 2105 tests passing, ruff + pyright clean

## v1.2.16

### SELinux/Immutable Distro Fix
- **Fixed**: udev rules using `TAG+="uaccess"` which fails on SELinux-enforcing distros (Bazzite, Silverblue, Fedora Atomic)
- Now uses `MODE="0666"` for all udev rules — works universally across all Linux distros

## v1.2.15

### Stale Udev Detection
- **Auto-detect stale udev rules**: `trcc detect` now warns when USB storage quirk is missing and prompts `sudo trcc setup-udev` + reboot
- Prevents confusing "no device found" errors after adding new device support

## v1.2.14

### GrandVision 360 AIO Support
- Added GrandVision 360 AIO (VID `87AD:70DB`) as a known SCSI device
- Fixed sysfs VID readback for non-standard vendor IDs
- Device layer dedup: lcd_driver delegates to scsi_device, extracted `_create_usb_transport()`, `get_frame_chunks()` single source of truth
- Fixed LED PM/SUB byte offset (`pm=resp[5], sub=resp[4]`) — was off by one due to Windows Report ID prepend

## v1.2.13

### Format Button Fix
- **Fixed**: Format buttons (time/date/temp) not updating preview on fresh install
- Set `overlay_enabled` on theme load so format changes render immediately
- Persist format preferences (time_format, date_format, temp_unit) across theme changes via `conf.save_format_pref()`

## v1.2.12

### Fresh Install Overlay Fix
- **Fixed**: Overlay/mask changes not updating preview or LCD on fresh install (no saved device config)
- Root cause: `OverlayModel.enabled` defaults to `False` and was only set `True` by `start_metrics()`. On fresh install, no saved config → overlay disabled → `render_overlay_and_preview()` returned raw background unchanged
- `render_overlay_and_preview()` now uses `force=True` to always render through the renderer, bypassing the `enabled` check (preview path should always show edits)
- `_on_overlay_changed()` auto-enables overlay and starts metrics timer when user is actively editing elements

## v1.2.11

### LCD Send Pipeline Fix
- **Overlay changes**: Editing overlay elements (color, position, font, format, text) now sends the rendered frame to the LCD immediately
- **Mask toggle/reset**: Toggling or clearing mask now sends to LCD
- **Background toggle**: Switching to static background now sends to LCD
- **Image crop**: Cropped image now sends to LCD after crop completes
- **Video first frame**: Loading a video theme now shows the first frame on LCD immediately (was blank until timer fired)
- **send_current_image**: Now applies overlay before sending (was sending raw background)
- **_render_and_send**: Fixed to send overlay-rendered image, not raw `current_image`
- **DRY**: Extracted `_load_and_play_video()` — eliminates 5 scattered video load+play patterns

## v1.2.10

### First-Launch Preview Fix
- **Fixed**: Theme previews not showing on first GUI launch after `pip install` or upgrade
- Root cause: `Settings` singleton resolved paths at import time (before data existed), and `set_resolution()` short-circuited when resolution was unchanged — paths were never refreshed after `ensure_all_data()` downloaded archives

## v1.2.9

### HID Handshake Protocol Fix
- **LED routing fixed**: `hid-debug` now routes LED devices (Type 1) through `LedProtocol` instead of `HidProtocol` — was returning None immediately for all LED devices
- **Retry logic**: 3-attempt handshake with 500ms delay between retries (matching Windows UCDevice.cs `Timer_event`)
- **Timeout increased**: Handshake uses 5000ms timeout (was 100ms) — frame I/O stays at 100ms
- **Relaxed Type 2 validation**: Removed strict `resp[16] == 0x10` check (Windows doesn't require it)
- **Relaxed LED validation**: Bad magic/cmd bytes now warn instead of reject (matching Windows `DeviceDataReceived1`)
- **PM/SUB offset fix**: Type 2 LCD now reads `pm=resp[5], sub=resp[4]` (matching Windows Report ID offset)
- **Endpoint auto-detection**: `PyUsbTransport` enumerates actual device endpoints instead of hardcoding EP 0x02
- **Error visibility**: Actual USB exceptions logged and exposed via `protocol.last_error` instead of generic "None"
- **Hex dump in diagnostics**: `hid-debug` now shows raw handshake response bytes for bug reports

### OOP Refactor
- **DcConfig**: Merged `dc_parser.py` + `dc_writer.py` into single `DcConfig` class in `dc_config.py`
- **conf.py**: Moved all config persistence from `paths.py` into dedicated `conf.py` module
- **device_base.py**: Added `DeviceHandler` ABC and `HandshakeResult` base dataclass
- **Dataclasses**: `ThemeItem`/`LocalThemeItem`/`CloudThemeItem`/`MaskItem` replace raw dicts in theme browsers
- **DownloadableThemeBrowser**: Template method base class in `base.py` for cloud/mask/web browsers
- **gif_animator.py**: Simplified with cleaner class structure, removed dead code
- **paths.py**: Facade pattern with `ensure_all_data()`, cleaner on-demand download

## v1.2.8

### KISS Refactor
- Consolidated 5 duplicate Settings tab handlers (`_on_color_changed`, `_on_position_changed`, `_on_font_changed`, `_on_format_changed`, `_on_text_changed`) into single `_update_selected(**fields)` method in `uc_theme_setting.py`
- Each handler is now a one-liner delegating to `_update_selected` with keyword args
- Removed dead `set_format_options()` method from `overlay_renderer.py` (never called)
- Removed 6 dead LED legacy stubs from `models.py` (`_tick_static`, `_tick_breathing`, `_tick_colorful`, `_tick_rainbow`, `_tick_temp_linked`, `_tick_load_linked`) — production dispatches via `_tick_single_mode` directly

## v1.2.7

### Strip Theme Data from Wheel
- Removed all theme data from the pip wheel — themes download on first run only
- `_has_actual_themes()` now requires PNG files (ignores leftover `.dc` files)

## v1.2.6

### Config Marker Verification
- Fixed stale config marker: `is_resolution_installed()` now verifies data exists on disk
- Added debug logging for theme setup, tab switches, and directory verification

## v1.2.5

### One-Time Data Setup
- Download themes/previews/masks once per resolution, tracked in `~/.trcc/config.json`
- `is_resolution_installed()` / `mark_resolution_installed()` skip re-download on subsequent launches
- Custom themes saved to `~/.trcc/data/` (survives pip upgrades)

## v1.2.4

### Fix pip Upgrade Data Loss
- Theme archives now extract to `~/.trcc/data/` instead of site-packages
- `install-desktop` generates `.desktop` file inline for pip installs (no bundled template needed)

## v1.2.3

### Logging Refactor
- Replaced `print()` with `logging` across 12 modules
- Thread-safe device send with lock
- Extracted `_setup_theme_dirs()` helper
- Suppressed `pyusb` deprecation warning (`_pack_` in ctypes)

## v1.2.2

### Fix Local Theme Loading
- Fixed local themes not loading from pip install (`Custom_*` dirs blocked on-demand download)

## v1.2.1

### Debug Logging & HID Fix
- Fixed RGB565 byte order for non-320x320 SCSI devices
- Fixed GUI crash on HID handshake failure
- Added verbose debug logging (`trcc -vv gui`)

## v1.2.0

### HR10 2280 PRO Digital Support
- 7-segment display renderer (`hr10_display.py`) — converts text + unit into 31-LED color array
- NVMe temperature daemon (`hr10-tempd` CLI command) — reads sysfs temp, breathe animation, thermal color gradient
- LED diagnostic command (`led-debug`) — handshake + test colors for LED devices
- Interactive HSV color wheel widget (`uc_color_wheel.py`) for LED hue selection
- 7-segment preview widget (`uc_seven_segment.py`) — QPainter rendering matching physical HR10 display
- Unified LED panel — all LED device styles (1-13) handled by single `UCLedControl` panel, matching Windows FormLED.cs
- HR10-specific widgets (drive metrics, display selection, circulate mode) shown conditionally in the LED panel
- LED segment visualization widget (`uc_screen_led.py`) with colored circles at segment positions
- Contributor: [Lcstyle](https://github.com/Lcstyle) (GitHub PR #9)

### Controller Naming
- Renamed `FormCZTVController` → `LCDDeviceController` (LCD orchestrator)
- Renamed `FormLEDController` → `LEDDeviceController` (LED orchestrator)
- Consistent naming convention: `{Domain}Controller` for state engines, `{Protocol}DeviceController` for orchestrators

### Autostart on Login
- Auto-enable autostart on first GUI launch (matches Windows `KaijiQidong()` behavior)
- Creates `~/.config/autostart/trcc.desktop` with `trcc --last-one` on first run
- `--last-one` launches GUI minimized to system tray and sends last-used theme
- Settings panel checkbox reflects and toggles the actual autostart state
- `trcc resume` command for headless theme send (scripting/cron)

### Reference Theme Save
- Custom saved themes use `config.json` with path references (no file copies)
- On save: writes `config.json` + `Theme.png` thumbnail to `Custom_{name}/`
- On load: resolves background/mask/overlay paths from `config.json`
- Fixes overlay-on-resume: overlay config persisted in theme JSON

### Protocol Reverse Engineering
- Complete SCSI protocol reference from USBLCD.exe (Ghidra decompilation)
- Complete USB bulk protocol reference from USBLCDNEW.exe (.NET/ILSpy)
- Documented all CDB commands, handshake sequences, frame transfer formats
- New docs: [USBLCD_PROTOCOL.md](USBLCD_PROTOCOL.md), [USBLCDNEW_PROTOCOL.md](USBLCDNEW_PROTOCOL.md)

### LCD Blink Mitigation
- SCSI init now checks poll response for `0xA1A2A3A4` boot signature (matching USBLCD.exe)
- If device is still booting, waits 3s and re-polls (up to 5 retries)
- Added 100ms post-init delay to let display controller settle before first frame

### Code Quality
- `ruff` linting enforced in CI (E/F/W/I rules) — 0 violations across 26 files
- Fixed 122 ruff violations (unsorted imports, unused vars, f-strings, lambda, class redefinition)
- Removed unused imports across 10 files
- Sorted all import blocks (isort)
- Removed redundant `requirements.txt` (`pyproject.toml` is single source of truth)
- Deduplicated magic bytes: `led_device.LED_MAGIC` now imports `TYPE2_MAGIC` from `hid_device` (was copy-pasted)
- Extracted `_select_item()` into `BaseThemeBrowser` — shared visual selection logic for local, cloud, and mask browsers
- Centralized `pil_to_pixmap()` usage in `UCImageCut` (was inline 5-line conversion)
- Added `_create_info_panel()` factory in `UCLedControl` — eliminates duplicate QFrame+label creation for mem/disk panels
- Consolidated 5 repeated stylesheet strings into module-level constants (`_STYLE_INFO_BG`, `_STYLE_INFO_NAME`, etc.)

### On-Demand Download
- All 15 LCD resolutions now have bundled `.7z` theme archives (was 4)
- New resolutions: 240x320, 360x360, 800x480, 854x480, 960x540, 1280x480, 1600x720, 1920x462, plus portrait variants
- On-demand download for themes, cloud previews, and mask archives from GitHub on first use
- 33 Web archives (cloud previews + masks) for all resolutions
- Downloaded archives stored in `~/.trcc/data/` when package dir is read-only (pip install)
- Cross-distro 7z install help shown when system `7z` is not available

### HID Device Identification
- PM→FBL→resolution mapping from Windows FormCZTV.cs (all known PM byte values)
- `pm_to_fbl()` and `fbl_to_resolution()` functions in `hid_device.py`
- `PM_TO_BUTTON_IMAGE` mapping for dynamic sidebar button updates after handshake
- New `trcc hid-debug` CLI command — hex dump diagnostic for HID bug reports

### Packaging
- Assets and data moved into `trcc` package (`src/trcc/assets/`, `src/trcc/data/`)
- `pip install .` now produces a complete wheel with all GUI images, fonts, and themes
- HID devices auto-detected — `--testing-hid` flag no longer needed

### Bug Fixes
- Fixed resume RGB565 conversion to match GUI (big-endian + masked)
- Fixed LEDMode int-to-enum conversion crash on config save
- Fixed device config persistence for autostart theme state
- Fixed json import ordering in controllers
- Fixed saved theme overlay not rendering (overlay must enable before background load)
- Fixed tab 2 mask not persisting in saved themes (mask source directory tracking)
- Fixed `install_desktop()` icon path (`src/assets/` → `src/trcc/assets/`)
- Fixed `install.sh` desktop shortcut using generic icon instead of TRCC icon
- Fixed CI workflow missing libEGL and QT_QPA_PLATFORM for PyQt6 tests

### Documentation
- Added [Supported Devices](SUPPORTED_DEVICES.md) page with all USB IDs
- Added [Development Status](DEVELOPMENT_STATUS.md) tracking page
- Expanded [Technical Reference](TECHNICAL_REFERENCE.md) with full SCSI command table
- Removed `--testing-hid` references from all docs (HID is auto-detected)

### Testing
- 2029 tests across 27 test files (up from 1777)
- 96% coverage on non-Qt backend

## v1.1.3

### LED RGB Control (`hid-protocol-testing` branch)
- RGB LED control panel (FormLED equivalent) for HID LED devices (e.g. AX120 DIGITAL)
- 7 LED effect modes: Static, Breathing, Rainbow, Cycle, Wave, Flash, Music
- Per-segment and global on/off toggles, brightness slider, color picker
- `LedProtocol` in device_factory.py routes LED devices separately from LCD HID
- `FormLEDController` with tick-based animation loop (30ms, matching Windows timer1)
- `led_device.py`: LED styles, packet builder, HID sender, effect engine
- `uc_led_control.py`: PyQt6 LED control panel with segment grid and mode buttons
- Device routing: sidebar auto-routes HID LED devices to LED view instead of LCD form
- 376 new LED tests (245 led_device + 131 led_controller)

### Save Custom Theme Fixes
- Fixed font size DPI corruption on save — sizes no longer inflate each save cycle (36pt → 48pt → 64pt)
- Fixed font charset lost on save — preserves GB2312 charset (134) instead of defaulting to 0
- Fixed font style default — Regular (0) instead of Bold (1), matching Windows `new Font()` defaults
- Lossless DC config round-trip: original parsed DC data preserved in `OverlayModel._dc_data` and merged on save instead of reconstructing from scratch
- Cloud video themes now copy MP4 to working dir for inclusion in saved custom themes
- `ThemeInfo.from_directory()` now detects `.mp4` files in theme directories (not just Theme.zt)
- Saved custom themes with MP4 backgrounds properly reload as video on reopen

### Cross-Distro Compatibility
- Centralized all platform-specific helpers in `paths.py` (single source of truth)
- `require_sg_raw()` with install instructions for 8+ distro families (Fedora, Debian, Arch, openSUSE, Void, Alpine, Gentoo, NixOS)
- Dynamic SCSI device scan via `/sys/class/scsi_generic/` (replaces hardcoded `range(16)`)
- `FONT_SEARCH_DIRS` covering 20+ font directories across all major Linux distros
- Replaced `os.system()` with `subprocess.run()` in cli.py for security/correctness
- Install guide expanded to cover 25+ Linux distributions

### CI / Testing
- Added `hid-protocol-testing` branch to GitHub Actions test workflow (Python 3.10, 3.11, 3.12)
- 563 HID/LED tests (114 device protocol + 73 factory/routing + 245 LED device + 131 LED controller) — total 1777 tests across all branches
- Fixed Python 3.12 `mock.patch` failure for optional `pynvml` import
- Added `ruff` to dev dependencies for CI lint step

### Documentation
- Added HID device PIDs (`0416:5302`, `0418:5303`, `0418:5304`) to supported devices
- Split README device tables into SCSI (stable) and HID (testing) sections with USB IDs
- Added `lsusb` example to help users identify their device
- Created [Device Testing Guide](DEVICE_TESTING.md) with install, switch, and reporting instructions
- Added CI badge to README
- Added [CLI Reference](CLI_REFERENCE.md) with all commands, options, and troubleshooting
- Updated Documentation table on all branches

### Autostart on Login
- Auto-enable autostart on first GUI launch (matches Windows `KaijiQidong()` behavior)
- Creates `~/.config/autostart/trcc.desktop` with `trcc --last-one` on first run
- Checkbox in Settings panel reflects and toggles the actual autostart state
- Subsequent launches refresh the `.desktop` file if the install path changed
- 15 new autostart tests covering first-launch, toggle, path refresh, and edge cases

### Bug Fixes
- Theme.png preview now includes rendered overlays and masks (was showing raw background only)
- `dc_writer.py` only writes fallback Theme.png if one doesn't already exist (controller writes the better rendered version)
- Fixed cli.py `--version` flag (was stuck at 1.1.0)

## v1.1.2

### Bug Fixes
- Fixed LCD send: init handshake (poll + init) was being skipped on first frame send
- Dynamic frame chunk calculation for all resolutions (was hardcoded to 320x320)
- Local themes grid now sorts default themes (Theme1-5) first
- Added Qt6 installation docs for additional distros

### Test Suite (1209 tests, 96% coverage)
- Expanded from 880 → 1209 tests across 6 coverage sprints
- All 18 non-Qt backend modules now 92-100% covered (combined 96%)
- Added 3 Qt component test files (test_qt_constants, test_qt_base, test_qt_widgets)

## v1.1.1

### Test Suite (298 tests)
- Added test_dc_writer (18 tests): binary write, roundtrip, overlay_config_to_theme, carousel, .tr export/import
- Added test_paths (25 tests): config persistence, per-device config, path helpers, resolution/temp unit
- Added test_sysinfo_config (18 tests): config load/save, defaults, auto_map
- Added test_device_implementations (25 tests): RGB565 conversion, resolution, commands, registry
- Added test_scsi_device (18 tests): CRC32, header building, frame chunking
- Added test_models (30 tests): ThemeInfo, ThemeModel, DeviceModel, VideoState, OverlayModel
- Added test_theme_io (14 tests): C# string encoding, .tr export/import roundtrip
- Removed 7 dead test files (1490 lines) importing non-existent modules
- Fixed 3 RGBA→RGB assertion mismatches in overlay renderer tests
- Existing tests: test_dc_parser (133), test_device_detector (17), test_overlay_renderer (25)

### Bug Fixes
- Fixed `dc_writer.py`: `overlay_config_to_theme()` and `import_theme()` called `DisplayElement()` without required positional args — runtime crash
- Fixed `theme_io.py`: `export_theme()` missing `bg_len` write when no background image — caused import to read past EOF

## v1.1.0

- Per-device configuration — each LCD remembers its theme, brightness, rotation, overlay, and carousel
- Carousel mode — auto-rotate through up to 6 themes on a timer
- Theme export/import — save/load themes as `.tr` files
- Video trimmer — trim videos and export as `Theme.zt` frame packages
- Image cropper — crop and resize images for any LCD resolution
- Fullscreen color picker — eyedropper tool for picking screen colors
- Dynamic font and coordinate scaling across resolutions
- Font picker dialog for overlay elements
- Mask toggle to hide/show instead of destroying mask data
- Mask reset/clear functionality
- Screen cast with PipeWire/Portal support for Wayland
- Sensor customization dashboard with reassignable sensor slots
- Overlay element cards matching Windows UCXiTongXianShiSub exactly
- Font name and style preservation when loading themes
- Fixed disabled overlay elements being re-enabled on property changes
- Fixed 12-hour time format (2:58 PM instead of 02:58 PM)
- Video resume when toggling video display back on

## v1.0.0

- Initial release
- Full GUI port of Windows TRCC 2.0.3
- Local and cloud theme support
- Video/GIF playback with FFmpeg
- Theme editor with overlay elements
- System info dashboard with 77+ sensors
- Screen cast functionality
- Multi-device and multi-resolution support

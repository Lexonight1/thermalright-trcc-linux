# Changelog

## v1.2.0

### HR10 2280 PRO Digital Support
- 7-segment display renderer (`hr10_display.py`) — converts text + unit into 31-LED color array
- NVMe temperature daemon (`hr10-tempd` CLI command) — reads sysfs temp, breathe animation, thermal color gradient
- LED diagnostic command (`led-diag`) — handshake + test colors for LED devices
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
- Cross-distro 7z install help shown when neither `py7zr` nor system `7z` is available

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
- 1988 tests across 26 test files (up from 1777)
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
- Created [HID Testing Guide](HID_TESTING.md) with install, switch, and reporting instructions
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

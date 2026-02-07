# Changelog

## v1.1.3

### Cross-Distro Compatibility
- Centralized all platform-specific helpers in `paths.py` (single source of truth)
- `require_sg_raw()` with install instructions for 8+ distro families (Fedora, Debian, Arch, openSUSE, Void, Alpine, Gentoo, NixOS)
- Dynamic SCSI device scan via `/sys/class/scsi_generic/` (replaces hardcoded `range(16)`)
- `FONT_SEARCH_DIRS` covering 20+ font directories across all major Linux distros
- Replaced `os.system()` with `subprocess.run()` in cli.py for security/correctness
- Install guide expanded to cover 25+ Linux distributions

### CI / Testing
- Added `hid-protocol-testing` branch to GitHub Actions test workflow (Python 3.10, 3.11, 3.12)
- 187 HID protocol tests (114 device protocol + 73 factory/routing) — total 1396 tests across all branches
- Fixed Python 3.12 `mock.patch` failure for optional `pynvml` import
- Added `ruff` to dev dependencies for CI lint step

### Documentation
- Added HID device PIDs (`0416:5302`, `0418:5303`, `0418:5304`) to supported devices
- Split README device tables into SCSI (stable) and HID (testing) sections with USB IDs
- Added `lsusb` example to help users identify their device
- Created HID Testing Guide with install, switch, and reporting instructions
- Added CI badge to README
- Added [CLI Reference](CLI_REFERENCE.md) with all commands, options, and troubleshooting
- Updated Documentation table on all branches

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

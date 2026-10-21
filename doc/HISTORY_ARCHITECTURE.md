# Architecture History — GoF Refactoring & SOLID Evolution

This documents the architectural refactoring journey from v6.0.0 through v8.1.9, then summarises the v9 era that produced today's tree. The modules named in the v6–v8 sections are history; most no longer exist. For per-version changelogs, see `CHANGELOG.md`. For the current architecture, see `GUIDE_ARCHITECTURE.md`.

## GoF Refactoring (COMPLETE — v6.0.0 through v7.0.10)

### All Phases
- **Phase 1: Segment Display Collapse** — `led_segment.py` 1109→687 lines (-422, 38%). Properties→class attrs, 4 encode methods→unified `_encode_digits()` + `_encode_7seg()`, LF12 delegates to LF8. Flyweight + Strategy.
- **Phase 2: HID Subclasses — SKIPPED** — Template Method already well-applied, logic genuinely differs between Type2/Type3. ~20 line savings not worth it.
- **Phase 3: Controller Layer Elimination** — `controllers.py` 699→608 lines (-91). Deleted 5 thin wrapper controllers (ThemeController, DeviceController, VideoController, OverlayController, LEDController). LCDDeviceController = Facade over 4 services (~35 methods). LEDDeviceController absorbed LEDController. ~50 GUI call sites rewritten, 7 test files updated. Law of Demeter enforced: GUI→Facade→Services only.
- **Phase 4: UsbProtocol Base** — `factory.py` 874→846 lines (-28). Extracted shared transport lifecycle (open/close/ensure) from HidProtocol + LedProtocol into `UsbProtocol` base class. Template Method.
- **Phase 5: LED Config Serialization** — `services/led.py` save/load driven by `_PERSIST_FIELDS` dict + `_ALIASES` dict. Single source of truth for which fields persist. Memento pattern.
- **Total**: 24 files changed, -1203 net lines.

### v6.0.1 Extensions
- **CLI Dispatchers** (deleted in v7.0.6): `LEDDispatcher` + `DisplayDispatcher` were Command pattern wrappers — replaced by `LCDDevice`/`LEDDevice` direct methods.
- **Metrics Observer**: `UCLedControl.update_metrics()` — panel dispatches to style-specific update methods internally. `qt_app_mvc._poll_sensors()` reduced from 15 lines to 2. Observer pattern.
- **ANSI Preview**: `--preview` flag on all LCD/LED CLI commands renders true-color terminal art. `ImageService.to_ansi()` for stills, `to_ansi_cursor_home()` for video.
- **LED Visual Test Harness**: `tests/gui/test_led_visual.py` — standalone Qt app for testing all 12 LED styles with live metrics, device buttons, index overlay, and signal wiring.

### v6.1.5: Portrait Cloud Directory Switching
- **Non-square displays (e.g. 1280x480 Trofeo Vision) mounted vertically** were loading cloud backgrounds/masks from the landscape directory (`1280480/`) instead of the portrait directory (`4801280/`).
- **Root cause**: `_on_rotation_change()` set rotation but didn't re-resolve cloud/mask directories. `_apply_device_config()` similarly restored rotation without resolving portrait directories.
- **Fix**: Added `Settings.resolve_cloud_dirs(rotation)` — swaps width/height for web_dir and masks_dir when rotation is 90°/270° on non-square displays. Wired into both `_on_rotation_change()` and `_apply_device_config()`.
- **C# reference**: `GetWebBackgroundImageDirectory()` (FormCZTV.cs:3749) and `GetFileListMBDir()` (FormCZTV.cs:4255) both check `directionB` for portrait switching. Local themes (`ThemeML`) stay landscape — only cloud dirs switch.
- **No portrait local theme pack exists** — Windows doesn't ship `Theme4801280` either. Only cloud backgrounds/masks have portrait variants.
- Addresses #1. 5 new tests (2408 total).

### v6.1.3–v6.1.4: LED GUI Settings & Theme Restore Fix
- **LED GUI settings not syncing on startup**: `load_config()` correctly restored LED state, but `panel.initialize()` reset controls to defaults. Added `_sync_ui_from_state()` to push loaded state into UI after initialization.
- **`--last-one` theme restore overwriting saved preference**: Auto-fallback was persisting to config, silently overwriting the user's saved theme. Now uses `persist=False` for fallback loads.
- v6.1.4 is a re-release of v6.1.3 (PyPI rejects reuse of version+filename after tag move).

### v6.1.1–v6.1.2: Wire Remap Audit
- **Full ReSetUCScreenLED audit**: All 12 styles checked against C# `ReSetUCScreenLED*()` overrides. Styles 2 (PA120), 3 (AK120), 4 (LC1) had default constructor indices instead of style-specific overrides. 9 other styles verified correct.
- **Guard test**: `test_all_remap_indices_in_range` checks `idx < style.led_count` — catches this class of bug automatically.
- **Root cause pattern**: C# `UCScreenLED` constructor assigns Cpu1=2, SSD=6, etc. but `ReSetUCScreenLED{N}()` overrides per style. Remap tables must use the overridden indices.

### v7.0.x: GoF File Renames + SOLID Architecture
- **GoF File Renames (v7.0.1)**: 13 files in `adapters/device/` renamed to `{pattern}_{name}.py` — every adapter file named by its primary design pattern (e.g., `factory.py` → `abstract_factory.py`, `frame.py` → `template_method_device.py`)
- **SOLID Refactoring (v7.0.2)**: All 5 SOLID principles applied to device protocol architecture:
  - **ISP**: Split `DeviceProtocol` into `LCDMixin` (send_image, send_pil) + `LEDMixin` (send_led_data)
  - **LSP**: Removed `LedProtocol.send_image()` returning False, `DeviceProtocol.send_led_data()` default
  - **DIP**: Injected protocol factory into `DeviceService` via `get_protocol` param + `_get_proto()` method
  - **SRP**: Moved `detect_lcd_resolution()` from `DeviceService` to `ScsiDevice.detect_resolution()`
  - **OCP**: Added `@DeviceProtocolFactory.register()` decorator for self-registering protocols
- **Explicit Dependencies (v7.0.3)**: Added `click` as direct dependency (was transitive through `typer`). Addresses #50.
- **API DRY (v7.0.4)**: Extracted `require_connected()` into `api/models.py` — eliminated 4 duplicated dispatcher guard patterns.

### v7.0.5: QtRenderer — Eliminate PIL from Hot Path
- **Renderer ABC expanded** (`core/ports.py`): Added apply_brightness, apply_rotation, encode_rgb565, encode_jpeg, open_image, surface_size to existing Renderer ABC
- **QtRenderer** (`adapters/render/qt.py`): Full QImage/QPainter implementation — compositing, text, rotation, brightness, RGB565/JPEG encoding, font resolution. Zero PIL in hot path.
- **PilRenderer** (`adapters/render/pil.py`): Same new methods implemented with PIL (fallback only)
- **ImageService** (`services/image.py`): Now a thin facade — all methods delegate to `_renderer` via `set_renderer()` / `_r()`. Defaults to QtRenderer.
- **Font pixel sizing**: `QFont.setPixelSize(size)` — PIL callers pass pixel sizes, Qt `QFont(family, size)` interprets as points. Must use `setPixelSize()`.
- **Test infrastructure**: `conftest.py` helpers `make_test_surface()`, `surface_size()`, `get_pixel()` — all tests use native renderer surfaces
- **PIL boundary conversion**: PIL Images entering the system converted once via `renderer.from_pil()`, then flow as QImage throughout

### v7.0.6: SOLID Device ABCs — Replace Controller Layer
- **Device ABC** (`core/ports.py`): 4 methods (connect, connected, device_info, cleanup). Minimal contract for all devices.
- **LCDDevice** (`core/lcd_device.py`): Direct methods (capability classes inlined in v8.0.0). Delegates to services.
- **LEDDevice** (`core/led_device.py`): Direct methods — set_color, set_mode, tick, zone/segment ops. Delegates to LEDService.
- **ControllerBuilder** (`core/builder.py`): Fluent builder, returns concrete `LCDDevice`/`LEDDevice` types (not `Device` ABC).
- **TRCCApp** (`gui/trcc_app.py`): Thin QMainWindow shell (C# Form1 equivalent). Handlers dict, one per device.
- **LCDHandler** (`gui/lcd_handler.py`): One per LCD device (C# FormCZTV equivalent). Owns LCDDevice, timers, state.
- **CLI slimmed**: `_display.py` and `_led.py` are thin print wrappers — `_connect_or_fail()` → call device method → print result.
- **Deleted**: `core/controllers.py` (LCDDeviceController + LEDDeviceController), backward compat aliases (DisplayDispatcher, LEDDispatcher), 197 dead tests.

### v7.0.7–v7.0.10: Bug Fixes, Cloud Parity, CI Package Deps
- **Cloud theme resolution parity**: All 32 C# v2.1.2 resolutions added to `theme_cloud.py` RESOLUTION_URLS and `tools/pack_theme_archives.py` — landscape, portrait, u/l split variants. Full match of `FormCZTV` `GifDirectoryWeb*`/`GifWebDir*` constants.
- **CI distro package dependencies fixed**: `release.yml` inline package specs (RPM, DEB, Arch `.PKGINFO`) had missing/incomplete Python deps. Root cause of #51 (typer not found on CachyOS). All three formats now declare full dependency lists matching `pyproject.toml`.
- **`tools/check_pkg_deps.py`**: NEW tool — queries Arch, Fedora, Debian repos to verify which PyPI deps have native packages vs need bundling. Found: Arch missing `python-uvicorn` (must bundle via pip), Fedora/Debian all available.
- **CodeQL fix**: Stack trace exposure in `api/display.py` preview endpoint (CWE-209). Wrapped `_encode_frame` in try/except.
- **Bulk RGB565 encoding fix**: v7.0.10 corrected bulk protocol encoding.

### v7.1.0–v7.1.1: Bulk FBL Bug, Theme Persist, System Install Fix
- **Bulk/LY FBL bug (#54)**: `BulkDevice.handshake()` and `LyDevice.handshake()` returned `model_id=PM` (raw handshake byte) instead of `model_id=FBL` (lookup code). GUI stored PM as `fbl_code` → `DeviceInfo.use_jpeg` computed wrong encoding. PM=32 bulk devices got JPEG encoding when they need RGB565 → scrambled display. Fixed: both now return `pm_to_fbl(pm, sub)`.
- **Theme persist on first boot**: `_restore_theme()` fallback used `persist=False`, so `--last-one` autostart never saved the theme. Fixed: `persist = not saved` — persists when no prior save existed.
- **Handshake guard**: Added `_handshake_pending` flag to prevent duplicate concurrent handshakes from device poll timer.
- **Log noise**: Removed per-frame DEBUG logs (~30/sec) from display, device, image services and factory — was rotating out useful INFO messages within seconds of video playback.
- **PermissionError on system-wide installs (#51)**: `_find_data_dir()`, `get_web_dir()`, `get_web_masks_dir()` fell back to read-only package path (`/usr/lib/python3.x/.../trcc/data/`) when no themes existed. Cloud theme `mkdir` crashed. Fixed: fallback is now `USER_DATA_DIR` (`~/.trcc/data/`), always user-writable.
- **CodeQL alert**: Restructured preview endpoint exception handling to prevent stack trace flow analysis false positive (CWE-209).

### v7.1.2–v7.1.4: CLI Device Selection, Software Update System
- **CLI auto-select fix**: `_get_service()` falls back to first detected device when saved device path doesn't match. Removed premature auto-select from `DeviceService.scan()` — selection is caller's responsibility.
- **Software update system** (`uc_about.py`): Version check uses GitHub releases API (not PyPI). Detects install method (pip/pipx/pacman/dnf/apt) and distro on first launch, persists to `config.json` via `Settings.get_install_info()`/`save_install_info()`. Update button toggles dark→light overlay when update available. Click triggers method-appropriate upgrade: pip/pipx run directly, package managers download from release assets + `pkexec` for sudo prompt.
- **Install info in config**: `config.json` stores `install_info.method` and `install_info.distro` — detected once, read forever. No runtime guessing after first launch.
- **GitHub release assets**: Package download URLs come from the release JSON (no hardcoded filenames). Handles Fedora version changes automatically.
- **CodeQL fix**: Restructured preview endpoint try/except to satisfy flow analysis (CWE-209).

### v7.1.5: Brightness Persist, Overlay Restore, Test Warnings
- **Brightness not persisting across restarts**: `_restore_brightness` called `DisplaySettings.set_brightness(percent)` which re-persisted the percent value (100) as `brightness_level`, overwriting the saved level (3). Next restart mapped 100 via `{1:25, 2:50, 3:100}.get(100, 50)` → 50% fallback. Fixed: restore now sets `DisplayService.brightness` directly, bypassing `DisplaySettings` persist side-effect. Also added `_update_ldd_icon()` after `apply_device_config()` so brightness button icon reflects restored level.
- **Stale overlay on custom theme restart (#58)**: Overlay config in `config.json` is per-device, not per-theme. Switching from official theme (with overlay) to custom theme (without) left stale overlay saved. On restart, custom theme loaded then `_restore_overlay` applied the old overlay. Fixed: `_load_theme_overlay_config` now clears and persists `enabled: False` when theme has no overlay config.
- **Test warnings eliminated**: QMouseEvent deprecated 5-arg constructor → 6-arg (added `globalPos`). Unclosed PIL `Image.open()` in `_load_mask_into` → context manager. Unclosed `HTTPError` in test mocks → explicit `.close()`. Unclosed `Image.open()` in `test_dc_writer` → context manager. pyusb `_pack_` filter fixed (`usb` → `usb.*`).

### v8.0.0: Hexagonal Purification + CPU Optimization (-684 lines, 48%→6% CPU with MP4)
- **Hexagonal violations fixed**: `led_segment.py`, `color.py`, `paths.py` moved from `adapters/` → `core/`. Lazy-import `DataManager` in `services/display.py` (no adapter imports at module level in services).
- **Double sensor polling eliminated**: `UCInfoModule` and `UCActivitySidebar` had their own polling timers AND MetricsMediator subscriptions — double work. Removed redundant timers; MetricsMediator is now the single polling authority.
- **Preview skip when minimized**: `LCDHandler` accepts `is_visible_fn` from `TRCCApp`. Video tick and overlay render skip `set_image()` when window is minimized — no QImage scaling or QPixmap conversion for invisible widgets.
- **QMovie visibility management**: Cloud theme GIF thumbnails (`CloudThemeThumbnail._movie`) created but NOT started. `UCThemeWeb.showEvent()`/`hideEvent()` start/stop all QMovies — zero CPU when cloud panel not visible.
- **VideoFrameCache rewrite**: Replaced bulk `_build_layer4()` with lazy per-frame `_ensure_frame()` — only encodes current frame on access, caches last result. Matches C# `FormCZTV.Timer_event` approach.
- **Capability classes inlined**: `ThemeOps`, `VideoOps`, `OverlayOps`, `FrameOps`, `DisplaySettings` dissolved into `LCDDevice` directly — unnecessary indirection removed.
- **DeviceProfile table**: Replaces scattered encoding logic with a single data-driven lookup.
- **LED segment data consolidated**: `core/led_segment.py` now owns all segment display data (was in `adapters/device/led_segment.py`).
- **data_repository.py DRY**: -139 lines of duplicated archive extraction logic.
- 48 files changed, -684 net lines

### v8.0.2: Test Restructuring — Hexagonal Directory Layout
- **Test directory mirrors source**: 53 test files reorganized from flat `tests/` into subdirectories matching `src/trcc/` hexagonal layers: `tests/{core,services,adapters/{device,infra,system},cli,api,gui}/`. Cross-cutting tests (architecture, integration, memory, conf) stay at `tests/` root.
- **Merged duplicates**: `test_api_ext.py` → `test_api.py`, `test_doctor_ext.py` → `test_doctor.py`, `test_debug_report_ext.py` → `test_debug_report.py`
- **Dissolved `hid_testing/`**: Tests moved to `adapters/device/`, conftest fixtures merged into `adapters/device/conftest.py`
- **Deleted 22 mock-wiring tests**: Tests that only verified `mock.assert_called_once_with()` — passed even with broken code
- 4021 tests passing, ruff clean, pyright clean

### v8.1.0: Strict Dependency Injection
- **All service constructors strict**: `RuntimeError` if required adapter deps not provided. No lazy fallback imports in services.
- **Composition roots fully wired**: `builder.py`, CLI functions, `api/__init__.py`, `lcd_device.py:_build_services()` all inject concrete adapter deps.
- **Services never import adapters**: One accepted exception — `SystemService._get_instance()` (convenience singleton, composition root).
- **LCDDevice stores DC deps**: `dc_config_cls` and `load_config_json_fn` injected at construction for overlay/mask methods.
- **Cloud thumbnail blank fix**: `_on_download_complete()` calls `_set_movies_running(True)` when panel is visible.
- **conftest fixtures**: `tests/services/conftest.py` with shared DI-wired service fixtures.
- 4021 tests across 56 files

### v8.1.4: Slideshow Carousel Fix
- **Theme slideshow not rotating**: `_on_slideshow_tick()` bailed out when `video.playing` was True — stale state from a previously loaded animated/video theme. Timer fired every 3s but the early return skipped all theme changes.
- **Fix**: Instead of returning when video is playing, stop video and animation timer, then proceed with theme rotation.

### v8.1.9: LED View Switch Fix (#61)
- **LED display going dark on view switch**: `_show_view()` called `_led.stop()` whenever navigating away from the LED panel (back button, about, sysinfo). `stop()` closes the USB transport, so the physical LED display stopped receiving data and went dark.
- **Fix**: Removed `_led.stop()` from `_show_view()`. LED keeps running independently of which GUI panel is shown. Only stops on full app quit or explicit device switch to LCD.

## The v9 Era — Rebuild, Cutover, One Shared App

- **Command bus, first attempt (v9.1.4, `70683c66`)**: CLI, API and GUI dispatched frozen-dataclass Commands through a `CommandBus` with middleware and per-context handlers.
- **Command bus removed (`b259fb19`, first released in v9.2.7)**: `core/command_bus.py`, `core/commands/` and `core/handlers/` deleted. UIs called `LCDDevice`/`LEDDevice` methods directly.
- **Clean-slate rebuild (v9.4.8, `c309a557`)**: a new tree, `trcc.next`, opt-in via `trcc-next` / `TRCC_NEXT=1`. Its first commit brought back a Command bus contract: a `Command` ABC whose subclasses return typed Results. That contract grew into today's `app.dispatch(cmd)`, with no handler map.
- **Cutover (v9.7.0, `4fa876be`)**: `next/` promoted to the `trcc` package root, with the old tree kept as `trcc.legacy`. Six days later (`73f4122d`, same release) the legacy tree and its tests moved to the `legacy` branch, and the `TRCC_LEGACY` escape hatch was removed.
- **Presentation layer (from v9.7.1, `b68f5802`)**: Qt-free Presentation Models in `ui/presentation/`, shared by `ui/gui` and `ui/qtgui`. A boundary test (`tests/test_architecture_boundaries.py`) now forbids importing Qt, adapters or the App there.
- **One `Registry` (v9.9.6, `23cf3adf`)**: `PlatformFactory` and `DeviceFactory` collapsed into one keyed `Registry` (`core/factory.py`). Subclasses register by naming their key in the class line (`key="linux"`, `wire=Wire.SCSI`).
- **One shared App by default (v9.10.5)**: `_boot.trcc()` returns an `AppProxy` to a background App that every window and command shares, and starts that App on first use. Closing a window no longer stops the panels; `trcc kill` does. `TRCC_DAEMON=0` keeps the old per-process behaviour.
- **Root shares the App (v9.10.6)**: only root raised from a user's session (`sudo`, `run0`, `pkexec`, `doas`) stays in-process. A root login or a root service shares an App like any other user.
- **Windows joins the shared App (v9.11.0, `f30dc462`)**: on Windows the shared App runs over a named pipe, with a small TRCC service handling SCSI panels and the PawnIO driver.

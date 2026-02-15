"""TRCC Linux version information."""

__version__ = "3.0.8"
__version_info__ = tuple(int(x) for x in __version__.split("."))

# Version history:
# 1.0.0 - Initial release: GUI, local themes, cloud themes, video playback
# 1.1.0 - Settings tab fixes: overlay element cards, mask visibility, font picker,
#         12-hour time format without leading zero
# 1.1.1 - Test suite (298 tests), bug fixes found via testing
# 1.1.2 - Fix LCD send (init handshake was skipped), dynamic frame chunks for
#         multi-resolution, local themes sort defaults first, Qt6 install docs
# 1.1.3 - Cross-distro compatibility (centralized paths.py), Theme.png preview
#         includes overlays/mask, install guide covers 25+ distros, dynamic SCSI
#         scan, os.system→subprocess.run
# 1.2.0 - Autostart on login, reference theme save (config.json), resume command,
#         ruff linting, protocol reverse engineering docs, 1836 tests
# 1.2.1 - Fix RGB565 byte order for non-320x320 SCSI devices, fix GUI crash on
#         HID handshake failure, add verbose debug logging (trcc -vv gui)
# 1.2.2 - Fix local themes not loading from pip install (Custom_ dirs blocked
#         on-demand download), bump for PyPI
# 1.2.3 - Refactor: print→logging across 12 modules, thread-safe device send,
#         extract _setup_theme_dirs helper, pyusb deprecation warning filter
# 1.2.4 - Fix pip upgrade wiping themes: extract to ~/.trcc/data/ not site-packages,
#         fix install-desktop for pip installs (generate .desktop inline)
# 1.2.5 - One-time data setup: download themes/previews/masks once per resolution,
#         track in config, custom themes saved to ~/.trcc/data/ (survives upgrades)
# 1.2.6 - Fix stale config marker (verify data on disk), add debug logging for
#         theme setup, tab switches, directory verification
# 1.2.7 - Strip all theme data from wheel (download on first run only), fix
#         _has_actual_themes to require PNGs (ignore leftover .dc files)
# 1.2.8 - KISS refactor: consolidate 5 duplicate settings handlers into
#         _update_selected(), remove dead code (set_format_options, LED stubs)
# 1.2.9 - Fix HID handshake protocol (retry, timeout, endpoint auto-detect,
#         relaxed validation), OOP refactor (DcConfig, conf.py, dataclasses)
# 1.2.10 - Fix first-launch preview bug (paths not re-resolved after download)
# 1.2.11 - Fix LCD send pipeline: overlay/mask/crop/video changes now update LCD,
#          extracted _load_and_play_video() DRY helper, send_current_image applies overlay
# 1.2.12 - Fix overlay not rendering on fresh install: render_overlay_and_preview()
#          now bypasses model.enabled check, auto-enable overlay on element edit
# 1.2.13 - Fix format buttons not updating preview on fresh install: set overlay_enabled
#          on theme load, persist format prefs (time/date/temp) across theme changes
# 1.2.14 - Add GrandVision 360 AIO support (VID 87AD:70DB), fix sysfs VID readback
# 1.2.15 - Auto-detect stale udev quirks: trcc detect warns and prompts
#          sudo trcc setup-udev + reboot when USB storage quirk is missing
# 1.2.16 - Fix udev permissions on SELinux/immutable distros (Bazzite, Silverblue):
#          use MODE="0666" instead of TAG+="uaccess" which fails with SELinux enforcing
# 1.2.17 - SCSI device identification via poll resolution byte (PM mapping from
#          USBLCD.exe protocol), KVM LED backend, sensor-driven LED fix (cpu_percent/
#          gpu_usage metric keys), unified DEVICE_BUTTON_IMAGE dict for all protocols,
#          LED button image resolver, sensor source CPU/GPU toggle in LED control UI
# 1.2.18 - Fix GrandVision 360 AIO (87AD:70DB) causing GUI hang: device is vendor-
#          specific USB (class 255), not HID — removed from LED device list to stop
#          timeout loop on startup
# 1.2.19 - Raw USB bulk protocol for GrandVision/Mjolnir Vision (87AD:70DB):
#          BulkDevice handler (handshake + RGB565 frame send via pyusb),
#          BulkProtocol in device factory, bulk udev rules, CLI detect/probe
# 1.2.20 - Fix GUI not performing handshake for HID/Bulk devices: resolution
#          stayed (0,0) so themes never loaded. Now runs handshake async on
#          device selection. OOP refactor: DeviceEntry dataclass, PmEntry
#          NamedTuple, _LedProbeCache class, _resolve_pm() DRY helper
# 2.0.0  - Major refactor: rename modules to consistent device_* / driver_*
#          naming, extract constants.py, add debug_report.py diagnostic tool,
#          HR10 LED backend (device_led_hr10.py), gif_animator→media_player rename
# 2.0.1  - Fix PM=36 (240x240) wrong resolution: unify FBL/PM tables into
#          constants.py (single source of truth), PM=FBL default instead of
#          hardcoded 320x320. Fix PyQt6 version in trcc report. CI: add ffmpeg,
#          auto-publish to PyPI on tag push. Delete dead shim files.
# 3.0.0  - Hexagonal architecture: services layer (7 services), CLI Typer,
#          REST API adapter, FontResolver extraction, pure media decoders,
#          overlay_renderer merged into services/overlay, dead code removed
#          (theme_io, constants, device_base). Module renames: paths→data_repository,
#          sensor_enumerator→system_sensors, sysinfo_config→system_config,
#          cloud_downloader→theme_cloud, driver_lcd→device_lcd. 2081 tests.
# 3.0.1  - Full CLI parity: 36 Typer commands expose all service methods.
#          New: theme-save, theme-export, theme-import, led-sensor, mask --clear,
#          brightness, rotation, screencast, overlay, theme-list, theme-load,
#          led-color, led-mode, led-brightness, led-off, video. 2148 tests.
# 3.0.2  - Fix bulk protocol (87AD:70DB GrandVision): correct frame header
#          format (magic, cmd, dimensions, mode), robust kernel driver detach
#          before set_configuration(), chunked 16 KiB writes, interface claim.
#          Fix legacy autostart cleanup (glob desktop files). 2156 tests.
# 3.0.3  - Fix background display mode: continuous LCD sending via metrics
#          timer (C# myBjxs/isToTimer parity), toggle OFF renders black+overlays,
#          theme click resets all mode toggles. Security hardening (timing-safe
#          auth, PIL bomb cap, zip-slip, TOCTOU). Tooltips on all user-facing
#          buttons. Distro name in debug report. Help→troubleshooting guide.
# 3.0.4  - Fix bulk frame encoding: JPEG (cmd=2) instead of raw RGB565 for
#          all USBLCDNew devices (87AD:70DB), matching C# ImageToJpg protocol.
#          PM=32 remains RGB565 (cmd=3). Add PM=5 to bulk resolution table
#          (Mjolnir Vision → 240×320). 2166 tests.
# 3.0.5  - Fix LED mode button images: asset filename typo caused buttons
#          to show plain text instead of icons. 2167 tests.
# 3.0.6  - Single-instance guard: prevent duplicate systray entries on launch.
#          Font size spinbox in overlay color picker (independent of font dialog).
#          2167 tests.
# 3.0.7  - Unified segment display renderer for all 11 LED device styles.
#          OOP class hierarchy (SegmentDisplay ABC + 10 subclasses) with
#          data/logic separation. Covers AX120, PA120, AK120, LC1, LF8,
#          LF12, LF10, CZ1, LC2, LF11, LF15. LEDService generalized for
#          all digit-display styles (not just AX120). 2291 tests.
# 3.0.8  - Fix LED segment display: °C/°F toggle now propagates to segment
#          renderer, CPU/GPU sensor source selector now filters phase cycling
#          (show only CPU or GPU instead of always both). OOP refactor: move
#          loose functions into classes across 5 files (conf.py, device_scsi.py,
#          device_led_hr10.py, device_factory.py, device_hid.py). 2290 tests.

"""TRCC Linux version information."""

__version__ = "1.2.6"
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

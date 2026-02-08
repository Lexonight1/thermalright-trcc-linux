"""TRCC Linux version information."""

__version__ = "1.2.0"
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

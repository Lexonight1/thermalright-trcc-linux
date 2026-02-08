# Development Status

TRCC Linux is under **active development**. This page tracks what's stable, what's in progress, and what's coming next.

**Current version:** 1.2.0
**Branch:** `stable`

## What's Stable

These features are tested and working on the `stable` branch:

- **SCSI LCD support** — Frozen Warframe (SE/PRO/Ultra), Thermalright LCD, Winbond LCD
- **Full GUI** — local/cloud/mask themes, overlays, video playback, carousel, image cropper, video trimmer
- **System info overlays** — 77+ sensors (CPU, GPU, RAM, disk, network, fans)
- **Per-device config** — each LCD remembers its theme, brightness, rotation, overlay, and carousel settings
- **Autostart** — launches minimized to system tray on login, sends last-used theme
- **CLI** — `detect`, `send`, `setup-udev`, `gui`, `resume`, `install-desktop`, `uninstall`
- **Cross-distro compatibility** — tested on Fedora, Debian/Ubuntu, Arch, openSUSE, Void, Alpine, Gentoo, NixOS
- **96% test coverage** — 1836 tests across 25 test files

### Supported Devices (Stable)

| Product | VID:PID | Protocol |
|---------|---------|----------|
| Frozen Warframe SE/PRO/Ultra | 0402:3922 | SCSI |
| Thermalright LCD | 87CD:70DB | SCSI |
| Winbond LCD | 0416:5406 | SCSI |

## What's In Progress

### HID Device Support (`hid-protocol-testing` branch)

HID devices use a different USB protocol (DA/DB/DC/DD handshake instead of SCSI). The code is written and tested (563 HID/LED tests) but needs real hardware validation.

**Devices being tested:**

| VID:PID | Product | Tester Status |
|---------|---------|---------------|
| 0416:8001 | Nuvoton HID (Digital series, Vision products) | Tester active — device detected, debugging `Assets.get` crash |
| 0416:5302 | Winbond USBDISPLAY (Trofeo Vision LCD) | Tester active — device detected, no SCSI path (expected for HID) |
| 0416:52E2 | ALi HID LCD (Vision/Warframe newer HW) | Awaiting tester |
| 0418:52E3 | ALi Corp LCD variant | Awaiting tester |
| 0418:52E4 | ALi Corp LCD variant | Awaiting tester |

**LED RGB control** is also on this branch — 7 effect modes (Static, Breathing, Rainbow, Cycle, Wave, Flash, Music) for devices like AX120 DIGITAL.

### Linting & Formatting

`ruff` is configured in `pyproject.toml` but not yet enforced in CI or run across the codebase.

## Roadmap

| # | Item | Status |
|---|------|--------|
| 1 | Test coverage 96%+ | Done |
| 2 | CI/CD (GitHub Actions) | Done |
| 3 | Type checking (pyright basic) | Done |
| 4 | Cross-distro compatibility | Done |
| 5 | Security hardening | Done |
| 6 | Reference theme save (config.json) | Done |
| 7 | HID device validation | In progress (needs testers) |
| 8 | Linting / formatting (ruff) | Next |
| 9 | Version bump → 1.2.0 | After linting |
| 10 | Packaging & release (wheel, PyPI) | After version bump |

## What May Change

- **HID device support** is on a separate branch and may change as testers report issues
- **CLI output format** may be adjusted (e.g. `trcc detect` output)
- **Config file format** may evolve (migrations will be handled where possible)

## Reporting Issues

If something breaks:
1. `git pull && pip install --force-reinstall --no-deps .`
2. Check the [CHANGELOG](CHANGELOG.md) for recent changes
3. Run `trcc detect --all` and include the output
4. Open an issue at https://github.com/Lexonight1/thermalright-trcc-linux/issues

## See Also

- [SUPPORTED_DEVICES.md](SUPPORTED_DEVICES.md) — full device list with USB IDs
- [CHANGELOG.md](CHANGELOG.md) — version history
- [HID_TESTING.md](HID_TESTING.md) — how to help test HID devices
- [INSTALL_GUIDE.md](INSTALL_GUIDE.md) — installation for all distros
- [USBLCD_PROTOCOL.md](USBLCD_PROTOCOL.md) — SCSI protocol (from USBLCD.exe reverse engineering)
- [USBLCDNEW_PROTOCOL.md](USBLCDNEW_PROTOCOL.md) — USB bulk protocol (from USBLCDNEW.exe reverse engineering)

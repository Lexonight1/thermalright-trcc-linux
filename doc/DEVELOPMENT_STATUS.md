# Development Status

TRCC Linux is **feature-complete** — all 45 features from the Windows TRCC 2.0.3 have been ported, with full CLI/GUI/API parity via hexagonal architecture.

**Current version:** 3.0.9
**Branch:** `stable`
**Tests:** 2290 across 35 files
**PyPI:** [trcc-linux](https://pypi.org/project/trcc-linux/)

## What's Stable

All features are tested and working on the `stable` branch:

- **4 protocol backends** — SCSI, HID, LED, Bulk (raw USB)
- **Full GUI** — local/cloud/mask themes, overlays, video playback, carousel, image cropper, video trimmer
- **System info overlays** — 77+ sensors (CPU, GPU, RAM, disk, network, fans)
- **LED RGB control** — 6 effect modes (Static, Breathing, Colorful, Rainbow, Temp-linked, Load-linked), sensor-linked colors, 13 device styles
- **HR10 support** — 7-segment display renderer, NVMe temperature daemon, color wheel
- **Per-device config** — each LCD remembers its theme, brightness, rotation, overlay, and carousel settings
- **Autostart** — launches minimized to system tray on login, sends last-used theme
- **CLI** — 36 Typer commands with full service parity (theme, LED, display, overlay, screencast, video, diagnostics)
- **REST API** — optional FastAPI adapter for headless/remote control (`trcc serve`)
- **Services layer** — 8 pure-Python service classes shared by GUI, CLI, and API
- **Cross-distro compatibility** — tested on Fedora, Debian/Ubuntu, Arch, openSUSE, Void, Alpine, Gentoo, NixOS, SteamOS, Bazzite
- **96% test coverage** — 2290 tests across 35 test files

### Supported Devices

**SCSI devices** — fully supported:
| USB ID | Devices |
|--------|---------|
| `87CD:70DB` | FROZEN HORIZON PRO, FROZEN MAGIC PRO, FROZEN VISION V2, CORE VISION, ELITE VISION, AK120, AX120, PA120 DIGITAL, Wonder Vision |
| `87AD:70DB` | GrandVision 360 AIO, Mjolnir Vision 360 |
| `0416:5406` | LC1, LC2, LC3, LC5 (AIO pump heads) |
| `0402:3922` | FROZEN WARFRAME, FROZEN WARFRAME SE |

**HID LCD devices** — auto-detected:
| USB ID | Devices |
|--------|---------|
| `0416:5302` | AS120 VISION, BA120 VISION, FROZEN WARFRAME PRO, ELITE VISION, LC5 |
| `0418:5303` | TARAN ARMS |
| `0418:5304` | TARAN ARMS |

**HID LED devices** — RGB LED control:
| USB ID | Devices |
|--------|---------|
| `0416:8001` | AX120 DIGITAL, PA120 DIGITAL, Peerless Assassin 120 DIGITAL, HR10 2280 PRO DIGITAL |

**Bulk USB devices** — raw USB protocol:
| USB ID | Devices |
|--------|---------|
| `87AD:70DB` | GrandVision 360 AIO, Mjolnir Vision 360 |

## Roadmap

| # | Item | Status |
|---|------|--------|
| 1 | Full GUI port of Windows TRCC 2.0.3 | Done |
| 2 | Test coverage 96%+ | Done (2148 tests) |
| 3 | CI/CD (GitHub Actions) | Done |
| 4 | Type checking (pyright basic) | Done |
| 5 | Cross-distro compatibility | Done |
| 6 | Linting (ruff) | Done — 0 violations |
| 7 | PyPI publish | Done — [trcc-linux](https://pypi.org/project/trcc-linux/) |
| 8 | HID LCD support | Done — auto-detected |
| 9 | LED RGB control | Done — 6 modes, 13 styles, sensor-linked |
| 10 | Bulk USB protocol | Done — GrandVision/Mjolnir Vision |
| 11 | HR10 7-segment display | Done — temp daemon + GUI |
| 12 | On-demand download | Done — 15 resolutions + 33 web archives |
| 13 | Diagnostic report (`trcc report`) | Done |
| 14 | Hexagonal architecture (services layer) | Done — 8 services |
| 15 | CLI Typer refactor | Done — 36 commands |
| 16 | REST API adapter | Done — FastAPI (`trcc serve`) |
| 17 | Unified segment display renderer | Done — 11 styles, OOP class hierarchy |
| 18 | SELinux full audit | Planned |
| 19 | Type annotation hardening (pyright strict) | Planned |

## Reporting Issues

If something breaks:
1. Run `trcc report` and copy the output
2. Open an issue at https://github.com/Lexonight1/thermalright-trcc-linux/issues
3. Include your distro, kernel version, and the report output

## See Also

- [SUPPORTED_DEVICES.md](SUPPORTED_DEVICES.md) — full device list with USB IDs
- [CHANGELOG.md](CHANGELOG.md) — version history
- [DEVICE_TESTING.md](DEVICE_TESTING.md) — how to help test devices
- [INSTALL_GUIDE.md](INSTALL_GUIDE.md) — installation for all distros
- [CLI_REFERENCE.md](CLI_REFERENCE.md) — all 36 commands
- [USBLCD_PROTOCOL.md](USBLCD_PROTOCOL.md) — SCSI protocol (from USBLCD.exe reverse engineering)
- [USBLCDNEW_PROTOCOL.md](USBLCDNEW_PROTOCOL.md) — USB bulk protocol (from USBLCDNEW.exe reverse engineering)
- [USBLED_PROTOCOL.md](USBLED_PROTOCOL.md) — HID LED protocol (from FormLED.cs reverse engineering)

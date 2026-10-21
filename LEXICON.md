# TRCC Linux — Lexicon

Shared terminology so everyone uses the same names.

## Devices & Protocols
| Term | Meaning |
|------|---------|
| **SCSI** | USB mass-storage protocol used to send LCD frames (RGB565) |
| **HID** | USB Human Interface Device protocol — handshake, resolution detection |
| **LED** | RGB LED control protocol via HID (effects, colors, zones) |
| **Bulk** | Raw USB vendor-specific bulk transfer protocol (GrandVision/Mjolnir Vision) |
| **LY** | USB bulk protocol for LY-type LCDs (0416:5408/5409), chunked 512-byte frames |
| **PM** | Product Model byte from HID handshake — identifies device variant |
| **FBL** | Firmware Byte Layout — maps PM to screen resolution |
| **PID** | USB Product ID (e.g., `0x5302`, `0x8001`) |
| **VID** | USB Vendor ID — Thermalright is `0x0416` |

## Theme System
| Term | Meaning |
|------|---------|
| **Local theme** | User-saved theme on disk (tab 1) — directory with `00.png`, `config1.dc` |
| **Cloud theme** | Downloadable MP4 video from czhorde.cc servers (tab 2) |
| **Mask** | Overlay layout template downloaded from cloud (tab 3) — `zt{resolution}/` |
| **config1.dc** | Binary overlay config file — text positions, fonts, sensor bindings |
| **Reference theme** | A theme whose manifest names its background/mask by a path relative to a data root (`web/{w}{h}/<id>`) instead of bundling the bytes — resolved user library first, then the default/cloud library (`adapters/theme/filesystem.py`) |
| **.tr file** | Theme export/import archive format |

## GUI Components
| Term | Meaning |
|------|---------|
| **UCPreview** | Main preview panel (500x500) showing current LCD frame |
| **UCThemeLocal** | Local themes browser (tab 1) |
| **UCThemeWeb** | Cloud themes browser (tab 2) |
| **UCThemeMask** | Cloud masks browser (tab 3) |
| **UCThemeSetting** | Overlay editor / display mode panels (thin orchestrator + re-exports) |
| **OverlayElementWidget** | Single 60x60 overlay grid cell (`overlay_element.py`) |
| **OverlayGridPanel** | 7x6 grid managing overlay elements (`overlay_grid.py`) |
| **ColorPickerPanel** | RGB color picker, XY position, font selector (`color_and_add_panels.py`) |
| **AddElementPanel** | Create new overlay elements by type (`color_and_add_panels.py`) |
| **DataTablePanel** | Context-sensitive format controls (`display_mode_panels.py`) |
| **DisplayModePanel** | Toggle + action buttons for display modes (`display_mode_panels.py`) |
| **ScreenCastPanel** | Screen capture with coordinate inputs (`display_mode_panels.py`) |
| **UCDevice** | Device sidebar with detection and selection |
| **UCLedControl** | LED RGB control panel (every style in `LED_STYLES`, `core/led_models.py`) |
| **UCScreenLED** | LED segment visualization (colored circles) |
| **Carousel** | Auto-rotating theme slideshow |
| **Screencast** | Live screen capture sent to LCD |

## Data Flow
| Term | Meaning |
|------|---------|
| **On-demand download** | Archives fetched from GitHub raw at runtime when not found locally |
| **Theme archive** | `theme{W}{H}.7z` (plus an optional variant suffix, e.g. `theme1600720l.7z`) — bundled theme images per resolution |
| **Web archive** | `{W}{H}.7z` — cloud theme preview PNGs |
| **Mask archive** | `zt{W}{H}.7z` — cloud mask overlay templates |
| **RGB565** | 16-bit pixel format (5R/6G/5B) sent to LCD via SCSI |
| **User content** | An asset the USER authored, under `user_content_dir()` (`~/.trcc-user/`) — arrives at NATIVE resolution, so the compositor scales it by `DeviceSettings.fit_mode`. `Paths.is_user_content(path)` is the ONE place this is decided |
| **Program content** | Shipped or cloud-downloaded, under `data_dir()` (`~/.trcc/data/`) — pre-authored AT the device canvas, so it takes the C# native-or-black width test (`Renderer.bg_fit`), never a letterbox |
| **Origin** | Which of the two a given surface is. It travels WITH the surface (`RenderContent.background_is_user`, `Playback.is_user_content`), decided by whoever resolved it — never re-derived downstream |

## Settings Tab
| Term | Meaning |
|------|---------|
| **`_update_selected(**fields)`** | Single handler for all overlay config changes — updates selected element and propagates |
| **`require_mode`** | Optional guard in `_update_selected` — only applies update if element's `mode` matches |
| **`mode_sub`** | Sub-format selector within a mode (e.g., 0=24H, 1=12H for time) |

## Directories
| Term | Meaning |
|------|---------|
| **`Paths`** | ABC in `core/ports.py` — every location below is one of its methods. Each `Platform` returns its concrete `Paths`; the examples are Linux's |
| **`config_dir()`** | Application config (`~/.trcc/`) — `trcc.json` (settings, `CONFIG_FILE` in `services/settings.py`), `trcc.log`, TLS certs, LED probe cache |
| **`data_dir()`** | `config_dir()/data` (`~/.trcc/data/`) — program/cloud content downloaded at runtime |
| **`user_content_dir()`** | `~/.trcc-user/` — what the user authored |
| **`user_data_dir()`** | `user_content_dir()/data` (`~/.trcc-user/data/`) — the user tree, laid out like `data_dir()` |
| **`src/trcc/data/`** | The `.7z` archives in the repo — the source `adapters/repo/data_install.py` downloads from (GitHub raw); not shipped in the wheel |

## Versioning
| Term | Meaning |
|------|---------|
| **Major** | First digit (`X.0.0`) — breaking changes, major architectural shifts |
| **Minor** | Second digit (`0.X.0`) — new features, device support, significant enhancements |
| **Patch** | Third digit (`0.0.X`) — bug fixes, small corrections, no new features |

## Adapters (Hexagonal Architecture)
| Term | Meaning |
|------|---------|
| **adapters/device/** | One `Device` subclass per wire (SCSI, HID, Bulk, LY, LED) + their transports |
| **adapters/system/** | Per-OS `Platform` subclasses (linux, windows, macos, bsd) + OS integration (udev, autostart, polkit, SELinux) |
| **adapters/sensors/** | Sensor sources (hwmon, LHM, SMC, NVML, psutil, …), composed into one view by `aggregator.py` |
| **adapters/diagnostics/** | `trcc report`, doctor, health checks |
| **adapters/theme/** | Themes on disk (`filesystem.py`) + the cloud catalog (`cloud.py`) |
| **adapters/repo/** | Data-archive download from GitHub (`data_install.py`, `github_releases.py`, `http.py`) |
| **adapters/infra/** | Infrastructure I/O — logging, IPC sockets/pipes, TLS, background runners (data install, video export), send scheduler |
| **services/** | Core hexagon — pure Python business logic, no framework deps. Strict DI — RuntimeError if deps not injected. |
| **Composition root** | `_boot.trcc()` returns the App every UI dispatches on — an `AppProxy` to the shared App, or an in-process `App` when `_boot._local_reason` names a reason. `App` (`app.py`) builds the services with their adapters and owns the one Command bus |
| **Strict DI** | Service and device constructors raise `RuntimeError` if required adapter dependencies are missing. No lazy fallback imports. No deferred adapter imports in core/. |
| **Platform** | ABC in `core/ports.py`; per-OS subclass in `adapters/system/`, picked by `current_platform()`. Its `paths()` returns the OS's `Paths` (see Directories) |

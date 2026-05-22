# TRCC Linux — Claude Code Project Instructions

## No progress theater (read this first, every session)

The user's time is the constraint. Sounding productive is not being productive. For months on the `next/` rebuild, sessions ended with "Phase X complete" summaries that papered over what wasn't done. The user formed a picture of a near-done rebuild from those summaries. An audit later showed ~45% Command coverage, ~5% GUI, no cloud themes, no i18n, no DC writer, no screencast, no diagnostics — none of it flagged. That was lying by omission.

**Status comes from artifacts, not from session summaries.**

- The audit file (`/tmp/audit_comparison.txt` or current equivalent) is the scoreboard. A feature is "done" only when its row flips MISSING → WIRED *and* `next/` does what legacy does on real hardware. Words don't flip rows; verified runs do.
- End every session with an explicit gap report in this exact shape:
  - **Ported this session**: [list]
  - **Verified working**: [list]
  - **Still missing in this area**: [list]
- If "verified working" is shorter than "ported," say so out loud.

**Banned phrases** (each one covered drift in the past):
- "Feature parity" — unless every audit row in the relevant area is WIRED
- "The architecture is in place" — architecture isn't features; this was the biggest cover
- "We're done with X" — requires the audit row
- "Pattern works end-to-end" — not a proxy for "feature works"
- "Phase X complete" — phases are defined by Claude and therefore always "complete"; use the audit row instead

**No phase or milestone names.** Feature-by-feature against the audit row, nothing else.

**If the user catches drift, don't soften it.** "Misleading framing" is a softer word for "lying by omission." Own it plainly the first time.

Related: `memory/feedback_no_progress_theater.md`, `memory/feedback_no_bs.md`, `memory/feedback_always_be_honest_about_fixes.md`.

## Two Source Trees (read this first)

The repo currently carries two parallel source trees on the `dev` branch:

- **`src/trcc/`** — the **shipping / legacy code**.  Everything users install today runs this.  Full feature set (sensors, setup wizard, autostart, theme download, LED segment displays, `.zt` animations, 4-OS support, etc.).  Every architecture principle below this header describes this tree — SCSI uses `/dev/sgN` + SG_IO on Linux, `DeviceProtocolFactory` exists, `ControllerBuilder` wires things, etc.
- **`src/trcc/next/`** — a **clean-slate rebuild** (12 commits, `c309a55`→`39b3169`).  Proof that a simpler 5-role hexagonal design (Platform / UsbTransport / Device / App / UIs) works end-to-end with one Command bus.  Architecture is complete (3 UIs, two-layer scene cache, video + mask + rotation, tickers wired).  Feature parity with legacy: NO.  Hardware-verified on real devices: NO.  See `memory/project_next_clean_slate.md` for the full status table.

**When the user asks about "the app" or bug fixes for shipping users, work in `src/trcc/`.**  Only touch `src/trcc/next/` when the task is explicitly about the clean-slate rebuild.  Never mix imports between the two trees.

Legacy architecture follows below.

## Architecture — Hexagonal (Ports & Adapters)

### Layer Map
- **Models** (`core/models.py`): Pure dataclasses, enums, domain constants — zero logic, zero I/O, zero framework deps
- **Services** (`services/`): Core hexagon — all business logic, pure Python. `ImageService` delegates to active `Renderer`. `OverlayService` uses injected Renderer for compositing/text.
- **Paths** (`core/paths.py`): Fallback path constants (`DATA_DIR`, `USER_DATA_DIR`). Primary resolution via `PlatformSetup` adapter injected into `Settings`. Zero project imports.
- **Devices** (`core/lcd_device.py`, `core/led_device.py`): Application-layer facades. Strict DI — `RuntimeError` if deps not injected. Zero adapter imports. Delegate to services, return result dicts.
- **Builder** (`core/builder.py`): `ControllerBuilder` — fluent builder, assembles devices with DI. Composition root: imports adapters to inject into services.
- **Views** (`gui/`): PySide6 GUI adapter. `TRCCApp` (thin shell) + `LCDHandler`/`LEDHandler` (one per device).
- **CLI** (`cli/`): Typer CLI adapter (package: `__init__.py` + 8 submodules). Thin wrappers over `LCDDevice`/`LEDDevice`.
- **API** (`api/`): FastAPI REST adapter (package: `__init__.py` + 7 submodules). 78 endpoints. WebSocket preview stream + cloud themes + export. Uses `LCDDevice`/`LEDDevice` from core/.
- **Config** (`conf.py`): `Settings` singleton. `init_settings(platform)` called by composition roots. Single source of truth for mutable app state.
- **Entry**: `cli/` → `trcc_app.py` (TrccApp) → builder.build_device()
- **Protocols**: All protocols implement `send_data()` — SCSI (LCD frames), HID (handshake/resolution), LED (RGB effects + segment displays)
- **Platform** (`core/ports.py`): `OSConfig` dataclass + `Platform` class. OS is data (config), not architecture (class hierarchy). One `Platform` object DI'd everywhere via `builder.os`.
- **OS files** (`adapters/system/{os}_platform.py`): Each exports an `OSConfig` instance (`LINUX_OS`, `WINDOWS_OS`, etc.) + OS-specific functions. ~200 lines each, not 1300.
- **Sensors** (`adapters/system/_base.py`): One `SensorEnumerator` with plugin discovery — tries hwmon, LHM, SMC, sysctl, psutil, pynvml. Each plugin self-guards at runtime.
- **CI**: `release.yml` (Linux RPM/DEB/Arch), `windows.yml` (PyInstaller + Inno Setup), `macos.yml` (PyInstaller + create-dmg)
- **On-demand download**: Theme/Web/Mask archives fetched from GitHub at runtime via `data_repository.py`

### Design Patterns (Used in This Project)
- **Singleton**: `conf.settings` — app-wide state. Widgets read from singleton, never store copies.
- **Factory Method**: `abstract_factory.py` builds protocol-specific device adapters
- **Adapter**: Hexagonal adapters/ — CLI, GUI, API all adapt to the same core services
- **Command**: User actions (button click, terminal command) — log, undo, queue across interfaces
- **Observer**: PySide6 signals broadcast updates from core to views without coupling
- **Strategy**: Swap display/export behaviors without modifying core service logic
- **Template Method**: Concrete method on ABC calls `@abstractmethod` on subclass (e.g. `handshake()` → `_do_handshake()`)
- **Dependency Injection**: Inject dependencies at runtime, never hardcode
- **Repository Pattern**: `data_repository.py` — service layer doesn't know if data comes from file, DB, or API
- **Ports & Adapters**: ABCs as contracts; CLI, GUI, API interact with core the same way
- **DTOs**: `dataclass` for passing data across hexagonal boundaries

### Abstract Base Classes (ABCs)
Two layers: **transport** (raw device I/O) and **adapter** (MVC integration).

#### Transport Layer (`adapters/device/template_method_device.py` + `template_method_hid.py`)
```
UsbDevice (ABC) — handshake() + close()
├── FrameDevice (ABC) — + send_frame()
│   ├── ScsiDevice (adapter_scsi.py)
│   ├── BulkDevice (_template_method_bulk.py)
│   └── HidDevice (ABC, template_method_hid.py) — + build_init_packet, validate_response, parse_device_info
│       ├── HidDeviceType2
│       └── HidDeviceType3
└── LedDevice (ABC) — + send_led_data() + is_sending
    └── LedHidSender (adapter_led.py)
```

#### Adapter Layer (`adapters/device/abstract_factory.py`)
```
DeviceProtocol (ABC) — Template Method: handshake() concrete, _do_handshake() abstract
├── send_data() — unified method, payload is protocol-specific
│
├── ScsiProtocol  (DeviceProtocol + LCDMixin, wraps ScsiDevice)
├── HidProtocol   (UsbProtocol + LCDMixin, wraps HidDevice)
├── BulkProtocol  (DeviceProtocol + LCDMixin, wraps BulkDevice)
└── LedProtocol   (UsbProtocol + LEDMixin, wraps LedHidSender)

DeviceProtocolFactory — @register() decorator for self-registration (OCP)
```

#### Other ABCs
| ABC | File | Subclasses | Purpose |
|-----|------|------------|---------|
| `Renderer` | `core/ports.py` | QtRenderer (1) | Image rendering — compositing, text, encoding, rotation. PIL eliminated. |
| `UsbTransport` | `adapters/device/hid.py` | PyUsbTransport, HidApiTransport (2) | USB I/O abstraction — mockable for tests |
| `SegmentDisplay` | `adapters/device/led_segment.py` | AX120, PA120, AK120, LC1, LF8, LF12, LF10, CZ1, LC2, LF11 (10) | LED 7-segment mask computation per product |
| `BasePanel` | `gui/base.py` | UCDevice, UCAbout, UCPreview, UCThemeSetting, BaseThemeBrowser (5+3 indirect) | GUI panel lifecycle: `_setup_ui()` enforced, `apply_language()`, `get_state()`/`set_state()` |

**Rules**:
- ABC = contract + shared behavior (no Java-style `IFoo` + `AbstractFoo` split)
- ABC at architectural boundaries even with 1 implementation — extensibility for future devices
- Don't add `typing.Protocol` unless third-party plugins need it
- PySide6 metaclass conflict: `QFrame` + `ABC` raises `TypeError` → use `__init_subclass__` (see `BasePanel`)

### Data Ownership
Every piece of data has exactly ONE owner. Violations = bugs.

| Data Kind | Owner | Examples |
|-----------|-------|---------|
| Domain constants (static mappings) | `core/models.py` | `FBL_TO_RESOLUTION`, `LOCALE_TO_LANG`, `HARDWARE_METRICS`, `TIME_FORMATS` |
| Device registries (VID/PID, protocol) | `core/models.py` | VID/PID tables, device type enums |
| Mutable app state (user prefs) | `conf.py` → `Settings` | resolution, language, temp_unit, format prefs |
| GUI asset resolution | `gui/assets.py` → `Assets` | file lookup, `.png` auto-append, pixmap loading, localization |
| Business logic | `services/` | image processing, overlay rendering, sensor polling |
| View state (widget-local) | Each widget | button states, selection indices, animation counters |

**Rules**:
- Models own ALL static domain data — lookups, mappings, enums, constants go in `core/models.py`
- Settings owns ALL mutable app state — widgets read `settings.X`, never store own copies
- Assets owns ALL asset resolution — no manual `f"{name}.png"` anywhere
- Services own ALL business logic — pure Python, no Qt, no framework deps
- Views own ONLY rendering — read from Settings/Models, call Services, display results

### Three-Factory Chain (OS → Protocol → Device)

The composition pipeline is a chain of three factories, **same idiom** in every layer: ABC + `@<Name>Factory.register(key)` self-registering subclasses + a single classmethod chokepoint that dispatches by name. Read one, you've read all three.

| Factory | Defined in | Subclasses | Dispatch key | Chokepoint |
|---|---|---|---|---|
| `PlatformFactory` | `adapters/system/__init__.py` | `WindowsFactory`, `MacOSFactory`, `LinuxFactory`, `BSDFactory` | `sys.platform` | `.current()` |
| `ProtocolFactory` | `adapters/device/factory.py` | `ScsiProtocolFactory`, `HidProtocolFactory`, `BulkProtocolFactory`, `LyProtocolFactory`, `LedProtocolFactory` | `info.protocol` (string from registry) | `.for_info(info)` |
| `DeviceFactory` | `core/device/factory.py` | `LCDDeviceFactory`, `LEDDeviceFactory` | device kind, derived from `PROTOCOL_TRAITS[info.protocol].is_led` | `.for_info(info, builder)` |

**The chain** (top to bottom of the composition root):

```
PlatformFactory.current()              ← OS dispatch     → Platform
    Platform.detect_devices()          ← OS-specific     → list[DetectedDevice]
        DeviceInfo.from_detected(d)    ← typed DTO chokepoint
            ProtocolFactory.for_info(info) ← protocol by name  → DeviceProtocol
                DeviceFactory.for_info(info, builder) ← device by kind
                    LCDDevice(protocol=…, …)  OR  LEDDevice(protocol=…, …)
```

**Why three factories**: OCP at every layer. New OS = `@PlatformFactory.register('haiku')` + subclass. New protocol = `@ProtocolFactory.register('whatever')` + subclass. New Device kind = `@DeviceFactory.register('seven_segment')` + subclass. Zero touchpoints in callers.

**Verification**: `dev/smoke_platforms.py` asserts all three registries are populated and dispatch correctly — 42 checks total, runs on the dev box without any OS-specific tooling installed.

## Daemon Mode (`TRCC_DAEMON`)

Opt-in singleton background process that owns USB and serves CLI / API / GUI clients over a Unix domain socket. Toggled by `TRCC_DAEMON=1`. Off by default during cutover. Falls back to in-process silently on platforms where `AF_UNIX` is unavailable (Windows < build 17063), so the flag is safe to leave set on every OS.

### Composition root

`src/trcc/_boot.py` — single canonical factory used by **every** UI:

```python
from trcc._boot import trcc
result = trcc().lcd.set_brightness(0, 75)   # CLI / API / GUI all do this
```

What `trcc()` returns depends on environment:

| Environment | Returns | Behaviour |
|---|---|---|
| `TRCC_DAEMON` unset | real `Trcc` | reuses `TrccApp._instance._trcc` if a composition root has run, else builds standalone with offscreen `QtRenderer` |
| `TRCC_DAEMON=1` + `AF_UNIX` available | `TrccProxy` | auto-spawns daemon via `daemon.ensure_daemon()`, calls travel over the socket |
| `TRCC_DAEMON=1` on Windows < 17063 | real `Trcc` | silent fallback, no error |

`TrccProxy` is a **structural drop-in** for `Trcc` — same `.lcd` / `.led` / `.control_center` / `.events` surface, same call shape. UIs hold `Trcc` (typed) and never distinguish.

### Wire format (manifold)

Every dispatched call serializes as:

```json
{"role": "lcd", "method": "set_brightness", "args": [0, 75], "kwargs": {}}
```

Response: `{"success": bool, "message": str, "error": str | None, ...extras}`. Path / bytes args are sanitized at the proxy boundary (`Path → str`, `bytes → {"__bytes__": "<base64>"}`) and reconstructed server-side. Daemon's `IPCServer._dispatch_manifold` looks up the role on the bound `Trcc`, calls `getattr(target, method)(*args, **kwargs)`, returns the `OpResult`.

`{"kill": true}` and `{"subscribe": "<topic>"}` are two extra wire shapes for daemon control + long-lived event subscriptions.

### Lifecycle

| Action | Command | What happens |
|---|---|---|
| Start daemon | `trcc daemon` | binds socket, refuses if another daemon is running |
| Stop daemon (CLI) | `trcc kill` | sends `{"kill": true}`, waits for socket to disappear |
| Stop daemon (API) | `POST /trcc/kill` | same wire |
| Stop daemon (signal) | `kill <pid>` / SIGTERM | Qt event loop has a 100 ms heartbeat timer so Python signal handlers fire promptly |
| Auto-spawn on first use | implicit when `TRCC_DAEMON=1` and no daemon found | clients call `daemon.ensure_daemon()` → `subprocess.Popen([trcc, "daemon"], start_new_session=True, …)` |

### What's still pending

- **GUI in daemon mode**: today the GUI gates `TRCC_DAEMON=1` with a clear refusal because `LCDHandler` references real `LCDDevice` instances. Phase 9 (TrccApp dissolution) wires GUI through `_boot.trcc()` and makes it a proper daemon client.
- **Tests**: the existing test suite tests legacy `TrccApp` paths. Daemon-mode tests come back when Phase 9 settles.
- **Donor matrix**: SCSI verified end-to-end. HID / Bulk / LY / LED through daemon are inferred-but-unverified — same `Trcc` dispatch path donors confirmed for the in-process flow, just with JSON serialization in front. Real-hardware donors close the matrix.

## Conventions

### Code Style
- **OOP** — classes with clear SRP. `dataclass` for data, `Enum` for categories.
- **Pure Python** — use the language to its fullest. Dunders (`__getitem__`, `__contains__`, `__iter__`, `__len__`) for registry classes so data describes itself. `@property` for derived attributes. `match/case` for pattern dispatch. Generators for lazy iteration. Context managers for resource lifecycle. Data should be self-describing — behavior derived from structure, not plumbing code.
- **DRY** — 3+ duplicates = centralize. Two = smell. One-off = inline.
- **Type hints** on all public APIs
- **Logging**: `log = logging.getLogger(__name__)` — never `print()`
- **Paths**: `pathlib.Path` preferred; `os.path` only in `data_repository.py`
- **Thread safety**: Qt signals for background→GUI communication — never `QTimer.singleShot` from non-main threads
- **Import from canonical location** — `from .core.models import X`, not re-defining locally

### SOLID
- **SRP** — services own logic, views own rendering, models own data
- **OCP** — `@DeviceProtocolFactory.register()` for self-registering protocols. New devices = new data, not modified logic.
- **LSP** — no fake implementations. If a subclass can't fulfill the contract, don't inherit.
- **ISP** — `LCDMixin` + `LEDMixin` instead of one fat `DeviceProtocol`
- **DIP** — inject dependencies at runtime. Core never imports concrete adapters.

### Hexagonal Purity
- Dependencies point inward ONLY: adapters → services → core
- Services and core NEVER import from adapters
- Infrastructure deps injected via constructor params
- Composition roots (CLI, GUI, API) wire concrete implementations
- No fallback imports — services must not lazy-import adapters. `RuntimeError` if not injected.
- No workarounds — find root cause, fix it. No silent degradation, no environment sniffing, no dual code paths.

### Testing
- **Tests prove code correctness, not that the app works.** After any refactor, run the real app (`PYTHONPATH=src python -m trcc gui`) and `dev/mock_gui.py`. Check `~/.trcc/trcc.log` and `dev/.trcc/trcc.log` for errors. A green test suite means nothing if the device can't handshake.
- `pytest` with `PYTHONPATH=src`; run: `PYTHONPATH=src pytest tests/ -n 8 -x -q`
- Tests mirror `src/trcc/` hexagonal layers (`tests/{core,services,adapters/{device,infra,system},cli,api,gui}/`)
- Refactoring changes mock targets → use `conftest.py` fixtures/helpers, not 50+ inline updates
- Model-parametrized tests: `FBL_PROFILES`, `LED_STYLES`, `ALL_DEVICES` are single source of truth — `@pytest.mark.parametrize` over them. Never hardcode domain values in tests.
- `ruff check .` + `pyright` must pass before any commit (0 errors, 0 warnings)
- **MockPlatform** (`tests/mock_platform.py`): proper `Platform` subclass — noop USB, temp paths, real DI flow. Same `ControllerBuilder(platform)` wiring as production. Never duck-type a platform mock.
- **Dev mock GUI** (`dev/mock_gui.py`): patches `core.paths` to `dev/.trcc/`, creates `MockPlatform`, mirrors `gui/__init__.py::launch()` exactly. If production launch changes, update mock_gui to match.

### Patterns for Adding Things

**New domain data** (constant, mapping, enum):
1. Search `core/models.py` first — may already exist
2. Add to `core/models.py` with section comment
3. `from .core.models import MY_CONSTANT` where needed

**New app state** (user preference):
1. Add to `Settings` — `_get_saved_X()` / `_save_X()` + public `set_X()`
2. Persist in `config.json` via `load_config()` / `save_config()`
3. Widgets read `settings.X` — never pass through constructor chains

**New assets**:
1. Put file in `src/trcc/assets/gui/`
2. Reference by base name — `Assets.get('MY_ASSET')` auto-resolves `.png`
3. Localized variants: `{base}{lang}.png`, use `Assets.get_localized()`

## Security

Zero tolerance for security issues. Fix within hexagonal architecture — never suppress.

### Principles
- Fix at the boundary, keep core pure — validation in adapter layers (API, CLI)
- No suppression comments — no `# nosec`, `# type: ignore` for security, `# noqa`
- CodeQL must stay clean — zero alerts, false positives get fixed not dismissed

### Rules by Area
- **Subprocess**: `subprocess.run([...], shell=False)` with arg lists. Never interpolate user input. `pkexec` = exact command list.
- **API**: Validate all path params (reject `..`, absolute paths). No stack traces in responses. Structured error responses with Pydantic.
- **File system**: Zip slip prevention (validate members before extracting). Theme/mask paths `.resolve()` + verify under expected dir. Config `json.load()` with try/except, fall back to defaults.
- **USB**: Bounds-check handshake bytes. Timeout all operations. Garbage data = log + disconnect, never crash.
- **Downloads**: Pin to `https://github.com/Lexonight1/thermalright-trcc-linux/`. Validate content after extraction.
- **Tests**: Full exact values, no partial substring checks. No `# nosec` in tests.

## Known Issues
- `pyusb 1.3.1` deprecated `_pack_` on Python 3.14 — suppressed in pytest config
- `pip install .` can use cached wheel — use `pip install --force-reinstall --no-deps .`
- CI runs as root — mock `subprocess.run` in non-root tests
- Never `setStyleSheet()` on ancestor widgets — blocks `QPalette` image backgrounds
- Optional imports (`hid`, `dbus`, `gi`, `pynvml`) need `# pyright: ignore[reportMissingImports]`
- C# asset suffixes are legacy — `Assets.get_localized()` maps ISO 639-1 → legacy suffixes via `ISO_TO_LEGACY`
- **Issue #87**: Python 3.14 typer crash in `sudo_reexec` — FIXED: dispatches via `python -c` (direct function call), bypasses typer.

## GitHub Issues
- Never use "Fixes #N" in commit messages — GitHub auto-closes on push to default branch
- Don't close issues until reporter confirms the fix works
- Every reply ends with funding footer: `\n\n---\nIf this project helps you, consider [buying me a beer](https://buymeacoffee.com/Lexonight1) 🍺 or [Ko-fi](https://ko-fi.com/lexonight1) ☕`
- Check if reporter is a donor (README thanks section) — thank by name if so
- **Package upgrade instructions must be copy-paste ready** — always provide the full `wget -c <URL>` + install command so users can paste directly into their terminal. Use the actual download URL from `gh release view --json assets`, not just "go to Releases page". Distro-specific:
  - Arch: `wget -c <url>.pkg.tar.zst && sudo pacman -U trcc-linux-*.pkg.tar.zst`
  - Fedora: `wget -c <url>.rpm && sudo dnf install ./trcc-linux-*.rpm`
  - Ubuntu/Debian (legacy): `wget -c <url>.legacy_all.deb && sudo dpkg -i trcc-linux_*.legacy_all.deb`
  - Ubuntu/Debian (standard): `wget -c <url>.deb && sudo dpkg -i trcc-linux_*.deb`

## Deployment
- Default branch: `main`
- Never push without explicit user instruction
- Dev repo: `~/Desktop/projects/thermalright/trcc-linux`
- Testing repo: `~/Desktop/trcc_testing/thermalright-trcc-linux/`
- PyPI: `trcc-linux` (published)
- Tag push triggers PyPI release — always `git tag v{version} && git push origin v{version}` after push

## Development Workflow

### Plan Before Coding
Non-trivial changes: think through full impact, state plan, wait for confirmation, THEN implement in one pass. Never jump in and patch as you go.

### Look at the Log Before the Code
When the user reports a broken feature: `grep -iE "error|traceback|warning" ~/.trcc/trcc.log` FIRST. The log usually names the broken step in one line; reading code to find it wastes 20 minutes and the user's patience.

### Check Existing Fallback / Guard Code Before Rewriting
Before rewriting any call site that dispatches through a facade (`self._app.*`, `self._trcc.*`, etc.), `grep "if self._app is not None"` in the same file. Many sites already have `else: self._x.y()` fallback paths written years ago — flipping the parameter that selects them is a 1-line fix instead of an 80-line rewrite.

### Shape-Compat Before Writing a Migration
Before adding code that writes a file another tool reads (legacy ↔ next/ sharing `config.json`, any inter-tool state), READ the other tool's reader and match the shape. Use a different filename if shapes differ (`trcc.json` is next/'s persistence filename, distinct from legacy's `config.json`) — sharing a filename with different shapes is how you corrupt user data. Don't bake temporal labels (`-next`, `-new`, `-v2`) into permanent filenames; pick a name that survives cutover.

### pycache Before Bulk Moves
`git rm -r dir/` leaves `__pycache__` behind. Then `git mv a/x dir/` nests at `dir/a/x` instead of replacing. Before any bulk directory operation: `find . -name __pycache__ -type d -exec rm -rf {} +`.

### Network Retries ↔ UI Locks
If you lengthen a timeout or add retry on a network call, audit every UI state that gates on "busy". A 120s retry chain with `_downloading=True` locking clicks is worse UX than 30s fail-fast.

### Cross-Platform Fixes (Windows / macOS / BSD)
Non-Linux bugs get the slowest, most disciplined approach in this repo. The Linux dev does not run these OSes day-to-day, so guessing wastes commits, releases, and reporter trust.

**Protocol — every cross-platform bug, every time:**

1. **Reproduce or get a log first.** No code changes until you have either a stack trace from the reporter / VM, or a deterministic repro. "I think Windows does X" without a log = stop.
2. **Read the log to find the broken link.** Trace `OS → memory → transport → device` in that order. Fix the FIRST broken step, not the loudest symptom.
3. **Web-research the canonical pattern before inventing.** For any claim of the form "Windows/macOS/BSD doesn't support X" or "this API behaves differently on Y":
   - Step 1: Check the C# decompile (`/home/ignorant/Downloads/v2.1.4_decompiled/`) — the original Windows app already solved it.
   - Step 2: Web-search authoritative docs (MSDN, Apple Developer, FreeBSD handbook) AND how shipping projects in the same space solve it (Rainmeter, Hass.Agent, OpenRGB, OpenHardwareMonitor, Zabbix, Glances).
   - Step 3: Propose the canonical pattern. **Only invent a custom approach when nothing fits** — and document why in the commit.
   - Past failure: v9.6.0 added pythonnet + HardwareMonitor (50 MB, 3 deps) for a Windows sensor problem the WMI namespace pattern already solved cleanly.
4. **One canonical fix, not a chain.** If you find yourself writing fix N+1 that modifies the same code as fix N (e.g. five `StreamHandler.emit` overrides in a row), STOP. Revert the chain, read the actual API contract, ship one fix that addresses the root cause.
5. **Architecture lens before the patch:**
   - **SRP**: Windows-specific console encoding belongs in ONE place — the logging adapter — not scattered across handlers, modules, and entry points.
   - **OCP**: A platform-specific behavior is a subclass under `adapters/system/{os}_platform.py` or a gated module, not `if sys.platform == 'win32'` smeared through core or services.
   - **DIP**: Core never imports `winreg`, `wmi`, `pywin32`, `pyobjc`, `IOKit`, or `dbus`. Adapters do. Core consumes the `Platform` port.
   - **DRY**: If two OSes need similar adapter logic, extract the shared piece to `adapters/system/_base.py` plugin discovery — don't copy-paste between platform files.
6. **Verify before declaring done.** A Windows fix is "done" when a reporter confirms on real hardware or you've reproduced + verified in the Windows VM. Green CI + green tests prove the code compiles and types match — they do NOT prove the bug is fixed.

**Antipattern shortlist (do not repeat):**
- Five-commit fix chains where each commit reworks the previous one — root cause was never found.
- Reaching for chmod / sudo / pythonnet / monkey-patches when the kernel/OS has a native solution one config change away.
- "Windows is weird" / "macOS is weird" as a reason to skip research — those OSes have 30 years of documented patterns; find the right one.

**Verification reality (what we can actually test):**

| OS | VM on dev box? | Verification path | Iteration speed |
|---|---|---|---|
| Linux | Native | Dev box | Fast |
| Windows | Yes (VM) | Windows VM on Linux host | Medium — can repro in-house |
| macOS | **No** — Apple licensing + hardware lock | Reporter only | Slow — every fix needs a reporter cycle |
| FreeBSD / OpenBSD | Possible (lightweight VM) but not set up yet | Reporter only for now | Slow |

**Implications:**
- **Windows**: get the VM repro before claiming a fix. If you can't repro in the VM, the bug report is either incomplete or the fix is unverified — say so.
- **macOS**: extra weight on research + canonical-pattern discipline because we cannot self-verify. Draft reply, ship to a single reporter with `awaiting-reporter` label, wait for confirmation BEFORE generalizing the fix to a release. Never post "try vX.Y.Z" for macOS until at least one reporter has confirmed; one anecdotal pass is the gate.
- **BSD**: same reporter-dependent loop as macOS until we set up a VM.

**Per-OS metrics ecosystem notes:**
- **Windows** has clear winners: LibreHardwareMonitor (WMI namespace `root\LibreHardwareMonitor`), OpenHardwareMonitor, Hass.Agent, HWiNFO64 shared memory. Pick the one that matches user install state — don't ship our own kernel driver.
- **macOS metrics is FRAGMENTED — extra research required.** No single canonical source like LHM. Candidates to evaluate before any code: IOKit SMC keys (Intel vs Apple Silicon differ, and Apple Silicon SMC keys are largely undocumented), `powermetrics` CLI (root-only), `macmon` (Apple Silicon focused), `iStats`/`iStatistica`, `osx-cpu-temp`, `stats` (menubar app, no API). Read what each actually exposes, on which CPU family, with what permissions, before deciding. Do NOT assume Intel SMC keys work on Apple Silicon.
- **BSD** uses `sysctl` for almost everything (`hw.sensors`, `dev.cpu.N.temperature`). FreeBSD ≠ OpenBSD ≠ NetBSD — verify the sysctl name on the target distro.

### Two Modes

**Development** — local commits, no push, no version bump:
- Small logical commits to `main`
- `ruff check .` + `pyright` before each commit
- Do NOT push or bump version

**Release** — validated and ready for users:
1. `ruff check .` + `pyright` — 0 errors
2. `PYTHONPATH=src pytest tests/ -n 8 -x -q` — all pass
3. Commit + push existing changes to `main`
4. Bump version in `src/trcc/__version__.py`, `pyproject.toml`, AND `flake.nix`
5. Add version history entry in `__version__.py`
6. Update `doc/CHANGELOG.md`
7. Lint + test again
8. Commit + push version bump to `main`
9. `git tag v{version} && git push origin v{version}` (triggers CI + PyPI)
10. `gh release create v{version} --target main --title "v{version}"`
11. Comment on relevant GitHub issues

### Trigger Words
Bare `patch`, `minor`, or `major` → full release workflow:
1. Lint + test uncommitted changes first
2. Commit + push existing changes to `main`
3. Bump version in `__version__.py`, `pyproject.toml`, `flake.nix`
4. Version history + changelog
5. Update `release.yml` inline package specs (download URLs use fixed-name aliases — no version in guide/README URLs)
6. Lint + test again
7. Commit + push version bump + tag + GitHub release

## GUI Standards
- **Overlay enabled**: `_load_theme_overlay_config()` must call `set_overlay_enabled(True)`
- **Format prefs**: Persist via `conf.save_format_pref()`, applied on theme load via `conf.apply_format_prefs()`
- **Theme loads**: DC for layout, user prefs for formats (time_format, date_format, temp_unit)
- **Signal chain**: format button → `_on_format_changed()` → `_update_selected()` → `to_overlay_config()` → `CMD_OVERLAY_CHANGED` → `_on_overlay_changed()` → `render_overlay_and_preview()`
- **QPalette vs Stylesheet**: Never `setStyleSheet()` on ancestors — blocks palette backgrounds
- **First-run**: No device config → overlay disabled. Theme click re-enables. Defaults: 24h, yyyy/MM/dd, Celsius.
- **First install auto-load**: `EnsureDataCommand` extracts in background → `notify_data_ready()` → `_update_theme_directories()` → auto-loads first theme if `current_image is None`
- **Delegate pattern**: Settings tab → `invoke_delegate(CMD_*, data)` → main window
- **`_update_selected(**fields)`**: Single entry point for element property changes
- **Multi-LCD shared widgets**: All `LCDHandler` instances share one preview/progress widget set. Only the *active* handler may write to those widgets — gated by `self._ui_active`. `apply_device_config` / `reactivate` set it `True`; `set_inactive` (sidebar A→B switch) and `restore_inactive_state` (initial-scan keep-alive) set it `False`. `_on_video_tick` and `_render_and_send` honor the gate. Cleanup uses full `deactivate()` (stops all timers); sidebar switch uses `set_inactive()` (keeps animation timer running so the LCD's physical screen doesn't go dark when another device owns the GUI).

## Reference Docs
- Architecture history: `doc/HISTORY_ARCHITECTURE.md`
- Project history: `doc/HISTORY_PROJECT.md`
- Changelog: `doc/CHANGELOG.md`
- **Clean-slate rebuild status**: `memory/project_next_clean_slate.md` — what `src/trcc/next/` has, what's stub, what's untested, commit map

## Execution Boundaries (Non-Negotiable)

### File Modification Rules
- Only modify files explicitly named in the request
- If a fix requires touching an unspecified file, STOP and ask first
- Do not clean up related code while inside a file
- Do not create new files without explicit instruction

### Before Every File You Touch
1. Which layer is this file in?
2. Which port interface does it implement or consume?
3. Does any import violate the layer law?

### Complexity Calibration
- Trivial: execute directly, no preamble
- Moderate: one sentence stating approach, then execute
- Complex: full plan, wait for confirmation, implement in one pass

### On Uncertainty
If the boundary is unclear — stop and ask.
A precise question is better than a confident wrong answer.

### Behavioral Rules
- **Listen and implement** — implement what the user says, don't reinterpret. Device type is a boolean from discovery, config data makes the object, handler manipulates it. Keep it simple.
- **Never post to GitHub without approval** — always show the draft first, wait for explicit "post it" / "ok" before running `gh issue comment` or `gh pr create`. The user is the maintainer — their voice, their project.
- **Be honest about fixes** — never claim a bug is fixed unless the specific code change addresses the specific problem. Refactors don't fix user bugs. Don't reply to issues with upgrade instructions when the bug isn't actually fixed.
- **Plan before patching** — don't jump to code edits on bug reports. Read all relevant code, trace the full flow, use web search to understand platform-specific behavior, enter plan mode, get confirmation, then implement in one pass. Don't assume cross-platform bugs without evidence from each platform.

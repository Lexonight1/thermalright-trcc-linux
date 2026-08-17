# TRCC Linux — Claude Code Project Instructions

## Session start — read memory FIRST, before anything else

Before acting on any request, in this order:

1. **Read `memory/SESSION.md`** — the single **live handoff**: current tree
   state, what the last session pushed, and where to resume. This is the
   front-door; it replaces the stale "Resume here next" blocks that used to
   accrete in this file.
2. **Read `memory/MEMORY.md`** — the index of durable topic files (facts,
   feedback, user profile) — and open the linked `*.md` files relevant to the
   task. The index is auto-loaded but **truncated** (>200 lines); the linked
   files hold the real detail.
3. **Need history?** Past session handoffs are archived in `memory/sessions/`
   (dated, immutable) — read on demand, e.g. when `SESSION.md` references a
   prior session.

Full path base:
`/home/ignorant/.claude/projects/-home-ignorant-Desktop-projects-thermalright-trcc-linux/memory/`

Recover state from the files on disk, not the compressed index or a blank slate.
Memories are point-in-time — verify any file:line claim against current code
before asserting it as fact.

**Session wrap-up (the other half of the loop):** before overwriting
`SESSION.md` with the new state, copy its outgoing content to
`memory/sessions/YYYY-MM-DD-<slug>.md` and add a line to that folder's README
index. Then rewrite `SESSION.md` for the current session. Durable facts still go
to topic files + `MEMORY.md`, never into `SESSION.md`.

Handoff state does NOT live in this file. It lives in `memory/SESSION.md`,
which is rewritten every session, with the durable facts in the topic memos it
links. This file is for things that stay true: architecture, conventions,
standards, and the rules below.

The dated "RESUME …" blocks that used to sit here were removed on 2026-08-17 —
226 lines of superseded state, some of it actively wrong by then. Every memory
file they pointed at was verified present first, and the block text is kept
verbatim in `memory/sessions/2026-08-17-claudemd-resume-blocks.md` in case a
commit hash or a "NEXT:" line in one never reached its topic memo.

**If you are looking for what to work on, read `memory/SESSION.md`.**

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

**"Reporter-gated" is the new progress theater.** Before declaring a
device/feature "can't bench-verify, needs the reporter," ask: did legacy ship
the tooling to verify it (a mock, a fake platform, a harness in `tests/` or
`dev/`)? In the 2026-06-03 session "reporter-gated" became my cover for not
having looked — legacy had a multi-device `MockPlatform` + `dev/devices.json`
that simulates ANY device fleet in the real GUI with zero hardware (the cutover
dropped it; restoring it is the way to verify #136/#137/widescreen panels).

**Legacy audits are INVENTORIES, not greps.** A keyword `git grep v9.6.5 …`
that finds nothing is NOT evidence the cutover kept a feature — the thing you
didn't think to grep for is exactly what got dropped. For any "did the cutover
keep X?" question, READ the legacy module(s) end-to-end and enumerate their
capabilities; end with an explicit "legacy had A,B,C,D; cutover kept A,B; C,D
dropped — evidence:lines." Greps repeatedly missed whole dropped subsystems
(geometry/portrait, the hotplug→connect bridge, the multi-device mock).

**Grep is for LOCATING code, never for CONCLUDING about it.** This generalizes
the inventory rule to EVERY claim — "wired", "dead", "done", "verified", "N
files clean". Finding a symbol/handler/connection proves it EXISTS, not that it
WORKS: the bug is almost always what the wired code DOES with the data at a
boundary, not a missing connection. To claim "X is wired/works", trace the data
through every hop — signal → handler → command.execute → service → render — and
confirm each hop passes the right SHAPE to the next; stop at the first hop you
haven't read. To claim "X is dead/safe to delete", read every consumer's BODY.
2026-06-04, one session: called drag-drop "gone" (it was wired on a child
widget), `native_orientation` "dead" (grep), "537 DCs parse clean" (checked
positions, never text content — the actual bug), and the GUI settings panel
"wired" (the command it dispatches writes edits to the wrong render layer so
nothing applies). User: "you are just grepping words without understanding what
the functions actually do … tired of going in fucking circles." See
`memory/feedback_grep_is_not_understanding.md`.

Related: `memory/feedback_grep_is_not_understanding.md`, `memory/feedback_no_progress_theater.md`, `memory/feedback_no_bs.md`, `memory/feedback_always_be_honest_about_fixes.md`, `memory/feedback_legacy_audit_grep_not_inventory.md`.

## Source Tree Layout — Post-Cutover (read this first)

The cutover (commit `4fa876be`, branch `cutover/next-to-root`) promoted
the clean-slate rebuild (formerly `src/trcc/next/`) to the project
root.  Layout now:

- **`src/trcc/`** — the **shipping codebase** and the *only* tree on
  `main`.  This is what `python -m trcc gui` runs.  Hexagonal
  architecture, one Command bus, ABCs at each port.  Originally the
  "next/" rebuild; promoted to root after the variant-override /
  data-install / ThemeDir / 0xDC-trailer ports caught it up to legacy
  parity for the verified SCSI 320x320 path.
- **The legacy tree was MOVED OFF `main`** (commit `73f4122d`) onto the
  `legacy` branch — `src/trcc/legacy/` and `tests/legacy/` no longer
  exist on `main`, and the `TRCC_LEGACY=1` escape hatch is gone from the
  entry point (`_entry.py` dispatches straight to the new tree).  To
  consult the original implementation, `git show legacy:<path>` or check
  out the `legacy` branch — don't expect `trcc.legacy.*` to import on
  `main` (any harness that does is dead; the obsolete ones were pruned in
  `7d9fe0f9`).

`tests/` exercises the shipping tree.  `dev/` smoke harnesses drive it
too — `dev/smoke_full_pipeline.py` is the broad one; `dev/tools/
diagnose_metrics.py` verifies the live metrics→render→preview chain on a
summoned mock device.

Rules:

* **Default mental model**: every feature/bug lives in `src/trcc/`.
  "Legacy" now means the `legacy` branch or the C# decompile, consulted
  as a *reference*, never imported.
* **Never invent file/path names** in the shipping tree.  TRCC theme dirs
  use the strict legacy convention — `00.png` (background),
  `01.png` (mask), `Theme.png` (panel thumbnail; NEVER rendered),
  `config1.dc` (binary layout), `Theme.{mp4,mov,webm,zt}` (video).
  The original rebuild fabricated names like `background.png` and
  `mask.png` that don't exist anywhere; those got purged in
  `8f6bb53c`.  Anything in `src/trcc/` that diverges from legacy's
  filenames / DC byte layout / handshake parsing / variant lookup
  is a bug until proven otherwise — fix by reading the legacy branch /
  decompile and copying the working shape, not by patching the fabrication.
* **Logging is part of the port.**  See "Logging coverage is
  mandatory" section below.

### Path conventions on disk

Two distinct user-data trees — don't conflate them; the distinction
is load-bearing for scaling, mutation rights, and the diagnostic loop:

* `~/.trcc/data/` — **program/cloud data**.  Downloaded by the app
  at runtime (cloud themes, cloud masks, shipped data).  **Pre-scaled
  to the device's canvas resolution.**  Read-only from the user's POV.
* `~/.trcc-user/data/` — **user-uploaded data**.  Saved themes +
  custom backgrounds/masks the user authored.  Native resolution;
  the render pipeline scales at composite time via `fit_mode`.

The `PlayVideo` decode-size gate switches on
`path.is_relative_to(paths.user_content_dir())` — user upload →
decode native, let `fit_mode` scale; program/cloud asset → canvas
decode (pre-scaled).  Same axis applies anywhere "should this asset
be treated as authored-for-this-device vs authored-by-the-user".

**GUI "online themes" terminology trap**: the "online themes" tab
in the legacy-style GUI shows **MASKS**, not themes.  They live at
`~/.trcc/data/web/zt{w}{h}/<mask_id>/` and contain `01.png` (mask
image, normally RGBA with 60%+ opaque pixels) + a DC config with
overlay elements.  Clicking one dispatches `ApplyMask` (not
`LoadTheme`); `LoadTheme.execute:131` itself re-applies the mask
when reloading, and `:209` dispatches `PlayVideo` for any bundled
video — so a single "online theme" click can chain `ApplyMask →
LoadTheme → ApplyMask + PlayVideo`.  That's the flow shape, not a
bug.  Don't call them "cloud themes" in code or analysis.

## Architecture — Hexagonal (Ports & Adapters)

### Layer Map
- **Models** (`core/models.py`): Pure dataclasses, enums, domain constants — zero logic, zero I/O, zero framework deps.
- **Services** (`services/`): Core hexagon — all business logic, pure Python. `DisplayService` (`services/display.py`) delegates to the active `Renderer`; `OverlayService` uses the injected Renderer for compositing/text.
- **Paths** (`core/ports.py`): `Paths` ABC — per-OS subpaths (theme/web/mask/user dirs) derived from `data_dir`/`config_dir`/`user_content_dir`. Each `Platform` returns its concrete `Paths`. Zero adapter imports.
- **Devices** (`core/ports.py` ABCs + `adapters/device/`): one unified `Device` ABC. Wire adapters `ScsiLcd`/`HidLcd`/`BulkLcd`/`LyLcd` (`adapters/device/{scsi,hid,bulk,ly}_lcd.py`) + `Led` (`led.py`), each *is* the device that speaks its wire. Built by `DEVICES[info.wire]`.
- **Composition root** (`_boot.py` + `app.py`): `_boot.trcc()` builds the in-process `App` (or an `AppProxy` in daemon mode). `App` owns the services, the `EventBus`, and the one Command bus (`app.dispatch`). Replaces the old `ControllerBuilder`.
- **Views** (`ui/gui/`): PySide6 GUI adapter. `TRCCApp` (`ui/gui/trcc_app.py`, thin shell) + `LCDHandler`/`LEDHandler` (one per device). `ui/qtgui/` is the in-progress native-skin rebuild.
- **CLI** (`ui/cli/`): Typer CLI adapter (package). Thin wrappers that build Commands and `app.dispatch(...)` them.
- **API** (`ui/api/`): FastAPI REST adapter (package). ~105 routes incl. WebSocket preview stream + cloud themes + export. Dispatches Commands on the App.
- **Config** (`services/settings.py`): `Settings` — mutable app + per-device state (resolution, language, orientation, format prefs, mask/theme), persisted to `config.json`. Reached via `app.settings`; widgets read it, never store copies.
- **Entry**: `trcc._entry:main` (console script / `python -m trcc`) → `ui/cli` → `_boot.trcc()` → `App` (composition root: `current_platform()` + `DEVICES[wire]`).
- **Wires**: each device adapter speaks its protocol — SCSI (LCD frames), HID (handshake/resolution), Bulk, LY, LED (RGB effects + segment displays). See "Two-Factory Chain" + the ABC tables below.
- **Platform** (`core/ports.py` + `adapters/system/`): `Platform` ABC in core; per-OS subclass in `adapters/system/{linux,windows,macos,bsd}.py`, dispatched by `current_platform()` (`sys.platform`). DI'd everywhere as `app.platform`.
- **Sensors** (`adapters/sensors/`): `SensorEnumerator` (ABC in `core/ports.py`) built per-OS by the aggregator (`adapters/sensors/aggregator.py`) — hwmon, LHM, SMC, sysctl, psutil, pynvml plugins, each self-guarding at runtime. `snapshot()` yields the typed `HardwareMetrics` every consumer observes.
- **CI**: `release.yml` (Linux RPM/DEB/Arch), `windows.yml` (PyInstaller + Inno Setup), `macos.yml` (PyInstaller + create-dmg), plus `ci.yml`/`tests.yml`/`codeql.yml`.
- **On-demand download**: Theme/Web/Mask archives fetched from GitHub at runtime by `DataInstallService` (`services/data_install.py`) via the repo adapters (`adapters/repo/`: `github_releases.py`, `http.py`).

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

#### Other ABCs → **`doc/REFERENCE_PORTS.md`** (generated)

**Do not maintain a port table by hand here.** The one that used to live in this
spot listed **4** ports when the tree had **28**, and two of those four pointed
at files that no longer contained them (`UsbTransport` in
`adapters/device/hid.py` — a class that does not exist; `SegmentDisplay` in
`adapters/device/led_segment.py` — it moved to `services/`). A contributor
following it looked in the wrong file for a class with the wrong name.

`doc/REFERENCE_PORTS.md` is generated from the live ABCs by
`dev/gen_ports_reference.py`: every port, **what you implement**, **what you
inherit free**, how you register, and who already implements it — ordered
cheapest-to-extend first. Regenerated by the pre-commit hook whenever
`src/trcc/` changes and gated by
`tests/test_docs_ports_consistency.py`, so it cannot drift.

Answering "which class do I extend to add X?" is that page's job, not this
file's.

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

### Two-Registry Chain (OS → Device)

The composition pipeline is a chain of two registries, **same idiom** in every layer: one shared `Registry` (`core/factory.py`), subclasses that name their own key **in their class line**, and a mapping lookup. Read one, you've read both.

There is no `PlatformFactory` / `DeviceFactory` class any more — both were shells around a dict. `Registry` is that dict, written once, parameterised by what happens on a **miss** (`Reject` raises · `FallBackTo` substitutes + warns · `.find()` returns `None`).

The cutover unified legacy's separate Protocol + Device layers into one `Device` ABC, so there is **no `ProtocolFactory`** — `ScsiLcd`/`HidLcd`/`BulkLcd`/`LyLcd`/`Led` each *are* the device that speaks their wire. A separate protocol factory would wrap nothing.

| Registry | Defined in | Subclasses | Dispatch key | Lookup | On miss |
|---|---|---|---|---|---|
| `PLATFORMS` | `adapters/system/_base.py` | `LinuxPlatform`, `WindowsPlatform`, `MacOSPlatform`, `BSDPlatform` | `sys.platform` (BSD variants → `"bsd"`) | `current_platform()` | fall back to linux + warn |
| `DEVICES` | `adapters/device/_base.py` | `ScsiLcd`, `HidLcd`, `BulkLcd`, `AliLcd`, `LyLcd`, `Led` | `info.wire` (the `Wire` enum) | `DEVICES[wire]` | raise `DeviceNotFoundError` |

Each registry lives beside the base class that registers into it (`_base.py`), so the base can register its own children without importing its own package — that would be a cycle. Both are in the **adapter layer**, not core: the registry is the only place naming concrete adapter classes, so core (`Platform` / `Device` ABCs) never imports an adapter. Importing each package fires the side-effect imports of its OS / device modules, which *define* those classes, which is what registers them.

**Registration is a class keyword, not a decorator.** `__init_subclass__` on the base reads it:

```python
class ScsiLcd(BaseDevice[ScsiTransport], wire=Wire.SCSI): ...
class LinuxPlatform(BaseOS, key="linux"): ...
```

The key sits in the class definition rather than floating above it, so it cannot drift from the class or be forgotten separately. Omitting it means "intermediate base, don't register" — which is what `BaseBulkDevice` is.

**The chain** (top to bottom of the composition root):

```
current_platform()                  ← OS dispatch    → Platform
    Platform.scan_devices()         ← OS-specific    → list[DeviceInfo]
        App.attach(vid, pid)        ← composition root
            DEVICES[info.wire]      ← wire           → Device subclass
                Platform.open_transport(wire, …)     → Transport (Wire→opener table)
                    ScsiLcd(info, transport)  (or HidLcd / BulkLcd / AliLcd / LyLcd / Led)
```

**Why registries**: OCP at every layer, and the axes **add** rather than multiply. New OS = one subclass, one new file. New wire = one subclass, one new file. New wire needing a *new kernel interface* = one row in `BaseOS._transport_openers` — not a new abstract method on `Platform` implemented four times. Zero touchpoints in callers (`_boot.trcc`, `App.attach`).

**Verification**: `dev/smoke_factories.py` asserts both registries are populated and dispatch correctly — runs on the dev box without any OS-specific tooling installed.

## Daemon Mode (`TRCC_DAEMON`)

Opt-in singleton background process that owns USB and serves CLI / API / GUI clients over a Unix domain socket. Toggled by `TRCC_DAEMON=1`. Off by default during cutover. Falls back to in-process silently on platforms where `AF_UNIX` is unavailable (Windows < build 17063), so the flag is safe to leave set on every OS.

### Composition root

`src/trcc/_boot.py` — single canonical factory used by **every** UI:

```python
from trcc._boot import trcc
result = trcc().dispatch(SendColor(key="0402:3922", r=255, g=0, b=0))  # CLI / API / GUI all do this
```

The one dispatch surface is the **Command bus** — `app.dispatch(SomeCommand(...))`.
There is no `.lcd` / `.led` role facade (that was the legacy `Trcc` object); UIs
build Commands and dispatch them.

What `trcc()` returns depends on environment:

| Environment | Returns | Behaviour |
|---|---|---|
| `TRCC_DAEMON` unset | real `App` | in-process `App` built from the passed `platform` / `renderer` (both auto-detected — `current_platform()` / `QtRenderer` — when omitted) |
| `TRCC_DAEMON=1` + `AF_UNIX` available | `AppProxy` | auto-spawns daemon via `daemon.ensure_daemon()`; each `dispatch(cmd)` is one socket round-trip |
| `TRCC_DAEMON=1` on Windows < 17063 | real `App` | silent in-process fallback (no `AF_UNIX`), no error |

`AppProxy` (`proxy.py`) is a drop-in for **`App.dispatch(cmd)` only** — it
serializes the Command, round-trips it, and returns the Result. Any *other*
attribute access raises `AttributeError` ("daemon mode only exposes
dispatch(cmd)") — to query App state remotely, send a Command, never reach for
a field.

### Wire format

One line of JSON per request, reflective over `dataclasses.fields` (adding a
Command + Result is zero-touch for IPC). Dispatch:

```json
{"command": "SendColor", "kwargs": {"key": "0402:3922", "r": 255, "g": 0, "b": 0}}
```

The dispatcher (`IPCServer._dispatch_envelope`) looks `command` up in the
Command registry, builds the dataclass via type-hint coercion (str → Path,
list → tuple, dict → nested dataclass, int → Enum), calls `app.dispatch(cmd)`,
and serializes the Result back:

```json
{"type": "SendResult", "ok": true, "message": "Sent 204800 bytes (#ff0000)", "key": "0402:3922", "bytes_sent": 204800}
```

`{"kill": true}` is the one control shape (daemon shutdown).

### Lifecycle

| Action | Command | What happens |
|---|---|---|
| Start daemon | `trcc daemon` | binds socket, refuses if another daemon is running |
| Stop daemon (CLI) | `trcc kill` | sends `{"kill": true}`, waits for socket to disappear |
| Stop daemon (API) | `POST /trcc/kill` | same wire |
| Stop daemon (signal) | `kill <pid>` / SIGTERM | Qt event loop has a 100 ms heartbeat timer so Python signal handlers fire promptly |
| Auto-spawn on first use | implicit when `TRCC_DAEMON=1` and no daemon found | clients call `daemon.ensure_daemon()` → `subprocess.Popen([trcc, "daemon"], start_new_session=True, …)` |

### What's still pending

- **GUI as a remote daemon *client***: `run_gui` already builds its `App` through
  `_boot.trcc()` and can bind the daemon-style `IPCServer` (the `ipc=` param) so
  it *hosts* Command dispatch over the socket. What's unverified is the GUI
  running purely as a *client* of a separate daemon process (holding an
  `AppProxy` instead of a local `App`) across every wire.
- **Daemon round-trip tests**: the suite (1500+) runs on the shipping tree; the
  in-process Command paths are well covered, but dedicated socket round-trip
  tests (encode → daemon → `app.dispatch` → encode_result → decode) are still
  thin.
- **Donor matrix**: SCSI verified end-to-end. HID / Bulk / LY / LED through the
  daemon are inferred-but-unverified — same `App.dispatch` path confirmed for the
  in-process flow, just with JSON serialization in front. Real-hardware donors
  close the matrix.

## THE RULE: every function gets a log line — new code AND old code

**Every function or method you write gets a log line. Every function you touch
that hasn't got one, gets one.** Not "every service method", not "every
Command" — every function.

**Why it is absolute:** users diagnose through `trcc report`, which pastes the
log file. For hardware we do not own and cannot reproduce on, **that paste is
the entire diagnosis**. A function with no log line is a bug report we cannot
answer and a round-trip asking someone to reproduce with a flag they didn't
know existed.

**It is gated, not remembered** — `tests/test_logging_coverage.py`, a
**ratchet**:

* add a silent function → the count rises → **FAIL**
* give a silent function a log line → the count falls → **FAIL**, telling you
  to lower `MAX_SILENT` so the ground is not given back

It could not start green: **1451 of 3074 countable functions were silent** when
it landed (ui 693, adapters 444, core 147, services 143). That number lives in
the test so every diff shows the direction of travel. **It only ever goes down.**

```
PYTHONPATH=src python3 dev/tools/logging_coverage.py --list      # name them
PYTHONPATH=src python3 dev/tools/logging_coverage.py --area ui   # one area
```

**Only two exclusions, both for cause:**

* **abstract methods and stubs** — no body ran, so nothing happened to report.
* **dunders the logger invokes while formatting** (`__repr__`, `__str__`,
  `__len__`, `__eq__`, …) — a log call inside one recurses forever. This is a
  technical impossibility, not a preference. `__init__`, `__enter__`,
  `__getitem__` and `__init_subclass__` are **not** exempt and are counted.

**The file always keeps DEBUG, at every verbosity.** It used to be `DEBUG if
verbose else INFO`, so a user who ran the app normally, hit a problem and sent
a report shipped us a log with every DEBUG line already discarded — the branch
decisions, the resolved values, the silent-skip reasons. Exactly what we needed,
gone. The rotating file is capped (1 MB × 5, `latest` at 10 MB), so per-frame
DEBUG rolls over rather than growing without limit.

## Logging coverage is mandatory

Without logs we can't debug what we can't see.  Legacy had 828 log
calls; the post-cutover tree had 634 (28% gap), and the gap was
concentrated in `core/commands.py` (10 logs for 92 Commands) and the
top-level services (overlay/display/theme: 12 logs total).  Result:
"Theme1 doesn't show its clock" — no logs to trace where the clock
data was lost between DC parse and the render pixel.  That was the
cost.

Rules for every new method touching user-visible state:

1. **One log line on entry** of every public method on a service /
   adapter / Command.  The boundary IS the value (see also memory
   `feedback_thin_layer_log_every_method`).
2. **One log line at every branch that changes outcome** — every
   skip, every fallback, every early-return.  Don't make a future
   debugger guess which branch fired.
3. **Resolved values, not just intent.**  `log.info("draw_text:
   'CPU' at (74, 200) size=24")` beats `log.info("drawing text")` —
   the actual data is what reproduces the bug.
4. **Per-tick noise stays at DEBUG.**  Commands fired every frame
   (`SendFrame`, `RenderAndSend`, `ReadSensors`) set
   ``LOG_LEVEL: ClassVar[int] = logging.DEBUG`` so a default `-v`
   isn't drowned.  Use INFO for one-shot actions (theme load,
   device connect, mode toggle); DEBUG for per-frame.
5. **Warn loudly on silent skips.**  If a metric has no sensor
   reading or a clock source is unresolved, that's a `log.warning`
   with the available context — INCLUDING the sample keys that DID
   resolve so the reporter can spot the typo.
6. **Verify in the log after every behavioral change.**  Before
   declaring a fix done, grep your own logs for the line that
   proves it.  If the line doesn't exist, the test isn't real.

### The `_on_*_tick` trap — per-tick handlers are DEBUG, never INFO

Any `_on_*` method connected to a `QTimer.timeout` or to
`make_timer(...)` fires per-frame (~15–30 Hz for animation; slower
for slideshow / refresh).  Those entry logs go at **DEBUG**, never
INFO — INFO becomes per-frame noise, ~900 KB of `_on_video_tick`
spam in a 22-second session, and buries the user-action lines we
just paid for in the same pass.

Before any bulk `_on_*` logging pass, grep
`QTimer\.timeout\.connect\(self\._on_|make_timer\(self\._on_` and
**exempt every match** from the INFO blanket rule.  Names today:
`_on_video_tick`, `_on_slideshow_tick`, `_on_flash_tick`,
`_on_tick`, `_on_play_tick`, `_preview_tick`.  If a handler already
has first-tick-INFO + subsequent-DEBUG logic (look for
`self._..._first_tick_logged`-style flags + state-transition skip
logic), **leave it alone** — don't prepend a blanket entry log;
the body already does the right thing.

### `configure_logging` is called exactly once

The CLI root callback (`ui.cli.main:_root`) configures logging based
on the `-v` flag.  **Subsequent calls overwrite the level.**  Launch
entry points (`ui.gui.__init__.launch`, future qtgui equivalent,
etc.) must NOT re-call `configure_logging` — a second call with
`verbosity=0` silently downgrades DEBUG back to INFO and the user's
`-v` is silently lost.  If a new entry point can legitimately be
invoked without going through the CLI (rare; none exist today), add
a guard: only configure if no `_trcc_handler`-tagged handler is
already attached on the root logger.  When a user reports "DEBUG
lines I expect aren't showing up", first grep the log for
`configure_logging:` and check whether it appears more than once
with different levels.

### Coverage applies to the WHOLE app surface, not just services

The rules above apply equally to every layer the user can interact
with: services, Commands, adapters, **GUI panels (`ui/gui/*.py`,
`ui/qtgui/*.py`)**, **CLI command bodies (`ui/cli/*.py`)**, **API
endpoints (`ui/api/*.py`)**, the daemon, the IPC server.  A silent
panel is a debugging blind spot — when the user reports "metrics
don't update when I change the refresh interval," but
`uc_about._on_refresh_changed` has no log line, **the bug is
invisible to anyone reading the log**.  No "let me start with this
panel" — the diligent move is **every public method on every
panel, in one pass**.

Benchmark for "covered":

| Layer | What counts as covered |
|---|---|
| Service method | log.info on entry with resolved args |
| Command.execute | log.info on entry; log.warning on every guard-fail branch |
| Adapter public method | log.info on entry; log.debug per-tick |
| GUI panel `_on_*` slot | log.info with the resolved widget state that fired it (button value, slider position, picker selection) |
| GUI panel `set_*` mutator | log.info with old → new transition |
| GUI panel `update_*` per-tick refresh | log.debug (per-tick noise) with the readings/keys actually rendered |
| CLI command body | log.info on entry with args; log.warning on guard-fail |
| API endpoint | log.info on entry with the path params (path-sanitized — never raw user input) |

Anti-pattern (the half-fix that wastes a session):

* Adding logs to ONE method of ONE panel because that's where you
  last looked.  Next bug lands in a sibling method that's still
  silent, and you're blind again.

If a layer is under-logged (CLI has 1 log call for 114 methods,
qtgui has 12 for 310, etc.), **that's a debt to clear in one pass,
not a "we'll do it when we touch that file" deferral**.

This rule is non-negotiable; "code-first, logs-after" wastes hours
on the next bug.

## Conventions

### Code Style
- **OOP** — classes with clear SRP. `dataclass` for data, `Enum` for categories.
- **Pure Python** — use the language to its fullest. Dunders (`__getitem__`, `__contains__`, `__iter__`, `__len__`) for registry classes so data describes itself. `@property` for derived attributes. `match/case` for pattern dispatch. Generators for lazy iteration. Context managers for resource lifecycle. Data should be self-describing — behavior derived from structure, not plumbing code.
- **DRY** — 3+ duplicates = centralize. Two = smell. One-off = inline.
- **Type hints** on all public APIs
- **Logging**: `log = logging.getLogger(__name__)` — never `print()`
- **Paths**: `pathlib.Path` everywhere; `os.path` only where a *lexical* path-string operation is required — currently just `adapters/repo/data_install.py` (zip-slip member normalization, where pathlib deliberately won't collapse `..`). Enforced by `tests/test_architecture_boundaries.py::test_os_path_confined_to_zip_slip_normalisation` (the old `data_repository.py` was removed in the cutover).
- **Thread safety**: Qt signals for background→GUI communication — never `QTimer.singleShot` from non-main threads
- **Import from canonical location** — `from .core.models import X`, not re-defining locally

### SOLID
- **SRP** — services own logic, views own rendering, models own data
- **OCP** — subclasses self-register by naming their key in the class line (`class LinuxPlatform(BaseOS, key="linux")`, `class ScsiLcd(BaseDevice[ScsiTransport], wire=Wire.SCSI)`); `__init_subclass__` on the base does the rest. New device = new registry row, not modified logic.
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
- **We ship BOTH `pytest` and `pytest-qt` — use them.** Default to **pure
  `pytest`, no Qt**: Presentation Models (`ui/presentation/`), services, core,
  and any toolkit-free interaction logic must be tested without constructing a
  `QApplication` — that's the whole point of extracting them. Reach for
  **`pytest-qt` (`qtbot`)** ONLY at the thin View↔PM binding seam — where you
  must wait on a real Qt signal (`qtbot.waitSignal`) or simulate input
  (`qtbot.mouseClick`). Don't hand-roll a bare `QApplication` fixture to fake
  what `qtbot` already gives you. `pytest-qt` is a declared test dependency in
  `pyproject.toml`; keep it declared (never rely on an ambient install).
- Tests mirror `src/trcc/` hexagonal layers (`tests/{core,services,adapters/{device,infra,system},cli,api,gui,ui/presentation}/`)
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

## Development Environment
- **Build on Python 3.12.** This is a portability decision, not a personal preference: Debian stable and several other LTS distros ship 3.12 as the system interpreter, and building under 3.12 keeps the project usable on every Python from 3.12 forward without per-version pain.
- Don't use 3.13 / 3.14-only syntax or stdlib features. `pyproject.toml` says `requires-python = ">=3.10"` for the install gate, but the dev gate is **3.12** — anything newer is allowed to work but not required to.
- Run tests / scripts with `python3.12` explicitly. `/usr/bin/python` on the dev box may point at 3.14; a 3.14-only crash (e.g. a `QFontDatabase` segfault, a `pyusb._pack_` deprecation, a typer/sudo_reexec issue) is not automatically a project bug — repro under 3.12 first.

## Known Issues
- **GUI overlay/settings edits don't apply + overlays render duplicated — FIXED
  + render-verified 2026-06-06.** The double-draw is gone: `build_frame`
  (`services/display.py`) now renders ONE effective layout via
  `resolve_overlay_elements(theme_config, mask, user)` (`services/overlay.py`) —
  precedence user > mask > theme, each REPLACES (never adds). The old additive
  `user_elements=` pass was removed. Verified at the render level through the real
  `OverlayService` + `DisplayService.build_frame`: an edit draws each element
  exactly ONCE, applies at the new position, no ghost at the old (8 resolver unit
  tests in `test_overlay_resolve.py` + a draw-count check). The reported symptom
  is resolved.
  **Font typeface — FIXED 2026-06-06.** Overlay text rendered in Noto Sans, not
  the themes' Microsoft YaHei. Root cause traced: the bundled YaHei TTCs
  (`assets/fonts/MSYH.TTC`/`MSYHBD.TTC`) were never registered with Qt, AND
  `OverlayService` doesn't pass a per-element family — overlay text draws in the
  app DEFAULT font (`_get_font` resolves `QFont()`). Fix in
  `adapters/render/qt.py::_register_bundled_fonts` (called from `_ensure_qt_app`,
  the GUI + headless render chokepoint): glob-register every `assets/fonts/` file,
  then set the app default font to `Microsoft YaHei` resolved from the UNION of the
  user's system/downloaded fonts + the bundled copy (user's install wins, bundled
  fills the gap). Verified: `_get_font(...).family() == "Microsoft YaHei"`. NOTE:
  per-element theme font names are still NOT plumbed to `draw_text` (a separate
  latent feature, not the reported typeface bug — all themes use YaHei).
  **Still open in this area** (separate, secondary): DEFERRED additive sites in
  EXPORT/MASK-AUTHORING (`export_dc`/`ThemeDcExport`, `persist_user_mask_dc`,
  `UploadMask` seed, the DC codec `user_overlay_elements=` param) still append user
  onto `config["elements"]` — the render + SaveTheme paths are fixed, these
  export/mask paths aren't (they need a different empty-vs-theme fallback; clean fix
  = remove the codec param, resolve effective elements at the command layer). DC
  parser is CORRECT. See `memory/project_gui_overlay_edit_bug.md`.
- `pyusb 1.3.1` deprecated `_pack_` on Python 3.14 — suppressed in pytest config
- `pip install .` can use cached wheel — use `pip install --force-reinstall --no-deps .`
- CI runs as root — mock `subprocess.run` in non-root tests
- Never `setStyleSheet()` on ancestor widgets — blocks `QPalette` image backgrounds
- Optional imports (`hid`, `dbus`, `gi`, `pynvml`) need `# pyright: ignore[reportMissingImports]`
- C# asset suffixes are legacy — `Assets.get_localized()` maps ISO 639-1 → legacy suffixes via `ISO_TO_LEGACY`
- **Issue #87**: Python 3.14 typer crash in `sudo_reexec` — FIXED: dispatches via `python -c` (direct function call), bypasses typer.
- **`pyudev` is a REQUIRED Linux dependency** (not graceful-optional): hotplug —
  live device attach/detach AND the boot-time coldplug — is built on it.
  Without it the udev monitor can't start and a device plugged in after launch
  (or missed at the boot discover) never connects. Declared in `pyproject.toml`
  (`sys_platform=='linux'`) + the deb/rpm/Arch/Nix specs. The graceful-`None`
  fallback stays only for environments that strip libudev, and now logs a
  WARNING naming the consequence.
- **Multi-device mock — RESTORED 2026-06-04** (`20cb97b9`). `tests/mock_platform.py`
  `MockPlatform(specs, root)` (extends conftest `FakePlatform`) scripts per-device
  SCSI/Bulk/LED handshakes so `dev/mock_gui.py` simulates any fleet with zero
  hardware; `dev/_mock_bootstrap` selects it when `dev/devices.json` (local,
  gitignored) yields specs, else the real `DevPlatform`. `dev/smoke_portrait_854480.py`
  is the self-contained verifier. **Still NOT scripted**: HID + LY wires
  (warn-and-skip); add their handshake byte formats before simulating those panels.
- **Non-square (`rotate=True`) panels — geometry/portrait now mock-verified.** The
  cutover fragmented legacy's geometry pipeline (#136); restored + verified:
  data-download-on-handshake, content-matched portrait composition (portrait
  theme → `visual=480x854`, device 90° skipped; landscape → `854x480`),
  preview-bezel reorientation. The geometry-DTO "re-centralize" was a phantom
  (already done via `DeviceProfile` + `DisplayService`); dead `native_orientation`
  removed (`39516d47`). **PREVIEW rotation FIXED 2026-06-23 (`959b1648`, mock-verified
  "solved all the mess ups on rotation"):** `rotate=True` panels showed the preview
  image rotated by the device-mount 90° in an upright bezel (FBL 50 sideways /
  FBL 192 upside-down). C#-grounded — the decompile's `RotateFlip` count is **0**, the
  C# never rotates the preview image (composes on an orientation-sized canvas;
  `SetMyUCScreenImage` FBL 50 @ angle 0 → 320×240 landscape control). Fix is
  PREVIEW-ONLY: `build_frame` captures `preview_surface` BEFORE the device-rotate
  steps; `DisplayService._apply_post_processing` gained `device_rotate=False` for
  `build_preview_surface`. Wire untouched (the 150 geometry tests assert WIRE bytes
  and stayed green). **Still pending — Tier-1 WIRE-output gap (separate, hardware-gated)**:
  whether our wire rotation matches the C# `isFanZhuan`/`get_encode_rotation` model —
  the device encode-rotation table (`encode_base`/`encode_invert`/`encode_sub_bases`)
  is recorded-but-UNWIRED, a blanket `rotate→90°` replaced legacy's `get_encode_rotation`
  on the 4 widescreen JPEG panels (FBL 114/128/192/224). The mock can't confirm wire
  output; needs a real device. See `memory/project_geometry_subsystem_and_mock.md`.

## GitHub Issues
- Never use "Fixes #N" in commit messages — GitHub auto-closes on push to default branch
- Don't close issues until reporter confirms the fix works
- Every reply ends with funding footer: `\n\n---\nIf this project helps you, consider [buying me a beer](https://buymeacoffee.com/Lexonight1) 🍺 or [Ko-fi](https://ko-fi.com/lexonight1) ☕`
- Check if reporter is a donor (README thanks section) — thank by name if so
- **Package upgrade instructions must be copy-paste ready** — always provide the full `wget -c <URL>` + install command so users can paste directly into their terminal. Use the actual download URL from `gh release view --json assets`, not just "go to Releases page". Distro-specific:
  - Arch: `wget -c <url>.pkg.tar.zst && sudo pacman -U trcc-linux-*.pkg.tar.zst`
  - Fedora: `wget -c <url>.rpm && sudo dnf install ./trcc-linux-*.rpm`
  - Ubuntu/Debian (legacy): `wget -c <url>.legacy_all.deb && sudo apt install ./trcc-linux_*.legacy_all.deb`
  - Ubuntu/Debian (standard): `wget -c <url>.deb && sudo apt install ./trcc-linux_*.deb`

## Deployment
- Default branch: `main`
- Never push without explicit user instruction
- Dev repo: `~/Desktop/projects/thermalright/trcc-linux`
- Testing repo: `~/Desktop/trcc_testing/thermalright-trcc-linux/`
- PyPI: `trcc-linux` (published)
- Tag push triggers PyPI release — always `git tag v{version} && git push origin v{version}` after push

## Development Workflow

### Plan Before Coding
Non-trivial changes: think through full impact, state the plan, wait for confirmation, THEN implement. Never jump in and patch as you go.

- **Separate the fix from the redesign.** When a bug is reported, land the *minimum* fix and verify it FIRST — do not bundle a refactor or redesign into a bug fix. A redesign is its own deliberate, separately-approved project, never smuggled in under "while I'm in here." (Cautionary: a one-line `active_themes` fix nearly got buried under a multi-file asset-library redesign — the fix is the deliverable; the redesign is a separate choice.)
- **Large changes go in verifiable increments, not one big-bang pass.** Break the plan into phases where each is independently verified (ruff + pyright + targeted tests + run the app) and leaves the app in a working state before the next begins. Big-bang edits across many files are how half-migrated states and subtle breakage sneak in — especially when the change touches the very path you're fixing or relocates user data. Do the data-touching / behavior-changing phases last and most carefully.
- **Foundation work is not a feature.** Plumbing (paths, ports, resolution wiring) that changes no user-visible behavior is "the plumbing is in," not "the feature works" — say which, per "No progress theater."
- **No copy-pasta.** Reuse over duplication: read the existing/legacy implementation end-to-end, then translate the pattern *in place* (rewire imports + call sites) — never copy blocks between files or trees. Store each asset/fact once and reference it; don't duplicate it across consumers.
- **Professional Python, hexagonal/SOLID/DRY by default.** Every change meets the bar set in the "Architecture", "Code Style", "SOLID", and "Hexagonal Purity" sections above — idiomatic Python (dataclasses, enums, dunders, `match`/`case`, type hints, context managers, generators), classes with one clear responsibility, dependencies pointing inward only (core imports no adapter), ports at the boundaries. Elegance and correctness are the bar — not "it works."

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
4. Bump the version — **exactly two literals**: `src/trcc/__version__.py` and
   `pyproject.toml`.  **Do NOT add one to `flake.nix`** — it derives the version
   from `pyproject.toml` (`builtins.fromTOML`, PR #209), and re-introducing a
   literal there re-creates the drift that PR removed.
5. Regenerate the man pages — `PYTHONPATH=src python3.12 dev/gen_manpages.py`.
   All 7 carry the version in their `.TH` line, so
   `tests/test_manpages.py::test_committed_manpages_are_current` FAILS at step 7
   until you do.  (`doc/REFERENCE_CLI.md` is generated too but carries no
   version — it must come back byte-identical; if it moves on a version-only
   bump, its output is not deterministic and that's a bug.)
6. Update `doc/CHANGELOG.md` — **this is the version history**.  `__version__.py`
   no longer holds a history block; its docstring points here.
7. Lint + test again
8. Commit + push version bump to `main`
9. `git tag v{version} && git push origin v{version}` (triggers CI + PyPI)
10. `gh release create v{version} --target main --title "v{version}"`
11. Verify the release landed — `gh run list` all green, and PyPI actually
    serves the new version — BEFORE telling any reporter to upgrade.
12. Comment on relevant GitHub issues.  Order is **fix → verify → bump → reply**;
    never announce an unconfirmed change as "the fix" (see
    `memory/feedback_fix_then_bump_then_reply.md`).  Upgrade commands must be
    copy-paste ready and matched to how that reporter actually installed
    (pacman / dnf / apt / `pipx upgrade`) — real URLs from
    `gh release view v{version} --json assets`.

### Trigger Words
Bare `patch`, `minor`, or `major` → full release workflow:
1. Lint + test uncommitted changes first
2. Commit + push existing changes to `main`
3. Bump version in `__version__.py` + `pyproject.toml` (two files — `flake.nix`
   derives it; see the Release list above)
4. Regenerate man pages (`dev/gen_manpages.py`) + changelog entry
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
- **Methods of Operation (the working playbook)**: `METHOD.md` — the C#-oracle port loop (observe → oracle → diff → locate → KISS → verify → guard → confirm), its four executable stations, the "where does the fix go?" layer map, and the anti-patterns. Read it before porting any device/feature.
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

# Architecture

## Big picture

```text
                   ┌──────────────────────────────────┐
                   │  Composition root (_boot.trcc)   │
                   │  Builds either:                  │
                   │     Trcc          (in-process)   │
                   │     TrccProxy     (daemon mode)  │
                   └────────────────┬─────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
              ▼                     ▼                     ▼
       ┌────────────┐        ┌────────────┐        ┌────────────┐
       │ ui/cli/    │        │ ui/gui/    │        │ ui/api/    │
       │ Typer cmds │        │ TRCCApp    │        │ FastAPI    │
       └─────┬──────┘        └─────┬──────┘        └─────┬──────┘
             │                     │                     │
             └─────────────────────┼─────────────────────┘
                                   ▼
                         ┌────────────────────┐
                         │ Trcc (or proxy)    │
                         │   .lcd             │  ← LCDCommands
                         │   .led             │  ← LEDCommands
                         │   .control_center  │  ← ControlCenterCommands
                         │   .events          │  ← EventBus
                         │   .lcd_devices     │  ← DeviceRegistry
                         │   .led_devices     │
                         │   .renderer        │
                         └─────────┬──────────┘
                                   │
                  ┌────────────────┼────────────────┐
                  ▼                ▼                ▼
            services/        core/device/      adapters/
            (pure logic)     (LCDDevice +      (USB transports,
                              workflows)        renderer, system)
```

UIs depend only on the `Trcc` shape.  In daemon mode `_boot.trcc()`
returns a `TrccProxy` instead — same surface, every call serialized to
the daemon over a Unix socket and back.

## Layers (hexagonal: ports & adapters)

| Layer | What lives here | Hard rules |
|---|---|---|
| `core/models/` | Domain dataclasses, enums, registries.  No I/O, no framework deps. | Pure Python.  No imports from anywhere outside `core/`. |
| `core/ports.py` | Abstract ports — `Renderer`, `Platform`, `OSConfig`. | The contracts adapters implement. |
| `core/device/` | `LCDDevice` + `LEDDevice` facades + composed helpers (persistence, theme workflow). | Holds USB-state-aware logic, but never opens USB itself — that's the protocol's job. |
| `core/lcd_commands.py`, `led_commands.py`, `control_center_commands.py` | Command bus surface (`Trcc.lcd.X(idx, ...)`). | Each method is `@command`-decorated; routes through index → `DeviceRegistry` lookup → device delegate. |
| `core/trcc.py` | The single `Trcc` class every UI holds. | Pure DI: every dep injected at ctor.  No global mutation. |
| `core/trcc_proxy.py` | `TrccProxy` — drop-in replacement that routes calls over IPC. | Structurally typed against `Trcc`; UIs never branch on which they got. |
| `core/events.py` | `EventBus` + `Topic` strings for state-change notifications. | Subscribers are loosely coupled; publishers don't know who's listening. |
| `services/` | Stateful coordination — `DeviceService`, `DisplayService`, `OverlayService`, `LEDService`, `ThemeService`, `MediaService`, `SystemService`. | No Qt, no PySide6, no FastAPI imports.  Pure Python with the rendering port. |
| `adapters/device/` | USB transports — SCSI sg_io, HID, Bulk, LY, LED.  Plus `factory.py` which routes (vid, pid) → protocol via self-registering `@register()`. | Implements transport ABCs from `template_method_device.py`. |
| `adapters/render/qt.py` | `QtRenderer` — implements the `Renderer` port via QImage/QPainter. | Single concrete implementation today; the port lets us swap. |
| `adapters/system/` | Per-OS platform implementations + sensor enumeration. | One `Platform` ABC, four subclasses (Linux/Windows/macOS/BSD). |
| `adapters/infra/` | I/O — config, logs, downloads, fonts, archive extraction, cloud themes. | Stateful but framework-agnostic. |
| `ipc.py` | Unix socket server bound to a `Trcc`.  Manifold dispatch + long-lived event subscriptions. | Daemon-side; transport only. |
| `daemon.py` | `trccd` entry point — builds Trcc, wires IPCServer, runs Qt loop. | The other side of `_boot.trcc()`'s `TrccProxy` branch. |
| `_boot.py` | The single composition root: `trcc()` returns the cached process-local handle. | Picks `Trcc` vs `TrccProxy` based on `TRCC_DAEMON`. |
| `ui/cli/` | Typer CLI commands. | Calls `trcc().lcd.X(...)` etc.  Never imports from adapters directly. |
| `ui/api/` | FastAPI app + endpoints. | Same. |
| `ui/gui/` | PySide6 `TRCCApp` + per-device handlers. | Same. |
| `install/` | Standalone setup wizard. | Runs without `trcc` installed (used during distro setup). |

## The composition root

Every UI does the same thing:

```python
from trcc._boot import trcc
result = trcc().lcd.set_brightness(0, 75)
```

`_boot.trcc()` is cached per-process.  Behaviour:

- **`TRCC_DAEMON` unset** (default): builds an in-process `Trcc` once.  Wires platform, renderer, services, then runs `Trcc.discover()` so the first command sees devices.
- **`TRCC_DAEMON=1`**: returns a `TrccProxy` connected to a running daemon.  Auto-spawns the daemon via `daemon.ensure_daemon()` on first call.
- **Windows < build 17063**: `AF_UNIX` unavailable → silent fallback to in-process.  The flag is safe to set on any OS.

Tests + GUI launch can override the platform / renderer:

```python
trcc(MockPlatform(specs))                # tests
trcc(platform, renderer=QtRenderer(),    # GUI: windowed renderer + deferred discover
     discover_now=False)
```

## In-process vs. daemon mode

| | In-process (`TRCC_DAEMON` unset) | Daemon (`TRCC_DAEMON=1`) |
|---|---|---|
| `_boot.trcc()` returns | `Trcc` | `TrccProxy` |
| USB ownership | the calling process | the daemon process |
| `Trcc.lcd.set_brightness(0, 75)` | direct method call | manifold IPC request → daemon dispatches → response back |
| `Trcc.events.subscribe(Topic.FRAME, cb)` | in-process pub/sub | long-lived socket per subscription, JSON event lines |
| `Topic.FRAME` payload | native `QImage` | encoded as `{"__surface__": "<base64 PNG>"}` envelope at the wire boundary; decoded back to `QImage` in the proxy's reader thread |

The proxy is a structural drop-in for `Trcc` — `.lcd`, `.led`, `.control_center`, `.events` all match by shape.  GUI / CLI / API call sites never have to branch.

## Manifold IPC

Every dispatched call serializes as:

```json
{"role": "lcd", "method": "set_brightness", "args": [0, 75], "kwargs": {}}
```

Response: `{"success": bool, "message": str, "error": str | null, ...extras}`.

Special wire shapes:
- `{"kill": true}` — graceful daemon shutdown.
- `{"subscribe": "<topic>"}` — open a long-lived event subscription.
- `{"role": "_meta", "method": "lcd_descriptors", ...}` — non-facade Trcc methods.

Path / bytes args are sanitized at the proxy boundary (`Path → str`, `bytes → {"__bytes__": "<base64>"}`) and reconstructed server-side.  `Topic.FRAME` event payloads use the same envelope pattern for `QImage` (`core/wire.py`).

## Device descriptors

`Trcc.lcd_descriptors()` and `Trcc.led_descriptors()` return `list[DeviceInfo]` — JSON-safe identity descriptors.  `TrccProxy.lcd_descriptors()` mirrors it: same return type, fetched over IPC via `_meta.lcd_descriptors`.

`DeviceInfo.to_wire_dict()` / `from_wire_dict()` handle the `UsbAddress` nested dataclass and the JSON `tuple → list` quirk for resolution.  The GUI sidebar today still iterates the live `lcd_devices` registry, so daemon-mode GUI launches with an empty sidebar — descriptor-driven handler construction is the next refactor (10C.6 in the migration log).

## Command bus

`LCDCommands`, `LEDCommands`, `ControlCenterCommands` are the public method surface.  Each method:

```python
@command(result_cls=FrameResult, topic=Topic.LCD_BRIGHTNESS, include_frame=True)
def set_brightness(self, lcd: int, percent: int):
    if (dev := self._get(lcd)) is None:
        return FrameResult(success=False, error=f'LCD {lcd} not found')
    return dev.set_brightness(percent)
```

`@command` does the boilerplate: catches exceptions → `OpResult`, optionally publishes a topic on success, optionally bundles the result frame for IPC.

Indexes (`lcd: int`) are how UIs address devices.  The `DeviceRegistry` lets you also look up by path or `(vid, pid)`, but the wire format is always integer index — that's the manifold's contract with the proxy.

## Events

`Trcc.events` is an `EventBus`.  Topics in `core/events.py`:

- `DEVICE_LIST`, `DEVICE_CONNECTED`, `DEVICE_DISCONNECTED` — device lifecycle.
- `FRAME`, `PROGRESS` — streaming.  Payloads can carry surfaces; the IPC forwarder envelopes them.
- `METRICS` — sensor data (1 Hz tick).
- `LCD_*`, `LED_*`, `CONTROL_CENTER_*` — state-change announcements after each successful command.
- `BOOTSTRAP_PROGRESS`, `DATA_READY` — first-run extraction signal.

Every command that mutates state publishes its topic.  Subscribers (GUI handlers, CLI watchers, API SSE) react.  Daemon mode: events flow daemon → client only via long-lived subscription sockets.

## Devices (`core/device/`)

`LCDDevice` and `LEDDevice` are the device-level facades.  `LCDDevice` was split in 10B:

```text
LCDDevice                 (core/device/lcd.py — the facade)
├── LCDPersistence        (core/device/lcd_persistence.py)
│       SRP: per-device config writes, restore_device_settings reads
└── LCDThemeWorkflow      (core/device/lcd_theme_workflow.py)
        SRP: multi-step theme load / restore / rotation reload /
             save / import / export
```

`LCDDevice` composes the helpers in its ctor and exposes the public API as one-line delegates.  External callers see no change.

## Display pipeline (`services/display.py`)

`DisplayService` is the orchestrator.  Its rendering subgraph and CLI/API blocking loops were split in 10B:

```text
DisplayService            (services/display.py — orchestrator + state)
├── RenderPipeline        (services/display_pipeline.py)
│       SRP: pure rendering — composite, brightness, split overlay,
│            preview rotation.  Owns the split-overlay asset cache.
├── display_loops         (services/display_loops.py — module-level fns)
│       SRP: blocking video / static keepalive loops for CLI + API
└── ThemePersistence      (services/theme_persistence.py)
        SRP: theme save/import/export
```

Geometry primitives (`native_resolution`, `_rotation`, `_data_root`, `_user_root`, `_has_portrait_themes`) live on `DisplayService` directly — they used to be a separate `Orientation` class which 10B.0a deleted.  Per-rotation derivations (`output_resolution`, `canvas_resolution`, `theme_dir`, `web_dir`, `masks_dir`, `user_theme_dir`, `user_masks_dir`) are properties on `DisplayService`; `LCDDevice` exposes them as delegates.  `LCDHandler` reads `lcd.theme_dir` and the device proxies the lookup.

## GUI shell

`ui/gui/trcc_app.py::TRCCApp` is a thin `QMainWindow` shell.  It holds a `Trcc` handle, builds widget panels (`uc_*.py`), and creates one `LCDHandler` or `LEDHandler` per detected device.

Per-device `LCDHandler` (`ui/gui/lcd_handler.py`) now routes every write through `self._app.lcd.X(self._lcd_idx, ...)` (10C.2) — the command bus.  In daemon mode that same code path serializes the call to the daemon.  Reads still go through `self._lcd.X` (the live device); decoupling those is the 10C.6 follow-up.

Multi-LCD keep-alive (issue #120) lives entirely in handler-local state (`_ui_active` flag).  Inactive handlers stop writing to shared widgets but keep their animation timer running so the LCD's panel doesn't go dark when the user switches devices in the GUI.

## Settings

`Settings` (`conf.py`) is a singleton-ish handle.  Phase 10A.3 + Tier E moved every consumer onto pure DI:

- `Trcc` takes `settings` at ctor.
- `LCDCommands`, `LEDCommands`, `ControlCenterCommands` take `settings` at ctor.
- `SystemService` takes `settings` at ctor.
- CLI / API / GUI read settings via `_trcc().settings` (the cached factory) — no direct `from trcc.conf import settings` imports anywhere in the call chain.

Static path-resolution leaves (`data_repository.py`, `theme_downloader.py`) intentionally use the global — they're stable utilities that read paths once after `init_settings(platform)` runs at boot.

## Verification

Three layers, not interchangeable:

| Layer | Files | Catches |
|---|---|---|
| Unit tests | `tests/` (5668 today) | logic bugs, contract violations, regressions in pure-Python paths |
| Programmatic GUI smoke | `dev/smoke_rotation_mask.py` (19 assertions) | end-to-end rotation / portrait / mask flow on a non-square mock device — the path the unit tests can't reach because Qt signals + theme reload + persistence interact |
| Daemon-mode smoke | `dev/smoke_daemon_gui.py` (13 assertions) | descriptors + command-bus dispatch + FRAME event envelope round-trip over a real Unix socket — proves the proxy is a true substitute for Trcc |

Both smokes use `MockPlatform` — no real USB, no reporter feedback loop.  Run before claiming any refactor is "verified".

`PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 dev/smoke_rotation_mask.py`
`PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 dev/smoke_daemon_gui.py`

## Conventions

- **Pure DI**: every collaborator is injected at construction.  No setters, no module mutation, no `import` from a deeper layer.
- **Composition over inheritance**: `LCDDevice` composes `LCDPersistence` + `LCDThemeWorkflow` rather than subclassing.
- **One-way dependencies**: `core/` → nowhere; `services/` → `core/`; `adapters/` → `services/` + `core/`; `ui/` → `core/` only via `_boot.trcc()`.
- **Public API stays stable across refactors**: when 10B split god classes, every existing `lcd.set_brightness(...)` / `display_svc.run_video_loop(...)` kept the same signature.  Internal extraction never breaks call sites.
- **JSON-safe wire formats**: any value crossing the IPC boundary has a `to_wire_dict` / `from_wire_dict` pair (`DeviceInfo`) or an envelope helper (`core/wire.py` for native surfaces).
- **No singleton patches in tests**: tests construct real (or mock) collaborators and inject them.  `tmp_config` fixture for path isolation.
- **No `# type: ignore`** without a comment explaining the runtime quirk.  Acceptable cases: PySide6 stub vs. runtime mismatch; `# type: ignore[union-attr]` after we've already null-checked.

## Reference

- `doc/HISTORY_PROJECT.md` — release timeline, milestones.
- `doc/HISTORY_ARCHITECTURE.md` — refactor history, why each layer exists.
- `doc/CHANGELOG.md` — user-facing changes per release.
- `CLAUDE.md` — the project's conventions for Claude Code sessions.

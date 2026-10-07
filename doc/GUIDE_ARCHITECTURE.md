# Architecture

How TRCC Linux is put together, for anyone changing it — or building their own
UI against it.

## Big picture

One `App` owns the devices and the services. Every user interface is a thin
client that builds a **Command** and dispatches it, then renders the typed
**Result**. Nothing else crosses the boundary.

```text
   trcc gui     trcc qtgui      trcc api        trcc <cmd>
       │            │               │               │
       └────────────┴───────┬───────┴───────────────┘
                            │   app.dispatch(SomeCommand(...)) -> Result
                            ▼
                          App                    (app.py)
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        services/      EventBus     devices{key}
     display, theme,   (core/       ScsiLcd · HidLcd
     overlay, led,     events.py)   BulkLcd · LyLcd · Led
     metrics, …            │
                           └──► UIs observe: FrameSent, DeviceConnected,
                                SensorsUpdated, ErrorOccurred, …
```

A UI never imports a service or an adapter. If it needs something the bus does
not expose, the answer is a new Command, not an import — see
[`doc/REFERENCE_COMMANDS.md`](REFERENCE_COMMANDS.md) for the full surface.

## Layers (hexagonal: ports & adapters)

Dependencies point inward only: `adapters/` → `services/` → `core/`. Core
imports no adapter, ever.

| Path | What lives there |
|---|---|
| `core/models.py` | domain data — enums, VID/PID types, constants. No logic, no I/O. |
| `core/ports.py` | the ABCs: `Platform`, `Renderer`, `Paths`, `Device` transports, `SensorEnumerator`, … See [`REFERENCE_PORTS.md`](REFERENCE_PORTS.md). |
| `core/commands/` | every Command and Query. One file per domain; `__init__.py` re-exports them all. |
| `core/results.py` | every Result dataclass. All carry `ok` and `message`. |
| `core/events.py` | the `Event` hierarchy and `EventBus`. |
| `services/` | business logic, pure Python — display, overlay, theme, LED effects, metrics. No Qt, no framework. |
| `adapters/device/` | one class per wire: `ScsiLcd`, `HidLcd`, `BulkLcd`, `LyLcd`, `Led`. |
| `adapters/system/` | one class per OS: `LinuxOS`, `WindowsPlatform`, `MacOSPlatform`, `BsdOS`. |
| `adapters/render/qt.py` | `QtRenderer` — the only place Qt touches rendering. |
| `ui/{cli,api,gui,qtgui}/` | the four front-ends. Thin. |
| `ui/presentation/` | toolkit-free presentation models shared by the graphical UIs. |
| `app.py` | the `App`: owns devices, services, the bus, and `dispatch`. |
| `_boot.py` | the one factory every UI calls. |
| `ipc.py` · `proxy.py` · `daemon.py` | the daemon and its wire. |

## The composition root

`trcc._boot.trcc()` is the only way a UI obtains an App:

```python
from trcc._boot import trcc
app = trcc()                     # the shared App's proxy, or an in-process App
```

It takes an optional `platform` and `renderer` for injection — that seam is what
lets `dev/mock.py` drive any UI against a `MockPlatform` with no hardware.

## The shared App vs in-process

| | default | `TRCC_DAEMON=0`, as root, or on Windows |
|---|---|---|
| `trcc()` returns | `AppProxy` | `App` |
| a dispatch is | one JSON round-trip over a Unix socket | a method call |
| who owns USB | the shared App (`trcc daemon`), started by the first UI that needs it | this process |

`AppProxy` implements the `CommandBus` port and **nothing else**:
`dispatch(cmd) -> Result`, `events` (the App's events, streamed over the socket
and republished on a local bus), `remote`, and `on_app_gone` (told when the App
stops on purpose or dies). Every other attribute raises. That is deliberate: a
UI that reaches for `app.settings` or `app.devices` works locally and breaks
remotely, so the proxy makes the mistake loud rather than silent.

When the App stops on purpose (`trcc kill`), the last line of every event
stream says so and every open UI closes. When it dies, the stream just ends:
each open UI starts again, which starts a new App.

## The IPC wire

One line of JSON per message. Three shapes:

```json
{"command": "SetBrightness", "kwargs": {"key": "0402:3922", "percent": 75}}
{"type": "BrightnessResult", "ok": true, "message": "...", "percent": 75}
{"kill": true}
```

Serialization is reflective over `dataclasses.fields`, so adding a Command and
its Result is zero-touch for IPC. `Path` travels as a string, `bytes` as
`{"__bytes__": "<base64>"}`, and a value that cannot be represented (a live
renderer surface) is dropped to `null` with a warning rather than crashing the
call.

Devices are addressed by **`key`** — the `"vid:pid"` string, e.g.
`"0402:3922"` — everywhere: in Commands, on the wire, and in `app.devices`.

## Command bus

The contract is **135 types: 101 Commands and 34 Queries**. A `Query` is a read
and nothing else; naming the kind is what makes a missing read obvious rather
than archaeological.

Dispatch is pure polymorphism — `App.dispatch` is `cmd.execute(self)`. There is
no handler map and no registration table, so **adding a capability is one frozen
dataclass with an `execute` method**, plus its Result in `core/results.py` and a
re-export from `core/commands/__init__.py`. That last step is gated: a Command
that is not re-exported works in-process but is undispatchable over IPC, so a
test enumerates every one and fails if any is missing.

Every Command guards its own preconditions and returns a failed Result with a
message; it does not raise at the UI.

## Events

`EventBus` is synchronous: `publish` calls each handler **on the publishing
thread**, matching on the event's **exact type** (not `isinstance`). Two
consequences worth knowing before you subscribe:

- A handler must hand off, never work inline. The graphical UIs subscribe a
  `BusBridge` whose whole body is one Qt `signal.emit`; anything heavier would
  run on the render thread.
- Subscribing to a base class receives nothing. Subscribe to the concrete type.

## Devices — two registries, one idiom

The OS and the wire are each a keyed registry, and a class states its own key in
its class line:

```python
class LinuxOS(BaseOS, key="linux"): ...
class ScsiLcd(BaseDevice[ScsiTransport], wire=Wire.SCSI): ...
```

```text
current_platform()                 sys.platform  -> Platform
  Platform.scan_devices()                        -> list[DeviceInfo]
    App.attach(vid, pid)
      DEVICES[info.wire]           Wire enum     -> Device subclass
        Platform.open_transport(wire, ...)       -> Transport
```

Wires: `SCSI`, `HID`, `BULK`, `LY`, `LED`. Platforms: `linux`, `win32`,
`darwin`, `bsd`. A missing wire raises; a missing platform falls back to Linux
with a warning.

**Adding a cooler is a row in `core/registry.py`** — pure data. The App picks
the right `Device` subclass from the row's `wire` field, so nothing else
changes.

## Display pipeline (`services/display.py`)

`DisplayService` orchestrates rendering: composite background and mask, draw
overlay elements, apply brightness and split, then hand the frame to the device.
`RenderPipeline` in the same module owns the pure rendering half.

Geometry is per-device and rotation-aware: `output_resolution`,
`canvas_resolution`, and the theme/mask directories all derive from the device's
profile and its orientation, so a portrait panel and a landscape one read the
same code.

The preview the GUI shows is captured **before** the device-mount rotation, so
what you see upright is what the panel displays upright.

## Unified UI — the presentation layer

There is **one app** for every device. Whether you plug in an LCD cooler or an
LED segment display, whether it speaks SCSI, HID, Bulk, LY, or LED — the same
window, the same command bus, the same settings. The piece that makes that work
without a pile of `if device.is_led` branches scattered across the GUI is the
**presentation layer** (`ui/presentation/`).

### One device → one `DevicePresentation`

`ui/presentation/device_presentation.py::presentation_for(info)` maps a device's
resolved `ProductInfo` to a toolkit-free contract: its `kind` (LED vs LCD), the
`view_name` the UI should show (`"led"` segment panel vs `"form"` theme/preview),
and whether it has metric gauges to populate. Every graphical front-end asks this
one function "what does this device present?" instead of each re-deriving it.

### Qt-free Presentation Models

The decisions a device view coordinates — geometry, rotation, split policy,
preview sizing, sensor/metric formatting, LED panel/selector composition — live
in `ui/presentation/` as **pure Python with zero Qt, zero `App` handle, zero
widgets**:

| Model | Owns |
|---|---|
| `LcdPresentationModel` | per-device display state, canvas/rotation geometry, split mode, video math |
| `lcd_panel` / `led_panel` / `led_display` | preview composition + which panel sections/selectors a device shows |
| `sensor_display` / `led_metrics_format` | value→string formatting shared by every sensor UI |
| `overlay_model` / `overlay_serialization` | overlay element resolution + DC (de)serialisation |

Because they hold no toolkit, they unit-test with plain `pytest` (no
`QApplication`), and a different presentation — the native-skin `ui/qtgui`
rebuild, a future TUI or web UI — can bind the *same* logic. Both `ui/gui`
(PySide6 legacy skin) and `ui/qtgui` are thin View layers that read these models
and poke their own widgets; neither owns the decisions.

### The boundary gate

`tests/test_architecture_boundaries.py` machine-enforces the purity: any import
of Qt, an adapter, or the `App` under `ui/presentation` fails the build. That's
what keeps the layer a genuine seam rather than drifting back into a Qt tangle.

### Adding a device is a row, not a rewrite

Because the View is toolkit-agnostic and driven entirely by the
handshake-resolved `ProductInfo` + these models, onboarding a new panel is a
data change (a registry row + its geometry/PM/SUB), not new UI branching.

## Settings

`app.settings` holds app-wide and per-device state — resolution, orientation,
language, temperature unit, format preferences, current theme and mask —
persisted to `trcc.json`. Widgets read it through Commands and never keep their
own copy.

## Verification

```bash
pytest                       # the suite; pyproject sets testpaths, pythonpath, -n auto
ruff check .                 # lint
pyright                      # types — 0 errors required
python -m trcc gui           # the real app
python dev/mock.py --ui gui  # any UI against a simulated device, no hardware
```

Ratchets that fail the build rather than warn: the silent-function count, the
UI reach counts (how often a UI reaches around the bus), and the generated
documents below.

## Conventions

- **Dependencies point inward.** `core/` imports nothing above it. A UI reaching
  into `services/` or `adapters/` is a bug with a test that names it.
- **Inject collaborators**, do not import them at the point of use. That is what
  makes the mock platform possible.
- **Every function gets a log line.** Diagnosis happens through `trcc report`,
  on hardware nobody here owns; a silent function is an unanswerable bug report.
  Per-frame work logs through `core.logs.per_frame` so it costs nothing by
  default.
- **Type hints on public APIs**, `pathlib` over `os.path`, `dataclass` over
  dict, `Enum` over strings.
- **Generated docs are generated.** Do not hand-edit `REFERENCE_CLI.md`,
  `REFERENCE_PORTS.md`, `REFERENCE_COMMANDS.md` or the man pages — the
  pre-commit hook rebuilds them and a test fails if the committed copy is stale.

## Reference

- [`doc/REFERENCE_COMMANDS.md`](REFERENCE_COMMANDS.md) — every Command and Query: the contract a UI speaks.
- [`doc/REFERENCE_PORTS.md`](REFERENCE_PORTS.md) — every ABC: what to implement to add a device, OS or renderer.
- [`doc/REFERENCE_CLI.md`](REFERENCE_CLI.md) — every CLI command.
- [`METHOD_UI.md`](../METHOD_UI.md) — the rules that keep the four UIs equivalent.
- [`doc/HISTORY_ARCHITECTURE.md`](HISTORY_ARCHITECTURE.md) — why each layer exists.
- [`doc/CHANGELOG.md`](CHANGELOG.md) — user-facing changes per release.

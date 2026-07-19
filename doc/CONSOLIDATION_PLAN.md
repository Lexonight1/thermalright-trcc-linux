# Consolidation Plan — audited C# → clean hexagonal Python

**Status:** PLAN ONLY. No code written. Every increment gets its own written
plan + explicit approval before any code. Prerequisite (the full C# audit) is
DONE — see `dev/decompiler/AUDIT_INDEX.md` + the 13 `BEHAVIOR_*.md` + memory
`project_cs_full_audit`.

## The target (what "done" looks like)

- **Hardware = DI'd objects behind ports.** `Platform` (OS), `Device` (panel),
  `SensorSource` (GPU/CPU/fans/mem), `UsbTransport` (wire), `Renderer`. Core
  imports only the ports. OS *mediates*, Device is the *sink*, GPU/sensors are
  *sources* — architecturally identical: hardware behind a port, injected.
- **Software = one generic path.** `resolve_spec → inject collaborators → run the
  pipeline` (read sensors → compose frame → send). No hardware-specifics in the
  path. The composition root does the per-device wiring once, at connect; the app
  runs generic. New device = a data row + a registration; app code never changes.
- **Variety = data.** Every per-resolution/product/OS difference is a value on the
  `DeviceSpec` / a table row — never a copied method or a per-resolution subclass.
- **Reuse the methods we have.** `Renderer.rotate/resize/composite/draw_text/
  encode_*`, `Device.send`, the sensor sources. Compose them; write new code ONLY
  where behaviour is missing. The C#'s 251 duplications ([COPY-PASTE]=178,
  [GOD]=73) are the same ~18 methods called with different numbers.
- **Thin UIs.** gui/qtgui/cli/api are skins over one `dispatch(Command)`.

## Current state (honest — structure exists, interior unconsolidated)

| | Have (verified) | Missing |
|---|---|---|
| Hexagonal layers + boundary gate | ✅ (20 tests, core imports no adapter) | — |
| DI'd hardware ports (Platform/Device/Sensor/Transport/Renderer) | ✅ | — |
| Command bus (`app.dispatch`) | ✅ | UIs not fully thinned onto it |
| Reusable `Renderer` methods (rotate/resize/composite/encode/draw_text) | ✅ | not yet spec-driven in the render path |
| `DeviceSpec` | ~40% (`DeviceProfile`) | header/fit/threshold/edge/byte-order/theme-dir scattered |
| One pipeline LCD+LED | ❌ | parallel subsystems |
| Correctness G1–G5 (incl. Mjolnir G2) | ❌ | — |

Estimate: **~20–30% of the interior consolidation done** (structure + a few
consolidated pieces like `wire_angle`, `variants.py`, the rainbow table).

## Professional design

### Value types (the variety, as data)
```python
class Encoder(Enum):   JPEG = auto();  RGB565 = auto()
class EdgeFill(Enum):  NONE = auto();  BLACK_RING = auto();  REPLICATE = auto()
class BgFit(Enum):     NATIVE_OR_BLACK = auto()

@dataclass(frozen=True, slots=True)
class DeviceSpec:
    resolution:     tuple[int, int]
    encoder:        Encoder
    byte_order:     Literal["<", ">"]
    wire_base:      int                  # rotation base at angle 0
    bg_fit:         BgFit
    crop_threshold: float                # the per-res magic double, verbatim
    edge_fill:      EdgeFill
    header:         HeaderTemplate       # (magic: bytes, dim_offset, len_offset)
    theme_dir:      str
    led:            LedSpec | None = None

def resolve_spec(fp: Fingerprint) -> DeviceSpec: ...   # pm/sub → fbl/mode → spec; ONLY reader of tables
```

### `Renderer` — base class, Template Method, reusable methods
```python
class Renderer(ABC):
    def build_frame(self, spec, content, angle) -> bytes:      # Template Method (fixed skeleton)
        canvas = self.compose_canvas(spec, angle)
        canvas = self.bg_fit(content.bg, canvas, spec)         # G2 lives here
        self.draw_overlay(canvas, content.overlay)             # UPRIGHT (audit caveat 5)
        wire   = self.rotate(canvas, self.wire_angle(spec, angle), spec.edge_fill)
        return self.frame(spec, self.encode(wire, spec))
    # concrete, reused:  bg_fit, wire_angle (= (wire_base - angle) % 360), frame (header+payload)
    # abstract hooks:    compose_canvas, rotate, encode   (rotate/resize/composite/encode_* already exist)
```

### `Device` — base class, DI'd transport+renderer, Template Method handshake
```python
class Device(ABC):
    def __init__(self, info, transport: UsbTransport, renderer: Renderer): ...
    def handshake(self) -> DeviceSpec:                         # Template Method
        self._spec = resolve_spec(fingerprint(self._do_handshake()))
        return self._spec
    def render_and_send(self, content, angle) -> None:        # generic
        self._t.write(self._wrap(self._r.build_frame(self._spec, content, angle)))
    # abstract:  _do_handshake, _wrap    (subclasses ScsiLcd/HidLcd/BulkLcd/LyLcd/Led override ONLY these)
```

### DI wiring — composition root (the software "completes the path")
```python
transport = platform.make_transport(info)                     # OS makes the seam
device    = DeviceFactory.for_wire(info.wire)(info, transport, renderer)   # inject
spec      = device.handshake()                                # resolve + cache
# thereafter generic:  device.render_and_send(content, angle)
```

### Patterns
Template Method (`build_frame`, `handshake`) · Strategy-injected (`Encoder`,
effect, `Renderer`) · Abstract Factory + `@register` (`PlatformFactory`,
`DeviceFactory`) · Constructor DI · Data-driven/Flyweight (`DeviceSpec` resolved
once, resident).

### Contracts
Full type annotations (`pyright` clean) · no `if resolution ==` at runtime (lint
gate) · core imports no adapter (boundary test) · every base method unit-tested ·
every spec cell oracle-tested (`test_csharp_oracle_parity`).

## Increments (each: additive where possible, oracle+suite+mock gated, app stays working, OWN go-ahead)

**1 — DeviceSpec + tables (data foundation)**
- Builds: `DeviceSpec` + enums + `resolve_spec` + tables.
- Files: `core/protocol.py` (grow `DeviceProfile`, fields defaulted), `core/models.py`
  (tables), `tests/test_csharp_oracle_parity.py` (assert each cell == C#).
- Verify: oracle green · suite green · ruff+pyright clean · **zero behaviour change** (no consumer touched).
- Risk: none.

**2 — Render Template Method (delivers G1/G2/G5)**
- Builds: `Renderer.build_frame` + `bg_fit`/`wire_angle`/`frame`; `Device.render_and_send`
  calls it; `handshake` caches the spec.
- Files: `core/ports.py`, `adapters/render/qt.py` (`bg_fit`), `services/display.py`,
  `core/geometry.py` (per-res branches → spec reads).
- Verify: oracle green · geometry/rotation suites green · **`dev/mock.py` Mjolnir (pm=5)
  0/90/180/270 fills + text upright** · then **on-glass on the user's device**.
- Risk: medium (behaviour change) — mock + glass gated, no ship until confirmed.

**3 — LED onto the pipeline**
- Files: `core/led_models.py`, `services/led_*`. Verify: LED tests + `dev/mock.py --device 0416:8001`.

**4 — G3/G4 as spec rows**
- Files: `core/models.py` + encode/rotate primitives. **G3 (fbl 51/53 byte order) needs a real
  device confirm before flipping — do NOT flip blind** (Frozen Warframe fbl 51 is a working device).

**5 — thin the UIs onto the bus**
- Files: `ui/qtgui/*`, `ui/presentation/*`. Verify: qtgui/gui parity, both drive `dev/mock.py`.
  Last + most careful (UI churn).

## Do NOT

- No rewrite — evolve the existing hexagon; keep every working, tested thing.
- No new files beyond those named per increment, and only on approval.
- No bundling — G2 lands in increment 2, never smuggled into 1.
- Don't port the C# bugs the audit flagged: the "dead-toggle" (buttonOnOff only turns ON),
  the `textBoxW/H` swap in the square-panel aspect branch. Implement correctly.

## Resume here
Increment 1 (the spec) is fully specced above and low-risk. Next session: confirm the plan,
get the go on increment 1, implement ONLY those three files, then plan increment 2.

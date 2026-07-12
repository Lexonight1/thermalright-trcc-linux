# TRCC Linux — UI Methods of Operation

The companion to `METHOD.md`. That one is about matching the **device's**
return; this one is about keeping the **four UIs** — CLI, API, GUI, qtgui — a
single application wearing four faces, never four applications that happen to
share a repo.

## The premise

There is **one** app. The GUI, the CLI, the REST API, and the qtgui skin are
*views* onto it. A user who does something in one UI must get the identical
result they'd get doing it in any other, because underneath they are the same
behaviour, the same state, and the same lifecycle. A capability that works in
one UI and not another is a **bug**, not a missing feature — the plumbing to do
it everywhere already exists (see #150: the GUI could keep a volatile panel lit
from persisted state; the CLI couldn't, because its entry skipped the restore
the GUI did on connect).

## The three backbones

Everything a UI does resolves to one of three shared spines. A UI owns none of
them; it only *invokes* them.

1. **Behaviour → one Command.** Every user action is a single `Command`
   dispatched on the one bus: `app.dispatch(SomeCommand(...))`. The UI
   translates its native input (a click, an argv, a JSON body) into that
   Command and renders the `Result`. It holds **zero** domain logic. Same
   action → same Command in all four UIs.
2. **State → one `Settings`, persisted to `trcc.json`.** All mutable app +
   per-device state (resolution, orientation, current theme, background,
   format prefs) lives in `services/settings.py` → `Settings`, persisted to
   `paths.config_dir()/trcc.json` (`_CONFIG_FILE`). Every UI reads and writes
   `app.settings`; **no UI keeps its own copy.** This is why a theme picked in
   the GUI is already selected when you open the CLI — the state was never in
   the GUI, it was in `trcc.json`.
3. **Lifecycle → one entry sequence.** From launch to a displaying device the
   steps are identical everywhere: **detect → ensure data → connect → restore
   → stream** (below). A UI decides *when* to run a step (GUI on window-ready,
   CLI on command, API on request), never *what* the step does.

## The UI is a thin translator — the shape of every adapter method

    parse native input  →  build Command(s)  →  app.dispatch(...)  →  render Result

If an adapter method does more than this — resolves a resolution, walks the
filesystem, decides a fallback, mutates state directly — that logic belongs in a
core Command or `Settings`, and the drift starts here. The GUI translating a
click-index into an element id is fine (that's input translation); the GUI
deciding *which theme to auto-load* is not (that's a lifecycle decision — it
belongs in the shared step, see `RestoreDeviceState`).

## The entry contracts — the #150 lesson, written down

These are the invariants every UI must honour at its entry points. They are the
exact things that drift silently, because each UI reaches them by a different
path:

- **Before any wire command → `ensure_connected` (idempotent).** A stateless
  process (a CLI invocation, an API request) holds no attached device; a wire
  command dispatched cold fails "not attached". Every CLI wire command and API
  wire route attaches first. (#150: `theme cloud-load` / `display load-image`
  had skipped it.)
- **At any display-start → `RestoreDeviceState` (idempotent).** Beginning to
  *stream* (GUI connect, CLI `display play` / `keepalive`, API `restore-theme`)
  must rehydrate the device's persisted display state — the theme, or the first
  available theme, then the persisted background — because a fresh process's
  in-memory `active_themes` is empty. One shared Command does it for all four.
  (#150: the GUI did this on connect; the CLI streaming loops did not.)
- **Per-tick handlers log at DEBUG; one-shot actions at INFO.** (See CLAUDE.md
  logging section — the same rule binds every UI.)

The test of a good entry contract: you can state it as "at *entry X*, dispatch
*Command Y*," and it reads the same for all four UIs.

## The first-run / lifecycle sequence

The whole path, launch → glass, as shared Commands:

| Step | Command(s) | What it does |
|---|---|---|
| detect | `DiscoverDevices` | scan the bus, resolve `DeviceInfo` |
| ensure data | `EnsureDataDownload(w, h)` | download + extract the themes/masks for this canvas |
| connect | `ConnectDevice` / `EnsureConnected` | attach + handshake |
| restore | `RestoreDeviceState` | persisted theme → else first theme → replay background |
| stream | `RenderAndSend` / `KeepAliveLoop` | render live metrics + keep a volatile panel lit |

**First-run parity:** a first-run user on *any* UI should get autodetect +
download + a displayed first theme — the GUI does this on launch; the CLI, API,
and qtgui must reach the same sequence, not a bespoke onboarding each.

## Where the current UIs diverge (honest current state)

The backbones exist; the *lifecycle* is not yet unified — first-run is
reimplemented per surface, and that is the standing debt this doc names:

- **CLI:** `RunQuickstart` + implicit `EnsureDataDownload` via `DiscoverDevices`
  + `system.py`.
- **API:** an explicit `POST /theme/init` (`EnsureDataDownload`) + `/devices`
  scan — no single onboarding call.
- **GUI:** `splash.py` + `trcc_app` download orchestration on launch.
- **qtgui:** `device_picker` + `device_panel` — least verified.

The target is a single shared "ensure-ready" path (detect → ensure data →
connect → restore) each UI invokes, the way `RestoreDeviceState` unified the
restore step. Until then, adding onboarding to one UI without the others is the
drift to resist.

## The parity gate

`tests/test_ui_parity.py` pins the CLI ↔ API Command surface: the two complete
programmatic UIs must dispatch the same Commands, and every intentional
asymmetry lives in an annotated ledger with a reason. A new Command wired into
one surface but not the other **fails the test** — which forces the author to
either reach parity or record why not. That test is the enforceable half of this
doc; the prose is the why.

## Anti-patterns — the ways UI unity breaks

- **Business logic in an adapter.** A resolution resolved, a fallback decided, a
  theme chosen inside `ui/*` — it belongs in a Command or `Settings`. This is
  where every divergence is born.
- **A UI shadowing `Settings`.** A widget/handler caching its own copy of state
  the config owns → the UIs disagree the moment one writes and the other
  doesn't re-read.
- **A capability wired in one UI only.** "The GUI can, the CLI can't" is a bug.
  Add the Command once; expose it as a thin dispatch in every surface.
- **A bespoke lifecycle per UI.** Reimplementing detect/download/restore instead
  of invoking the shared step — the exact class of drift #150 came from.
- **Reaching for a new Command when one exists.** Parity means *reuse* the
  existing Command across UIs, not add a near-duplicate for one of them.

## The honesty clause

Same as `METHOD.md`: status comes from artifacts. "Unified" is true only when
the parity gate is green *and* the behaviour is one Command *and* the state is in
`trcc.json`. Say which UIs are verified and which are inferred — qtgui parity is
the least verified today. See `CLAUDE.md` → "No progress theater".

# TRCC Linux — Methods of Operation

The standing playbook. Every device and every feature goes through the same
loop, so the work is repeatable engineering, not fresh detective work each time.

## The premise

We reproduce the working Windows C# app's **behavior and returns** in a clean,
Linux-first, hexagonal codebase.

- The **C# decompile is the oracle** — the reference for *what* the hardware
  expects and returns: the handshake bytes, the resolution a PM byte maps to,
  the wire rotation, the encode format.
- Our **architecture is the form** — hexagonal, SOLID, DRY, KISS, one unified
  UI. It is *how* we produce those returns, better than the C# did.

**Match the data; improve the form.** The return is sacred; the code shape is
ours to make right.

## Two non-negotiable disciplines

1. **The C# defines the RETURN, never the CODE SHAPE.** Extract the behaviour
   (bytes, resolution, handshake, rotation) and reimplement it cleanly. Never
   carry the C#'s god-switches, mutable statics, stringly-typed APIs, or its
   inconsistencies (e.g. a `theme240320` folder that actually holds landscape
   content). Match the return; express it as it *should* have been written.
2. **KISS the form to the smallest thing that produces the return.** A data row
   beats a table beats a function beats a subsystem. Reuse before rewrite. Used
   once → inline; used twice → helper; used three times → centralise. When you
   reach for a new function, first ask whether a set or a table already gets the
   same return. (The bulk PM fix was a one-line set change, not the
   `bulk_resolve_fbl` function first proposed.)

## The loop — every device, every feature

1. **Observe the real return.** Get the device's actual bytes — PM / SUB /
   resolution / raw handshake. You cannot match what you cannot see.
2. **Consult the oracle.** Run that same fingerprint through the C# to get the
   authoritative return. The C# is the spec.
3. **Diff.** Ours vs. the C#'s. **A divergence is a bug by definition** — this
   is the step that kills guessing and reporter-gating *of the diagnosis*.
4. **Locate it in the owning layer.** Hexagonal tells you where: is this a core
   *behaviour* (resolution / protocol / geometry) or an adapter *I/O* concern
   (wire, OS, sensors)? Fix at that boundary; nothing else moves.
5. **KISS the fix.** The minimal expression that makes the return match.
6. **Verify against the oracle, not the reporter.** Re-run the whole corpus vs.
   the C#. Green means matched.
7. **Guard it.** A test pins the return so it can't re-drift.
8. **Confirm on glass — last.** Hardware / reporter confirmation is the
   *closing* gate before an issue is closed, never the *diagnostic* gate.

## The four stations — the loop made executable

These are not four unrelated dev tools; they are the four stations of one
assembly line. Keep them working.

| Loop step | Station | Where |
|---|---|---|
| 1 · Observe | Live handshake capture in `trcc report` | `adapters/diagnostics/debug_report.py` |
| 2–3 · Oracle + diff | The C# device oracle (run any fingerprint through `FormCZTVInit`) | `dev/decompiler/formcztv_init.py`, `dev/decompiler/audit_rotation.py` |
| 6 · Verify | Capability-diff audit (ours vs. the C# across the corpus) | `dev/decompiler/audit_devices.py` |
| 8 · Confirm without hardware | Scripted multi-device mock | `tests/mock_platform.py`, `dev/mock_gui.py` |

The C# decompile itself is the source of truth the oracle transcribes — keep the
line citations so any claim is checkable against the exact statement.

## Where does the fix go? (the hexagonal answer)

The fix lands in the layer that **owns the diverging data** — never smeared
across layers.

| The diverging return is about… | Layer | Home |
|---|---|---|
| Resolution / protocol / geometry | **core** | `core/protocol.py`, `core/geometry.py`, `core/models.py` |
| Wire I/O, handshake, transport | **adapter** | `adapters/device/*` |
| OS, sensors, paths | **adapter** | `adapters/system/*`, `adapters/sensors/*` |
| A UI affordance (what the user clicks) | **the View** | `ui/gui`, `ui/cli`, `ui/api` |
| The *behaviour* that affordance triggers | **a core Command** | `core/commands/*` |

Dependencies point inward only: adapters → services → core. Core never imports
an adapter. The C# behaviour lives in core; the OS-native I/O lives in adapters;
the device never knows which OS is talking to it — it just gets the bytes the C#
would have sent.

## The unified-UI rule

Every user action is **one Command**, dispatched identically by the GUI, CLI,
and API. A UI translates its native input into that Command; it never owns
domain logic. Example: Flash is one `FlashOverlayElement` Command — the GUI
translates a click-index into the element id, the CLI and API name the id
directly, and all three dispatch the same Command against the same
`effective_overlay_elements`.

## Anti-patterns — the ways this method breaks

- **Reporter-gating the diagnosis.** The oracle diagnoses; the reporter only
  confirms. If you're "waiting on a reporter" to know *what's wrong*, you
  skipped steps 1–3.
- **Guessing a byte instead of observing it.** If you can't see the device's
  return, fix the diagnostic loop (step 1) first — that is the higher-leverage
  bug.
- **Building a function where a data row matches the C#.** Over-engineering the
  form.
- **Reproducing the C#'s code shape or its mess.** Faithful returns, not
  faithful spaghetti.
- **Grepping to conclude instead of tracing to conclude.** Grep locates; only
  reading the body and tracing the data through every hop concludes.
- **Running `pyright` per-file instead of repo-wide.** The gate is
  `python3 -m pyright` over the whole tree; a per-file pass hides errors that
  cross-file inference surfaces.
- **Pushing or releasing outward-facing without explicit confirmation.** A
  release (PyPI, packages, a GitHub release) is irreversible and the user's call
  to make.

## The honesty clause

Status comes from artifacts, not summaries. A fix is done when its return
matches the oracle *and* a test guards it — not when it "should" work. Say what
is verified, what is inferred, and what is still reporter-gated, in those exact
terms. See `CLAUDE.md` → "No progress theater".

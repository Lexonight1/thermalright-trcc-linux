#!/usr/bin/env python3
"""Command-surface completeness audit — could someone build their own UI?

The "unified UI" promise is that CLI / API / GUI / qtgui all drive the core the
SAME way: build a Command, ``app.dispatch(cmd)``, read the Result — plus a few
read-only port properties (``device.profile``, ``device.handshake``, …).  A
third-party UI (a TUI, a web front-end, a Stream Deck plugin) can do everything
gui does *iff* every capability is reachable through that contract.

This tool finds where a UI reaches **around** the contract — importing
``services`` / ``adapters`` symbols directly instead of dispatching a Command.
Each such import is a candidate **contract hole**: a capability a command-only
UI would be missing (e.g. gui calling ``FileContentStore.discover_masks`` for mask
previews that ``ListMasks`` doesn't carry).

It surfaces candidates; a human judges which are real holes vs. legitimate
infrastructure (a GUI importing ``QtRenderer`` is fine; importing
``FileContentStore`` to compute data a Command should carry is a hole).

**The contract is Commands *and* Queries.**  It read as 102 Commands until
2026-09-02 while the true surface was 135, because the denominator matched only
classes whose literal base was ``Command`` — every one of the 34 ``Query``
subclasses inherits ``Query`` instead and was invisible, and the abstract
``Query`` base was counted as a capability in their place.  The printed result
was self-evidently impossible and shipped anyway: *"api dispatches 117
command(s)"* against a *"102 Command"* contract.  Reads are half of what a UI
does; a contract audit that cannot see them is measuring its own universe.

**One collector, shared with the gate.**  ``reach_by_command`` is imported by
``tests/test_ui_parity.py``, which ratchets the answer.  It used to be written
twice — once here over the AST, once there over the runtime classes — and the
two had already drifted apart by 34 commands, because this copy could not see
past a dispatch helper (``dispatch_echo(SomeCommand())`` in the CLI,
``_dispatch(cmd)`` in both GUIs' LED handlers) or into an ``IfExp``
(``dispatch(Enable() if on else Disable())`` recorded neither branch).

Usage::
    PYTHONPATH=src python3 dev/tools/ui_contract.py
"""
from __future__ import annotations

import argparse
import ast
import tempfile
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src" / "trcc"

# Runnable without PYTHONPATH=src — the contract is read from the live classes,
# not re-derived from the AST, so the import has to resolve.
sys.path.insert(0, str(_REPO / "src"))

_UI_ROOT = _SRC / "ui"

_UIS = {
    "cli": _UI_ROOT / "cli",
    "api": _UI_ROOT / "api",
    "gui": _UI_ROOT / "gui",
    "qtgui": _UI_ROOT / "qtgui",
}

#: The ONE module allowed to name a skin's internals.  It builds the faces,
#: exactly as ``adapters/device/_base.py`` names its device classes in order to
#: register them — infrastructure that knows its consumers is otherwise an
#: inversion.  Measured 2026-09-20: all 9 shared->skin imports in the tree are
#: this file, so it is an invariant the tree already holds, not a wish.
_COMPOSITION_ROOT = "_uis"

#: A floor, not a target — 135 today.  Guards the DENOMINATOR: a collector that
#: silently returns little makes every UI look complete.  See
#: ``project_a_measurement_that_names_its_own_universe``.
_MIN_CONTRACT = 100

# Colours
_G, _Y, _R, _GREY, _B, _RST = (
    "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[1m", "\033[0m",
)


def contract_classes() -> tuple[set[str], set[str]]:
    """``(commands, queries)`` — the dispatchable surface, from the live classes.

    Runtime introspection rather than an AST walk, because inheritance is the
    question being asked and only the interpreter answers it reliably: ``Query``
    subclasses ``Command``, so an AST match on the literal base name misses
    every read.  It also sidesteps a collision the AST cannot see — two classes
    named ``DeviceState`` exist (the Query, and a presentation dataclass).
    """
    import trcc.core.commands as commands
    from trcc.core.commands._base import Command, Query

    concrete = {
        name
        for name in dir(commands)
        if isinstance(obj := getattr(commands, name), type)
        and issubclass(obj, Command)
        and obj not in (Command, Query)   # the bases themselves are not capabilities
    }
    if len(concrete) < _MIN_CONTRACT:
        raise SystemExit(
            f"contract collector returned {len(concrete)} classes, under the "
            f"floor of {_MIN_CONTRACT} — trcc.core.commands exported nothing "
            f"useful.  Every UI would score as complete against it."
        )
    queries = {n for n in concrete if issubclass(getattr(commands, n), Query)}
    return concrete - queries, queries


def reach_by_command(
    uis: dict[str, Path] | None = None,
) -> dict[str, set[str]]:
    """Which UI trees reach each contract class, by AST — never by regex.

    *uis* exists so :func:`gate` can point the collector at a fixture.  Without
    it the two reference kinds below cannot be told apart on the real tree —
    every Command a UI uses by NAME is also imported by ALIAS there, so
    deleting the ``ast.Name`` arm changed no number and a first cut of the gate
    passed with it deleted.

    A reference is an ``ast.Name`` (``dispatch(Foo(...))``) or an ``ast.alias``
    (``from ... import Foo``).  Deliberately broader than "an inline call in a
    dispatch argument": a UI that hands a Command to a helper is still reaching
    the capability, and matching only the inline shape undercounted the CLI by
    34.  Matching the class NAME in UI source *text* would over-count the other
    way — ``SendFrame`` appears in two UI trees and is dispatched by neither,
    only mentioned in comments.
    """
    commands, queries = contract_classes()
    reach: dict[str, set[str]] = {n: set() for n in commands | queries}
    for ui, root in (uis or _UIS).items():
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                seen = None
                if isinstance(node, ast.Name):
                    seen = node.id
                elif isinstance(node, ast.alias):
                    seen = node.name
                if seen in reach:
                    reach[seen].add(ui)
    if not any(reach.values()):
        raise SystemExit(
            f"reach collector found no Command referenced by any of "
            f"{sorted(uis or _UIS)} — the UI roots are wrong or empty."
        )
    return reach


def dispatched_by(ui: str, reach: dict[str, set[str]]) -> set[str]:
    """The contract classes *ui* reaches — one collector, one answer."""
    return {name for name, uis in reach.items() if ui in uis}


@dataclass(slots=True)
class UiSurface:
    """What one UI touches: Commands dispatched vs. layers reached around."""

    name: str
    dispatched: set[str] = field(default_factory=set)
    service_imports: dict[str, str] = field(default_factory=dict)   # symbol → file:line
    adapter_imports: dict[str, str] = field(default_factory=dict)


def scan_ui(name: str, root: Path, dispatched: set[str]) -> UiSurface:
    surface = UiSurface(name=name, dispatched=dispatched)
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        try:
            rel = path.relative_to(_SRC)
        except ValueError:
            # *root* is advertised as a parameter but this was pinned to
            # ``_SRC``, so the function only ever worked for roots inside the
            # tree — which meant it could not be pointed at a fixture, which
            # meant it could not be proven.  ``rel`` is a human-readable
            # location label and nothing more.
            rel = path.relative_to(root.parent)
        for node in ast.walk(tree):
            _record_bypass(node, rel, surface)
    return surface


def _record_bypass(node: ast.AST, rel: Path, surface: UiSurface) -> None:
    """``from ...services.x import Y`` / ``...adapters.x`` → record the symbol."""
    if not isinstance(node, ast.ImportFrom):
        return
    parts = (node.module or "").split(".")
    where = f"{rel}:{node.lineno}"
    if "services" in parts:
        for alias in node.names:
            surface.service_imports.setdefault(alias.name, where)
    if "adapters" in parts:
        for alias in node.names:
            surface.adapter_imports.setdefault(alias.name, where)


# =========================================================================
# The UI->UI axis — a second way to reach around the contract
# =========================================================================
#
# The checks above ask "does a UI reach past the Command bus into services or
# adapters?".  They cannot see a UI reaching SIDEWAYS, into another UI, because
# ``_record_bypass`` matches only ``services`` / ``adapters`` in the module
# path.  Three questions live on that axis, and all three are DERIVED from the
# import graph rather than read off a list — a list of blessed modules is a
# fact expressed twice, and would drift from the tree it describes.
#
#   cross-skin   one skin importing another skin's modules
#   inversion    shared ui/ infrastructure naming a skin, other than the
#                composition root that exists to build them
#   mislocated   a module in a shared location whose production consumers are
#                exactly ONE skin: it claims to be shared and is not
#
# Measured 2026-09-20: 0 cross-skin violations, 0 inversions, and **10 of the
# 14 modules in ui/presentation serve gui alone** — 1416 of its 1756 lines.
# The first two are a floor to hold; the third is the finding.


@dataclass(frozen=True, slots=True)
class CrossEdge:
    """One import crossing between UI areas.  ``src``/``dst`` are areas."""

    src: str
    dst: str
    where: str
    names: tuple[str, ...]

    @property
    def kind(self) -> str | None:
        """``"cross-skin"`` / ``"inversion"``, or None when legitimate."""
        if self.src == "root" or self.dst in ("shared", "root"):
            return None                      # the composition root, or a skin
            #                                  using shared infrastructure
        if self.src == "shared":
            return "inversion"
        return "cross-skin" if self.src != self.dst else None


def _package_anchor(ui_root: Path) -> Path:
    """The directory holding the TOP-level package, the way Python finds it.

    Anchoring at ``ui_root.parent`` instead cost a real finding: on the real
    tree that names ``ui/cli/system.py`` ``ui.cli.system``, and
    ``from ...ui.api.main import build_app`` there walks up three levels from a
    two-segment name, falls off the top and resolves to nothing.  One cross-skin
    reach went silently missing — found only because a second measurement
    disagreed.  Walking up while ``__init__.py`` exists gives ``trcc.ui.cli.system``
    on the real tree and ``faces.alpha.window`` on a fixture, with no special case.
    """
    anchor = ui_root
    while (anchor.parent / "__init__.py").exists():
        anchor = anchor.parent
    return anchor.parent


def _module_name(path: Path, anchor: Path) -> str:
    """Fully-qualified dotted name of *path*, relative to the package anchor."""
    parts = list(path.relative_to(anchor).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_import(node: ast.ImportFrom, this: str, is_pkg: bool) -> str | None:
    """Absolute module for an ``ImportFrom``, relative levels included.

    A relative import contains NEITHER the package name nor a slash, so text
    search cannot see it at all: grep reported 0 importers of ``ui.gui`` where
    the AST reported 4.  Resolving the level is the whole job.
    """
    if node.level == 0:
        return node.module
    base = this.split(".")
    if not is_pkg:
        base = base[:-1]                     # a module: level 1 is its package
    if (up := node.level - 1):
        base = base[:-up] if up <= len(base) else []
    if not base:
        return None
    return ".".join(base + ([node.module] if node.module else []))


def _area(module: str, ui_pkg: str, skins: frozenset[str]) -> str | None:
    """Which UI area *module* lives in — a skin name, ``root``, or ``shared``.

    *ui_pkg* is the ui package's own dotted name (``trcc.ui``).  None when the
    module is outside it entirely (core, services, PySide6, …); those are the
    OTHER axis and ``_record_bypass`` already judges them.
    """
    if not module.startswith(ui_pkg + "."):
        return None
    head = module[len(ui_pkg) + 1:].split(".")[0]
    if head in skins:
        return head
    return "root" if head == _COMPOSITION_ROOT else "shared"


def _ui_files(ui_root: Path) -> list[Path]:
    return [p for p in sorted(ui_root.rglob("*.py"))
            if "__pycache__" not in p.parts]


def cross_edges(ui_root: Path | None = None,
                skins: frozenset[str] | None = None) -> list[CrossEdge]:
    """Every import crossing UI areas.  Fixture-pointable, so it can be proven.

    *ui_root* is a parameter rather than a constant for the reason ``scan_ui``
    was fixed: a collector pinned to the real tree cannot be aimed at a fixture,
    and one that cannot be aimed at a fixture cannot be shown to work.
    """
    ui_root = ui_root or _UI_ROOT
    skins = skins if skins is not None else frozenset(_UIS)
    anchor = _package_anchor(ui_root)
    ui_pkg = _module_name(ui_root, anchor)
    edges: list[CrossEdge] = []
    for path in _ui_files(ui_root):
        this = _module_name(path, anchor)
        src = _area(this, ui_pkg, skins)
        if src is None:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = _resolve_import(node, this, path.name == "__init__.py")
            dst = _area(target, ui_pkg, skins) if target else None
            if dst is None or dst == src:
                continue
            edges.append(CrossEdge(
                src, dst, f"{path.relative_to(anchor)}:{node.lineno}",
                tuple(a.name for a in node.names)))
    return edges


def mislocated(ui_root: Path | None = None,
               skins: frozenset[str] | None = None) -> list[tuple[str, int, str]]:
    """Shared-location modules serving ONE skin — ``(module, lines, skin)``.

    "Shared location" is derived, not listed: anything under ``ui/`` that is not
    inside a skin package.  A new ``ui/widgets/`` is covered the day it lands.

    Package re-exports are followed.  ``presentation/__init__.py`` does
    ``from .device_presentation import presentation_for``, so a consumer writing
    ``from ..presentation import presentation_for`` credits the PACKAGE and the
    module reads as having no consumer at all.  Measured without this, two
    modules reported zero consumers when both have one — the false-zero shape
    this file's own docstring warns about, one level down.
    """
    ui_root = ui_root or _UI_ROOT
    skins = skins if skins is not None else frozenset(_UIS)
    anchor = _package_anchor(ui_root)
    ui_pkg = _module_name(ui_root, anchor)
    files = _ui_files(ui_root)
    trees = {p: ast.parse(p.read_text(encoding="utf-8")) for p in files}

    # name -> defining module, for every `from .sub import Name` in a package
    # __init__.  Only level-1: a deeper relative import is not a re-export.
    reexport: dict[tuple[str, str], str] = {}
    for path, tree in trees.items():
        if path.name != "__init__.py":
            continue
        pkg = _module_name(path, anchor)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                for alias in node.names:
                    reexport[(pkg, alias.name)] = f"{pkg}.{node.module}"

    consumers: dict[str, set[str]] = {}
    for path, tree in trees.items():
        this = _module_name(path, anchor)
        src = _area(this, ui_pkg, skins)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = _resolve_import(node, this, path.name == "__init__.py")
            if not target or _area(target, ui_pkg, skins) is None:
                continue
            for alias in node.names:
                real = reexport.get((target, alias.name), target)
                if real != this and src in skins:
                    consumers.setdefault(real, set()).add(src)

    out: list[tuple[str, int, str]] = []
    for path in files:
        rel = path.relative_to(ui_root)
        if rel.parts[0] in skins or path.name == "__init__.py":
            continue                          # a skin's own code, or a package door
        module = _module_name(path, anchor)
        if len(found := consumers.get(module, set())) == 1:
            lines = len(path.read_text(encoding="utf-8").splitlines())
            out.append((module, lines, next(iter(found))))
    return sorted(out, key=lambda row: -row[1])


# =========================================================================
# The OBSERVE half — the other driving port
# =========================================================================
#
# Everything above measures ``dispatch(cmd) -> Result``: 144 Commands and
# Queries, request/response.  That is HALF the driving surface.  The other half
# is the EventBus -- 44 Event types, push -- and it is equally universal: it
# crosses the daemon boundary too (``AppProxy.events`` opens a stream and
# reconstructs real typed Events, so ``BusBridge`` works untouched).
#
# Nothing counted it.  A UI that dispatches every Command and observes nothing
# scored 100% here, and the two Qt skins diverge hardest on exactly this axis:
# measured 2026-09-20, ``ui/gui`` touches the bus in ONE file and ``ui/qtgui``
# in twenty.  Every parity number this tool has ever printed was half a
# measurement -- ``project_a_measurement_that_names_its_own_universe`` again,
# one port over.
#
# A UI reaches an Event TWO ways, and both must be followed or the answer is
# wrong for one skin:
#
#   * by NAME  -- ``events.subscribe(FrameSent, ...)``, or a handler typed
#     ``def _on(self, e: FrameSent)``.  qtgui does this.
#   * by SIGNAL -- ``self._bus.frame_sent.connect(...)``, which reaches
#     ``FrameSent`` without the identifier appearing anywhere.  gui does this,
#     so a name-only collector reports gui as observing almost nothing.
#
# The signal->Event map is not a list here.  It is READ from ``BusBridge._wire``,
# which pairs them in one literal tuple -- the same reason the rest of this file
# derives rather than lists.

_BUS_BRIDGE = _UI_ROOT / "bus_bridge.py"


def event_types() -> set[str]:
    """The observe contract, from the live classes — the ``Event`` subclasses."""
    from trcc.core.events import Event

    import trcc.core.events as events
    found = {
        name
        for name in dir(events)
        if isinstance(obj := getattr(events, name), type)
        and issubclass(obj, Event)
        and obj is not Event
    }
    if not found:
        raise SystemExit(
            "event collector returned nothing — trcc.core.events exported no "
            "Event subclass.  Every UI would score as observing everything."
        )
    return found


def signal_to_event(bridge: Path | None = None) -> dict[str, set[str]]:
    """``{qt_signal_name: {EventTypeName, ...}}`` read from ``BusBridge._wire``.

    Parsed from the pairing tuple rather than restated here: a second copy of
    this map would drift from the bridge the day a signal is added, and the
    drift would be silent -- the event would simply stop being counted.

    A SET per signal: ``settings_changed`` carries every settings event.  One
    name per signal kept only the last pair, so eleven bridged events read as
    one and the other ten as missing wires.
    """
    tree = ast.parse((bridge or _BUS_BRIDGE).read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        # (SomeEvent, self.some_signal)
        if (isinstance(node, ast.Tuple) and len(node.elts) == 2
                and isinstance(node.elts[0], ast.Name)
                and isinstance(node.elts[1], ast.Attribute)):
            out.setdefault(node.elts[1].attr, set()).add(node.elts[0].id)
    return out


def event_reach(uis: dict[str, Path] | None = None,
                bridge: Path | None = None) -> dict[str, set[str]]:
    """Which UI trees observe each Event type, by AST — name OR bridge signal."""
    known = event_types()
    by_signal = signal_to_event(bridge)
    reach: dict[str, set[str]] = {name: set() for name in known}
    for ui, root in (uis or _UIS).items():
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                seen: set[str] = set()
                if isinstance(node, ast.Name) and node.id in known:
                    seen = {node.id}
                elif isinstance(node, ast.alias) and node.name in known:
                    seen = {node.name}
                elif isinstance(node, ast.Attribute) and node.attr in by_signal:
                    # ``self._bus.frame_sent`` -> FrameSent
                    seen = by_signal[node.attr]
                for name in seen & reach.keys():
                    reach[name].add(ui)
    return reach


def unheard_split(
    reach: dict[str, set[str]], bridge: Path | None = None,
) -> tuple[list[str], list[str]]:
    """``(unbridged, declined)`` — two very different silences.

    An **unbridged** Event cannot reach a Qt skin at all: ``BusBridge`` never
    forwards it, so no widget could observe it however much it wanted to.  A
    **declined** one IS offered on a signal and nobody connects.  Lumping them
    together hides the useful half — the first is a missing wire, the second is
    a choice.

    A function rather than four lines inside the printer, so the split is a
    claim a gate can break instead of presentation nobody checks.
    """
    bridged = set().union(*signal_to_event(bridge).values())
    unheard = {name for name, uis in reach.items() if not uis}
    return sorted(unheard - bridged), sorted(unheard & bridged)


def _print_observe(reach: dict[str, set[str]]) -> None:
    total = len(reach)
    print(f"{_B}Observe half{_RST}  "
          f"{_GREY}({total} Event type(s) — the other driving port){_RST}")
    for ui in _UIS:
        seen = sum(1 for uis in reach.values() if ui in uis)
        tone = _G if seen > total // 2 else (_Y if seen else _R)
        print(f"  {ui:<8} observes {tone}{seen:>3}{_RST} of {total}")
    # Two very different silences, and lumping them together hides the useful
    # one.  An UNBRIDGED event cannot reach a Qt skin at all -- BusBridge never
    # forwards it, so no widget could observe it however much it wanted to.  A
    # bridged-but-unobserved event is a capability that IS offered and declined.
    unbridged, declined = unheard_split(reach)
    if unbridged:
        print(f"  {_R}{len(unbridged)}{_RST} never reach a Qt skin — "
              f"{_GREY}BusBridge does not forward them: "
              f"{', '.join(unbridged[:5])}"
              f"{'…' if len(unbridged) > 5 else ''}{_RST}")
    if declined:
        print(f"  {_Y}{len(declined)}{_RST} are bridged but nobody connects — "
              f"{_GREY}{', '.join(declined[:5])}"
              f"{'…' if len(declined) > 5 else ''}{_RST}")
    print()


def _print_cross(edges: list[CrossEdge], stranded: list[tuple[str, int, str]]) -> None:
    flagged = [e for e in edges if e.kind]
    print(f"{_B}UI -> UI reaches{_RST}  "
          f"{_GREY}({len(edges)} cross-area import(s) total){_RST}")
    if not flagged:
        print(f"  {_G}none — no skin names another skin, and only the "
              f"composition root names a skin at all{_RST}")
    for edge in flagged:
        print(f"  {_R}{edge.kind}{_RST}  {edge.src} -> {edge.dst}  "
              f"{', '.join(edge.names)}  {_GREY}{edge.where}{_RST}")

    print(f"\n{_B}Shared in name only{_RST}  "
          f"{_GREY}(a module outside every skin, used by exactly one){_RST}")
    if not stranded:
        print(f"  {_G}none — every shared module has more than one skin{_RST}")
        return
    for module, lines, skin in stranded:
        print(f"  {_Y}{skin:<6}{_RST} {lines:>5} lines  {module}")
    print(f"  {_GREY}{len(stranded)} module(s), "
          f"{sum(n for _m, n, _s in stranded)} lines{_RST}")


def _print_ui(surface: UiSurface) -> None:
    svc, adp = surface.service_imports, surface.adapter_imports
    holes = len(svc) + len(adp)
    tone = _G if holes == 0 else (_Y if holes <= 4 else _R)
    print(f"{_B}{surface.name}{_RST}  "
          f"reaches {len(surface.dispatched)} of the contract  ·  "
          f"{tone}{holes} contract bypass(es){_RST}")
    for symbol, where in sorted(svc.items()):
        print(f"    {_R}services{_RST}  {symbol:<28} {_GREY}{where}{_RST}")
    for symbol, where in sorted(adp.items()):
        print(f"    {_Y}adapters{_RST}  {symbol:<28} {_GREY}{where}{_RST}")
    print()


# =========================================================================
# --gate — prove the tool before trusting its number
# =========================================================================

#: A UI package, in the shape the real ones have: several modules, and imports
#: written RELATIVELY.  A single-file fixture cannot fail on a path- or
#: level-resolution bug, which is the class of bug that has shipped here
#: before — ``dup_bodies`` reported zero for every multi-module family while
#: its single-file gate stayed green.
_FIXTURE_PANEL = '''
from ...core.commands import SendColor          # the contract — NOT a bypass
from ...services.display import DisplayService  # a bypass
from ...adapters.render.qt import QtRenderer    # a bypass


def act(app):
    return app.dispatch(SendColor(key="x", r=1, g=2, b=3))
'''

_FIXTURE_CLEAN = '''
from ...core.commands import ListDevices


def read(app):
    return app.dispatch(ListDevices())
'''

#: A whole ``ui/`` in miniature for the UI->UI axis: two skins, a composition
#: root, shared infrastructure, and a shared package with one stranded module.
#: Every import is RELATIVE, because resolving the level IS the job — a fixture
#: written with absolute imports cannot fail on the bug this collector exists
#: to avoid.
_FIXTURE_FACES: dict[str, str] = {
    "__init__.py": "",
    # the composition root: names a skin's internals, and is allowed to
    "_uis.py": "from .alpha.window import Window\n",
    # shared infrastructure that names a skin — an INVERSION
    "infra.py": "from .beta.panel import Panel\n\n\ndef thing():\n    return 1\n",
    "alpha/__init__.py": "",
    "alpha/window.py": (
        "from ..beta.panel import Panel\n"        # CROSS-SKIN
        # The same reach written as a DEEP relative import, the shape
        # ``ui/cli/system.py`` uses: up past the ui package and back down
        # through it by name.  Anchored one level too low this resolves to
        # nothing and the reach vanishes silently — which it did.
        "from ...faces.beta.panel import DeepMarker\n"
        "from ..infra import thing\n"             # fine: shared infrastructure
        "from ..shared.only_alpha import Lonely\n"
        "from ..shared.both import Shared\n"      # direct
        "from ..shared.solo import helper\n"      # a subpackage DOOR
        "from ..shared.solo.impl import VALUE\n"
        "\n\nclass Window:\n    pass\n"
    ),
    "beta/__init__.py": "",
    "beta/panel.py": (
        "from ..infra import thing\n"
        "from ..shared import both_name\n"        # through the package DOOR
        "\n\nDeepMarker = 1\n\n\nclass Panel:\n    pass\n"
    ),
    "shared/__init__.py": "from .both import both_name\n",
    "shared/only_alpha.py": "class Lonely:\n    pass\n",
    "shared/both.py": "class Shared:\n    pass\n\n\nboth_name = Shared\n",
    # A shared SUBPACKAGE that serves one skin.  Its ``__init__`` defines
    # ``helper`` itself, so nothing re-exports it and the door collects a real
    # consumer — which is what makes the "a door is never itself stranded"
    # check able to fail.  Without this the check was vacuous: module names
    # have ``__init__`` stripped, so the string it looked for could not occur.
    "shared/solo/__init__.py": "def helper():\n    return 2\n",
    "shared/solo/impl.py": "VALUE = 3\n",
}


#: For the OBSERVE half.  Real Event names, because ``event_types()`` reads the
#: live classes on purpose — but a fixture BRIDGE and fixture UIs, so the two
#: reach paths can be told apart.  On the real tree they cannot: a name-only
#: collector scores gui 3 and a signal-only collector scores qtgui low, and
#: either wrong answer looks plausible.
_FIXTURE_BRIDGE = '''
class BusBridge:
    def _wire(self):
        pairs = (
            (FrameSent, self.frame_sent),
            (ThemeLoaded, self.theme_loaded),
            (ErrorOccurred, self.error_occurred),
            (MaskApplied, self.settings_changed),
            (OverlayChanged, self.settings_changed),
        )
'''

#: Names ``FrameSent`` with **no import** — so the ``ast.Name`` arm is the only
#: thing that can find it.  Importing it here instead would let the ``alias``
#: arm cover for a deleted ``Name`` arm and the gate would pass with half the
#: collector gone; ``reach_by_command`` records that exact hole one port over.
_FIXTURE_BY_NAME = '''
def on(event: FrameSent):
    return event
'''

#: Reached by IMPORT alone — the other half, isolated the same way.
_FIXTURE_BY_ALIAS = '''
from trcc.core.events import SensorsUpdated
'''

#: Never names an Event at all — the gui shape.  Reaches ``ThemeLoaded`` ONLY
#: through the bridge signal, which is invisible to a name-based collector.
_FIXTURE_BY_SIGNAL = '''
def wire(bridge):
    bridge.theme_loaded.connect(print)
    bridge.settings_changed.connect(print)
'''


def _write_faces(tmp: Path) -> Path:
    """Write the fixture and return its ui root.

    Nested one package deep on purpose: a deep relative import needs somewhere
    to walk UP to, so a flat fixture cannot express the ``from ...ui.api.main``
    shape at all — and that is the shape whose resolution broke.
    """
    root = tmp / "pkgroot" / "faces"
    (tmp / "pkgroot").mkdir(parents=True, exist_ok=True)
    (tmp / "pkgroot" / "__init__.py").write_text("", encoding="utf-8")
    for rel, text in _FIXTURE_FACES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def gate() -> int:
    """Re-prove the tool against known answers.  Offline except the import."""
    checks: list[tuple[str, bool]] = []

    # --- the AST half: fixture-able -------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "skin"
        root.mkdir()
        (root / "panel.py").write_text(_FIXTURE_PANEL, encoding="utf-8")
        (root / "clean.py").write_text(_FIXTURE_CLEAN, encoding="utf-8")
        surface = scan_ui("fixture", root, set())

    checks.append(("a services import IS a bypass",
                   "DisplayService" in surface.service_imports))
    checks.append(("an adapters import IS a bypass",
                   "QtRenderer" in surface.adapter_imports))
    checks.append(("a core.commands import is NOT a bypass",
                   "SendColor" not in surface.service_imports
                   and "SendColor" not in surface.adapter_imports))
    checks.append(("a RELATIVE import is still resolved",
                   surface.service_imports.get("DisplayService", "")
                   .startswith("skin/panel.py")
                   or "panel.py" in surface.service_imports.get(
                       "DisplayService", "")))
    checks.append(("a second module in the package is scanned",
                   "ListDevices" not in surface.service_imports))

    # --- the runtime half: cannot use a fixture --------------------------
    # ``contract_classes`` imports the LIVE classes on purpose (``Query``
    # subclasses ``Command``, and two classes are named ``DeviceState``), so
    # it is gated against invariants of the real tree instead.
    commands, queries = contract_classes()
    checks.append(("commands and queries do not overlap",
                   not (commands & queries)))
    checks.append(("the contract clears its own floor",
                   len(commands | queries) >= _MIN_CONTRACT))
    checks.append(("a known Command is classified as one",
                   "SendColor" in commands and "SendColor" not in queries))
    checks.append(("a known Query is classified as one",
                   "ListDevices" in queries and "ListDevices" not in commands))
    checks.append(("the abstract bases are not capabilities",
                   "Command" not in commands | queries
                   and "Query" not in commands | queries))

    # The reason this tool walks the AST instead of matching text: SendFrame
    # is NAMED in two UI trees and dispatched by neither.  A text match would
    # report it as reached by both.
    reach = reach_by_command()
    checks.append(("a Command only MENTIONED in comments reads as unreached",
                   reach.get("SendFrame") == set()))
    checks.append(("a Command genuinely dispatched reads as reached",
                   reach.get("SendColor") == {"cli", "api"}))

    # The two reference kinds, isolated.  On the real tree every Command used
    # by NAME is also imported by ALIAS, so deleting either arm changes no
    # number there — a first cut of this gate passed with ast.Name deleted.
    with tempfile.TemporaryDirectory() as tmp:
        name_only = Path(tmp) / "probe"
        name_only.mkdir()
        (name_only / "m.py").write_text(
            "def act(app):\n    return app.dispatch(SendColor(key='x'))\n",
            encoding="utf-8")
        by_name = reach_by_command({"probe": name_only})

        alias_only = Path(tmp) / "probe2"
        alias_only.mkdir()
        (alias_only / "m.py").write_text(
            "from trcc.core.commands import ListDevices\n", encoding="utf-8")
        by_alias = reach_by_command({"probe2": alias_only})

    checks.append(("a Command used by NAME with no import is reached",
                   by_name.get("SendColor") == {"probe"}))
    checks.append(("a Command reached by IMPORT alone is reached",
                   by_alias.get("ListDevices") == {"probe2"}))

    # --- the UI->UI axis: two skins, a root, and a shared package --------
    skins = frozenset({"alpha", "beta"})
    with tempfile.TemporaryDirectory() as tmp:
        faces = _write_faces(Path(tmp))
        edges = cross_edges(faces, skins)
        stranded = mislocated(faces, skins)

    kinds = {(e.src, e.dst): e.kind for e in edges}
    names = {module for module, _lines, _skin in stranded}
    # ``alias.name``, not ``asname`` — the tool records the symbol REACHED, so a
    # marker that is renamed on import cannot be found by its local name.
    deep = [e for e in edges if "DeepMarker" in e.names]

    checks.append(("a skin importing ANOTHER skin is cross-skin",
                   kinds.get(("alpha", "beta")) == "cross-skin"))
    checks.append(("a DEEP relative reach resolves and is cross-skin",
                   len(deep) == 1 and deep[0].kind == "cross-skin"))
    checks.append(("shared infrastructure naming a skin is an inversion",
                   kinds.get(("shared", "beta")) == "inversion"))
    checks.append(("the composition root naming a skin is NOT flagged",
                   kinds.get(("root", "alpha")) is None))
    checks.append(("a skin using shared infrastructure is NOT flagged",
                   kinds.get(("alpha", "shared")) is None))
    checks.append(("a shared module used by ONE skin is mislocated",
                   "pkgroot.faces.shared.only_alpha" in names))
    # Beta reaches ``both`` ONLY through ``shared/__init__``.  If the re-export
    # were not followed its import would credit the PACKAGE, ``both`` would
    # read as alpha-only, and this module would be falsely reported stranded.
    checks.append(("a package re-export is followed to its defining module",
                   "pkgroot.faces.shared.both" not in names))
    # ``shared.solo``'s door IS imported by exactly one skin, so it would be
    # reported without the skip.  Its modules are reported individually; naming
    # the door too would double-count the same finding.
    checks.append(("a package door is never itself reported stranded",
                   "pkgroot.faces.shared.solo" not in names
                   and "pkgroot.faces.shared.solo.impl" in names))

    # Denominator floor on the real tree: a collector that silently returns
    # nothing would report a perfectly clean UI layer.
    real = cross_edges()
    checks.append(("the real tree yields cross-area edges at all",
                   len(real) > 20))

    # --- the OBSERVE half: both reach paths, told apart ------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bridge = root / "bridge.py"
        bridge.write_text(_FIXTURE_BRIDGE, encoding="utf-8")
        named, signalled = root / "named", root / "signalled"
        aliased = root / "aliased"
        for d in (named, signalled, aliased):
            d.mkdir()
        (named / "m.py").write_text(_FIXTURE_BY_NAME, encoding="utf-8")
        (signalled / "m.py").write_text(_FIXTURE_BY_SIGNAL, encoding="utf-8")
        (aliased / "m.py").write_text(_FIXTURE_BY_ALIAS, encoding="utf-8")
        pairs = signal_to_event(bridge)
        heard = event_reach(
            {"named": named, "signalled": signalled, "aliased": aliased},
            bridge)
        un, dec = unheard_split(heard, bridge)

    checks.append(("the signal map is READ from the bridge, not listed",
                   pairs == {"frame_sent": {"FrameSent"},
                             "theme_loaded": {"ThemeLoaded"},
                             "error_occurred": {"ErrorOccurred"},
                             "settings_changed": {"MaskApplied",
                                                  "OverlayChanged"}}))
    checks.append(("an Event reached by NAME, never imported, is observed",
                   heard.get("FrameSent") == {"named"}))
    checks.append(("an Event reached by IMPORT alone is observed",
                   heard.get("SensorsUpdated") == {"aliased"}))
    # The arm gui depends on.  Deleting it scores gui 3 of 39 instead of 15,
    # and the wrong number is entirely believable.
    checks.append(("an Event reached ONLY by its bridge signal is observed",
                   heard.get("ThemeLoaded") == {"signalled"}))
    # One signal, many events: every event it carries is observed, not the
    # last pair read.
    checks.append(("every Event a shared signal carries is observed",
                   heard.get("MaskApplied") == heard.get("OverlayChanged")
                   == {"signalled"}))
    checks.append(("an Event reached by neither is NOT observed",
                   heard.get("ErrorOccurred") == set()))
    checks.append(("the observe contract clears its own floor",
                   len(event_types()) >= 30))
    # The two silences are different findings; a split that collapses them
    # reports a missing wire as a deliberate choice.
    # BrightnessChanged is not in the fixture bridge at all: no widget could
    # observe it, so it is a missing WIRE.
    checks.append(("an unforwarded Event reads as UNBRIDGED",
                   "BrightnessChanged" in un and "BrightnessChanged" not in dec))
    # ErrorOccurred IS forwarded and no fixture UI connects: a CHOICE, not a
    # missing wire.  Collapsing the two empties this list, which is the whole
    # point of asserting both directions.
    checks.append(("a forwarded Event nobody connects reads as DECLINED",
                   "ErrorOccurred" in dec and "ErrorOccurred" not in un))
    checks.append(("an OBSERVED Event is in neither silence",
                   "FrameSent" not in un + dec))

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    failed = [label for label, ok in checks if not ok]
    if failed:
        print(f"\n{len(failed)} gate check(s) FAILED — do not trust this "
              f"tool's output until they pass.")
        return 1
    print(f"\nAll {len(checks)} gate checks passed.")
    return 0


def main() -> int:
    if "--gate" in sys.argv:
        return gate()

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-bypasses", type=int, default=None, metavar="N",
                    help="exit non-zero if the total contract bypass count "
                         "exceeds N.  A ratchet, like MAX_SILENT: a UI that "
                         "reaches past the Command bus for something new "
                         "fails the build, and fixing one lets you lower N.")
    ap.add_argument("--max-cross", type=int, default=None, metavar="N",
                    help="exit non-zero if more than N UI->UI reaches are "
                         "flagged (a skin naming another skin, or shared "
                         "infrastructure naming a skin).  0 today.")
    args = ap.parse_args()
    commands, queries = contract_classes()
    reach = reach_by_command()
    print(f"{_B}Command-surface completeness audit{_RST}")
    print(f"  contract = {len(commands)} Command(s) + {len(queries)} Query(ies) "
          f"= {len(commands) + len(queries)} + read-only port properties\n")

    surfaces = [scan_ui(name, root, dispatched_by(name, reach))
                for name, root in _UIS.items() if root.is_dir()]
    for surface in surfaces:
        _print_ui(surface)

    # The delta: symbols gui/qtgui reach for that the pure command UIs don't.
    pure = set()
    for s in surfaces:
        if s.name in ("cli", "api"):
            pure |= set(s.service_imports) | set(s.adapter_imports)
    print(f"{_B}Candidate holes — reached by a GUI, not by cli/api{_RST}")
    print(f"{_GREY}(a symbol the graphical UIs pull from services/adapters but the "
          f"command-only UIs never need = a likely gap in the contract){_RST}")
    flagged = False
    for s in surfaces:
        if s.name not in ("gui", "qtgui"):
            continue
        gui_only = (set(s.service_imports) | set(s.adapter_imports)) - pure
        for symbol in sorted(gui_only):
            where = s.service_imports.get(symbol) or s.adapter_imports.get(symbol)
            print(f"  {_R}{s.name}{_RST}  {symbol:<28} {_GREY}{where}{_RST}")
            flagged = True
    if not flagged:
        print(f"  {_G}none — every service/adapter a GUI reaches, cli/api reach too{_RST}")

    _print_observe(event_reach())
    edges = cross_edges()
    stranded = mislocated()
    _print_cross(edges, stranded)

    total = sum(len(s.service_imports) + len(s.adapter_imports)
                for s in surfaces)
    print(f"\n{_B}total contract bypasses:{_RST} {total}")
    flagged = len([e for e in edges if e.kind])
    if args.max_cross is not None and flagged > args.max_cross:
        print(f"{_R}FAIL{_RST} — {flagged} UI->UI reach(es) exceeds the "
              f"--max-cross ceiling of {args.max_cross}.  A skin is reaching "
              f"into another skin, or shared infrastructure has learned who "
              f"its consumers are; both are inversions of the layering.")
        return 1
    if args.max_bypasses is not None and total > args.max_bypasses:
        print(f"{_R}FAIL{_RST} — {total} bypass(es) exceeds the "
              f"--max-bypasses ceiling of {args.max_bypasses}.  A UI is "
              f"reaching past the Command bus; route it through a Command "
              f"or lower the ceiling if you fixed one.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

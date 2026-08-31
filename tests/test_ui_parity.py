"""UI parity gate — a capability should be reachable from every UI.

The four UIs are one app wearing four faces (see ``METHOD_UI.md``): a user
capability is ONE core Command, dispatched by every UI that could want it.  A
Command only one UI reaches is a capability the other surfaces' users cannot
have, and #150 was exactly that class of bug.

**This file holds THE record of accepted exceptions.**  It used to hold half of
one.  A second list — ``KNOWN_SINGLE_CLIENT_COMMANDS`` in
``test_architecture_boundaries`` — was added 2026-08-28 to ask the same question
across all four trees, which is the "future gate" an earlier version of this
docstring asked for; it was built beside this list rather than replacing it, and
the two shared only 4 of 21 names.  Merged here 2026-08-30, because one rule
recorded twice drifts, and this pair already had.
"""
from __future__ import annotations

import ast
from pathlib import Path

import trcc.core.commands as _commands
from trcc.core.commands._base import Command, Query

_SRC = Path(__file__).resolve().parents[1] / "src"

#: Every UI tree.  gui/qtgui reach Commands through interactive handlers rather
#: than only top-level imports, which is why the collector below matches any
#: reference and not just an ``ImportFrom``.
_UI_TREES = ("gui", "qtgui", "cli", "api")


def _all_command_names() -> set[str]:
    """Every concrete Command subclass exported from ``trcc.core.commands``."""
    return {
        name
        for name in dir(_commands)
        if isinstance(obj := getattr(_commands, name), type)
        and issubclass(obj, Command)
        and obj not in (Command, Query)   # the bases themselves are not capabilities
    }


def _reach_by_command() -> dict[str, set[str]]:
    """Which UI trees reach each Command, by AST — never by regex.

    A reference is an ``ast.Name`` (``dispatch(Foo(...))``) or an ``ast.alias``
    (``from ... import Foo``).  Matching the class NAME in UI source text
    over-counts: ``SendFrame`` appears in two UI trees and is dispatched by
    neither, only mentioned in comments.

    This replaced a narrower collector that matched ``ImportFrom`` alone.  The
    two were measured against each other before the swap and agreed exactly on
    cli (120) and api (115) — the broader one is needed only because gui/qtgui
    do not import everything they dispatch at module level.
    """
    names = _all_command_names()
    reach: dict[str, set[str]] = {n: set() for n in names}
    for ui in _UI_TREES:
        for path in (_SRC / "trcc" / "ui" / ui).rglob("*.py"):
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
    return reach


def _commands_dispatched_by(package: str) -> set[str]:
    """The Commands *package* reaches — one collector, one answer."""
    return {c for c, uis in _reach_by_command().items() if package in uis}


# ── THE record of accepted UI-reach exceptions ──────────────────────────────
#
# ONE dict, because every question below is derivable from the reach set:
#
#     single-client   len(uis) <= 1
#     CLI-only        "cli" in uis and "api" not in uis
#     API-only        "api" in uis and "cli" not in uis
#
# **Why one and not two.**  The reasons lived here (2026-07-12) and the
# four-tree count lived in ``test_architecture_boundaries`` (2026-08-28), so
# neither could contradict the other.  ``git blame`` put every reason at the
# July date — 49 days unexamined while ``src/trcc/ui`` churned through the
# cutover — and one had quietly gone false: ``ResetDevice`` was excused because
# "POST /reset covers the reset intent", but that route sends StopVideo + a red
# frame and never drops the device's cached state.
#
# Tag every reason ``scoped:`` (deliberate — reachable another way, or the
# surface genuinely does not want it) or ``gap:`` (a real hole someone should
# close).  A ``gap`` is not permission to leave it; it is a promise it is known.

KNOWN_UI_ASYMMETRY: dict[str, tuple[frozenset[str], str]] = {
    # ── Turned by a service task, so NO UI dispatches them ────────────────
    "CaptureScreencastFrame": (frozenset(), (
        "scoped: ScreencastDriver turns it; every UI reaches the capability "
        "through Start/StopScreencastDriver"
    )),
    "AdvanceSlideshow": (frozenset({"gui"}), (
        "scoped: SlideshowDriver turns it (2026-08-30), and the gui also drives "
        "it from its own QTimer. Every UI reaches the capability through "
        "Start/StopSlideshowDriver -- before that driver existed a slideshow "
        "configured from the CLI or API was persisted and never rotated"
    )),
    "SendFrame": (frozenset(), (
        "scoped: a deliberate scripting/daemon affordance -- ipc.py names it as "
        "the Command whose raw bytes survive JSON, and its own docstring says "
        "'useful for scripts and end-to-end smoke tests'"
    )),
    "SendScreencastFrame": (frozenset({"gui"}), (
        "scoped: the frame-push half of the gui's own capture timer; cli/api use "
        "ScreencastDriver, and Start/StopScreencast reach all four"
    )),

    # ── Ergonomic composites -- both halves reachable separately ──────────
    "EnsureConnected": (frozenset({"cli"}), (
        "scoped: the CLI's ensure_connected() helper wraps it before every wire "
        "command; the API attaches per-request on the daemon-held App"
    )),
    "InitializeLed": (frozenset({"cli"}), (
        "scoped: the API initialises the LED via SendColor on connect/reset; "
        "explicit init is a CLI setup step"
    )),
    "LoadVideo": (frozenset({"cli", "qtgui"}), (
        "scoped: the capability is covered by PlayVideo (shared); LoadVideo is "
        "a CLI stage-without-play convenience"
    )),
    "ToggleVideo": (frozenset({"cli", "gui", "qtgui"}), (
        "scoped: verb sugar over PauseVideo (shared), which the API exposes "
        "directly"
    )),
    "RestoreLastTheme": (frozenset({"cli", "gui", "qtgui"}), (
        "scoped: the CLI main connect path keeps the raw restore; the API and "
        "GUI use the unified RestoreDeviceState (shared)"
    )),

    # ``GetAutostartStatus`` sat here from 2026-07-12 to 2026-08-31, excused
    # as "desktop autostart is a CLI/desktop concern; the headless API server
    # does not manage the user's session autostart".  The API served BOTH
    # ``GET`` and ``POST /system/autostart`` the whole time -- it managed
    # autostart by reaching ``trcc.platform.autostart()`` past the bus, and
    # dropped the ``path`` field the Command returns.
    #
    # ``test_recorded_ui_reach_matches_reality`` could not catch it: the
    # recorded reach SET was accurate, and only the PROSE was false.  What
    # finally exposed it was widening the reach collector in
    # ``test_architecture_boundaries`` to see the API's own
    # ``request.app.state.trcc`` idiom -- so the answer to "who checks the
    # reasons?" turned out to be a gate one layer down, not a gate on prose.
    # The entry is retired rather than reworded: all four UIs reach it now.

    # ── Listings each surface answers its own way ─────────────────────────
    "ListDevices": (frozenset({"api", "gui", "qtgui"}), (
        "scoped: the CLI uses DiscoverDevices for `trcc device list`. NOT "
        "redundancy -- that probes the bus and ATTACHES what it finds, this "
        "reports what is already attached, and it exists because reaching "
        "app.devices raises under TRCC_DAEMON=1. It is part of the "
        "daemon-client fix. api and gui joined 2026-08-31: GET /system/status and "
        "GET /trcc/status report state (a status route must not attach hardware "
        "as a side effect -- the same distinction from the other end), and the "
        "gui replays its initial fleet from it instead of iterating app.devices"
    )),
    "SetOverlayConfig": (frozenset({"api", "gui", "qtgui"}), (
        "scoped: the CLI edits overlays element-wise (Add/Update/Delete"
        "OverlayElement, shared); the API adds a bulk SetOverlayConfig"
    )),

    # ── Cached vs rendered -- deliberately two questions ──────────────────
    "CurrentFrame": (frozenset({"gui"}), (
        "scoped: BuildPreview (all four) renders to answer; this returns what "
        "the pipeline last produced without rendering. Arguably worth an api "
        "route one day"
    )),

    # ── Asked only by the UI that needs the answer ────────────────────────
    # Recorded 2026-08-30 as "gap: qtgui answers this its own way", from an
    # assumption. TRACED the same day, and the assumption was wrong BOTH times
    # -- qtgui does not answer these differently, it does not ask them.
    "PreviewSize": (frozenset({"gui"}), (
        "scoped: qtgui does not ask -- preview_panel._refresh dispatches "
        "BuildPreview and scales the surface to a fixed _PREVIEW_MAX, never "
        "sizing per device. The gui needs this for its device-accurate bezel "
        "(#136); the cockpit deliberately shows one fixed size"
    )),
    "ResolveThemeDirectories": (frozenset({"gui"}), (
        "scoped: qtgui never resolves directories -- local_theme_browser "
        "dispatches ListThemes(resolution=...), asking for THEMES, and "
        "_browser_base._target_resolution derives only a resolution. The gui's "
        "browsers are path-driven and need the directories themselves"
    )),
}


def _recorded(predicate) -> set[str]:
    """Names whose RECORDED reach satisfies *predicate*."""
    return {n for n, (uis, _why) in KNOWN_UI_ASYMMETRY.items() if predicate(uis)}


def test_recorded_ui_reach_matches_reality() -> None:
    """Every recorded reach must be TRUE, or the reason beside it is fiction.

    This is the test neither old record had, and the reason they drifted: a
    reason nobody re-reads is a decision that expires silently. ``ResetDevice``
    sat excused for 49 days by a route that does something else entirely, and
    nothing in the suite could say so.
    """
    actual = _reach_by_command()
    wrong = {
        name: (sorted(recorded), sorted(actual.get(name, set())))
        for name, (recorded, _why) in KNOWN_UI_ASYMMETRY.items()
        if actual.get(name, set()) != set(recorded)
    }
    assert not wrong, (
        "Recorded UI reach no longer matches reality. Update the entry -- and "
        "RE-READ its reason, which may have expired with it -- or delete it if "
        "the asymmetry is gone:\n"
        + "\n".join(
            f"  {n}: recorded {rec} but actually {act}"
            for n, (rec, act) in sorted(wrong.items())
        )
    )


def test_no_new_single_client_commands() -> None:
    """A Command reachable from <=1 UI is a capability some users cannot have.

    Derived from the one record above rather than kept as a second list, which
    is exactly what this and ``KNOWN_SINGLE_CLIENT_COMMANDS`` used to be.
    """
    measured = {c for c, uis in _reach_by_command().items() if len(uis) <= 1}
    expected = _recorded(lambda uis: len(uis) <= 1)
    assert measured == expected, (
        "Single-client Commands drifted:\n"
        f"  unexpected -- wire into another UI, or record it with a reason: "
        f"{sorted(measured - expected)}\n"
        f"  stale -- now reachable from more than one UI, update the record: "
        f"{sorted(expected - measured)}"
    )


def test_cli_and_api_dispatch_the_same_commands_modulo_the_record() -> None:
    """The CLI <-> API surface differs by EXACTLY what the record allows."""
    cli = _commands_dispatched_by("cli")
    api = _commands_dispatched_by("api")

    expected_cli_only = _recorded(lambda uis: "cli" in uis and "api" not in uis)
    expected_api_only = _recorded(lambda uis: "api" in uis and "cli" not in uis)

    assert cli - api == expected_cli_only, (
        "CLI<->API parity drifted (CLI-only set changed):\n"
        f"  unexpected -- wire into the API, or record it: "
        f"{sorted((cli - api) - expected_cli_only)}\n"
        f"  stale -- no longer CLI-only, update the record: "
        f"{sorted(expected_cli_only - (cli - api))}"
    )
    assert api - cli == expected_api_only, (
        "CLI<->API parity drifted (API-only set changed):\n"
        f"  unexpected -- wire into the CLI, or record it: "
        f"{sorted((api - cli) - expected_api_only)}\n"
        f"  stale -- no longer API-only, update the record: "
        f"{sorted(expected_api_only - (api - cli))}"
    )


def test_shared_command_surface_is_the_bulk_of_both() -> None:
    """Sanity: the two programmatic UIs overwhelmingly share their surface.

    Parity is the norm and the record is the exception. Guards against the
    collector silently returning empty and every assertion above passing
    vacuously.
    """
    cli = _commands_dispatched_by("cli")
    api = _commands_dispatched_by("api")
    shared = cli & api
    assert len(shared) > 3 * len(KNOWN_UI_ASYMMETRY)
    assert len(shared) >= 80   # ~110 today; a floor that catches a dead collector


def test_every_recorded_reason_is_tagged() -> None:
    """Each reason is tagged ``scoped:`` or ``gap:``.

    So a review can tell a deliberate asymmetry from debt without re-deriving
    the judgement -- which is the work the merged record exists to preserve.
    """
    for name, (_uis, reason) in KNOWN_UI_ASYMMETRY.items():
        assert reason.startswith(("scoped:", "gap:")), (
            f"{name}: reason must start with 'scoped:' or 'gap:', got {reason!r}"
        )


def test_the_collector_ignores_comments() -> None:
    """The reason this is an AST walk and not a regex.

    ``SendFrame`` is NAMED in two UI trees and dispatched by neither -- the text
    survives only in comments. A regex scores it 2 and the record above would
    have called it healthy.
    """
    assert _reach_by_command()["SendFrame"] == set(), (
        "SendFrame is dispatched by a UI again -- good, but update this "
        "self-test, which exists to prove the collector ignores comments"
    )
    src = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (_SRC / "trcc" / "ui").rglob("*.py")
        if "__pycache__" not in p.parts
    )
    assert "SendFrame" in src, (
        "the premise is gone: SendFrame is no longer even MENTIONED in the UI "
        "trees, so this no longer demonstrates regex-vs-AST"
    )

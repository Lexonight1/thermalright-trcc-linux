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

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
sys.path.insert(0, str(_ROOT / "dev" / "tools"))

import ui_contract  # noqa: E402  # pyright: ignore[reportMissingImports]

#: The collector lives in ``dev/tools/ui_contract.py`` and is imported, not
#: restated.  It WAS written twice — once there over the AST, once here over the
#: runtime classes — and the two had drifted apart by 34 commands before anyone
#: compared them: the tool's copy could not see a Command handed to a dispatch
#: helper (``dispatch_echo(SomeCommand())``), so it scored the CLI at 90 against
#: a true 124, and printed a contract size smaller than the surface it measured.
#: Same precedent as ``test_logging_coverage``: the tool owns the measurement,
#: the test ratchets it.


def _reach_by_command() -> dict[str, set[str]]:
    """Which UI trees reach each Command.  One collector, one answer."""
    return ui_contract.reach_by_command()


def _commands_dispatched_by(package: str) -> set[str]:
    """The Commands *package* reaches."""
    return ui_contract.dispatched_by(package, _reach_by_command())


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
    "InitializeLed": (frozenset({"cli"}), (
        "scoped: the API initialises the LED via SendColor on connect/reset; "
        "explicit init is a CLI setup step"
    )),
    "LoadVideo": (frozenset({"api", "cli", "qtgui"}), (
        "scoped: the API route landed 2026-09-08 — the old reason, that this "
        "was 'a CLI stage-without-play convenience', was wrong twice over: "
        "qtgui uses it as its main video-import path, and a REST client could "
        "play a .zt it already had but had no way to turn an .mp4 into one. "
        "gui is the remaining hold-out and does not need it: its trimmer "
        "wants the loose .zt back (ExportVideoClip + SetBackground), which is "
        "a different capability, not a missing one"
    )),
    "ToggleVideo": (frozenset({"cli", "gui", "qtgui"}), (
        "scoped: verb sugar over PauseVideo (shared), which the API exposes "
        "directly"
    )),
    "RestoreLastTheme": (frozenset({"cli", "qtgui"}), (
        "scoped: the CLI main connect path keeps the raw restore, and qtgui "
        "exposes it as an explicit 'Restore last' button; the API and GUI "
        "dispatch the unified RestoreDeviceState at display-start. "
        "CORRECTED 2026-09-03: this reason claimed the GUI used "
        "RestoreDeviceState while gui's ONLY site was the never-called "
        "restore_inactive_state -- the recorded REACH was accurate, so "
        "test_recorded_ui_reach_matches_reality passed while the prose beside "
        "it was fiction. gui dispatches it for real now, which is what made "
        "the reach change and this entry finally honest."
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
    # ListDevices: retired 2026-09-25 -- the CLI reaches it too now, through
    # the exit hook that names panels which blank when frames stop (#228).
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

    # ── Reached by cli AND api, by NEITHER gui (the unguarded axis) ──────
    #
    # Nothing asked this question until 2026-09-10, so NONE of these were
    # recorded: the single-client gate fires at <= 1 UI and these have two,
    # and the cli<->api gate sees them AGREE.  ``ResetDevice`` is the proof
    # of what that costs -- it sat here while being DisconnectDevice
    # byte-for-byte for the entire rebuild.
    #
    # Tagged ``gap`` unless there is evidence for ``scoped``: a gap asks for
    # attention, a scope excuses, and guessing the second is how a record
    # stops being worth reading.
    "KeepAliveLoop": (frozenset({"cli", "api"}), (
        "scoped: a hold/confirm LOOP, not a user capability -- the GUIs keep a "
        "device awake through their own render cadence"
    )),
    "EnsureConnected": (frozenset({"cli", "api"}), (
        "scoped: the attach-first PRECONDITION a wire command runs before itself "
        "(31e7fcb4 gave the API what the CLI always had); the GUIs own "
        "connection lifecycle in their per-device handlers"
    )),
    "TickDisplay": (frozenset({"cli", "api"}), (
        "scoped: the core ticks video in every session now (VideoLoop, #249) "
        "and announces each frame as VideoAdvanced, which both GUIs observe. "
        "cli `display play` and the stateless REST tick route still dispatch "
        "it, because neither runs a session"
    )),
    "DaemonStatus": (frozenset({"cli", "api"}), (
        "scoped: the GUIs build the App in-process and can HOST the IPC server, so "
        "they are the daemon rather than a client asking after one"
    )),
    "EnsureDaemon": (frozenset({"cli", "api"}), (
        "scoped: same: a GUI that hosts the App has no daemon to start"
    )),
    "StopDaemon": (frozenset({"cli", "api"}), (
        "scoped: same: closing the window ends the process that IS the daemon"
    )),
    "RunDoctor": (frozenset({"cli", "api"}), (
        "scoped: renders a CLI-shaped summary AND an exit code; a GUI wants the "
        "health checks underneath, which is a different Command, not this "
        "one"
    )),
    "RunQuickstart": (frozenset({"cli", "api"}), (
        "scoped: a terminal onboarding walk (doctor -> scan) whose whole output is "
        "CLI text; the GUIs have their own first-run flow"
    )),
    "ResetDevice": (frozenset({"cli", "api"}), (
        "gap: power-cycle + restore. It sat in exactly this blind spot, which is "
        "why nobody noticed it was DisconnectDevice byte-for-byte for the "
        "whole rebuild"
    )),
    "ExportConfig": (frozenset({"cli", "api"}), (
        "gap: write a device's settings to JSON -- a GUI user cannot back up a "
        "configuration"
    )),
    "ImportConfig": (frozenset({"cli", "api"}), (
        "gap: restore settings from an ExportConfig JSON; the other half of the "
        "same hole"
    )),
    "ExportDcTheme": (frozenset({"cli", "api"}), (
        "gap: write a theme as a legacy config1.dc"
    )),
    "ExportOverlay": (frozenset({"cli", "api"}), (
        "gap: copy a theme's overlay config out"
    )),
    "ListSensors": (frozenset({"cli", "api"}), (
        "gap: enumerate sensor descriptors -- a GUI picker builds its list some "
        "other way instead of asking the bus"
    )),
    "ListFans": (frozenset({"cli", "api"}), (
        "gap: enumerate fans with live readings; same shape as ListSensors"
    )),
    "ListDisks": (frozenset({"cli", "api"}), (
        "gap: enumerate mounted partitions; same shape"
    )),
    "ListFonts": (frozenset({"cli", "api", "qtgui"}), (
        "gap: ui/gui ALONE enumerates families its own way -- ColorPickerPanel "
        "opens a QFontDialog, which asks Qt directly rather than the bus.  This "
        "entry used to read 'an overlay editor needs exactly this', and it was "
        "right: qtgui's element dialog adopted the Query when the family "
        "control landed, which also closed the deeper hole the reason could "
        "not see -- `font` reached the domain object, the Commands, Settings "
        "and draw_text, and THREE of the four faces could not produce it.  "
        "gui's own picker goes when ui/gui does"
    )),
    "ListWebThemes": (frozenset({"cli", "api"}), (
        "gap: downloaded cloud-theme previews for a resolution"
    )),
    "ListLedModes": (frozenset({"cli", "api"}), (
        "gap: the LEDMode enum names a picker needs"
    )),
    "ListLedStyles": (frozenset({"cli", "api"}), (
        "gap: LED styles from the PM registry. Do NOT wire it until the Query is "
        "trusted -- see project_led_capability_columns"
    )),
    "SetLedColors": (frozenset({"cli", "api"}), (
        "gap: set the LED colour array + on/off + brightness; the GUIs have LED "
        "panels but reach the capability another way"
    )),
    "RenderLed": (frozenset({"cli", "api"}), (
        "gap: compute and send one LED frame from settings + sensors"
    )),
    "SendColor": (frozenset({"cli", "api"}), (
        "gap: push a solid-colour frame to an LCD"
    )),
    "SendImage": (frozenset({"cli", "api"}), (
        "gap: push an image without staging it as a theme"
    )),
    "PauseVideo": (frozenset({"cli", "api"}), (
        "gap: toggle the per-device playback pause flag"
    )),
    "LoopVideo": (frozenset({"cli", "api"}), (
        "gap: toggle whether playback wraps or sticks at the last frame"
    )),
    "StartSlideshowDriver": (frozenset({"cli", "api", "qtgui"}), (
        "gap: ui/gui ALONE still rotates from its own QTimer instead of the "
        "shared driver.  qtgui adopted the driver 2026-09-15 -- its panel "
        "already dispatched ConfigureSlideshow + SetSlideshow, which persist "
        "the config and start nothing, so a slideshow set up there reported "
        "itself enabled and never switched a theme.  gui is now the only "
        "duplicated mechanism left, and it goes when ui/gui does"
    )),
    "StopSlideshowDriver": (frozenset({"cli", "api", "qtgui"}), (
        "gap: the other half of the same pair -- ui/gui only"
    )),
    "SetMediaPlayer": (frozenset({"cli", "api"}), (
        "gap: set the device's media-player source URI"
    )),
    "UploadBootAnimation": (frozenset({"cli", "api"}), (
        "gap: upload a boot animation to a SCSI LCD's flash"
    )),
    "RenderDcStandalone": (frozenset({"cli", "api"}), (
        "gap: render a DC config with no device and no theme load -- a preview "
        "capability"
    )),
    "EnsureDataDownload": (frozenset({"cli", "api"}), (
        "gap: force-install the theme/cloud/mask archives. The gui OBSERVES "
        "DataInstalled (trcc_app.py:560) but dispatches nothing, so a GUI "
        "user cannot re-fetch archives whose first download failed"
    )),
    "MarkFirstRunDone": (frozenset({"cli", "api"}), (
        "gap: record that onboarding completed; the GUIs have a first-run flow "
        "but do not close it through the bus"
    )),
    "RunSetup": (frozenset({"cli", "api"}), (
        "gap: OS-specific one-time setup (udev rules, WinUSB guide). A GUI-only "
        "Linux user needs udev rules just as much (#194)"
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


#: The two graphical faces.  A capability absent from BOTH is absent from every
#: window a user can open, however well the CLI covers it.
_GUIS = frozenset({"gui", "qtgui"})


def _absent_from_both_guis(uis: frozenset[str]) -> bool:
    """Reached by cli AND api, by NEITHER graphical UI."""
    return "cli" in uis and "api" in uis and not (uis & _GUIS)


def test_no_command_is_absent_from_both_guis() -> None:
    """The axis nothing asked about, and the one the standard is written on.

    *Anything the C# GUI can do, every UI should be able to do.*  A Command the
    CLI and the API both dispatch and NEITHER window offers is precisely a
    capability the graphical users cannot have -- and until 2026-09-10 no gate
    could see it, for three structural reasons at once:

        gui <-> qtgui delta   absent from BOTH, so in neither difference set
        single-client gate    fires at len(uis) <= 1; these have exactly 2
        cli <-> api gate      cli and api AGREE, so there is nothing to report

    ``ResetDevice`` is what that costs.  It sat in this set for the whole
    rebuild while being ``DisconnectDevice`` byte-for-byte, and no assertion in
    this file could have noticed.

    Derived from the ONE record, like every other gate here: to add a Command
    to this set you must write down why, and ``gap:`` is not permission to
    leave it -- it is a promise that it is known.
    """
    measured = {c for c, uis in _reach_by_command().items()
                if _absent_from_both_guis(frozenset(uis))}
    expected = _recorded(_absent_from_both_guis)

    assert measured == expected, (
        "Commands absent from BOTH GUIs drifted:\n"
        f"  unexpected -- wire it into a window, or record it with a reason: "
        f"{sorted(measured - expected)}\n"
        f"  stale -- a window reaches it now, update the record: "
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

    The ratio counts ``scoped:`` entries alone, not the whole record.  A
    ``scoped`` is an asymmetry we have EXCUSED, and letting those outgrow the
    shared surface is what this is watching for.  A ``gap`` is the opposite --
    it says parity IS broken here and someone should close it -- so counting
    gaps would punish the act of writing a hole down, and reward leaving it
    undiscovered.  That is not hypothetical: recording the 33 Commands on the
    both-GUIs-absent axis took the record from 13 to 46 and tripped this
    assertion, having improved the tree rather than harmed it.
    """
    cli = _commands_dispatched_by("cli")
    api = _commands_dispatched_by("api")
    shared = cli & api
    excused = {n for n, (_uis, why) in KNOWN_UI_ASYMMETRY.items()
               if why.startswith("scoped:")}
    assert len(shared) > 3 * len(excused)
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


# =========================================================================
# The collector measures REFERENCE, not reachability — so dead code lies
# =========================================================================
#
# ``reach_by_command`` counts an ``ast.Name`` or an ``ast.alias``: importing a
# Command is reach.  That breadth is deliberate (matching only inline dispatch
# undercounted the CLI by 34), but it has one consequence — **a function nobody
# calls keeps its import alive, and the UI scores a capability it cannot
# actually perform.**
#
# Measured 2026-09-03: gui's ONLY ``RestoreDeviceState`` site was
# ``restore_inactive_state``, which had zero callers in src/, tests/ or dev/.
# gui's reach read 83; the truth was 82, and ``RestoreDeviceState`` — the
# entry contract METHOD_UI.md requires at every display-start — actually sat in
# the "cli+api, neither GUI" set that no gate watches.

_QT_VIRTUALS = frozenset({
    "closeEvent", "paintEvent", "resizeEvent", "showEvent", "hideEvent",
    "mousePressEvent", "mouseMoveEvent", "mouseReleaseEvent", "keyPressEvent",
    "eventFilter", "enterEvent", "leaveEvent", "wheelEvent", "dragEnterEvent",
    "dropEvent", "mouseDoubleClickEvent", "contextMenuEvent", "sizeHint",
    "changeEvent", "focusInEvent", "focusOutEvent", "moveEvent",
})
_FRAMEWORK_ENTRIES = frozenset({"main", "run", "launch", "build_app"})


def _identifier_corpus() -> str:
    """Every line of src/ and tests/, for a name-reference check."""
    return "\n".join(
        f.read_text(encoding="utf-8")
        for root in (_SRC, _ROOT / "tests")
        for f in root.rglob("*.py")
    )


def _dispatching_functions_never_referenced() -> list[str]:
    """``file:line name`` for every ui/ function that dispatches but is dead."""
    import ast
    import re

    blob = _identifier_corpus()
    dead: list[str] = []
    for path in sorted((_SRC / "trcc" / "ui").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # A DECORATED function is called by whatever decorated it — every
            # FastAPI route and Typer command.  Omitting this exemption flags
            # 95 live entry points; it is load-bearing, not caution.
            if (node.decorator_list or node.name.startswith("__")
                    or node.name in _QT_VIRTUALS
                    or node.name in _FRAMEWORK_ENTRIES):
                continue
            dispatches = any(
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr in ("dispatch", "_dispatch")
                for sub in ast.walk(node)
            )
            if not dispatches:
                continue
            uses = (len(re.findall(rf"\b{re.escape(node.name)}\b", blob))
                    - len(re.findall(rf"\bdef\s+{re.escape(node.name)}\b", blob)))
            if uses == 0:
                dead.append(f"{path.relative_to(_SRC)}:{node.lineno} {node.name}")
    return dead


def test_no_ui_command_is_reached_only_from_dead_code() -> None:
    """A dispatch nobody can execute is a capability the UI does not have.

    This guards the collector's premise rather than its arithmetic: reach is
    measured by reference, so an uncalled function makes a UI *look* capable.
    Delete the dead function (its import goes with it and the count corrects
    itself), or wire it up.
    """
    dead = _dispatching_functions_never_referenced()
    assert not dead, (
        "These ui/ functions dispatch Commands but nothing calls them, so the "
        "reach they contribute is fiction:\n  " + "\n  ".join(dead)
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

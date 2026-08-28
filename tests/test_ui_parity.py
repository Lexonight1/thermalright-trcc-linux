"""UI parity gate — the CLI and API must dispatch the same Commands.

The four UIs are one app wearing four faces (see ``METHOD_UI.md``): a user
capability is ONE core Command, dispatched identically by every UI.  This pins
the CLI ↔ API Command surface — the two complete programmatic UIs — so a
Command wired into one but not the other FAILS here, forcing the author to
either reach parity or record the asymmetry with a reason.  That structurally
kills the "one UI forgot" class of bug (#150 was exactly that).

Scope: CLI ↔ API only.  GUI/qtgui dispatch Commands through interactive
handlers rather than top-level imports, so their reachability needs a dynamic
collector — a future gate, noted here rather than silently omitted.
"""
from __future__ import annotations

import ast
from pathlib import Path

import trcc.core.commands as _commands
from trcc.core.commands._base import Command

_SRC = Path(__file__).resolve().parents[1] / "src"


def _all_command_names() -> set[str]:
    """Every concrete Command subclass exported from ``trcc.core.commands``."""
    return {
        name
        for name in dir(_commands)
        if isinstance(obj := getattr(_commands, name), type)
        and issubclass(obj, Command)
        and obj is not Command
    }


def _commands_dispatched_by(package: str) -> set[str]:
    """Command names a UI package imports from ``core.commands`` (≈ dispatches).

    A thin UI adapter imports a Command only to build + dispatch it, so the set
    of Commands imported from ``core.commands`` across ``ui/<package>`` is a
    faithful proxy for the Commands that surface reaches.
    """
    all_cmds = _all_command_names()
    found: set[str] = set()
    for path in (_SRC / "trcc" / "ui" / package).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and "core.commands" in node.module
            ):
                found |= {alias.name for alias in node.names if alias.name in all_cmds}
    return found


# ── The parity ledger — every intentional CLI/API asymmetry, with a reason ──
# Tag each reason ``scoped:`` (legitimately one-UI) or ``gap:`` (should reach
# parity — a tracked TODO).  Delete an entry when the asymmetry is resolved; a
# stale entry (no longer in the live diff) FAILS the tests below, and a NEW
# one-sided Command FAILS until it is either wired into the other UI or ledgered
# here with a reason.

_CLI_ONLY: dict[str, str] = {
    "EnsureConnected": (
        "scoped: the CLI's ensure_connected() helper wraps it before every wire "
        "command; the API attaches per-request on the daemon-held App"
    ),
    "ExportOverlay": (
        "scoped: the API exports via ExportDcTheme (POST /theme/export-dc); "
        "overlay-only export is a CLI file convenience"
    ),
    "GetAutostartStatus": (
        "scoped: desktop autostart is a CLI/desktop concern; the headless API "
        "server does not manage the user's session autostart"
    ),
    "InitializeLed": (
        "scoped: the API initialises the LED via SendColor on connect/reset; "
        "explicit init is a CLI setup step"
    ),
    "LoadVideo": (
        "scoped: the capability is covered by PlayVideo (shared); LoadVideo is a "
        "CLI stage-without-play convenience"
    ),
    "RenderDcStandalone": "scoped: a CLI diagnostic/dev command",
    "ResetDevice": (
        "scoped: the API POST /reset route covers the reset intent via a "
        "SendColor probe"
    ),
    "RestoreLastTheme": (
        "scoped: the CLI main connect path keeps the raw restore; the API and "
        "GUI use the unified RestoreDeviceState (shared)"
    ),
    "RunQuickstart": (
        "gap: CLI-only first-run onboarding; the API has piecemeal /theme/init + "
        "/devices with no single onboarding command — the lifecycle drift "
        "METHOD_UI names (target: a shared EnsureReady command)"
    ),
    "SendImage": (
        "scoped: the API and GUI use LoadImage (shared); SendImage is a CLI "
        "raw-send variant"
    ),
    "ToggleVideo": (
        "scoped: verb sugar over PauseVideo (shared), which the API exposes "
        "directly"
    ),
}

_API_ONLY: dict[str, str] = {
    "ListWebThemes": (
        "gap: the API lists web masks (ListWebThemes); the CLI has cloud-list "
        "(ListCloudThemes) for themes but no web-mask listing command"
    ),
    "SetOverlayConfig": (
        "scoped: the CLI edits overlays element-wise (Add/Update/Delete"
        "OverlayElement, shared); the API adds a bulk SetOverlayConfig"
    ),
}


def test_cli_and_api_dispatch_the_same_commands_modulo_ledger() -> None:
    """The CLI ↔ API Command surface differs by EXACTLY the ledger.

    A new one-sided Command (or a ledgered one that changed sides) fails here.
    """
    cli = _commands_dispatched_by("cli")
    api = _commands_dispatched_by("api")

    assert cli - api == set(_CLI_ONLY), (
        "CLI↔API Command parity drifted (CLI-only set changed):\n"
        f"  unexpected — wire into the API or add to _CLI_ONLY with a reason: "
        f"{sorted((cli - api) - set(_CLI_ONLY))}\n"
        f"  stale — remove from _CLI_ONLY (no longer CLI-only): "
        f"{sorted(set(_CLI_ONLY) - (cli - api))}"
    )
    assert api - cli == set(_API_ONLY), (
        "CLI↔API Command parity drifted (API-only set changed):\n"
        f"  unexpected — wire into the CLI or add to _API_ONLY with a reason: "
        f"{sorted((api - cli) - set(_API_ONLY))}\n"
        f"  stale — remove from _API_ONLY (no longer API-only): "
        f"{sorted(set(_API_ONLY) - (api - cli))}"
    )


def test_shared_command_surface_is_the_bulk_of_both() -> None:
    """Sanity: the two UIs overwhelmingly share their Command surface (parity is
    the norm; the ledger is the exception).  Guards against the collector
    silently returning empty and the parity test passing vacuously."""
    cli = _commands_dispatched_by("cli")
    api = _commands_dispatched_by("api")
    shared = cli & api
    assert len(shared) > 3 * (len(_CLI_ONLY) + len(_API_ONLY))
    assert len(shared) >= 80   # ~95 today; a floor that catches a broken collector


def test_every_ledger_reason_is_tagged() -> None:
    """Each ledger reason is tagged ``scoped:`` or ``gap:`` so drift reviews stay
    honest about which asymmetries are deliberate and which are debt."""
    for name, reason in {**_CLI_ONLY, **_API_ONLY}.items():
        assert reason.startswith(("scoped:", "gap:")), (
            f"{name}: reason must start with 'scoped:' or 'gap:', got {reason!r}"
        )

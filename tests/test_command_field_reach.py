"""No face may drop a Command field that another face sets.

``dev/tools/ui_contract.py`` measures WHICH Commands each UI dispatches, and
that is how "unified UI" has been gated.  It cannot see the payload: all four
faces dispatch ``AddOverlayElement``, so all four score equal, while three of
them could not set ``font`` -- a field the domain object, ``Settings``,
``element_family`` and ``draw_text`` all supported and every layer beneath the
faces already gated.  Two more were found the same way once the axis was
measured at all:

* ``ExportVideoClip.fit_mode`` -- gui alone passed the trimmer's W/H choice, so
  a clip exported from the CLI, the API or qtgui was always letterboxed and
  could never fill the panel.  #291, still open in three faces.
* ``SaveTheme.overwrite`` -- the GUIs read ``target_exists`` and offer a
  one-click overwrite; the CLI and the API could not send it, and the API could
  not even RECEIVE the flag, because the route flattened the refusal into
  ``400 {"detail": ...}``.

**This gate is RELATIVE and deliberately so.**  It fires when one face sets a
field another face dispatching the same Command does not.  It is NOT the same
rule as ``test_overlay_vocabulary_reaches_every_face``, which is ABSOLUTE --
every field, whether or not any face sets it -- and therefore catches a field
NO face reaches, which this one cannot see.  Neither subsumes the other; do
not merge them.

**Scope is DERIVED** from ``dataclasses.fields``, so a field added to a Command
widens this gate without anyone remembering to.

MUTATION CHECK -- delete a kwarg from any dispatch site named below and
``test_no_new_field_asymmetry`` must name that Command, that face and that
field.  Delete an entry from the table while the asymmetry remains and the same
test must fail.  Close an asymmetry without removing its entry and
``test_no_stale_field_asymmetry_records`` must fail.
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from trcc.ipc import COMMAND_TYPES

_UI_ROOT = Path(__file__).resolve().parents[1] / "src" / "trcc" / "ui"
_FACES = ("cli", "api", "gui", "qtgui")

#: ``key`` addresses the device on almost every Command; it is routing, not a
#: capability, and a face that omits it would fail at construction anyway.
_ROUTING = frozenset({"key"})

# ── THE record of accepted field asymmetries ────────────────────────────────
#
# Tagged like ``test_ui_parity.KNOWN_UI_ASYMMETRY``: ``scoped:`` (deliberate --
# the face genuinely does not want it) or ``gap:`` (a real hole).
#
# ``unclassified:`` is a THIRD tag and it is honest rather than tidy.  These
# were measured on 2026-09-22 and NOT individually traced.  Writing ``scoped:``
# prose for a pair nobody has read is how a record stops being worth reading --
# this repo has been burned by exactly that (see the ``ResetDevice`` note in
# ``test_ui_parity``).  The count is asserted below so the backlog is visible
# and can only shrink by someone reading one.
KNOWN_FIELD_ASYMMETRY: dict[tuple[str, str], tuple[frozenset[str], str]] = {
    ("AddOverlayElement", "qtgui"): (frozenset({"element_id"}), (
        "scoped: the id is auto-generated (UUID4) when omitted and returned in "
        "the Result; the editor never needs to choose one, while a script or a "
        "REST client may want an idempotent id"
    )),
    ("StopVideo", "api"): (frozenset({"keep_override"}), (
        "scoped: TRACED 2026-09-22.  The flag exists only to stop gui's own "
        "_cleanup_device teardown wiping the persisted background (#271).  No "
        "other face makes that call, App.close never dispatches StopVideo, and "
        "App.detach already frees the decode -- so clearing the override is "
        "the CORRECT behaviour for a user-initiated stop, which is the only "
        "StopVideo these faces make"
    )),
    ("StopVideo", "cli"): (frozenset({"keep_override"}), (
        "scoped: same as api -- a user-initiated stop must clear the override"
    )),
    ("StopVideo", "qtgui"): (frozenset({"keep_override"}), (
        "scoped: same -- qtgui's only StopVideo is the Stop button"
    )),
    ("BuildPreview", "api"): (frozenset({"sample_cols"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("BuildPreview", "cli"): (frozenset({"encode"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("BuildPreview", "gui"): (frozenset({"sample_cols"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("BuildPreview", "qtgui"): (frozenset({"sample_cols"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("EnableAutostart", "gui"): (frozenset({"target"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("EnsureDaemon", "api"): (frozenset({"timeout"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("GetPaths", "api"): (frozenset({"resolution"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("ImportTheme", "gui"): (frozenset({"name"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("ImportTheme", "qtgui"): (frozenset({"name"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("ListCloudThemes", "api"): (frozenset({"resolution"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("ListCloudThemes", "cli"): (frozenset({"resolution"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("ListMasks", "api"): (frozenset({"directory"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("ListMasks", "gui"): (frozenset({"directory"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("ListMasks", "qtgui"): (frozenset({"directory"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("ListThemes", "gui"): (frozenset({"directory"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("ListThemes", "qtgui"): (frozenset({"directory"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("LoadVideo", "qtgui"): (frozenset({"end_ms", "rotation", "start_ms"}), (
        "unclassified: measured 2026-09-22, not traced -- qtgui trims through "
        "ExportVideoClip, so this may well be scoped, but nobody has read it"
    )),
    ("PlayVideo", "qtgui"): (frozenset({"fps"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("RunUpgrade", "gui"): (frozenset({"dry_run"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("StartScreencastDriver", "api"): (frozenset({"interval_s"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
    ("StartScreencastDriver", "cli"): (frozenset({"interval_s"}), (
        "unclassified: measured 2026-09-22, not traced"
    )),
}


def _kwargs_by_command(face: str) -> dict[str, set[str]]:
    """Union of keyword names *face* passes to each Command it constructs.

    Union across sites: a face may build one Command in several places (the
    CLI seeds overlay elements from a ``--metric`` spec with ``**kwargs``,
    which names nothing and so contributes nothing), and the question is
    whether the face as a whole can express the field.
    """
    found: dict[str, set[str]] = {}
    for path in sorted((_UI_ROOT / face).rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if name not in COMMAND_TYPES:
                continue
            found.setdefault(name, set()).update(
                kw.arg for kw in node.keywords if kw.arg is not None)
    return found


def _measure() -> dict[tuple[str, str], frozenset[str]]:
    """Every (Command, face) that drops a field another face sets."""
    per_face = {f: _kwargs_by_command(f) for f in _FACES}
    out: dict[tuple[str, str], frozenset[str]] = {}
    for name, command in COMMAND_TYPES.items():
        if not dataclasses.is_dataclass(command):
            continue
        fields = {f.name for f in dataclasses.fields(command)} - _ROUTING
        dispatchers = {f: per_face[f][name] for f in _FACES
                       if name in per_face[f]}
        if len(dispatchers) < 2 or not fields:
            continue
        named = set().union(*dispatchers.values()) & fields
        for face, passed in dispatchers.items():
            missing = named - passed
            if missing:
                out[(name, face)] = frozenset(missing)
    return out


def test_no_new_field_asymmetry() -> None:
    measured = _measure()
    unexpected = {
        pair: sorted(fields - KNOWN_FIELD_ASYMMETRY.get(pair, (frozenset(), ""))[0])
        for pair, fields in measured.items()
        if fields - KNOWN_FIELD_ASYMMETRY.get(pair, (frozenset(), ""))[0]
    }
    assert not unexpected, (
        "A face stopped carrying a field another face sets:\n  "
        + "\n  ".join(f"{cmd}.{face} no longer passes {fields}"
                      for (cmd, face), fields in sorted(unexpected.items()))
        + "\nPass it at the dispatch site (and give the face a control for "
          "it), or record it in KNOWN_FIELD_ASYMMETRY with a reason."
    )


def test_no_stale_field_asymmetry_records() -> None:
    """A closed asymmetry must lose its record, or the table becomes fiction."""
    measured = _measure()
    stale = sorted(
        f"{cmd}.{face} ({sorted(fields)})"
        for (cmd, face), (fields, _) in KNOWN_FIELD_ASYMMETRY.items()
        if not (fields & measured.get((cmd, face), frozenset()))
    )
    assert not stale, (
        "These records no longer describe reality — the face carries the "
        "field now.  Delete them:\n  " + "\n  ".join(stale)
    )


@pytest.mark.parametrize("pair", sorted(KNOWN_FIELD_ASYMMETRY))
def test_every_record_is_tagged(pair: tuple[str, str]) -> None:
    """Untagged prose is how a record stops being worth reading."""
    _, reason = KNOWN_FIELD_ASYMMETRY[pair]
    assert reason.startswith(("scoped:", "gap:", "unclassified:")), (
        f"{pair} must start its reason with scoped: / gap: / unclassified:"
    )


#: Pairs measured 2026-09-22 and NOT individually traced.  Pinned BOTH ways
#: below, which is the point: a one-sided ``<=`` passes when the count FALLS,
#: and the cheapest way to make it fall is to relabel a record ``scoped:``
#: without reading it -- the exact move the tag exists to prevent.  Caught by
#: mutation while this file was being written; the one-sided version let it
#: through silently.  Same two-sided idiom as ``test_god_classes``.
#: 22 -> 21 on 2026-09-23: ``("StartScreencast", "qtgui")`` / ``audio`` was
#: TRACED by the ``ui/gui`` hand walk and closed.  It was a real gap -- gui had
#: a mic button, cli ``--audio``, api ``body.audio``, and ``grep -ri audio
#: src/trcc/ui/qtgui/`` returned ZERO lines -- and tracing it turned up a
#: defect in the Command underneath: re-issuing a live session with
#: ``audio=False`` persisted the flag and never released the microphone, so
#: the bars kept drawing after every face turned them off.  See
#: ``_sync_audio`` in ``core/commands/device.py``.
UNCLASSIFIED = 21


def test_the_unclassified_backlog_does_not_grow() -> None:
    """A NEW asymmetry needs a reason, not an ``unclassified`` tag."""
    found = _unclassified()
    assert len(found) <= UNCLASSIFIED, (
        f"{len(found)} unclassified records, recorded {UNCLASSIFIED}.  A new "
        f"asymmetry needs a REASON: {sorted(set(found) - _RECORDED)}"
    )


def test_the_unclassified_backlog_has_no_slack() -> None:
    """And it may not fall silently — falling must be someone READING one."""
    found = _unclassified()
    assert len(found) >= UNCLASSIFIED, (
        f"Unclassified records went DOWN to {len(found)} from "
        f"{UNCLASSIFIED}.  If you TRACED one, lower UNCLASSIFIED in "
        f"tests/test_command_field_reach.py and say what you found in its "
        f"reason.  If you only relabelled it, read it first -- an unread "
        f"'scoped:' is worse than an honest 'unclassified:'."
    )


def _unclassified() -> list[tuple[str, str]]:
    return [p for p, (_, why) in KNOWN_FIELD_ASYMMETRY.items()
            if why.startswith("unclassified:")]


#: Snapshot of the pairs that were unclassified when the count was pinned, so
#: the "grew" message can name the NEW one rather than just a number.
_RECORDED = frozenset({
    ("BuildPreview", "api"), ("BuildPreview", "cli"), ("BuildPreview", "gui"),
    ("BuildPreview", "qtgui"), ("EnableAutostart", "gui"),
    ("EnsureDaemon", "api"), ("GetPaths", "api"), ("ImportTheme", "gui"),
    ("ImportTheme", "qtgui"), ("ListCloudThemes", "api"),
    ("ListCloudThemes", "cli"), ("ListMasks", "api"), ("ListMasks", "gui"),
    ("ListMasks", "qtgui"), ("ListThemes", "gui"), ("ListThemes", "qtgui"),
    ("LoadVideo", "qtgui"), ("PlayVideo", "qtgui"), ("RunUpgrade", "gui"),
    ("StartScreencastDriver", "api"), ("StartScreencastDriver", "cli"),
})

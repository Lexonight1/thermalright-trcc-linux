"""The gui's LCD page has every control the Windows app's has -- checked, not hoped.

Features went missing for months with nobody noticing: the local-theme panel's
Game mode button and CPU box were never ported (2.1.6 and 2.1.8 both have
them), because nothing listed what the C# form shows.  This does.
``dev/decompiler/control-inventory.json`` (from ``inventory_controls.py``,
committed because CI has no decompile) lists every designer control on
``FormCZTV`` and the user controls it embeds -- where it sits and when the C#
shows it.  Our window is built for real over the mock, and each control is
looked for at the same place:

* **visible** in the C# -> a widget of ours must sit at that rect;
* **hidden** for good -> nothing of ours may show there, and a hidden user
  control's children are not checked at all (unreachable in the C#);
* **conditional** -> shown by mode at runtime; not judged by position.

A control the C# MOVES at runtime (``moved_at``) has no fixed place to check:
``UCScreenImage`` places the preview per panel, so its designer rect is only
where it starts.  One it only RESIZES (``resized_at``) is checked at its
position, any size -- ``UCComboBoxA`` grows its height to open the list.

Matching is by geometry because the gui copies the C#'s coordinates.  Every
miss that is a decision, not a defect, is a ``scoped:`` row with its reason;
every defect is a ``gap:`` row -- and ``GAPS`` only goes down.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_INVENTORY = json.loads(
    (Path(__file__).resolve().parents[1] / "dev" / "decompiler"
     / "control-inventory.json").read_text(encoding="utf-8"))

#: C# class -> where its controls live in our window, as an attribute path
#: from ``TRCCApp``.  Derived from the data, not the names: for each class the
#: widget whose children sit at the most of its control rects (2026-10-05).
_WHERE: dict[str, str] = {
    "FormCZTV": "form_container",
    "UCThemeSetting": "uc_theme_setting",
    "UCThemeLocal": "uc_theme_local",
    "UCThemeWeb": "uc_theme_web",
    "UCThemeMask": "uc_theme_mask",
    "UCImageCut": "uc_image_cut",
    "UCVideoCut": "uc_video_cut",
    "UCScreenImageBK": "uc_preview",
    "UCBoFangQiKongZhi": "uc_preview.progress_container",
    "UCScrollB": "uc_brightness",
    "UCBeiJingXianShi": "uc_theme_setting.background_panel",
    "UCMengBanXianShi": "uc_theme_setting.mask_panel",
    "UCShiPingBoFangQi": "uc_theme_setting.video_panel",
    "UCXiTongXianShi": "uc_theme_setting.overlay_grid",
    "UCXiTongXianShiColor": "uc_theme_setting.color_panel",
    "UCXiTongXianShiTable": "uc_theme_setting.data_table",
    # 0 of its rects match: ours stacks the buttons, the C# lays them in a row.
    "UCXiTongXianShiAdd": "uc_theme_setting.add_panel",
}

#: C# classes that have no widget of ours, and why.
_NOT_PORTED: dict[str, str] = {
    "UCComboBoxA": (
        "scoped: a native QComboBox replaces the C#'s hand-drawn list (a header "
        "and 8 option buttons); where the combo sits is checked on FormCZTV"),
    "UCShortcut": (
        "gap: the icon element editor -- overlay mode 5 (an image or a file's "
        "icon on the panel) is not supported; 0 of 9,809 shipped/cached themes "
        "use it, but the C# lets a user add one"),
}

#: Every control whose check fails today, keyed ``Class.control`` -- each one a
#: ``scoped:`` decision with its reason or a ``gap:`` to fix.  Baseline measured
#: 2026-10-05, each role read in the decompile before its row was written.
_KNOWN: dict[str, str] = {
    "FormCZTV.ucComboBoxA1": (
        "gap: the rotation dropdown sits at x=39, the C#'s at x=26"),
    "UCThemeSetting.ucTouPingXianShi1": (
        "gap: 2.1.8's FormCZTV never shows its screencast panel (designer "
        "Visible=false, no runtime show anywhere); ours shows ScreenCastPanel. "
        "Where 2.1.8 puts screen casting is not read yet"),
    "UCBeiJingXianShi.buttonOnOff": (
        "gap: the background on/off -- the C#'s slide switch is 50x50 at "
        "(0, 0), ours 36x18 at (5, 5)"),
    "UCShiPingBoFangQi.buttonOnOff": (
        "gap: the media-player on/off -- the C#'s slide switch is 50x50 at "
        "(0, 0), ours 36x18 at (5, 5)"),
    "UCMengBanXianShi.button1": (
        "gap: the local-mask button sits at x=149 in the C#; ours are at 115 "
        "and 175"),
    "UCVideoCut.labelTimer": (
        "gap: the cutter's time labels sit elsewhere -- ours 150x16 at "
        "(32, 531), the C#'s 88x20"),
    "UCVideoCut.labelStartTimer": (
        "gap: the cutter's start time -- ours 150x16 at (32, 597), the C#'s "
        "88x20"),
    "UCVideoCut.labelAllTimer": (
        "gap: the cutter's length -- ours 120x16 at (370, 531), the C#'s 88x20 "
        "at (408, 531)"),
    "UCVideoCut.labelStopTimer": (
        "gap: the cutter's end time -- ours 120x16 at (370, 597), the C#'s "
        "88x20 at (408, 597)"),
    "UCXiTongXianShiColor.buttonText": (
        "gap: the font button is 24x24 in the C# with the name and size as "
        "labels beside it; ours is one 125x24 button"),
    "UCXiTongXianShiColor.label1": "gap: the font name label (see buttonText)",
    "UCXiTongXianShiColor.label2": (
        "gap: the font size label -- ours is an editable size box at (140, 89)"),
    "UCXiTongXianShiColor.ucColorB1": (
        "gap: the custom-painted colour bar (UCColorB, picked with the mouse) "
        "is missing"),
}

#: The ``gap:`` rows above and in ``_NOT_PORTED`` -- the LCD page's distance
#: from the Windows app.  Only goes down.
GAPS = 14    # 16 -> 14 (2026-10-07): UCVideoCut.button1/2 -- the 15/24 fps choice


def _reachable() -> dict[str, list[dict[str, Any]]]:
    """The classes a user can reach: not through a user control the C# hides
    for good."""
    classes = _INVENTORY["classes"]
    out: dict[str, list[dict[str, Any]]] = {}
    queue = [_INVENTORY["root"]]
    while queue:
        cls = queue.pop(0)
        if cls in out or cls not in classes:
            continue
        out[cls] = classes[cls]
        queue += [c["type"] for c in classes[cls]
                  if c["type"].startswith("UC") and c["visibility"] != "hidden"]
    return out


@pytest.fixture(scope="module")
def window(tmp_path_factory: pytest.TempPathFactory, qapp: Any) -> Iterator[Any]:
    """The real gui over a connected mock panel, as a user opens it."""
    import time

    from tests.conftest import renderable_theme
    from tests.mock_platform import MockPlatform
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import ConnectDevice, LoadTheme
    from trcc.ui.gui.trcc_app import TRCCApp

    root = tmp_path_factory.mktemp("lcd_page")
    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}], root),
              renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key="0402:3922")).ok
    theme = renderable_theme(app.platform.paths().theme_dir(320, 320), "Theme1")
    assert app.dispatch(LoadTheme(key="0402:3922", path=theme)).ok
    win = TRCCApp(app=app)
    win.replay_initial_devices()
    deadline = time.monotonic() + 10
    while "0402:3922" not in win._handlers and time.monotonic() < deadline:
        qapp.processEvents()
    win._activate_device("0402:3922")
    qapp.processEvents()
    yield win
    win.close()
    app.close()


def _widget(window: Any, path: str) -> Any:
    obj = window
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def _ours(widget: Any) -> list[tuple[list[int], bool, str]]:
    """Every descendant of *widget*: its rect in *widget*'s coordinates, whether
    it would show (all ancestors up to *widget* visible-to-it), and its type."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QWidget

    out = []
    for child in widget.findChildren(QWidget):
        at = child.mapTo(widget, QPoint(0, 0))
        g = child.geometry()
        out.append(([at.x(), at.y(), g.width(), g.height()],
                    child.isVisibleTo(widget), type(child).__name__))
    return out


def _findings(window: Any) -> dict[str, str]:
    """``Class.control`` -> what is wrong with it, for every reachable control."""
    found: dict[str, str] = {}
    for cls, controls in _reachable().items():
        if cls in _NOT_PORTED or not controls:
            continue
        ours = _ours(_widget(window, _WHERE[cls]))
        for c in controls:
            # Resized at runtime: still where the designer put it, any size.
            same = ((lambda o: o[0][:2] == c["rect"][:2]) if c["resized_at"]
                    else (lambda o: o[0] == c["rect"]))
            at = [o for o in ours if same(o)]
            key = f"{cls}.{c['name']}"
            if c["visibility"] == "visible" and not at and not c["moved_at"]:
                found[key] = f"missing: nothing of ours at {c['rect']}"
            elif c["visibility"] == "hidden" and any(o[1] for o in at):
                found[key] = (f"shown: the C# hides it for good, ours shows "
                              f"{[o[2] for o in at if o[1]]} at {c['rect']}")
    return found


def test_the_inventory_can_read_every_visibility() -> None:
    """A loop over ``Controls`` shows or hides without naming a control, so its
    effect is invisible to the inventory -- none may sit in the page."""
    assert _INVENTORY["unreadable_visibility"] == []


def test_every_reachable_class_has_a_home() -> None:
    reachable = {cls for cls, cs in _reachable().items() if cs}
    assert reachable - set(_WHERE) - set(_NOT_PORTED) == set()
    assert set(_WHERE) & set(_NOT_PORTED) == set()


def test_every_mapped_widget_exists(window: Any) -> None:
    from PySide6.QtWidgets import QWidget

    for cls, path in _WHERE.items():
        assert isinstance(_widget(window, path), QWidget), (cls, path)


def test_the_lcd_page_matches_the_windows_app(window: Any) -> None:
    found = _findings(window)

    new = {k: v for k, v in found.items() if k not in _KNOWN}
    fixed = sorted(set(_KNOWN) - set(found))
    assert not new, (
        "controls the Windows app has that ours does not match -- port them, "
        "or add a scoped:/gap: row to _KNOWN with the reason:\n"
        + "\n".join(f"  {k}: {v}" for k, v in sorted(new.items())))
    assert not fixed, f"fixed -- delete these _KNOWN rows: {fixed}"


def test_every_known_row_is_tagged_and_gaps_only_go_down() -> None:
    for key, reason in {**_KNOWN, **_NOT_PORTED}.items():
        assert reason.startswith(("scoped:", "gap:")), key
    rows = {**_KNOWN, **_NOT_PORTED}.values()
    assert sum(r.startswith("gap:") for r in rows) == GAPS

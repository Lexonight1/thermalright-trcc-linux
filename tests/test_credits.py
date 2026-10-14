"""The open-source thanks: one list (``core.credits``), every UI shows it,
and the README names the same projects."""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.credits import CREDITS

from .conftest import FakePlatform

_README = Path(__file__).resolve().parents[1] / "README.md"
#: Thanked in the README on its own: the reference app, not open source.
_NOT_OPEN_SOURCE = {"Thermalright TRCC"}


def _readme_projects() -> list[str]:
    """Each "Built on the Work of" bullet's project names -- the bold ones
    before its dash (a bullet may name two: macmon and Stats)."""
    text = _README.read_text(encoding="utf-8")
    section = text.split("## Built on the Work of", 1)[1].split("\n## ", 1)[0]
    names = []
    for bullet in re.findall(r"^- (.*)$", section, re.MULTILINE):
        head = bullet.split(" — ", 1)[0]
        names += re.findall(r"\*\*\[?([^\]*]+)\]?(?:\([^)]*\))?\*\*", head)
    return names


def test_the_readme_and_the_list_name_the_same_projects_in_order() -> None:
    """Two places say who TRCC thanks; this keeps them saying the same."""
    readme = [n for n in _readme_projects() if n not in _NOT_OPEN_SOURCE]
    assert readme == [c.name for c in CREDITS]


def test_the_control_centre_shows_every_project(qtbot, fake_platform: FakePlatform) -> None:
    """The gui: under the graphics card, beside its "About" label."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.ui.gui.uc_about import UCAbout

    widget = UCAbout(app=App(platform=fake_platform, renderer=QtRenderer()))
    qtbot.addWidget(widget)
    assert widget._credits.text() == " · ".join(c.name for c in CREDITS)
    assert widget._credits.toolTip().splitlines() == [c.line for c in CREDITS]


def test_the_about_label_is_one_of_the_control_centre_rows() -> None:
    """Translated in every language, like the rows above it."""
    from trcc.core.i18n import ABOUT_CREDITS_POS, ABOUT_GPU_POS, TRANSLATIONS

    assert all(table.get("About") for table in TRANSLATIONS.values())
    assert (ABOUT_CREDITS_POS[0], ABOUT_CREDITS_POS[2:]) == (ABOUT_GPU_POS[0],
                                                             ABOUT_GPU_POS[2:])
    assert ABOUT_CREDITS_POS[1] > ABOUT_GPU_POS[1]


def test_the_window_labels_the_about_row_like_its_siblings(
        qtbot, fake_platform: FakePlatform) -> None:
    """The row label is one of the window's translated labels -- the same
    list ("Graphics card" and the rest) it is drawn and retranslated from."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.ui.gui.trcc_app import TRCCApp

    window = TRCCApp(app=App(platform=fake_platform, renderer=QtRenderer()))
    qtbot.addWidget(window)
    rows = {key: label for label, key in window._i18n_labels
            if label.parent() is window.uc_about}
    assert ("Graphics card" in rows, "About" in rows) == (True, True)
    assert rows["About"].x() == rows["Graphics card"].x()
    assert rows["About"].y() > rows["Graphics card"].y()
    window.close()


def test_qtgui_s_about_panel_shows_every_project(qtbot, fake_platform: FakePlatform) -> None:
    from trcc.adapters.render.qt import QtRenderer
    from trcc.ui.bus_bridge import BusBridge
    from trcc.ui.qtgui.panels.about_panel import AboutPanel

    app = App(platform=fake_platform, renderer=QtRenderer())
    panel = AboutPanel(app, BusBridge(app.events))
    qtbot.addWidget(panel)
    assert panel._credits.text().splitlines() == [c.line for c in CREDITS]


def test_the_cli_prints_every_project(cli_runner) -> None:
    from trcc.ui.cli.main import app

    result = cli_runner.invoke(app, ["credits"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[1:] == [f"  {c.line}" for c in CREDITS]


@pytest.fixture
def client(fake_platform: FakePlatform) -> Iterator[object]:
    from trcc.adapters.render.qt import QtRenderer
    from trcc.ui.api.main import build_app

    from .conftest import loopback_client

    with loopback_client(build_app(trcc=App(platform=fake_platform,
                                            renderer=QtRenderer()))) as c:
        yield c


def test_the_api_serves_every_project(client) -> None:
    response = client.get("/system/credits")  # type: ignore[attr-defined]
    assert response.status_code == 200
    assert response.json() == [{"name": c.name, "author": c.author, "licence": c.licence,
                                "used_for": c.used_for} for c in CREDITS]

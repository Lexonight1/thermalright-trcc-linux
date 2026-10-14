"""The About: who TRCC is inspired by, what it does, and the licensing of every
work it uses -- one source (``core.credits``) every UI shows, with the README
naming the same projects TRCC is built from."""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.credits import (
    BUILT_FROM,
    GROUPS,
    INSPIRATIONS,
    WE_LOVE,
    WHAT_TRCC_DOES,
    Credit,
    inspired_line,
)

from .conftest import FakePlatform

_ROOT = Path(__file__).resolve().parents[1]
_README = _ROOT / "README.md"
#: Thanked in the README on its own: the reference app, not open source.
_NOT_OPEN_SOURCE = {"Thermalright TRCC"}
_ALL = [credit for _title, credits in GROUPS for credit in credits]


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


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ── the data ─────────────────────────────────────────────────────────

def test_the_readme_and_built_from_name_the_same_projects_in_order() -> None:
    """Two places say what TRCC is built from; this keeps them the same."""
    readme = [n for n in _readme_projects() if n not in _NOT_OPEN_SOURCE]
    assert readme == [c.name for c in BUILT_FROM]


def test_the_people_are_in_the_maintainer_s_order_each_with_a_picture() -> None:
    from trcc.ui.gui.uc_thanks import PICTURES

    assert [p.name for p in INSPIRATIONS] == [
        "God", "Jesus", "Charles Babbage", "Ada Lovelace", "Andrew S. Tanenbaum",
        "Richard Stallman", "Linus Torvalds"]
    assert inspired_line() == (
        "Inspired by God, Jesus, Charles Babbage, Ada Lovelace, Andrew S. Tanenbaum, "
        "Richard Stallman and Linus Torvalds.")
    assert all((PICTURES / p.picture).is_file() for p in INSPIRATIONS)
    assert all(p.did and p.picture_by and p.picture_licence
               and p.picture_page.startswith("https://commons.wikimedia.org/wiki/File:")
               for p in INSPIRATIONS)


def test_every_dependency_is_credited() -> None:
    """A library added to pyproject.toml without regenerating
    (dev/tools/gen_credits.py) would run uncredited."""
    tomllib = pytest.importorskip("tomllib")      # 3.11+; 3.12 carries the gate
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {_key(re.split(r"[ ;<>=!\[~(]", d, maxsplit=1)[0])
                for d in project["project"]["dependencies"]}
    credited = {_key(c.name) for c in _ALL}
    assert declared - credited == set()


@pytest.mark.parametrize(("licence", "url"), [
    ("MIT", "https://spdx.org/licenses/MIT.html"),
    ("MIT License", "https://spdx.org/licenses/MIT.html"),
    ("Apache-2.0 OR BSD-3-Clause", "https://spdx.org/licenses/Apache-2.0.html"),
    ("GPL-2.0-or-later, with the Bootloader Exception",
     "https://spdx.org/licenses/GPL-2.0-or-later.html"),
    ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/"),
    ("BSD License", ""),                      # which BSD is not said: no guess
    ("Public domain", ""),
    ("", ""),
])
def test_a_licence_links_to_its_own_text(licence: str, url: str) -> None:
    assert Credit("x", "y", licence).licence_url == url


# ── every UI ─────────────────────────────────────────────────────────

def test_the_about_page_shows_the_people_then_the_licensing(qtbot) -> None:
    from trcc.ui.gui.uc_thanks import UCThanks

    page = UCThanks()
    qtbot.addWidget(page)
    # A table of two cells a person: picture and name, then their sentences.
    assert [n.text() for n in page.names] == [p.name for p in INSPIRATIONS]
    assert [w.text() for w in page.people] == [p.did for p in INSPIRATIONS]
    assert all(n.width() == page.names[0].width() for n in page.names)
    assert (page.what.text(), page.love.text()) == (WHAT_TRCC_DOES, WE_LOVE)
    assert len(page.entries) == len(_ALL)
    for entry, credit in zip(page.entries, _ALL, strict=True):
        assert (f"href='{credit.licence_url}'" in entry.text()) is bool(
            credit.licence_url and credit.licence)
    assert all(e.openExternalLinks() for e in page.entries)


def test_the_control_center_bar_names_it_and_opens_about(
        qtbot, fake_platform: FakePlatform) -> None:
    """The Control Center page's top bar: its name, and About beside it --
    both translated like every label; About opens the About page."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.ui.gui.trcc_app import TRCCApp

    window = TRCCApp(app=App(platform=fake_platform, renderer=QtRenderer()))
    qtbot.addWidget(window)
    cc = window.uc_about
    keys = {key for _label, key in window._i18n_labels}
    assert {"Control Center", "About"} <= keys
    assert (cc.title_label.text(), cc.about_link.text()) == ("Control Center", "About")
    window._show_view('about')
    cc.about_link.click()
    assert (window.uc_thanks.isVisibleTo(window), cc.isVisibleTo(window)) == (True, False)
    window.close()


def test_qtgui_s_about_panel_shows_everything(qtbot, fake_platform: FakePlatform) -> None:
    from trcc.adapters.render.qt import QtRenderer
    from trcc.ui.bus_bridge import BusBridge
    from trcc.ui.qtgui.panels.about_panel import AboutPanel

    app = App(platform=fake_platform, renderer=QtRenderer())
    panel = AboutPanel(app, BusBridge(app.events))
    qtbot.addWidget(panel)
    text = panel._credits.toPlainText()
    for needle in ([p.name for p in INSPIRATIONS] + [WHAT_TRCC_DOES, WE_LOVE]
                   + [title for title, _ in GROUPS] + [c.name for c in _ALL]):
        assert needle in text


def test_trcc_thanks_prints_everything(cli_runner) -> None:
    from trcc.ui.cli.main import app

    result = cli_runner.invoke(app, ["thanks"])
    lines = result.output.splitlines()
    assert result.exit_code == 0, result.output
    assert lines[1:9] == ["  Inspired by"] + [f"    {p.name} -- {p.did}"
                                               for p in INSPIRATIONS]
    assert (f"  {WHAT_TRCC_DOES}" in lines, f"  {WE_LOVE}" in lines) == (True, True)
    for title, credits in GROUPS:
        assert f"  {title}" in lines
        for credit in credits:
            assert any(line.startswith(f"    {credit.name}") for line in lines)
            assert not credit.licence_url or f"        {credit.licence_url}" in lines


@pytest.fixture
def client(fake_platform: FakePlatform) -> Iterator[object]:
    from trcc.adapters.render.qt import QtRenderer
    from trcc.ui.api.main import build_app

    from .conftest import loopback_client

    with loopback_client(build_app(trcc=App(platform=fake_platform,
                                            renderer=QtRenderer()))) as c:
        yield c


def test_the_api_serves_everything(client) -> None:
    body = client.get("/system/credits").json()  # type: ignore[attr-defined]
    assert [p["name"] for p in body["inspired_by"]] == [p.name for p in INSPIRATIONS]
    assert (body["what_trcc_does"], body["we_love"]) == (WHAT_TRCC_DOES, WE_LOVE)
    assert [g["title"] for g in body["groups"]] == [title for title, _ in GROUPS]
    assert [c["name"] for g in body["groups"] for c in g["credits"]] == [
        c.name for c in _ALL]

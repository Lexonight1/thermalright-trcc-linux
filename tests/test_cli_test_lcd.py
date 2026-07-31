"""``trcc display test-lcd`` — the terminal's preview, previously untested.

It used to reach for the device, the theme, the sensors, the DisplayService
*and* the renderer (five reaches, all fatal under ``TRCC_DAEMON=1``).  Now it
dispatches ``BuildPreview(sample_cols=…)`` and formats the grid it gets back,
so the only thing left in the UI is the ANSI half-block art.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice
from trcc.core.models import Theme

from .mock_platform import MockPlatform

_SPEC = {"type": "lcd", "vid": "87ad", "pid": "70db",
         "resolution": "854x480", "pm": 11, "sub": 5}
_KEY = "87ad:70db"


@pytest.fixture
def lcd_app(tmp_path: Path) -> Iterator[App]:
    """Wire the CLI's cached App to a connected 854x480 mock + real renderer."""
    from trcc.ui.cli import _ctx

    _ctx.set_platform(MockPlatform([_SPEC], tmp_path))
    _ctx.set_renderer(QtRenderer())
    app = _ctx.get_app()
    app.attach(0x87AD, 0x70DB)
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    yield app


def _cli():
    from trcc.ui.cli.main import app
    return app


def test_test_lcd_reports_an_unattached_device(
    cli_runner: CliRunner, lcd_app: App,
) -> None:
    result = cli_runner.invoke(_cli(), ["display", "test-lcd", "dead:beef"])

    assert result.exit_code == 1
    assert "dead:beef" in result.output


def test_test_lcd_asks_for_a_theme_when_none_is_loaded(
    cli_runner: CliRunner, lcd_app: App,
) -> None:
    result = cli_runner.invoke(_cli(), ["display", "test-lcd", _KEY])

    assert result.exit_code == 1
    assert "No active theme" in result.output


def test_test_lcd_prints_half_block_art_at_the_panel_aspect(
    cli_runner: CliRunner, lcd_app: App, tmp_path: Path,
) -> None:
    """20 columns of an 854x480 panel samples 12 rows, and each terminal line
    packs two of them — 6 lines of art, not the square block the old
    renderer-side sampling produced."""
    lcd_app.active_themes[_KEY] = Theme(
        path=tmp_path / "theme", name="t",
        resolution=(854, 480), config={"elements": []},
    )

    result = cli_runner.invoke(
        _cli(), ["display", "test-lcd", _KEY, "--cols", "20"],
    )

    assert result.exit_code == 0
    art = [ln for ln in result.output.splitlines() if "▀" in ln]
    assert len(art) == 6
    assert all(ln.count("▀") == 20 for ln in art)

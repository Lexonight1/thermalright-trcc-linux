"""``trcc system dashboard-{add,delete,rename,bind}`` — the CLI can author.

Found by module 10 of the ``ui/gui`` hand walk: gui and the API could add,
delete and rename a sensor-dashboard panel, qtgui could only rebind a row, and
the CLI could do none of it -- it could only print the layout and persist an
auto-map.

**No gate could see that.**  All four faces dispatch ``SetSensorDashboard``,
so ``ui_contract`` scored parity; the Command has exactly ONE field,
``panels``, which all four faces pass, so ``test_command_field_reach`` had no
asymmetry to record.  The gap was in the CONTENT of a tuple.  These tests
assert on the PERSISTED layout read back through the bus, because that is the
only thing that distinguishes "the face built the right tuple" from "the face
dispatched the Command".
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from trcc.app import App
from trcc.core.commands import GetSensorDashboard, SetSensorDashboard
from trcc.core.models import PanelConfig, SensorBinding

from .conftest import FakePlatform


@pytest.fixture
def cli_app(tmp_home: Path) -> Iterator[App]:
    """Point the CLI's cached App at a temp home — never the user's config.

    Driving the real CLI during development wrote to ``~/.trcc`` and had to be
    undone by hand.  The fixture is the guard: ``tmp_home`` redirects
    ``config_dir``, so ``system_config.json`` is written inside the test.
    """
    from trcc.ui.cli import _ctx

    _ctx.set_platform(FakePlatform(tmp_home))
    yield _ctx.get_app()


def _cli():
    from trcc.ui.cli.main import app
    return app


def _panels(app: App) -> list[PanelConfig]:
    return list(app.dispatch(GetSensorDashboard()).panels)


def _names(app: App) -> list[str]:
    return [p.name for p in _panels(app)]


def _run(runner: CliRunner, *args: str):
    return runner.invoke(_cli(), ["system", *args])


# ── add ──────────────────────────────────────────────────────────────────


def test_add_appends_the_models_definition_of_a_panel(
    cli_runner: CliRunner, cli_app: App,
) -> None:
    """The SAME factory gui and qtgui use — not a third literal."""
    before = len(_panels(cli_app))

    result = _run(cli_runner, "dashboard-add", "--name", "Water loop")

    assert result.exit_code == 0, result.output
    panels = _panels(cli_app)
    assert len(panels) == before + 1
    assert panels[-1].name == "Water loop"
    assert panels[-1].category_id == PanelConfig.CUSTOM_CATEGORY
    assert [b.label for b in panels[-1].sensors] == [
        "Sensor 1", "Sensor 2", "Sensor 3", "Sensor 4",
    ]
    assert all(b.sensor_id == "" for b in panels[-1].sensors)


def test_add_defaults_the_name(cli_runner: CliRunner, cli_app: App) -> None:
    assert _run(cli_runner, "dashboard-add").exit_code == 0
    assert _names(cli_app)[-1] == "Custom"


# ── the listing is what makes an index usable ────────────────────────────


def test_the_listing_prints_the_index_the_other_verbs_take(
    cli_runner: CliRunner, cli_app: App,
) -> None:
    """Names are NOT unique — every added panel is "Custom" until renamed —
    so position is the only unambiguous handle, and it has to be printed."""
    _run(cli_runner, "dashboard-add")
    _run(cli_runner, "dashboard-add")

    out = _run(cli_runner, "dashboard").output
    names = _names(cli_app)
    assert names.count("Custom") == 2, "the ambiguity this test is about"
    for index, name in enumerate(names):
        assert f"  {index}  [" in out, out
        assert name in out


# ── rename ───────────────────────────────────────────────────────────────


def test_rename_changes_the_named_panel_only(
    cli_runner: CliRunner, cli_app: App,
) -> None:
    before = _names(cli_app)

    assert _run(cli_runner, "dashboard-rename", "1", "Graphics").exit_code == 0

    after = _names(cli_app)
    assert after[1] == "Graphics"
    assert after[:1] + after[2:] == before[:1] + before[2:]


def test_rename_refuses_a_blank_name(
    cli_runner: CliRunner, cli_app: App,
) -> None:
    before = _names(cli_app)
    result = _run(cli_runner, "dashboard-rename", "0", "   ")
    assert result.exit_code != 0
    assert _names(cli_app) == before


# ── delete ───────────────────────────────────────────────────────────────


def test_delete_removes_that_panel(
    cli_runner: CliRunner, cli_app: App,
) -> None:
    before = _names(cli_app)

    assert _run(cli_runner, "dashboard-delete", "2").exit_code == 0

    assert _names(cli_app) == before[:2] + before[3:]


def test_delete_refuses_an_index_that_is_not_there(
    cli_runner: CliRunner, cli_app: App,
) -> None:
    """And says what the valid range IS — a bare refusal costs a round trip."""
    before = _names(cli_app)
    result = _run(cli_runner, "dashboard-delete", "99")

    assert result.exit_code != 0
    assert "99" in result.output
    assert f"0..{len(before) - 1}" in result.output
    assert _names(cli_app) == before


def test_deleting_the_last_panel_is_refused_by_the_COMMAND(
    cli_runner: CliRunner, cli_app: App,
) -> None:
    """The CLI adds no guard of its own — the Command owns the rule.

    An empty layout makes the next read fall back to defaults, a wipe dressed
    up as a write.  ``SetSensorDashboard`` refuses it, so the CLI surfaces the
    refusal and exits non-zero rather than restating the policy.
    """
    cli_app.dispatch(SetSensorDashboard(panels=(
        PanelConfig(category_id=1, name="Only",
                    sensors=[SensorBinding("Row", "cpu:temp", "°C")]),
    )))
    assert _names(cli_app) == ["Only"]

    result = _run(cli_runner, "dashboard-delete", "0")

    assert result.exit_code != 0
    assert "at least one panel" in result.output
    assert _names(cli_app) == ["Only"]


# ── bind ─────────────────────────────────────────────────────────────────


def test_bind_takes_the_unit_from_the_sensor(
    cli_runner: CliRunner, cli_app: App,
) -> None:
    """The GUIs bind by PICKING, so the unit comes from the sensor there too.

    A caller-supplied unit is a second source for a fact the enumerator
    already owns.
    """
    from trcc.core.commands import ListSensors

    known = cli_app.dispatch(ListSensors()).sensors
    sensor = next(s for s in known if s.unit)

    assert _run(cli_runner, "dashboard-bind", "0", "1", sensor.sensor_id
                ).exit_code == 0

    binding = _panels(cli_app)[0].sensors[1]
    assert binding.sensor_id == sensor.sensor_id
    assert binding.unit == sensor.unit


def test_bind_refuses_a_sensor_this_machine_does_not_have(
    cli_runner: CliRunner, cli_app: App,
) -> None:
    before = _panels(cli_app)[0].sensors[0].sensor_id
    result = _run(cli_runner, "dashboard-bind", "0", "0", "nonsense:sensor")

    assert result.exit_code != 0
    assert "nonsense:sensor" in result.output
    assert _panels(cli_app)[0].sensors[0].sensor_id == before


def test_bind_refuses_a_row_that_is_not_there_and_writes_NOTHING(
    cli_runner: CliRunner, cli_app: App,
) -> None:
    """The refusal happens mid-edit, AFTER the layout was read.

    This asserts the OUTCOME only.  It does NOT prove the write-after-``yield``
    ordering: the row guard runs before the assignment, so even a ``finally``
    would write back an unchanged layout.  The ordering itself is pinned
    directly below, on the helper.
    """
    from trcc.core.commands import ListSensors

    sensor = cli_app.dispatch(ListSensors()).sensors[0]
    before = [b.sensor_id for b in _panels(cli_app)[0].sensors]

    result = _run(cli_runner, "dashboard-bind", "0", "9", sensor.sensor_id)

    assert result.exit_code != 0
    assert "no row 9" in result.output
    assert [b.sensor_id for b in _panels(cli_app)[0].sensors] == before


# ── the helper's own contract ────────────────────────────────────────────


def test_a_failed_edit_persists_nothing_even_after_mutating(
    cli_app: App,
) -> None:
    """``_dashboard_edit`` writes AFTER its ``yield``, never in a ``finally``.

    Every command today guards before it mutates, so no CLI path can reach a
    half-made change — which is exactly why this is asserted on the helper
    instead of through one.  A future verb that validates late would silently
    persist the wreckage, and the test that was supposed to catch that passed
    against a ``finally`` because the path it drove had nothing to lose.

    MUTATION CHECK: wrap the ``yield`` in ``try`` / ``finally`` with the
    dispatch inside the ``finally`` and this fails on the renamed panel.
    """
    from trcc.ui.cli.system import _dashboard_edit

    before = _names(cli_app)

    with pytest.raises(RuntimeError, match="late refusal"):
        with _dashboard_edit() as panels:
            panels[0].name = "WRECKAGE"      # mutate FIRST
            panels.append(PanelConfig.custom("Also wreckage"))
            raise RuntimeError("late refusal")

    assert _names(cli_app) == before

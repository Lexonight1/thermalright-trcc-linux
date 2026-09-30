"""Every face that dispatches an overlay-element Command must carry every field.

``core/models.py`` states that the element field vocabulary is restated at four
boundaries -- the domain object, the Result view, the Commands and the API
request bodies -- and single-sources the DEFAULTS so they cannot drift.  The
FIELD LIST was never gated, and it had already drifted: ``font`` reached the
domain object, the Commands, ``Settings``, ``element_family`` and
``draw_text`` -- every layer beneath the faces, each one gated by
``test_overlay_font_family.py`` -- while three of the four faces could not
produce it.  Driven rather than read, before this file existed:

* ``POST /devices/{key}/display/overlay-elements`` with ``font`` came back
  ``font=''``, and with ``show_unit: false`` came back ``show_unit=True`` --
  a field the request model DECLARED and the route never forwarded.
* ``trcc display overlay-add`` had ``--bold``, ``--italic``, ``--show-unit``
  and no ``--font``.
* qtgui's element dialog had Weight and Slant rows and no family.

``dev/tools/ui_contract.py`` could not see any of it: it measures WHICH
Commands a face dispatches, and all four score equal on ``AddOverlayElement``.
The difference is which FIELD the payload carries, so that is what this gates.

**The scope is DERIVED** -- from ``dataclasses.fields`` on the Commands
themselves, so a field added to a Command widens this gate on its own rather
than needing someone to remember.  A face that does not dispatch the Command
contributes no site and is not measured (``ui/gui`` edits through
``SetOverlayConfig``), which is the honest rule: this asks "does the payload
you build carry the whole vocabulary", not "does every skin have every verb".

MUTATION CHECK -- drop ``font=`` from either API dispatch site, either CLI
dispatch site or either qtgui dispatch site and
``test_every_face_that_dispatches_carries_the_whole_vocabulary`` must name that
face and that field.  Drop ``font`` from an API request model and
``test_the_api_request_models_declare_the_whole_vocabulary`` must fail.  Both
were confirmed to fail before this file was committed.
"""
from __future__ import annotations

import ast
import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from trcc.app import App
from trcc.core.commands import AddOverlayElement, UpdateOverlayElement

from .conftest import loopback_client
from .test_api_routes import _SmokeRenderer

_UI_ROOT = Path(__file__).resolve().parents[1] / "src" / "trcc" / "ui"
_FACES = ("api", "cli", "gui", "qtgui")

#: Routing, not vocabulary: ``key`` names the device and ``element_id``
#: addresses the element.  Every other field describes the element itself.
_ROUTING = frozenset({"key", "element_id"})

_COMMANDS = (AddOverlayElement, UpdateOverlayElement)


def _vocabulary(command: type) -> frozenset[str]:
    """The element fields a face must be able to populate on ``command``."""
    return frozenset(
        f.name for f in dataclasses.fields(command) if f.name not in _ROUTING
    )


def _keywords_by_command(face: str) -> dict[str, set[str]]:
    """Union of keyword names this face passes to each overlay Command.

    Union across sites rather than per-site: a face may build the payload in
    more than one place (``cli/theme.py`` seeds elements from a ``--metric``
    spec with ``**kwargs``, which names nothing and so contributes nothing),
    and the question is whether the face as a whole can express the field.
    """
    wanted = {c.__name__ for c in _COMMANDS}
    found: dict[str, set[str]] = {}
    for path in sorted((_UI_ROOT / face).rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if name not in wanted:
                continue
            found.setdefault(name, set()).update(
                kw.arg for kw in node.keywords if kw.arg is not None
            )
    return found


@pytest.fixture
def api_client(fake_platform) -> Iterator[TestClient]:
    """The real FastAPI app, built exactly as ``test_api_routes`` builds it."""
    from trcc.ui.api.main import build_app

    trcc = App(platform=fake_platform, renderer=_SmokeRenderer())
    with loopback_client(build_app(trcc=trcc)) as client:
        yield client


@pytest.mark.parametrize("face", _FACES)
@pytest.mark.parametrize("command", _COMMANDS, ids=lambda c: c.__name__)
def test_every_face_that_dispatches_carries_the_whole_vocabulary(
    face: str, command: type,
) -> None:
    passed = _keywords_by_command(face).get(command.__name__)
    if passed is None:
        pytest.skip(f"{face} does not dispatch {command.__name__}")
    missing = _vocabulary(command) - passed
    assert not missing, (
        f"{face} builds {command.__name__} without "
        f"{sorted(missing)} — the field is on the Command and reaches the "
        f"renderer, so a {face} user cannot set what every other layer "
        f"already supports.  Pass it at the dispatch site (and give the face "
        f"a control for it)."
    )


def test_the_gui_carries_the_whole_vocabulary_through_config_fields() -> None:
    """The gate above skips the gui, and must not be read as a pass for it.

    Since 2026-09-30 the gui sends one element edit at a time as
    ``_send_edit(Command, element_id=..., **fields)`` with the fields from
    ``config_fields(cell)`` -- a call that names no keyword, so the syntax
    gate cannot see it.  This checks that path: the gui dispatches both
    Commands through ``_send_edit``, and ``config_fields`` over every editor
    mode can express the whole vocabulary.
    """
    from trcc.core.models import OverlayElementConfig, OverlayMode
    from trcc.ui.presentation.overlay_serialization import config_fields

    source = (_UI_ROOT / "gui" / "uc_theme_setting.py").read_text()
    sent_through = {
        node.args[0].id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_send_edit" and node.args
        and isinstance(node.args[0], ast.Name)
    }
    assert {c.__name__ for c in _COMMANDS} <= sent_through, sent_through

    cells = [OverlayElementConfig(mode=mode, main_count=0, sub_count=1)
             for mode in (OverlayMode.CUSTOM, OverlayMode.TIME, OverlayMode.DATE,
                          OverlayMode.WEEKDAY, OverlayMode.HARDWARE)]
    carried: set[str] = set()
    for cell in cells:
        fields = config_fields(cell)
        assert fields is not None, cell.mode
        carried.update(fields)
    for command in _COMMANDS:
        missing = _vocabulary(command) - carried
        assert not missing, f"the gui cannot express {sorted(missing)} on {command.__name__}"


def test_at_least_two_faces_are_actually_measured() -> None:
    """The gate above SKIPS a face with no site, so prove it measures some.

    Without this, deleting every dispatch site in the tree would turn the
    parametrized gate into four skips and stay green.
    """
    measured = [f for f in _FACES if _keywords_by_command(f)]
    assert len(measured) >= 2, (
        f"only {measured} dispatch an overlay-element Command — the gate "
        f"above is skipping almost everything and proving nothing"
    )


def test_the_api_request_models_declare_the_whole_vocabulary() -> None:
    """The route can only forward what its request model accepts."""
    from trcc.ui.api.schemas import (
        OverlayElementAddRequest,
        OverlayElementSchema,
        OverlayElementUpdateRequest,
    )

    for model, command in (
        (OverlayElementAddRequest, AddOverlayElement),
        (OverlayElementUpdateRequest, UpdateOverlayElement),
        (OverlayElementSchema, AddOverlayElement),
    ):
        missing = _vocabulary(command) - set(model.model_fields)
        assert not missing, (
            f"{model.__name__} omits {sorted(missing)} — a client cannot "
            f"send a field the model does not declare"
        )


def test_the_api_round_trips_a_font_and_a_hidden_unit(
    api_client: TestClient,
) -> None:
    """The gesture, end to end, read back off the Result.

    ``show_unit`` is here because it is the field that WAS declared and
    dropped: the structural gate above would not have caught a model that
    accepts a value the route then ignores.
    """
    resp = api_client.post(
        "/devices/0402:3922/display/overlay-elements",
        json={"type": "metric", "metric": "cpu:temp",
              "font": "Courier New", "show_unit": False},
    )
    assert resp.status_code == 200
    element = resp.json()["element"]
    assert element["font"] == "Courier New"
    assert element["show_unit"] is False

    patched = api_client.patch(
        f"/devices/0402:3922/display/overlay-elements/{element['id']}",
        json={"font": "DejaVu Sans"},
    )
    assert patched.status_code == 200
    assert patched.json()["element"]["font"] == "DejaVu Sans"


def test_the_cli_round_trips_a_font(cli_runner: CliRunner, cli_app) -> None:
    """``--font`` reaches the persisted element, not just the help text."""
    from trcc.ui.cli.main import app

    added = cli_runner.invoke(
        app,
        ["display", "overlay-add", "0402:3922", "text",
         "--text", "CPU", "--font", "Courier New"],
    )
    assert added.exit_code == 0
    elements = cli_app.settings.for_device("0402:3922").user_overlay_elements
    assert [e.font for e in elements] == ["Courier New"]

    updated = cli_runner.invoke(
        app,
        ["display", "overlay-update", "0402:3922", elements[0].id,
         "--font", "DejaVu Sans"],
    )
    assert updated.exit_code == 0
    elements = cli_app.settings.for_device("0402:3922").user_overlay_elements
    assert [e.font for e in elements] == ["DejaVu Sans"]

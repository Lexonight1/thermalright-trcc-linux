"""Every copy of the overlay element's fields matches ``OverlayElement``.

The field list -- names and defaults -- is written out wherever an element
crosses a boundary: the Result entry, the Add and Update Commands, three API
bodies and two CLI commands.  Each needs its fields declared in its own class
(IPC, pyright, FastAPI and Typer all read them there), so they cannot simply
derive from the spec.  What they can do is be checked against it: a field
added to the spec and missed in one copy, or a default changed in one place,
fails here naming the copy.

That is the shape of the font bug (#291): the DC writer read a key the
elements no longer carried, and nothing compared the two.

MUTATION CHECK -- give the CLI's ``--size`` a default of 17, or drop
``show_unit`` from ``OverlayElementUpdateRequest``: this fails naming it.
"""
from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from typing import Any

import pytest

from trcc.core.commands import AddOverlayElement, UpdateOverlayElement
from trcc.core.models import OverlayElement
from trcc.core.results import OverlayElementEntry
from trcc.ui.api import schemas
from trcc.ui.cli import display

#: A copy declares this field with no default -- stricter than the spec, so
#: there is no default to compare.
REQUIRED = object()


def _spec() -> dict[str, Any]:
    return _of_dataclass(OverlayElement)


def _of_dataclass(cls: type) -> dict[str, Any]:
    return {f.name: REQUIRED if f.default is dataclasses.MISSING else f.default
            for f in dataclasses.fields(cls)}


def _of_model(model: type) -> dict[str, Any]:
    return {name: REQUIRED if info.is_required() else info.default
            for name, info in model.model_fields.items()}


def _of_cli(command: Callable[..., None]) -> dict[str, Any]:
    """A Typer command's parameters, named by their FLAG (``--format``,
    ``--show-unit/--hide-unit``, ``--id``) -- the Python names are free to
    dodge builtins (``fmt``, ``type_``), the flags are what a user types."""
    out: dict[str, Any] = {}
    for name, param in inspect.signature(command).parameters.items():
        info = param.default
        decls = getattr(info, "param_decls", None) or ()
        flag = decls[0].split("/")[0].lstrip("-") if decls else name.rstrip("_")
        field = {"id": "element_id"}.get(flag, flag.replace("-", "_"))
        default = getattr(info, "default", info)
        out[field] = REQUIRED if default is ... or default is inspect.Parameter.empty else default
    return out


def _expected(drop: set[str], add: set[str], *, optional: bool) -> dict[str, Any]:
    spec = {name: (None if optional else default)
            for name, default in _spec().items() if name not in drop}
    return {**spec, **dict.fromkeys(add, REQUIRED)}


#: (copy, its fields, spec fields it leaves out, fields of its own, every
#: field optional -- an Update leaves what it is not given alone).
_COPIES = [
    ("results.OverlayElementEntry", lambda: _of_dataclass(OverlayElementEntry),
     set(), set(), False),
    ("AddOverlayElement", lambda: _of_dataclass(AddOverlayElement),
     {"id"}, {"key", "element_id"}, False),
    ("UpdateOverlayElement", lambda: _of_dataclass(UpdateOverlayElement),
     {"id", "type"}, {"key", "element_id"}, True),
    ("api OverlayElementSchema", lambda: _of_model(schemas.OverlayElementSchema),
     set(), set(), False),
    ("api OverlayElementAddRequest", lambda: _of_model(schemas.OverlayElementAddRequest),
     {"id"}, {"element_id"}, False),
    ("api OverlayElementUpdateRequest",
     lambda: _of_model(schemas.OverlayElementUpdateRequest),
     {"id", "type"}, set(), True),
    ("cli overlay-add", lambda: _of_cli(display.overlay_add),
     {"id"}, {"key", "element_id"}, False),
    ("cli overlay-update", lambda: _of_cli(display.overlay_update),
     {"id", "type"}, {"key", "element_id"}, True),
]


@pytest.mark.parametrize(("name", "read", "drop", "add", "optional"), _COPIES,
                         ids=[c[0] for c in _COPIES])
def test_a_copy_carries_the_spec_fields_with_its_defaults(
    name: str, read: Callable[[], dict[str, Any]], drop: set[str],
    add: set[str], optional: bool,
) -> None:
    got, want = read(), _expected(drop, add, optional=optional)
    assert sorted(got) == sorted(want), (
        f"{name} declares {sorted(set(got) - set(want))} beyond the spec and "
        f"misses {sorted(set(want) - set(got))} -- OverlayElement is the field "
        f"list; every copy follows it")
    differ = {field: (got[field], want[field]) for field in want
              if REQUIRED not in (got[field], want[field])
              and got[field] != want[field]}
    assert not differ, f"{name}: (its default, the spec's) differ -- {differ}"


def test_the_copies_are_found_at_all() -> None:
    """A reader that returned nothing would pass every case above."""
    assert all(len(read()) >= 12 for _name, read, *_ in _COPIES)


def test_a_dict_with_only_an_id_reads_back_as_the_spec_defaults() -> None:
    """``from_dict`` is a copy too: the defaults it fills in for a missing key
    are the spec's, so a DC-parsed element and a constructed one agree."""
    read = dataclasses.asdict(OverlayElement.from_dict({"id": "e1"}))
    spec = dataclasses.asdict(OverlayElement(id="e1"))
    differ = {k: (read[k], spec[k]) for k in spec if read[k] != spec[k]}
    assert not differ, f"from_dict fills (its default, the spec's) -- {differ}"

"""One door for every Command -- the bus's own envelope, in the CLI and the API.

The App's socket already takes ``{"command": "<Name>", "kwargs": {...}}`` for
any registered Command (``ipc.decode_command``), and a module's Commands are
registered the moment its ``core`` loads.  So every UI can reach every
Command -- TRCC's and every module's -- through one door each, with no
per-module code: ``trcc dispatch`` and ``POST /dispatch``.  A UI's own
commands and routes stay as friendly shortcuts; nothing is reachable only
through them.

One rule the API door adds (:func:`carries_files`): a Command with a file
field -- a ``Path``, or raw bytes -- is not taken there.  Its own route checks
the path; a token-holding client on another machine must not name any file
the user can read or write.  The CLI door has no such rule: it is the user,
in their own shell, with the rights they already have.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import logging
import types
import typing
from pathlib import PurePath
from typing import Any

from .. import ipc, modules
from ..core.commands import Command
from ..core.logs import per_frame

log = logging.getLogger(__name__)
#: Called once per field of every Command on a listing: per-item noise.
frame_log = per_frame(__name__)


def known() -> dict[str, type[Command[Any]]]:
    """Every Command the door takes, by name -- TRCC's and each shipped
    module's (their ``core`` loaded, so a CLI process knows them too)."""
    for key in sorted(modules.shipped()):
        modules.load_core(key)
    found = dict(sorted(ipc.COMMAND_TYPES.items()))
    log.debug("known: %d command(s)", len(found))
    return found


def _holds_files(hint: Any, seen: set[type]) -> bool:
    """Whether a value of type *hint* can carry a file: a path or raw bytes,
    anywhere inside it."""
    frame_log.debug("_holds_files: %s", hint)
    if inspect.isclass(hint) and (issubclass(hint, (PurePath, bytes, bytearray))):
        return True
    if inspect.isclass(hint) and dataclasses.is_dataclass(hint):
        if hint in seen:
            return False
        seen.add(hint)
        return any(_holds_files(h, seen) for h in ipc._hints(hint).values())
    origin = typing.get_origin(hint)
    if origin is not None or isinstance(hint, types.UnionType):
        return any(_holds_files(a, seen) for a in typing.get_args(hint))
    return False


def carries_files(cls: type[Command[Any]]) -> bool:
    """Whether *cls* has a file field -- what the API door refuses."""
    files = any(_holds_files(hint, set()) for hint in ipc._hints(cls).values())
    log.debug("carries_files: %s -> %s", cls.__name__, files)
    return files


def describe(cls: type[Command[Any]]) -> dict[str, Any]:
    """*cls* as a listing shows it: its fields and their types, what it says
    it does, and whether it carries files."""
    hints = ipc._hints(cls)
    # Every Command is a frozen dataclass; ``fields`` is told so.
    assert dataclasses.is_dataclass(cls), cls
    fields = {f.name: _type_name(hints.get(f.name)) for f in dataclasses.fields(cls)}
    doc = inspect.getdoc(cls) or ""
    entry = {"command": cls.__name__, "fields": fields,
             "summary": doc.split("\n\n", 1)[0].replace("\n", " "),
             "files": carries_files(cls)}
    log.debug("describe: %s", cls.__name__)
    return entry


def _type_name(hint: Any) -> str:
    """A field's type as text: ``int``, ``str | None``, ``tuple[int, ...]``."""
    name = hint.__name__ if inspect.isclass(hint) else str(hint)
    text = name.replace("typing.", "").replace("pathlib.", "")
    frame_log.debug("_type_name: %s", text)
    return text


def build(name: str, kwargs: dict[str, Any]) -> Command[Any]:
    """The Command *name* with *kwargs* -- ``ValueError`` naming what is wrong:
    an unknown name, an unknown field, or a value its field cannot take."""
    log.info("build: %s %s", name, sorted(kwargs))
    if name not in known():
        raise ValueError(f"Unknown command: {name!r} -- `trcc dispatch --list` "
                         "or GET /dispatch lists them")
    try:
        return ipc.decode_command({"command": name, "kwargs": kwargs})
    except (TypeError, KeyError) as e:
        raise ValueError(f"{name}: {e}") from e


def parse_value(text: str) -> Any:
    """A CLI value: JSON when it is JSON (``5``, ``true``, ``[1, 2]``,
    ``{"a": 1}``), the text itself otherwise (``0416:8001``, ``rainbow``)."""
    try:
        value = json.loads(text)
    except ValueError:
        value = text
    log.debug("parse_value: %r -> %s", text[:40], type(value).__name__)
    return value

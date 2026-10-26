"""What the modules add to a UI -- the one place every face asks.

A module's ``ui/<face>/`` exports fixed names, the way a Django app's
``urls.py`` exports ``urlpatterns``:

============  ==============================================================
face          names
============  ==============================================================
``gui``       ``PAGE`` -- the page its mode tab opens (a ``ModulePage``)
``gui``       ``VIEW`` -- a whole page its sidebar button opens (a ``ModuleView``)
``qtgui``     ``PANEL`` -- its sidebar panel (a ``BasePanel`` with ``LABEL``)
``cli``       ``GROUP`` (the ``trcc`` group, ``""`` for the top) + ``COMMANDS``
``api``       ``ROUTERS`` -- the FastAPI routers it adds
============  ==============================================================

Which modules a UI loads is the App's to say (``ListModules``): one in the
user's folder runs only if the App runs it.  The CLI and the API add every
module the App runs, on or off -- an off module's command still answers, and
the App says it is off.  The windows add only what is on, and follow
``ModuleChanged``.  A module that fails to load is logged and skipped: one
broken module never breaks a window.
"""
from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

from .. import modules

if TYPE_CHECKING:
    from ..core.ports import CommandBus, Platform

log = logging.getLogger(__name__)


def installed() -> list[str]:
    """The modules a UI may load before it has asked the App: those that ship
    with TRCC (and a test's) -- never one from the user's folder."""
    keys = sorted(modules.shipped())
    log.debug("installed: %s", keys)
    return keys


def listed(bus: CommandBus) -> list[str]:
    """Every module the App runs, on or off -- [] when it cannot be asked."""
    from ..core.commands import ListModules

    try:
        result = bus.dispatch(ListModules())
    except Exception:
        log.exception("listed: the App could not be asked for its modules")
        return []
    keys = sorted(m.key for m in result.modules if m.allowed)
    log.info("listed: %s%s", keys, "" if result.ok else f" ({result.message})")
    return keys


def search_user_folder(platform: Platform | None) -> None:
    """This process finds the user's installed modules too -- each UI does,
    so it can load the faces of those the App runs."""
    from .._boot import host_platform

    log.debug("search_user_folder: %s", type(platform).__name__)
    modules.use_folder(host_platform(platform).paths().user_modules_dir())


def contribution(key: str, face: str, name: str,
                 optional: bool = False) -> Any | None:
    """Module *key*'s *name* for *face* (``PAGE`` for ``gui`` ...), or None
    when it adds nothing there -- or failed to load, which is logged.  A
    face that lacks *name* is an author's slip, logged as an error -- unless
    *optional*: the gui's ``PAGE`` and ``VIEW``, either of which a module may
    add without the other."""
    try:
        part = modules.load_face(key, face)
    except Exception:
        log.exception("module %s: its %s part did not load -- skipped", key, face)
        return None
    found = getattr(part, name, None) if part is not None else None
    if part is not None and found is None:
        if optional:
            log.debug("module %s: ui/%s/ adds no %s", key, face, name)
            return None
        if getattr(part.__spec__, "_initializing", False):
            # Asked while the face itself is mid-import (it imported this UI
            # first) -- the flag behind Python's "partially initialized
            # module" error.  Not a fault: the UI asks again before it serves.
            log.debug("module %s: ui/%s/ still loading -- asked later", key, face)
            return None
        log.error("module %s: ui/%s/ exports no %s -- skipped", key, face, name)
    log.debug("contribution: %s %s %s -> %s", key, face, name, found is not None)
    return found


def face_loaded(key: str) -> bool:
    """Whether this process imported any of module *key*'s UI -- so a window
    built from it holds the old code after an update, until reopened."""
    prefix = f"{modules.__name__}.{key}.ui"
    loaded = any(name == prefix or name.startswith(f"{prefix}.") for name in sys.modules)
    log.debug("face_loaded: %s -> %s", key, loaded)
    return loaded


def contributions(face: str, name: str,
                  keys: list[str] | None = None) -> dict[str, Any]:
    """*name* for *face* from each module in *keys* (every installed one by
    default) that adds one, by key."""
    found = {key: c for key in (installed() if keys is None else keys)
             if (c := contribution(key, face, name)) is not None}
    log.info("contributions: %s %s -> %s", face, name, sorted(found))
    return found

"""Hello's root -- the module skeleton.  Copy it with ``dev/tools/new_module.py``.

It is a real, working module: the tests load it through the same catalog as
every built-in, so if the module API changes, this breaks in CI instead of
quietly going stale.  doc/GUIDE_MODULES.md walks through it.

Two steps, at different times:

* ``_enable`` / ``_disable`` -- REGISTER: hook into the app.  Runs in every
  App, a one-shot CLI command's too, so never touch hardware or start a
  thread here.
* ``start`` / ``stop`` -- RUN: hardware and threads.  Only inside a session.

Everything goes through ``host``, never the App.  ``host`` gives only what
``module.json`` declares under "capabilities" -- here, "events".  Its
Commands (``core/``) run against this class, so they reach the App only
through the same host.
"""
from __future__ import annotations

import logging

from trcc.adapters.modules import BaseModule
from trcc.core.events import ModuleChanged
from trcc.core.ports import ModuleHost

log = logging.getLogger(__name__)


class HelloModule(BaseModule, key="hello"):
    """Hears the Modules page and says so in the log."""

    # Shown when this class is used without its module.json (a test's);
    # module.json is what the Modules page reads.
    title, summary = "Hello", "The smallest module there is."

    def pre_enable(self, host: ModuleHost) -> None:
        """Can it run here?  Refuse in PLAIN WORDS with a next step -- the
        tile shows this sentence to the user:

            raise ModuleUnavailable("Needs working graphics drivers -- see the guide")
        """
        log.info("hello: pre_enable")

    def _enable(self, host: ModuleHost) -> None:
        """Register.  A subscription made here is taken back automatically
        when the module is switched off -- no need to undo it by hand."""
        log.info("hello: enable")
        host.subscribe(ModuleChanged, self._heard)

    def start(self, host: ModuleHost) -> None:
        """The session has begun: hardware and threads start here."""
        log.info("hello: start")

    def stop(self, host: ModuleHost) -> None:
        """The session ends, or the module is switched off: stop them."""
        log.info("hello: stop")

    def _disable(self, host: ModuleHost) -> None:
        """Take out anything ``_enable`` registered that the host does not
        track (subscriptions it does)."""
        log.info("hello: disable")

    def _heard(self, event: ModuleChanged) -> None:
        """Every module switched on or off, in any window."""
        log.info("hello: %s was switched %s", event.key,
                 "on" if event.enabled else "off")

    def greet(self, name: str) -> str:
        """What ``Greet`` answers -- a module's work lives in the module."""
        log.info("hello: greeting %s", name)
        return f"Hello, {name}!"

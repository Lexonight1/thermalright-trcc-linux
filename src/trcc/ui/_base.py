"""``UserInterface`` — the port every UI face implements, and its registry.

The third registry, beside ``PLATFORMS`` (OS) and ``DEVICES`` (wire).  UI was
always the third adapter family — "each adapter family (OS, Device, UI) should
be one abstract base class = the interface, with factory children that share
the same method NAMES but supply different INTERNALS" — and it is the one that
never got the treatment.  CLI, API, GUI, qtgui and the daemon were five
hand-written bring-up sequences with five different signatures.

**Why a bus and not a convention.**  ``METHOD_UI.md`` already declared the
launch contract (``run(platform) -> int``), but a contract that lives only in
prose is a contract nothing checks.  The cost is on record: ``App.start_session``
exists *because* the bring-up was copy-pasted into ``run_daemon`` / ``run_gui``
/ ``run_qtgui``, one copy drifted, and a reporter running ``trccd.service``
watched a connected device stay permanently blank until the missing
``metrics_loop.start()`` was added to that copy alone (#148).  This module makes
the sequence structural: there is one ``start()``, and a UI supplies only what
genuinely differs.

Register by naming the key in the class line, exactly as a wire adapter does::

    class GuiUI(UserInterface, key="gui"): ...

``key=None`` means "intermediate base, don't register".

It is ``key=``, never ``name=``: on Python 3.10 ``ABCMeta.__new__`` takes a
positional parameter called ``name``, so a class keyword of that name raised
``TypeError`` and every face that starts a session -- gui, qtgui, api, daemon
-- crashed on launch from v9.10.0 until 3.11 made those parameters
positional-only.  Ubuntu 22.04 runs 3.10.

**Placement is load-bearing, and it is gated.**  Concrete UI classes live in
``ui/_uis.py``, NOT inside ``ui/gui/`` or ``ui/qtgui/``.  Importing *any*
submodule of those packages runs their ``__init__``, which imports the whole
window: measured, ``import trcc.ui.gui.assets`` costs **249 ms and pulls 17
PySide6 modules**.  Because this registry populates by eager side-effect import
— the same way ``DEVICES`` does — a class placed there would put PySide6 and
FastAPI on the critical path of every ``trcc`` invocation, ~700 ms against a
~350 ms baseline.  Keeping the classes light instead costs **~1 ms and loads
zero heavy modules**, which is what lets this use the ONE registry idiom rather
than inventing a lazy second one.  ``tests/test_ui_bus.py`` gates it.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

from ..core.errors import UnknownUserInterfaceError
from ..core.factory import Registry, Reject
from ..core.logs import per_frame

if TYPE_CHECKING:
    from ..app import App
    from ..core.commands import Command
    from ..core.events import EventBus
    from ..core.ports import Platform
    from ..core.results import Result

log = logging.getLogger(__name__)
#: Per-dispatch family — silenced with the frame path, see core.logs.
frame_log = per_frame(__name__)

#: Binds the caller's Result subclass, so ``dispatch`` keeps concrete typing.
R = TypeVar("R", bound="Result")


# The face table.  A miss RAISES: asking for a UI that does not exist is a typo
# or a stale entry point, and quietly starting a different face of the app would
# be worse than stopping.  Same policy as ``DEVICES``, not ``PLATFORMS``.
UIS: Registry[str, type[UserInterface]] = Registry(
    "ui", on_missing=Reject(UnknownUserInterfaceError),
)


class UserInterface(ABC):
    """One face of the one app — CLI, API, GUI, qtgui, daemon.

    A UI owns **only** its own loop.  Everything around that loop — the
    single-instance guard, composing the ``App``, bringing the session up,
    tearing it down — is invariant and lives in :meth:`start`.
    """

    #: The registry key, set by ``__init_subclass__`` from the class line.
    key: ClassVar[str] = ""

    #: Set by :meth:`start`.  Class-level defaults rather than an ``__init__``
    #: so a face writes only the constructor IT needs -- no ``super().__init__``
    #: to forget, which is the same reason registration is a class keyword.
    _composed: App | None = None
    _platform: Platform | None = None

    #: Whether this UI needs the live session (coldplug + metrics + LED loops).
    #: False for the one-shot CLI, which must not pay for a coldplug it will
    #: never use -- ``App.start_session``'s own docstring says one-shot scripts
    #: skip it.
    needs_session: ClassVar[bool] = True

    def __init_subclass__(cls, key: str | None = None, **kwargs: Any) -> None:
        """Register the subclass under the ``key=`` it declares."""
        super().__init_subclass__(**kwargs)
        if key is None:
            log.debug("%s: intermediate UI base, not registered", cls.__name__)
            return
        log.debug("%s: registering as UI %r", cls.__name__, key)
        cls.key = key
        UIS.register(key)(cls)

    # ── The universal start — Template Method ────────────────────────────

    def start(self, platform: Platform | None = None) -> int:
        """Launch this UI and return its process exit code.

        The invariant sequence, in one place:

        1. **single-instance guard** — must precede *any* construction, because
           a peer-detected exit has to happen before a ``QApplication`` is built
           or USB is opened.
        2. **compose** the App (local, or an ``AppProxy`` in daemon mode).
        3. **bring the session up** — coldplug + live loops, unless the UI
           declares it does not need them.
        4. **run** the UI's own loop.
        5. **close** the App, always.
        """
        log.info("start: ui=%s platform=%s", type(self).__name__,
                 type(platform).__name__)
        self._platform = platform
        if (code := self.preflight()) is not None:
            log.info("start: %s refused to launch, exit=%d",
                     type(self).__name__, code)
            return code
        try:
            if self.needs_session:
                # Compose BEFORE bring_up, not lazily inside it.  A Qt face
                # builds its QApplication here, and ``QtGuiUI.bring_up`` opens
                # a splash — a QWidget.  Left lazy, the splash was constructed
                # first and Qt aborted the process outright:
                # ``QWidget: Must construct a QApplication before a QWidget``,
                # SIGABRT, caught by ``dev/smoke_ui_shutdown.py`` and by
                # nothing in the unit suite, because it is process lifecycle.
                #
                # Laziness still buys what it was for: the CLI declares
                # ``needs_session = False``, so ``trcc --help`` composes
                # nothing.  The faces that DO need a session were always going
                # to compose a moment later anyway.
                _ = self._app
                if not self.bring_up():
                    log.warning("start: %s bring-up failed",
                                type(self).__name__)
                    return 1
            return self.run()
        finally:
            # Close only what was actually built.  A face that never
            # dispatched -- ``trcc --help`` reaching the CLI face -- composed
            # nothing, and closing a phantom would build an App just to tear
            # it down.
            if self._composed is not None:
                log.info("start: %s closing down", type(self).__name__)
                self._composed.close()
                self._composed = None
            self.teardown()
            # The unconditional teardown proof.  ``dev/smoke_ui_shutdown.py``
            # greps for exactly this: a UI that exits WITHOUT it left the panel
            # lit and the transport held (#143), and "the process is gone" on
            # its own does not distinguish the two.  Logged AFTER close, never
            # before — a marker printed ahead of the work it attests to is
            # worse than none.
            log.info("%s: cleanup complete — process exit",
                     type(self).__name__)

    # ── The command bus — what a face IS ─────────────────────────────────
    #
    # A face speaks Commands and nothing else.  ``dispatch`` and ``events``
    # are the whole surface; the ``App`` behind them is deliberately private,
    # so "no UI reaches past the bus" stops being a ratchet somebody maintains
    # and becomes a thing that cannot be typed.  Measured across the two
    # windows and both handlers: **100 of 103** uses of their App were
    # ``.dispatch`` and 2 were ``.events`` -- this is the shape the code
    # already wanted.

    def dispatch(self, cmd: Command[R]) -> R:
        """Send one Command and get its typed Result.  THE surface.

        DEBUG, not INFO: ``App.dispatch`` already logs every dispatch at the
        Command's own ``LOG_LEVEL`` — that is the single chokepoint a
        ``trcc report`` is read for.  A second INFO line here would double
        every user-action record and drown the one that carries the args.
        """
        frame_log.debug("dispatch: %s via %s",
                        type(cmd).__name__, type(self).__name__)
        return self._app.dispatch(cmd)

    @property
    def events(self) -> EventBus:
        """Observe — the other half of the bus, same object either mode."""
        log.debug("events: %s subscribing to the bus", type(self).__name__)
        return self._app.events

    @property
    def _app(self) -> App:
        """The App this face dispatches on, composed ONCE on first use.

        Lazy on purpose.  ``trcc --help`` builds zero Apps today (measured),
        and eagerly composing in :meth:`start` would have cost every text
        command a platform scan plus a 111 ms ``QtRenderer`` — which is
        precisely what kept the CLI out of the registry.  Composing on first
        dispatch instead lets every face join without paying for one.
        """
        if self._composed is None:
            log.info("_app: composing for %s", type(self).__name__)
            self._composed = self.compose(self._platform)
        return self._composed

    def preflight(self) -> int | None:
        """Refuse to launch, or None to proceed.  Runs before ANY construction.

        Two UIs have an exclusivity precondition, and they are genuinely
        different -- which is why this is a hook and not a ``ClassVar``:

        * the GUI takes ``SingleInstance("gui")``; a peer means the running
          window was raised, so the correct exit code is **0**, a success.
        * the daemon tests ``ipc.daemon_running()`` -- a different socket
          serving a different purpose (``SingleInstance``'s own docstring
          separates the two) -- and a peer means this process could not start,
          so the correct exit code is **1**, a failure.

        Collapsing those into one declaration would have changed the daemon's
        mechanism AND its exit code.  It also has to run before
        :meth:`compose`: the daemon's check placed any later would have opened
        USB in :meth:`bring_up` before discovering another daemon owns the bus.

        The default returns None, which is not the no-op stub this codebase
        refuses.  A stub LIES -- ``enable()`` that silently does nothing tells
        the caller autostart is on.  "This UI has no launch precondition" is a
        true statement about the CLI, the API and qtgui.
        """
        log.debug("preflight: %s has no launch precondition",
                  type(self).__name__)
        return None

    def teardown(self) -> None:
        """Release anything :meth:`preflight` acquired.  Runs after the loop."""
        log.debug("teardown: %s has nothing to release", type(self).__name__)

    # ── The three variation points ───────────────────────────────────────

    def compose(self, platform: Platform | None) -> App:
        """Build the App this UI will dispatch on.

        The default is the canonical factory, which is also where the
        local-vs-daemon decision lives.  The Qt skins override it because
        ``QtRenderer`` needs a windowed ``QApplication`` to exist first.
        """
        log.info("compose: %s building via _boot.trcc", type(self).__name__)
        from .._boot import trcc
        return trcc(platform=platform)

    def bring_up(self) -> bool:
        """Start the live session.  True to continue, False to abort.

        The default is the one shared bring-up.  The GUI overrides it to run
        the same coldplug on a splash worker so the window can paint progress.
        """
        log.info("bring_up: %s starting session", type(self).__name__)
        self._app.start_session()
        return True

    @abstractmethod
    def run(self) -> int:
        """Run this UI's own loop and return its exit code.

        Reach the app through :meth:`dispatch` / :attr:`events`.
        """


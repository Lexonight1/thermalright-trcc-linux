"""Shared cadence machinery for periodic device drivers.

``ScreencastDriver`` and ``SlideshowDriver`` both drive one device on a fixed
interval from a :class:`~trcc.core.ports.SendScheduler` thread, and both
repeated the same ``__init__`` / ``key`` / ``wait`` / ``wake`` plumbing
verbatim — only their scheduler namespace, their default interval, and their
``run_once`` work differ.  This is that shared half, so a concrete driver is
now just a namespace + a default interval + a ``_run``.

``SendTask`` (the *port*, in ``core.ports``) stays a pure contract; this
concrete base lives in ``services`` beside the drivers it serves — the same
placement as ``BaseDevice`` in the device-adapter layer, never in ``core``.
``_run`` is deliberately left abstract here: it is the one method that
genuinely differs per driver, so the base stays abstract and the polymorphism
lives exactly where the behaviour does.
"""
from __future__ import annotations

import logging
import threading
from abc import abstractmethod
from typing import TYPE_CHECKING, ClassVar

from ..core.logs import per_frame
from ..core.ports import SendTask

if TYPE_CHECKING:                                    # pragma: no cover
    from ..app import App

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class BaseSendTask(SendTask):
    """A :class:`SendTask` driven on a fixed cadence for one device.

    Subclass contract — three declarations, nothing more:

    * ``KEY_PREFIX`` — the scheduler namespace (``"screencast:"`` etc.); it
      keeps the driver out of the device's own scheduler slot, so registering
      it never stops the device's ``DeviceSender``.  See the driver modules.
    * ``DEFAULT_INTERVAL_S`` — the cadence used when a caller passes none.
    * ``_run(now) -> float`` — the actual per-tick work (abstract); the base's
      ``run_once`` calls it only while the panel is connected.
    """

    KEY_PREFIX: ClassVar[str]
    DEFAULT_INTERVAL_S: ClassVar[float]

    def __init__(self, app: App, device_key: str,
                 interval_s: float | None = None) -> None:
        interval = self.DEFAULT_INTERVAL_S if interval_s is None else interval_s
        log.info("%s: %s every %.0f ms",
                 type(self).__name__, device_key, interval * 1000)
        self._app = app
        self._device_key = device_key
        self._interval = interval
        self._wake = threading.Event()

    @property
    def key(self) -> str:
        """The NAMESPACED scheduler key — see the driver module docstrings."""
        key = f"{self.KEY_PREFIX}{self._device_key}"
        log.debug("%s.key: %s → %s", type(self).__name__, self._device_key, key)
        return key

    def wait(self, timeout: float) -> None:
        """Block until woken or *timeout* elapses."""
        frame_log.debug("%s.wait: %s %.3fs",
                        type(self).__name__, self._device_key, timeout)
        self._wake.wait(timeout)
        self._wake.clear()

    def wake(self) -> None:
        """Interrupt a pending :meth:`wait` (scheduler teardown)."""
        frame_log.debug("%s.wake: %s", type(self).__name__, self._device_key)
        self._wake.set()

    #: Whether this task works a panel -- and so waits while it is away.
    NEEDS_PANEL: ClassVar[bool] = True
    #: How often a waiting task looks for its panel again.
    AWAY_POLL_S: ClassVar[float] = 1.0

    def run_once(self, now: float) -> float:
        """Do one turn of the work, unless the panel is away.

        A driver outlives a panel that blinks (``App.stop_sender`` leaves it
        running), so while the panel is gone it must cost nothing: no screen
        capture, no theme switched for nobody, no stream read.  It resumes on
        the first turn after the panel is back -- the source never stopped.
        """
        if self.NEEDS_PANEL:
            device = self._app.devices.get(self._device_key)
            if device is None or not device.is_connected:
                frame_log.debug("%s: %s away — waiting", type(self).__name__,
                                self._device_key)
                return self.AWAY_POLL_S
        return self._run(now)

    @abstractmethod
    def _run(self, now: float) -> float:
        """One turn of the task's work; return the seconds until the next."""

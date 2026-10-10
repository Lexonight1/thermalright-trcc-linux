"""The App asks whether a newer TRCC exists -- never a window."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

from trcc.core.results import UpdateCheckResult
from trcc.services.update_watch import UpdateWatch

NEWER = UpdateCheckResult(ok=True, local_version="9.10.8",
                          latest_version="9.11.0", update_available=True)


def _until(done: Callable[[], bool], timeout: float = 2.0) -> None:
    end = time.monotonic() + timeout
    while not done() and time.monotonic() < end:
        time.sleep(0.005)
    assert done(), "timed out"


def test_it_checks_at_once_keeps_the_answer_and_tells_it() -> None:
    told: list[UpdateCheckResult] = []
    watch = UpdateWatch(lambda: NEWER, told.append, interval_s=60)
    assert watch.latest is None                       # before the first answer
    watch.start()
    _until(lambda: bool(told))
    assert told == [NEWER] and watch.latest == NEWER
    watch.stop()


def test_it_checks_again_every_interval_and_starts_once() -> None:
    calls: list[int] = []
    watch = UpdateWatch(lambda: calls.append(1) or NEWER, lambda r: None,
                        interval_s=0.02)
    watch.start()
    watch.start()                                     # idempotent
    _until(lambda: len(calls) >= 3)
    watch.stop()
    settled = len(calls)
    time.sleep(0.06)
    assert len(calls) == settled                      # stopped means stopped


def test_a_check_that_raises_is_logged_and_the_watch_goes_on() -> None:
    answers = iter([RuntimeError("boom"), NEWER])

    def check() -> UpdateCheckResult:
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    told: list[UpdateCheckResult] = []
    watch = UpdateWatch(check, told.append, interval_s=0.01)
    watch.start()
    _until(lambda: bool(told))
    watch.stop()
    assert told == [NEWER]


def test_an_answer_that_lands_after_stop_is_dropped() -> None:
    release = threading.Event()

    def slow() -> UpdateCheckResult:
        release.wait(2)
        return NEWER

    told: list[UpdateCheckResult] = []
    watch = UpdateWatch(slow, told.append, interval_s=60)
    watch.start()
    stopper = threading.Thread(target=watch.stop)
    stopper.start()
    time.sleep(0.02)
    release.set()
    stopper.join(2)
    assert told == [] and watch.latest is None


def test_the_app_checks_from_its_session_and_every_ui_can_read_it(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """start_session starts the watch; UpdateStatus answers without the
    network; UpdateChecked reaches every listener; close stops it."""
    from tests.mock_platform import MockPlatform
    from trcc.app import App
    from trcc.core.commands import UpdateStatus
    from trcc.core.events import UpdateChecked

    app = App(MockPlatform([], tmp_path))
    assert app.dispatch(UpdateStatus()).message == "Not checked yet"
    heard: list[UpdateChecked] = []
    app.events.subscribe(UpdateChecked, heard.append)
    app.update_watch.start()        # what start_session does, without coldplug
    _until(lambda: bool(heard))
    status = app.dispatch(UpdateStatus())
    assert heard[0].ok == status.ok and heard[0].message == status.message
    app.close()
    assert app.update_watch._thread is None

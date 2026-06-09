"""LEDHandler must require an App handle.

A None ``app`` silently disabled the metrics gate (``update_metrics`` returns
early when ``self._app`` is falsy) — that was the LED ``--`` bug: the handler
was built without ``app`` and every gauge stayed blank.  The handler now fails
loudly at construction, so the composition root can never wire it app-less
again.  No QApplication needed — the guard raises before any widget is touched.
"""
from __future__ import annotations

from typing import Any, cast

import pytest

from trcc.ui.gui.led_handler import LEDHandler


def test_led_handler_rejects_none_app() -> None:
    with pytest.raises(RuntimeError, match="App handle"):
        # device/panel are never reached — the app guard is first.
        LEDHandler(object(), cast(Any, None), lambda *a, **k: None, app=None)

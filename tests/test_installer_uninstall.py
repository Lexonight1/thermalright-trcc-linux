"""The Windows uninstaller removes every autostart the app can create.

An autostart's NAME is its identity: the uninstaller can only delete what it
names exactly.  The sign-in task arrived in 78a7f568 and the uninstaller never
learned of it, so uninstalling left a task that ran a deleted program at every
sign-in.  The names live in ``_autostart.py``; this keeps the installer in step.
"""
from __future__ import annotations

import re
from pathlib import Path

from trcc.adapters.system import _autostart

_ISS = Path(__file__).resolve().parent.parent / "installer" / "trcc.iss"


def _uninstall_step() -> str:
    """The body of ``CurUninstallStepChanged``, where the clean-up runs."""
    text = _ISS.read_text(encoding="utf-8")
    found = re.search(r"procedure CurUninstallStepChanged.*?\nend;", text, re.S)
    assert found, "the installer lost its uninstall step"
    return found.group(0)


def test_the_uninstaller_deletes_the_sign_in_task() -> None:
    """MUTATION CHECK: rename ``_TASK_NAME`` and this fails."""
    step = _uninstall_step()

    assert f'/delete /tn "{_autostart._TASK_NAME}" /f' in step


def test_the_uninstaller_deletes_the_run_value() -> None:
    step = _uninstall_step()
    run_key = _autostart._WIN_RUN_KEY_PATH

    assert f"'{run_key}',\n      '{_autostart._DEFAULT_VALUE_NAME}');" in step

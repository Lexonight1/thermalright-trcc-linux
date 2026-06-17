"""Guards for the root re-exec helper (``adapters/system/_elevate``).

The bug this fixes: ``pip install --user`` puts ``trcc`` in the user's
site-packages, which root's interpreter doesn't see, so a bare
``sudo python -c "import trcc"`` died with ``ModuleNotFoundError`` running
``trcc system setup``.  The fix injects the importable dirs onto ``sys.path``
inside the ``-c`` snippet.  These tests assert that injection happens and that
the helper never shells out unmocked.
"""
from __future__ import annotations

import site
import subprocess

import pytest

from trcc.adapters.system import _elevate


def test_import_paths_include_user_site_packages() -> None:
    """The user site-packages dir (where ``pip install --user`` lands) must be
    in the injected paths — that's the dir root's Python otherwise can't see."""
    paths = _elevate._import_paths()
    assert site.getusersitepackages() in paths
    # The trcc package root (src/ or site-packages) is injected too, so a
    # source checkout / system install resolves as well.
    assert any(p.endswith("src") or "site-packages" in p for p in paths)


def test_reexec_injects_paths_into_snippet(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sudo command must be ``sudo <python> -c "<inject>; <snippet>"`` with
    the importable dirs spliced onto sys.path before the snippet runs."""
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(_elevate, "_import_paths", lambda: ["/u/.local/site-packages"])
    monkeypatch.setattr(_elevate.subprocess, "run", _fake_run)

    rc = _elevate.reexec_as_root("from trcc.x import install; sys.exit(install())")

    assert rc == 0
    cmd = captured["cmd"]
    assert cmd[0] == "sudo"
    assert cmd[-2] == "-c"
    code = cmd[-1]
    assert "sys.path[:0] = ['/u/.local/site-packages']" in code
    assert code.endswith("from trcc.x import install; sys.exit(install())")


def test_reexec_returns_1_when_sudo_cannot_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spawn failure (no sudo) is a clean non-zero, not an exception."""
    monkeypatch.setattr(_elevate, "_import_paths", lambda: [])

    def _boom(*a, **k):  # type: ignore[no-untyped-def]
        raise OSError("sudo: command not found")

    monkeypatch.setattr(_elevate.subprocess, "run", _boom)
    assert _elevate.reexec_as_root("sys.exit(0)") == 1

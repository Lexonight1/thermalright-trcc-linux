"""MacOSAutostart — LaunchAgent plist writer + launchctl DI seam.

The real ``launchctl`` and ``~/Library/LaunchAgents/`` only exist on
macOS; tests inject a tmpdir for the plist path and a recording
runner that captures every launchctl invocation without spawning a
subprocess.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from trcc.adapters.system._autostart import (
    MacOSAutostart,
    _render_plist,
)

# =========================================================================
# Recording runner — captures every launchctl call
# =========================================================================


class _Runner:
    """Stand-in for the launchctl subprocess runner.

    Returns ``returncode`` for every call; the test pre-loads a queue
    of returncodes so it can simulate "bootstrap already loaded"
    (rc=17) / "bootout not loaded" (rc=5) etc.
    """

    def __init__(self, returncodes: list[int] | None = None) -> None:
        self.calls: list[list[str]] = []
        self._returncodes = list(returncodes) if returncodes is not None else []

    def __call__(self, args: list[str]) -> int:
        self.calls.append(list(args))
        return self._returncodes.pop(0) if self._returncodes else 0


def _build(
    tmp_path: Path,
    *,
    runner: _Runner | None = None,
    program_args: list[str] | None = None,
) -> tuple[MacOSAutostart, _Runner, Path]:
    """Construct a MacOSAutostart pointed at a tmpdir plist."""
    rec = runner if runner is not None else _Runner()
    plist = tmp_path / "LaunchAgents" / "com.thermalright.trcc.plist"
    autostart = MacOSAutostart(
        plist_path=plist,
        program_args=program_args or ["/opt/trcc/bin/trcc", "gui"],
        runner=rec,
        uid=501,
    )
    return autostart, rec, plist


# =========================================================================
# _render_plist — pure-string rendering
# =========================================================================


def test_render_plist_contains_label_and_program_arguments() -> None:
    body = _render_plist(
        ["/opt/trcc/bin/trcc", "gui"],
        label="com.thermalright.trcc",
    )
    assert "<key>Label</key>" in body
    assert "<string>com.thermalright.trcc</string>" in body
    assert "<string>/opt/trcc/bin/trcc</string>" in body
    assert "<string>gui</string>" in body


def test_render_plist_sets_run_at_load() -> None:
    body = _render_plist(["/x"], label="L")
    assert "<key>RunAtLoad</key>" in body
    assert "<true/>" in body


def test_render_plist_keepalive_false() -> None:
    """Keep-alive must be False — the GUI is an ordinary login app, not a daemon."""
    body = _render_plist(["/x"], label="L")
    assert "<key>KeepAlive</key>" in body
    assert "<false/>" in body


def test_render_plist_preserves_python_module_invocation() -> None:
    """When trcc isn't on PATH we fall back to python -m trcc gui."""
    body = _render_plist(["/usr/bin/python3", "-m", "trcc", "gui"])
    assert "<string>/usr/bin/python3</string>" in body
    assert "<string>-m</string>" in body
    assert "<string>trcc</string>" in body


# =========================================================================
# is_enabled / enable / disable round-trip
# =========================================================================


def test_is_enabled_false_before_any_writes(tmp_path: Path) -> None:
    autostart, _runner, _plist = _build(tmp_path)
    assert autostart.is_enabled() is False


def test_enable_writes_plist_and_calls_bootstrap(tmp_path: Path) -> None:
    autostart, runner, plist = _build(tmp_path)
    autostart.enable()

    assert plist.exists()
    body = plist.read_text()
    assert "<string>/opt/trcc/bin/trcc</string>" in body
    # launchctl bootstrap gui/501 /tmp/.../com.thermalright.trcc.plist
    assert runner.calls == [["launchctl", "bootstrap", "gui/501", str(plist)]]


def test_is_enabled_true_after_enable(tmp_path: Path) -> None:
    autostart, _runner, _plist = _build(tmp_path)
    autostart.enable()
    assert autostart.is_enabled() is True


def test_disable_calls_bootout_and_removes_plist(tmp_path: Path) -> None:
    autostart, runner, plist = _build(tmp_path)
    autostart.enable()
    assert plist.exists()

    autostart.disable()
    assert not plist.exists()
    # bootout target is the gui/<uid>/<label> domain identifier
    assert runner.calls[-1] == ["launchctl", "bootout", "gui/501/com.thermalright.trcc"]


def test_disable_when_never_enabled_is_silent(tmp_path: Path) -> None:
    autostart, runner, _plist = _build(tmp_path)
    autostart.disable()                  # plist doesn't exist → no-op
    assert runner.calls == []


def test_enable_already_loaded_returncode_is_accepted(tmp_path: Path) -> None:
    """rc=17 ("already loaded") from bootstrap is tolerated — idempotent."""
    runner = _Runner(returncodes=[17])
    autostart, _runner, plist = _build(tmp_path, runner=runner)
    autostart.enable()                   # would raise / log error if rejected

    assert plist.exists()


def test_disable_not_loaded_returncode_is_accepted(tmp_path: Path) -> None:
    """rc=5 ("not loaded") from bootout is tolerated — idempotent."""
    runner = _Runner(returncodes=[5])
    autostart, _runner, plist = _build(tmp_path, runner=runner)
    autostart.enable()                   # write the plist first
    runner.calls.clear()
    runner._returncodes = [5]            # bootout will return "not loaded"

    autostart.disable()
    assert not plist.exists()            # plist removed even when bootout said 5


# =========================================================================
# Default program-args resolution (matches the Windows shape)
# =========================================================================


def test_resolve_macos_program_args_uses_trcc_next_when_on_path(
    monkeypatch,
) -> None:
    from trcc.adapters.system import _autostart

    monkeypatch.setattr(
        _autostart.shutil, "which",
        lambda name: "/opt/trcc/bin/trcc" if name == "trcc" else None,
    )
    args = _autostart._resolve_macos_program_args()
    # --resume: an autostarted LaunchAgent starts in the tray, matching Linux
    # since #201.  macOS never got that fix because #201 was reported on Linux.
    assert args == ["/opt/trcc/bin/trcc", "gui", "--resume"]


def test_resolve_macos_program_args_falls_back_to_python(monkeypatch) -> None:
    from trcc.adapters.system import _autostart

    monkeypatch.setattr(_autostart.shutil, "which", lambda name: None)
    args = _autostart._resolve_macos_program_args()
    assert "-m" in args
    assert "trcc" in args
    # --resume, so an autostarted LaunchAgent comes up in the tray instead of
    # popping a window — the behaviour Linux has had since #201 and macOS had
    # not.  The subcommand is still the last thing before it.
    assert args[-2:] == ["gui", "--resume"]


# =========================================================================
# refresh — re-render an installed plist (the #201 upgrade path)
# =========================================================================


def test_refresh_rerenders_an_installed_plist(tmp_path: Path) -> None:
    """An existing LaunchAgent picks up a changed argv on upgrade.

    Was a no-op — "the plist needs no rebuild between sessions" — which held
    only while the argv could never change.  ``--resume`` changed it, and
    without this an existing agent launches a visible window forever.
    """
    autostart, _rec, plist = _build(tmp_path,
                                  program_args=["/opt/trcc/bin/trcc", "gui"])
    autostart.enable()
    assert "--resume" not in plist.read_text(encoding="utf-8")

    upgraded, _rec2, _p = _build(
        tmp_path, program_args=["/opt/trcc/bin/trcc", "gui", "--resume"],
    )
    upgraded.refresh()

    assert "<string>--resume</string>" in plist.read_text(encoding="utf-8"), (
        "refresh left the stale argv in the plist"
    )


_OLD = 1_000_000_000_000_000_000      # 2001, in ns: no write can land on it


@pytest.fixture
def trcc_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """One fixed program path, so the rendered argv is stable."""
    from trcc.adapters.system import _autostart

    monkeypatch.setattr(_autostart.shutil, "which",
                        lambda name: "/opt/trcc/bin/trcc" if name == "trcc" else None)


def test_refresh_leaves_a_current_plist_and_launchd_alone(
    tmp_path: Path, trcc_on_path: None,
) -> None:
    """It ran on every UI start: a rewrite and a ``launchctl bootstrap`` of a
    loaded agent each time (PR #303)."""
    autostart, rec, plist = _build(tmp_path)
    autostart.enable("qtgui")
    os.utime(plist, ns=(_OLD, _OLD))     # a rewrite now cannot share its tick
    rec.calls.clear()

    autostart.refresh()

    assert plist.stat().st_mtime_ns == _OLD
    assert rec.calls == []
    assert autostart.installed_target() == "qtgui"


def test_refresh_rewrites_a_stale_plist_without_touching_launchd(
    tmp_path: Path, trcc_on_path: None,
) -> None:
    """#201 still holds: an agent from before ``--resume`` gets it.  Loading
    is launchd's, at the next login."""
    autostart, rec, plist = _build(tmp_path)
    plist.parent.mkdir(parents=True)
    plist.write_text(_render_plist(["/opt/trcc/bin/trcc", "gui"]), encoding="utf-8")

    autostart.refresh()

    assert plist.read_text(encoding="utf-8") == _render_plist(
        ["/opt/trcc/bin/trcc", "gui", "--resume"])
    assert rec.calls == []


def test_enabling_again_loads_the_agent_without_rewriting_it(
    tmp_path: Path, trcc_on_path: None,
) -> None:
    autostart, rec, plist = _build(tmp_path)
    autostart.enable("gui")
    os.utime(plist, ns=(_OLD, _OLD))
    rec.calls.clear()

    autostart.enable("gui")

    assert plist.stat().st_mtime_ns == _OLD
    assert rec.calls == [["launchctl", "bootstrap", "gui/501", str(plist)]]


def test_refresh_does_not_install_a_plist_that_was_never_enabled(
    tmp_path: Path,
) -> None:
    """The invariant every refresh shares: never create an entry."""
    autostart, rec, plist = _build(tmp_path)

    autostart.refresh()

    assert not plist.exists()
    assert rec.calls == [], f"refresh shelled out for a missing agent: {rec.calls}"


# =========================================================================
# Which UI starts with the computer
# =========================================================================


@pytest.mark.parametrize("target", ["gui", "qtgui", "api", "daemon"])
def test_enable_writes_the_chosen_target_into_the_plist(
    tmp_path: Path, target: str,
) -> None:
    autostart, _rec, plist = _build(tmp_path)

    autostart.enable(target)

    assert f"<string>{target}</string>" in plist.read_text(encoding="utf-8")
    assert autostart.installed_target() == target


def test_refresh_preserves_the_installed_target(tmp_path: Path) -> None:
    autostart, _rec, _plist = _build(tmp_path)
    autostart.enable("daemon")

    autostart.refresh()

    assert autostart.installed_target() == "daemon"

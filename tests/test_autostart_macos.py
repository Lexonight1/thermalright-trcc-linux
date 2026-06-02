"""MacOSAutostart — LaunchAgent plist writer + launchctl DI seam.

The real ``launchctl`` and ``~/Library/LaunchAgents/`` only exist on
macOS; tests inject a tmpdir for the plist path and a recording
runner that captures every launchctl invocation without spawning a
subprocess.
"""
from __future__ import annotations

from pathlib import Path

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
    assert args == ["/opt/trcc/bin/trcc", "gui"]


def test_resolve_macos_program_args_falls_back_to_python(monkeypatch) -> None:
    from trcc.adapters.system import _autostart

    monkeypatch.setattr(_autostart.shutil, "which", lambda name: None)
    args = _autostart._resolve_macos_program_args()
    assert "-m" in args
    assert "trcc" in args
    assert args[-1] == "gui"

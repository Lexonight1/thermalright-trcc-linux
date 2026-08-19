"""XdgDesktopAutostart — per-user XDG .desktop file lifecycle (Linux + BSD)."""
from __future__ import annotations

from pathlib import Path

from trcc.adapters.system._autostart import XdgDesktopAutostart


def test_is_enabled_false_when_no_file(tmp_home: Path) -> None:
    mgr = XdgDesktopAutostart()
    assert mgr.is_enabled() is False


def test_enable_writes_desktop_entry(tmp_home: Path) -> None:
    mgr = XdgDesktopAutostart()

    mgr.enable()

    assert mgr.is_enabled() is True
    assert mgr.path.exists()
    assert mgr.path.parent.name == "autostart"
    assert mgr.path.name == "trcc.desktop"


def test_enable_content_has_xdg_required_fields(tmp_home: Path) -> None:
    mgr = XdgDesktopAutostart()

    mgr.enable()

    text = mgr.path.read_text(encoding="utf-8")
    assert text.startswith("[Desktop Entry]"), "must start with spec-required header"
    assert "\nType=Application\n" in text
    assert "\nExec=" in text
    # Exec resolves to `trcc gui` (preferred, if script on PATH) or
    # `<python> -m trcc gui` (fallback).  Either points at next/.
    assert "trcc" in text or "trcc.next" in text, (
        "Exec line should reference the next/ tree"
    )


def test_exec_line_starts_hidden_in_tray(tmp_home: Path) -> None:
    """Autostart must launch with `--resume` so login starts hidden in the
    tray instead of popping a window — the behaviour that regressed (#201)."""
    mgr = XdgDesktopAutostart()

    mgr.enable()

    exec_line = next(
        line for line in mgr.path.read_text(encoding="utf-8").splitlines()
        if line.startswith("Exec=")
    )
    assert exec_line.endswith("gui --resume"), exec_line


def test_enable_permissions_are_readable(tmp_home: Path) -> None:
    mgr = XdgDesktopAutostart()

    mgr.enable()

    mode = mgr.path.stat().st_mode & 0o777
    assert mode == 0o644


def test_disable_removes_file(tmp_home: Path) -> None:
    mgr = XdgDesktopAutostart()
    mgr.enable()
    assert mgr.is_enabled() is True

    mgr.disable()

    assert mgr.is_enabled() is False
    assert not mgr.path.exists()


def test_disable_is_idempotent(tmp_home: Path) -> None:
    mgr = XdgDesktopAutostart()

    mgr.disable()  # nothing to remove — must not raise

    assert mgr.is_enabled() is False


def test_refresh_rewrites_when_file_present(tmp_home: Path) -> None:
    mgr = XdgDesktopAutostart()
    mgr.enable()
    # Truncate the file to simulate corruption
    mgr.path.write_text("bogus")

    mgr.refresh()

    assert "[Desktop Entry]" in mgr.path.read_text(encoding="utf-8")


def test_refresh_noops_when_file_absent(tmp_home: Path) -> None:
    mgr = XdgDesktopAutostart()

    mgr.refresh()

    assert not mgr.path.exists()


def test_linux_and_bsd_platforms_share_the_xdg_manager(tmp_home: Path) -> None:
    """Both Linux and BSD return the shared XDG autostart (BSD was Noop)."""
    from trcc.adapters.system.bsd import FreeBsdOS, OpenBsdOS
    from trcc.adapters.system.linux import LinuxOS

    assert isinstance(LinuxOS().autostart(), XdgDesktopAutostart)
    assert isinstance(FreeBsdOS().autostart(), XdgDesktopAutostart)
    assert isinstance(OpenBsdOS().autostart(), XdgDesktopAutostart)

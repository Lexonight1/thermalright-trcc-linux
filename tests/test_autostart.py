"""XdgDesktopAutostart — per-user XDG .desktop file lifecycle (Linux + BSD)."""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.adapters.system._autostart import (
    XdgDesktopAutostart,
    autostart_argv,
    target_from_argv,
    target_from_command,
)
from trcc.core.models import AUTOSTART_TARGETS


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


# =========================================================================
# Which UI starts with the computer
# =========================================================================
#
# All four ship, so pinning autostart to `gui` made the other three
# unreachable at login.  The INSTALLED ENTRY is the record of what was
# chosen — there is no second copy in Settings that could drift from it.


@pytest.mark.parametrize("target", sorted(AUTOSTART_TARGETS))
def test_enable_writes_the_chosen_target(tmp_home: Path, target: str) -> None:
    mgr = XdgDesktopAutostart()

    mgr.enable(target)

    exec_line = next(
        line for line in mgr.path.read_text(encoding="utf-8").splitlines()
        if line.startswith("Exec=")
    )
    assert exec_line.split()[1] == target, exec_line
    assert mgr.installed_target() == target


def test_refresh_preserves_the_installed_target(tmp_home: Path) -> None:
    """The #201 repair must not silently reset the user's choice.

    ``refresh()`` re-renders BY CALLING ``enable()``, so a target passed only
    as an argument would be lost here and every repair would quietly put the
    entry back to ``gui``.
    """
    mgr = XdgDesktopAutostart()
    mgr.enable("daemon")

    mgr.refresh()

    assert mgr.installed_target() == "daemon", (
        "refresh reset the target to the default"
    )


def test_installed_target_is_none_when_nothing_is_installed(
    tmp_home: Path,
) -> None:
    assert XdgDesktopAutostart().installed_target() is None


def test_a_legacy_entry_without_flags_still_reads_as_gui(tmp_home: Path) -> None:
    """Entries written before #201 say `trcc gui` with no flag."""
    mgr = XdgDesktopAutostart()
    mgr.enable()
    mgr.path.write_text(
        mgr.path.read_text(encoding="utf-8").replace(" --resume", ""),
        encoding="utf-8",
    )

    assert mgr.installed_target() == "gui"


# =========================================================================
# target_from_argv — read the target at its KNOWN position
# =========================================================================


@pytest.mark.parametrize(("argv", "expected"), [
    (["/usr/bin/trcc", "gui", "--resume"],                 "gui"),
    (["/usr/bin/trcc", "qtgui", "--resume"],               "qtgui"),
    (["/usr/bin/trcc", "api"],                             "api"),
    (["/usr/bin/trcc", "daemon"],                          "daemon"),
    (["/usr/bin/python3", "-m", "trcc", "gui", "--resume"], "gui"),
    (["/usr/bin/python3", "-m", "trcc", "daemon"],         "daemon"),
    (["/usr/bin/trcc", "gui"],                             "gui"),   # pre-#201
    # A PATH COMPONENT spelled like a target must not be mistaken for one.
    (["/home/gui/bin/trcc", "daemon"],                     "daemon"),
    (["/opt/api/trcc", "gui", "--resume"],                 "gui"),
    # Not our shape → None.  A WRONG target is worse than none: it would
    # rewrite the user's entry to a ui they never chose.
    (["/usr/bin/trcc", "--log-file", "daemon", "gui"],     None),
    (["/usr/bin/something-else"],                          None),
    (["/usr/bin/trcc"],                                    None),
    ([],                                                   None),
])
def test_target_is_read_at_its_known_position(
    argv: list[str], expected: str | None,
) -> None:
    """Scanning for the first token that IS a target is wrong.

    ``trcc --log-file daemon gui`` answers ``daemon`` under a scan and ``None``
    under the position rule — and None is the right answer, because that is not
    a command we write.
    """
    assert target_from_argv(argv) == expected


@pytest.mark.parametrize(("value", "expected"), [
    ('"C:\\Program Files\\trcc\\trcc.exe" gui --resume', "gui"),
    ('"C:\\Program Files\\trcc\\trcc.exe" daemon',       "daemon"),
    ('"C:\\trcc\\trcc.exe" gui',                          "gui"),
    ("/usr/bin/trcc qtgui --resume",                      "qtgui"),
])
def test_a_quoted_program_does_not_shift_the_target_position(
    value: str, expected: str,
) -> None:
    """The Windows Run key quotes the program so a Program Files path with
    spaces survives; a plain split would shred it and move the target."""
    assert target_from_command(value) == expected


@pytest.mark.parametrize("target", sorted(AUTOSTART_TARGETS))
def test_what_we_write_parses_back_to_what_we_wrote(target: str) -> None:
    """Round trip — the property the whole design rests on."""
    assert target_from_argv(autostart_argv(target)) == target


# ── Entries earlier versions wrote under other names ─────────────────────

def _entry(exec_cmd: str, *, gnome_key: bool = True) -> str:
    return ("[Desktop Entry]\nType=Application\nName=TRCC Linux\n"
            f"Exec={exec_cmd}\n"
            + ("X-GNOME-Autostart-enabled=true\n" if gnome_key else ""))


_OURS = [
    ("trcc-linux.desktop", "/usr/bin/trcc gui --resume"),
    ("trcc-linux.desktop", "env PYTHONPATH=/src /usr/bin/python3 -m trcc.cli gui --resume"),
    ("trcc-linux.desktop", "/usr/bin/trcc --last-one"),
    ("trcc-next.desktop", "/home/u/.local/bin/trcc-next gui"),
    ("trcc-next.desktop", "/usr/bin/python3 -m trcc.next gui"),
]


@pytest.mark.parametrize("action", ["enable", "disable", "refresh"])
@pytest.mark.parametrize(("name", "exec_cmd"), _OURS,
                         ids=[f"{n}:{e.split()[0].rsplit('/', 1)[-1]}-{i}"
                              for i, (n, e) in enumerate(_OURS)])
def test_an_old_entry_trcc_wrote_is_removed(
    tmp_path: Path, name: str, exec_cmd: str, action: str,
) -> None:
    """An upgraded user kept a second login entry that turning autostart
    off could not remove -- nothing looked for these names.

    MUTATION CHECK: skip the retirement and the old file stays.
    """
    auto = XdgDesktopAutostart(config_home=tmp_path)
    old = tmp_path / "autostart" / name
    old.parent.mkdir(parents=True)
    old.write_text(_entry(exec_cmd), encoding="utf-8")

    getattr(auto, action)()

    assert not old.exists()


@pytest.mark.parametrize("entry", [
    _entry("/usr/bin/trcc gui --resume", gnome_key=False),   # a tweak tool's copy
    _entry("/opt/other/app"),                                # someone else's program
], ids=["no_gnome_key", "not_trcc"])
def test_an_old_name_that_is_not_ours_is_left_alone(tmp_path: Path, entry: str) -> None:
    auto = XdgDesktopAutostart(config_home=tmp_path)
    old = tmp_path / "autostart" / "trcc-linux.desktop"
    old.parent.mkdir(parents=True)
    old.write_text(entry, encoding="utf-8")

    auto.refresh()

    assert old.read_text(encoding="utf-8") == entry


def test_refresh_removes_an_old_entry_without_turning_autostart_on(
    tmp_path: Path,
) -> None:
    """No current entry means the user's latest choice is OFF; carrying the
    old one over would switch it back on."""
    auto = XdgDesktopAutostart(config_home=tmp_path)
    old = tmp_path / "autostart" / "trcc-linux.desktop"
    old.parent.mkdir(parents=True)
    old.write_text(_entry("/usr/bin/trcc gui --resume"), encoding="utf-8")

    auto.refresh()

    assert not old.exists()
    assert not auto.is_enabled()


# ── #311: the App as the user's systemd service ─────────────────────

def _service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
             answers: list[int] | None = None):
    from trcc.adapters.system import _autostart

    monkeypatch.setattr(_autostart, "launch_argv",
                        lambda sub, *a: ["/opt/my trcc/bin/trcc", sub, *a])
    calls: list[tuple[str, ...]] = []
    replies = iter(answers or [0, 0])

    def systemctl(*args: str) -> int:
        calls.append(args)
        return next(replies)
    return _autostart.SystemdUserService(tmp_path, systemctl), calls


def test_the_unit_runs_this_install_and_outlives_the_desktop_session(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#311: a unit PartOf graphical-session.target was stopped by the logout
    into Gamescope.  MUTATION CHECK: bind it to graphical-session.target."""
    service, _calls = _service(tmp_path, monkeypatch)
    text = service.render()
    assert 'ExecStart="/opt/my trcc/bin/trcc" daemon\n' in text
    assert "WantedBy=default.target\n" in text
    assert "graphical-session" not in text


def test_install_writes_enables_and_starts_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, calls = _service(tmp_path, monkeypatch)
    assert service.install() is True
    assert service.path == tmp_path / "systemd" / "user" / "trccd.service"
    assert service.path.read_text() == service.render()
    assert calls == [("daemon-reload",), ("enable", "--now", "trccd.service")]


def test_install_says_when_systemd_would_not_start_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, calls = _service(tmp_path, monkeypatch, answers=[0, 1])
    assert service.install() is False
    assert len(calls) == 2


def test_setup_as_root_leaves_the_users_service_alone(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A per-user service written as root would land in root's home."""
    from trcc.adapters.system import linux

    monkeypatch.setattr(linux.os, "geteuid", lambda: 0)
    monkeypatch.setattr(linux, "SystemdUserService",
                        lambda: pytest.fail("installed as root"))
    linux._install_user_service()


def test_the_shipped_units_are_not_tied_to_the_desktop_session() -> None:
    root = Path(__file__).resolve().parents[1] / "packaging" / "systemd"
    for unit in ("trccd.service", "trcc-display.service"):
        lines = (root / unit).read_text().splitlines()
        assert not [ln for ln in lines if ln.startswith(("PartOf=", "After="))
                    and "graphical-session" in ln], unit


@pytest.mark.parametrize(("env", "shown", "adopted"), [
    # Started by systemd at boot: nothing here, the desktop's in the manager.
    ({}, "WAYLAND_DISPLAY=wayland-0\nXDG_SESSION_TYPE=wayland\nHOME=/h\n",
     ["WAYLAND_DISPLAY", "XDG_SESSION_TYPE"]),
    # Started by a window: it already has a display -- nothing changes.
    ({"DISPLAY": ":0"}, "WAYLAND_DISPLAY=wayland-0\n", []),
    # No desktop logged in yet, or no systemd.
    ({}, "", []),
])
def test_a_boot_started_app_learns_the_desktop_from_the_user_manager(
        monkeypatch: pytest.MonkeyPatch, env: dict[str, str], shown: str,
        adopted: list[str]) -> None:
    """MUTATION CHECK: skip the adoption in LinuxOS._build_screen_capture --
    a service-started App casts nothing; here, return [] always."""
    from trcc.adapters.system import linux

    for name in linux._SESSION_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert linux.adopt_user_session_environment(lambda: shown) == adopted
    assert all(linux.os.environ[n] for n in adopted)
    assert "HOME" not in adopted


def test_the_capture_is_built_after_the_desktop_is_adopted(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The join: adoption is worthless unless the capture chain is built
    after it.  MUTATION CHECK: drop the ``adopt_session`` call in
    ``BaseOS._build_screen_capture``."""
    from trcc.adapters import screencast
    from trcc.adapters.system import linux

    order: list[str] = []
    monkeypatch.setattr(linux.LinuxOS, "adopt_session",
                        staticmethod(lambda: order.append("adopt") or []))
    monkeypatch.setattr(screencast, "build_screen_capture",
                        lambda session, config: order.append("build"))
    linux.LinuxOS()._build_screen_capture()
    assert order == ["adopt", "build"]

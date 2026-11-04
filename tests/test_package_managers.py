"""The package managers answer what the real ones answered.

Every case is a RECORDING: the exact stdout, stderr and exit code the real
command gave, replayed through the real ``_run`` so its exit-code handling is
what is tested.  ``Pacman``: measured 2026-11-04 in
``docker.io/library/archlinux`` (pacman 7.1.0), sync databases absent -- the
warnings that adds go to stderr and must not change an answer.
"""
from __future__ import annotations

import subprocess
from typing import Any

import pytest

from trcc.adapters.system import _packages
from trcc.adapters.system._packages import Pacman

_NO_SYNC_DB = ("warning: database file for 'core' does not exist (use '-Sy' to "
               "download)\nwarning: database file for 'extra' does not exist "
               "(use '-Sy' to download)\n")

#: argv -> (stdout, stderr, returncode), as pacman gave them.
_PACMAN: dict[tuple[str, ...], tuple[str, str, int]] = {
    ("pacman", "-Qqo", "/usr/bin/pacman"): ("pacman\n", _NO_SYNC_DB, 0),
    ("pacman", "-Qqo", "/etc/hostname"): (
        "", _NO_SYNC_DB + "error: No package owns /etc/hostname\n", 1),
    ("pacman", "-Qq", "pacman"): ("pacman\n", _NO_SYNC_DB, 0),
    ("pacman", "-Qq", "no-such-package"): (
        "", _NO_SYNC_DB + "error: package 'no-such-package' was not found\n", 1),
    # The files db: absent (never synced by TRCC), then synced (-Fy).
    ("pacman", "-Fq", "/usr/bin/7z"): ("", _NO_SYNC_DB, 1),
    ("pacman", "-Fq", "/usr/bin/ffmpeg"): ("extra/ffmpeg\n", "", 0),
}


@pytest.fixture
def pacman(monkeypatch: pytest.MonkeyPatch) -> Pacman:
    """A ``Pacman`` whose commands answer from the recordings."""
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        stdout, stderr, code = _PACMAN[tuple(argv)]
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    monkeypatch.setattr(_packages.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(_packages.subprocess, "run", run)
    return Pacman()


def test_pacman_owns_names_the_package_and_none_for_no_owner(pacman: Pacman) -> None:
    assert (pacman.owns("/usr/bin/pacman"), pacman.owns("/etc/hostname")) == (
        "pacman", None)


def test_pacman_installed_by_name(pacman: Pacman) -> None:
    assert (pacman.installed("pacman"), pacman.installed("no-such-package")) == (
        True, False)


def test_pacman_provides_strips_the_repo_and_never_guesses(pacman: Pacman) -> None:
    """``extra/ffmpeg`` -> ``ffmpeg``; no files db -> None ("cannot
    determine"), never a name."""
    assert (pacman.provides("/usr/bin/ffmpeg"), pacman.provides("/usr/bin/7z")) == (
        "ffmpeg", None)


def test_pacman_install_argv() -> None:
    assert Pacman().install_argv("7zip") == ("sudo", "pacman", "-S", "7zip")


def test_without_pacman_nothing_is_known(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_packages.shutil, "which", lambda name: None)
    p = Pacman()
    assert (p.owns("/usr/bin/trcc"), p.provides("/usr/bin/7z"),
            p.installed("pacman")) == (None, None, False)


def test_the_pacman_family_asks_pacman(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arch and its family get ``Pacman``; until #333 they got
    ``NoPackageManager``, and every file read as unowned."""
    from trcc.adapters.system import linux

    arch = next(f for f in linux._FAMILIES if f.manager == "pacman")
    os_ = linux.LinuxOS()
    monkeypatch.setattr(os_, "_family", arch)
    assert isinstance(os_._build_packages(), Pacman)

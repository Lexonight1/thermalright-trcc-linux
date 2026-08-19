"""Each OS family answers with ITS OWN commands, not another family's.

One class served three BSDs until 2026-08-19, spelling every install
``pkg install`` — a command only FreeBSD has, so OpenBSD and NetBSD users were
told to run something their system does not provide.  Linux had the same shape:
four parallel tables keyed on package manager, in three files, one already
drifted short by two entries.

Both are now one class per family, and this is the gate that would have caught
either.  It asserts the DIFFERENCE — that apt says apt and does NOT say dnf —
because a test that only checks "returns a non-empty string" passes happily
while every family answers identically.

MUTATION CHECK -- point ``PacmanLinux._INSTALL_CMD`` at apt's string.
MEASURED 2026-08-19: **1 failed** —
``test_linux_family_uses_its_own_manager[PacmanLinux]``.  (A first draft
of this docstring predicted 2.  Run the mutation, then write the number.)
"""
from __future__ import annotations

import pytest

from trcc.adapters.system.bsd import FreeBsdOS, NetBsdOS, OpenBsdOS
from trcc.adapters.system.linux import _LINUX_FAMILIES, GenericLinux, LinuxOS


@pytest.mark.parametrize("family", _LINUX_FAMILIES, ids=lambda c: c.__name__)
def test_linux_family_uses_its_own_manager(family: type[LinuxOS]) -> None:
    """The install line names this family's manager and no other's."""
    hint = family().software_install_hint("ffmpeg")
    assert family._MANAGER.split("-")[0] in hint, (
        f"{family.__name__} does not name its own manager {family._MANAGER!r}")

    others = {f._MANAGER.split("-")[0] for f in _LINUX_FAMILIES} - {
        family._MANAGER.split("-")[0]}
    # "apk add" must not appear in a pacman hint, etc.
    for other in others:
        assert f"{other} " not in hint, (
            f"{family.__name__} emits {other}'s command: {hint!r}")


@pytest.mark.parametrize("family", _LINUX_FAMILIES, ids=lambda c: c.__name__)
def test_linux_family_upgrade_targets_our_package(family: type[LinuxOS]) -> None:
    """Every family that has an upgrade recipe upgrades trcc-linux with it."""
    cmd = family().upgrade_command()
    if not cmd:                       # NixOS is flake-managed — no single line
        assert family._MANAGER == "nix-env", (
            f"{family.__name__} has no upgrade command and is not NixOS")
        return
    assert "trcc-linux" in cmd
    assert family._MANAGER in cmd or family._MANAGER.split("-")[0] in cmd


def test_unrecognised_linux_says_so_rather_than_guessing() -> None:
    """GenericLinux must not answer as somebody else's distro."""
    hint = GenericLinux().software_install_hint("ffmpeg")
    assert "your package manager" in hint
    for mgr in ("apt", "dnf", "pacman", "zypper", "apk", "xbps"):
        assert f"{mgr} " not in hint
    assert GenericLinux().package_manager() == ""


def test_unconfirmed_package_name_is_not_borrowed_from_debian() -> None:
    """A family with no confirmed name must not inherit Debian's.

    Advising ``python3-pynvml`` where it does not exist is #207.  Alpine and
    Void have no confirmed pynvml package, so they get the pip fallback.
    """
    from trcc.adapters.system.linux import ApkLinux, PacmanLinux, XbpsLinux

    assert "python3-pynvml" not in ApkLinux().software_install_hint("pynvml")
    assert "python3-pynvml" not in XbpsLinux().software_install_hint("pynvml")
    # Arch HAS a confirmed name and must use it.
    assert "python-nvidia-ml-py" in PacmanLinux().software_install_hint("pynvml")


@pytest.mark.parametrize(
    ("bsd", "command", "forbidden"),
    [(FreeBsdOS, "pkg install", "pkg_add"),
     (OpenBsdOS, "pkg_add", "pkg install"),
     (NetBsdOS, "pkg_add", "pkg install")],
    ids=["freebsd", "openbsd", "netbsd"],
)
def test_each_bsd_uses_its_own_package_command(
    bsd: type, command: str, forbidden: str,
) -> None:
    """FreeBSD has pkg; OpenBSD and NetBSD have pkg_add.  Not interchangeable."""
    hint = bsd().software_install_hint("ffmpeg")
    assert command in hint
    assert forbidden not in hint

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
from trcc.adapters.system.linux import (
    _FAMILIES,
    _GENERIC_FAMILY,
    LinuxFamily,
    LinuxOS,
)
from trcc.adapters.system.macos import MacOSPlatform
from trcc.adapters.system.windows import WindowsPlatform
from trcc.core.models import DisplayServer


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda f: f.name)
def test_linux_family_uses_its_own_manager(family: LinuxFamily) -> None:
    """The install line names this family's manager and no other's.

    Parametrised over RECORDS now, not classes.  The eight LinuxOS subclasses
    this used to iterate defined zero methods between them; the family is data
    and is injected into the one class.
    """
    hint = LinuxOS(family).software_install_hint("ffmpeg")
    assert family.manager.split("-")[0] in hint, (
        f"{family.name} does not name its own manager {family.manager!r}")

    others = {f.manager.split("-")[0] for f in _FAMILIES} - {
        family.manager.split("-")[0]}
    # "apk add" must not appear in a pacman hint, etc.
    for other in others:
        assert f"{other} " not in hint, (
            f"{family.name} emits {other}'s command: {hint!r}")


#: The one native package each family can install -- the release's fixed-name
#: download, never the family's repo: no distro repo carries trcc-linux.
_NATIVE_PACKAGE = {"dnf": "trcc-linux-latest.noarch.rpm",
                   "apt": "trcc-linux-latest_all.deb",
                   "pacman": "trcc-linux-latest-any.pkg.tar.zst"}


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda f: f.name)
def test_a_package_install_is_told_how_to_upgrade_itself(
        family: LinuxFamily, monkeypatch: pytest.MonkeyPatch) -> None:
    """``trcc system upgrade`` ran ``sudo apt upgrade -y trcc-linux`` and the
    like, and trcc-linux is in no distro repo: apt upgraded the WHOLE system
    and reported success (measured: "23 upgraded", trcc unchanged, rc 0);
    pacman did a partial -Syu, then "target not found".  The hint now names
    the release's own package, or the release page where we ship none.
    MUTATION CHECK: hand back the old repo-upgrade line -> fails."""
    monkeypatch.setattr(LinuxOS, "install_method", lambda self: "package")

    hint = LinuxOS(family).upgrade_hint()

    assert "upgrade -y" not in hint and "-Syu" not in hint
    if (package := _NATIVE_PACKAGE.get(family.manager)) is not None:
        assert package in hint
    else:
        assert "github.com/Lexonight1/thermalright-trcc-linux/releases" in hint


@pytest.mark.parametrize(("method", "expected"), [
    ("pipx", "pipx upgrade trcc-linux"),
    ("pyinstaller", "github.com/Lexonight1/thermalright-trcc-linux/releases"),
])
def test_how_it_was_installed_decides_the_hint(
        method: str, expected: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LinuxOS, "install_method", lambda self: method)

    assert expected in LinuxOS(_FAMILIES[0]).upgrade_hint()


def test_the_legacy_deb_venv_is_told_to_install_the_legacy_deb(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The legacy deb pip-installs into /opt/trcc-linux, so it reads as "pip";
    a pip upgrade there would fight the package that owns the venv."""
    import sys
    monkeypatch.setattr(LinuxOS, "install_method", lambda self: "pip")
    monkeypatch.setattr(sys, "prefix", "/opt/trcc-linux")

    assert "trcc-linux-latest.legacy_all.deb" in LinuxOS(_FAMILIES[0]).upgrade_hint()


def test_unrecognised_linux_says_so_rather_than_guessing() -> None:
    """GenericLinux must not answer as somebody else's distro."""
    hint = LinuxOS(_GENERIC_FAMILY).software_install_hint("ffmpeg")
    assert "your package manager" in hint
    for mgr in ("apt", "dnf", "pacman", "zypper", "apk", "xbps"):
        assert f"{mgr} " not in hint
    assert LinuxOS(_GENERIC_FAMILY).package_manager() == ""


def test_unconfirmed_package_name_is_not_borrowed_from_debian() -> None:
    """A family with no confirmed name must not inherit Debian's.

    Advising ``python3-pynvml`` where it does not exist is #207.  Alpine and
    Void have no confirmed pynvml package, so they get the pip fallback.
    """
    by_manager = {f.manager: f for f in _FAMILIES}

    for manager in ("apk", "xbps-install"):
        hint = LinuxOS(by_manager[manager]).software_install_hint("pynvml")
        assert "python3-pynvml" not in hint, f"{manager} borrowed Debian's name"
    # Arch HAS a confirmed name and must use it.
    assert "python-nvidia-ml-py" in (
        LinuxOS(by_manager["pacman"]).software_install_hint("pynvml"))


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


# ── Enterprise Linux: same manager as Fedora, different package set ────────


@pytest.mark.parametrize(("label", "text", "is_el"), [
    ("fedora", 'ID=fedora\nVERSION_ID=44\n', False),
    ("rocky", 'ID="rocky"\nID_LIKE="rhel centos fedora"\n', True),
    ("almalinux", 'ID="almalinux"\nID_LIKE="rhel centos fedora"\n', True),
    ("rhel", 'ID="rhel"\nID_LIKE="fedora"\n', True),
    ("centos-stream", 'ID="centos"\nID_LIKE="rhel fedora"\n', True),
    ("oracle", 'ID="ol"\nID_LIKE="fedora"\n', True),
    ("debian", "ID=debian\n", False),
    ("empty", "", False),
])
def test_enterprise_linux_is_told_apart_from_fedora(
        label: str, text: str, is_el: bool) -> None:
    """Real /etc/os-release contents, not a mocked verdict.

    Fedora and EL both answer ``dnf``, so the binary probe cannot separate
    them — and it matters, because both binaries the app probes for are
    EPEL-only on EL.  There is no EL box here, so the file contents are
    injected; mocking the outcome would assert the rule against itself.
    """
    from trcc.adapters.system.linux import _is_enterprise_linux

    assert _is_enterprise_linux(text) is is_el, label


class _Packages:
    """A PackageManager whose EPEL answer the test dictates."""

    def __init__(self, epel: bool) -> None:
        self._epel = epel

    def owns(self, path: str) -> None:
        return None

    def provides(self, path: str) -> None:
        return None

    def installed(self, package: str) -> bool:
        return self._epel

    def install_argv(self, package: str) -> tuple[str, ...]:
        return ()


def _el(epel: bool) -> LinuxOS:
    from trcc.adapters.system.linux import _EL_FAMILY

    os_obj = LinuxOS(_EL_FAMILY)
    os_obj._packages = _Packages(epel)
    return os_obj


def test_el_without_epel_gets_a_command_that_works() -> None:
    """One runnable line, not a caveat.

    Whether EPEL is enabled is answerable locally (rpm -q epel-release), so
    the advice adapts instead of warning about a condition the user would have
    to check themselves.
    """
    hint = _el(epel=False).software_install_hint("7z")
    assert hint == "sudo dnf install epel-release && sudo dnf install /usr/bin/7z"
    assert "\n" not in hint, "fix_hint renders on one line"


def test_el_with_epel_gets_the_plain_command() -> None:
    """A user who already enabled EPEL must not be told to enable it again."""
    assert _el(epel=True).software_install_hint("7z") == (
        "sudo dnf install /usr/bin/7z")


def test_fedora_is_never_given_the_epel_step() -> None:
    """Fedora ships these in its own repos; the EPEL step would be noise."""
    from trcc.adapters.system.linux import _FAMILIES

    fedora = LinuxOS(next(f for f in _FAMILIES if f.manager == "dnf"))
    assert "epel" not in fedora.software_install_hint("7z")


def test_a_broken_package_query_never_breaks_the_hint() -> None:
    """A hint must not raise — it is printed when things are already wrong."""
    os_obj = LinuxOS.__new__(LinuxOS)
    from trcc.adapters.system.linux import _EL_FAMILY

    os_obj._family = _EL_FAMILY
    os_obj._packages = None                    # forces a build, then a failure

    class _Boom:
        def installed(self, package: str) -> bool:
            raise RuntimeError("rpmdb is locked")

    os_obj._packages = _Boom()
    assert os_obj.software_install_hint("7z") == "sudo dnf install /usr/bin/7z"


def test_detection_actually_selects_the_el_family(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The WIRING, not the predicate and the hint separately.

    Added because a mutation exposed the hole: disabling the EL branch in
    _detect_family failed nothing.  test_enterprise_linux_is_told_apart tested
    the predicate, and the EPEL tests injected an EL family directly, so the
    line joining them was covered by neither and could be deleted silently.
    """
    import trcc.adapters.system.linux as linux_mod

    monkeypatch.setattr(linux_mod.shutil, "which",
                        lambda name: "/usr/bin/dnf" if name == "dnf" else None)
    monkeypatch.setattr(linux_mod, "_is_enterprise_linux", lambda text=None: True)
    assert LinuxOS._detect_family().name == "EL-family"

    monkeypatch.setattr(linux_mod, "_is_enterprise_linux", lambda text=None: False)
    assert LinuxOS._detect_family().name == "Fedora-family"


def test_an_unrecognised_bsd_gets_a_product_not_the_factory() -> None:
    """resolve() must return something it MADE, not itself.

    It returned ``cls``: BsdOS is registered under "bsd" and is instantiable,
    so a DragonFly or MidnightBSD user silently became the base class and
    nothing in the output said so.
    """
    import inspect

    from trcc.adapters.system.bsd import BsdOS, GenericBsd

    resolved = BsdOS.resolve()
    assert resolved is not BsdOS, "the factory is still returning itself"
    assert resolved is GenericBsd
    assert not inspect.isabstract(GenericBsd)


def test_an_unknown_bsd_is_not_told_to_run_pkg_add() -> None:
    """An unknown BSD is not a pkg_add BSD.

    DragonFly uses ``pkg``, MidnightBSD uses ``mport``.  Guessing pkg_add here
    would be the #207 failure -- a command the system does not have -- which is
    what ab2ff630 fixed when one class told OpenBSD to run ``pkg install``.
    """
    from trcc.adapters.system.bsd import GenericBsd

    hint = GenericBsd().software_install_hint("7z")
    for guess in ("pkg_add", "pkg install", "mport"):
        assert guess not in hint, f"GenericBsd guessed {guess!r}: {hint!r}"
    assert "PATH" in hint


def test_the_named_bsds_still_answer_for_themselves() -> None:
    """Adding the generic must not change the three that are recognised."""
    from trcc.adapters.system.bsd import FreeBsdOS, NetBsdOS, OpenBsdOS

    assert "pkg install" in FreeBsdOS().software_install_hint("7z")
    assert "pkg_add" in OpenBsdOS().software_install_hint("7z")
    assert "pkg_add ffmpeg7" in NetBsdOS().software_install_hint("ffmpeg")


# ── the display session: an OS fact, answered by the OS ──────────────────

_SESSION_VARS = ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY",
                 "XDG_CURRENT_DESKTOP")


@pytest.mark.parametrize(("env", "server", "desktops"), [
    ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"},
     DisplayServer.WAYLAND, ("kde",)),
    ({"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"},
     DisplayServer.WAYLAND, ("ubuntu", "gnome")),
    ({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "XFCE"},
     DisplayServer.X11, ("xfce",)),
    ({"DISPLAY": ":0"}, DisplayServer.X11, ()),
    ({}, DisplayServer.HEADLESS, ()),
], ids=["plasma-wayland", "ubuntu-gnome-wayland", "xfce-x11", "bare-display",
        "headless"])
def test_the_display_session_is_read_from_the_session_variables(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str],
    server: DisplayServer, desktops: tuple[str, ...],
) -> None:
    """Until 2026-09-18 nothing in the tree could answer this, so the capture
    chain ran every desktop's tool on every desktop: on Plasma that was an
    X11 grabber ringing the bell two to three times a second and a wlroots
    tool failing every tick.  The desktop list is kept whole and lower-cased
    because the spec puts the most specific name FIRST -- ``ubuntu:GNOME`` --
    so "which family" is membership, never the head.
    """
    for var in _SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)

    session = LinuxOS().display_session()

    assert (session.server, session.desktops) == (server, desktops)


@pytest.mark.parametrize("os_", [WindowsPlatform, MacOSPlatform],
                         ids=lambda c: c.__name__)
def test_native_windowing_ignores_leftover_session_variables(
    monkeypatch: pytest.MonkeyPatch, os_: type,
) -> None:
    """An XQuartz or WSL variable must not turn Windows or macOS into an X11
    session: there the OS's own windowing is the whole answer.
    """
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("DISPLAY", ":0")

    assert os_().display_session().server is DisplayServer.NATIVE

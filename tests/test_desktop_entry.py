"""XdgDesktopEntry — the applications-menu entry a pip install never got.

Our packages register TRCC in the menu; `pip`/`pipx` ships the same files
inside the wheel and registers nothing.  A SteamOS user spent a whole thread
watching the app autostart into his tray with no way to launch it again
after closing it (#231), because SteamOS has no package and pip is the only
route there.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.adapters.system import _desktop_entry
from trcc.adapters.system._desktop_entry import XdgDesktopEntry


@pytest.fixture
def unpackaged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a pip install: no distro package owns the menu entry."""
    monkeypatch.setattr(
        _desktop_entry, "_SYSTEM_ENTRY", Path("/nonexistent/trcc-linux.desktop"),
    )


def test_not_installed_on_a_clean_home(tmp_home: Path, unpackaged: None) -> None:
    assert XdgDesktopEntry().is_installed() is False


def test_install_writes_the_menu_entry(tmp_home: Path, unpackaged: None) -> None:
    entry = XdgDesktopEntry()

    assert entry.install() is True

    assert entry.is_installed() is True
    assert entry.path.parent.name == "applications"
    assert entry.path.name == "trcc-linux.desktop"


def test_entry_has_the_xdg_required_fields(tmp_home: Path, unpackaged: None) -> None:
    entry = XdgDesktopEntry()

    entry.install()

    text = entry.path.read_text(encoding="utf-8")
    assert text.startswith("[Desktop Entry]"), "spec-required header"
    assert "\nType=Application\n" in text
    assert "\nName=" in text
    assert "\nExec=" in text


def test_exec_is_rewritten_to_a_resolvable_command(
    tmp_home: Path, unpackaged: None,
) -> None:
    """The shipped file says `Exec=trcc gui`, which assumes PATH.

    A venv install the user never activated would get a menu entry that
    silently does nothing — worse than having no entry at all.
    """
    entry = XdgDesktopEntry()

    entry.install()

    exec_line = next(
        line for line in entry.path.read_text(encoding="utf-8").splitlines()
        if line.startswith("Exec=")
    )
    assert exec_line.endswith(" gui"), exec_line
    # Either the resolved console script or `<python> -m trcc gui`.
    assert exec_line.startswith("Exec=/"), (
        f"Exec must be an absolute command, not a bare name: {exec_line}"
    )


def test_icons_land_in_the_hicolor_theme(tmp_home: Path, unpackaged: None) -> None:
    """`Icon=trcc` only resolves if the PNGs are named trcc.png in hicolor."""
    entry = XdgDesktopEntry()

    entry.install()

    icon_root = entry.path.parents[1] / "icons" / "hicolor"
    installed = sorted(p for p in icon_root.rglob("trcc.png"))
    assert installed, f"no icons under {icon_root}"
    for icon in installed:
        assert icon.parent.name == "apps", icon
        assert icon.parent.parent.name.count("x") == 1, icon


def test_icon_name_matches_what_the_entry_asks_for(
    tmp_home: Path, unpackaged: None,
) -> None:
    """Guard the pair that silently drifted once already.

    The autostart template asked for `Icon=trcc-linux` while every install
    path shipped the icon as `trcc.png`, so that entry referenced an icon
    that did not exist anywhere.  Name and file must agree.
    """
    entry = XdgDesktopEntry()

    entry.install()

    icon_field = next(
        line.split("=", 1)[1]
        for line in entry.path.read_text(encoding="utf-8").splitlines()
        if line.startswith("Icon=")
    )
    icon_root = entry.path.parents[1] / "icons" / "hicolor"
    assert list(icon_root.rglob(f"{icon_field}.png")), (
        f"entry asks for Icon={icon_field} but no {icon_field}.png was installed"
    )


def test_a_packaged_install_is_left_alone(tmp_home: Path) -> None:
    """When a distro package owns the entry, do nothing at all.

    A shadow copy in ~/.local/share would outrank the package's and then go
    stale on the next upgrade — the user would keep launching the old path.
    """
    entry = XdgDesktopEntry()

    # _SYSTEM_ENTRY is NOT patched out here: on a packaged box it exists, on
    # a bare one it does not, and the contract differs per case.
    if _desktop_entry._SYSTEM_ENTRY.is_file():
        assert entry.install() is False
        assert not entry.path.exists(), "must not shadow the packaged entry"
    else:
        assert entry.install() is True


def test_install_is_idempotent(tmp_home: Path, unpackaged: None) -> None:
    entry = XdgDesktopEntry()

    assert entry.install() is True
    first = entry.path.read_text(encoding="utf-8")
    assert entry.install() is True

    assert entry.path.read_text(encoding="utf-8") == first


def test_uninstall_removes_the_entry(tmp_home: Path, unpackaged: None) -> None:
    entry = XdgDesktopEntry()
    entry.install()

    entry.uninstall()

    assert entry.is_installed() is False
    assert not entry.path.exists()

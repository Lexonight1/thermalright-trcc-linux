"""Path containment must survive symlinks (#261).

"Is this the user's own asset or a shipped one?" is asked in six places, and
it decides which theme gets restored and how a background is fitted.  It used
to be asked with ``Path.is_relative_to``, which compares path *strings* and
never touches the filesystem — so on any system where the same directory has
two absolute spellings, the answer could be wrong.

Fedora Atomic systems (Bazzite, Silverblue) are exactly that: ``/home`` is a
symlink to ``/var/home``.  Four of the reporters in our queue run Bazzite.
"""
from __future__ import annotations

from pathlib import Path

from trcc.core._safe import is_under


def _bazzite_home(tmp_path: Path) -> tuple[Path, Path]:
    """Recreate the Bazzite layout: /home -> /var/home, same dir two ways."""
    real = tmp_path / "var" / "home" / "u" / ".trcc-user"
    (real / "data" / "theme854480" / "MyTheme").mkdir(parents=True)
    (tmp_path / "home").symlink_to(tmp_path / "var" / "home")
    via_symlink = (tmp_path / "home" / "u" / ".trcc-user"
                   / "data" / "theme854480" / "MyTheme")
    return via_symlink, real


def test_a_user_theme_reached_through_a_symlink_is_still_a_user_theme(
    tmp_path: Path,
) -> None:
    """#261: a saved theme was classified as shipped, so restore loaded the
    shipped theme of the same name — "my settings reset every restart".

    MUTATION CHECK: swap ``is_under`` back for a bare
    ``via_symlink.is_relative_to(user_root)`` and this fails — it answers
    False for a path that is plainly inside the directory.
    """
    via_symlink, real_root = _bazzite_home(tmp_path)

    # The lexical answer, which is what shipped and what was wrong.
    assert via_symlink.is_relative_to(real_root) is False

    assert is_under(via_symlink, real_root) is True


def test_the_same_holds_when_the_root_is_the_symlinked_form(
    tmp_path: Path,
) -> None:
    """Either side may be the symlinked spelling — one component resolves the
    home directory and another does not, and we do not control which."""
    via_symlink, _ = _bazzite_home(tmp_path)
    symlinked_root = tmp_path / "home" / "u" / ".trcc-user"

    assert is_under(via_symlink, symlinked_root) is True
    assert is_under(via_symlink.resolve(), symlinked_root) is True


def test_a_path_outside_the_root_is_still_outside(tmp_path: Path) -> None:
    """Resolving must not turn every path into a match."""
    _, real_root = _bazzite_home(tmp_path)
    shipped = tmp_path / "var" / "home" / "u" / ".trcc" / "data" / "theme854480" / "T1"
    shipped.mkdir(parents=True)

    assert is_under(shipped, real_root) is False


def test_a_path_that_does_not_exist_yet_still_answers(tmp_path: Path) -> None:
    """Theme paths are compared before they are written, so a missing path
    must resolve lexically rather than raise."""
    _, real_root = _bazzite_home(tmp_path)
    not_yet = real_root / "data" / "theme854480" / "NotSavedYet"

    assert not_yet.exists() is False
    assert is_under(not_yet, real_root) is True


def test_no_lexical_containment_check_survives_outside_the_helper() -> None:
    """The rule was applied inconsistently — four of six sites resolved both
    sides, two compared raw paths — which is why it needed a seam rather than
    discipline.  Keep it that way.
    """
    import trcc

    root = Path(trcc.__file__).parent
    offenders = [
        f"{p.relative_to(root)}:{n}"
        for p in root.rglob("*.py")
        if p.name != "_safe.py"
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "is_relative_to" in line
    ]
    assert offenders == [], (
        "use core._safe.is_under — Path.is_relative_to is lexical and gets "
        f"symlinked homes wrong (#261): {offenders}"
    )

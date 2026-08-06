"""The udev health check must look for the file we actually install.

It didn't.  `check_udev_rules_linux` searched for `99-trcc.rules`, a name
nothing in this project has ever written — the writer lays down
`99-trcc-lcd.rules`, and the packages install that same name under /etc
(RPM) or /lib (deb, Arch).

So every correctly configured Linux machine was told "No TRCC udev rules
found", in the diagnostic we ask people to run when their device isn't
detected.  One reporter reasonably concluded setup had aborted before the
udev step and went looking for a bug that wasn't there (#258).

A hardcoded copy of a path owned elsewhere is the whole defect, so these
tests compare against the writer rather than against a literal.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trcc.adapters.diagnostics import health
from trcc.adapters.system._udev import RULES_PATH


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only check")
def test_check_finds_the_file_the_writer_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact path `trcc system setup` creates must read as installed."""
    monkeypatch.setattr(Path, "is_file", lambda self: self == RULES_PATH)

    result = health.check_udev_rules_linux()

    assert result.severity == "OK", result.message
    assert str(RULES_PATH) in result.message


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only check")
@pytest.mark.parametrize("directory", ["/lib/udev/rules.d", "/usr/lib/udev/rules.d"])
def test_check_finds_the_packaged_locations(
    directory: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deb and Arch packages install under /lib, not /etc."""
    packaged = Path(directory) / RULES_PATH.name
    monkeypatch.setattr(Path, "is_file", lambda self: self == packaged)

    result = health.check_udev_rules_linux()

    assert result.severity == "OK", result.message
    assert str(packaged) in result.message


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only check")
def test_warns_when_nothing_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "is_file", lambda self: False)

    result = health.check_udev_rules_linux()

    assert result.severity == "WARN"
    assert result.fix_hint is not None
    assert str(RULES_PATH) in result.fix_hint, (
        "the hint must name the file we actually write, or it sends the "
        "reader after a file that will never exist"
    )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only check")
def test_a_legacy_rules_file_still_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boxes carrying an older install's file are still configured."""
    legacy = Path("/etc/udev/rules.d/99-trcc.rules")
    monkeypatch.setattr(Path, "is_file", lambda self: self == legacy)

    assert health.check_udev_rules_linux().severity == "OK"


def test_no_user_facing_text_names_a_rules_file_we_never_write() -> None:
    """Grep the shipping tree + guides for the wrong filename.

    Seven places named `99-trcc.rules`, including the uninstall
    instructions, which told users to `rm -f` a path that does not exist
    and so left the real rule installed on every manual uninstall.
    """
    root = Path(__file__).resolve().parents[1]
    searched = [
        *(root / "src").rglob("*.py"),
        *(root / "doc").glob("*.md"),
        root / "install.sh",
    ]
    offenders: list[str] = []
    for path in searched:
        if not path.is_file() or path.name in {"_udev.py", "health.py"}:
            continue        # both mention the legacy name on purpose
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1,
        ):
            if "99-trcc.rules" in line:
                offenders.append(f"{path.relative_to(root)}:{number}")
    assert not offenders, (
        "these name a udev rules file nothing installs — the real one is "
        f"{RULES_PATH.name}:\n  " + "\n  ".join(offenders)
    )

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
from typing import Any

import pytest

from trcc.adapters.diagnostics import health
from trcc.adapters.system._udev import RULES_DIRS, RULES_PATH


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only check")
def _current_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every stubbed rule file reads as THIS version's.  These tests are about
    WHERE the rule is found; unstubbed, the check read the dev box's real
    /etc copy -- an older package's -- and called it stale (it is)."""
    from trcc.adapters.system._udev import build_udev_rules
    current = {RULES_PATH.name: build_udev_rules()}
    monkeypatch.setattr(Path, "read_text",
                        lambda self, *a, **k: current.get(self.name, ""))


def _installed(monkeypatch: pytest.MonkeyPatch, *paths: Path) -> None:
    """Only *paths* exist."""
    present = {*paths}
    monkeypatch.setattr(Path, "is_file", lambda self: self in present)


def test_check_finds_the_file_the_writer_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact path `trcc system setup` creates must read as installed."""
    _installed(monkeypatch, RULES_PATH)
    _current_content(monkeypatch)

    result = health.check_udev_rules_linux()

    assert result.severity == "OK", result.message
    assert str(RULES_PATH) in result.message


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only check")
@pytest.mark.parametrize("directory", RULES_DIRS, ids=str)
def test_check_finds_the_rule_in_every_directory_udev_reads(
    directory: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The packages install under /lib or /usr/lib, a `make install` under
    /usr/local/lib, and udev also reads /run -- two of which this check once
    ignored (#273).  Parametrized over the writer's list, not a copy of it, so
    the next directory added there is covered here without anyone remembering.
    """
    installed = directory / RULES_PATH.name
    _installed(monkeypatch, installed)
    _current_content(monkeypatch)

    result = health.check_udev_rules_linux()

    assert result.severity == "OK", result.message
    assert result.message == f"udev rules installed: {installed}"


def test_rules_dirs_cover_every_directory_udev_reads() -> None:
    """udev(7), "RULES FILES": rules are read from /usr/lib/udev/rules.d,
    /usr/local/lib/udev/rules.d, /run/udev/rules.d and /etc/udev/rules.d.

    That list is restated from the manual on purpose -- it is the oracle.  A
    test parametrized over RULES_DIRS cannot notice a directory missing from
    RULES_DIRS: drop /run there and its case simply disappears, still green
    (measured).  This one fails.
    """
    udev_7_rules_files = {
        Path("/usr/lib/udev/rules.d"),
        Path("/usr/local/lib/udev/rules.d"),
        Path("/run/udev/rules.d"),
        Path("/etc/udev/rules.d"),
    }
    missing = udev_7_rules_files - set(RULES_DIRS)
    assert not missing, (
        "udev reads these but the health check never looks there: "
        f"{sorted(map(str, missing))}"
    )


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
    _installed(monkeypatch, legacy)
    _current_content(monkeypatch)

    assert health.check_udev_rules_linux().severity == "OK"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only check")
@pytest.mark.parametrize("beside_the_new_one", [False, True], ids=["alone", "beside-new"])
def test_trccs_every_user_rule_file_is_named(
        beside_the_new_one: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """The 99- file from before 2026-10-22 still works -- for every user --
    and read after the 70- rule its mode wins.  Alone it used to read as
    "no rules found".  MUTATION CHECK: drop the legacy check."""
    from trcc.adapters.system._udev import LEGACY_RULES_NAME, build_udev_rules

    legacy = Path("/etc/udev/rules.d") / LEGACY_RULES_NAME
    present = {legacy} | ({RULES_PATH} if beside_the_new_one else set())
    monkeypatch.setattr(Path, "is_file", lambda self: self in present)
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (
        build_udev_rules() if self == RULES_PATH else
        "# Thermalright TRCC devices — generated by `trcc system setup` "
        "(and packaged)\n"))

    result = health.check_udev_rules_linux()

    assert (result.severity, result.message, result.fix_hint) == (
        "WARN",
        f"udev rules in {legacy} are from an older TRCC and let every user on "
        "this computer reach the coolers",
        "Run `trcc system setup` -- it replaces them with rules for you and "
        "the trcc group only")


def test_a_module_that_cannot_reach_its_devices_is_reported(fake_platform: Any) -> None:
    """Doctor names each locked module and the one way to unlock it; the
    rest stay quiet.  MUTATION CHECK: drop ``check_module_devices`` from
    ``run_health_checks``."""
    from trcc.core.models import ModuleInfo

    rgb = ModuleInfo(key="rgb", title="RGB Lighting", summary="", enabled=True,
                     locked="Its USB devices need your permission")
    quiet = ModuleInfo(key="visualizer", title="Audio Visualizer", summary="",
                       enabled=True)
    result = health.check_module_devices([rgb, quiet])
    assert (result.name, result.severity, result.message, result.fix_hint) == (
        "module-devices", "WARN",
        "RGB Lighting: Its USB devices need your permission",
        "trcc system modules unlock rgb -- or press Unlock on its tile in Modules")
    assert health.check_module_devices([quiet]).severity == "OK"
    assert "module-devices" in {c.name for c in health.run_health_checks(
        fake_platform, modules=[rgb]).checks}


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



# ── the device group, when TRCC runs as the user's service (2026-10-22) ──

@pytest.fixture
def as_alice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """alice (uid 1000), her user service written or not, the trcc group's
    members chosen per test -- none of it this machine's."""
    import grp
    import pwd
    import types

    from trcc.adapters.system import _autostart

    unit = tmp_path / "trccd.service"
    monkeypatch.setattr(health.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(health.os, "getuid", lambda: 1000)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: types.SimpleNamespace(
        pw_name="alice", pw_gid=1000))
    monkeypatch.setattr(_autostart, "SystemdUserService",
                        lambda: types.SimpleNamespace(path=unit))

    def group(members: list[str] | None) -> None:
        def getgrnam(name: str) -> object:
            if members is None:
                raise KeyError(name)
            return types.SimpleNamespace(gr_mem=members, gr_gid=1003)
        monkeypatch.setattr(grp, "getgrnam", getgrnam)
    return unit, group


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only check")
@pytest.mark.parametrize("service, members, severity", [
    (False, [], "OK"),
    (True, [], "WARN"),
    (True, None, "WARN"),
    (True, ["alice"], "OK"),
], ids=["no-service", "not-a-member", "no-group", "member"])
def test_the_user_service_needs_the_device_group(
        as_alice, service: bool, members, severity: str) -> None:
    """After a logout only the group reaches the coolers, so a user service
    without it loses them.  MUTATION CHECK: ignore the membership."""
    unit, group = as_alice
    if service:
        unit.write_text("[Unit]\n", encoding="utf-8")
    group(members)
    result = health.check_device_group_linux()
    assert result.severity == severity
    if severity == "WARN":
        assert (result.message, result.fix_hint) == (
            "TRCC runs as your user service, which keeps the panels after you "
            "log out, but alice is not in the trcc group -- after a logout it "
            "cannot reach them",
            "sudo usermod -aG trcc alice, then log out and back in (or run "
            "`trcc system setup`)")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only check")
def test_a_rule_opening_every_disk_to_every_user_is_reported(tmp_path: Path) -> None:
    """Only the rule with no device in it and a world-writable mode: a
    rule for one device, a 0660 one, a comment and TRCC's own pass.
    MUTATION CHECK: drop the ``_ONE_DEVICE`` test."""
    from trcc.adapters.system._udev import build_udev_rules

    etc, lib = tmp_path / "etc", tmp_path / "lib"
    etc.mkdir()
    lib.mkdir()
    (etc / "99-old.rules").write_text('SUBSYSTEM=="scsi_generic", MODE="0666"\n')
    (etc / "50-one.rules").write_text(
        '# SUBSYSTEM=="scsi_generic", MODE="0666"\n'
        'SUBSYSTEM=="scsi_generic", ATTRS{idVendor}=="0402", MODE="0666"\n'
        'SUBSYSTEM=="scsi_generic", MODE="0660", GROUP="disk"\n')
    (lib / "70-trcc-lcd.rules").write_text(build_udev_rules())

    result = health.check_world_open_scsi_linux([etc, lib])
    assert (result.name, result.severity, result.message) == (
        "udev-raw-scsi", "WARN",
        "a udev rule lets every user on this computer send raw SCSI commands "
        f"to every disk: {etc / '99-old.rules'}:1")
    (etc / "99-old.rules").unlink()
    assert health.check_world_open_scsi_linux([etc, lib]).severity == "OK"

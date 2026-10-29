"""``LinuxModuleDevices``: the App's side of a module's USB devices -- which
helper it runs, how, and what it tells the user.  The rules live in a temp
dir and every helper run is recorded; nothing runs pkexec, nothing reaches /etc.
"""
from __future__ import annotations

import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from trcc.adapters.system import _module_devices, linux
from trcc.adapters.system._module_devices import (
    HELPER,
    HELPER_ASSET,
    HELPER_OFF,
    LinuxModuleDevices,
    NixosModuleDevices,
)

KEY = "aura"
IDS = ("0b05:19af", "0b05:1aa6")


def _helper() -> ModuleType:
    asset = str(HELPER_ASSET)
    loader = SourceFileLoader("_helper_trcc_module_devices", asset)
    module = ModuleType(loader.name)
    loader.exec_module(module)
    return module


class _Run:
    """Records each helper run; *grants* is what a run leaves on disk."""

    def __init__(self, rules: Path, code: int = 0, grants: tuple[str, ...] = ()) -> None:
        self.rules, self.code, self.grants = rules, code, grants
        self.argv: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.argv.append(argv)
        if self.grants:
            _granted(self.rules, self.grants)
        return subprocess.CompletedProcess(argv, self.code, "", "")


def _granted(rules: Path, devices: tuple[str, ...], key: str = KEY) -> None:
    """The rule file as the real helper writes it."""
    rules.mkdir(parents=True, exist_ok=True)
    (rules / f"70-trcc-module-{key}.rules").write_text(
        _helper().rule_text(key, list(devices)), encoding="utf-8")


def _packaged(program: str) -> list[str]:
    return ["pkexec", program]


def _devices(tmp_path: Path, run: _Run, *, pkexec: Any = _packaged,
             root: bool = False, which: str | None = "/usr/bin/pkexec",
             cls: type[LinuxModuleDevices] = LinuxModuleDevices) -> LinuxModuleDevices:
    return cls(pkexec, rules_dir=tmp_path, run=run, is_root=lambda: root,
               which=lambda name: which)


# ── reading what is granted ───────────────────────────────────────────────


def test_it_reads_back_what_the_helper_writes(tmp_path: Path) -> None:
    """The helper may import nothing from trcc: its header and lines are
    read here, and must agree."""
    helper = _helper()
    assert helper.header(KEY) == _module_devices.header(KEY)
    _granted(tmp_path, IDS)
    assert _devices(tmp_path, _Run(tmp_path)).granted(KEY) == IDS


def test_a_rule_that_is_not_trccs_reads_as_nothing_granted(tmp_path: Path) -> None:
    (tmp_path / f"70-trcc-module-{KEY}.rules").write_text(
        '# my own\nSUBSYSTEM=="usb", ATTR{idVendor}=="0b05", ATTR{idProduct}=="19af"\n')
    run = _Run(tmp_path)
    assert _devices(tmp_path, run).granted(KEY) == ()
    _devices(tmp_path, run).revoke(KEY)
    assert run.argv == []


# ── granting ──────────────────────────────────────────────────────────────


def test_a_grant_runs_trccs_own_polkit_action(tmp_path: Path) -> None:
    run = _Run(tmp_path, grants=IDS)
    assert _devices(tmp_path, run).grant(KEY, IDS) == ""
    assert run.argv == [["pkexec", HELPER, "enable", KEY, *IDS]]


def test_a_grant_already_in_place_asks_for_no_password(tmp_path: Path) -> None:
    _granted(tmp_path, IDS)
    run = _Run(tmp_path)
    assert _devices(tmp_path, run).grant(KEY, tuple(reversed(IDS))) == ""
    assert run.argv == []


def test_a_cancelled_password_says_the_devices_stay_locked(tmp_path: Path) -> None:
    run = _Run(tmp_path, code=126)
    assert _devices(tmp_path, run).grant(KEY, IDS) == (
        "its USB devices stay locked -- the password was not given")


def test_a_failed_helper_says_its_exit(tmp_path: Path) -> None:
    assert _devices(tmp_path, _Run(tmp_path, code=1)).grant(KEY, IDS) == (
        "could not unlock its USB devices (exit 1)")


def test_a_rule_written_but_not_applied_says_to_replug(tmp_path: Path) -> None:
    """Driven: in a sandbox without udev the helper writes the rule and exits
    1 -- the devices unlock at the next plug, not now."""
    assert _devices(tmp_path, _Run(tmp_path, code=1, grants=IDS)).grant(KEY, IDS) == (
        "its USB devices unlock once you replug them (exit 1)")


def test_without_a_packaged_helper_polkit_runs_the_packages_own_copy(
        tmp_path: Path) -> None:
    run = _Run(tmp_path, grants=IDS)
    assert _devices(tmp_path, run, pkexec=lambda program: None).grant(KEY, IDS) == ""
    assert run.argv == [["/usr/bin/pkexec", sys.executable, "-I", str(HELPER_ASSET),
                         "enable", KEY, *IDS]]


def test_without_polkit_it_names_the_command_that_will(tmp_path: Path) -> None:
    run = _Run(tmp_path)
    note = _devices(tmp_path, run, pkexec=lambda program: None, which=None).grant(KEY, IDS)
    assert note == ("to let you reach its USB devices, run in a terminal: sudo "
                    f"{sys.executable} -I {HELPER_ASSET} enable {KEY} {' '.join(IDS)}")
    assert run.argv == []


# ── taking back ───────────────────────────────────────────────────────────


def test_taking_back_runs_the_password_free_twin(tmp_path: Path) -> None:
    _granted(tmp_path, IDS)
    run = _Run(tmp_path)
    _devices(tmp_path, run).revoke(KEY)
    assert run.argv == [["pkexec", HELPER_OFF, KEY]]


def test_taking_back_what_was_never_granted_runs_nothing(tmp_path: Path) -> None:
    run = _Run(tmp_path)
    _devices(tmp_path, run).revoke(KEY)
    assert run.argv == []


def test_a_grant_of_nothing_takes_back(tmp_path: Path) -> None:
    _granted(tmp_path, IDS)
    run = _Run(tmp_path)
    assert _devices(tmp_path, run).grant(KEY, ()) == ""
    assert run.argv == [["pkexec", HELPER_OFF, KEY]]


def test_as_root_it_runs_the_helper_file_itself(tmp_path: Path) -> None:
    _granted(tmp_path, IDS)
    run = _Run(tmp_path)
    _devices(tmp_path, run, root=True).revoke(KEY)
    assert run.argv == [[sys.executable, "-I", str(HELPER_ASSET), "disable", KEY]]


# ── NixOS and the other systems ───────────────────────────────────────────


def test_on_nixos_it_names_the_rule_to_declare_and_runs_nothing(tmp_path: Path) -> None:
    run = _Run(tmp_path)
    devices = _devices(tmp_path, run, cls=NixosModuleDevices)
    assert devices.grant(KEY, IDS) == (
        "on NixOS, add a udev rule for its USB devices (0b05:19af, 0b05:1aa6) to "
        "services.udev.extraRules in your configuration, then run sudo "
        "nixos-rebuild switch")
    devices.revoke(KEY)
    assert run.argv == []


def test_linux_grants_through_the_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linux, "_is_nixos", lambda: False)
    assert type(linux.LinuxOS().module_devices()) is LinuxModuleDevices
    monkeypatch.setattr(linux, "_is_nixos", lambda: True)
    assert type(linux.LinuxOS().module_devices()) is NixosModuleDevices


@pytest.mark.parametrize(("platform", "note"), [
    ("windows", ""), ("macos", ""),
    ("bsd", "TRCC cannot unlock a module's USB devices on this system yet"),
])
def test_where_any_program_opens_a_usb_device_there_is_nothing_to_grant(
        platform: str, note: str) -> None:
    from trcc.adapters.system import bsd, macos, windows
    cls = {"windows": windows.WindowsPlatform, "macos": macos.MacOSPlatform,
           "bsd": bsd.BsdOS}[platform]
    devices = cls.__new__(cls).module_devices()
    assert devices.grant(KEY, IDS) == note
    assert devices.grant(KEY, ()) == ""

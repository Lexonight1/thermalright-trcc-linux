"""``LinuxRamAccess`` -- where the user stands on the RAM bus, and the grant.

Every state is read from a fake /dev and rule file: nothing here opens a bus,
runs pkexec or touches /etc.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from trcc.adapters.system import _ram_access
from trcc.adapters.system._ram_access import (
    HEADER,
    HELPER,
    NIXOS_HEADER,
    LinuxRamAccess,
    NixosRamAccess,
)
from trcc.core.models import RamAccessState, RamAccessStatus


class _Run:
    """Records each helper run and answers with *code*."""

    def __init__(self, code: int = 0) -> None:
        self.code, self.argv = code, []

    def __call__(self, argv, **_kw):
        self.argv.append(list(argv))
        return subprocess.CompletedProcess(argv, self.code, "", "")


def _access(tmp_path: Path, *, buses=(3,), nodes=(3,), ours: bool | None = None,
            pkexec: bool = True, polkit: bool = True, root: bool = False,
            run: _Run | None = None) -> LinuxRamAccess:
    """*pkexec*: TRCC's packaged helper + policy; *polkit*: pkexec at all."""
    dev = tmp_path / "dev"
    dev.mkdir(exist_ok=True)
    for n in nodes:
        (dev / f"i2c-{n}").write_bytes(b"")
    rule = tmp_path / "70-trcc-ram-lighting.rules"
    if ours is not None:
        rule.write_text((HEADER if ours else "# an admin's own rule") + "\n",
                        encoding="utf-8")
    return LinuxRamAccess(
        lambda helper: ["pkexec", helper] if pkexec else None,
        find=lambda: tuple(buses), dev=dev, rule=rule,
        run=run or _Run(), is_root=lambda: root,
        which=lambda name: f"/usr/bin/{name}" if polkit else None)


@pytest.mark.parametrize("kwargs, state", [
    (dict(buses=()), RamAccessState.NO_BUS),
    (dict(ours=True), RamAccessState.ON),
    (dict(ours=None), RamAccessState.ELSEWHERE),
    (dict(nodes=(), ours=True), RamAccessState.NOT_APPLIED),
    (dict(nodes=(), ours=None), RamAccessState.OFF),
    (dict(nodes=(), ours=False), RamAccessState.OFF),
], ids=["no-bus", "ours-and-access", "someone-elses", "installed-not-applied",
        "off", "an-admins-file-is-not-ours"])
def test_where_the_user_stands(tmp_path: Path, kwargs, state) -> None:
    assert _access(tmp_path, **kwargs).status().state is state


def test_root_reads_the_rule_not_its_own_access(tmp_path: Path) -> None:
    """Root opens every node, which says nothing about the user it acts for."""
    assert _access(tmp_path, root=True, ours=None).status().state is RamAccessState.OFF
    assert _access(tmp_path, root=True, ours=True).status().state is RamAccessState.ON


def test_without_polkit_the_status_names_a_command_that_works(tmp_path: Path) -> None:
    """Not ``sudo trcc ...``: sudo drops ~/.local/bin, and root's Python
    cannot import a package installed for the user."""
    assert _access(tmp_path, nodes=()).status().command == ""
    off = _access(tmp_path, nodes=(), pkexec=False, polkit=False).status()
    assert off.command == (f"sudo {sys.executable} -I "
                           f"{_ram_access.HELPER_ASSET} enable")


def test_any_install_with_polkit_gets_the_password_prompt(tmp_path: Path) -> None:
    """pip, pipx, a source checkout: no /usr/bin helper, so pkexec runs the
    package's own helper file -- the same file, the same checks.

    MUTATION CHECK: drop the ``which("pkexec")`` branch in ``_argv``."""
    run = _Run(0)
    access = _access(tmp_path, nodes=(), pkexec=False, run=run)
    assert access.status().command == ""
    access.enable()
    assert run.argv == [["/usr/bin/pkexec", sys.executable, "-I",
                         str(_ram_access.HELPER_ASSET), "enable"]]


def test_enable_asks_pkexec_for_the_packaged_helper(tmp_path: Path) -> None:
    run = _Run(0)
    _access(tmp_path, nodes=(), run=run).enable()
    assert run.argv == [["pkexec", HELPER, "enable"]]


def test_a_dismissed_password_prompt_changes_nothing(tmp_path: Path) -> None:
    status = _access(tmp_path, nodes=(), run=_Run(126)).enable()
    assert status.message == "Cancelled -- nothing was changed"
    assert status.state is RamAccessState.OFF


def test_root_runs_the_packaged_helper_file_itself(tmp_path: Path) -> None:
    """``sudo trcc system ram-lighting enable`` on a pip install: no /usr/bin
    helper, so the package's own file -- one writer, never a second one."""
    run = _Run(0)
    _access(tmp_path, root=True, run=run).disable()
    assert run.argv == [[sys.executable, "-I", str(_ram_access.HELPER_ASSET),
                         "disable"]]
    assert _ram_access.HELPER_ASSET.is_file()


def test_nothing_can_enable_it_here_so_nothing_runs(tmp_path: Path) -> None:
    run = _Run(0)
    status = _access(tmp_path, nodes=(), pkexec=False, polkit=False,
                     run=run).enable()
    assert run.argv == []
    assert status.message == (f"Run in a terminal: sudo {sys.executable} -I "
                              f"{_ram_access.HELPER_ASSET} enable")


def test_an_os_without_it_says_so(tmp_path: Path) -> None:
    from tests.conftest import FakePlatform
    status = FakePlatform(tmp_path).ram_access().status()
    assert status.state is RamAccessState.UNSUPPORTED


def test_turning_off_takes_the_password_free_helper(tmp_path: Path) -> None:
    """Off only takes access away: its own program, its own polkit action.
    On still goes through the password-every-time one.

    MUTATION CHECK: drop the HELPER_OFF branch in ``_argv``."""
    run = _Run(0)
    access = _access(tmp_path, ours=True, run=run)
    access.disable()
    access.enable()
    assert run.argv == [["pkexec", _ram_access.HELPER_OFF],
                        ["pkexec", HELPER, "enable"]]


# ── NixOS: the grant is declared, never written ───────────────────────

def _nixos(tmp_path: Path, *, nodes=(3,), header: str | None = None,
           run: _Run | None = None) -> NixosRamAccess:
    """NixOS's /etc rule (declared when *header* is NIXOS_HEADER), fake /dev."""
    dev = tmp_path / "dev"
    dev.mkdir(exist_ok=True)
    for n in nodes:
        (dev / f"i2c-{n}").write_bytes(b"")
    rule = tmp_path / "70-trcc-ram-lighting.rules"
    if header is not None:
        rule.write_text(header + "\n", encoding="utf-8")
    return NixosRamAccess(lambda helper: ["pkexec", helper], find=lambda: (3,),
                          dev=dev, rule=rule, run=run or _Run(),
                          is_root=lambda: False,
                          which=lambda name: f"/usr/bin/{name}")


_NIXOS_HOW_ON = ("set programs.trcc-linux.ramLighting = true; in your NixOS "
                 "configuration, run sudo nixos-rebuild switch, then restart")


def test_on_nixos_off_says_the_line_and_offers_no_button(tmp_path: Path) -> None:
    """NixOS builds /etc from its configuration: no helper can write the rule,
    so "off" is UNSUPPORTED -- every window shows that as its message alone."""
    status = _nixos(tmp_path, nodes=()).status()
    assert status == RamAccessStatus(
        RamAccessState.UNSUPPORTED,
        f"RAM lighting is off -- to enable it, {_NIXOS_HOW_ON}",
        "programs.trcc-linux.ramLighting = true;")


@pytest.mark.parametrize("nodes, header, state", [
    ((3,), NIXOS_HEADER, RamAccessState.ON),
    ((), NIXOS_HEADER, RamAccessState.NOT_APPLIED),
    ((3,), None, RamAccessState.ELSEWHERE),
    ((3,), HEADER, RamAccessState.ELSEWHERE),
], ids=["declared", "declared-not-applied", "someone-elses", "not-the-nixos-rule"])
def test_on_nixos_the_declared_rule_is_trccs(tmp_path: Path, nodes, header,
                                             state) -> None:
    """MUTATION CHECK: drop ``NixosRamAccess._HEADER``."""
    assert _nixos(tmp_path, nodes=nodes, header=header).status().state is state


@pytest.mark.parametrize("action, line", [
    ("enable", "programs.trcc-linux.ramLighting = true;"),
    ("disable", "programs.trcc-linux.ramLighting = false;")])
def test_on_nixos_switching_runs_nothing_and_names_the_line(
        tmp_path: Path, action: str, line: str) -> None:
    """MUTATION CHECK: drop ``NixosRamAccess._change``."""
    run = _Run()
    access = _nixos(tmp_path, header=NIXOS_HEADER, run=run)
    status = getattr(access, action)()
    assert run.argv == []
    assert status == RamAccessStatus(
        RamAccessState.ON,
        f"RAM lighting is part of your NixOS configuration -- to {action} it, "
        f"set {line} in your NixOS configuration, run sudo nixos-rebuild "
        "switch, then restart", line)

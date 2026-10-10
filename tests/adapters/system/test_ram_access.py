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
from trcc.adapters.system._ram_access import HEADER, HELPER, LinuxRamAccess
from trcc.core.models import RamAccessState


class _Run:
    """Records each helper run and answers with *code*."""

    def __init__(self, code: int = 0) -> None:
        self.code, self.argv = code, []

    def __call__(self, argv, **_kw):  # type: ignore[no-untyped-def]
        self.argv.append(list(argv))
        return subprocess.CompletedProcess(argv, self.code, "", "")


def _access(tmp_path: Path, *, buses=(3,), nodes=(3,), ours: bool | None = None,
            pkexec: bool = True, root: bool = False, run: _Run | None = None
            ) -> LinuxRamAccess:
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
        run=run or _Run(), is_root=lambda: root)


@pytest.mark.parametrize("kwargs, state", [
    (dict(buses=()), RamAccessState.NO_BUS),
    (dict(ours=True), RamAccessState.ON),
    (dict(ours=None), RamAccessState.ELSEWHERE),
    (dict(nodes=(), ours=True), RamAccessState.NOT_APPLIED),
    (dict(nodes=(), ours=None), RamAccessState.OFF),
    (dict(nodes=(), ours=False), RamAccessState.OFF),
], ids=["no-bus", "ours-and-access", "someone-elses", "installed-not-applied",
        "off", "an-admins-file-is-not-ours"])
def test_where_the_user_stands(tmp_path: Path, kwargs, state) -> None:  # type: ignore[no-untyped-def]
    assert _access(tmp_path, **kwargs).status().state is state


def test_root_reads_the_rule_not_its_own_access(tmp_path: Path) -> None:
    """Root opens every node, which says nothing about the user it acts for."""
    assert _access(tmp_path, root=True, ours=None).status().state is RamAccessState.OFF
    assert _access(tmp_path, root=True, ours=True).status().state is RamAccessState.ON


def test_without_a_helper_the_status_names_the_command(tmp_path: Path) -> None:
    assert _access(tmp_path, nodes=()).status().command == ""
    off = _access(tmp_path, nodes=(), pkexec=False).status()
    assert off.command == "sudo trcc system ram-lighting enable"


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
    status = _access(tmp_path, nodes=(), pkexec=False, run=run).enable()
    assert run.argv == []
    assert status.message == "Run in a terminal: sudo trcc system ram-lighting enable"


def test_an_os_without_it_says_so(tmp_path: Path) -> None:
    from tests.conftest import FakePlatform
    status = FakePlatform(tmp_path).ram_access().status()
    assert status.state is RamAccessState.UNSUPPORTED

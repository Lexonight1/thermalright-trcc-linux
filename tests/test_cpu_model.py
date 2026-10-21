"""The sensor list names the CPU by its model, on every OS.

It named it after whichever backend answered: with PawnIO reading the
temperature on Windows every CPU row read "PawnIO (CPU) ...", Usage and Clock
included, which come from elsewhere.  Each OS's builder now reads the model
its own way -- /proc/cpuinfo, the registry, sysctl -- with no admin.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from trcc.adapters.sensors import _sysctl
from trcc.adapters.sensors import windows as win
from trcc.adapters.sensors.aggregator import BaselineSensors, linux_cpu_model
from trcc.adapters.sensors.psutil_sources import PsutilCpu, PsutilMemory

_X86 = """processor\t: 0
vendor_id\t: GenuineIntel
model name\t: 12th Gen Intel(R) Core(TM) i9-12900K
cpu MHz\t\t: 3200.000

processor\t: 1
model name\t: 12th Gen Intel(R) Core(TM) i9-12900K
"""
_PI = """processor\t: 0
BogoMIPS\t: 108.00

Hardware\t: BCM2835
Revision\t: c03114
Model\t\t: Raspberry Pi 4 Model B Rev 1.4
"""


@pytest.mark.parametrize(("text", "model"), [
    (_X86, "12th Gen Intel(R) Core(TM) i9-12900K"),
    (_PI, "Raspberry Pi 4 Model B Rev 1.4"),        # no "model name": the board's
    ("processor\t: 0\n", ""),
])
def test_linux_reads_the_model_from_cpuinfo(tmp_path: Path, text: str, model: str) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text(text)
    assert linux_cpu_model(cpuinfo) == model


def test_an_unreadable_cpuinfo_is_no_model(tmp_path: Path) -> None:
    assert linux_cpu_model(tmp_path / "missing") == ""


def test_sysctl_value_collapses_whitespace_and_fails_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="Intel(R) Core(TM)   i7-8700 CPU\n")

    monkeypatch.setattr(_sysctl.subprocess, "run", run)
    assert _sysctl.sysctl_value("machdep.cpu.brand_string") == "Intel(R) Core(TM) i7-8700 CPU"
    assert calls == [["sysctl", "-n", "machdep.cpu.brand_string"]]

    def missing(cmd: list[str], **_: Any) -> Any:
        raise FileNotFoundError("sysctl")

    monkeypatch.setattr(_sysctl.subprocess, "run", missing)
    assert _sysctl.sysctl_value("hw.model") == ""
    monkeypatch.setattr(_sysctl.subprocess, "run",
                        lambda cmd, **_: SimpleNamespace(returncode=1, stdout="junk"))
    assert _sysctl.sysctl_value("hw.model") == ""


class _Key:
    def __enter__(self) -> _Key:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_windows_reads_the_registry_name_task_manager_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[tuple[Any, str]] = []

    def open_key(root: Any, path: str) -> _Key:
        opened.append((root, path))
        return _Key()

    fake = SimpleNamespace(
        HKEY_LOCAL_MACHINE="HKLM", OpenKey=open_key,
        QueryValueEx=lambda key, name: ("AMD Ryzen 9 9950X 16-Core Processor          ", 1))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", fake)
    assert win.windows_cpu_model() == "AMD Ryzen 9 9950X 16-Core Processor"
    assert opened == [("HKLM", r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")]


def test_off_windows_there_is_no_registry() -> None:
    assert win.windows_cpu_model() == ""


class _PawnioLikeCpu(PsutilCpu):
    """A backend that names itself, as PawnIO and HWiNFO do."""

    @property
    def name(self) -> str:
        return "PawnIO (CPU)"


@pytest.mark.parametrize(("cpu_name", "device"), [
    ("AMD Ryzen 9 9950X 16-Core Processor", "AMD Ryzen 9 9950X 16-Core Processor"),
    ("", "PawnIO (CPU)"),                           # no model read: as before
])
def test_cpu_rows_carry_the_model_not_the_backend(cpu_name: str, device: str) -> None:
    """MUTATION CHECK: label the rows with ``self._cpu.name`` -- "PawnIO (CPU)"."""
    sensors = BaselineSensors(cpu=_PawnioLikeCpu(), memory=PsutilMemory(), gpus=[],
                              cpu_name=cpu_name)
    labels = {r.sensor_id: r.label for r in sensors.discover()}
    assert labels["cpu:usage"].startswith(device + " ")
    assert labels["cpu:temp"].startswith(device + " ")

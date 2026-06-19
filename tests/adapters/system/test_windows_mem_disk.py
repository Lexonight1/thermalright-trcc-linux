"""Windows memory/disk enrichment — SMBIOS type, form factor, SMART health.

The cutover hardcoded ``type`` / ``form_factor`` / disk ``health`` to
``"Unknown"``; this restores legacy's WMI mapping.  Exercised from the
Linux dev box by injecting a fake ``wmi`` module — no Windows host.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from trcc.adapters.system.windows import (
    _disk_health,
    _disk_type,
    _windows_disk_info,
    _windows_memory_info,
)
from trcc.core.models import memory_form_factor, memory_type

# ── core/models maps ──────────────────────────────────────────────────


def test_memory_type_maps_every_smbios_code() -> None:
    assert memory_type(26) == "DDR4"
    assert memory_type(34) == "DDR5"
    assert memory_type(35) == "LPDDR5"
    assert memory_type(20) == "DDR"


def test_memory_type_unknown_for_unmapped_or_none() -> None:
    assert memory_type(0) == "Unknown"
    assert memory_type(999) == "Unknown"
    assert memory_type(None) == "Unknown"


def test_memory_form_factor_maps_and_falls_back() -> None:
    assert memory_form_factor(8) == "DIMM"
    assert memory_form_factor(12) == "SODIMM"
    assert memory_form_factor(13) == "RIMM"
    assert memory_form_factor(None) == "Unknown"
    assert memory_form_factor(99) == "Unknown"


# ── disk type heuristic ───────────────────────────────────────────────


def test_disk_type_ssd_from_model() -> None:
    assert _disk_type(SimpleNamespace(Model="Samsung SSD 980", MediaType="")) == "SSD"
    assert _disk_type(SimpleNamespace(Model="WD NVMe", MediaType="")) == "SSD"


def test_disk_type_hdd_and_unknown() -> None:
    assert _disk_type(SimpleNamespace(Model="ST2000 HDD", MediaType="Fixed hard disk media")) == "HDD"
    assert _disk_type(SimpleNamespace(Model="Generic Drive", MediaType="")) == "Unknown"
    assert _disk_type(SimpleNamespace(Model=None, MediaType=None)) == "Unknown"


# ── full WMI flow via an injected fake ``wmi`` module ──────────────────


class _FakeWmiHandle:
    def __init__(self, mems: list, disks: list, statuses: list) -> None:
        self._mems, self._disks, self._statuses = mems, disks, statuses

    def Win32_PhysicalMemory(self) -> list:
        return self._mems

    def Win32_DiskDrive(self) -> list:
        return self._disks

    def MSStorageDriver_FailurePredictStatus(self) -> list:
        return self._statuses


def _install_fake_wmi(monkeypatch: pytest.MonkeyPatch, handle: _FakeWmiHandle) -> None:
    fake = SimpleNamespace(WMI=lambda **kw: handle)
    monkeypatch.setitem(sys.modules, "wmi", fake)


def test_windows_memory_info_maps_type_and_form_factor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mem = SimpleNamespace(
        Manufacturer="Corsair", PartNumber="CMK32", ConfiguredClockSpeed=6000,
        Speed=6000, Capacity=str(16 * 1024 ** 3), DeviceLocator="DIMM A1",
        Rank=2, DataWidth=64, TotalWidth=64,
        SMBIOSMemoryType=34, FormFactor=8,
    )
    _install_fake_wmi(monkeypatch, _FakeWmiHandle([mem], [], []))

    slots = _windows_memory_info()

    assert len(slots) == 1
    assert slots[0]["type"] == "DDR5"
    assert slots[0]["form_factor"] == "DIMM"
    assert slots[0]["size"] == "16 GB"


def test_windows_disk_info_includes_type_and_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disk = SimpleNamespace(
        DeviceID="\\\\.\\PHYSICALDRIVE0", Model="Samsung SSD 990",
        Size=str(2 * 1024 ** 4), MediaType="Fixed hard disk media",
    )
    status = SimpleNamespace(Active=True, PredictFailure=False)
    _install_fake_wmi(monkeypatch, _FakeWmiHandle([], [disk], [status]))

    disks = _windows_disk_info()

    assert len(disks) == 1
    assert disks[0]["type"] == "SSD"
    assert disks[0]["health"] == "PASSED"


def test_disk_health_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    status = SimpleNamespace(Active=True, PredictFailure=True)
    _install_fake_wmi(monkeypatch, _FakeWmiHandle([], [], [status]))

    assert _disk_health("\\\\.\\PHYSICALDRIVE0") == "FAILED"


def test_disk_health_unknown_without_device_id() -> None:
    assert _disk_health(None) == "Unknown"
    assert _disk_health("") == "Unknown"

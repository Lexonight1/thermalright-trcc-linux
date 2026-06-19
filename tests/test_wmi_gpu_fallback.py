"""WMI Win32_VideoController GPU fallback.

A machine with an AMD/Intel GPU and no HWiNFO/LHM/pynvml backend showed
"No GPU detected"; the WMI fallback names the adapter so the picker is
populated.  Restores legacy ``windows/enumerator.py::_wmi_video_controller_gpus``.
"""
from __future__ import annotations

from types import SimpleNamespace

from trcc.adapters.sensors import windows as win
from trcc.adapters.sensors._wmi_gpu import (
    WmiVideoControllerGpu,
    discover_wmi_gpus,
)

# ── the source itself ─────────────────────────────────────────────────


def test_video_controller_gpu_identity_and_null_readings() -> None:
    gpu = WmiVideoControllerGpu(0, "NVIDIA GeForce RTX 4090")
    assert gpu.key == "wmi:0"
    assert gpu.name == "NVIDIA GeForce RTX 4090"
    assert gpu.is_discrete is True
    # No telemetry from Win32_VideoController — every reading is None.
    assert gpu.temp() is None
    assert gpu.usage() is None
    assert gpu.clock() is None
    assert gpu.power() is None
    assert gpu.fan() is None
    assert gpu.vram_used() is None
    assert gpu.vram_total() is None


def test_integrated_gpu_not_marked_discrete() -> None:
    assert WmiVideoControllerGpu(0, "Intel UHD Graphics 770").is_discrete is False
    assert WmiVideoControllerGpu(1, "AMD Radeon Graphics").is_discrete is False


# ── discovery ─────────────────────────────────────────────────────────


def _fake_handle(names: list[str | None]):
    controllers = [SimpleNamespace(Name=n) for n in names]
    return lambda: SimpleNamespace(Win32_VideoController=lambda: controllers)


def test_discover_wmi_gpus_names_each_adapter_skipping_blanks() -> None:
    gpus = discover_wmi_gpus(_fake_handle(["AMD Radeon RX 7900 XTX", None, "  "]))
    assert [g.name for g in gpus] == ["AMD Radeon RX 7900 XTX"]
    assert gpus[0].key == "wmi:0"
    assert gpus[0].is_discrete is True


def test_discover_wmi_gpus_empty_when_no_handle() -> None:
    assert discover_wmi_gpus(lambda: None) == []


# ── chain integration — fallback only when live backends are empty ─────


def test_chain_uses_wmi_only_when_live_backends_empty(monkeypatch) -> None:
    monkeypatch.setattr(win, "discover_nvidia_gpus", lambda: [])
    monkeypatch.setattr(win, "discover_lhm_gpus", lambda: [])
    monkeypatch.setattr(win, "discover_hwinfo_gpus", lambda: [])
    sentinel = WmiVideoControllerGpu(0, "Intel Arc A770")
    monkeypatch.setattr(win, "discover_wmi_gpus", lambda: [sentinel])

    chains = win._build_windows_gpu_chains()

    assert chains == [sentinel]


def test_chain_skips_wmi_when_a_live_backend_present(monkeypatch) -> None:
    live = WmiVideoControllerGpu(0, "NVIDIA RTX 4080")  # any GpuSource stands in
    monkeypatch.setattr(win, "discover_nvidia_gpus", lambda: [live])
    monkeypatch.setattr(win, "discover_lhm_gpus", lambda: [])
    monkeypatch.setattr(win, "discover_hwinfo_gpus", lambda: [])

    def _boom() -> list:
        raise AssertionError("WMI fallback must not run when a backend is live")

    monkeypatch.setattr(win, "discover_wmi_gpus", _boom)

    chains = win._build_windows_gpu_chains()

    assert chains == [live]

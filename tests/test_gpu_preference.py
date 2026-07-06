"""The GPU selector, universal path: pick a GPU and the metric re-routes.

The cutover kept the selector UI + settings but dropped the functional hops —
the enumerator never honored the choice, so ``primary_gpu()`` always returned
the first discrete GPU.  These lock the restored behaviour at the layer every
UI (cli/api/gui/qtgui) shares: the ``SensorEnumerator`` port, the
``SetGpuDevice`` command, and the ``App`` boot-seed.
"""
from __future__ import annotations

from conftest import FakeCpu, FakeGpu, FakeMemory, FakePlatform

from trcc.adapters.sensors.aggregator import BaselineSensors
from trcc.app import App
from trcc.core.commands.system import SetGpuDevice


def _two_gpu_enumerator() -> BaselineSensors:
    """Discrete nvidia:0 + integrated intel:1 (the multi-GPU case)."""
    return BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(),
        gpus=[FakeGpu(0, discrete=True, vendor="nvidia"),
              FakeGpu(1, discrete=False, vendor="intel")],
        fans=[],
    )


# ── core port: primary_gpu honors the preference ─────────────────────

def test_primary_gpu_defaults_to_discrete_with_no_preference() -> None:
    enum = _two_gpu_enumerator()
    assert enum._preferred_gpu_key is None
    assert enum.primary_gpu().key == "nvidia:0"  # first discrete


def test_primary_gpu_honors_the_preferred_key() -> None:
    enum = _two_gpu_enumerator()
    enum.set_preferred_gpu("intel:1")            # the integrated GPU
    assert enum.primary_gpu().key == "intel:1"


def test_preference_can_be_cleared_back_to_auto() -> None:
    enum = _two_gpu_enumerator()
    enum.set_preferred_gpu("intel:1")
    enum.set_preferred_gpu("")                    # '' = clear
    assert enum._preferred_gpu_key is None
    assert enum.primary_gpu().key == "nvidia:0"


def test_stale_preference_falls_back_to_discrete() -> None:
    """A saved GPU that's no longer present must not blank the metric."""
    enum = _two_gpu_enumerator()
    enum.set_preferred_gpu("nvidia:9")            # unplugged / wrong
    assert enum.primary_gpu().key == "nvidia:0"


def test_discrete_nvidia_beats_a_misflagged_amd_apu() -> None:
    """#157: an AMD APU with a large UMA framebuffer reports is_discrete=True.

    An NVML-reported NVIDIA is always genuinely discrete, so it must win the
    auto-pick — not lose to ``amd:0`` on the old alphabetical tiebreak (which
    is how an RTX 5090 lost to a Raphael iGPU).
    """
    enum = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(),
        gpus=[FakeGpu(0, discrete=True, vendor="amd"),      # APU, mis-flagged
              FakeGpu(1, discrete=True, vendor="nvidia")],  # the real card
        fans=[],
    )
    assert enum.primary_gpu().key == "nvidia:1"
    assert [g.key for g in enum.gpus()][0] == "nvidia:1"    # order too


# ── universal command: SetGpuDevice wires BOTH hops ──────────────────

def test_set_gpu_device_sets_settings_and_enumerator(fake_platform) -> None:
    app = App(fake_platform)
    result = app.dispatch(SetGpuDevice(gpu_key="nvidia:0"))

    assert result.ok
    assert app.settings.app.active_gpu == "nvidia:0"           # persisted
    assert app.platform.sensors()._preferred_gpu_key == "nvidia:0"  # live


def test_set_gpu_device_empty_clears_both(fake_platform) -> None:
    app = App(fake_platform)
    app.dispatch(SetGpuDevice(gpu_key="nvidia:0"))
    app.dispatch(SetGpuDevice(gpu_key=""))

    assert app.settings.app.active_gpu is None
    assert app.platform.sensors()._preferred_gpu_key is None


# ── composition root: boot seeds the persisted choice (restart) ──────

def test_boot_seeds_persisted_gpu_into_enumerator(fake_platform, tmp_home) -> None:
    # Session 1: pick a GPU — persisted to trcc.json under tmp_home.
    app1 = App(fake_platform)
    app1.dispatch(SetGpuDevice(gpu_key="nvidia:0"))

    # Session 2: a fresh process on the same paths must honor it WITHOUT any
    # GUI — the App composition root seeds the enumerator from settings.
    app2 = App(FakePlatform(tmp_home))
    assert app2.settings.app.active_gpu == "nvidia:0"
    assert app2.platform.sensors()._preferred_gpu_key == "nvidia:0"

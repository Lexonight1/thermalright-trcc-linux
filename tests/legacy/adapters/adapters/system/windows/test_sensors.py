"""Tests for Windows sensor enumerator — strategy-chain architecture.

Shared base behavior (psutil, nvidia, computed I/O, polling, read_all)
is tested in tests/adapters/system/conftest.py.

Tests follow the app flow: ``discover()`` → ``read_all()`` → ``map_defaults()``.
Mock at the I/O boundary only:

- The LHM WMI namespace handle is injected by patching
  ``trcc.adapters.system.windows.sources.lhm._probe_wmi_namespace``
  (and ``_spawn_lhm`` for "no LHM" paths).  This is what
  ``_LHMSubprocess.start()`` checks first; patching it makes the
  ``LHMSource`` light up (or not) without touching its internals.
- ``psutil`` is patched on the enumerator module.

LHM is read via the ``root\\LibreHardwareMonitor`` WMI namespace, which
exposes flat ``Hardware`` and ``Sensor`` collections.  Sub-hardware is
just a Hardware row with a non-empty ``Parent``; sensors belong to
their parent hardware via ``Sensor.Parent == Hardware.Identifier``.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

# Strategy-chain modules.  ``ENUM_MODULE`` is where ``psutil`` is imported;
# ``LHM_MODULE`` is where the LHM probe helpers live so we can patch them.
ENUM_MODULE = 'trcc.legacy.adapters.system.windows.enumerator'
LHM_MODULE = 'trcc.legacy.adapters.system.windows.sources.lhm'


# ── LHM WMI mock helpers ─────────────────────────────────────────────


def _mock_lhm_sensor(name: str, sensor_type: str, value: float | None,
                     parent: str = '') -> MagicMock:
    """Create a mock LHM Sensor row (one entry from `Sensor` WMI class)."""
    s = MagicMock()
    s.Name = name
    s.SensorType = sensor_type
    s.Value = value
    s.Parent = parent
    return s


def _mock_lhm_hardware(name: str, hw_type: str, identifier: str = '',
                      parent: str = '') -> MagicMock:
    """Create a mock LHM Hardware row (one entry from `Hardware` WMI class).

    SubHardware is represented as a Hardware row with non-empty ``parent``.
    """
    hw = MagicMock()
    hw.Name = name
    hw.HardwareType = hw_type
    hw.Identifier = identifier or f'/{hw_type.lower()}/0'
    hw.Parent = parent
    return hw


def _make_lhm_namespace(
    hardware: list[MagicMock],
    sensors_by_parent: dict[str, list[MagicMock]] | None = None,
) -> MagicMock:
    """Mock the WMI handle to ``root\\LibreHardwareMonitor``.

    ``Hardware()`` returns the flat list.  ``Sensor(Parent=X)`` returns the
    sensors belonging to the hardware whose Identifier is X.
    """
    ns = MagicMock()
    ns.Hardware.return_value = hardware
    sensors = sensors_by_parent or {}
    ns.Sensor.side_effect = lambda Parent='': sensors.get(Parent, [])
    return ns


@contextmanager
def _lhm_via_di(lhm_ns: MagicMock | None):
    """Inject LHM behavior through the DI seam — trickle-down, no module patches.

    Builds a fake ``_LHMSubprocess`` with the namespace handle pre-populated
    (or empty), then swaps the ``@register('lhm')`` registry entry with a
    subclass that wires that fake into ``LHMSource(subprocess=fake)``.
    When the enumerator walks ``in_priority_order()``, it picks up the stub.

    ``lhm_ns=None`` → ``start()`` returns ``None``, ``probe()`` returns False.
    ``lhm_ns=<mock>`` → ``start()`` short-circuits to the mock, source lights up.
    """
    from trcc.legacy.adapters.system.windows.sources._base import WindowsSensorSource
    from trcc.legacy.adapters.system.windows.sources.lhm import (
        LHMSource,
        _LHMSubprocess,
    )

    class _FakeSubprocess(_LHMSubprocess):
        """Override ``start()`` to skip spawn — just return whatever's set."""
        def start(self) -> MagicMock | None:
            return self._namespace_handle
        def stop(self) -> None:
            self._namespace_handle = None

    fake = _FakeSubprocess()
    fake._namespace_handle = lhm_ns

    class _StubLHMSource(LHMSource):
        def __init__(self) -> None:
            super().__init__(subprocess=fake)

    original = WindowsSensorSource._registry.get('lhm')
    WindowsSensorSource._registry['lhm'] = _StubLHMSource
    try:
        yield
    finally:
        if original is not None:
            WindowsSensorSource._registry['lhm'] = original
        else:
            WindowsSensorSource._registry.pop('lhm', None)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_win_no_lhm(mock_io_no_nvidia):
    """Windows enumerator — no LHM, no nvidia. Psutil only."""
    with patch(f'{ENUM_MODULE}.psutil') as win_psutil, _lhm_via_di(None):
        win_psutil.sensors_temperatures.return_value = {}
        mock_io_no_nvidia.win_psutil = win_psutil
        mock_io_no_nvidia.lhm_ns = None
        yield mock_io_no_nvidia


@pytest.fixture
def mock_win_lhm(mock_io_no_nvidia):
    """Windows enumerator with mocked LHM GPU + CPU."""
    gpu_id = '/gpu-nvidia/0'
    cpu_id = '/intelcpu/0'
    gpu_sensors = [
        _mock_lhm_sensor('GPU Core', 'Temperature', 72.0, parent=gpu_id),
        _mock_lhm_sensor('GPU Core Load', 'Load', 95.0, parent=gpu_id),
        _mock_lhm_sensor('GPU Core Clock', 'Clock', 1950.0, parent=gpu_id),
        _mock_lhm_sensor('GPU Package Power', 'Power', 310.0, parent=gpu_id),
        _mock_lhm_sensor('GPU Fan', 'Fan', 1800.0, parent=gpu_id),
    ]
    cpu_sensors = [
        _mock_lhm_sensor('CPU Package', 'Temperature', 65.0, parent=cpu_id),
        _mock_lhm_sensor('CPU Package Power', 'Power', 125.0, parent=cpu_id),
    ]
    gpu_hw = _mock_lhm_hardware('NVIDIA RTX 4090', 'GpuNvidia', identifier=gpu_id)
    cpu_hw = _mock_lhm_hardware('Intel Core i9', 'Cpu', identifier=cpu_id)
    ns = _make_lhm_namespace(
        [gpu_hw, cpu_hw],
        {gpu_id: gpu_sensors, cpu_id: cpu_sensors},
    )

    with patch(f'{ENUM_MODULE}.psutil') as win_psutil, _lhm_via_di(ns):
        win_psutil.sensors_temperatures.return_value = {}
        mock_io_no_nvidia.win_psutil = win_psutil
        mock_io_no_nvidia.lhm_ns = ns
        yield mock_io_no_nvidia


@pytest.fixture
def mock_win_nvidia(mock_io):
    """Windows enumerator — no LHM, nvidia fallback."""
    mock_io.setup_nvidia(temp=68, usage=80, clock=1800, power_mw=250000, fan=55)
    with patch(f'{ENUM_MODULE}.psutil') as win_psutil, _lhm_via_di(None):
        win_psutil.sensors_temperatures.return_value = {}
        mock_io.win_psutil = win_psutil
        mock_io.lhm_ns = None
        yield mock_io


def _make_enum():
    """Construct the strategy-chain enumerator (LHM patches applied via fixture)."""
    from trcc.legacy.adapters.system.windows.enumerator import WindowsSensorEnumerator
    return WindowsSensorEnumerator()


@pytest.fixture
def enum_no_lhm(mock_win_no_lhm):
    e = _make_enum()
    e.discover()
    return e


@pytest.fixture
def enum_lhm(mock_win_lhm):
    e = _make_enum()
    e.discover()
    return e


@pytest.fixture
def enum_nvidia(mock_win_nvidia):
    e = _make_enum()
    e.discover()
    return e


# ── Discovery ────────────────────────────────────────────────────────


class TestDiscover:

    def test_psutil_only_when_no_lhm_no_nvidia(self, enum_no_lhm):
        sources = {s.source for s in enum_no_lhm.get_sensors()}
        assert 'psutil' in sources
        assert 'computed' in sources
        assert 'lhm' not in sources
        assert 'nvidia' not in sources

    def test_lhm_gpu_sensors_registered(self, enum_lhm):
        ids = [s.id for s in enum_lhm.get_sensors()]
        assert any('gpu_core' in sid and 'lhm:' in sid for sid in ids)
        assert any('cpu_package' in sid and 'lhm:' in sid for sid in ids)

    def test_lhm_gpu_skips_nvidia_discovery(self, enum_lhm):
        """When LHM contributes a GPU, pynvml fallback is skipped."""
        assert not any(s.source == 'nvidia' for s in enum_lhm.get_sensors())

    def test_nvidia_fallback_when_no_lhm(self, enum_nvidia):
        ids = [s.id for s in enum_nvidia.get_sensors()]
        assert 'nvidia:0:temp' in ids
        assert 'nvidia:0:gpu_busy' in ids

    def test_psutil_cpu_temps_registered(self, mock_io_no_nvidia):
        with patch(f'{ENUM_MODULE}.psutil') as win_psutil, _lhm_via_di(None):
            win_psutil.sensors_temperatures.return_value = {
                'coretemp': [MagicMock(label='Package', current=65.0)],
            }
            e = _make_enum()
            e.discover()
            assert any(s.id == 'psutil:temp:coretemp:0' for s in e.get_sensors())

    def test_wmi_noop_without_package(self, enum_no_lhm):
        """WMI not installed on test system — no wmi sensors."""
        assert not any(s.source == 'wmi' for s in enum_no_lhm.get_sensors())

    def test_lhm_subhardware_discovered(self, mock_io_no_nvidia):
        cpu_id = '/intelcpu/0'
        sub_id = '/intelcpu/0/core/0'
        core_sensor = _mock_lhm_sensor('Core #0', 'Temperature', 60.0, parent=sub_id)
        cpu_hw = _mock_lhm_hardware('Intel CPU', 'Cpu', identifier=cpu_id)
        sub_hw = _mock_lhm_hardware('CPU Core', 'Cpu', identifier=sub_id, parent=cpu_id)
        ns = _make_lhm_namespace([cpu_hw, sub_hw], {sub_id: [core_sensor]})

        with patch(f'{ENUM_MODULE}.psutil') as wp, _lhm_via_di(ns):
            wp.sensors_temperatures.return_value = {}
            e = _make_enum()
            e.discover()
            assert any('cpu_core' in s.id for s in e.get_sensors())


# ── Reading ──────────────────────────────────────────────────────────


class TestReadAll:

    def test_lhm_readings(self, enum_lhm):
        readings = enum_lhm.read_all()
        lhm_keys = [k for k in readings if k.startswith('lhm:')]
        assert len(lhm_keys) > 0
        gpu_temp_key = [k for k in lhm_keys if 'gpu_core' in k and 'load' not in k]
        assert gpu_temp_key
        assert readings[gpu_temp_key[0]] == 72.0

    def test_nvidia_fallback_readings(self, enum_nvidia):
        readings = enum_nvidia.read_all()
        assert readings['nvidia:0:temp'] == 68.0
        assert readings['nvidia:0:gpu_busy'] == 80.0

    def test_psutil_base_readings(self, enum_no_lhm):
        readings = enum_no_lhm.read_all()
        assert 'psutil:cpu_percent' in readings
        assert 'psutil:mem_percent' in readings

    def test_lhm_none_values_skipped(self, mock_io_no_nvidia):
        board_id = '/mainboard/0'
        dead_sensor = _mock_lhm_sensor('Dead', 'Temperature', None, parent=board_id)
        hw = _mock_lhm_hardware('Board', 'Motherboard', identifier=board_id)
        ns = _make_lhm_namespace([hw], {board_id: [dead_sensor]})

        with patch(f'{ENUM_MODULE}.psutil') as wp, _lhm_via_di(ns):
            wp.sensors_temperatures.return_value = {}
            e = _make_enum()
            e.discover()
            readings = e.read_all()
            assert not any(k.startswith('lhm:board:dead') for k in readings)

    def test_lhm_poll_exception_isolated(self, mock_io_no_nvidia):
        """LHM WMI failure doesn't crash the enumerator.

        WMI raises arbitrary COM errors during poll if the LHM provider
        de-registers (process killed, machine sleep wake).  Verify the
        enumerator catches and logs without aborting the whole poll.
        """
        cpu_id = '/intelcpu/0'
        cpu_hw = _mock_lhm_hardware('Broken', 'Cpu', identifier=cpu_id)
        ns = _make_lhm_namespace([cpu_hw], {})
        # Make Hardware() crash on the second call (poll), not on discovery.
        ns.Hardware.side_effect = [[cpu_hw], RuntimeError("COM disconnected")]

        with patch(f'{ENUM_MODULE}.psutil') as wp, _lhm_via_di(ns):
            wp.sensors_temperatures.return_value = {}
            e = _make_enum()
            e.discover()
            readings = e.read_all()
            assert 'psutil:cpu_percent' in readings


# ── Mapping ──────────────────────────────────────────────────────────


class TestMapDefaults:

    def test_lhm_gpu_mapping(self, enum_lhm):
        mapping = enum_lhm.map_defaults()
        assert 'gpu_temp' in mapping
        assert mapping['gpu_temp'].startswith('lhm:')

    def test_nvidia_fallback_gpu_mapping(self, enum_nvidia):
        mapping = enum_nvidia.map_defaults()
        assert mapping['gpu_temp'] == 'nvidia:0:temp'
        assert mapping['gpu_usage'] == 'nvidia:0:gpu_busy'

    def test_no_gpu_mapping_without_any(self, enum_no_lhm):
        mapping = enum_no_lhm.map_defaults()
        assert 'gpu_temp' not in mapping

    def test_common_mappings(self, enum_no_lhm):
        mapping = enum_no_lhm.map_defaults()
        assert mapping['cpu_percent'] == 'psutil:cpu_percent'
        assert mapping['mem_available'] == 'psutil:mem_available'
        assert mapping['disk_read'] == 'computed:disk_read'
        assert mapping['net_total_up'] == 'computed:net_total_up'

    def test_lhm_cpu_temp_mapping(self, enum_lhm):
        mapping = enum_lhm.map_defaults()
        assert 'cpu_temp' in mapping
        assert mapping['cpu_temp'].startswith('lhm:')

    def test_lhm_cpu_power_mapping(self, enum_lhm):
        mapping = enum_lhm.map_defaults()
        assert 'cpu_power' in mapping
        assert 'package' in mapping['cpu_power']


# ── Polling lifecycle ────────────────────────────────────────────────


class TestPolling:

    def test_lhm_stopped_on_stop(self, mock_win_lhm):
        """``stop_polling`` must terminate the LHM subprocess we spawned.

        With the strategy chain, ``stop`` walks every live source and
        invokes ``source.stop()``; ``LHMSource.stop()`` calls into
        ``_LHMSubprocess.stop()`` which clears the namespace handle.
        """
        e = _make_enum()
        e.discover()
        e.start_polling(interval=0.01)
        # Capture the LHMSource that contributed (set during discover).
        from trcc.legacy.adapters.system.windows.sources.lhm import LHMSource
        lhm_sources = [s for s in e._live_sources if isinstance(s, LHMSource)]
        assert lhm_sources, "LHM source did not register"
        e.stop_polling()
        assert lhm_sources[0]._lhm.namespace is None


# ── LHM type map ─────────────────────────────────────────────────────


class TestLhmTypeMap:

    def test_known_types_mapped(self):
        from trcc.legacy.adapters.system.windows.sources.lhm import _LHM_TYPE_MAP
        assert 'Temperature' in _LHM_TYPE_MAP
        assert 'Fan' in _LHM_TYPE_MAP
        assert 'Clock' in _LHM_TYPE_MAP
        assert 'Load' in _LHM_TYPE_MAP
        assert 'Power' in _LHM_TYPE_MAP

    def test_unknown_type_not_mapped(self):
        from trcc.legacy.adapters.system.windows.sources.lhm import _LHM_TYPE_MAP
        assert 'Warp' not in _LHM_TYPE_MAP


# ── WMI GPU fallback (issue #131) ────────────────────────────────────
#
# AMD-on-Windows users without LibreHardwareMonitor used to see "No GPUs
# detected" because ``pynvml`` is NVIDIA-only.  v9.6.0 added a
# ``Win32_VideoController`` fallback that detects every GPU Windows knows
# about — sensor data still requires LHM/HWiNFO, but the card appears
# in ``trcc gpus`` for selection.


def _mock_wmi_video(name: str = 'AMD Radeon RX 9070 XT') -> MagicMock:
    """Build a mock Win32_VideoController instance with the given name."""
    vc = MagicMock()
    vc.Name = name
    return vc


class TestWmiGpuFallback:
    """Verify the ``_wmi_video_controller_gpus()`` helper used when neither
    LHM nor pynvml returned any GPUs."""

    def _patch_wmi(self, controllers: list[MagicMock]):
        """Install a mock ``wmi`` module returning the given controllers."""
        mock_wmi_mod = MagicMock()
        mock_wmi_instance = MagicMock()
        mock_wmi_instance.Win32_VideoController.return_value = controllers
        mock_wmi_mod.WMI.return_value = mock_wmi_instance
        return patch.dict('sys.modules', {'wmi': mock_wmi_mod})

    def test_returns_amd_gpu(self):
        """The reporter's exact card (RX 9070 XT) appears in the list."""
        from trcc.legacy.adapters.system.windows.enumerator import (
            _wmi_video_controller_gpus,
        )
        with self._patch_wmi([_mock_wmi_video('AMD Radeon RX 9070 XT')]):
            result = _wmi_video_controller_gpus()
        assert result == [('wmi:0', 'AMD Radeon RX 9070 XT')]

    def test_returns_multiple_gpus_in_order(self):
        """Integrated + discrete are both listed, indexed in detection order."""
        from trcc.legacy.adapters.system.windows.enumerator import (
            _wmi_video_controller_gpus,
        )
        controllers = [
            _mock_wmi_video('AMD Radeon Graphics'),     # integrated
            _mock_wmi_video('AMD Radeon RX 9070 XT'),   # discrete
        ]
        with self._patch_wmi(controllers):
            result = _wmi_video_controller_gpus()
        assert result == [
            ('wmi:0', 'AMD Radeon Graphics'),
            ('wmi:1', 'AMD Radeon RX 9070 XT'),
        ]

    def test_strips_whitespace_in_name(self):
        from trcc.legacy.adapters.system.windows.enumerator import (
            _wmi_video_controller_gpus,
        )
        with self._patch_wmi([_mock_wmi_video('  AMD Radeon  ')]):
            result = _wmi_video_controller_gpus()
        assert result == [('wmi:0', 'AMD Radeon')]

    def test_skips_controllers_with_no_name(self):
        """WMI sometimes returns ghost controllers (Name=None).  Skip them."""
        from trcc.legacy.adapters.system.windows.enumerator import (
            _wmi_video_controller_gpus,
        )
        ghost = MagicMock()
        ghost.Name = None
        controllers = [ghost, _mock_wmi_video('Real GPU')]
        with self._patch_wmi(controllers):
            result = _wmi_video_controller_gpus()
        assert result == [('wmi:1', 'Real GPU')]

    def test_returns_empty_when_wmi_pkg_missing(self):
        """No ``wmi`` package on path (e.g. running on Linux): empty list, no crash."""
        from trcc.legacy.adapters.system.windows.enumerator import (
            _wmi_video_controller_gpus,
        )
        with patch.dict('sys.modules', {'wmi': None}):
            # ``import wmi`` with sys.modules[wmi]=None raises ImportError
            result = _wmi_video_controller_gpus()
        assert result == []

    def test_returns_empty_on_wmi_exception(self):
        """WMI subsystem can raise (e.g. COM init failure).  Don't propagate."""
        from trcc.legacy.adapters.system.windows.enumerator import (
            _wmi_video_controller_gpus,
        )
        mock_wmi_mod = MagicMock()
        mock_wmi_mod.WMI.side_effect = RuntimeError('COM not initialised')
        with patch.dict('sys.modules', {'wmi': mock_wmi_mod}):
            result = _wmi_video_controller_gpus()
        assert result == []


class TestGetGpuListFallbackOrder:
    """``get_gpu_list()`` preference: source.gpu_list() → pynvml → WMI."""

    def test_wmi_only_fires_when_lhm_and_pynvml_empty(self, mock_io_no_nvidia):
        """Reporter scenario: AMD GPU, no LHM, no NVIDIA — WMI fallback fires."""
        with patch(f'{ENUM_MODULE}.psutil') as wp, _lhm_via_di(None):
            wp.sensors_temperatures.return_value = {}
            e = _make_enum()
            e.discover()  # discovers no live sources
            # Patch the base get_gpu_list (pynvml NVIDIA path) and the WMI helper.
            from trcc.legacy.adapters.system._base import SensorEnumeratorBase
            with patch.object(
                SensorEnumeratorBase, 'get_gpu_list', return_value=[],
            ), patch(
                f'{ENUM_MODULE}._wmi_video_controller_gpus',
                return_value=[('wmi:0', 'AMD Radeon RX 9070 XT')],
            ):
                result = e.get_gpu_list()
            assert result == [('wmi:0', 'AMD Radeon RX 9070 XT')]

    def test_wmi_skipped_when_lhm_returns_gpus(self, mock_io_no_nvidia):
        """LHM is preferred when running — its results include sensor data."""
        gpu_id = '/gpu-amd/0'
        lhm_gpu = _mock_lhm_hardware(
            'AMD Radeon RX 9070 XT', 'GpuAmd', identifier=gpu_id,
        )
        ns = _make_lhm_namespace([lhm_gpu])

        with patch(f'{ENUM_MODULE}.psutil') as wp, _lhm_via_di(ns):
            wp.sensors_temperatures.return_value = {}
            e = _make_enum()
            e.discover()
            with patch(
                f'{ENUM_MODULE}._wmi_video_controller_gpus',
                return_value=[('wmi:0', 'should not see this')],
            ) as wmi_mock:
                result = e.get_gpu_list()
            wmi_mock.assert_not_called()
            assert any('amd_radeon' in key for key, _ in result)

    def test_wmi_skipped_when_pynvml_returns_gpus(self, mock_io_no_nvidia):
        """pynvml NVIDIA path takes precedence over WMI when present."""
        with patch(f'{ENUM_MODULE}.psutil') as wp, _lhm_via_di(None):
            wp.sensors_temperatures.return_value = {}
            e = _make_enum()
            e.discover()
            from trcc.legacy.adapters.system._base import SensorEnumeratorBase
            with patch.object(
                SensorEnumeratorBase, 'get_gpu_list',
                return_value=[('nvidia:0', 'RTX 4090 (24576 MB)')],
            ), patch(
                f'{ENUM_MODULE}._wmi_video_controller_gpus',
                return_value=[('wmi:0', 'should not see this')],
            ) as wmi_mock:
                result = e.get_gpu_list()
            wmi_mock.assert_not_called()
            assert result == [('nvidia:0', 'RTX 4090 (24576 MB)')]

# tests/cli/test_set_gpu.py
from unittest.mock import patch, MagicMock


def test_set_gpu_single_gpu_exits_early(monkeypatch):
    """Single GPU: informs user and exits without slot assignment."""
    fake_gpus = [{'pci_slot': '0000:0f:00.0', 'vendor': '1002', 'name': 'RX 7900 XTX',
                  'driver_key': 'amdgpu', 'drm_card': 'card1'}]
    monkeypatch.setattr("trcc.core.platform.LINUX", True)
    with patch("trcc.adapters.system.linux.sensors.detect_gpus", return_value=fake_gpus):
        from trcc.cli._system import set_gpu
        result = set_gpu()
    assert result == 0


def test_set_gpu_multi_assigns_slots(monkeypatch):
    """Multi-GPU: user assigns GPUs to top and bottom slots."""
    fake_gpus = [
        {'pci_slot': '0000:06:00.0', 'vendor': '1002', 'name': 'RX 5700 XT',
         'driver_key': 'amdgpu', 'drm_card': 'card0'},
        {'pci_slot': '0000:0f:00.0', 'vendor': '1002', 'name': 'RX 7900 XTX',
         'driver_key': 'amdgpu.1', 'drm_card': 'card1'},
    ]
    inputs = iter(["2", "", "1", ""])
    monkeypatch.setattr("trcc.core.platform.LINUX", True)
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    with patch("trcc.adapters.system.linux.sensors.detect_gpus", return_value=fake_gpus), \
         patch("trcc.conf.Settings") as mock_settings:
        mock_settings._get_saved_gpu_slots.return_value = {}
        mock_settings._get_saved_gpu_indicator_color.return_value = '#0000FF'
        from trcc.cli._system import set_gpu
        result = set_gpu()
    assert result == 0
    mock_settings.set_gpu_slots.assert_called_once_with(
        {"top": "0000:0f:00.0", "bottom": "0000:06:00.0"}, 5)


def test_set_gpu_no_gpus(monkeypatch):
    """No GPUs detected returns 1."""
    monkeypatch.setattr("trcc.core.platform.LINUX", True)
    with patch("trcc.adapters.system.linux.sensors.detect_gpus", return_value=[]):
        from trcc.cli._system import set_gpu
        result = set_gpu()
    assert result == 1


def test_set_gpu_no_slots_assigned(monkeypatch):
    """Multi-GPU but user skips all slots returns 1."""
    fake_gpus = [
        {'pci_slot': '0000:06:00.0', 'vendor': '1002', 'name': 'RX 5700 XT',
         'driver_key': 'amdgpu', 'drm_card': 'card0'},
        {'pci_slot': '0000:0f:00.0', 'vendor': '1002', 'name': 'RX 7900 XTX',
         'driver_key': 'amdgpu.1', 'drm_card': 'card1'},
    ]
    inputs = iter(["", "", "", ""])
    monkeypatch.setattr("trcc.core.platform.LINUX", True)
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    with patch("trcc.adapters.system.linux.sensors.detect_gpus", return_value=fake_gpus), \
         patch("trcc.conf.Settings") as mock_settings:
        mock_settings._get_saved_gpu_slots.return_value = {}
        from trcc.cli._system import set_gpu
        result = set_gpu()
    assert result == 1

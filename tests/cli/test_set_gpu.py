# tests/cli/test_set_gpu.py
from unittest.mock import patch

from trcc.cli._system import set_gpu


def test_set_gpu_single_gpu_no_slots(monkeypatch):
    """Single GPU: sets gpu pci slot, no slot assignment."""
    fake_gpus = [{'pci_slot': '0000:0f:00.0', 'vendor': '1002', 'name': 'RX 7900 XTX',
                  'driver_key': 'amdgpu', 'drm_card': 'card1'}]
    monkeypatch.setattr("trcc.cli._system.LINUX", True)
    with patch("trcc.cli._system.detect_gpus", return_value=fake_gpus), \
         patch("trcc.cli._system.Settings") as mock_settings, \
         patch("trcc.cli._system.SensorEnumerator"):
        result = set_gpu()
    assert result == 0
    mock_settings.set_gpu.assert_called_once_with('0000:0f:00.0')
    mock_settings.set_gpu_slots.assert_not_called()


def test_set_gpu_multi_assigns_slots(monkeypatch):
    """Multi-GPU: user assigns GPUs to top and bottom slots."""
    fake_gpus = [
        {'pci_slot': '0000:06:00.0', 'vendor': '1002', 'name': 'RX 5700 XT',
         'driver_key': 'amdgpu', 'drm_card': 'card0'},
        {'pci_slot': '0000:0f:00.0', 'vendor': '1002', 'name': 'RX 7900 XTX',
         'driver_key': 'amdgpu.1', 'drm_card': 'card1'},
    ]
    # User picks: GPU 2 for top, skip middle, GPU 1 for bottom, default frequency
    inputs = iter(["2", "", "1", ""])
    monkeypatch.setattr("trcc.cli._system.LINUX", True)
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    with patch("trcc.cli._system.detect_gpus", return_value=fake_gpus), \
         patch("trcc.cli._system.Settings") as mock_settings, \
         patch("trcc.cli._system.SensorEnumerator"):
        mock_settings._get_saved_gpu_slots.return_value = {}
        result = set_gpu()
    assert result == 0
    mock_settings.set_gpu_slots.assert_called_once_with(
        {"top": "0000:0f:00.0", "bottom": "0000:06:00.0"}, 5)


def test_set_gpu_no_gpus(monkeypatch):
    """No GPUs detected returns 1."""
    monkeypatch.setattr("trcc.cli._system.LINUX", True)
    with patch("trcc.cli._system.detect_gpus", return_value=[]):
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
    inputs = iter(["", "", "", ""])  # skip all slots
    monkeypatch.setattr("trcc.cli._system.LINUX", True)
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    with patch("trcc.cli._system.detect_gpus", return_value=fake_gpus), \
         patch("trcc.cli._system.Settings") as mock_settings, \
         patch("trcc.cli._system.SensorEnumerator"):
        mock_settings._get_saved_gpu_slots.return_value = {}
        result = set_gpu()
    assert result == 1

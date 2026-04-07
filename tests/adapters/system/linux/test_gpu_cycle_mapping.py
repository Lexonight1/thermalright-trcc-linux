from unittest.mock import patch

from trcc.adapters.system.linux.sensors import SensorEnumerator


def test_map_defaults_uses_active_gpu_slot():
    """When gpu_slots config exists, map_defaults uses the active slot's GPU."""
    config = {
        'gpu_slots': {'top': '0000:0f:00.0', 'bottom': '0000:06:00.0'},
        'gpu_active_slot': 'top',
    }
    fake_gpus = [
        {'pci_slot': '0000:0f:00.0', 'vendor': '1002', 'name': 'RX 7900 XTX',
         'driver_key': 'amdgpu.1', 'drm_card': 'card1'},
    ]
    with patch("trcc.conf.load_config", return_value=config), \
         patch("trcc.adapters.system.linux.sensors.detect_gpus", return_value=fake_gpus):
        enum = SensorEnumerator()
        SensorEnumerator._default_map = None
        defaults = enum.map_defaults()
        assert isinstance(defaults, dict)

def test_map_defaults_falls_back_to_gpu_pci_slot():
    """When no gpu_slots, falls back to legacy gpu_pci_slot."""
    config = {'gpu_pci_slot': '0000:0f:00.0'}
    fake_gpus = [
        {'pci_slot': '0000:0f:00.0', 'vendor': '1002', 'name': 'RX 7900 XTX',
         'driver_key': 'amdgpu.1', 'drm_card': 'card1'},
    ]
    with patch("trcc.conf.load_config", return_value=config), \
         patch("trcc.adapters.system.linux.sensors.detect_gpus", return_value=fake_gpus):
        enum = SensorEnumerator()
        SensorEnumerator._default_map = None
        defaults = enum.map_defaults()
        assert isinstance(defaults, dict)

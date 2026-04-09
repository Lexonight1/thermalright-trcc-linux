from unittest.mock import patch

from trcc.adapters.system.linux.sensors import SensorEnumerator


def test_gpu_mapping_for_pci_amd():
    """gpu_mapping_for_pci returns sensor IDs for an AMD GPU by PCI slot."""
    fake_gpus = [
        {'pci_slot': '0000:0f:00.0', 'vendor': '1002', 'name': 'RX 7900 XTX',
         'driver_key': 'amdgpu.1', 'drm_card': 'card2'},
        {'pci_slot': '0000:06:00.0', 'vendor': '1002', 'name': 'RX 5700 XT',
         'driver_key': 'amdgpu', 'drm_card': 'card1'},
    ]
    with patch("trcc.adapters.system.linux.sensors.detect_gpus", return_value=fake_gpus):
        enum = SensorEnumerator()
        mapping = enum.gpu_mapping_for_pci('0000:06:00.0')
        assert 'gpu_temp' in mapping
        assert 'gpu_usage' in mapping
        assert 'gpu_clock' in mapping
        assert 'gpu_power' in mapping


def test_gpu_mapping_for_pci_unknown():
    """gpu_mapping_for_pci returns empty strings for unknown PCI slot."""
    with patch("trcc.adapters.system.linux.sensors.detect_gpus", return_value=[]):
        enum = SensorEnumerator()
        mapping = enum.gpu_mapping_for_pci('0000:99:00.0')
        assert mapping == {'gpu_temp': '', 'gpu_usage': '', 'gpu_clock': '', 'gpu_power': ''}


def test_map_defaults_uses_best_gpu():
    """map_defaults uses _best_gpu() — slot routing is handled by LED swap."""
    with patch.object(SensorEnumerator, '_best_gpu',
                      return_value={'vendor': 'amd', 'hwmon_driver': 'amdgpu',
                                    'drm_card': 'card1', 'nvidia_idx': None}):
        enum = SensorEnumerator()
        SensorEnumerator._default_map = None
        defaults = enum.map_defaults()
        assert isinstance(defaults, dict)

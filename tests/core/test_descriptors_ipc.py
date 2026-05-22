"""IPC dispatch tests for Trcc.lcd_descriptors / led_descriptors (10C.1).

Exercises the new ``_meta.lcd_descriptors`` and ``_meta.led_descriptors``
arms of ``IPCServer._dispatch_meta`` without spinning up a real socket
server.  We feed a stub Trcc with known device descriptors, dispatch the
manifold call directly, and verify the wire payload round-trips through
``DeviceInfo.from_wire_dict`` to a structurally-equal list.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from trcc.legacy.core.models.device import DeviceInfo, UsbAddress
from trcc.legacy.ipc import IPCServer


def _make_descriptor(idx: int, vid: int, pid: int, **overrides) -> DeviceInfo:
    defaults = {
        'name': f'Device {idx}',
        'path': f'mock:lcd:{idx}:{vid:04x}:{pid:04x}',
        'resolution': (320, 320),
        'vid': vid, 'pid': pid,
        'usb_address': UsbAddress(bus=1, address=idx + 5),
        'device_index': idx,
        'fbl_code': 100,
        'protocol': 'scsi',
        'device_type': 1,
        'implementation': 'ali_corp_lcd_v1',
        'button_image': 'A1FROZEN WARFRAME PRO',
        'pm_byte': 32, 'sub_byte': 1,
    }
    defaults.update(overrides)
    return DeviceInfo(**defaults)


class TestDescriptorIPCDispatch(unittest.TestCase):
    """_meta.lcd_descriptors / led_descriptors round-trip a list of DeviceInfos."""

    def _stub_trcc(
        self,
        lcd_infos: list[DeviceInfo] | None = None,
        led_infos: list[DeviceInfo] | None = None,
    ) -> MagicMock:
        trcc = MagicMock()
        trcc.lcd_descriptors.return_value = lcd_infos or []
        trcc.led_descriptors.return_value = led_infos or []
        return trcc

    def _equal(self, a: DeviceInfo, b: DeviceInfo) -> None:
        for field in (
            'name', 'path', 'resolution', 'vid', 'pid', 'usb_address',
            'device_index', 'fbl_code', 'protocol', 'device_type',
            'implementation', 'button_image', 'pm_byte', 'sub_byte',
        ):
            self.assertEqual(getattr(a, field), getattr(b, field),
                             f"field mismatch: {field}")

    def test_lcd_descriptors_dispatch_empty(self):
        server = IPCServer(trcc=self._stub_trcc())
        response = server._dispatch_meta("lcd_descriptors", (), {})
        self.assertTrue(response['success'])
        self.assertEqual(response['descriptors'], [])

    def test_led_descriptors_dispatch_empty(self):
        server = IPCServer(trcc=self._stub_trcc())
        response = server._dispatch_meta("led_descriptors", (), {})
        self.assertTrue(response['success'])
        self.assertEqual(response['descriptors'], [])

    def test_lcd_descriptors_dispatch_round_trip(self):
        """One LCD's descriptor goes out as wire dicts, comes back as DeviceInfo."""
        original = _make_descriptor(0, 0x0402, 0x3922, usb_address=None)
        server = IPCServer(trcc=self._stub_trcc(lcd_infos=[original]))
        response = server._dispatch_meta("lcd_descriptors", (), {})

        self.assertTrue(response['success'])
        self.assertEqual(len(response['descriptors']), 1)
        # Reconstruct via the symmetric wire decoder used by TrccProxy.
        restored = DeviceInfo.from_wire_dict(response['descriptors'][0])
        self._equal(original, restored)

    def test_led_descriptors_dispatch_with_usb_address(self):
        """Verify UsbAddress survives dispatch — covers the non-SCSI shape."""
        original = _make_descriptor(
            0, 0x0416, 0x8001,
            protocol='hid', device_type=2,
            usb_address=UsbAddress(bus=2, address=7),
        )
        server = IPCServer(trcc=self._stub_trcc(led_infos=[original]))
        response = server._dispatch_meta("led_descriptors", (), {})

        self.assertTrue(response['success'])
        restored = DeviceInfo.from_wire_dict(response['descriptors'][0])
        self._equal(original, restored)
        assert restored.usb_address is not None
        self.assertEqual(restored.usb_address.bus, 2)
        self.assertEqual(restored.usb_address.address, 7)

    def test_multiple_lcd_descriptors_preserve_order(self):
        """List order matters — devices are addressed by index throughout the GUI."""
        a = _make_descriptor(0, 0x0402, 0x3922, usb_address=None)
        b = _make_descriptor(
            1, 0x0418, 0x5303,
            resolution=(1280, 480),
            protocol='hid', device_type=3, fbl_code=128,
            pm_byte=6,
        )
        server = IPCServer(trcc=self._stub_trcc(lcd_infos=[a, b]))
        response = server._dispatch_meta("lcd_descriptors", (), {})

        self.assertTrue(response['success'])
        restored_a = DeviceInfo.from_wire_dict(response['descriptors'][0])
        restored_b = DeviceInfo.from_wire_dict(response['descriptors'][1])
        self._equal(a, restored_a)
        self._equal(b, restored_b)

    def test_lcd_descriptors_handles_trcc_exception(self):
        """If Trcc.lcd_descriptors raises, dispatch returns success=False."""
        trcc = MagicMock()
        trcc.lcd_descriptors.side_effect = RuntimeError("boom")
        server = IPCServer(trcc=trcc)
        response = server._dispatch_meta("lcd_descriptors", (), {})
        self.assertFalse(response['success'])
        self.assertIn("RuntimeError", response['error'])

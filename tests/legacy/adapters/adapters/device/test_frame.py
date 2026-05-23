"""Tests for transport-layer ABC hierarchy (UsbDevice, FrameDevice, LedDevice)."""

import pytest

from trcc.legacy.adapters.device.frame import FrameDevice, LedDevice, UsbDevice
from trcc.legacy.core.models import HandshakeResult, HidHandshakeInfo, LedHandshakeInfo

# =========================================================================
# UsbDevice (root ABC)
# =========================================================================


class TestUsbDevice:
    """UsbDevice is the universal contract — handshake + close."""

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            UsbDevice()  # type: ignore[abstract]

    def test_incomplete_subclass_rejected(self):
        class OnlyHandshake(UsbDevice):
            def handshake(self):
                return HandshakeResult()

        with pytest.raises(TypeError):
            OnlyHandshake()  # type: ignore[abstract]

    def test_concrete_subclass_works(self):
        class StubDevice(UsbDevice):
            def handshake(self):
                return HandshakeResult(resolution=(320, 320))

            def close(self):
                pass

        dev = StubDevice()
        result = dev.handshake()
        assert result.resolution == (320, 320)
        dev.close()


# =========================================================================
# FrameDevice (LCD frame senders)
# =========================================================================


class TestFrameDevice:
    """FrameDevice extends UsbDevice with send_frame()."""

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            FrameDevice()  # type: ignore[abstract]

    def test_missing_send_frame_rejected(self):
        class NoSendFrame(FrameDevice):
            def handshake(self):
                return HandshakeResult()

            def close(self):
                pass

        with pytest.raises(TypeError):
            NoSendFrame()  # type: ignore[abstract]

    def test_concrete_subclass_works(self):
        class StubFrame(FrameDevice):
            def handshake(self):
                return HandshakeResult(resolution=(480, 480))

            def send_frame(self, image_data):
                return len(image_data) > 0

            def close(self):
                pass

        dev = StubFrame()
        assert dev.send_frame(b'\x00\x01') is True
        assert dev.send_frame(b'') is False


# =========================================================================
# LedDevice (LED data senders)
# =========================================================================


class TestLedDevice:
    """LedDevice extends UsbDevice with send_led_data() + is_sending."""

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            LedDevice()  # type: ignore[abstract]

    def test_missing_is_sending_rejected(self):
        class NoIsSending(LedDevice):
            def handshake(self):
                return HandshakeResult()

            def send_led_data(self, packet):
                return True

            def close(self):
                pass

        with pytest.raises(TypeError):
            NoIsSending()  # type: ignore[abstract]

    def test_concrete_subclass_works(self):
        class StubLed(LedDevice):
            def __init__(self):
                self._sending = False

            def handshake(self):
                return HandshakeResult()

            def send_data(self, packet):
                return True

            @property
            def is_sending(self):
                return self._sending

            def close(self):
                pass

        dev = StubLed()
        assert dev.is_sending is False
        assert dev.send_data(b'\xff') is True


# =========================================================================
# Hierarchy: real classes inherit correctly
# =========================================================================


class TestHierarchy:
    """Verify real device classes are in the correct ABC hierarchy."""

    def test_scsi_is_frame_device(self):
        from trcc.legacy.adapters.device.scsi import ScsiDevice

        assert issubclass(ScsiDevice, FrameDevice)
        assert issubclass(ScsiDevice, UsbDevice)

    def test_bulk_is_frame_device(self):
        from trcc.legacy.adapters.device.bulk import BulkDevice

        assert issubclass(BulkDevice, FrameDevice)
        assert issubclass(BulkDevice, UsbDevice)

    def test_hid_device_is_frame_device(self):
        from trcc.legacy.adapters.device.hid import HidDevice

        assert issubclass(HidDevice, FrameDevice)
        assert issubclass(HidDevice, UsbDevice)

    def test_hid_type2_is_frame_device(self):
        from trcc.legacy.adapters.device.hid import HidDeviceType2

        assert issubclass(HidDeviceType2, FrameDevice)
        assert issubclass(HidDeviceType2, UsbDevice)

    def test_hid_type3_is_frame_device(self):
        from trcc.legacy.adapters.device.hid import HidDeviceType3

        assert issubclass(HidDeviceType3, FrameDevice)
        assert issubclass(HidDeviceType3, UsbDevice)

    def test_led_sender_is_led_device(self):
        from trcc.legacy.adapters.device.led import LedHidSender

        assert issubclass(LedHidSender, LedDevice)
        assert issubclass(LedHidSender, UsbDevice)

    def test_led_sender_not_frame_device(self):
        from trcc.legacy.adapters.device.led import LedHidSender

        assert not issubclass(LedHidSender, FrameDevice)


# =========================================================================
# Return type compatibility
# =========================================================================


class TestReturnTypes:
    """HandshakeResult subclasses satisfy the ABC contract."""

    def test_hid_handshake_info_is_handshake_result(self):
        assert issubclass(HidHandshakeInfo, HandshakeResult)

    def test_led_handshake_info_is_handshake_result(self):
        assert issubclass(LedHandshakeInfo, HandshakeResult)

"""Tests for kvm_led_device.py — KVM LED protocol packet builder and persistence."""

import tempfile
import unittest
from pathlib import Path

from trcc.device_kvm_led import (
    CMD_LED,
    CMD_ONOFF,
    CMD_SCENE_SAVE,
    CMD_STATE_QUERY,
    DEFAULT_CHANNEL_ENABLES,
    HEADER,
    NUM_CHANNELS,
    PROMODE_HEADER,
    PROMODE_SIZE,
    KvmChannelState,
    KvmLedState,
    KvmPacketBuilder,
    KvmProModePersistence,
)

# =============================================================================
# KvmChannelState
# =============================================================================

class TestKvmChannelState(unittest.TestCase):
    """Default channel state values."""

    def test_defaults(self):
        ch = KvmChannelState()
        self.assertTrue(ch.on)
        self.assertEqual(ch.mode, 1)
        self.assertEqual(ch.brightness, 100)
        self.assertEqual((ch.r, ch.g, ch.b), (255, 0, 0))


# =============================================================================
# KvmLedState
# =============================================================================

class TestKvmLedState(unittest.TestCase):

    def test_default_10_channels(self):
        state = KvmLedState()
        self.assertEqual(len(state.channels), NUM_CHANNELS)
        self.assertEqual(len(state.channel_enables), NUM_CHANNELS)

    def test_default_channel_enables(self):
        state = KvmLedState()
        self.assertEqual(state.channel_enables, DEFAULT_CHANNEL_ENABLES)
        # Channel 8 disabled by default
        self.assertEqual(state.channel_enables[8], 0)

    def test_channels_independent(self):
        """Each channel is a separate instance."""
        state = KvmLedState()
        state.channels[0].r = 0
        self.assertEqual(state.channels[1].r, 255)


# =============================================================================
# KvmPacketBuilder — ON/OFF
# =============================================================================

class TestBuildOnoff(unittest.TestCase):

    def test_packet_length(self):
        pkt = KvmPacketBuilder.build_onoff(KvmLedState())
        self.assertEqual(len(pkt), 15)

    def test_header_and_command(self):
        pkt = KvmPacketBuilder.build_onoff(KvmLedState())
        self.assertEqual(pkt[0:2], HEADER)
        self.assertEqual(pkt[2], CMD_ONOFF)
        self.assertEqual(pkt[3], 0x00)  # channel index (broadcast)

    def test_all_on_by_default(self):
        pkt = KvmPacketBuilder.build_onoff(KvmLedState())
        for i in range(NUM_CHANNELS):
            self.assertEqual(pkt[5 + i], 1)

    def test_some_channels_off(self):
        state = KvmLedState()
        state.channels[0].on = False
        state.channels[5].on = False
        pkt = KvmPacketBuilder.build_onoff(state)
        self.assertEqual(pkt[5], 0)  # ch 0 off
        self.assertEqual(pkt[10], 0)  # ch 5 off
        self.assertEqual(pkt[6], 1)  # ch 1 on

    def test_mode_byte(self):
        pkt = KvmPacketBuilder.build_onoff(KvmLedState(), mode=42)
        self.assertEqual(pkt[4], 42)


# =============================================================================
# KvmPacketBuilder — LED
# =============================================================================

class TestBuildLed(unittest.TestCase):

    def test_packet_length(self):
        pkt = KvmPacketBuilder.build_led(KvmLedState())
        self.assertEqual(len(pkt), 23)

    def test_header_and_command(self):
        pkt = KvmPacketBuilder.build_led(KvmLedState(), channel=3, mode=7)
        self.assertEqual(pkt[0:2], HEADER)
        self.assertEqual(pkt[2], CMD_LED)
        self.assertEqual(pkt[3], 3)  # channel
        self.assertEqual(pkt[4], 7)  # mode

    def test_rgb_values(self):
        state = KvmLedState()
        state.channels[2].r = 10
        state.channels[2].g = 20
        state.channels[2].b = 30
        state.channels[2].brightness = 80
        pkt = KvmPacketBuilder.build_led(state, channel=2)
        self.assertEqual(pkt[5], 80)   # brightness
        self.assertEqual(pkt[7], 10)   # R
        self.assertEqual(pkt[8], 20)   # G
        self.assertEqual(pkt[9], 30)   # B

    def test_reserved_bytes_zero(self):
        pkt = KvmPacketBuilder.build_led(KvmLedState())
        self.assertEqual(pkt[10:13], bytes(3))

    def test_channel_enables(self):
        state = KvmLedState()
        pkt = KvmPacketBuilder.build_led(state)
        # Channel 8 disabled by default
        for i in range(NUM_CHANNELS):
            self.assertEqual(pkt[13 + i], state.channel_enables[i])
        self.assertEqual(pkt[13 + 8], 0)

    def test_speed_default(self):
        pkt = KvmPacketBuilder.build_led(KvmLedState())
        self.assertEqual(pkt[6], 1)  # default speed


# =============================================================================
# KvmPacketBuilder — Scene Save
# =============================================================================

class TestBuildSceneSave(unittest.TestCase):

    def test_packet_length(self):
        pkt = KvmPacketBuilder.build_scene_save(KvmLedState())
        self.assertEqual(len(pkt), 65)

    def test_header_and_command(self):
        pkt = KvmPacketBuilder.build_scene_save(KvmLedState(), scene=3)
        self.assertEqual(pkt[0:2], HEADER)
        self.assertEqual(pkt[2], CMD_SCENE_SAVE)
        self.assertEqual(pkt[3], 3)  # scene number

    def test_payload_layout(self):
        """Verify on/off, mode, brightness, RGB are at correct offsets."""
        state = KvmLedState()
        state.channels[0] = KvmChannelState(on=False, mode=7, brightness=50,
                                            r=11, g=22, b=33)
        pkt = KvmPacketBuilder.build_scene_save(state)
        # On/off at offset 5
        self.assertEqual(pkt[5], 0)   # ch 0 off
        self.assertEqual(pkt[6], 1)   # ch 1 on
        # Modes at offset 15
        self.assertEqual(pkt[15], 7)  # ch 0 mode
        self.assertEqual(pkt[16], 1)  # ch 1 mode (default)
        # Brightness at offset 25
        self.assertEqual(pkt[25], 50)   # ch 0
        self.assertEqual(pkt[26], 100)  # ch 1 (default)
        # RGB at offset 35
        self.assertEqual(pkt[35], 11)   # ch 0 R
        self.assertEqual(pkt[36], 22)   # ch 0 G
        self.assertEqual(pkt[37], 33)   # ch 0 B
        self.assertEqual(pkt[38], 255)  # ch 1 R (default)

    def test_all_channels_present(self):
        pkt = KvmPacketBuilder.build_scene_save(KvmLedState())
        # 10 on/off + 10 mode + 10 brightness + 30 rgb = 60 data bytes
        self.assertEqual(len(pkt) - 5, 60)


# =============================================================================
# KvmPacketBuilder — State Query
# =============================================================================

class TestBuildStateQuery(unittest.TestCase):

    def test_packet_length(self):
        pkt = KvmPacketBuilder.build_state_query()
        self.assertEqual(len(pkt), 5)

    def test_header_and_command(self):
        pkt = KvmPacketBuilder.build_state_query()
        self.assertEqual(pkt[0:2], HEADER)
        self.assertEqual(pkt[2], CMD_STATE_QUERY)
        self.assertEqual(pkt[3], 0)
        self.assertEqual(pkt[4], 0)


# =============================================================================
# KvmProModePersistence
# =============================================================================

class TestKvmProModePersistence(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_save_and_load_roundtrip(self):
        state = KvmLedState()
        state.channels[0] = KvmChannelState(on=False, mode=6, brightness=77,
                                            r=11, g=22, b=33)
        state.channels[9] = KvmChannelState(on=True, mode=2, brightness=50,
                                            r=100, g=200, b=150)

        path = self.tmpdir / "proMode.dc"
        KvmProModePersistence.save(state, path)

        loaded = KvmProModePersistence.load(path)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        ch0 = loaded.channels[0]
        self.assertFalse(ch0.on)
        self.assertEqual(ch0.mode, 6)
        self.assertEqual(ch0.brightness, 77)
        self.assertEqual((ch0.r, ch0.g, ch0.b), (11, 22, 33))

        ch9 = loaded.channels[9]
        self.assertTrue(ch9.on)
        self.assertEqual(ch9.mode, 2)
        self.assertEqual(ch9.brightness, 50)
        self.assertEqual((ch9.r, ch9.g, ch9.b), (100, 200, 150))

    def test_file_size(self):
        path = self.tmpdir / "proMode.dc"
        KvmProModePersistence.save(KvmLedState(), path)
        self.assertEqual(path.stat().st_size, PROMODE_SIZE)

    def test_file_header_byte(self):
        path = self.tmpdir / "proMode.dc"
        KvmProModePersistence.save(KvmLedState(), path)
        data = path.read_bytes()
        self.assertEqual(data[0], PROMODE_HEADER)

    def test_load_missing_file(self):
        result = KvmProModePersistence.load(self.tmpdir / "nope.dc")
        self.assertIsNone(result)

    def test_load_invalid_header(self):
        path = self.tmpdir / "bad.dc"
        path.write_bytes(bytes([0xFF] * PROMODE_SIZE))
        result = KvmProModePersistence.load(path)
        self.assertIsNone(result)

    def test_load_truncated_file(self):
        path = self.tmpdir / "short.dc"
        path.write_bytes(bytes([PROMODE_HEADER] * 10))
        result = KvmProModePersistence.load(path)
        self.assertIsNone(result)

    def test_scene_save_and_load(self):
        state = KvmLedState()
        state.channels[3].r = 42
        KvmProModePersistence.save_scene(state, 2, self.tmpdir)

        loaded = KvmProModePersistence.load_scene(2, self.tmpdir)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.channels[3].r, 42)

    def test_scene_filename(self):
        KvmProModePersistence.save_scene(KvmLedState(), 4, self.tmpdir)
        self.assertTrue((self.tmpdir / "4proMode.dc").is_file())

    def test_creates_parent_dirs(self):
        path = self.tmpdir / "sub" / "dir" / "proMode.dc"
        KvmProModePersistence.save(KvmLedState(), path)
        self.assertTrue(path.is_file())

    def test_all_channels_roundtrip(self):
        """Every channel's state survives save/load."""
        state = KvmLedState()
        for i in range(NUM_CHANNELS):
            ch = state.channels[i]
            ch.on = (i % 2 == 0)
            ch.mode = i
            ch.brightness = i * 10
            ch.r = i * 25
            ch.g = 255 - i * 25
            ch.b = i * 5

        path = self.tmpdir / "proMode.dc"
        KvmProModePersistence.save(state, path)
        loaded = KvmProModePersistence.load(path)
        assert loaded is not None

        for i in range(NUM_CHANNELS):
            orig = state.channels[i]
            got = loaded.channels[i]
            self.assertEqual(got.on, orig.on, f"ch{i} on")
            self.assertEqual(got.mode, orig.mode, f"ch{i} mode")
            self.assertEqual(got.brightness, orig.brightness, f"ch{i} brightness")
            self.assertEqual((got.r, got.g, got.b),
                             (orig.r, orig.g, orig.b), f"ch{i} rgb")


if __name__ == '__main__':
    unittest.main()

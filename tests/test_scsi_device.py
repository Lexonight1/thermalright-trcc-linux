"""Tests for scsi_device – SCSI frame chunking, header building, CRC."""

import binascii
import struct
import unittest

from trcc.scsi_device import (
    _build_header,
    _crc32,
    _get_frame_chunks,
    _CHUNK_SIZE,
    _FRAME_CMD_BASE,
)


class TestCRC32(unittest.TestCase):
    """CRC32 against known values."""

    def test_empty(self):
        self.assertEqual(_crc32(b''), binascii.crc32(b'') & 0xFFFFFFFF)

    def test_known_value(self):
        self.assertEqual(_crc32(b'hello'), binascii.crc32(b'hello') & 0xFFFFFFFF)

    def test_unsigned(self):
        """Result is always unsigned 32-bit."""
        result = _crc32(b'\xff' * 100)
        self.assertGreaterEqual(result, 0)
        self.assertLess(result, 2**32)


class TestBuildHeader(unittest.TestCase):
    """20-byte SCSI command header: cmd(4) + zeros(8) + size(4) + crc32(4)."""

    def test_length(self):
        header = _build_header(0xF5, 0xE100)
        self.assertEqual(len(header), 20)

    def test_cmd_field(self):
        header = _build_header(0xF5, 0xE100)
        cmd = struct.unpack('<I', header[:4])[0]
        self.assertEqual(cmd, 0xF5)

    def test_zero_padding(self):
        header = _build_header(0xF5, 0xE100)
        self.assertEqual(header[4:12], b'\x00' * 8)

    def test_size_field(self):
        header = _build_header(0x1F5, 0xE100)
        size = struct.unpack('<I', header[12:16])[0]
        self.assertEqual(size, 0xE100)

    def test_crc_matches_payload(self):
        header = _build_header(0xF5, 0xE100)
        payload = header[:16]
        expected_crc = binascii.crc32(payload) & 0xFFFFFFFF
        actual_crc = struct.unpack('<I', header[16:20])[0]
        self.assertEqual(actual_crc, expected_crc)

    def test_different_cmds_different_headers(self):
        h1 = _build_header(0xF5, 0xE100)
        h2 = _build_header(0x1F5, 0xE100)
        self.assertNotEqual(h1, h2)


class TestGetFrameChunks(unittest.TestCase):
    """Frame chunk calculation for various resolutions."""

    def test_320x320_total_bytes(self):
        chunks = _get_frame_chunks(320, 320)
        total = sum(size for _, size in chunks)
        self.assertEqual(total, 320 * 320 * 2)  # 204,800

    def test_320x320_chunk_count(self):
        chunks = _get_frame_chunks(320, 320)
        self.assertEqual(len(chunks), 4)  # 3×64K + 8K

    def test_480x480_total_bytes(self):
        chunks = _get_frame_chunks(480, 480)
        total = sum(size for _, size in chunks)
        self.assertEqual(total, 480 * 480 * 2)  # 460,800

    def test_480x480_chunk_count(self):
        chunks = _get_frame_chunks(480, 480)
        self.assertEqual(len(chunks), 8)  # 7×64K + 2K

    def test_640x480_total_bytes(self):
        chunks = _get_frame_chunks(640, 480)
        total = sum(size for _, size in chunks)
        self.assertEqual(total, 640 * 480 * 2)  # 614,400

    def test_240x240_total_bytes(self):
        chunks = _get_frame_chunks(240, 240)
        total = sum(size for _, size in chunks)
        self.assertEqual(total, 240 * 240 * 2)  # 115,200

    def test_chunk_sizes_within_limit(self):
        """No chunk exceeds 64 KiB."""
        for w, h in [(320, 320), (480, 480), (640, 480)]:
            for _, size in _get_frame_chunks(w, h):
                self.assertLessEqual(size, _CHUNK_SIZE)

    def test_cmd_encodes_index(self):
        """Chunk index embedded in bits [27:24] above base command."""
        chunks = _get_frame_chunks(480, 480)
        for i, (cmd, _) in enumerate(chunks):
            expected = _FRAME_CMD_BASE | (i << 24)
            self.assertEqual(cmd, expected, f"Chunk {i}: {cmd:#x} != {expected:#x}")

    def test_last_chunk_may_be_smaller(self):
        chunks = _get_frame_chunks(320, 320)
        last_size = chunks[-1][1]
        self.assertEqual(last_size, 320 * 320 * 2 - 3 * _CHUNK_SIZE)  # 8192


if __name__ == '__main__':
    unittest.main()

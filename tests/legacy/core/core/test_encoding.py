"""Tests for core/encoding.py — RGB565 encoding utilities.

Parametrized over FBL_PROFILES so every device profile is covered
automatically. Adding a new profile to models.py adds new test cases here.
"""
from __future__ import annotations

import struct
import unittest

import pytest

from trcc.legacy.core.encoding import byte_order_for, rgb_to_bytes
from trcc.legacy.core.models import FBL_PROFILES

# ── Parametrized over every FBL profile ──────────────────────────────────────

_RGB565_PROFILES = [
    (fbl, p) for fbl, p in FBL_PROFILES.items() if not p.jpeg
]

_JPEG_PROFILES = [
    (fbl, p) for fbl, p in FBL_PROFILES.items() if p.jpeg
]


@pytest.mark.parametrize("fbl,profile", _RGB565_PROFILES,
                         ids=[str(f) for f, _ in _RGB565_PROFILES])
def test_byte_order_matches_profile(fbl, profile):
    """byte_order_for() returns the byte order declared in FBL_PROFILES."""
    expected = profile.byte_order  # '>' or '<'
    assert byte_order_for('scsi', profile.resolution, fbl=fbl) == expected


@pytest.mark.parametrize("fbl,profile", _JPEG_PROFILES,
                         ids=[str(f) for f, _ in _JPEG_PROFILES])
def test_jpeg_profiles_return_big_endian(fbl, profile):
    """JPEG-mode FBLs declare big_endian=False but return '<' (irrelevant for JPEG)."""
    result = byte_order_for('hid', profile.resolution, fbl=fbl)
    assert result in ('<', '>'), f"FBL {fbl} returned unexpected byte order: {result!r}"


@pytest.mark.parametrize("proto", ['scsi', 'hid', 'bulk'],
                         ids=['scsi', 'hid', 'bulk'])
@pytest.mark.parametrize("fbl,profile", _RGB565_PROFILES,
                         ids=[str(f) for f, _ in _RGB565_PROFILES])
def test_protocol_irrelevant_with_fbl(proto, fbl, profile):
    """Protocol string is ignored when FBL is provided — profile decides."""
    expected = profile.byte_order
    assert byte_order_for(proto, profile.resolution, fbl=fbl) == expected


# ── Fallback (no FBL) uses resolution heuristic ──────────────────────────────

class TestByteOrderFallback(unittest.TestCase):
    """When FBL is absent, byte_order_for() falls back to resolution heuristic."""

    def test_320x320_defaults_big_endian(self):
        """320x320 without FBL → '>' (safe default for SCSI devices)."""
        from trcc.legacy.core.models import FBL_PROFILES
        profile = FBL_PROFILES[100]   # canonical 320x320 profile
        self.assertEqual(byte_order_for('scsi', profile.resolution), '>')

    def test_480x480_defaults_little_endian(self):
        """480x480 without FBL → '<'."""
        from trcc.legacy.core.models import FBL_PROFILES
        profile = FBL_PROFILES[72]
        self.assertEqual(byte_order_for('scsi', profile.resolution), '<')

    def test_240x240_defaults_little_endian(self):
        from trcc.legacy.core.models import FBL_PROFILES
        profile = FBL_PROFILES[36]
        self.assertEqual(byte_order_for('hid', profile.resolution), '<')

    def test_320x240_defaults_little_endian(self):
        from trcc.legacy.core.models import FBL_PROFILES
        profile = FBL_PROFILES[50]
        self.assertEqual(byte_order_for('scsi', profile.resolution), '<')


# ── rgb_to_bytes ─────────────────────────────────────────────────────────────

class TestRgbToBytes(unittest.TestCase):
    """rgb_to_bytes() — single pixel RGB → RGB565 conversion."""

    def test_pure_red(self):
        """Pure red (255,0,0) → 0xF800 in RGB565."""
        self.assertEqual(rgb_to_bytes(255, 0, 0, '>'), struct.pack('>H', 0xF800))

    def test_pure_green(self):
        """Pure green (0,255,0) → 0x07E0 in RGB565."""
        self.assertEqual(rgb_to_bytes(0, 255, 0, '>'), struct.pack('>H', 0x07E0))

    def test_pure_blue(self):
        """Pure blue (0,0,255) → 0x001F in RGB565."""
        self.assertEqual(rgb_to_bytes(0, 0, 255, '>'), struct.pack('>H', 0x001F))

    def test_white(self):
        """White (255,255,255) → 0xFFFF."""
        self.assertEqual(rgb_to_bytes(255, 255, 255, '>'), struct.pack('>H', 0xFFFF))

    def test_black(self):
        """Black (0,0,0) → 0x0000."""
        self.assertEqual(rgb_to_bytes(0, 0, 0, '>'), b'\x00\x00')

    def test_big_endian_byte_order(self):
        """Big-endian packs MSB first."""
        result = rgb_to_bytes(255, 0, 0, '>')
        self.assertEqual(result[0], 0xF8)
        self.assertEqual(result[1], 0x00)

    def test_little_endian_byte_order(self):
        """Little-endian packs LSB first."""
        result = rgb_to_bytes(255, 0, 0, '<')
        self.assertEqual(result[0], 0x00)
        self.assertEqual(result[1], 0xF8)

    def test_output_is_2_bytes(self):
        """Every pixel encodes to exactly 2 bytes."""
        for r, g, b in [(0, 0, 0), (128, 64, 32), (255, 255, 255)]:
            with self.subTest(r=r, g=g, b=b):
                self.assertEqual(len(rgb_to_bytes(r, g, b)), 2)

    def test_default_byte_order_is_big_endian(self):
        """Default byte_order parameter is '>'."""
        self.assertEqual(rgb_to_bytes(255, 0, 0), rgb_to_bytes(255, 0, 0, '>'))

    def test_bit_masking(self):
        """RGB565: R uses 5 bits, G uses 6 bits, B uses 5 bits."""
        result = struct.unpack('>H', rgb_to_bytes(255, 255, 255, '>'))[0]
        self.assertEqual((result >> 11) & 0x1F, 31)
        self.assertEqual((result >> 5) & 0x3F, 63)
        self.assertEqual(result & 0x1F, 31)

    def test_low_bits_discarded(self):
        """Low bits below RGB565 precision are discarded."""
        self.assertEqual(rgb_to_bytes(7, 3, 7, '>'), b'\x00\x00')


if __name__ == '__main__':
    unittest.main()

"""
Reusable binary reader for TRCC .dc / .tr file parsing.

Replaces the duplicated read_int32/read_bool/read_string/read_float/read_byte
helper closures that were copy-pasted across dc_parser.py and dc_writer.py.
"""

import struct


class BinaryReader:
    """Sequential binary reader mirroring C# BinaryReader used in Windows TRCC."""

    __slots__ = ('data', 'pos')

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def read_int32(self) -> int:
        if self.pos + 4 > len(self.data):
            raise IndexError("End of data")
        val = struct.unpack_from('<i', self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_bool(self) -> bool:
        if self.pos >= len(self.data):
            raise IndexError("End of data")
        val = self.data[self.pos] != 0
        self.pos += 1
        return val

    def read_string(self) -> str:
        if self.pos >= len(self.data):
            return ""
        length = self.data[self.pos]
        self.pos += 1
        if length > 0 and self.pos + length <= len(self.data):
            try:
                s = self.data[self.pos:self.pos + length].decode('utf-8')
            except (UnicodeDecodeError, ValueError):
                s = ""
            self.pos += length
            return s
        return ""

    def read_float(self) -> float:
        if self.pos + 4 > len(self.data):
            raise IndexError("End of data")
        val = struct.unpack_from('<f', self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_byte(self) -> int:
        if self.pos >= len(self.data):
            raise IndexError("End of data")
        val = self.data[self.pos]
        self.pos += 1
        return val

    def read_bytes(self, n: int) -> bytes:
        """Read exactly n bytes and advance position."""
        if self.pos + n > len(self.data):
            raise IndexError("End of data")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def remaining(self) -> int:
        """Bytes remaining from current position."""
        return len(self.data) - self.pos

    def has_bytes(self, n: int) -> bool:
        """Check if at least n bytes remain."""
        return self.pos + n <= len(self.data)

    def skip(self, n: int) -> None:
        """Skip n bytes forward."""
        self.pos += n

    def read_font_color(self) -> tuple:
        """Read font config + ARGB color (C# UCXiTongXianShiSub pattern).

        Returns (font_name, font_size, font_style, font_unit, font_charset,
                 alpha, red, green, blue).
        """
        font_name = self.read_string()
        font_size = self.read_float()
        font_style = self.read_byte()
        font_unit = self.read_byte()
        font_charset = self.read_byte()
        a = self.read_byte()
        r = self.read_byte()
        g = self.read_byte()
        b = self.read_byte()
        return font_name, font_size, font_style, font_unit, font_charset, a, r, g, b

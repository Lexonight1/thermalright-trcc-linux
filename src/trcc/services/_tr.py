"""The Windows app's ``.tr`` theme file, read and written byte for byte.

``.tr`` is not an archive.  ``buttonDaoChu_Click`` (FormCZTV.cs) writes one
stream, and ``buttonDaoRu_Click`` reads it back:

    DD DC DD DC            magic (DC DC DC DC for the older 0xDC layout)
    <config body>          exactly config1.dc minus its first byte
    DC x 10240             padding
    int32 n, n bytes       mask 01.png (n == 0: no mask)
    int32 0, int32 n, n    background 00.png            -- or --
    int32 k, ...           Theme.zt minus its first byte (k frames, to EOF)

So ``config1.dc`` is the magic's first byte plus the body, and ``Theme.zt`` is
``0xDC`` plus the tail.  Both are kept VERBATIM rather than re-serialised: a
round trip through our model would drop whatever the model does not carry.

The images are PNG from ``BitmapToByte``, which returns
``MemoryStream.GetBuffer()`` -- the whole internal buffer, so a PNG can trail
zero bytes after its IEND.  They are trimmed on read.

We used to write a zip under this name, which the Windows importer cannot
read, and to accept only a zip, so a Windows export did nothing here (#272).
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field

from ..core.errors import ThemeError
from . import _dc as Dc

log = logging.getLogger(__name__)

_MAGIC_DD = b"\xdd\xdc\xdd\xdc"
_MAGICS = (_MAGIC_DD, b"\xdc\xdc\xdc\xdc")
_PADDING = b"\xdc" * 10240
_PNG_END = b"IEND\xaeB`\x82"


@dataclass(frozen=True, slots=True)
class TrTheme:
    """A ``.tr`` file's parts, each already in its on-disk theme form.

    ``repr=False`` throughout: these are whole images, and a repr that
    rendered them would put megabytes into any log line naming the object.
    """

    config_dc: bytes = field(repr=False)                       # config1.dc
    mask_png: bytes | None = field(default=None, repr=False)        # 01.png
    background_png: bytes | None = field(default=None, repr=False)  # 00.png
    theme_zt: bytes | None = field(default=None, repr=False)        # Theme.zt


def is_tr(head: bytes) -> bool:
    """True when *head* starts with a ``.tr`` magic."""
    log.debug("is_tr: %s", head[:4].hex())
    return head[:4] in _MAGICS


def read(data: bytes) -> TrTheme:
    """Split a ``.tr`` into its theme files; ``ThemeError`` if it is not one."""
    if not is_tr(data):
        raise ThemeError(f"not a .tr theme file (starts {data[:4].hex()})")
    if (end := data.find(_PADDING, 4)) < 0:
        raise ThemeError(".tr theme file has no padding block -- truncated?")
    config_dc = data[:1] + data[4:end]
    Dc.Reader().parse(config_dc, ".tr")         # validates; raises ThemeError
    pos = end + len(_PADDING)

    mask_len, pos = _int32(data, pos)
    mask, pos = _take(data, pos, mask_len)
    flag, pos = _int32(data, pos)
    if flag == 0:
        bg_len, pos = _int32(data, pos)
        background, _ = _take(data, pos, bg_len)
        theme = TrTheme(config_dc, _trim_png(mask), _trim_png(background))
    else:
        theme = TrTheme(config_dc, _trim_png(mask), theme_zt=b"\xdc" + data[pos - 4:])
    log.info("read: config=%dB mask=%s background=%s zt=%s",
             len(config_dc), _size(theme.mask_png), _size(theme.background_png),
             _size(theme.theme_zt))
    return theme


def write(theme: TrTheme) -> bytes:
    """The ``.tr`` the Windows app writes for *theme* -- and can import."""
    if theme.config_dc[:1] != _MAGIC_DD[:1]:
        raise ThemeError("a .tr export needs a 0xDD config1.dc")
    mask = theme.mask_png or b""
    out = [_MAGIC_DD, theme.config_dc[1:], _PADDING, _pack(len(mask)), mask]
    if theme.background_png is not None:
        out += [_pack(0), _pack(len(theme.background_png)), theme.background_png]
    elif theme.theme_zt is not None:
        out.append(theme.theme_zt[1:])
    else:
        raise ThemeError("a .tr needs a still background (00.png) or a Theme.zt; "
                         "export as .zip to keep a video background")
    data = b"".join(out)
    log.info("write: %d bytes (mask=%s background=%s zt=%s)", len(data),
             _size(theme.mask_png), _size(theme.background_png),
             _size(theme.theme_zt))
    return data


def _int32(data: bytes, pos: int) -> tuple[int, int]:
    """One little-endian int32 at *pos*, bounds-checked."""
    log.debug("_int32: pos=%d of %d", pos, len(data))
    if pos + 4 > len(data):
        raise ThemeError(f".tr theme file truncated at byte {pos}")
    return struct.unpack_from("<i", data, pos)[0], pos + 4


def _take(data: bytes, pos: int, length: int) -> tuple[bytes | None, int]:
    """*length* bytes at *pos* (None for 0), refusing a length past the end."""
    log.debug("_take: %d byte(s) at %d of %d", length, pos, len(data))
    if length < 0 or pos + length > len(data):
        raise ThemeError(f".tr theme file: {length}-byte block at {pos} "
                         f"runs past its {len(data)} bytes")
    return (data[pos:pos + length] or None), pos + length


def _pack(value: int) -> bytes:
    log.debug("_pack: %d", value)
    return struct.pack("<i", value)


def _trim_png(png: bytes | None) -> bytes | None:
    """Drop ``GetBuffer()``'s zero bytes after a PNG's IEND chunk."""
    log.debug("_trim_png: %s", _size(png))
    if png is None or (end := png.find(_PNG_END)) < 0:
        return png
    return png[:end + len(_PNG_END)]


def _size(blob: bytes | None) -> str:
    log.debug("_size: %s", None if blob is None else len(blob))
    return "none" if blob is None else f"{len(blob)}B"

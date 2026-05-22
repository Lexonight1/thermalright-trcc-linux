"""Shared parity-test scaffolding.

Two responsibilities:

  * **Cross-tree helpers** — map legacy ``style_id`` ints to next/ ``LedStyle``
    enums (and similar coercions) so a single parity-test body can hand
    equivalent inputs to both trees.

  * **Diff reporting** — when two byte strings differ, surface the *first*
    diverging offset with a hex window of surrounding context.  Big "byte
    arrays don't match" assertion messages are useless; ``hex_diff_context``
    is.

Both trees coexist on ``main``; they share the top-level ``trcc`` namespace
package, so a single Python process can import ``trcc.foo`` (legacy) and
``trcc.foo`` side-by-side without collision.  Tests rely on that —
nothing here patches sys.modules or fiddles with the import system.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trcc.legacy.core.models import LedStyle


# =========================================================================
# Legacy style_id (int) ↔ next/ LedStyle (enum)
# =========================================================================
#
# Legacy ``core/models/led.py`` keys LED styles by int (1..12).  Next/
# uses the ``LedStyle`` string enum; the comment lines next to each
# member spell out the legacy id.  This table is the single mapping
# point so test bodies don't sprinkle ``LedStyle.AX120`` constants and
# magic numbers in the same line.


def style_by_legacy_id() -> dict[int, LedStyle]:
    """Return the legacy_id → LedStyle map.  Imported lazily so this
    module is import-safe before next/ is on the path."""
    from trcc.legacy.core.models import LedStyle

    return {
        1:  LedStyle.AX120,
        2:  LedStyle.PA120,
        3:  LedStyle.AK120,
        4:  LedStyle.LC1,
        5:  LedStyle.LF8,
        6:  LedStyle.LF12,
        7:  LedStyle.LF10,
        8:  LedStyle.CZ1,
        9:  LedStyle.LC2,
        10: LedStyle.LF11,
        11: LedStyle.LF15,
        12: LedStyle.LF13,
    }


# =========================================================================
# Diff reporting
# =========================================================================


@dataclass(frozen=True, slots=True)
class ByteDiff:
    """One byte-level disagreement between legacy and next/ output."""
    offset: int
    legacy_byte: int
    next_byte: int
    legacy_window: bytes
    next_window: bytes
    legacy_total_len: int
    next_total_len: int

    def hex_context(self) -> str:
        """Multi-line hex dump centred on the diff.

        ::

            offset 0x102:
              legacy: 00 11 22 33 ^44 55 66 77
              next  : 00 11 22 33 ^4a 55 66 77
              (legacy_total=320 bytes, next_total=320 bytes)
        """
        legacy_hex = " ".join(f"{b:02x}" for b in self.legacy_window)
        next_hex = " ".join(f"{b:02x}" for b in self.next_window)
        return (
            f"offset 0x{self.offset:x} (= byte {self.offset}):\n"
            f"  legacy: {legacy_hex}\n"
            f"  next  : {next_hex}\n"
            f"  (legacy_total={self.legacy_total_len} bytes, "
            f"next_total={self.next_total_len} bytes)\n"
            f"  diverging byte: legacy=0x{self.legacy_byte:02x} "
            f"({self.legacy_byte}) vs next=0x{self.next_byte:02x} "
            f"({self.next_byte})"
        )


def diff_bytes(
    legacy: bytes,
    next_: bytes,
    *,
    window: int = 16,
) -> ByteDiff | None:
    """Return the first diverging byte's context, or None if equal.

    Different-length outputs report the diff at the boundary so the
    caller sees *both* lengths in the error message.
    """
    if legacy == next_:
        return None

    common = min(len(legacy), len(next_))
    offset = next(
        (i for i in range(common) if legacy[i] != next_[i]),
        common,
    )

    half = window // 2
    win_lo = max(0, offset - half)
    win_hi_legacy = min(len(legacy), win_lo + window)
    win_hi_next = min(len(next_), win_lo + window)

    return ByteDiff(
        offset=offset,
        legacy_byte=legacy[offset] if offset < len(legacy) else -1,
        next_byte=next_[offset] if offset < len(next_) else -1,
        legacy_window=legacy[win_lo:win_hi_legacy],
        next_window=next_[win_lo:win_hi_next],
        legacy_total_len=len(legacy),
        next_total_len=len(next_),
    )


def assert_bytes_equal(
    legacy: bytes,
    next_: bytes,
    *,
    label: str = "wire bytes",
) -> None:
    """Pytest-friendly assertion with hex-diff context on failure."""
    diff = diff_bytes(legacy, next_)
    if diff is not None:
        raise AssertionError(
            f"{label}: legacy != next/\n{diff.hex_context()}",
        )


# =========================================================================
# Fixture helpers — canned inputs that legacy + next/ both consume
# =========================================================================


def solid_color_array(
    count: int, rgb: tuple[int, int, int],
) -> list[tuple[int, int, int]]:
    """``[rgb] * count`` — typed for the LED color-array surface."""
    return [rgb] * count


def gradient_color_array(count: int) -> list[tuple[int, int, int]]:
    """One-color-per-LED rainbow walk — exercises remap-table coverage.

    The i-th LED gets ``(i * 4, 255 - i * 2, i * 7) % 256``, which (a)
    produces a deterministic distinct color per index, and (b) makes
    misaligned remap tables jump out by an order of magnitude when the
    diff window prints adjacent bytes.
    """
    return [
        ((i * 4) & 0xFF, (255 - i * 2) & 0xFF, (i * 7) & 0xFF)
        for i in range(count)
    ]


def alternating_on_mask(count: int) -> Sequence[bool]:
    """Every other LED on — exercises the global_on / is_on path of
    ``build_led_packet`` so off-LEDs reliably appear as (0,0,0)."""
    return [i % 2 == 0 for i in range(count)]

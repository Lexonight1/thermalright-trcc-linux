#!/usr/bin/env python3
"""Capture a live HWiNFO64 shared-memory dump for the test fixture corpus.

Run on Windows with HWiNFO64 running and ``Settings → Shared Memory Support``
enabled.  The free version of HWiNFO supports SHM for the first 12 hours
after launch; the Pro version always supports it.

The captured ``.bin`` file is committed under
``tests/fixtures/hwinfo/`` so the parser is verifiable on any platform —
no live MMF required in CI.  Each fixture tightens the contract for a
different HWiNFO version / hardware combination.

Reuses the production ``_HWiNFOMapping`` adapter and the pure
``_parse_header`` function from ``trcc.adapters.sensors._hwinfo``
— no duplicate ctypes wiring, no parallel struct definitions.

Usage::

    python dev/dump_hwinfo_shm.py
    python dev/dump_hwinfo_shm.py --output tests/fixtures/hwinfo/hwinfo_custom.bin
    python dev/dump_hwinfo_shm.py --verify-only

The default output filename is derived from the header version + reading
count: ``hwinfo_v{version}_{sensor_count}x{entry_count}.bin``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add src/ to path so we can import the production adapter.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / 'src'))

from trcc.adapters.sensors._hwinfo import (
    _HWINFO_MAGIC,
    _BytesMapping,
    _Header,
    _HWiNFOMapping,
    _parse_header,
)

_DEFAULT_FIXTURE_DIR = _REPO_ROOT / 'tests' / 'fixtures' / 'hwinfo'


def _live_size(header: _Header) -> int:
    """Bytes from the start of the MMF that hold live data -- the end of
    whichever table, sensors or readings, reaches further.  Anything beyond
    is unused padding in the file mapping.  (``_Header.total_size`` until
    the 2026-05-11 refactor dropped it, leaving this tool calling it.)"""
    return max(header.sec_off + header.sec_size * header.sec_count,
               header.ent_off + header.ent_size * header.ent_count)


def _capture(mapping: _HWiNFOMapping) -> bytes | None:
    """Read header → compute high-water mark → read that many bytes.

    Returns ``None`` if the MMF isn't HWiNFO64 (wrong magic, short header).
    """
    head = mapping.read(0, 1024)  # plenty for the 44-byte header
    try:
        header = _parse_header(head)
    except ValueError as e:
        print(f"  ERROR  header parse failed: {e}", file=sys.stderr)
        return None
    if header.magic != _HWINFO_MAGIC:
        print(f"  ERROR  magic mismatch: got 0x{header.magic:08x}, "
              f"expected 0x{_HWINFO_MAGIC:08x}", file=sys.stderr)
        return None
    size = _live_size(header)
    print(f"  HWiNFO header OK: version={header.version}, "
          f"sensors={header.sec_count}, readings={header.ent_count}")
    print(f"  Live region size: {size:,} bytes")
    return mapping.read(0, size)


def _verify(data: bytes) -> None:
    """Confirm a captured dump round-trips through the parser cleanly."""
    header = _parse_header(_BytesMapping(data).read(0, 64))
    if header.magic != _HWINFO_MAGIC:
        print("  VERIFY FAIL: round-trip magic mismatch", file=sys.stderr)
        sys.exit(1)
    if _live_size(header) != len(data):
        print(f"  VERIFY WARN: live size={_live_size(header):,} != "
              f"file_size={len(data):,} — may be a different format version",
              file=sys.stderr)
    print(f"  VERIFY OK: round-trip parse matches "
          f"({header.sec_count} sensors, {header.ent_count} readings)")


def _default_output_path(data: bytes) -> Path:
    """Build ``hwinfo_v{ver}_{sec_count}x{ent_count}.bin`` under the fixture dir."""
    header = _parse_header(data)
    fname = (
        f"hwinfo_v{header.version}_"
        f"{header.sec_count}x{header.ent_count}.bin"
    )
    return _DEFAULT_FIXTURE_DIR / fname


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        '--output', '-o', type=Path, default=None,
        help='Output file path; defaults to a versioned name under tests/fixtures/hwinfo/',
    )
    parser.add_argument(
        '--verify-only', action='store_true',
        help='Capture and round-trip-verify, but do not write a file',
    )
    args = parser.parse_args()

    print(f"  HWiNFO SHM dump  ({sys.platform})")
    try:
        mapping = _HWiNFOMapping()
    except OSError as e:
        print(f"  ERROR  HWiNFO MMF not found: {e}", file=sys.stderr)
        print("         On Windows: ensure HWiNFO64 is running with "
              "'Shared Memory Support' enabled.", file=sys.stderr)
        print("         On Linux/macOS: this is expected; this is a "
              "Windows-only tool.", file=sys.stderr)
        return 1

    try:
        data = _capture(mapping)
    finally:
        mapping.close()

    if data is None:
        return 1

    _verify(data)
    if args.verify_only:
        return 0

    out_path = args.output or _default_output_path(data)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    print(f"  Wrote {len(data):,} bytes → {out_path.relative_to(_REPO_ROOT)}")
    print()
    print("  Submit this file as a test fixture by committing it and "
          "opening a PR — every new hardware/version combination "
          "tightens HWiNFOSource's regression contract.")
    return 0


if __name__ == '__main__':
    sys.exit(main())

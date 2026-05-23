"""Fixture-based tests for HWiNFOSource — pure parser validation.

Tests run against captured HWiNFO Shared-Memory dumps in
``tests/fixtures/hwinfo/*.bin``.  No Win32 surface — the ``_BytesMapping``
adapter feeds the parser directly from a byte buffer, so the contract
is verifiable on Linux / macOS / CI without a live MMF.

Capture workflow for contributors with HWiNFO64:

    1. Open HWiNFO64, enable Shared Memory Support.
    2. ``python dev/dump_hwinfo_shm.py``
    3. Commit ``tests/fixtures/hwinfo/<auto-named>.bin`` to a PR.

Each new fixture (different HWiNFO version, different hardware)
tightens the parser's regression contract without us needing access to
that hardware.

Assertions are *structural* — they hold for any HWiNFO version /
hardware as long as the on-the-wire format hasn't moved.  Specific
sensor values, names, or counts are NOT asserted, because those vary
per machine.
"""
from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trcc.legacy.adapters.system.windows.sources.hwinfo import (
    _HWINFO_MAGIC,
    HWiNFOSource,
    _BytesMapping,
    _parse_header,
)

# Fixture directory + glob.  When empty (no contributor has submitted
# a dump yet), every test in this module skips with a clear reason.
_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / 'fixtures' / 'hwinfo'
)
_FIXTURES = sorted(_FIXTURE_DIR.glob('*.bin')) if _FIXTURE_DIR.is_dir() else []

# Categories produced by the HWiNFO SensorType → TRCC category mapping.
_VALID_CATEGORIES = frozenset({
    'temperature', 'voltage', 'fan', 'current', 'power', 'clock', 'usage',
})


pytestmark = pytest.mark.skipif(
    not _FIXTURES,
    reason=(
        "No HWiNFO fixtures yet — run `python dev/dump_hwinfo_shm.py` on "
        "Windows with HWiNFO64 + Shared Memory Support, then commit the "
        "resulting tests/fixtures/hwinfo/*.bin file."
    ),
)


@pytest.fixture(params=_FIXTURES, ids=lambda p: p.name)
def fixture_bytes(request: pytest.FixtureRequest) -> bytes:
    """Load one captured HWiNFO MMF dump as raw bytes."""
    return request.param.read_bytes()


def _make_source(data: bytes) -> HWiNFOSource:
    """Build a HWiNFOSource whose port reads from ``data`` (no Win32)."""
    return HWiNFOSource(mapping=_BytesMapping(data))


def _make_enum() -> MagicMock:
    """Minimal stub of WindowsSensorEnumerator — only the surface ``contribute()`` uses."""
    enum = MagicMock()
    enum._sensors = []
    enum._register_poll = MagicMock()
    return enum


# ── Header parser — pure, no source object needed ──────────────────────


class TestHeaderParsing:
    """The pure ``_parse_header`` decode contract."""

    def test_magic_matches(self, fixture_bytes: bytes) -> None:
        header = _parse_header(fixture_bytes)
        assert header.magic == _HWINFO_MAGIC, (
            f"fixture has wrong magic 0x{header.magic:08x}; "
            f"expected 0x{_HWINFO_MAGIC:08x}"
        )

    def test_section_offsets_within_file(self, fixture_bytes: bytes) -> None:
        header = _parse_header(fixture_bytes)
        file_size = len(fixture_bytes)
        sec_end = header.sec_off + header.sec_size * header.sec_count
        ent_end = header.ent_off + header.ent_size * header.ent_count
        assert sec_end <= file_size, (
            f"sensor section runs past end of fixture: {sec_end} > {file_size}"
        )
        assert ent_end <= file_size, (
            f"entry section runs past end of fixture: {ent_end} > {file_size}"
        )

    def test_total_size_is_high_water_mark(self, fixture_bytes: bytes) -> None:
        header = _parse_header(fixture_bytes)
        assert header.total_size <= len(fixture_bytes)
        # And it should equal the higher of the two section ends.
        assert header.total_size == max(
            header.sec_off + header.sec_size * header.sec_count,
            header.ent_off + header.ent_size * header.ent_count,
        )


# ── Probe ──────────────────────────────────────────────────────────────


class TestProbe:
    """``HWiNFOSource.probe()`` accepts well-formed fixtures."""

    def test_probe_succeeds_for_real_fixture(self, fixture_bytes: bytes) -> None:
        source = _make_source(fixture_bytes)
        try:
            assert source.probe() is True
        finally:
            source.stop()

    def test_probe_rejects_short_buffer(self) -> None:
        source = HWiNFOSource(mapping=_BytesMapping(b'\x00' * 8))
        assert source.probe() is False
        source.stop()

    def test_probe_rejects_wrong_magic(self, fixture_bytes: bytes) -> None:
        # Flip the first byte — magic check must fail.
        mutated = b'\x00' + fixture_bytes[1:]
        source = _make_source(mutated)
        assert source.probe() is False
        source.stop()


# ── Discovery (contribute) ─────────────────────────────────────────────


class TestContribute:
    """``HWiNFOSource.contribute()`` populates the enumerator with sensors."""

    def test_at_least_one_sensor_registered(self, fixture_bytes: bytes) -> None:
        source = _make_source(fixture_bytes)
        enum = _make_enum()
        try:
            assert source.probe()
            source.contribute(enum)
        finally:
            source.stop()
        assert len(enum._sensors) > 0, (
            "fixture had zero readings — parser is reading the wrong offsets "
            "or the HWiNFO format has changed"
        )

    def test_poll_callback_registered_once(self, fixture_bytes: bytes) -> None:
        source = _make_source(fixture_bytes)
        enum = _make_enum()
        try:
            source.probe()
            source.contribute(enum)
        finally:
            source.stop()
        enum._register_poll.assert_called_once()

    def test_every_sensor_has_valid_shape(self, fixture_bytes: bytes) -> None:
        source = _make_source(fixture_bytes)
        enum = _make_enum()
        try:
            source.probe()
            source.contribute(enum)
            for s in enum._sensors:
                assert s.source == 'hwinfo'
                assert s.category in _VALID_CATEGORIES, (
                    f"unexpected category {s.category!r} for sensor {s.id}"
                )
                assert s.name, f"empty name for sensor {s.id}"
                assert s.id.startswith('hwinfo:'), (
                    f"unexpected sid format {s.id!r}"
                )
                # Three colon-separated parts: 'hwinfo', sensor_index, entry_id.
                parts = s.id.split(':')
                assert len(parts) == 3, f"sid {s.id!r} has wrong shape"
                int(parts[1])  # raises if not a sensor_index integer
                int(parts[2])  # raises if not an entry_id integer
        finally:
            source.stop()

    def test_sensor_ids_unique(self, fixture_bytes: bytes) -> None:
        source = _make_source(fixture_bytes)
        enum = _make_enum()
        try:
            source.probe()
            source.contribute(enum)
            ids = [s.id for s in enum._sensors]
            assert len(ids) == len(set(ids)), (
                "duplicate sensor IDs registered — sensor_index/entry_id "
                "collision suggests a parser offset bug"
            )
        finally:
            source.stop()


# ── Polling ────────────────────────────────────────────────────────────


class TestPoll:
    """``HWiNFOSource.poll()`` returns finite floats for every registered sensor."""

    def test_poll_writes_finite_values_for_all_sensors(
        self, fixture_bytes: bytes,
    ) -> None:
        source = _make_source(fixture_bytes)
        enum = _make_enum()
        readings: dict[str, float] = {}
        try:
            source.probe()
            source.contribute(enum)
            source.poll(enum, readings)
            assert len(readings) == len(enum._sensors), (
                f"poll() wrote {len(readings)} readings for "
                f"{len(enum._sensors)} sensors — one or more reads short"
            )
            for sid, value in readings.items():
                assert isinstance(value, float), (
                    f"{sid} value is {type(value).__name__}, not float"
                )
                assert math.isfinite(value), (
                    f"{sid} value is non-finite ({value!r})"
                )
        finally:
            source.stop()

    def test_poll_is_deterministic_for_static_bytes(
        self, fixture_bytes: bytes,
    ) -> None:
        """Two polls over the same byte buffer yield identical readings.

        Fixtures are static snapshots — any difference means the parser
        depends on hidden state.
        """
        source = _make_source(fixture_bytes)
        enum = _make_enum()
        try:
            source.probe()
            source.contribute(enum)
            first: dict[str, float] = {}
            second: dict[str, float] = {}
            source.poll(enum, first)
            source.poll(enum, second)
            assert first == second, "poll() is not deterministic on static input"
        finally:
            source.stop()


# ── GPU enumeration hook ───────────────────────────────────────────────


class TestGpuList:
    """``HWiNFOSource.gpu_list()`` returns properly-shaped GPU entries."""

    def test_gpu_list_entries_have_hwinfo_key_prefix(
        self, fixture_bytes: bytes,
    ) -> None:
        source = _make_source(fixture_bytes)
        enum = _make_enum()
        try:
            source.probe()
            source.contribute(enum)
            for key, name in source.gpu_list():
                assert key.startswith('hwinfo:'), (
                    f"gpu_list key {key!r} missing hwinfo: prefix"
                )
                assert name, f"gpu_list entry {key!r} has empty display name"
        finally:
            source.stop()

"""macOS + BSD hardware-probe parsing tests.

Exercises ``_macos_memory_info`` / ``_macos_disk_info`` /
``_bsd_memory_info`` / ``_bsd_disk_info`` from the Linux dev box by
injecting canned profiler/sysctl/geom output through their DI seams.
"""
from __future__ import annotations

# ── macOS memory ──────────────────────────────────────────────────────


def _apple_silicon_profiler_payload() -> dict:
    return {
        "SPMemoryDataType": [
            {
                "dimm_manufacturer": "Apple",
                "dimm_part_number": "Unified",
                "dimm_type": "LPDDR5",
                "dimm_speed": "6400 MHz",
                "dimm_size": "16 GB",
                "dimm_form_factor": "Unified",
                "_name": "Apple Silicon",
            },
        ],
    }


def _intel_mac_profiler_payload() -> dict:
    return {
        "SPMemoryDataType": [
            {
                "_name": "BANK 0/DIMM0",
                "_items": [
                    {
                        "dimm_manufacturer": "Samsung",
                        "dimm_part_number": "M471A1K43DB1-CTD",
                        "dimm_type": "DDR4",
                        "dimm_speed": "2667 MHz",
                        "dimm_size": "8 GB",
                        "dimm_form_factor": "SODIMM",
                        "_name": "BANK 0/DIMM0",
                    },
                    {
                        "dimm_manufacturer": "Samsung",
                        "dimm_part_number": "M471A1K43DB1-CTD",
                        "dimm_type": "DDR4",
                        "dimm_speed": "2667 MHz",
                        "dimm_size": "8 GB",
                        "dimm_form_factor": "SODIMM",
                        "_name": "BANK 0/DIMM1",
                    },
                ],
            },
        ],
    }


def test_macos_memory_apple_silicon_unified_slot() -> None:
    from trcc.adapters.system.macos import _macos_memory_info

    payload = _apple_silicon_profiler_payload()
    slots = _macos_memory_info(runner=lambda _: payload)
    assert len(slots) == 1
    assert slots[0]["manufacturer"] == "Apple"
    assert slots[0]["type"] == "LPDDR5"
    assert slots[0]["size"] == "16 GB"
    assert slots[0]["form_factor"] == "Unified"


def test_macos_memory_intel_mac_two_dimms() -> None:
    from trcc.adapters.system.macos import _macos_memory_info

    payload = _intel_mac_profiler_payload()
    slots = _macos_memory_info(runner=lambda _: payload)
    assert len(slots) == 2
    assert all(s["type"] == "DDR4" for s in slots)
    assert {s["locator"] for s in slots} == {"BANK 0/DIMM0", "BANK 0/DIMM1"}


def test_macos_memory_falls_back_to_psutil_when_profiler_empty() -> None:
    """No SPMemoryDataType entries → single ``Total`` row from psutil."""
    from trcc.adapters.system.macos import _macos_memory_info

    slots = _macos_memory_info(runner=lambda _: {})
    assert len(slots) == 1
    assert slots[0]["locator"] == "Total"
    assert slots[0]["size"].endswith(" GB")


def test_macos_memory_skips_dimm_without_size() -> None:
    """Empty-slot DIMMs (no `dimm_size`) shouldn't render as empty rows."""
    from trcc.adapters.system.macos import _macos_memory_info

    payload = {
        "SPMemoryDataType": [
            {"dimm_size": "", "_name": "Empty Slot"},
        ],
    }
    slots = _macos_memory_info(runner=lambda _: payload)
    # falls through to psutil
    assert len(slots) == 1
    assert slots[0]["locator"] == "Total"


def test_macos_memory_handles_malformed_payload() -> None:
    """Non-dict items shouldn't crash; treated as no data → psutil fallback."""
    from trcc.adapters.system.macos import _macos_memory_info

    payload = {"SPMemoryDataType": [None, "not-a-dict", 42]}
    slots = _macos_memory_info(runner=lambda _: payload)
    assert len(slots) == 1
    assert slots[0]["locator"] == "Total"


# ── macOS disks ──────────────────────────────────────────────────────


def _macbook_storage_payload() -> dict:
    return {
        "SPStorageDataType": [
            {
                "bsd_name": "disk0",
                "size_in_bytes": str(512 * 1024 ** 3),
                "smart_status": "Verified",
                "physical_drive": {
                    "device_name": "APPLE SSD AP0512",
                    "medium_type": "Solid State",
                },
            },
            {
                "bsd_name": "disk2",
                "size_in_bytes": str(2 * 1024 ** 4),
                "smart_status": "Verified",
                "physical_drive": {
                    "device_name": "External HDD",
                    "medium_type": "Rotational",
                },
            },
        ],
    }


def test_macos_disk_parses_ssd_and_hdd() -> None:
    from trcc.adapters.system.macos import _macos_disk_info

    payload = _macbook_storage_payload()
    disks = _macos_disk_info(runner=lambda _: payload)
    assert len(disks) == 2
    by_name = {d["name"]: d for d in disks}
    assert by_name["disk0"]["type"] == "SSD"
    assert by_name["disk0"]["size"] == "512 GB"
    assert by_name["disk2"]["type"] == "HDD"
    assert by_name["disk2"]["size"] == "2.0 TB"


def test_macos_disk_defaults_to_ssd_for_unknown_medium() -> None:
    """Modern Macs are SSD-only — unknown medium_type → SSD."""
    from trcc.adapters.system.macos import _macos_disk_info

    payload = {
        "SPStorageDataType": [
            {
                "bsd_name": "disk0",
                "size_in_bytes": "0",
                "physical_drive": {"device_name": "?", "medium_type": ""},
            },
        ],
    }
    disks = _macos_disk_info(runner=lambda _: payload)
    assert disks[0]["type"] == "SSD"


def test_macos_disk_skips_entries_without_name_or_model() -> None:
    from trcc.adapters.system.macos import _macos_disk_info

    payload = {
        "SPStorageDataType": [
            {"bsd_name": "", "physical_drive": {}},
            {"bsd_name": "disk1", "physical_drive": {"device_name": "Drive"}},
        ],
    }
    disks = _macos_disk_info(runner=lambda _: payload)
    assert [d["name"] for d in disks] == ["disk1"]


def test_macos_disk_empty_on_missing_data() -> None:
    from trcc.adapters.system.macos import _macos_disk_info

    assert _macos_disk_info(runner=lambda _: {}) == []


# ── BSD memory ───────────────────────────────────────────────────────


def test_bsd_memory_total_from_hw_physmem() -> None:
    from trcc.adapters.system.bsd import _bsd_memory_info

    total = 16 * 1024 ** 3
    slots = _bsd_memory_info(runner=lambda _: str(total))
    assert len(slots) == 1
    assert slots[0]["locator"] == "Total"
    assert slots[0]["size"] == "16 GB"


def test_bsd_memory_falls_back_to_psutil_when_sysctl_fails() -> None:
    from trcc.adapters.system.bsd import _bsd_memory_info

    slots = _bsd_memory_info(runner=lambda _: None)
    assert len(slots) == 1
    assert slots[0]["locator"] == "Total"
    assert slots[0]["size"].endswith(" GB")


def test_bsd_memory_falls_back_on_garbage_physmem() -> None:
    from trcc.adapters.system.bsd import _bsd_memory_info

    slots = _bsd_memory_info(runner=lambda _: "not_a_number")
    assert len(slots) == 1
    assert slots[0]["locator"] == "Total"


# ── BSD disks ────────────────────────────────────────────────────────


_GEOM_OUTPUT = """\
Geom name: ada0
Providers:
1. Name: ada0
   Mediasize: 500107862016 (466G)
   Sectorsize: 512
   descr: Samsung SSD 850 EVO 500GB
   ident: S2RBNX0J123456
   rotationrate: 0
   fwsectors: 63
   fwheads: 16
Geom name: ada1
Providers:
1. Name: ada1
   Mediasize: 2000398934016 (1.8T)
   descr: WDC WD20EZRZ-00Z5HB0
   rotationrate: 5400
"""


def test_bsd_disk_parses_geom_output() -> None:
    from trcc.adapters.system.bsd import _bsd_disk_info

    disks = _bsd_disk_info(runner=lambda: _GEOM_OUTPUT)
    assert len(disks) == 2
    by_name = {d["name"]: d for d in disks}
    assert by_name["ada0"]["model"] == "Samsung SSD 850 EVO 500GB"
    assert by_name["ada0"]["type"] == "SSD"
    assert by_name["ada0"]["size"] == "466G"
    assert by_name["ada1"]["type"] == "HDD"
    assert by_name["ada1"]["size"] == "1.8T"


def test_bsd_disk_empty_when_geom_unavailable() -> None:
    """OpenBSD / NetBSD don't ship geom — runner returns "" → no disks."""
    from trcc.adapters.system.bsd import _bsd_disk_info

    assert _bsd_disk_info(runner=lambda: "") == []


def test_bsd_disk_defaults_unknown_health() -> None:
    from trcc.adapters.system.bsd import _bsd_disk_info

    minimal = "Geom name: ada0\n   descr: Test\n"
    disks = _bsd_disk_info(runner=lambda: minimal)
    assert disks[0]["health"] == "Unknown"

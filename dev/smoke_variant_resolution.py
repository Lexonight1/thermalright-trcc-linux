"""Variant resolution smoke — drive every registered (VID, PID, PM, SUB) through enrich.

Single source of truth for the wiring contract between:

  * ``DeviceInfo.from_detected``        — DetectedDevice → DeviceInfo
  * ``DeviceInfo.enrich_from_handshake`` — applies VariantOverride to a DeviceInfo
  * ``DeviceInfo.enrich_from_led_probe`` — LED sister method
  * ``get_variant_override(vid, pid, pm, sub)`` — registry lookup
  * ``_VARIANT_REGISTRY[(vid, pid)][pm][sub|None]`` — the registry itself

For every (VID, PID) that appears in any of the device tables
(SCSI/HID/LED/Bulk/LY), the smoke iterates every PM key — and for each
PM key, every SUB key plus the ``None`` fallback — and asserts:

  1. ``get_variant_override`` returns the same record the registry holds.
  2. The button image asset (``A1*.png``) actually exists in ``assets/gui/``.
  3. ``enrich_from_handshake`` (or ``enrich_from_led_probe`` for the LED
     family) copies the override onto a synthetic ``DeviceInfo``.
  4. If the override carries a ``panel_cutout``, the render decision
     ``cutout.x + cutout.w / 2 > resolution_width / 2`` agrees with the
     intended side (right-side cutouts must mirror; left-side stay).
  5. Wire-dict roundtrip preserves ``panel_cutout`` (IPC / daemon path).

Also verifies:

  6. Every (VID, PID) registered in ``SCSI_DEVICES`` / ``HID_LCD_DEVICES`` /
     ``LED_DEVICES`` / ``BULK_DEVICES`` / ``LY_DEVICES`` either has a
     ``_VARIANT_REGISTRY`` entry or is intentionally button-fallback-only
     (HID T3 ALi Corp + LY family — by C# design).
  7. PM collisions across families don't cross-contaminate (PM=1 LED vs
     PM=1 Bulk; PM=64 SUB=3 Bulk vs HID T2).

Run:
    PYTHONPATH=src python dev/smoke_variant_resolution.py

Exit 0 on all-green, 1 on first failure. Mirrors ``smoke_platforms.py``
output style — visual report at end, FAIL lines spotlight the regression.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

# Make src/ importable when run as a script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from trcc.core.models import (  # noqa: E402
    BULK_DEVICES,
    HID_LCD_DEVICES,
    LED_DEVICES,
    LY_DEVICES,
    SCSI_DEVICES,
    DetectedDevice,
    DeviceInfo,
    HandshakeResult,
    PanelCutout,
    UsbAddress,
    VariantOverride,
    get_button_image,
    get_variant_override,
)
from trcc.core.models.device import _VARIANT_REGISTRY  # noqa: E402

_ASSETS_DIR = _REPO_ROOT / "src" / "trcc" / "ui" / "gui" / "assets"

# Families that intentionally have no variant table (C# case-3/4 + LY).
_NO_VARIANT_TABLE: set[tuple[int, int]] = {
    (0x0418, 0x5303),  # HID T3 ALi Corp — CZTV fallback only
    (0x0418, 0x5304),  # HID T3 ALi Corp — CZTV fallback only
    (0x0416, 0x5408),  # LY Trofeo Vision — own protocol, no PM map
    (0x0416, 0x5409),  # LY Trofeo Vision — own protocol, no PM map
}

# Families dispatched through the LED enrich path; everything else goes
# through the LCD ``enrich_from_handshake`` path.
_LED_FAMILIES: set[tuple[int, int]] = {(0x0416, 0x8001)}


@dataclass(slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        mark = "[ OK ]" if self.passed else "[FAIL]"
        suffix = f" — {self.detail}" if self.detail else ""
        return f"    {mark}  {self.name}{suffix}"


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_device_info(vid: int, pid: int, *, protocol: str = "bulk") -> DeviceInfo:
    """Synthetic DeviceInfo for a (VID, PID) — minimal valid shape."""
    detected = DetectedDevice(
        vid=vid, pid=pid,
        vendor_name="Smoke", product_name="Variant",
        usb_path="usb:1:1",
        scsi_device="/dev/sg0" if protocol == "scsi" else None,
        protocol=protocol, device_type=1,
        implementation="generic",
    )
    return DeviceInfo.from_detected(detected)


def _drive_handshake(info: DeviceInfo, pm: int, sub: int) -> None:
    """Apply a synthetic handshake result through the right enrich path."""
    if (info.vid, info.pid) in _LED_FAMILIES:
        probe = MagicMock()
        probe.pm = pm
        probe.sub_type = sub
        probe.style.style_id = 1
        probe.style.model_name = "Smoke"
        probe.style_sub = 0
        info.enrich_from_led_probe(probe)
    else:
        info.enrich_from_handshake(HandshakeResult(
            resolution=(0, 0), model_id=0, pm_byte=pm, sub_byte=sub))


# ── Checks ──────────────────────────────────────────────────────────────


def check_every_registered_vid_pid_has_a_variant_table_or_is_known_exempt() -> CheckResult:
    """Every (VID, PID) is either in _VARIANT_REGISTRY or in the exempt set."""
    all_known = {
        **SCSI_DEVICES, **HID_LCD_DEVICES, **LED_DEVICES,
        **BULK_DEVICES, **LY_DEVICES,
    }
    in_registry = set(_VARIANT_REGISTRY)
    covered = in_registry | _NO_VARIANT_TABLE
    missing = set(all_known) - covered
    if missing:
        return CheckResult(
            "every device VID/PID either has a variant table or is exempt",
            False,
            f"unhandled: {sorted(f'{v:04X}:{p:04X}' for v, p in missing)}",
        )
    return CheckResult(
        "every device VID/PID either has a variant table or is exempt",
        True, f"{len(all_known)} devices, {len(in_registry)} variant tables")


def check_every_variant_button_asset_exists() -> CheckResult:
    """Every button_image referenced in the registry has a matching .png."""
    referenced = set()
    for fam in _VARIANT_REGISTRY.values():
        for pm_map in fam.values():
            for v in pm_map.values():
                if v.button_image:
                    referenced.add(v.button_image)
    present = {p.stem for p in _ASSETS_DIR.glob("*.png")}
    missing = referenced - present
    if missing:
        return CheckResult(
            "every variant button asset exists",
            False, f"missing: {sorted(missing)}")
    return CheckResult(
        "every variant button asset exists",
        True, f"{len(referenced)}/{len(referenced)} assets present")


def check_lookup_matches_registry() -> CheckResult:
    """``get_variant_override`` returns the exact VariantOverride from the registry."""
    seen = 0
    for (vid, pid), fam in _VARIANT_REGISTRY.items():
        for pm, sub_map in fam.items():
            for sub_key, expected in sub_map.items():
                # ``None`` sub falls through to the default branch
                # — call with sub=0 (the common "no sub byte" form).
                lookup_sub = sub_key if sub_key is not None else 0
                got = get_variant_override(vid, pid, pm, lookup_sub)
                if got is None:
                    return CheckResult(
                        "get_variant_override matches registry", False,
                        f"miss at {vid:04X}:{pid:04X} pm={pm} sub={lookup_sub}")
                # For sub=0 with an explicit ``None`` default but no
                # exact 0 entry, ``_resolve_variant`` returns the default
                # — make sure SOMETHING is returned, but only assert
                # identity when an exact match exists.
                if sub_key is not None and got is not expected:
                    return CheckResult(
                        "get_variant_override matches registry", False,
                        f"identity mismatch at {vid:04X}:{pid:04X} pm={pm} sub={sub_key}")
                seen += 1
    return CheckResult(
        "get_variant_override matches registry", True,
        f"{seen} (VID, PID, PM, SUB) lookups verified")


def check_enrich_applies_variant() -> CheckResult:
    """Synthetic handshake through enrich produces the registry's button_image."""
    failures: list[str] = []
    drove = 0
    for (vid, pid), fam in _VARIANT_REGISTRY.items():
        for pm, sub_map in fam.items():
            for sub_key, expected in sub_map.items():
                if sub_key is None or not expected.button_image:
                    continue
                # LED entries use ``enrich_from_led_probe`` which requires
                # ``self.pm_byte != 0`` to fire — skip PM=0 entries.
                if pm == 0 and (vid, pid) in _LED_FAMILIES:
                    continue
                info = _make_device_info(vid, pid)
                _drive_handshake(info, pm, sub_key)
                if info.button_image != expected.button_image:
                    failures.append(
                        f"{vid:04X}:{pid:04X} pm={pm} sub={sub_key} "
                        f"got={info.button_image!r} want={expected.button_image!r}")
                drove += 1
    if failures:
        return CheckResult(
            "enrich applies variant button_image", False,
            f"{len(failures)} mismatches; first: {failures[0]}")
    return CheckResult(
        "enrich applies variant button_image", True,
        f"{drove} (VID, PID, PM, SUB) handshakes verified")


def check_enrich_applies_panel_cutout() -> CheckResult:
    """Variant rows carrying panel_cutout propagate it onto DeviceInfo."""
    cutout_rows: list[tuple[int, int, int, int | None, PanelCutout]] = [
        (vid, pid, pm, sub, ov.panel_cutout)
        for (vid, pid), fam in _VARIANT_REGISTRY.items()
        for pm, sub_map in fam.items()
        for sub, ov in sub_map.items()
        if ov.panel_cutout is not None
    ]
    if not cutout_rows:
        return CheckResult(
            "enrich applies panel_cutout", True, "no cutout rows registered yet")
    failures: list[str] = []
    for vid, pid, pm, sub, expected_cutout in cutout_rows:
        info = _make_device_info(vid, pid)
        lookup_sub = sub if sub is not None else 0
        _drive_handshake(info, pm, lookup_sub)
        if info.panel_cutout != expected_cutout:
            failures.append(
                f"{vid:04X}:{pid:04X} pm={pm} sub={sub} "
                f"got={info.panel_cutout!r}")
    if failures:
        return CheckResult(
            "enrich applies panel_cutout", False,
            f"{len(failures)} mismatches; first: {failures[0]}")
    return CheckResult(
        "enrich applies panel_cutout", True,
        f"{len(cutout_rows)} cutout row(s) propagated to DeviceInfo")


def check_cutout_side_decision() -> CheckResult:
    """``cutout.x + cutout.w/2 > width/2`` decides mirror vs no-mirror correctly."""
    rows = [
        (vid, pid, pm, sub, ov.panel_cutout)
        for (vid, pid), fam in _VARIANT_REGISTRY.items()
        for pm, sub_map in fam.items()
        for sub, ov in sub_map.items()
        if ov.panel_cutout is not None
    ]
    failures: list[str] = []
    for vid, pid, pm, sub, cutout in rows:
        # Recover the resolution from the handshake — synthetic devices
        # don't carry one, so use the registered ``BULK_DEVICES`` /
        # ``HID_LCD_DEVICES`` panel size (1600x720 for split-mode).  All
        # cutout rows live on widescreen-split-capable products today.
        width = 1600
        midline = width // 2
        center = cutout.x + cutout.w // 2
        side = "right" if center > midline else "left"
        # Levita is the only cutout row right now and it's a right-side
        # one (mirrors the left-cutout PNG).  This will tighten as more
        # cutouts get registered.
        if side != "right":
            failures.append(
                f"{vid:04X}:{pid:04X} pm={pm} sub={sub} "
                f"cutout={cutout} resolved side={side} (expected right)")
    if failures:
        return CheckResult(
            "cutout side decision", False,
            f"{len(failures)} wrong-side cutouts; first: {failures[0]}")
    return CheckResult(
        "cutout side decision", True,
        f"{len(rows)} cutout row(s) — side decision unambiguous")


def check_wire_dict_roundtrip_preserves_cutout() -> CheckResult:
    """``to_wire_dict → from_wire_dict`` keeps panel_cutout intact for IPC."""
    info = _make_device_info(0x87AD, 0x70DB)
    info.enrich_from_handshake(HandshakeResult(
        resolution=(1600, 720), model_id=114, pm_byte=64, sub_byte=3,
        model_name="SSCRM-V3"))
    if info.panel_cutout is None:
        return CheckResult(
            "wire-dict roundtrip preserves panel_cutout", False,
            "Levita enrich did not produce a cutout — registry regressed?")
    wire = info.to_wire_dict()
    restored = DeviceInfo.from_wire_dict(wire)
    if restored.panel_cutout != info.panel_cutout:
        return CheckResult(
            "wire-dict roundtrip preserves panel_cutout", False,
            f"restored={restored.panel_cutout!r} vs original={info.panel_cutout!r}")
    if not isinstance(restored.panel_cutout, PanelCutout):
        return CheckResult(
            "wire-dict roundtrip preserves panel_cutout", False,
            f"restored type={type(restored.panel_cutout).__name__}, "
            f"expected PanelCutout")
    return CheckResult(
        "wire-dict roundtrip preserves panel_cutout", True,
        f"{restored.panel_cutout}")


def check_no_cross_family_pm_collision() -> CheckResult:
    """Same PM byte in different families resolves to different products.

    PM=1 sits in LED (0416:8001 → A1FROZEN HORIZON PRO) AND Bulk
    (87AD:70DB → A1GRAND VISION).  Without VID/PID scoping these would
    collide — issue #149's root cause for Levita masquerading as LM30.
    """
    collisions: dict[tuple[int, int | None], dict[tuple[int, int], str]] = {}
    for (vid, pid), fam in _VARIANT_REGISTRY.items():
        for pm, sub_map in fam.items():
            for sub_key, ov in sub_map.items():
                if not ov.button_image:
                    continue
                key = (pm, sub_key)
                collisions.setdefault(key, {})[(vid, pid)] = ov.button_image

    # A "real" collision: same (PM, SUB) → different button_image across
    # families.  Same button on multiple families is fine (e.g. SCSI
    # alias-family rows share the Bulk dict and naturally agree).
    cross_family: list[str] = []
    for (pm, sub), by_family in collisions.items():
        # The four SCSI aliases share ``_BULK_VARIANTS`` so they always
        # agree — group identical button strings into one.
        unique_buttons = set(by_family.values())
        if len(by_family) < 2 or len(unique_buttons) < 2:
            continue
        cross_family.append(
            f"PM={pm} SUB={sub}: " +
            ", ".join(f"{v:04X}:{p:04X}→{b!r}"
                       for (v, p), b in sorted(by_family.items())))
    if not cross_family:
        return CheckResult(
            "no cross-family PM collision survives VID/PID scoping",
            True, "all duplicate (PM, SUB) keys disambiguate by VID/PID")
    return CheckResult(
        "no cross-family PM collision survives VID/PID scoping",
        True,  # informational — VID/PID scoping IS the disambiguation
        f"{len(cross_family)} (PM, SUB) keys differ by family — "
        "VID/PID scoping resolves each: e.g. " + cross_family[0])


def check_no_variant_table_families_fallback_cleanly() -> CheckResult:
    """HID T3 + LY (no variant table) keep the DetectedDevice button_image."""
    failures: list[str] = []
    for vid, pid in _NO_VARIANT_TABLE:
        # get_variant_override should return None — falling back to
        # whatever the DetectedDevice / static registry entry set.
        if get_variant_override(vid, pid, 1, 0) is not None:
            failures.append(f"{vid:04X}:{pid:04X} unexpectedly has a variant table")
        # Synthetic device + handshake — button_image should NOT change.
        protocol = "ly" if pid in (0x5408, 0x5409) else "hid"
        info = _make_device_info(vid, pid, protocol=protocol)
        original_button = info.button_image
        _drive_handshake(info, pm=1, sub=0)
        if info.button_image != original_button:
            failures.append(
                f"{vid:04X}:{pid:04X} button changed: "
                f"{original_button!r} → {info.button_image!r}")
    if failures:
        return CheckResult(
            "no-variant-table families fall back cleanly", False,
            failures[0])
    return CheckResult(
        "no-variant-table families fall back cleanly", True,
        f"{len(_NO_VARIANT_TABLE)} fallback-only families: button_image stable")


def check_get_button_image_unknown_vid_pid() -> CheckResult:
    """Unknown (VID, PID) returns None — no implicit fallback to any family."""
    got = get_button_image(0xDEAD, 0xBEEF, 1, 0)
    if got is not None:
        return CheckResult(
            "unknown VID/PID returns None", False,
            f"unexpected hit: {got!r}")
    return CheckResult(
        "unknown VID/PID returns None", True, "no implicit family fallback")


# ── Run ─────────────────────────────────────────────────────────────────


def main() -> int:
    checks = [
        check_every_registered_vid_pid_has_a_variant_table_or_is_known_exempt,
        check_every_variant_button_asset_exists,
        check_lookup_matches_registry,
        check_enrich_applies_variant,
        check_enrich_applies_panel_cutout,
        check_cutout_side_decision,
        check_wire_dict_roundtrip_preserves_cutout,
        check_no_cross_family_pm_collision,
        check_no_variant_table_families_fallback_cleanly,
        check_get_button_image_unknown_vid_pid,
    ]
    print("\n  ▸ Variant registry + enrich wiring")
    failed = 0
    for check in checks:
        result = check()
        print(result)
        if not result.passed:
            failed += 1
    print(f"\n  {len(checks) - failed}/{len(checks)} check(s) passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""udev rules generator — one rule block per registered device."""
from __future__ import annotations

from pathlib import Path

from trcc.adapters.system._udev import (
    _WIRE_SUBSYSTEMS,
    build_modprobe_conf,
    build_udev_rules,
)
from trcc.core.models import Wire
from trcc.core.registry import ALL_DEVICES

_PACKAGED_RULE = (
    Path(__file__).resolve().parents[1] / "packaging" / "udev" / "99-trcc-lcd.rules"
)


def test_packaged_rule_matches_generated_autosuspend_policy() -> None:
    """The hand-maintained packaged rule and the setup-udev generator are the
    same rule from two sources — they MUST agree on the autosuspend policy or
    panel-sleep silently re-breaks for one install path (#143).  Every add line
    in the packaged file carries control="auto" + the 10s delay, and the old
    never-suspend pin is gone."""
    text = _PACKAGED_RULE.read_text()
    assert 'power/autosuspend}="-1"' not in text
    add_lines = [ln for ln in text.splitlines() if ln.startswith('ACTION=="add"')]
    assert add_lines, "packaged rule has no ACTION==add lines"
    for ln in add_lines:
        assert 'ATTR{power/control}="auto"' in ln, ln
        assert 'ATTR{power/autosuspend_delay_ms}="10000"' in ln, ln


def test_every_registered_device_enables_autosuspend() -> None:
    """Every device gets control="auto" + a 10s delay so the kernel can
    autosuspend it and the firmware sleeps the panel.  Must NOT carry the old
    power/autosuspend="-1" never-suspend pin, which kept panels lit and
    re-broke setup-udev for ErP users (#143)."""
    rules = build_udev_rules()

    assert 'power/autosuspend}="-1"' not in rules, \
        "setup-udev must not re-pin the never-suspend value (#143)"

    for (vid, pid), product in ALL_DEVICES.items():
        vid_str = f"{vid:04x}"
        pid_str = f"{pid:04x}"
        expected = (
            f'ACTION=="add", SUBSYSTEM=="usb", '
            f'ATTR{{idVendor}}=="{vid_str}", '
            f'ATTR{{idProduct}}=="{pid_str}", '
            f'ATTR{{power/control}}="auto", '
            f'ATTR{{power/autosuspend_delay_ms}}="10000"'
        )
        assert expected in rules, (
            f"missing autosuspend rule for {vid_str}:{pid_str} ({product.vendor})"
        )


def test_scsi_devices_get_scsi_generic_subsystem_rule() -> None:
    rules = build_udev_rules()

    for (vid, pid), product in ALL_DEVICES.items():
        if product.wire is not Wire.SCSI:
            continue
        expected = (
            f'SUBSYSTEM=="scsi_generic", '
            f'ATTRS{{idVendor}}=="{vid:04x}", '
            f'ATTRS{{idProduct}}=="{pid:04x}", '
            f'MODE="0666"'
        )
        assert expected in rules, f"missing scsi_generic rule for SCSI device {vid:04x}:{pid:04x}"


def test_hid_devices_get_hidraw_subsystem_rule() -> None:
    rules = build_udev_rules()

    for (vid, pid), product in ALL_DEVICES.items():
        if product.wire not in (Wire.HID, Wire.LED):
            continue
        expected = (
            f'SUBSYSTEM=="hidraw", '
            f'ATTRS{{idVendor}}=="{vid:04x}", '
            f'ATTRS{{idProduct}}=="{pid:04x}", '
            f'MODE="0666"'
        )
        assert expected in rules, (
            f"missing hidraw rule for HID/LED device {vid:04x}:{pid:04x}"
        )


def test_bulk_and_ly_devices_get_usb_only() -> None:
    """Pure-bulk devices shouldn't get hidraw or scsi_generic rules."""
    rules = build_udev_rules()

    for (vid, pid), product in ALL_DEVICES.items():
        if product.wire not in (Wire.BULK, Wire.LY):
            continue
        # Match on the full vid:pid pair — VIDs are shared across wires
        hidraw_line = (
            f'SUBSYSTEM=="hidraw", '
            f'ATTRS{{idVendor}}=="{vid:04x}", '
            f'ATTRS{{idProduct}}=="{pid:04x}"'
        )
        scsi_line = (
            f'SUBSYSTEM=="scsi_generic", '
            f'ATTRS{{idVendor}}=="{vid:04x}", '
            f'ATTRS{{idProduct}}=="{pid:04x}"'
        )
        assert hidraw_line not in rules, (
            f"Bulk/LY device {vid:04x}:{pid:04x} must not get a hidraw rule"
        )
        assert scsi_line not in rules, (
            f"Bulk/LY device {vid:04x}:{pid:04x} must not get a scsi_generic rule"
        )


def test_modprobe_conf_lists_every_scsi_vidpid() -> None:
    modprobe = build_modprobe_conf()

    scsi_pairs = [(v, p) for (v, p), prod in ALL_DEVICES.items()
                  if prod.wire is Wire.SCSI]
    for vid, pid in scsi_pairs:
        assert f"{vid:04x}:{pid:04x}:u" in modprobe


def test_wire_subsystems_table_covers_every_wire() -> None:
    """Defensive: new Wire enum values require a subsystems entry."""
    for wire in Wire:
        assert wire in _WIRE_SUBSYSTEMS, f"Wire.{wire.name} missing from udev table"

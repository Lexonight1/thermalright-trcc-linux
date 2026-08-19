"""USB runtime-power reporting — a SUSPENDED panel must be distinguishable.

#150 cost two months partly because a suspended panel and a dead one look
identical in every log we have: both are just ``[Errno 110] Operation timed
out``.  Nothing in TRCC has ever read the kernel's runtime-PM view.

The ``supports_remote_wakeup`` field is the discriminator that ended the
guessing: measured on the dev panel (0402:3922, config bmAttributes 0x80 →
bit 5 clear), the device cannot wake the host, so the kernel never
autosuspends it whatever ``power/control`` says — which is why this bench can
never reproduce the HID reporter's failure, and why that is a hardware fact
rather than an excuse.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.core.models import UsbPowerState


def _state(**over) -> UsbPowerState:
    base = dict(control="auto", runtime_status="active",
                autosuspend_delay_ms=10000, suspended_time_ms=0,
                supports_remote_wakeup=True)
    base.update(over)
    return UsbPowerState(**base)  # type: ignore[arg-type]


# ── the DTO's two derived facts ───────────────────────────────────────

def test_suspended_is_true_only_when_the_kernel_says_so() -> None:
    assert _state(runtime_status="suspended").suspended is True
    assert _state(runtime_status="active").suspended is False
    assert _state(runtime_status="").suspended is False


def test_may_autosuspend_needs_both_policy_and_capability() -> None:
    """control=auto alone is not enough — the device must be able to wake.

    This is the whole #150 discriminator. A panel with control=auto but no
    remote wakeup NEVER suspends, so it cannot exhibit the bug.
    """
    assert _state(control="auto", supports_remote_wakeup=True).may_autosuspend is True
    # the dev panel: policy allows it, hardware cannot -> never suspends
    assert _state(control="auto", supports_remote_wakeup=False).may_autosuspend is False
    # policy forbids it
    assert _state(control="on", supports_remote_wakeup=True).may_autosuspend is False


# ── the Platform port's honest default ────────────────────────────────

def test_non_linux_platforms_return_none_not_a_lie(tmp_path: Path) -> None:
    """Only Linux exposes runtime PM. A stub inventing a value would mislead
    exactly the debugging this exists to serve, so the default is None."""
    from tests.mock_platform import MockPlatform
    assert MockPlatform([], tmp_path).usb_power_state(0x0402, 0x3922) is None


# ── the Linux sysfs reader ────────────────────────────────────────────

def _fake_sysfs(root: Path, *, vid="0402", pid="3922", control="auto",
                status="suspended", bm="0xa0", delay="10000", susp="4200") -> Path:
    dev = root / "1-13.4"
    (dev / "power").mkdir(parents=True)
    (dev / "idVendor").write_text(f"{vid}\n")
    (dev / "idProduct").write_text(f"{pid}\n")
    (dev / "bmAttributes").write_text(f"{bm}\n")
    (dev / "power" / "control").write_text(f"{control}\n")
    (dev / "power" / "runtime_status").write_text(f"{status}\n")
    (dev / "power" / "autosuspend_delay_ms").write_text(f"{delay}\n")
    (dev / "power" / "runtime_suspended_time").write_text(f"{susp}\n")
    return dev


@pytest.fixture
def linux_platform():
    from trcc.adapters.system.linux import LinuxOS
    return LinuxOS()


def test_linux_reads_a_suspended_panel(
    linux_platform, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The state armangido's panel is in when `display play` times out."""
    _fake_sysfs(tmp_path, control="auto", status="suspended", bm="0xa0")
    monkeypatch.setattr("trcc.adapters.system.linux.Path", _rooted(tmp_path))
    state = linux_platform.usb_power_state(0x0402, 0x3922)
    assert state is not None
    assert state.suspended is True
    assert state.control == "auto"
    assert state.supports_remote_wakeup is True      # 0xa0 -> bit 5 set
    assert state.may_autosuspend is True
    assert state.suspended_time_ms == 4200


def test_linux_reads_the_dev_panel_that_can_never_suspend(
    linux_platform, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bmAttributes 0x80 -> bit 5 clear -> no remote wakeup.

    Measured on the real dev panel. Even at control=auto it stayed `active`
    for 40s with nothing streaming — which is why #150 is reporter-gated.
    """
    _fake_sysfs(tmp_path, control="auto", status="active", bm="0x80", susp="0")
    monkeypatch.setattr("trcc.adapters.system.linux.Path", _rooted(tmp_path))
    state = linux_platform.usb_power_state(0x0402, 0x3922)
    assert state is not None
    assert state.supports_remote_wakeup is False
    assert state.may_autosuspend is False, (
        "a panel that cannot wake the host must never be reported as "
        "autosuspendable — that misread is what sent me hunting a repro that "
        "this hardware cannot produce"
    )


def test_linux_returns_none_for_an_absent_device(
    linux_platform, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_sysfs(tmp_path, vid="dead", pid="beef")
    monkeypatch.setattr("trcc.adapters.system.linux.Path", _rooted(tmp_path))
    assert linux_platform.usb_power_state(0x0402, 0x3922) is None


def test_linux_survives_unreadable_sysfs(
    linux_platform, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Diagnostics must never be the thing that crashes the diagnostic."""
    dev = tmp_path / "1-13.4"
    dev.mkdir()
    (dev / "idVendor").write_text("0402\n")
    (dev / "idProduct").write_text("3922\n")      # no power/ dir, no bmAttributes
    monkeypatch.setattr("trcc.adapters.system.linux.Path", _rooted(tmp_path))
    state = linux_platform.usb_power_state(0x0402, 0x3922)
    assert state is not None
    assert state.control == "" and state.runtime_status == ""
    assert state.supports_remote_wakeup is False


def test_linux_returns_none_without_sysfs(
    linux_platform, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("trcc.adapters.system.linux.Path", _rooted(tmp_path / "nope"))
    assert linux_platform.usb_power_state(0x0402, 0x3922) is None


def _rooted(root: Path):
    """Redirect only /sys/bus/usb/devices; every other Path stays real."""
    real = Path

    def _factory(*args, **kwargs):
        if args and str(args[0]) == "/sys/bus/usb/devices":
            return real(root)
        return real(*args, **kwargs)
    return _factory


# ── it reaches the report, which is the entire point ──────────────────

def test_the_report_shows_usb_power_for_each_device(tmp_path: Path) -> None:
    """Had this line existed in May, #150 would have been diagnosable at once."""
    from trcc.adapters.diagnostics.debug_report import _render_devices
    out = _render_devices(
        [{"key": "0416:5302", "product": "USBDISPLAY", "wire": "hid",
          "power": "control=auto status=suspended remote_wakeup=yes "
                   "suspended_for=4200ms"}],
        error="",
    )
    assert "usb power: control=auto status=suspended" in out
    assert "remote_wakeup=yes" in out

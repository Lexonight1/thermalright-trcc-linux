"""Guards for the SELinux policy installer (``adapters/system/_selinux``).

The build+load path needs an enforcing SELinux system to verify end-to-end (a
Fedora VM), but the *guards* — no-op off SELinux, skip when already loaded, and
dry-run — are pure logic and must never touch the host.  Everything here mocks
``_selinux_active`` / ``subprocess`` so it's safe to run on an SELinux box too.
"""
from __future__ import annotations

import pytest

from trcc.adapters.system import _selinux


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if anything tries to shell out (build/load the policy)."""
    def _boom(*a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError(f"unexpected subprocess call: {a!r}")
    monkeypatch.setattr(_selinux.subprocess, "run", _boom)


def test_noop_when_selinux_inactive(
    monkeypatch: pytest.MonkeyPatch, no_subprocess: None,
) -> None:
    """Off SELinux (no /sys/fs/selinux or no semodule) → clean no-op, no shelling."""
    monkeypatch.setattr(_selinux, "_selinux_active", lambda: False)
    assert _selinux.install(dry_run=False) == 0


def test_skips_when_already_loaded(
    monkeypatch: pytest.MonkeyPatch, no_subprocess: None,
) -> None:
    """Active SELinux but module already loaded → no rebuild."""
    monkeypatch.setattr(_selinux, "_selinux_active", lambda: True)
    monkeypatch.setattr(_selinux, "_already_loaded", lambda: True)
    assert _selinux.install(dry_run=False) == 0


def test_dry_run_has_no_side_effects(
    monkeypatch: pytest.MonkeyPatch, no_subprocess: None, capsys,
) -> None:
    """Active SELinux, not loaded, dry-run → prints intent, touches nothing."""
    monkeypatch.setattr(_selinux, "_selinux_active", lambda: True)
    monkeypatch.setattr(_selinux, "_already_loaded", lambda: False)
    assert _selinux.install(dry_run=True) == 0
    assert "trcc_usb" in capsys.readouterr().out

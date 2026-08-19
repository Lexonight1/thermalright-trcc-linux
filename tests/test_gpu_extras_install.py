"""GPU sensor-extras install — venv-safe pip + non-fatal to setup (#161).

The bundled-venv ``.deb`` ran ``pip install --user``, which pip REFUSES inside a
virtualenv ("User site-packages are not visible in this virtualenv").  That both
skipped the NVIDIA reader (no GPU sensors on a 5080) AND made ``trcc system
setup`` report total failure even though udev (device access) had succeeded.
"""
from __future__ import annotations

import sys

import pytest

from trcc.adapters.sensors.gpu_detect import install_matching_gpu_extras


def _capture_pip(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {}

    class _Result:
        returncode = 0

    def _run(cmd: list[str], **_kw: object) -> _Result:
        calls["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(
        "trcc.adapters.sensors.gpu_detect.subprocess.run", _run,
    )
    return calls


def test_install_drops_user_flag_in_venv(monkeypatch: pytest.MonkeyPatch) -> None:
    """In a virtualenv, pip install must NOT pass --user — pip refuses it there
    and the whole setup aborts (the bundled-venv .deb, #161)."""
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    monkeypatch.setattr(sys, "prefix", "/opt/trcc/venv")   # prefix != base_prefix
    calls = _capture_pip(monkeypatch)

    rc = install_matching_gpu_extras({"nvidia"})

    assert rc == 0
    assert "--user" not in calls["cmd"], calls["cmd"]
    assert "install" in calls["cmd"]
    assert "nvidia-ml-py>=11.0.0" in calls["cmd"]


def test_install_uses_user_flag_on_system_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-venv system Python uses --user (no root; user site-packages)."""
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)    # not a venv
    calls = _capture_pip(monkeypatch)

    rc = install_matching_gpu_extras({"nvidia"})

    assert rc == 0
    assert "--user" in calls["cmd"], calls["cmd"]


def test_install_noop_without_pip_vendor(monkeypatch: pytest.MonkeyPatch) -> None:
    """AMD/Intel need no pip lib → nothing installed, returns 0."""
    calls = _capture_pip(monkeypatch)

    rc = install_matching_gpu_extras({"amd", "intel"})

    assert rc == 0
    assert "cmd" not in calls          # subprocess.run never called


def test_setup_succeeds_when_only_gpu_extras_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Device access (udev) is the real deliverable — a GPU-reader install
    failure must NOT make setup report failure (#161)."""
    from trcc.adapters.system import linux

    monkeypatch.setattr(linux, "install_udev_rules", lambda dry_run: 0)
    monkeypatch.setattr(linux, "detect_gpu_vendors", lambda: {"nvidia"})
    monkeypatch.setattr(linux, "install_matching_gpu_extras",
                        lambda vendors, dry_run: 1)         # GPU install fails
    monkeypatch.setattr(linux, "install_selinux_policy", lambda dry_run: 0)

    rc = linux.LinuxOS().setup(interactive=True)

    assert rc == 0


def test_setup_fails_when_udev_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """udev IS fatal — if device access can't be granted, setup must fail."""
    from trcc.adapters.system import linux

    monkeypatch.setattr(linux, "install_udev_rules", lambda dry_run: 1)
    monkeypatch.setattr(linux, "detect_gpu_vendors", lambda: set())
    monkeypatch.setattr(linux, "install_matching_gpu_extras",
                        lambda vendors, dry_run: 0)
    monkeypatch.setattr(linux, "install_selinux_policy", lambda dry_run: 0)

    rc = linux.LinuxOS().setup(interactive=True)

    assert rc != 0

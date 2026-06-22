"""DataInstallService.ensure_all — per-resolution + per-orientation archives.

A non-square panel uses both orientations (854x480 landscape AND 480x854
portrait): themes, web (cloud backgrounds), and masks all need the rotated
counterpart, or a rotated device has no oriented catalog to load.

``conftest._stub_data_install`` (autouse) noops ``ensure_all`` so no test hits
the network — so we capture the REAL method at import (before the patch) and
call it directly.
"""
from __future__ import annotations

from pathlib import Path

from trcc.services.data_install import DataInstallService

from .conftest import FakePlatform

_REAL_ENSURE_ALL = DataInstallService.ensure_all


class _RecordingInstaller:
    """Records every archive_name install() is asked for; installs nothing."""

    def __init__(self) -> None:
        self.archives: list[str] = []

    def install(self, *, archive_name: str, target_dir: Path,
                subpath: str | None = None) -> bool:
        self.archives.append(archive_name)
        return True


def _run(tmp_home: Path, resolution: tuple[int, int]) -> list[str]:
    paths = FakePlatform(tmp_home).paths()
    rec = _RecordingInstaller()
    _REAL_ENSURE_ALL(DataInstallService(paths, rec), resolution)  # type: ignore[arg-type]
    return rec.archives


def test_ensure_all_installs_all_three_for_the_native_resolution(
    tmp_home: Path,
) -> None:
    archives = _run(tmp_home, (854, 480))
    assert "theme854480.7z" in archives
    assert "854480.7z" in archives        # web / cloud backgrounds
    assert "zt854480.7z" in archives      # masks


def test_ensure_all_installs_themes_web_and_masks_for_rotated_orientation(
    tmp_home: Path,
) -> None:
    """Non-square → the rotated orientation gets ALL THREE, themes included —
    the bug was themes being skipped, so a portrait panel had no theme/cloud
    backgrounds in its oriented dir."""
    archives = _run(tmp_home, (854, 480))
    assert "theme480854.7z" in archives, "portrait themes must be installed"
    assert "480854.7z" in archives        # portrait web / cloud backgrounds
    assert "zt480854.7z" in archives      # portrait masks


def test_ensure_all_square_does_not_install_a_rotated_counterpart(
    tmp_home: Path,
) -> None:
    archives = _run(tmp_home, (320, 320))
    assert archives == ["theme320320.7z", "320320.7z", "zt320320.7z"]


def test_http_data_installer_implements_the_core_data_installer_port() -> None:
    """Step 5: the concrete installer subclasses the core DataInstaller ABC."""
    from trcc.adapters.repo.data_install import HttpDataInstaller
    from trcc.core.ports import DataInstaller

    class _FakeHttp:
        def fetch(self, url, timeout_s=30.0):
            return b""

    assert issubclass(HttpDataInstaller, DataInstaller)
    assert isinstance(HttpDataInstaller(http=_FakeHttp()), DataInstaller)

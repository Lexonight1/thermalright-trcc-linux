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

import pytest

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
        def fetch(self, url, timeout_s=30.0, max_bytes=None):
            return b""

    assert issubclass(HttpDataInstaller, DataInstaller)
    assert isinstance(HttpDataInstaller(http=_FakeHttp()), DataInstaller)


# =========================================================================
# WHO asks for an install — a guess must not fetch the wrong library
# =========================================================================


@pytest.mark.parametrize("vid,pid", [
    (0x0416, 0x5302),   # (0, 0): one id, three panel sizes
    (0x0402, 0x3922),   # guesses 320x320
    (0x87AD, 0x70DB),   # guesses 480x480; an 854x480 panel shares the id
    (0x87CD, 0x70DB),   # guesses 320x320
])
def test_discovery_installs_nothing_for_a_panel_it_cannot_identify(
    tmp_home: Path, monkeypatch, vid: int, pid: int,
) -> None:
    """``DiscoverDevices`` must not install for a resolution it GUESSED.

    It never handshakes, so the only resolution available to it is the static
    registry row.  For ``0416:5302`` that row is honest about not knowing --
    one USB id covers at least 240x320, 320x240 and 1280x480, and only the PM
    byte tells them apart.

    A guess is not inert.  While the row said ``(240, 320)``, discovery
    fetched theme240320 for a 1280x480 Trofeo Vision and the browser then
    pointed there, two minutes AFTER the handshake reported ``(1280, 480)``:
    "locked at 240x320" (#300, and the same shape in #244 / #257 / #267 /
    #268).  The panel's real data still arrives -- ``ConnectDevice`` installs
    for the HANDSHAKE resolution.

    Asserting on what was SUBMITTED, not on what landed on disk: the install
    itself is stubbed suite-wide, so a disk assertion would pass either way.
    """
    from trcc.app import App
    from trcc.core.commands import DiscoverDevices
    from trcc.core.models import DeviceInfo

    platform = FakePlatform(tmp_home)
    monkeypatch.setattr(
        platform, "scan_devices",
        lambda: [DeviceInfo(vid=vid, pid=pid)],
    )
    app = App(platform=platform)

    submitted: list[tuple] = []
    real = app.data_install_runner.submit
    monkeypatch.setattr(
        app.data_install_runner, "submit",
        lambda *a, **k: (submitted.append(a), real(*a, **k))[1],
    )

    result = app.dispatch(DiscoverDevices())

    assert result.ok is True
    assert [prod.key for prod in result.products] == [f"{vid:04x}:{pid:04x}"], (
        "the device must still be DISCOVERED — only the guessed install goes"
    )
    assert submitted == [], (
        f"discovery installed for a guessed resolution: {submitted}. "
        f"Only the handshake knows the panel's size and variant library, and "
        f"discovery never handshakes (#300)."
    )


def test_a_discovered_then_connected_panel_installs_only_its_own_data(
    tmp_path: Path, monkeypatch,
) -> None:
    """The measured case: an 854x480 87ad:70db, whose registry row guesses
    480x480.  Discovery fetched 38.5 MB of 480x480 data ahead of the real
    26 MB in the one-worker queue (2026-10-06).  After both steps, the ONLY
    install asked for is the handshake's own."""
    from trcc.app import App
    from trcc.core.commands import ConnectDevice, DiscoverDevices

    from .mock_platform import MockPlatform

    spec = {"type": "lcd", "vid": "87ad", "pid": "70db", "pm": 11, "sub": 5}
    app = App(platform=MockPlatform([spec], tmp_path, host_sensors=False))
    submitted: list[tuple] = []
    monkeypatch.setattr(app.data_install_runner, "submit",
                        lambda *a, **k: submitted.append(a))

    assert app.dispatch(DiscoverDevices()).ok
    assert app.dispatch(ConnectDevice(key="87ad:70db")).ok

    assert [args[0] for args in submitted] == [(854, 480)], submitted


def test_an_undeclared_row_never_yields_a_zero_sized_profile() -> None:
    """``(0, 0)`` means "ask the device", never "render nothing".

    Making ``0416:5302`` honest (#300) put a new value in front of
    ``DisplayService._resolve_profile``'s last fallback, which read
    ``info.native_resolution`` straight into a ``DeviceProfile``.  It
    synthesized **0x0** — and the whole suite stayed green, because nothing
    exercised an undeclared row without a handshake.  A zero-sized surface is
    not a cautious guess; every consumer downstream divides by it.

    Asserted for EVERY registry row, not just the one that exposed it: the
    next row to become honest must not reintroduce this.
    """
    from trcc.core.registry import ALL_DEVICES
    from trcc.services.display import DisplayService

    svc = DisplayService.__new__(DisplayService)
    svc._profile_fallbacks = set()

    for key, product in sorted(ALL_DEVICES.items()):
        profile = DisplayService._resolve_profile(svc, product, None)
        assert profile.width > 0 and profile.height > 0, (
            f"{key[0]:04x}:{key[1]:04x} resolves to "
            f"{profile.width}x{profile.height} with no handshake — a zero "
            f"surface, not a geometry"
        )


# ── Every resolution a panel can resolve to must have shipped artwork ──────


def test_every_reachable_resolution_has_both_shipped_catalogs() -> None:
    """A profile resolution IS an archive name, so the two cannot drift.

    ``DataInstallService`` builds the archive name straight from the panel's
    size (``theme{width}{height}.7z``), and a non-square panel needs BOTH
    orientations because the SUB byte or the user's angle can select either.
    So changing a resolution in ``protocol.py`` silently changes which file
    the app asks for -- and if nobody drew that artwork, the panel has no
    themes at all and the failure is a download 404, far from the edit.

    This is the gate on that coupling, and it is the concrete answer to the
    standing request in #248 to move 1920x462 to 1920x480 on the strength of a
    moire measurement: the C# knows only 1920x462 and 1920x440 (its own aspect
    constant is the exact rational 77/320 == 462/1920), and no
    ``theme1920480.7z`` exists or ever has.  Re-sizing that panel is not a
    one-line table edit; it is a request for a new artwork set.

    MUTATION CHECK: change any row in ``FBL_PROFILES`` or the by-PM tables to
    a size nothing was drawn for, and this names the missing file.
    """
    from trcc.core.geometry import catalog_spellings
    from trcc.core.protocol import (
        _FBL_192_BY_PM,
        _FBL_224_BY_PM,
        FBL_PROFILES,
    )

    data_dir = Path(__file__).resolve().parent.parent / "src" / "trcc" / "data"
    assert data_dir.is_dir(), f"shipped data directory is missing: {data_dir}"

    reachable = ({(p.width, p.height) for p in FBL_PROFILES.values()}
                 | set(_FBL_192_BY_PM.values())
                 | set(_FBL_224_BY_PM.values()))
    assert len(reachable) > 10, "the resolution set collapsed — gate is vacuous"

    missing: list[str] = []
    for resolution in sorted(reachable):
        for spelling in catalog_spellings(resolution):
            archive = f"theme{spelling[0]}{spelling[1]}.7z"
            if not (data_dir / archive).is_file():
                missing.append(f"{resolution} needs {archive}")
    assert not missing, (
        "a panel can resolve to a size with no shipped theme catalog, so it "
        f"would have no themes at all: {missing}"
    )


# =========================================================================
# A failed extract keeps nothing, so the next run tries again
# =========================================================================


class _Fetches:
    def fetch(
        self, url: str, timeout_s: float = 30.0, max_bytes: int | None = None,
    ) -> bytes:
        return b"ARCHIVE"


class _DiesHalfway:
    """Writes one file, then fails — a full disk, or a killed 7z."""

    def __init__(self, *, raises: bool) -> None:
        self.raises = raises
        self.calls = 0

    def extract(self, archive: Path, target: Path) -> bool:
        self.calls += 1
        target.mkdir(parents=True, exist_ok=True)
        (target / "Theme1").mkdir()
        if self.raises:
            raise OSError("No space left on device")
        return False


def test_a_failed_extract_is_not_mistaken_for_an_install(tmp_path: Path) -> None:
    """Extracted straight into the target, the half-written files made
    ``_is_populated`` call it installed, so it was never fetched again."""
    from trcc.adapters.repo.data_install import HttpDataInstaller

    for raises in (False, True):
        target = tmp_path / f"theme320320-{raises}"
        extractor = _DiesHalfway(raises=raises)
        installer = HttpDataInstaller(http=_Fetches(), extractor=extractor)

        first = installer.install("theme320320.7z", target)
        second = installer.install("theme320320.7z", target)

        assert (first, second) == (False, False)
        assert extractor.calls == 2, "the second run must try again"
        assert not target.exists()
        assert list(tmp_path.glob("*.partial")) == []


class _Wraps:
    """A good archive that wraps its content in one folder, as some do."""

    def extract(self, archive: Path, target: Path) -> bool:
        theme = target / "theme320320" / "Theme1"
        theme.mkdir(parents=True)
        (theme / "00.png").write_bytes(b"\x89PNG")
        return True


def test_a_good_extract_lands_unwrapped_with_nothing_beside_it(
    tmp_path: Path,
) -> None:
    """The success half of the staged install: extracted aside, unwrapped
    there, then swapped in -- including over an empty target directory."""
    from trcc.adapters.repo.data_install import HttpDataInstaller

    target = tmp_path / "theme320320"
    target.mkdir()                     # empty: what a failed first run leaves

    ok = HttpDataInstaller(http=_Fetches(), extractor=_Wraps()).install(
        "theme320320.7z", target)

    assert ok is True
    assert sorted(p.name for p in target.iterdir()) == ["Theme1"]
    assert (target / "Theme1" / "00.png").read_bytes() == b"\x89PNG"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["theme320320"]


# ── EnsureDataDownload announces what it installed ──────────────────────────

def test_an_explicit_download_tells_every_open_grid(fake_platform) -> None:
    """``trcc`` / the API can pre-fetch a resolution while a window is open.
    Only the connect-time installer announced ``DataInstalled``; the explicit
    Command installed in silence, so the open window's theme, cloud and mask
    grids stayed as they were until a restart.

    MUTATION CHECK -- MEASURED 2026-10-02: drop the publish → fails.
    """
    from trcc.app import App
    from trcc.core.commands import EnsureDataDownload
    from trcc.core.events import DataInstalled

    app = App(platform=fake_platform)
    seen: list[DataInstalled] = []
    app.events.subscribe(DataInstalled, seen.append)  # type: ignore[arg-type]

    assert app.dispatch(EnsureDataDownload(width=320, height=320)).ok
    assert app.dispatch(EnsureDataDownload(width=0, height=320)).ok is False

    assert [(e.resolution, e.ok) for e in seen] == [((320, 320), True)], (
        "one announcement for the install that ran, none for the refused one")

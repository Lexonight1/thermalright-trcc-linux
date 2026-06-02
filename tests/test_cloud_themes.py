"""End-to-end test for the cloud-theme port: list + materialise.

Uses a fake ``HttpFetcher`` to stay offline; proves the catalog parses
the categories, the service writes a real theme dir, and ``LoadTheme``
can pick it up from there.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.adapters.repo.http import HttpFetchError
from trcc.adapters.theme.cloud import CzhordeCatalog
from trcc.core.ports import HttpFetcher
from trcc.core.protocol import FBL_PROFILES
from trcc.services.cloud_theme import CloudThemeService

from .conftest import FakePaths

# Unique resolutions from the canonical FBL profile registry — the
# single source of truth for "what canvases the rebuild supports."
TEST_RESOLUTIONS: list[tuple[int, int]] = sorted({
    (p.width, p.height) for p in FBL_PROFILES.values()
})


class FakeHttp(HttpFetcher):
    """In-memory fetcher — maps URL → bytes (or raises)."""

    def __init__(self) -> None:
        self.responses: dict[str, bytes] = {}
        self.errors: dict[str, str] = {}
        self.calls: list[str] = []

    def fetch(self, url: str, timeout_s: float = 30.0) -> bytes:
        del timeout_s
        self.calls.append(url)
        if url in self.errors:
            raise HttpFetchError(self.errors[url])
        if url in self.responses:
            return self.responses[url]
        raise HttpFetchError(f"unexpected URL: {url}")


# =========================================================================


def test_categories_static_table_has_expected_prefixes() -> None:
    cats = CzhordeCatalog.categories()
    prefixes = {c.prefix for c in cats}
    assert prefixes == {"a", "b", "c", "d", "e", "y"}


def test_list_themes_all_enumerates_every_id(tmp_path: Path) -> None:
    catalog = CzhordeCatalog(http=FakeHttp(), cache_dir=tmp_path)
    themes = catalog.list_themes("all")
    # 82 + 25 + 72 + 55 + 54 + 10 = 298 themes total
    assert len(themes) == 82 + 25 + 72 + 55 + 54 + 10
    assert themes[0].id == "a001"
    assert themes[0].category == "a"


def test_list_themes_unknown_category_raises(tmp_path: Path) -> None:
    catalog = CzhordeCatalog(http=FakeHttp(), cache_dir=tmp_path)
    with pytest.raises(ValueError):
        catalog.list_themes("zzz")


def test_download_theme_caches_to_disk(tmp_path: Path) -> None:
    http = FakeHttp()
    payload = b"\x00" * 256
    # First server tried — international.
    http.responses[
        "http://www.czhorde.cc/tr/bj320320/a001.mp4"
    ] = payload
    catalog = CzhordeCatalog(
        http=http, cache_dir=tmp_path, resolution="320x320",
    )
    target = catalog.download_theme("a001")
    assert target.is_file()
    assert target.read_bytes() == payload
    # Second call is a cache hit — no new HTTP.
    catalog.download_theme("a001")
    assert len(http.calls) == 1


def test_download_falls_back_to_secondary_server(tmp_path: Path) -> None:
    http = FakeHttp()
    http.errors["http://www.czhorde.cc/tr/bj320320/a002.mp4"] = "503"
    http.responses[
        "http://www.czhorde.com/tr/bj320320/a002.mp4"
    ] = b"backup"
    catalog = CzhordeCatalog(
        http=http, cache_dir=tmp_path, resolution="320x320",
    )
    target = catalog.download_theme("a002")
    assert target.read_bytes() == b"backup"
    assert len(http.calls) == 2  # both servers tried


def test_download_both_servers_fail_raises(tmp_path: Path) -> None:
    http = FakeHttp()
    http.errors["http://www.czhorde.cc/tr/bj320320/a003.mp4"] = "503"
    http.errors["http://www.czhorde.com/tr/bj320320/a003.mp4"] = "504"
    catalog = CzhordeCatalog(
        http=http, cache_dir=tmp_path, resolution="320x320",
    )
    with pytest.raises(HttpFetchError):
        catalog.download_theme("a003")


def test_download_rejects_path_injection(tmp_path: Path) -> None:
    http = FakeHttp()
    catalog = CzhordeCatalog(http=http, cache_dir=tmp_path)
    with pytest.raises(ValueError):
        catalog.download_theme("../../etc/passwd")
    assert http.calls == []


# =========================================================================
# CloudThemeService end-to-end
# =========================================================================


@pytest.mark.parametrize("resolution", TEST_RESOLUTIONS)
def test_materialise_writes_flat_layout(
    tmp_path: Path, resolution: tuple[int, int],
) -> None:
    """``materialise`` returns the MP4 path itself and writes flat —
    legacy convention: ``data/web/{w}{h}/<id>.mp4`` next to the
    preview thumbnails from the bundled 7z archive.  No per-theme
    subdirectory, no per-theme trcc.json — those were a next/-only
    invention that broke the GUI's grid scan."""
    w, h = resolution
    paths = FakePaths(tmp_path)
    # Production wires the catalog cache_dir at ``paths.data_dir()/web``
    # so downloads land directly in ``paths.cloud_theme_dir(w, h)``
    # (the same dir the GUI grid scans).  Mirror that wiring here so
    # the test pins the no-duplicate-copy invariant.
    cache = paths.data_dir() / "web"
    cache.mkdir(parents=True, exist_ok=True)
    http = FakeHttp()
    http.responses[
        f"http://www.czhorde.cc/tr/bj{w}{h}/a004.mp4"
    ] = b"mp4-bytes"
    service = CloudThemeService(
        catalog=CzhordeCatalog(
            http=http, cache_dir=cache, resolution=f"{w}x{h}",
        ),
        paths=paths,
    )
    mp4_path = service.materialise("a004", resolution=resolution)
    # Returns the MP4 file (not a theme dir).
    assert mp4_path.is_file()
    assert mp4_path.name == "a004.mp4"
    # Flat layout under cloud_theme_dir.
    assert mp4_path.parent == paths.cloud_theme_dir(w, h)
    # No per-theme subdir or trcc.json — those were the bug.
    assert not (paths.cloud_theme_dir(w, h) / "a004").exists()
    assert not (paths.cloud_theme_dir(w, h) / "a004" / "trcc.json").exists()
    # Re-running is idempotent — no extra HTTP call, no duplicate write.
    again = service.materialise("a004", resolution=resolution)
    assert again == mp4_path
    assert len(http.calls) == 1

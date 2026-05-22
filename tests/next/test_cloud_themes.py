"""End-to-end test for the cloud-theme port: list + materialise.

Uses a fake ``HttpFetcher`` to stay offline; proves the catalog parses
the categories, the service writes a real theme dir, and ``LoadTheme``
can pick it up from there.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.next.adapters.repo.http import HttpFetchError
from trcc.next.adapters.theme.cloud import CzhordeCatalog
from trcc.next.core.ports import HttpFetcher
from trcc.next.services.cloud_theme import CloudThemeService

from .conftest import FakePaths


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


def test_materialise_creates_theme_dir(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    paths = FakePaths(tmp_path)
    http = FakeHttp()
    http.responses[
        "http://www.czhorde.cc/tr/bj320320/a004.mp4"
    ] = b"mp4-bytes"
    service = CloudThemeService(
        catalog=CzhordeCatalog(
            http=http, cache_dir=cache, resolution="320x320",
        ),
        paths=paths,
    )
    theme_dir = service.materialise("a004")
    assert theme_dir.is_dir()
    assert (theme_dir / "a004.mp4").is_file()
    assert (theme_dir / "trcc-next.json").is_file()
    # Re-running is idempotent — no extra HTTP call, no duplicate write.
    again = service.materialise("a004")
    assert again == theme_dir
    assert len(http.calls) == 1

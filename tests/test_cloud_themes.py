"""End-to-end test for the cloud-theme port: list + materialise.

Uses a fake ``HttpFetcher`` to stay offline; proves the catalog parses
the categories, the service writes a real theme dir, and ``LoadTheme``
can pick it up from there.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from trcc.adapters.repo.http import HttpFetchError, UrllibHttpFetcher
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


# How every vendor video opens -- read off the wire from both mirrors on
# 2026-10-06.  A fixture body has to start this way, or the catalog rightly
# refuses to cache it.
MP4 = b"\x00\x00\x00\x1cftypmp42"


class FakeHttp(HttpFetcher):
    """In-memory fetcher — maps URL → bytes (or raises)."""

    def __init__(self) -> None:
        self.responses: dict[str, bytes] = {}
        self.errors: dict[str, str] = {}
        self.calls: list[str] = []

    def fetch(
        self, url: str, timeout_s: float = 30.0, max_bytes: int | None = None,
    ) -> bytes:
        del timeout_s, max_bytes
        self.calls.append(url)
        if url in self.errors:
            raise HttpFetchError(self.errors[url])
        if url in self.responses:
            return self.responses[url]
        raise HttpFetchError(f"unexpected URL: {url}")


# =========================================================================


def test_categories_static_table_has_expected_prefixes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cats = CzhordeCatalog(http=FakeHttp(), cache_dir=tmp_path).categories()
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
    payload = MP4 + b"\x00" * 256
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


def test_download_theme_uses_per_call_resolution_for_folder_and_url(
    tmp_path: Path,
) -> None:
    """A per-call ``resolution`` overrides the construction default for BOTH the
    cache folder and the download URL — so each device caches in its own
    ``<res>`` dir (matches C# GetWebBackgroundImageDirectory/HttpDirectory).
    The bug: the fixed 320x320 default dumped every device's themes into
    web/320320 and the URL fetched the 320x320 variant, so no non-320 device
    found its background."""
    http = FakeHttp()
    payload = MP4 + b"\x01" * 128
    http.responses["http://www.czhorde.cc/tr/bj480854/a001.mp4"] = payload
    catalog = CzhordeCatalog(
        http=http, cache_dir=tmp_path, resolution="320x320",  # default ignored
    )

    target = catalog.download_theme("a001", "480x854")

    assert target == tmp_path / "480854" / "a001.mp4", target
    assert target.read_bytes() == payload
    assert http.calls == ["http://www.czhorde.cc/tr/bj480854/a001.mp4"]
    # The 320x320 default folder must NOT have been used.
    assert not (tmp_path / "320320").exists()


def test_download_falls_back_to_secondary_server(tmp_path: Path) -> None:
    http = FakeHttp()
    http.errors["http://www.czhorde.cc/tr/bj320320/a002.mp4"] = "503"
    http.responses[
        "http://www.czhorde.com/tr/bj320320/a002.mp4"
    ] = MP4 + b"backup"
    catalog = CzhordeCatalog(
        http=http, cache_dir=tmp_path, resolution="320x320",
    )
    target = catalog.download_theme("a002")
    assert target.read_bytes() == MP4 + b"backup"
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


def test_a_body_that_is_not_a_video_falls_through_and_is_never_cached(
    tmp_path: Path,
) -> None:
    """The mirrors are plain http: a 200 can carry anything.

    A captive portal's HTML page from the first mirror is refused like any
    other failed fetch, so the second mirror is tried; when it is no better,
    nothing is written for ffmpeg to probe later.
    """
    http = FakeHttp()
    page = b"<!DOCTYPE html><html>sign in to the wifi</html>"
    http.responses["http://www.czhorde.cc/tr/bj320320/a005.mp4"] = page
    http.responses["http://www.czhorde.com/tr/bj320320/a005.mp4"] = page
    catalog = CzhordeCatalog(http=http, cache_dir=tmp_path, resolution="320x320")

    with pytest.raises(HttpFetchError, match="is not a .mp4 file"):
        catalog.download_theme("a005")

    assert len(http.calls) == 2
    assert list(tmp_path.rglob("*")) == []

    http.responses["http://www.czhorde.com/tr/bj320320/a005.mp4"] = MP4 + b"real"
    assert catalog.download_theme("a005").read_bytes() == MP4 + b"real"


def test_a_cached_file_that_is_not_a_video_is_fetched_again(tmp_path: Path) -> None:
    """Before the type check any non-empty body was kept forever."""
    poisoned = tmp_path / "320320" / "a006.mp4"
    poisoned.parent.mkdir(parents=True)
    poisoned.write_bytes(b"<!DOCTYPE html>")
    http = FakeHttp()
    http.responses["http://www.czhorde.cc/tr/bj320320/a006.mp4"] = MP4 + b"real"
    catalog = CzhordeCatalog(http=http, cache_dir=tmp_path, resolution="320x320")

    assert catalog.download_theme("a006").read_bytes() == MP4 + b"real"
    assert len(http.calls) == 1


def test_a_download_leaves_only_the_file_it_names(tmp_path: Path) -> None:
    """Written to a sibling and renamed: no ``.part`` survives a good fetch."""
    http = FakeHttp()
    http.responses["http://www.czhorde.cc/tr/bj320320/a007.mp4"] = MP4 + b"x"
    catalog = CzhordeCatalog(http=http, cache_dir=tmp_path, resolution="320x320")

    target = catalog.download_theme("a007")

    assert list(target.parent.iterdir()) == [target]


def test_a_cached_video_has_the_mode_any_other_new_file_gets(tmp_path: Path) -> None:
    """``tempfile`` would have made it 0600 beside the 0644 thumbnails."""
    http = FakeHttp()
    http.responses["http://www.czhorde.cc/tr/bj320320/a008.mp4"] = MP4 + b"x"
    catalog = CzhordeCatalog(http=http, cache_dir=tmp_path, resolution="320x320")
    sibling = tmp_path / "plain"
    sibling.write_bytes(b"")

    target = catalog.download_theme("a008")

    assert target.stat().st_mode == sibling.stat().st_mode


class _SizedServer(ThreadingHTTPServer):
    """Remembers how much of the last body actually left the server."""

    sent = 0
    done: threading.Event


class _Sized(BaseHTTPRequestHandler):
    """Serves a body of ``int(path)`` bytes, in chunks, to a real client."""

    server: _SizedServer

    def do_GET(self) -> None:
        size = int(self.path.strip("/"))
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.end_headers()
        chunk = b"\x00" * 65536
        sent = 0
        try:
            for offset in range(0, size, len(chunk)):
                sent += self.wfile.write(chunk[: size - offset])
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client stopped reading -- what a cap is for
        self.server.sent = sent
        self.server.done.set()

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.fixture
def sized_server():  # type: ignore[no-untyped-def]
    server = _SizedServer(("127.0.0.1", 0), _Sized)
    server.done = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _url(server: _SizedServer, size: int) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/{size}"


def test_the_real_fetcher_refuses_a_body_over_its_cap(
    sized_server: _SizedServer,
) -> None:
    """Driven through ``urlopen`` itself, not a stand-in for it."""
    fetcher = UrllibHttpFetcher()
    cap = 1024 * 1024

    assert len(fetcher.fetch(_url(sized_server, cap), max_bytes=cap)) == cap
    with pytest.raises(HttpFetchError, match="larger than"):
        fetcher.fetch(_url(sized_server, cap + 1), max_bytes=cap)
    assert len(fetcher.fetch(_url(sized_server, 3 * cap))) == 3 * cap


def test_the_real_fetcher_stops_reading_at_its_cap(
    sized_server: _SizedServer,
) -> None:
    """Refusing AFTER reading it all would cap nothing.

    The server counts what it got onto the wire before the client hung up.
    Socket buffers let some megabytes through past the cap; reading the whole
    body would let all 256.
    """
    cap = 1024 * 1024

    with pytest.raises(HttpFetchError, match="larger than"):
        UrllibHttpFetcher().fetch(_url(sized_server, 256 * cap), max_bytes=cap)

    assert sized_server.done.wait(10)
    assert sized_server.sent < 64 * cap


def test_download_rejects_path_injection(tmp_path: Path) -> None:
    http = FakeHttp()
    catalog = CzhordeCatalog(http=http, cache_dir=tmp_path)
    with pytest.raises(ValueError):
        catalog.download_theme("../../etc/passwd")
    assert http.calls == []


# =========================================================================
# CloudThemeService end-to-end
# =========================================================================


def test_the_tile_gif_keeps_the_videos_aspect(tmp_path: Path) -> None:
    """A 16:9 video letterboxes into the 120x120 tile, as the C# fits its
    tile PNGs (UCThemeWeb.SetThemeWeb); ``scale=120:120`` stretched it."""
    import subprocess

    from PySide6.QtGui import QImage

    from trcc.core import toolchain
    from trcc.services.cloud_theme import _generate_animated_gif

    if not toolchain.present("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    mp4, gif = tmp_path / "a001.mp4", tmp_path / "a001.gif"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                    "-i", "color=c=red:s=320x180:d=1:r=8",
                    "-pix_fmt", "yuv420p", str(mp4)],
                   capture_output=True, check=True, timeout=60)

    _generate_animated_gif(mp4, gif)

    frame = QImage(str(gif))
    assert (frame.width(), frame.height()) == (120, 120)
    bar, middle = frame.pixelColor(60, 4), frame.pixelColor(60, 60)
    assert max(bar.red(), bar.green(), bar.blue()) < 30          # letterbox
    assert (middle.red() > 200, middle.green() < 60) == (True, True)


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
    ] = MP4 + b"mp4-bytes"
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


@pytest.mark.parametrize("resolution", TEST_RESOLUTIONS)
def test_download_cloud_theme_caches_without_applying(
    fake_platform, resolution: tuple[int, int],
) -> None:
    """``DownloadCloudTheme`` is the download half — it must NOT apply.

    ``LoadCloudTheme`` persists ``background_path`` and dispatches
    ``PlayVideo``.  That is the whole reason a separate Command exists: the
    GUI's cloud browser downloads on one event so a thumbnail can be drawn,
    and applies on a later click.  If this Command applied too, opening the
    browser would hijack whatever the panel is showing.
    """
    from trcc.app import App
    from trcc.core.commands import DownloadCloudTheme

    w, h = resolution
    app = App(fake_platform)
    http = FakeHttp()
    http.responses[f"http://www.czhorde.cc/tr/bj{w}{h}/a004.mp4"] = MP4 + b"mp4-bytes"
    cache = app.platform.paths().data_dir() / "web"
    cache.mkdir(parents=True, exist_ok=True)
    app.cloud_themes = CloudThemeService(
        catalog=CzhordeCatalog(http=http, cache_dir=cache,
                               resolution=f"{w}x{h}"),
        paths=app.platform.paths(),
    )

    result = app.dispatch(
        DownloadCloudTheme(theme_id="a004", resolution=resolution),
    )

    assert result.ok, result.message
    assert Path(result.theme_path).is_file()
    assert result.theme_id == "a004"
    # Applied nothing: no background persisted, no playback started.
    assert not app.settings.for_device("0402:3922").background_path
    assert app.media.playback("0402:3922") is None

    # Idempotent — the second call re-uses the cache (materialise's contract).
    again = app.dispatch(
        DownloadCloudTheme(theme_id="a004", resolution=resolution),
    )
    assert again.theme_path == result.theme_path
    assert len(http.calls) == 1


def test_download_cloud_theme_reports_a_failed_fetch(fake_platform) -> None:
    """A missing theme fails as ``ok=false``, never as an exception.

    Every UI renders ``message``; a traceback out of a Command would take the
    GUI's download worker thread with it.
    """
    from trcc.app import App
    from trcc.core.commands import DownloadCloudTheme

    app = App(fake_platform)
    cache = app.platform.paths().data_dir() / "web"
    cache.mkdir(parents=True, exist_ok=True)
    app.cloud_themes = CloudThemeService(
        catalog=CzhordeCatalog(http=FakeHttp(), cache_dir=cache,
                               resolution="320x320"),
        paths=app.platform.paths(),
    )

    result = app.dispatch(
        DownloadCloudTheme(theme_id="nope", resolution=(320, 320)),
    )
    assert not result.ok
    assert result.message


def test_czhorde_catalog_implements_the_core_cloud_catalog_port(tmp_path) -> None:
    """Step 5: the concrete catalog subclasses the core CloudCatalog ABC, and the
    DTOs live in core.models — so services type against core, not the adapter."""
    from trcc.adapters.theme.cloud import CzhordeCatalog
    from trcc.core.models import CloudCategory, CloudThemeEntry  # noqa: F401
    from trcc.core.ports import CloudCatalog

    catalog = CzhordeCatalog(http=FakeHttp(), cache_dir=tmp_path)
    assert isinstance(catalog, CloudCatalog)


def test_a_failed_download_leaves_no_directory(tmp_path: Path) -> None:
    """The cache directory was created before the fetch, so every failed
    download — and the API takes any width/height — left an empty one."""
    catalog = CzhordeCatalog(http=FakeHttp(), cache_dir=tmp_path,
                             resolution="1001x7")

    with pytest.raises(HttpFetchError):
        catalog.download_theme("a001")

    assert list(tmp_path.iterdir()) == []


# ── A tile click runs no ffmpeg; the App backfills the tile GIFs ────────────
#
# ``materialise`` made the PNG + GIF itself, so LoadCloudTheme -- a tile
# click -- re-ran up to 40 s of ffmpeg inside the dispatch for any video whose
# GIF had once failed, every click.  And nothing ever made a GIF for a video
# downloaded while ffmpeg was missing.

def _cached(tmp_path: Path, ids: tuple[str, ...]):  # type: ignore[no-untyped-def]
    paths = FakePaths(tmp_path)
    folder = paths.cloud_theme_dir(320, 320)
    folder.mkdir(parents=True)
    for theme_id in ids:
        (folder / f"{theme_id}.mp4").write_bytes(MP4 + b"video")
    service = CloudThemeService(
        catalog=CzhordeCatalog(http=FakeHttp(), cache_dir=paths.data_dir() / "web",
                               resolution="320x320"),
        paths=paths,
    )
    return service, folder


@pytest.fixture
def ffmpeg_calls(monkeypatch):  # type: ignore[no-untyped-def]
    from trcc.services import cloud_theme

    made: list[str] = []
    monkeypatch.setattr(cloud_theme, "_generate_animated_gif",
                        lambda mp4, gif: made.append(gif.name))
    monkeypatch.setattr(cloud_theme, "_extract_first_frame_png",
                        lambda mp4, png: made.append(png.name))
    monkeypatch.setattr(cloud_theme.toolchain, "present", lambda tool: True)
    return made


def test_a_tile_click_runs_no_ffmpeg(tmp_path: Path, ffmpeg_calls) -> None:
    service, _ = _cached(tmp_path, ("a004",))
    service.materialise("a004", (320, 320))
    assert ffmpeg_calls == []


def test_the_backfill_makes_only_the_missing_gifs(tmp_path: Path,
                                                  ffmpeg_calls) -> None:
    service, folder = _cached(tmp_path, ("a001", "a002"))
    (folder / "a002.gif").write_bytes(b"GIF89a")
    for theme_id in ("a001", "a002"):           # first-frame PNGs are fresh
        (folder / f"{theme_id}.png").write_bytes(b"png")
    assert service.backfill_previews((320, 320)) == 2
    assert ffmpeg_calls == ["a001.gif"]


def test_the_backfill_without_ffmpeg_runs_nothing(tmp_path: Path,
                                                  ffmpeg_calls,
                                                  monkeypatch) -> None:
    from trcc.services import cloud_theme

    monkeypatch.setattr(cloud_theme.toolchain, "present", lambda tool: False)
    service, _ = _cached(tmp_path, ("a001",))
    service.backfill_previews((320, 320))
    assert ffmpeg_calls == []


def test_downloaded_names_only_cached_videos(tmp_path: Path) -> None:
    service, folder = _cached(tmp_path, ("a002", "a001"))
    (folder / "a003.mp4").write_bytes(b"<html>not a video</html>")
    (folder / "a009.png").write_bytes(b"png")
    assert service._catalog.downloaded("320x320") == ("a001", "a002")


def test_a_landed_install_backfills_both_orientations(fake_platform,
                                                      monkeypatch) -> None:
    import time

    from trcc.app import App
    from trcc.core.events import DataInstalled

    app = App(fake_platform)
    seen: list = []
    monkeypatch.setattr(app.cloud_themes, "backfill_previews", seen.append)
    app.events.publish(DataInstalled(resolution=(854, 480), ok=False))
    app.events.publish(DataInstalled(resolution=(854, 480), ok=True))
    deadline = time.monotonic() + 5
    while len(seen) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen == [(854, 480), (480, 854)]


def test_the_explicit_download_still_makes_the_thumbnails(fake_platform,
                                                          ffmpeg_calls) -> None:
    from trcc.app import App
    from trcc.core.commands import DownloadCloudTheme

    app = App(fake_platform)
    http = FakeHttp()
    http.responses["http://www.czhorde.cc/tr/bj320320/a004.mp4"] = MP4 + b"v"
    app.cloud_themes = CloudThemeService(
        catalog=CzhordeCatalog(http=http,
                               cache_dir=app.platform.paths().data_dir() / "web",
                               resolution="320x320"),
        paths=app.platform.paths(),
    )
    assert app.dispatch(DownloadCloudTheme(theme_id="a004",
                                           resolution=(320, 320))).ok
    assert sorted(ffmpeg_calls) == ["a004.gif", "a004.png"]

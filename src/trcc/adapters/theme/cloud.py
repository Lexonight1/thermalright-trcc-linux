"""``CzhordeCatalog`` — Thermalright's hosted theme catalog.

Mirrors legacy ``adapters/infra/theme_cloud.py`` but injects an
``HttpFetcher`` so tests stay offline.  The catalog itself is static
(prefix → (display name, count)); the network calls are bytes-only.

Theme IDs follow legacy convention: prefix + 3-digit index (``a001``…
``y005``).  Each ID maps to a ``{base_url}{id}.mp4`` at a per-resolution
URL on either of two servers (the user picks one in Settings; the
catalog retries the other on failure).
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Literal

from ...core.errors import HttpFetchError
from ...core.logs import Blob
from ...core.models import CloudCategory, CloudThemeEntry
from ...core.ports import CloudCatalog, HttpFetcher

log = logging.getLogger(__name__)

# DTOs moved to ``core.models`` (pure data the CloudCatalog port speaks);
# re-exported here so existing ``from ...cloud import CloudCategory`` keeps working.
__all__ = ["CloudCategory", "CloudThemeEntry", "CzhordeCatalog"]


# =========================================================================
# Categories — static data (matches legacy theme_cloud.CATEGORIES)
# =========================================================================


_CATEGORIES: tuple[CloudCategory, ...] = (
    CloudCategory("a", "Gallery",   82),
    CloudCategory("b", "Tech",      25),
    CloudCategory("c", "HUD",       72),
    CloudCategory("d", "Light",     55),
    CloudCategory("e", "Nature",    54),
    CloudCategory("y", "Aesthetic", 10),
)


Server = Literal["china", "international"]


_SERVERS: dict[Server, str] = {
    "china":         "http://www.czhorde.com/tr/bj{resolution}/",
    "international": "http://www.czhorde.cc/tr/bj{resolution}/",
}

# Both mirrors are plain http (neither answers on :443), so a body is only as
# trustworthy as the network path it crossed.  Measured 2026-10-06, HEAD on
# all 298 ids at 1600x720: the largest video is 10.0 MB (b011).  64 MB leaves
# room for the vendor's next one and still refuses a body nobody should keep.
_MAX_BYTES = 64 * 1024 * 1024

# Enough of a file's head to tell what it is.
_HEAD_BYTES = 12


# =========================================================================
# Catalog
# =========================================================================


class CzhordeCatalog(CloudCatalog):
    """Read-side of Thermalright's hosted theme catalog.

    Construction is cheap (just stores config); the network only fires on
    explicit ``download_*`` calls.  Cached MP4 / PNG files live under
    ``cache_dir / resolution / <theme_id>.{mp4,png}`` and survive across
    process restarts — re-download only when a file is missing.

    The two-server fallback: try the user's preferred server first, then
    the other one.  If both fail, raise ``HttpFetchError``.
    """

    def __init__(
        self,
        http: HttpFetcher,
        cache_dir: Path,
        *,
        resolution: str = "320x320",
        preferred_server: Server = "international",
    ) -> None:
        log.debug("__init__: http=%s cache_dir=%s", http, cache_dir)
        self._http = http
        self._cache_dir = cache_dir
        self._resolution = resolution
        self._preferred = preferred_server

    # ── Static reads ──────────────────────────────────────────────────

    def categories(self) -> tuple[CloudCategory, ...]:
        log.debug("categories")
        return _CATEGORIES

    def list_themes(self, category: str = "all") -> list[CloudThemeEntry]:
        """Enumerate theme IDs in *category* (or all categories)."""
        log.debug("list_themes: category=%s", category)
        cats = (
            list(_CATEGORIES)
            if category in ("", "all")
            else [c for c in _CATEGORIES if c.prefix == category]
        )
        if category not in ("", "all") and not cats:
            raise ValueError(
                f"Unknown category {category!r}; expected one of "
                f"'all' / {', '.join(c.prefix for c in _CATEGORIES)}",
            )
        out: list[CloudThemeEntry] = []
        for cat in cats:
            for i in range(1, cat.count + 1):
                out.append(CloudThemeEntry(
                    id=f"{cat.prefix}{i:03d}",
                    category=cat.prefix,
                    category_name=cat.name,
                ))
        return out

    # ── Network ───────────────────────────────────────────────────────

    def download_theme(
        self, theme_id: str, resolution: str | None = None,
    ) -> Path:
        """Fetch ``<theme_id>.mp4`` (cached) and return its local path.

        ``resolution`` (``"WxH"``) selects BOTH the per-resolution cache folder
        AND the per-resolution download URL — so each device's themes cache in
        their own ``web/<res>`` dir.  Defaults to the construction-time
        resolution only when a caller doesn't know the device's (rare).
        """
        log.debug("download_theme: theme_id=%s resolution=%s", theme_id, resolution)
        return self._fetch_cached(theme_id, ".mp4", resolution or self._resolution)

    def downloaded(self, resolution: str) -> tuple[str, ...]:
        """Ids of the ``.mp4`` videos already in *resolution*'s cache."""
        folder = self._cache_dir / resolution.replace("x", "")
        ids = tuple(sorted(p.stem for p in folder.glob("*.mp4")
                           if _is_cached(p, ".mp4")))
        log.debug("downloaded: %s -> %d video(s)", resolution, len(ids))
        return ids

    # ── Internals ─────────────────────────────────────────────────────

    def _fetch_cached(self, theme_id: str, suffix: str, resolution: str) -> Path:
        if not _is_safe_theme_id(theme_id):
            log.error("CzhordeCatalog: rejected invalid theme id %r", theme_id)
            raise ValueError(f"Invalid cloud theme id: {theme_id!r}")
        res_dir = resolution.replace("x", "")
        cache = self._cache_dir / res_dir
        target = cache / f"{theme_id}{suffix}"
        if _is_cached(target, suffix):
            log.debug("CzhordeCatalog: cache hit %s", target)
            return target
        log.info("CzhordeCatalog: fetching %s%s @ %s (cache miss)",
                 theme_id, suffix, resolution)
        data = self._fetch_with_fallback(theme_id, suffix, resolution)
        # Only once there is something to keep: created before the fetch, a
        # failed download left an empty directory per requested resolution.
        cache.mkdir(parents=True, exist_ok=True)
        # A sibling of its own, then a rename: a reader never sees half a
        # file, and two UIs fetching the same theme -- threads of one App, or
        # two processes -- cannot write into one another's.  Not ``tempfile``:
        # it creates 0600, where every other file in the cache honours umask.
        part = target.with_name(
            f".{target.name}.{os.getpid()}.{threading.get_ident()}.part",
        )
        part.write_bytes(data)
        part.replace(target)
        log.info("CzhordeCatalog: cached %d bytes to %s", len(data), target)
        return target

    def _fetch_with_fallback(
        self, theme_id: str, suffix: str, resolution: str,
    ) -> bytes:
        """Primary first, backup on failure.

        Primary gets a 30s timeout — tight enough to fall through to
        the backup when the mirror is hard-down, loose enough to
        tolerate a moderately slow connection on the working one
        (urllib has only one combined socket timeout, so this also
        bounds the download itself, not just the connect).  Backup
        keeps the full 60s.  Both servers failing raises the last error.
        """
        if self._preferred == "international":
            order: tuple[Server, Server] = ("international", "china")
        else:
            order = ("china", "international")
        timeouts = (30.0, 60.0)
        last_err: HttpFetchError | None = None
        for server, timeout_s in zip(order, timeouts, strict=False):
            url = self._url_for(theme_id, suffix, server, resolution)
            try:
                data = self._http.fetch(
                    url, timeout_s=timeout_s, max_bytes=_MAX_BYTES,
                )
                if not _has_magic(suffix, data[:_HEAD_BYTES]):
                    raise HttpFetchError(
                        f"GET {url} is not a {suffix} file "
                        f"(starts {data[:_HEAD_BYTES]!r})",
                    )
                return data
            except HttpFetchError as e:
                last_err = e
                log.warning("CzhordeCatalog: fetch %s via %s failed: %s",
                            theme_id, server, e)
        log.error("CzhordeCatalog: all servers failed for %s%s",
                  theme_id, suffix)
        assert last_err is not None
        raise last_err

    def _url_for(
        self, theme_id: str, suffix: str, server: Server, resolution: str,
    ) -> str:
        log.debug("_url_for: theme_id=%s suffix=%s", theme_id, suffix)
        base = _SERVERS[server]
        res_dir = resolution.replace("x", "")
        base_url = base.replace("{resolution}", res_dir)
        return f"{base_url}{theme_id}{suffix}"


# =========================================================================
# Helpers
# =========================================================================


def _has_magic(suffix: str, head: bytes) -> bool:
    """Does ``head`` start the way a real ``suffix`` file does?

    An MP4 opens with its ``ftyp`` box (the vendor's do: ``....ftypmp42``),
    a PNG with its 8-byte signature.  Anything else — the HTML page a
    captive portal or a 404 serves with a 200 — is not cached for ffmpeg
    to probe.
    """
    log.debug("_has_magic: suffix=%s head=%s", suffix, Blob(head))
    match suffix:
        case ".mp4":
            return head[4:8] == b"ftyp"
        case ".png":
            return head.startswith(b"\x89PNG\r\n\x1a\n")
    return False


def _is_cached(target: Path, suffix: str) -> bool:
    """A cached file counts only if it is still the kind it claims to be.

    Before the type check, any non-empty body was kept forever, so a cache
    poisoned then is fetched again now rather than trusted.
    """
    log.debug("_is_cached: target=%s", target)
    if not target.is_file():
        return False
    with target.open("rb") as f:
        head = f.read(_HEAD_BYTES)
    if _has_magic(suffix, head):
        return True
    log.warning("CzhordeCatalog: cached %s is not a %s file (starts %r) "
                "— fetching it again", target, suffix, head)
    return False


def _is_safe_theme_id(theme_id: str) -> bool:
    """Reject path-injecty / non-conforming IDs.

    Legacy IDs are always ``<lowercase-letter><3 digits>``; we accept
    that and reject anything that could navigate the filesystem.
    """
    log.debug("_is_safe_theme_id: theme_id=%s", theme_id)
    if not (4 <= len(theme_id) <= 8):
        return False
    if not theme_id[0].isalpha() or not theme_id[1:].isalnum():
        return False
    return all(c not in theme_id for c in "/\\.")

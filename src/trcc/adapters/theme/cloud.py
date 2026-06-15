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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ...core.errors import HttpFetchError
from ...core.ports import HttpFetcher

log = logging.getLogger(__name__)


# =========================================================================
# Categories — static data (matches legacy theme_cloud.CATEGORIES)
# =========================================================================


@dataclass(frozen=True, slots=True)
class CloudCategory:
    """One entry in the cloud catalog's category table."""
    prefix: str
    name: str
    count: int


_CATEGORIES: tuple[CloudCategory, ...] = (
    CloudCategory("a", "Gallery",   82),
    CloudCategory("b", "Tech",      25),
    CloudCategory("c", "HUD",       72),
    CloudCategory("d", "Light",     55),
    CloudCategory("e", "Nature",    54),
    CloudCategory("y", "Aesthetic", 10),
)


@dataclass(frozen=True, slots=True)
class CloudThemeEntry:
    """One theme in the catalog — id + which category it lives in."""
    id: str               # e.g. "a001"
    category: str         # prefix, e.g. "a"
    category_name: str    # human label, e.g. "Gallery"


Server = Literal["china", "international"]


_SERVERS: dict[Server, str] = {
    "china":         "http://www.czhorde.com/tr/bj{resolution}/",
    "international": "http://www.czhorde.cc/tr/bj{resolution}/",
}


# =========================================================================
# Catalog
# =========================================================================


class CzhordeCatalog:
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
        self._http = http
        self._cache_dir = cache_dir
        self._resolution = resolution
        self._preferred = preferred_server

    # ── Static reads ──────────────────────────────────────────────────

    @staticmethod
    def categories() -> tuple[CloudCategory, ...]:
        return _CATEGORIES

    def list_themes(self, category: str = "all") -> list[CloudThemeEntry]:
        """Enumerate theme IDs in *category* (or all categories)."""
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
        return self._fetch_cached(theme_id, ".mp4", resolution or self._resolution)

    def download_preview(
        self, theme_id: str, resolution: str | None = None,
    ) -> Path:
        """Fetch ``<theme_id>.png`` (cached) and return its local path.

        Some entries don't have a PNG — caller catches HttpFetchError
        and falls back to extracting a still from the MP4 (or shows a
        placeholder).
        """
        return self._fetch_cached(theme_id, ".png", resolution or self._resolution)

    # ── Internals ─────────────────────────────────────────────────────

    def _fetch_cached(self, theme_id: str, suffix: str, resolution: str) -> Path:
        if not _is_safe_theme_id(theme_id):
            log.error("CzhordeCatalog: rejected invalid theme id %r", theme_id)
            raise ValueError(f"Invalid cloud theme id: {theme_id!r}")
        res_dir = resolution.replace("x", "")
        cache = self._cache_dir / res_dir
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / f"{theme_id}{suffix}"
        if target.is_file() and target.stat().st_size > 0:
            log.debug("CzhordeCatalog: cache hit %s", target)
            return target
        log.info("CzhordeCatalog: fetching %s%s @ %s (cache miss)",
                 theme_id, suffix, resolution)
        data = self._fetch_with_fallback(theme_id, suffix, resolution)
        target.write_bytes(data)
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
                return self._http.fetch(url, timeout_s=timeout_s)
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
        base = _SERVERS[server]
        res_dir = resolution.replace("x", "")
        base_url = base.replace("{resolution}", res_dir)
        return f"{base_url}{theme_id}{suffix}"


# =========================================================================
# Helpers
# =========================================================================


def _is_safe_theme_id(theme_id: str) -> bool:
    """Reject path-injecty / non-conforming IDs.

    Legacy IDs are always ``<lowercase-letter><3 digits>``; we accept
    that and reject anything that could navigate the filesystem.
    """
    if not (4 <= len(theme_id) <= 8):
        return False
    if not theme_id[0].isalpha() or not theme_id[1:].isalnum():
        return False
    return all(c not in theme_id for c in "/\\.")

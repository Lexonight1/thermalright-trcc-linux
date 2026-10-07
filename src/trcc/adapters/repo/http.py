"""``HttpFetcher`` implementation backed by ``urllib.request``.

Single GET, no redirects beyond what urllib does by default, no keep-alive
— this adapter intentionally stays small.  Anything fancier (parallel
downloads, retries, progress callbacks) is the caller's concern so the
port stays trivial to mock.
"""
from __future__ import annotations

import logging
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ...core.errors import HttpFetchError
from ...core.ports import HttpFetcher

log = logging.getLogger(__name__)


class OfflineHttpFetcher(HttpFetcher):
    """Fetches nothing, and says so -- for a test or dev run's platform.

    Fails the way no network fails (``HttpFetchError``), so every caller takes
    the path it already takes offline, and each refusal is in the log.
    """

    def fetch(
        self, url: str, timeout_s: float = 30.0, max_bytes: int | None = None,
    ) -> bytes:
        log.warning("OfflineHttpFetcher: refused GET %s (offline run)", url)
        raise HttpFetchError(f"offline: {url} was not fetched")


class UrllibHttpFetcher(HttpFetcher):
    """Minimal stdlib HTTP GET — keep this dependency-free."""

    _USER_AGENT = "trcc/1.0"

    def __init__(self, *, ssl_context: ssl.SSLContext | None = None) -> None:
        # Caller can hand in a stricter context (e.g. cert pinning); default
        # is the platform's default trust store via urllib.
        log.debug("__init__")
        self._ctx = ssl_context

    def fetch(
        self, url: str, timeout_s: float = 30.0, max_bytes: int | None = None,
    ) -> bytes:
        log.info("HTTP GET %s (timeout=%.1fs, max_bytes=%s)",
                 url, timeout_s, max_bytes)
        req = Request(url, headers={"User-Agent": self._USER_AGENT})
        try:
            with urlopen(req, timeout=timeout_s, context=self._ctx) as resp:
                status = getattr(resp, "status", 200)
                if status != 200:
                    log.warning("HTTP GET %s → status %s", url, status)
                    raise HttpFetchError(
                        f"GET {url} returned HTTP {status}",
                    )
                # One byte past the cap is enough to know it is over, and
                # never more than that is read.
                body = resp.read() if max_bytes is None else resp.read(max_bytes + 1)
                if max_bytes is not None and len(body) > max_bytes:
                    log.warning("HTTP GET %s → over the %d-byte cap, refused",
                                url, max_bytes)
                    raise HttpFetchError(
                        f"GET {url} is larger than {max_bytes} bytes",
                    )
                log.info("HTTP GET %s → %d bytes", url, len(body))
                return body
        except HTTPError as e:
            log.warning("HTTP GET %s → HTTPError %d", url, e.code)
            raise HttpFetchError(f"GET {url} → HTTP {e.code}") from e
        except URLError as e:
            log.warning("HTTP GET %s → URLError %s", url, e.reason)
            raise HttpFetchError(f"GET {url} → URL error: {e.reason}") from e
        except (TimeoutError, OSError) as e:
            log.warning("HTTP GET %s → %s: %s", url, type(e).__name__, e)
            raise HttpFetchError(f"GET {url} → {type(e).__name__}: {e}") from e

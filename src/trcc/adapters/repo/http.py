"""``HttpFetcher`` implementation backed by ``urllib.request``.

Single GET, no redirects beyond what urllib does by default -- each one
asked of the caller's ``allow`` when it passes one, every connection held to
a public address when it asks for ``public_only`` -- no keep-alive
— this adapter intentionally stays small.  Anything fancier (parallel
downloads, retries, progress callbacks) is the caller's concern so the
port stays trivial to mock.
"""
from __future__ import annotations

import http.client
import ipaddress
import logging
import socket
import ssl
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)

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
        allow: Callable[[str], bool] | None = None, public_only: bool = False,
    ) -> bytes:
        log.warning("OfflineHttpFetcher: refused GET %s (offline run)", url)
        raise HttpFetchError(f"offline: {url} was not fetched")


def _default_context() -> ssl.SSLContext:
    """The system's trust store, plus certifi's bundle when it is installed.

    macOS Python does not read the Keychain, and a PyInstaller build carries
    no system CA path, so HTTPS failed there until certifi's bundle was
    loaded (#109, 68d644af).  The cutover dropped that code with the legacy
    tree, and with nothing importing certifi PyInstaller stopped bundling
    it.  Additive and optional: the deb/rpm builds do not ship certifi, and
    the system store alone is right there.
    """
    ctx = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        log.info("_default_context: no certifi — the system trust store only")
        return ctx
    ctx.load_verify_locations(certifi.where())
    log.info("_default_context: system trust store + certifi %s",
             certifi.where())
    return ctx


class _CheckedRedirects(HTTPRedirectHandler):
    """urllib's redirects, each asked of *allow* before it is followed."""

    def __init__(self, allow: Callable[[str], bool]) -> None:
        log.debug("_CheckedRedirects.__init__")
        self._allow = allow

    def redirect_request(self, req: Request, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> Request | None:
        if not self._allow(newurl):
            log.warning("HTTP GET %s → redirect %d to %s refused", req.full_url,
                        code, newurl)
            fp.close()
            raise HttpFetchError(f"GET {req.full_url} redirected to {newurl}, "
                                 "which is not allowed")
        log.info("HTTP GET %s → redirect %d to %s", req.full_url, code, newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _is_public(address: str) -> bool:
    """Whether *address* is on the public internet -- not loopback, a private
    LAN, link-local (where a cloud's metadata service answers) or reserved.
    An IPv4 address written as IPv6 (``::ffff:127.0.0.1``) is judged as IPv4:
    Python does that itself only from 3.10.15 / 3.12.4 (gh-113171), and an
    older patch release -- Ubuntu 22.04's 3.10.12 -- is still installed."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    log.debug("_is_public: %s -> %s", address, ip.is_global)
    return ip.is_global


class _PublicOnly(http.client.HTTPConnection):
    """A connection that refuses an address off the public internet.

    Asked of the socket once it has connected, so it is the address the name
    really resolved to -- a lookup made first could be answered differently
    at connect time (DNS rebinding).  Through a proxy the socket's peer is the
    proxy and the proxy resolves the name: that hop is the user's, unchecked.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        log.debug("_PublicOnly.__init__: %s", self.host)
        self._through_proxy = False
        self._create_connection = self._connect_public

    def set_tunnel(self, host: str, port: int | None = None,
                   headers: Mapping[str, str] | None = None) -> None:
        log.debug("_PublicOnly.set_tunnel: %s through proxy %s", host, self.host)
        self._through_proxy = True
        super().set_tunnel(host, port, headers)

    def _connect_public(self, address: tuple[str, int], *args: Any,
                        **kwargs: Any) -> socket.socket:
        sock = socket.create_connection(address, *args, **kwargs)
        if self._through_proxy:
            log.info("_PublicOnly: through proxy %s -- the proxy resolves the "
                     "name, so its address is not checked", address[0])
            return sock
        peer = sock.getpeername()[0]
        if not _is_public(peer):
            sock.close()
            log.warning("_PublicOnly: %s resolved to %s, not a public address "
                        "-- refused", self.host, peer)
            raise HttpFetchError(f"{self.host} resolved to {peer}, which is not "
                                 "a public internet address")
        log.debug("_PublicOnly: %s -> %s", self.host, peer)
        return sock


class _PublicOnlyHTTPS(_PublicOnly, http.client.HTTPSConnection):
    """``_PublicOnly`` over TLS."""


class _PublicHTTPHandler(HTTPHandler):
    """urllib's http, every connection ``_PublicOnly``."""

    def http_open(self, req: Request) -> http.client.HTTPResponse:
        log.debug("_PublicHTTPHandler.http_open: %s", req.full_url)
        return self.do_open(_PublicOnly, req)


class _PublicHTTPSHandler(HTTPSHandler):
    """urllib's https, every connection ``_PublicOnlyHTTPS``."""

    def __init__(self, context: ssl.SSLContext) -> None:
        super().__init__(context=context)
        log.debug("_PublicHTTPSHandler.__init__")
        self._ctx = context

    def https_open(self, req: Request) -> http.client.HTTPResponse:
        log.debug("_PublicHTTPSHandler.https_open: %s", req.full_url)
        return self.do_open(_PublicOnlyHTTPS, req, context=self._ctx)


class UrllibHttpFetcher(HttpFetcher):
    """Minimal stdlib HTTP GET."""

    _USER_AGENT = "trcc/1.0"

    def __init__(self, *, ssl_context: ssl.SSLContext | None = None) -> None:
        # Caller can hand in a stricter context (e.g. cert pinning).
        log.debug("__init__")
        self._ctx = ssl_context or _default_context()

    def fetch(
        self, url: str, timeout_s: float = 30.0, max_bytes: int | None = None,
        allow: Callable[[str], bool] | None = None, public_only: bool = False,
    ) -> bytes:
        log.info("HTTP GET %s (timeout=%.1fs, max_bytes=%s, checked=%s, "
                 "public_only=%s)", url, timeout_s, max_bytes, allow is not None,
                 public_only)
        if allow is not None and not allow(url):
            log.warning("HTTP GET %s refused before sending", url)
            raise HttpFetchError(f"GET {url} is not allowed")
        req = Request(url, headers={"User-Agent": self._USER_AGENT})
        handlers: list[Any] = (
            [_PublicHTTPHandler(), _PublicHTTPSHandler(self._ctx)] if public_only
            else [HTTPSHandler(context=self._ctx)])
        if allow is not None:
            handlers.append(_CheckedRedirects(allow))
        try:
            with build_opener(*handlers).open(req, timeout=timeout_s) as resp:
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
            e.close()       # an error is a response too; 3.14 warns when one is left open
            log.warning("HTTP GET %s → HTTPError %d", url, e.code)
            raise HttpFetchError(f"GET {url} → HTTP {e.code}") from e
        except URLError as e:
            log.warning("HTTP GET %s → URLError %s", url, e.reason)
            raise HttpFetchError(f"GET {url} → URL error: {e.reason}") from e
        except (TimeoutError, OSError) as e:
            log.warning("HTTP GET %s → %s: %s", url, type(e).__name__, e)
            raise HttpFetchError(f"GET {url} → {type(e).__name__}: {e}") from e

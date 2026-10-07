"""HTTPS trusts the system store PLUS certifi's bundle when it is installed.

#109: macOS Python reads no Keychain and a PyInstaller build has no system CA
path, so every download failed CERTIFICATE_VERIFY_FAILED until certifi's
bundle was loaded.  The cutover dropped that code; nothing imported certifi,
so PyInstaller stopped bundling it.
"""
from __future__ import annotations

import ssl
import sys
from typing import Any

import pytest

from trcc.adapters.repo import http


class _Ctx:
    def __init__(self) -> None:
        self.loaded: list[Any] = []

    def load_verify_locations(self, path: Any) -> None:
        self.loaded.append(path)


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> _Ctx:
    made = _Ctx()
    monkeypatch.setattr(ssl, "create_default_context", lambda: made)
    return made


def test_the_default_context_loads_certifis_bundle(ctx: _Ctx) -> None:
    """MUTATION CHECK: drop the load and nothing is added to the store."""
    certifi = pytest.importorskip("certifi")

    http.UrllibHttpFetcher()

    assert ctx.loaded == [certifi.where()]


def test_without_certifi_the_system_store_is_used(
    ctx: _Ctx, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deb and rpm do not ship certifi; that must not break HTTPS."""
    monkeypatch.setitem(sys.modules, "certifi", None)      # import -> ImportError

    fetcher = http.UrllibHttpFetcher()

    assert fetcher._ctx is ctx and ctx.loaded == []


def test_a_context_handed_in_is_used_as_it_is(ctx: _Ctx) -> None:
    own = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    assert http.UrllibHttpFetcher(ssl_context=own)._ctx is own
    assert ctx.loaded == []

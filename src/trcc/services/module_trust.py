"""Which modules may be installed: the module index, signed and verified.

Two signed files, each ``{"signed": {...}, "signatures": [{"key", "sig"}]}``
with every signature over :func:`canonical` of ``signed``:

* ``root.json`` -- signed by a release key (``core.trust_keys.ROOT_KEYS``),
  names the keys that sign the index.  A new index key is a new root, not an
  app update.
* ``index.json`` -- signed by one of those, lists every module TRCC may
  install (``ModuleRelease``) and every release taken back (``Revocation``).

Both signatures are needed for an install, as on Android: the author signs
each release (:func:`author_statement`), and TRCC countersigns it by listing it
after review.  An update must carry the author key the module was installed
with -- so a stolen index key cannot swap code in, and a stolen author key
cannot skip the review.

Each file's ``version`` only rises: the caller passes the newest it has seen,
and an older file is refused (a rollback).  An expired index is still
authentic -- its revocations still count -- but installs nothing.  One index
lists every module, so files from different days cannot be mixed.

A signature says where a module came from and that it was reviewed -- not that
it is safe.  The review is the control; revocation is the backstop.

``dev/tools/sign_module_index.py`` writes these files with :func:`canonical`
and :func:`author_statement`, so both halves encode the same bytes.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any

from ..core.errors import UntrustedModule
from ..core.models import (
    MODULE_HOST,
    MODULE_KEY,
    ModuleAsks,
    ModuleIndex,
    ModuleRelease,
    Revocation,
    TrustRoot,
)
from ..core.ports import SignatureChecker
from ..core.trust_keys import ROOT_KEYS

log = logging.getLogger(__name__)

#: What each file says it is, inside the signed part: a root's signature
#: cannot be passed off as an index's.
ROOT_TYPE, INDEX_TYPE = "trcc-root", "trcc-index"
_SHA256 = re.compile(r"[0-9a-f]{64}")
#: A git commit: SHA-1, or SHA-256 in a repository that uses it.
_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_KEY_BYTES = 32


def canonical(signed: Mapping[str, Any]) -> bytes:
    """The bytes a signature covers: *signed* as JSON with sorted keys and no
    spaces, so the signer and every reader encode it the same."""
    data = json.dumps(signed, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    log.debug("module_trust.canonical: %d bytes", len(data))
    return data


def author_statement(key: str, version: str, sha256: str) -> bytes:
    """What a module's author signs: this bundle is release *version* of
    module *key* -- so a signed bundle cannot be listed under another name or
    version."""
    log.debug("module_trust.author_statement: %s %s %s", key, version, sha256)
    return f"trcc-module\n{key}\n{version}\n{sha256}\n".encode()


def _bytes(text: Any, where: str) -> bytes:
    """Base64 *text* decoded -- or ``UntrustedModule`` naming *where*."""
    log.debug("module_trust._bytes: %s", where)
    if not isinstance(text, str):
        raise UntrustedModule(f"{where}: not base64 text")
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as e:
        raise UntrustedModule(f"{where}: not base64 ({e})") from e


def _key(text: Any, where: str) -> bytes:
    """A raw Ed25519 public key from base64 *text*."""
    key = _bytes(text, where)
    log.debug("module_trust._key: %s %d bytes", where, len(key))
    if len(key) != _KEY_BYTES:
        raise UntrustedModule(f"{where}: {len(key)} bytes, not an Ed25519 key")
    return key


class _Fields:
    """Typed reads of one JSON object; each failure names the file and field."""

    def __init__(self, data: Any, where: str) -> None:
        log.debug("_Fields: %s", where)
        if not isinstance(data, dict):
            raise UntrustedModule(f"{where}: not a JSON object")
        self._data: dict[str, Any] = data
        self._where = where

    def _fail(self, name: str, wanted: str) -> UntrustedModule:
        log.warning("module_trust: %s: %s is not %s", self._where, name, wanted)
        return UntrustedModule(f"{self._where}: {name} is not {wanted}")

    def text(self, name: str, pattern: re.Pattern[str] | None = None) -> str:
        value = self._data.get(name)
        log.debug("_Fields.text: %s.%s", self._where, name)
        if not isinstance(value, str) or not value or (
                pattern is not None and not pattern.fullmatch(value)):
            raise self._fail(name, "the text it should be")
        return value

    def number(self, name: str) -> int:
        value = self._data.get(name)
        log.debug("_Fields.number: %s.%s = %r", self._where, name, value)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise self._fail(name, "a whole number from 1")
        return value

    def texts(self, name: str) -> tuple[str, ...]:
        value = self._data.get(name, [])
        log.debug("_Fields.texts: %s.%s", self._where, name)
        if not isinstance(value, list) or not all(
                isinstance(v, str) and v for v in value):
            raise self._fail(name, "a list of names")
        return tuple(value)

    def time(self, name: str) -> datetime:
        """An ISO 8601 time with its zone -- ``2027-01-01T00:00:00Z``.  The
        ``Z`` is spelled out for Python 3.10, whose parser lacks it."""
        value = self.text(name)
        log.debug("_Fields.time: %s.%s = %s", self._where, name, value)
        if value.endswith("Z"):
            value = value.removesuffix("Z") + "+00:00"
        try:
            when = datetime.fromisoformat(value)
        except ValueError:
            raise self._fail(name, "an ISO 8601 time") from None
        if when.tzinfo is None:
            raise self._fail(name, "a time with its zone")
        return when

    def objects(self, name: str) -> list[Any]:
        value = self._data.get(name, [])
        log.debug("_Fields.objects: %s.%s", self._where, name)
        if not isinstance(value, list):
            raise self._fail(name, "a list")
        return value

    def mapping(self, name: str) -> dict[str, Any]:
        value = self._data.get(name, {})
        log.debug("_Fields.mapping: %s.%s", self._where, name)
        if not isinstance(value, dict):
            raise self._fail(name, "a JSON object")
        return value


def parse_release(key: str, entry: Any) -> ModuleRelease:
    """The index's entry for module *key*, read -- not yet checked against a
    bundle (:meth:`ModuleTrust.check_release`)."""
    log.debug("module_trust.parse_release: %s", key)
    if not MODULE_KEY.fullmatch(key):
        raise UntrustedModule(f"index.json: {key!r} is not a module key")
    f = _Fields(entry, f"index.json module {key}")
    url = f.text("url")
    if not url.startswith("https://"):
        raise UntrustedModule(f"index.json module {key}: url is not https")
    author_key = f.text("author_key")
    _key(author_key, f"index.json module {key} author_key")
    return ModuleRelease(
        key=key, version=f.text("version"), url=url, size=f.number("size"),
        sha256=f.text("sha256", _SHA256), min_trcc=f.text("min_trcc"),
        commit=f.text("commit", _COMMIT),
        asks=ModuleAsks(capabilities=f.texts("capabilities"), network_hosts=_hosts(f, key)),
        author_key=author_key, author_sig=f.text("author_sig"), title=_optional(entry, "title"),
        summary=_optional(entry, "summary"), author=_optional(entry, "author"))


def _optional(entry: dict[str, Any], name: str) -> str:
    """Text the list only shows -- "" when absent or not text."""
    value = entry.get(name, "")
    log.debug("module_trust._optional: %s", name)
    return value if isinstance(value, str) else ""


def _hosts(f: _Fields, key: str) -> tuple[str, ...]:
    """The sites module *key*'s release may fetch from -- public hostnames."""
    hosts = f.texts("network_hosts")
    log.debug("module_trust._hosts: %s %s", key, hosts)
    if bad := [h for h in hosts if not MODULE_HOST.fullmatch(h)]:
        raise UntrustedModule(f"index.json module {key}: {bad[0]!r} is not a "
                              "public hostname")
    return hosts


def _revocation(entry: Any) -> Revocation:
    """One entry of the index's ``revoked`` list."""
    log.debug("module_trust._revocation")
    f = _Fields(entry, "index.json revoked")
    sha256 = entry.get("sha256", "")
    if sha256 != "":
        sha256 = f.text("sha256", _SHA256)
    return Revocation(key=f.text("key", MODULE_KEY), reason=f.text("reason"),
                      sha256=sha256)


class ModuleTrust:
    """Opens the root and the index, and checks a bundle against them.
    Every refusal is an ``UntrustedModule`` saying why."""

    def __init__(self, checker: SignatureChecker,
                 root_keys: Iterable[str] = ROOT_KEYS.values()) -> None:
        self._checker = checker
        self._root_keys = tuple(_key(k, "root key") for k in root_keys)
        log.info("ModuleTrust: %d root key(s)", len(self._root_keys))

    def open_root(self, data: bytes, seen: int = 0) -> TrustRoot:
        """``root.json`` *data*, verified -- refused when older than version
        *seen*, the newest this machine has accepted."""
        log.info("ModuleTrust.open_root: %d bytes, newest seen %d", len(data), seen)
        f = _Fields(self._open(data, ROOT_TYPE, self._root_keys, "root.json"), "root.json")
        root = TrustRoot(version=f.number("version"), expires=f.time("expires"),
                         index_keys=f.texts("index_keys"))
        if not root.index_keys:
            raise UntrustedModule("root.json: names no index key")
        for i, key in enumerate(root.index_keys):
            _key(key, f"root.json index_keys[{i}]")
        self._not_older(root.version, seen, "root.json")
        log.info("ModuleTrust.open_root: version %d, %d index key(s), expires %s",
                 root.version, len(root.index_keys), root.expires.isoformat())
        return root

    def open_index(self, data: bytes, root: TrustRoot, seen: int = 0) -> ModuleIndex:
        """``index.json`` *data*, verified against *root* -- refused when
        older than version *seen*.  It expires with its root, if sooner."""
        log.info("ModuleTrust.open_index: %d bytes, root %d, newest seen %d",
                 len(data), root.version, seen)
        keys = tuple(_key(k, "root.json index key") for k in root.index_keys)
        f = _Fields(self._open(data, INDEX_TYPE, keys, "index.json"), "index.json")
        version = f.number("version")
        self._not_older(version, seen, "index.json")
        releases = {key: parse_release(key, entry)
                    for key, entry in f.mapping("modules").items()}
        index = ModuleIndex(
            version=version, expires=min(f.time("expires"), root.expires),
            releases=MappingProxyType(releases),
            revoked=tuple(_revocation(r) for r in f.objects("revoked")))
        log.info("ModuleTrust.open_index: version %d, %d module(s) %s, %d revoked, "
                 "expires %s", index.version, len(releases), sorted(releases),
                 len(index.revoked), index.expires.isoformat())
        return index

    def is_current(self, index: ModuleIndex, now: datetime) -> bool:
        """Whether *index* may still install and update -- not past expiry."""
        current = now < index.expires
        log.info("ModuleTrust.is_current: index %d expires %s -> %s", index.version,
                 index.expires.isoformat(), current)
        return current

    def revoked(self, index: ModuleIndex, key: str, sha256: str) -> str | None:
        """Why *index* takes back module *key*'s bundle *sha256*, or None."""
        reason = next((r.reason for r in index.revoked
                       if r.key == key and r.sha256 in ("", sha256)), None)
        log.debug("ModuleTrust.revoked: %s %s -> %r", key, sha256, reason)
        return reason

    def check_bundle(self, index: ModuleIndex, key: str, data: bytes,
                     now: datetime, pinned_author: str | None = None) -> ModuleRelease:
        """The release bundle *data* is, when TRCC may install it: listed in a
        current *index*, not taken back, and passing :meth:`check_release`."""
        log.info("ModuleTrust.check_bundle: %s, %d bytes", key, len(data))
        release = index.releases.get(key)
        if release is None:
            raise UntrustedModule(f"{key}: not in the module index")
        if not self.is_current(index, now):
            raise UntrustedModule(f"the module index expired on "
                                  f"{index.expires:%Y-%m-%d} -- nothing new installs")
        self.check_release(release, data, pinned_author)
        if (reason := self.revoked(index, key, release.sha256)) is not None:
            raise UntrustedModule(f"{key} {release.version}: taken back -- {reason}")
        log.info("ModuleTrust.check_bundle: %s %s may install", key, release.version)
        return release

    def check_release(self, release: ModuleRelease, data: bytes,
                      pinned_author: str | None = None) -> None:
        """Refuse *data* unless it is the very bundle *release* lists, signed
        by its author -- and, for an update, by *pinned_author*, the key the
        installed copy came with."""
        name = f"{release.key} {release.version}"
        log.info("ModuleTrust.check_release: %s, %d bytes, pinned author %s",
                 name, len(data), pinned_author is not None)
        if len(data) != release.size:
            raise UntrustedModule(f"{name}: {len(data)} bytes, the index lists "
                                  f"{release.size}")
        if hashlib.sha256(data).hexdigest() != release.sha256:
            raise UntrustedModule(f"{name}: not the bundle the index lists")
        if pinned_author is not None and pinned_author != release.author_key:
            raise UntrustedModule(f"{name}: signed by a different author key than "
                                  "the installed copy")
        if not self._checker.verify(
                _key(release.author_key, f"{name} author_key"),
                author_statement(release.key, release.version, release.sha256),
                _bytes(release.author_sig, f"{name} author_sig")):
            raise UntrustedModule(f"{name}: not signed by its author")
        log.info("ModuleTrust.check_release: %s passes", name)

    def _open(self, data: bytes, kind: str, trusted: tuple[bytes, ...],
              where: str) -> Any:
        """The signed part of file *where*, when a *trusted* key signed it and
        it says it is a *kind*."""
        log.debug("ModuleTrust._open: %s, %d trusted key(s)", where, len(trusted))
        try:
            envelope = json.loads(data)
        except (ValueError, UnicodeDecodeError) as e:
            raise UntrustedModule(f"{where}: not JSON ({e})") from e
        f = _Fields(envelope, where)
        signed = f.mapping("signed")
        message = canonical(signed)
        if not any(self._signs(sig, message, trusted)
                   for sig in f.objects("signatures")):
            raise UntrustedModule(f"{where}: not signed by a key TRCC trusts")
        if signed.get("type") != kind:
            raise UntrustedModule(f"{where}: says it is {signed.get('type')!r}, not {kind!r}")
        return signed

    def _signs(self, sig: Any, message: bytes, trusted: tuple[bytes, ...]) -> bool:
        """Whether one ``signatures`` entry is a *trusted* key signing
        *message*.  An entry from a key TRCC does not know is passed over."""
        try:
            key, signature = _bytes(sig.get("key"), "key"), _bytes(sig.get("sig"), "sig")
        except (UntrustedModule, AttributeError):
            log.debug("ModuleTrust._signs: an unreadable entry, passed over")
            return False
        if key not in trusted:
            log.debug("ModuleTrust._signs: a key TRCC does not trust, passed over")
            return False
        good = self._checker.verify(key, message, signature)
        log.info("ModuleTrust._signs: trusted key %s -> %s",
                 base64.b64encode(key).decode()[:8], "good" if good else "BAD")
        return good

    @staticmethod
    def _not_older(version: int, seen: int, what: str) -> None:
        """Refuse *what* at *version* when this machine has accepted *seen*."""
        log.debug("ModuleTrust._not_older: %s %d, seen %d", what, version, seen)
        if version < seen:
            raise UntrustedModule(f"{what}: version {version} is older than "
                                  f"{seen}, already accepted -- a rollback")

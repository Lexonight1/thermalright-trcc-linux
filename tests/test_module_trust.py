"""The module index: signed by the tool, opened by the app -- both halves.

Every file here is written by ``dev/tools/sign_module_index.py`` and read by
``services/module_trust.py``, so a test passes only when the writer and the
reader agree.  The real keys never appear: each test makes throwaway pairs.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_public_key,
)

from trcc.adapters.modules import Ed25519Checker
from trcc.core.errors import UntrustedModule
from trcc.core.models import ModuleIndex
from trcc.core.trust_keys import ROOT_KEYS
from trcc.services.module_trust import (
    INDEX_TYPE,
    ROOT_TYPE,
    ModuleTrust,
    canonical,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "tools"))
import sign_module_index as tool

_REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
COMMIT = "0123456789abcdef0123456789abcdef01234567"
BUNDLE = b"weather module, release 3"


class Keys:
    """Throwaway PEM pairs on disk, as the real ones are kept."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def __getitem__(self, name: str) -> tuple[Path, Path]:
        private, public = (self._folder / f"{name}.key", self._folder / f"{name}.pub")
        if not private.exists():
            tool.keygen(self._folder / name)
        return private, public

    def raw(self, name: str) -> str:
        return tool.public_key(self[name][1])


@pytest.fixture
def keys(tmp_path: Path) -> Keys:
    return Keys(tmp_path)


@pytest.fixture
def trust(keys: Keys) -> ModuleTrust:
    """The app's verifier, trusting the throwaway ``root`` key."""
    return ModuleTrust(Ed25519Checker(), [keys.raw("root")])


@pytest.fixture
def root_file(keys: Keys, trust: ModuleTrust) -> bytes:
    return tool.make_root(1, [keys.raw("index")], 365, NOW, keys["root"][0], trust)


def _entry(keys: Keys, bundle: bytes = BUNDLE, version: str = "3",
           author: str = "author", **changes: Any) -> dict[str, Any]:
    """What ``add`` is told about a release, signed by *author*."""
    return {"version": version, "url": "https://example.org/weather-3.zip",
            "commit": COMMIT, "min_trcc": "9.12.0",
            "capabilities": ["events", "readings", "network"],
            "network_hosts": ["api.example.com"],
            "author_key": keys.raw(author),
            "author_sig": tool.author_sign(bundle, "weather", version, keys[author][0]),
            **changes}


def _index_file(keys: Keys, trust: ModuleTrust, root_file: bytes,
                signed: dict[str, Any] | None = None) -> bytes:
    """``index.json`` listing the weather release, as the tool signs it."""
    if signed is None:
        signed = tool.add(tool._signed(Path("/nonexistent")), "weather", BUNDLE,
                          _entry(keys), trust)
    return tool.reissue(signed, trust.open_root(root_file), 90, NOW,
                        keys["index"][0], trust)


def _opened(keys: Keys, trust: ModuleTrust, root_file: bytes) -> ModuleIndex:
    return trust.open_index(_index_file(keys, trust, root_file),
                            trust.open_root(root_file))


def _resign(data: bytes, key_file: Path, change: Any) -> bytes:
    """*data*'s signed part changed by *change*, then signed again by
    *key_file* -- a file a trusted key really did sign."""
    signed = json.loads(data)["signed"]
    change(signed)
    return tool.envelope(signed, key_file)


def _tampered(data: bytes, change: Any) -> bytes:
    """*data*'s signed part changed by *change*, the old signature kept."""
    body = json.loads(data)
    change(body["signed"])
    return json.dumps(body).encode()


# ── the pinned keys ───────────────────────────────────────────────────────


def test_the_pinned_root_keys_are_the_published_release_keys() -> None:
    """``core/trust_keys.py`` spells out what ``packaging/keys/*.pub``
    publishes.  Two copies of one fact, so this keeps them one."""
    published = {pub.stem: base64.b64encode(
        load_pem_public_key(pub.read_bytes()).public_bytes(
            Encoding.Raw, PublicFormat.Raw)).decode()
        for pub in sorted((_REPO / "packaging" / "keys").glob("*.pub"))}
    assert published == ROOT_KEYS


def test_the_app_trusts_both_release_keys_by_default() -> None:
    """The spare is there to retire a lost main key, so it must count."""
    assert ModuleTrust(Ed25519Checker())._root_keys == tuple(
        base64.b64decode(k) for k in ROOT_KEYS.values())


# ── the round trip ────────────────────────────────────────────────────────


def test_what_the_tool_signs_the_app_installs(keys: Keys, trust: ModuleTrust,
                                              root_file: bytes) -> None:
    index = _opened(keys, trust, root_file)
    release = trust.check_bundle(index, "weather", BUNDLE, NOW)
    assert (release.key, release.version, release.size, release.sha256,
            release.capabilities, release.commit) == (
        "weather", "3", len(BUNDLE), hashlib.sha256(BUNDLE).hexdigest(),
        ("events", "readings", "network"), COMMIT)
    assert release.network_hosts == ("api.example.com",)
    assert index.version == 1
    assert index.expires == NOW + timedelta(days=90)


def test_an_update_by_the_same_author_installs(keys: Keys, trust: ModuleTrust,
                                               root_file: bytes) -> None:
    """Key continuity passes when the key is the one installed with."""
    index = _opened(keys, trust, root_file)
    assert trust.check_bundle(index, "weather", BUNDLE, NOW,
                              pinned_author=keys.raw("author")).version == "3"


def test_a_signature_from_a_key_nobody_trusts_is_passed_over(
        keys: Keys, trust: ModuleTrust, root_file: bytes) -> None:
    """An extra, unknown signature beside the good one changes nothing."""
    body = json.loads(_index_file(keys, trust, root_file))
    stranger = Ed25519PrivateKey.generate()
    body["signatures"].insert(0, {"key": tool._raw(stranger),
                                  "sig": base64.b64encode(b"x" * 64).decode()})
    body["signatures"].insert(0, {"key": "not base64!", "sig": 7})
    body["signatures"].insert(0, "not an object")
    index = trust.open_index(json.dumps(body).encode(), trust.open_root(root_file))
    assert sorted(index.releases) == ["weather"]


# ── who signed it ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("change", [
    pytest.param(lambda s: s["modules"]["weather"].update(size=99), id="size"),
    pytest.param(lambda s: s["modules"]["weather"].update(
        url="https://evil.example/w.zip"), id="url"),
    pytest.param(lambda s: s["modules"]["weather"].update(
        capabilities=["events", "readings", "password"]), id="capabilities"),
    pytest.param(lambda s: s["modules"]["weather"].update(
        network_hosts=["api.example.com", "evil.example.net"]), id="network-hosts"),
    pytest.param(lambda s: s.update(version=99), id="version"),
    pytest.param(lambda s: s.update(revoked=[]), id="revoked"),
    pytest.param(lambda s: s["modules"].clear(), id="modules"),
])
def test_any_change_to_a_signed_index_is_refused(
        keys: Keys, trust: ModuleTrust, root_file: bytes, change: Any) -> None:
    signed_once = tool.revoke(tool.add(tool._signed(Path("/nonexistent")), "weather",
                                       BUNDLE, _entry(keys), trust),
                              "other", "gone")
    data = _index_file(keys, trust, root_file, signed_once)
    with pytest.raises(UntrustedModule) as e:
        trust.open_index(_tampered(data, change), trust.open_root(root_file))
    assert str(e.value) == "index.json: not signed by a key TRCC trusts"


def test_an_index_signed_by_a_key_the_root_does_not_name_is_refused(
        keys: Keys, trust: ModuleTrust, root_file: bytes) -> None:
    """The root key itself may not sign the index -- only the keys it names."""
    data = _resign(_index_file(keys, trust, root_file), keys["root"][0],
                   lambda s: None)
    with pytest.raises(UntrustedModule) as e:
        trust.open_index(data, trust.open_root(root_file))
    assert str(e.value) == "index.json: not signed by a key TRCC trusts"


def test_a_root_signed_by_an_untrusted_key_is_refused(
        keys: Keys, trust: ModuleTrust) -> None:
    impostor = tool.make_root(1, [keys.raw("index")], 365, NOW, keys["thief"][0],
                              ModuleTrust(Ed25519Checker(), [keys.raw("thief")]))
    with pytest.raises(UntrustedModule) as e:
        trust.open_root(impostor)
    assert str(e.value) == "root.json: not signed by a key TRCC trusts"


def test_a_root_cannot_be_passed_off_as_an_index(keys: Keys) -> None:
    """A root naming its own key as an index key: its signature is good for
    open_index, and only the type inside the signed part refuses it."""
    trust = ModuleTrust(Ed25519Checker(), [keys.raw("root")])
    root_file = tool.make_root(1, [keys.raw("root")], 365, NOW, keys["root"][0], trust)
    with pytest.raises(UntrustedModule) as e:
        trust.open_index(root_file, trust.open_root(root_file))
    assert str(e.value) == f"index.json: says it is {ROOT_TYPE!r}, not {INDEX_TYPE!r}"


def test_the_signed_bytes_are_the_canonical_encoding() -> None:
    """Sorted keys, no spaces, UTF-8 -- the same bytes on every machine."""
    assert canonical({"b": 1, "a": ["ü", {"d": 2, "c": 3}]}) == (
        '{"a":["ü",{"c":3,"d":2}],"b":1}'.encode())


# ── rollback and freshness ────────────────────────────────────────────────


def test_an_older_index_is_refused_as_a_rollback(
        keys: Keys, trust: ModuleTrust, root_file: bytes) -> None:
    data = _index_file(keys, trust, root_file)
    root = trust.open_root(root_file)
    assert trust.open_index(data, root, seen=1).version == 1   # the same file again
    with pytest.raises(UntrustedModule) as e:
        trust.open_index(data, root, seen=2)
    assert str(e.value) == ("index.json: version 1 is older than 2, already "
                            "accepted -- a rollback")


def test_an_older_root_is_refused_as_a_rollback(trust: ModuleTrust,
                                                root_file: bytes) -> None:
    with pytest.raises(UntrustedModule) as e:
        trust.open_root(root_file, seen=2)
    assert str(e.value) == ("root.json: version 1 is older than 2, already "
                            "accepted -- a rollback")


def test_an_expired_index_installs_nothing_but_still_revokes(
        keys: Keys, trust: ModuleTrust, root_file: bytes) -> None:
    """Authentic but stale: its revocations only take things away, so they
    still count; nothing new installs."""
    signed = tool.revoke(tool.add(tool._signed(Path("/nonexistent")), "weather",
                                  BUNDLE, _entry(keys), trust), "weather", "bad")
    index = trust.open_index(_index_file(keys, trust, root_file, signed),
                             trust.open_root(root_file))
    later = NOW + timedelta(days=91)
    assert not trust.is_current(index, later)
    assert trust.revoked(index, "weather", hashlib.sha256(BUNDLE).hexdigest()) == "bad"
    with pytest.raises(UntrustedModule) as e:
        trust.check_bundle(index, "weather", BUNDLE, later)
    assert str(e.value) == ("the module index expired on 2027-01-21 -- nothing "
                            "new installs")


def test_the_index_expires_with_its_root(keys: Keys, trust: ModuleTrust) -> None:
    root_file = tool.make_root(1, [keys.raw("index")], 30, NOW, keys["root"][0], trust)
    index = _opened(keys, trust, root_file)
    assert index.expires == NOW + timedelta(days=30)
    assert not trust.is_current(index, NOW + timedelta(days=31))


# ── the bundle ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("bundle", "message"), [
    (BUNDLE + b"!", f"weather 3: {len(BUNDLE) + 1} bytes, the index lists {len(BUNDLE)}"),
    (BUNDLE.upper(), "weather 3: not the bundle the index lists"),
])
def test_a_bundle_that_is_not_the_listed_one_is_refused(
        keys: Keys, trust: ModuleTrust, root_file: bytes, bundle: bytes,
        message: str) -> None:
    with pytest.raises(UntrustedModule) as e:
        trust.check_bundle(_opened(keys, trust, root_file), "weather", bundle, NOW)
    assert str(e.value) == message


def test_a_module_the_index_does_not_list_is_refused(
        keys: Keys, trust: ModuleTrust, root_file: bytes) -> None:
    with pytest.raises(UntrustedModule) as e:
        trust.check_bundle(_opened(keys, trust, root_file), "karmaviz", BUNDLE, NOW)
    assert str(e.value) == "karmaviz: not in the module index"


def test_an_update_from_a_different_author_key_is_refused(
        keys: Keys, trust: ModuleTrust, root_file: bytes) -> None:
    """Key continuity: the index lists it, but not with the key the
    installed copy came with -- a stolen index key cannot swap code in."""
    with pytest.raises(UntrustedModule) as e:
        trust.check_bundle(_opened(keys, trust, root_file), "weather", BUNDLE, NOW,
                           pinned_author=keys.raw("thief"))
    assert str(e.value) == ("weather 3: signed by a different author key than "
                            "the installed copy")


@pytest.mark.parametrize("entry", [
    pytest.param({"author_sig_from": "thief"}, id="another-key-signed-it"),
    pytest.param({"signed_version": "2"}, id="signed-as-another-version"),
])
def test_a_release_its_author_did_not_sign_is_refused(
        keys: Keys, trust: ModuleTrust, root_file: bytes,
        entry: dict[str, str]) -> None:
    """Listed and countersigned, but the author's signature is for another
    key or another version: the index alone cannot install code."""
    signer = entry.get("author_sig_from", "author")
    sig = tool.author_sign(BUNDLE, "weather", entry.get("signed_version", "3"),
                           keys[signer][0])
    signed = tool._signed(Path("/nonexistent"))
    signed["modules"]["weather"] = {
        **_entry(keys), "author_sig": sig, "size": len(BUNDLE),
        "sha256": hashlib.sha256(BUNDLE).hexdigest()}
    index = trust.open_index(_index_file(keys, trust, root_file, signed),
                             trust.open_root(root_file))
    with pytest.raises(UntrustedModule) as e:
        trust.check_bundle(index, "weather", BUNDLE, NOW)
    assert str(e.value) == "weather 3: not signed by its author"


@pytest.mark.parametrize(("sha256", "revoked"), [
    ("", True), (hashlib.sha256(BUNDLE).hexdigest(), True), ("ab" * 32, False),
])
def test_a_revoked_release_is_refused(keys: Keys, trust: ModuleTrust,
                                      root_file: bytes, sha256: str,
                                      revoked: bool) -> None:
    """Every release of a module, or only the one with that hash."""
    signed = tool.revoke(tool.add(tool._signed(Path("/nonexistent")), "weather",
                                  BUNDLE, _entry(keys), trust),
                         "weather", "sends your readings away", sha256)
    index = trust.open_index(_index_file(keys, trust, root_file, signed),
                             trust.open_root(root_file))
    if not revoked:
        assert trust.check_bundle(index, "weather", BUNDLE, NOW).version == "3"
        return
    with pytest.raises(UntrustedModule) as e:
        trust.check_bundle(index, "weather", BUNDLE, NOW)
    assert str(e.value) == "weather 3: taken back -- sends your readings away"


# ── malformed files ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("change", "message"), [
    (lambda s: s["modules"].update({"../evil": s["modules"]["weather"]}),
     "index.json: '../evil' is not a module key"),
    (lambda s: s["modules"]["weather"].update(url="http://example.org/w.zip"),
     "index.json module weather: url is not https"),
    (lambda s: s["modules"]["weather"].update(sha256="ABC"),
     "index.json module weather: sha256 is not the text it should be"),
    (lambda s: s["modules"]["weather"].update(size=True),
     "index.json module weather: size is not a whole number from 1"),
    (lambda s: s["modules"]["weather"].update(commit="main"),
     "index.json module weather: commit is not the text it should be"),
    (lambda s: s["modules"]["weather"].update(author_key="AAAA"),
     "index.json module weather author_key: 3 bytes, not an Ed25519 key"),
    (lambda s: s.update(expires="2027-01-01T00:00:00"),
     "index.json: expires is not a time with its zone"),
    (lambda s: s.update(expires="soon"),
     "index.json: expires is not an ISO 8601 time"),
    (lambda s: s.update(revoked=[{"key": "weather", "reason": "x", "sha256": "z"}]),
     "index.json revoked: sha256 is not the text it should be"),
    (lambda s: s["modules"]["weather"].update(network_hosts=["192.168.1.1"]),
     "index.json module weather: '192.168.1.1' is not a public hostname"),
])
def test_a_signed_but_malformed_index_is_refused(
        keys: Keys, trust: ModuleTrust, root_file: bytes, change: Any,
        message: str) -> None:
    """Signed by the right key and still refused: a signature vouches for
    who wrote the file, not that it is well formed."""
    data = _resign(_index_file(keys, trust, root_file), keys["index"][0], change)
    with pytest.raises(UntrustedModule) as e:
        trust.open_index(data, trust.open_root(root_file))
    assert str(e.value) == message


@pytest.mark.parametrize("data", [b"", b"{nope", b"[]", "\udcff".encode("utf-8",
                                                                     "surrogatepass")])
def test_a_file_that_is_not_a_signed_json_object_is_refused(
        trust: ModuleTrust, data: bytes) -> None:
    with pytest.raises(UntrustedModule):
        trust.open_root(data)


def test_a_root_naming_no_index_key_is_refused(keys: Keys, trust: ModuleTrust) -> None:
    with pytest.raises(UntrustedModule) as e:
        tool.make_root(1, [], 365, NOW, keys["root"][0], trust)
    assert str(e.value) == "root.json: names no index key"


def test_the_z_suffix_reads_on_every_python() -> None:
    """``fromisoformat`` learned ``Z`` only in 3.11; we keep 3.10."""
    key = Ed25519PrivateKey.generate()
    trust = ModuleTrust(Ed25519Checker(), [tool._raw(key)])
    signed = {"type": ROOT_TYPE, "version": 1, "expires": "2027-01-01T00:00:00Z",
              "index_keys": [tool._raw(key)]}
    data = json.dumps({"signed": signed, "signatures": [{
        "key": tool._raw(key),
        "sig": base64.b64encode(key.sign(canonical(signed))).decode()}]}).encode()
    assert trust.open_root(data).expires == datetime(2027, 1, 1, tzinfo=timezone.utc)


# ── the checker ───────────────────────────────────────────────────────────


def test_the_checker_says_no_to_wrong_shapes_without_raising() -> None:
    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    good = key.sign(b"m")
    checker = Ed25519Checker()
    assert checker.verify(raw, b"m", good)
    assert not checker.verify(raw, b"n", good)
    assert not checker.verify(raw[:31], b"m", good)
    assert not checker.verify(raw, b"m", good[:63])


# ── the tool's own guards ─────────────────────────────────────────────────


def test_keygen_never_overwrites_a_key_and_keeps_it_owner_only(tmp_path: Path) -> None:
    private, _ = tool.keygen(tmp_path / "k")
    assert private.stat().st_mode & 0o777 == 0o600
    before = private.read_bytes()
    with pytest.raises(SystemExit):
        tool.keygen(tmp_path / "k")
    assert private.read_bytes() == before


def test_the_tool_refuses_to_list_an_update_from_another_author(
        keys: Keys, trust: ModuleTrust) -> None:
    signed = tool.add(tool._signed(Path("/nonexistent")), "weather", BUNDLE,
                      _entry(keys), trust)
    with pytest.raises(UntrustedModule) as e:
        tool.add(signed, "weather", BUNDLE, _entry(keys, author="thief"), trust)
    assert str(e.value) == ("weather 3: signed by a different author key than "
                            "the installed copy")


def test_the_tool_writes_nothing_the_app_would_refuse(
        keys: Keys, trust: ModuleTrust, root_file: bytes) -> None:
    """``reissue`` opens what it signed: signing with a key the root does
    not name stops before a file exists."""
    signed = tool.add(tool._signed(Path("/nonexistent")), "weather", BUNDLE,
                      _entry(keys), trust)
    with pytest.raises(UntrustedModule):
        tool.reissue(signed, trust.open_root(root_file), 90, NOW, keys["thief"][0],
                     trust)


def test_private_key_pem_round_trips(tmp_path: Path) -> None:
    """The tool reads the same PEM shape ``sign_release.py`` keeps."""
    key = Ed25519PrivateKey.generate()
    pem = tmp_path / "k.key"
    pem.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    assert tool._raw(tool.private_key(pem)) == tool._raw(key)

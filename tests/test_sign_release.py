"""dev/tools/sign_release.py -- the release's signed checksum list, offline.

The real keys never appear here: each test makes a throwaway pair."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "tools"))
import sign_release

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def keys(tmp_path: Path) -> tuple[Path, Path]:
    """A throwaway Ed25519 pair as PEM files, as the real ones are kept."""
    key = Ed25519PrivateKey.generate()
    private, public = tmp_path / "test.key", tmp_path / "test.pub"
    private.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8,
                                          NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    return private, public


@pytest.fixture
def release(tmp_path: Path) -> Path:
    """A downloaded release: three assets and an old published list."""
    folder = tmp_path / "release"
    folder.mkdir()
    for name, body in (("a.deb", b"deb"), ("b.whl", b"wheel"), ("c.exe", b"exe")):
        (folder / name).write_bytes(body)
    return folder


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def test_the_list_names_every_file_plainly(release: Path) -> None:
    """A ./noarch/ or ../dist/ in front of a name made `sha256sum -c` fail on
    every release up to 9.11.2."""
    (release / sign_release.SUMS).write_text("old\n")
    (release / sign_release.SIGNATURE).write_bytes(b"old")
    assert sign_release.checksums(release) == (
        f"{_digest(b'deb')}  a.deb\n"
        f"{_digest(b'wheel')}  b.whl\n"
        f"{_digest(b'exe')}  c.exe\n")


def test_an_old_list_is_checked_by_file_name(release: Path) -> None:
    published = (f"{_digest(b'deb')}  ./noarch/a.deb\n"
                 f"{_digest(b'wheel')}  ../dist/b.whl\n")
    assert sign_release.published_mismatches(release, published) == []
    tampered = published.replace(_digest(b"wheel"), _digest(b"other"))
    assert sign_release.published_mismatches(release, tampered) == ["b.whl"]
    missing = f"{_digest(b'x')}  ../dist/gone.tar.gz\n"
    assert sign_release.published_mismatches(release, missing) == ["gone.tar.gz"]


def test_a_signature_verifies_and_a_changed_list_does_not(keys: tuple[Path, Path]) -> None:
    """MUTATION CHECK: make ``verify`` return True on InvalidSignature."""
    private, public = keys
    listing = b"abc  a.deb\n"
    signature = sign_release.sign(listing, private)
    assert sign_release.verify(listing, signature, public)
    assert not sign_release.verify(b"abd  a.deb\n", signature, public)


def test_another_key_does_not_verify(keys: tuple[Path, Path], tmp_path: Path) -> None:
    private, _public = keys
    other = Ed25519PrivateKey.generate().public_key()
    other_pub = tmp_path / "other.pub"
    other_pub.write_bytes(other.public_bytes(Encoding.PEM,
                                             PublicFormat.SubjectPublicKeyInfo))
    signature = sign_release.sign(b"list", private)
    assert not sign_release.verify(b"list", signature, other_pub)


def test_the_published_keys_are_public_ed25519_keys_only() -> None:
    """The repo carries the public halves; a private key here would be a leak."""
    keys = sorted((_REPO / "packaging" / "keys").iterdir())
    assert [k.name for k in keys] == ["trcc-release-main.pub",
                                      "trcc-release-spare.pub"]
    for key in keys:
        text = key.read_text()
        assert text.startswith("-----BEGIN PUBLIC KEY-----\n")
        assert "PRIVATE" not in text
    assert sign_release.DEFAULT_PUB == _REPO / "packaging/keys/trcc-release-main.pub"

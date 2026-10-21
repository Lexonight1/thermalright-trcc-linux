#!/usr/bin/env python3
"""Write and sign a release's complete SHA256SUMS.txt -- on the maintainer's machine.

Usage::

    PYTHONPATH=src python3.12 dev/tools/sign_release.py v9.11.2            # sign + upload
    PYTHONPATH=src python3.12 dev/tools/sign_release.py v9.11.2 --no-upload

Run it after every workflow of the release has finished.  It must run here,
not in CI, for two reasons:

* the Windows and macOS installers reach the release after ``release.yml``
  writes its checksum list, so only a list made afterwards can name them;
* the private key never leaves this machine -- a stolen GitHub token or a
  compromised Action can replace a file and its hash, but cannot sign.

It downloads every asset into an empty temporary folder (hashed, never run),
checks the hashes the release already published (by file name: lists from
before 9.11.3 named CI's build folders, ``./noarch/``, ``../dist/``), writes
every file into one list with plain names, signs it with Ed25519, checks the
signature against the PUBLISHED public key, and uploads list + signature.

Users verify with plain OpenSSL::

    openssl pkeyutl -verify -pubin -inkey trcc-release-main.pub -rawin \\
        -in SHA256SUMS.txt -sigfile SHA256SUMS.txt.sig
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)

REPO = "Lexonight1/thermalright-trcc-linux"
SUMS = "SHA256SUMS.txt"
SIGNATURE = f"{SUMS}.sig"
#: Outside the repo, readable only by the maintainer.
DEFAULT_KEY = Path.home() / ".config/trcc-signing/trcc-release-main.key"
#: What users verify with -- the published half, in the repo.
DEFAULT_PUB = Path(__file__).resolve().parents[2] / "packaging/keys/trcc-release-main.pub"


def sha256(path: Path) -> str:
    """*path*'s SHA-256, read in chunks: the installers are ~120 MB."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checksums(folder: Path) -> str:
    """Every file in *folder* -- but the list and its signature -- as
    ``sha256sum`` writes them: hash, two spaces, the plain file name."""
    files = sorted(p for p in folder.iterdir()
                   if p.is_file() and not p.name.startswith(SUMS))
    return "".join(f"{sha256(p)}  {p.name}\n" for p in files)


def published_mismatches(folder: Path, published: str) -> list[str]:
    """The entries of a published list that do not match the downloaded
    file of the same name -- compared by name, since old lists carried CI's
    folders in front of it."""
    bad = []
    for line in published.splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        path = folder / Path(name.strip()).name
        if not path.is_file() or sha256(path) != digest:
            bad.append(Path(name.strip()).name)
    return bad


def sign(data: bytes, key_file: Path) -> bytes:
    """An Ed25519 signature of *data* with the private key in *key_file*."""
    key = load_pem_private_key(key_file.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"{key_file} is not an Ed25519 private key")
    return key.sign(data)


def verify(data: bytes, signature: bytes, pub_file: Path) -> bool:
    """Whether *signature* is the public key in *pub_file* signing *data*."""
    key = load_pem_public_key(pub_file.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise SystemExit(f"{pub_file} is not an Ed25519 public key")
    try:
        key.verify(signature, data)
    except InvalidSignature:
        return False
    return True


def _gh(*args: str) -> None:
    """Run ``gh`` against the repo; any failure stops the tool."""
    subprocess.run(["gh", *args, "-R", REPO], check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write and sign a release's complete SHA256SUMS.txt.")
    parser.add_argument("tag", help="the release, e.g. v9.11.2")
    parser.add_argument("--key", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--pub", type=Path, default=DEFAULT_PUB)
    parser.add_argument("--no-upload", action="store_true",
                        help="write and check, but leave the release alone")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="trcc-sign-") as tmp:
        folder = Path(tmp)
        print(f"Downloading every asset of {args.tag} ...")
        _gh("release", "download", args.tag, "-D", str(folder))
        old = folder / SUMS
        if old.is_file():
            bad = published_mismatches(folder, old.read_text(encoding="utf-8"))
            if bad:
                print(f"STOP: the published hashes do not match: {bad}")
                return 1
            print("The hashes already published all match.")
        listing = checksums(folder)
        signature = sign(listing.encode(), args.key)
        if not verify(listing.encode(), signature, args.pub):
            print(f"STOP: {args.key} does not match the published {args.pub}")
            return 1
        print(listing, end="")
        print(f"{len(listing.splitlines())} files, signed and verified.")
        if args.no_upload:
            return 0
        old.write_text(listing, encoding="utf-8")
        (folder / SIGNATURE).write_bytes(signature)
        _gh("release", "upload", args.tag, str(old), str(folder / SIGNATURE),
            "--clobber")
        print(f"Uploaded {SUMS} and {SIGNATURE} to {args.tag}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

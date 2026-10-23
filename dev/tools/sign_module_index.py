#!/usr/bin/env python3
"""Write and sign the module index -- on the maintainer's machine, offline.

Usage::

    # once, and again to change the index key (the root is signed by a release key)
    PYTHONPATH=src python3.12 dev/tools/sign_module_index.py keygen ~/.config/trcc-signing/trcc-index
    PYTHONPATH=src python3.12 dev/tools/sign_module_index.py root root.json \\
        --version 1 --index-key ~/.config/trcc-signing/trcc-index.pub

    # an author signs a release of their module
    PYTHONPATH=src python3.12 dev/tools/sign_module_index.py author-sign weather-3.zip \\
        --key author.key --module weather --version 3

    # after reviewing it: list it, take one back, or just renew the expiry
    PYTHONPATH=src python3.12 dev/tools/sign_module_index.py add index.json --root root.json \\
        --module weather --version 3 --bundle weather-3.zip --url https://... \\
        --commit <sha> --min-trcc 9.12.0 --capabilities events,readings \\
        --author-key author.pub --author-sig <base64>
    PYTHONPATH=src python3.12 dev/tools/sign_module_index.py revoke index.json --root root.json \\
        --module weather [--sha256 <hex>] --reason "sends your readings away"
    PYTHONPATH=src python3.12 dev/tools/sign_module_index.py renew index.json --root root.json

Every change raises the file's version and sets its expiry (``--days``), signs
it, and then OPENS it the way TRCC does (``services/module_trust.py``) before
writing -- a file this writes is one the app accepts, or nothing is written.
The bytes signed are the verifier's own ``canonical`` and
``author_statement``, so the two halves cannot encode them differently.

``add`` refuses a release whose author signature does not check, and an update
signed by a different author key than the release it replaces -- key
continuity, which TRCC enforces again on every machine.

Private keys are PEM files like the release keys (``sign_release.py``) and
never leave this machine.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)
from sign_release import sign

from trcc.adapters.modules import Ed25519Checker
from trcc.core.errors import UntrustedModule
from trcc.core.models import TrustRoot
from trcc.core.trust_keys import ROOT_KEYS
from trcc.services.module_trust import (
    INDEX_TYPE,
    ROOT_TYPE,
    ModuleTrust,
    author_statement,
    canonical,
    parse_release,
)

KEYS = Path.home() / ".config/trcc-signing"
#: A root is signed with a release key; the index with the index key.
DEFAULT_ROOT_KEY = KEYS / "trcc-release-main.key"
DEFAULT_INDEX_KEY = KEYS / "trcc-index.key"


def _raw(key: Ed25519PrivateKey) -> str:
    """*key*'s public half, raw and base64 -- how the files name a key."""
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def private_key(key_file: Path) -> Ed25519PrivateKey:
    """The Ed25519 private key in PEM *key_file*."""
    key = load_pem_private_key(key_file.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"{key_file} is not an Ed25519 private key")
    return key


def public_key(pub_file: Path) -> str:
    """The Ed25519 public key in PEM *pub_file*, raw and base64."""
    key = load_pem_public_key(pub_file.read_bytes())
    return base64.b64encode(key.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def expiry(days: int, now: datetime) -> str:
    """*days* after *now*, spelled the way the files spell a time."""
    return (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(signed: dict[str, Any], key_file: Path) -> bytes:
    """*signed*, signed with the private key in *key_file*, as file bytes."""
    signature = sign(canonical(signed), key_file)
    body = {"signed": signed, "signatures": [
        {"key": _raw(private_key(key_file)),
         "sig": base64.b64encode(signature).decode()}]}
    return (json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n").encode("utf-8")


def keygen(stem: Path) -> tuple[Path, Path]:
    """A new Ed25519 pair: ``<stem>.key`` (owner-only) and ``<stem>.pub``."""
    private, public = stem.with_suffix(".key"), stem.with_suffix(".pub")
    if private.exists():
        raise SystemExit(f"{private} exists -- refusing to overwrite a key")
    key = Ed25519PrivateKey.generate()
    private.touch(mode=0o600)
    private.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8,
                                          NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    return private, public


def make_root(version: int, index_keys: list[str], days: int, now: datetime,
              key_file: Path, trust: ModuleTrust) -> bytes:
    """A signed ``root.json`` naming *index_keys* -- checked as TRCC will."""
    data = envelope({"type": ROOT_TYPE, "version": version,
                     "expires": expiry(days, now), "index_keys": index_keys},
                    key_file)
    trust.open_root(data)
    return data


def author_sign(bundle: bytes, module: str, version: str, key_file: Path) -> str:
    """An author's signature on release *version* of *module*, base64."""
    statement = author_statement(module, version, hashlib.sha256(bundle).hexdigest())
    return base64.b64encode(sign(statement, key_file)).decode()


def _signed(index_file: Path) -> dict[str, Any]:
    """The signed part of the index at *index_file*, or a new empty one."""
    if not index_file.exists():
        return {"type": INDEX_TYPE, "version": 0, "modules": {}, "revoked": []}
    signed = json.loads(index_file.read_text(encoding="utf-8"))["signed"]
    if signed.get("type") != INDEX_TYPE:
        raise SystemExit(f"{index_file} is not a module index")
    return signed


def reissue(signed: dict[str, Any], root: TrustRoot, days: int, now: datetime,
            key_file: Path, trust: ModuleTrust) -> bytes:
    """*signed* as the next version of the index, expiring in *days*, signed
    and opened as TRCC opens it -- or ``UntrustedModule`` saying why not."""
    seen = signed["version"]
    signed = {**signed, "version": seen + 1, "expires": expiry(days, now)}
    data = envelope(signed, key_file)
    trust.open_index(data, root, seen=seen)
    return data


def add(signed: dict[str, Any], module: str, bundle: bytes,
        entry: dict[str, Any], trust: ModuleTrust) -> dict[str, Any]:
    """*signed* listing *bundle* as *module*'s release, described by *entry*
    (version, url, commit, min_trcc, capabilities, network_hosts, author_key,
    author_sig).
    Refused unless its author signed it -- with the key its listed release
    carries, when it has one: the verifier's own checks, not a copy."""
    entry = {**entry, "size": len(bundle),
             "sha256": hashlib.sha256(bundle).hexdigest()}
    previous = signed["modules"].get(module)
    trust.check_release(parse_release(module, entry), bundle,
                        pinned_author=previous["author_key"] if previous else None)
    return {**signed, "modules": {**signed["modules"], module: entry}}


def revoke(signed: dict[str, Any], module: str, reason: str,
           sha256: str = "") -> dict[str, Any]:
    """*signed* taking back *module* -- every release, or only *sha256*."""
    entry = {"key": module, "reason": reason, **({"sha256": sha256} if sha256 else {})}
    return {**signed, "revoked": [*signed.get("revoked", []), entry]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write and sign the module index.")
    parser.add_argument("--trust", type=Path, action="append", default=[],
                        help="check against this root public key (PEM) instead of "
                             "the app's -- to try the tool with throwaway keys")
    sub = parser.add_subparsers(dest="action", required=True)

    p = sub.add_parser("keygen", help="a new Ed25519 pair: <stem>.key + <stem>.pub")
    p.add_argument("stem", type=Path)

    p = sub.add_parser("root", help="write root.json, signed by a release key")
    p.add_argument("out", type=Path)
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--index-key", type=Path, action="append", required=True,
                   help="an index public key (PEM); repeat for several")
    p.add_argument("--key", type=Path, default=DEFAULT_ROOT_KEY)
    p.add_argument("--days", type=int, default=365)

    p = sub.add_parser("author-sign", help="an author signs a release bundle")
    p.add_argument("bundle", type=Path)
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--module", required=True)
    p.add_argument("--version", required=True)

    for name, text in (("add", "list a reviewed release"),
                       ("revoke", "take a module or one release back"),
                       ("renew", "re-sign with a new version and expiry")):
        p = sub.add_parser(name, help=text)
        p.add_argument("index", type=Path)
        p.add_argument("--root", type=Path, required=True)
        p.add_argument("--key", type=Path, default=DEFAULT_INDEX_KEY)
        p.add_argument("--days", type=int, default=90)
        if name == "add":
            for flag in ("--module", "--version", "--url", "--commit",
                         "--min-trcc", "--author-sig"):
                p.add_argument(flag, required=True)
            p.add_argument("--bundle", type=Path, required=True)
            p.add_argument("--capabilities", default="")
            p.add_argument("--network-hosts", default="",
                           help="the sites it fetches from, comma-separated")
            p.add_argument("--author-key", type=Path, required=True)
        elif name == "revoke":
            p.add_argument("--module", required=True)
            p.add_argument("--reason", required=True)
            p.add_argument("--sha256", default="")

    args = parser.parse_args(argv)
    roots = [public_key(k) for k in args.trust] or list(ROOT_KEYS.values())
    trust, now = ModuleTrust(Ed25519Checker(), roots), datetime.now(timezone.utc)
    try:
        match args.action:
            case "keygen":
                private, public = keygen(args.stem)
                print(f"Wrote {private} (keep it here) and {public}.")
            case "root":
                keys = [public_key(k) for k in args.index_key]
                args.out.write_bytes(make_root(args.version, keys, args.days, now,
                                               args.key, trust))
                print(f"Wrote {args.out}: version {args.version}, {len(keys)} index key(s).")
            case "author-sign":
                print(author_sign(args.bundle.read_bytes(), args.module, args.version,
                                  args.key))
            case _:
                root = trust.open_root(args.root.read_bytes())
                signed = _signed(args.index)
                if args.action == "add":
                    signed = add(signed, args.module, args.bundle.read_bytes(), {
                        "version": args.version, "url": args.url, "commit": args.commit,
                        "min_trcc": args.min_trcc,
                        "capabilities": [c for c in args.capabilities.split(",") if c],
                        "network_hosts": [h for h in args.network_hosts.split(",") if h],
                        "author_key": public_key(args.author_key),
                        "author_sig": args.author_sig}, trust)
                elif args.action == "revoke":
                    signed = revoke(signed, args.module, args.reason, args.sha256)
                data = reissue(signed, root, args.days, now, args.key, trust)
                args.index.write_bytes(data)
                print(f"Wrote {args.index}: version {signed['version'] + 1}, "
                      f"{len(signed['modules'])} module(s), "
                      f"{len(signed.get('revoked', []))} revoked.")
    except UntrustedModule as e:
        print(f"STOP: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

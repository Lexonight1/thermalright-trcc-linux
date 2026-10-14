#!/usr/bin/env python3
"""Write ``src/trcc/core/credits_libraries.py``: every Python library TRCC runs
on, with who made it, its licence and its home -- pure attribution.

Read from each library's own installed metadata: the copyright holder from its
licence file's ``Copyright`` line (else the metadata's author or maintainer),
and its licence expression or classifier.  The libraries are
``pyproject.toml``'s dependencies on every OS, and what they pull in.  One not
installed here (a Windows- or macOS-only one) is read from PyPI's metadata.

    PYTHONPATH=src python3.12 dev/tools/gen_credits.py
"""
from __future__ import annotations

import importlib.metadata as md
import json
import re
import sys
import tomllib
import urllib.request
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "src" / "trcc" / "core" / "credits_libraries.py"
#: Extras whose libraries ship: uvicorn[standard] is the declared dependency.
_EXTRAS = ("", "standard")
#: A library counts if ANY OS TRCC ships for needs it.
_SYSTEMS = (
    {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix"},
    {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"},
    {"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix"},
)
#: Licence texts whose "Copyright" lines are the licence's own, not the holder's.
_LICENCE_TEXTS = re.compile(r"GNU (LESSER |LIBRARY )?GENERAL PUBLIC LICENSE|Apache License|"
                            r"Mozilla Public License|PYTHON SOFTWARE FOUNDATION LICENSE", re.I)
_COPYRIGHT = re.compile(
    r"^\s*Copyright\s*(?:\(c\)|\(C\)|©)?\s*(?:[\d][\d\s,\-–]*(?:present)?)?\s*,?\s*(?:by\s+)?(.+?)\s*$",
    re.IGNORECASE)


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    return Requirement(requirement).name


def _needed(requirement: str) -> bool:
    """Whether some OS TRCC ships for installs *requirement* (with the extras
    TRCC asks for) -- never a test or docs extra."""
    marker = Requirement(requirement).marker
    if marker is None:
        return True
    return any(marker.evaluate({**system, "python_version": "3.12",
                                "python_full_version": "3.12.0", "extra": extra})
               for system in _SYSTEMS for extra in _EXTRAS)


def roots() -> list[str]:
    """Every dependency ``pyproject.toml`` declares, on every OS."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return [_requirement_name(r) for r in project["dependencies"] if _needed(r)]


def holder(dist: md.Distribution) -> str:
    """The copyright holder its licence file names, if one does."""
    # The package's own licence before any it vendors (numpy carries dozens):
    # its metadata names that one first.
    named = (dist.metadata.get_all("License-File") or [])[:1]
    files = [f for f in dist.files or []
             if named and f.as_posix().endswith((f".dist-info/licenses/{named[0]}",
                                                 f".dist-info/{named[0]}"))]
    files = files or [f for f in dist.files or []
                      if re.search(r"(LICEN[CS]E|COPYING)", f.name, re.I)]
    for file in files:
        try:
            text = Path(str(dist.locate_file(file))).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _LICENCE_TEXTS.search(text):
            continue                       # the licence itself, not who holds it
        for line in text.splitlines():
            found = _COPYRIGHT.match(line)
            if found and not re.search(r"notice|holder|owner|<year>|\[yyyy\]|the above",
                                       found.group(1), re.I):
                return _clean(found.group(1))
    return ""


def _clean(who: str) -> str:
    """A holder as a reader should see it: no "All rights reserved", no
    leftover "to present", no markdown link, no e-mail address."""
    who = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", who)
    who = re.sub(r"\s*<[^>]*>", "", who)
    who = re.sub(r"\.?\s*All rights reserved\.?$", "", who, flags=re.I)
    who = re.sub(r"^(?:to\s+)?present\s*,?\s*|\s*\d{4}\s*(?:-|to)\s*present\s*$", "", who,
                 flags=re.I)
    return who.strip(" .,")


def author(meta: md.PackageMetadata) -> str:
    for field in ("Author", "Maintainer"):
        if (value := (meta.get(field) or "").strip()) and value.upper() != "UNKNOWN":
            return _clean(value)
    for field in ("Author-email", "Maintainer-email"):
        if value := (meta.get(field) or "").strip():
            return re.sub(r"\s*<[^>]*>", "", value).strip(' "')
    return ""


def licence(meta: md.PackageMetadata) -> str:
    if expression := (meta.get("License-Expression") or "").strip():
        return expression
    classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or []
                   if c.startswith("License ::") and c.split(" :: ")[-1] != "OSI Approved"]
    if classifiers:
        return " / ".join(classifiers)
    first = (meta.get("License") or "").strip().splitlines()
    return first[0][:60] if first and first[0].upper() != "UNKNOWN" else ""



def from_pypi(name: str) -> dict[str, str]:
    """A library not installed here, from PyPI's metadata (read only)."""
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=20) as reply:
        info = json.load(reply)["info"]
    who = (info.get("author") or info.get("maintainer") or "").strip()
    if not who:
        who = re.sub(r"\s*<[^>]*>", "", info.get("author_email") or "").strip(' "')
    licence_text = (info.get("license_expression") or "").strip() or next(
        (c.split(" :: ")[-1] for c in info.get("classifiers") or []
         if c.startswith("License ::")), (info.get("license") or "").strip()[:60])
    return {"name": info["name"], "author": who, "licence": licence_text}


def collect() -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    todo = roots()
    while todo:
        name = todo.pop()
        if _key(name) in seen:
            continue
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            seen[_key(name)] = from_pypi(name)
            continue
        meta = dist.metadata
        seen[_key(name)] = {"name": meta["Name"], "author": holder(dist) or author(meta),
                            "licence": licence(meta)}
        todo += [_requirement_name(r) for r in dist.requires or [] if _needed(r)]
    return sorted(seen.values(), key=lambda entry: entry["name"].lower())


def main() -> int:
    from trcc.core.credits import BUILT_FROM

    built_from = {_key(c.name) for c in BUILT_FROM}
    entries = [e for e in collect() if _key(e["name"]) not in built_from]
    lines = [
        '"""Every Python library TRCC runs on: (name, who made it, licence) --',
        "generated by ``dev/tools/gen_credits.py`` from each library's own metadata.",
        'Do not edit: regenerate.  ``core.credits`` makes them ``Credit`` s."""',
        "",
        "LIBRARIES: tuple[tuple[str, str, str], ...] = (",
    ]
    lines += [f"    ({e['name']!r}, {e['author']!r}, {e['licence']!r}),"
              for e in entries]
    lines.append(")")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(entries)} libraries -> {OUT.relative_to(ROOT)}")
    for e in entries:
        print(f"  {e['name']:24} {e['licence'][:28]:28} {e['author'][:44]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

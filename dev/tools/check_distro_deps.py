#!/usr/bin/env python3
"""Check our packaging claims against what the distros ACTUALLY ship.

Packaging carries frozen claims about the outside world:

    # Bundle sounddevice + nvidia-ml-py (neither in Fedora repos)
    optdepend = python-nvidia-ml-py: NVIDIA GPU sensor support

Both were true when written.  Both rot, because distro repos change and nobody
re-reads a comment.  When `nvidia-ml-py` became a hard dependency to fix "no
NVIDIA GPU sensors" (#207, #161), the Arch spec kept it as an *optdepend* — so
pacman never installed it and the fix reached Arch/CachyOS users as nothing at
all, while we told the reporter it was fixed.  Nothing re-checked, and the
packaging job only runs on a tag push, so there was no moment where it surfaced.

Two halves, deliberately split:

* ``tests/test_packaging_entrypoints.py`` — OFFLINE, runs every push.  Catches
  *our* drift: a hard pyproject dep not declared in a distro spec.
* this tool — ONLINE, run before a release.  Catches *the distro's* drift:
  packages appearing or vanishing under us.  It cannot be a test — tests must
  stay offline and deterministic.

Run:  python3 dev/tools/check_distro_deps.py
Exit: 0 = every claim still true, 1 = at least one claim has rotted.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _ROOT / "pyproject.toml"
_RELEASE_YML = _ROOT / ".github" / "workflows" / "release.yml"

_FEDORA_RELEASE = "f44"
_TIMEOUT = 15


# ── the name mapping (single source of truth; the offline test imports it) ──

# pyproject name -> (arch, fedora, debian) package names.
_PKG_NAMES: dict[str, tuple[str, str, str]] = {
    "PySide6":          ("pyside6", "python3-pyside6", "python3-pyside6.qtcore"),
    "numpy":            ("python-numpy", "python3-numpy", "python3-numpy"),
    "psutil":           ("python-psutil", "python3-psutil", "python3-psutil"),
    "pyusb":            ("python-pyusb", "python3-pyusb", "python3-usb"),
    "pyudev":           ("python-pyudev", "python3-pyudev", "python3-pyudev"),
    "click":            ("python-click", "python3-click", "python3-click"),
    "typer":            ("python-typer", "python3-typer", "python3-typer"),
    "fastapi":          ("python-fastapi", "python3-fastapi", "python3-fastapi"),
    "prompt_toolkit":   ("python-prompt_toolkit", "python3-prompt-toolkit",
                         "python3-prompt-toolkit"),
    "python-multipart": ("python-multipart", "python3-multipart",
                         "python3-multipart"),
    "certifi":          ("python-certifi", "python3-certifi", "python3-certifi"),
    "nvidia-ml-py":     ("python-nvidia-ml-py", "python3-nvidia-ml-py",
                         "python3-pynvml"),
    "uvicorn":          ("python-uvicorn", "python3-uvicorn", "python3-uvicorn"),
    "sounddevice":      ("python-sounddevice", "python3-sounddevice",
                         "python3-sounddevice"),
}

# pyproject name -> the module our code actually ``import``s.  Only listed
# where it differs from the distribution name, because that gap is where the
# decoys live: Fedora ships `python3-py3nvml`, whose name looks exactly like
# what we want, but it provides `py3nvml` — we import `pynvml`.  Mapping to it
# would satisfy a name check and still leave the reader broken.  A capability
# check ("who provides pynvml?") cannot be fooled that way.
_IMPORT_NAME = {
    "nvidia-ml-py": "pynvml",
    "python-multipart": "multipart",
    "PySide6": "PySide6",
    "prompt_toolkit": "prompt_toolkit",
}

# Arch packages we knowingly do NOT declare, because Arch has no official
# package and a `depend =` line would make `pacman -U` fail for everyone.
# This tool exists to tell us when an entry here stops being true.
ARCH_UNAVAILABLE = {"python-uvicorn", "python-sounddevice"}

# Packages the Fedora RPM vendors via pip instead of depending on.
# Justified only while Fedora genuinely has no package.
FEDORA_VENDORED = {"sounddevice", "nvidia-ml-py"}


def pyproject_runtime_deps() -> list[str]:
    """Hard runtime deps from pyproject, minus the win32-gated ones."""
    data = tomllib.loads(_PYPROJECT.read_text())
    out: list[str] = []
    for spec in data["project"]["dependencies"]:
        if "sys_platform == 'win32'" in spec:
            continue
        out.append(re.split(r"[><=;\[]", spec)[0].strip())
    return out


def arch_declared_depends() -> set[str]:
    """The `depend =` lines from the Arch .PKGINFO heredoc in release.yml."""
    text = _RELEASE_YML.read_text()
    start = text.index("          depend = python\n")
    block = text[start:text.index("          INFO", start)]
    return set(re.findall(r"^\s*depend = (\S+)", block, re.M))


# ── remote probes ──────────────────────────────────────────────────────

def _get(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None


def in_arch(pkg: str) -> str | None:
    """Repo name if Arch ships *pkg* officially, else None."""
    body = _get(f"https://archlinux.org/packages/search/json/?name={pkg}")
    if not body:
        return None
    try:
        results = json.loads(body).get("results", [])
    except json.JSONDecodeError:
        return None
    return results[0]["repo"] if results else None


def in_fedora(pkg: str) -> str | None:
    body = _get(f"https://mdapi.fedoraproject.org/{_FEDORA_RELEASE}/pkg/{pkg}")
    if not body:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    return f"{data.get('version', '?')}-{data.get('release', '?')}" if data.get(
        "version") else None


def in_debian(pkg: str) -> str | None:
    body = _get(
        f"https://api.ftp-master.debian.org/madison?package={pkg}&f=json"
    )
    if not body:
        return None
    try:
        entries = json.loads(body)
    except json.JSONDecodeError:
        return None
    for entry in entries:
        for suites in entry.values():
            for suite in ("stable", "testing", "unstable"):
                if suite in suites:
                    return f"{suite} {next(iter(suites[suite]))}"
    return None


# ── findings ───────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Finding:
    severity: str          # "STALE" (a claim rotted) | "GAP" (a real hole)
    dep: str
    message: str


def fedora_provides_module(module: str) -> list[str]:
    """Which Fedora packages provide ``import <module>`` — by capability.

    Asks "who provides this file?" instead of "does this name exist?", so a
    rename or a same-purpose-different-name package cannot hide, and a decoy
    (python3-py3nvml vs the pynvml we import) cannot masquerade.  Needs dnf, so
    it only runs on the Fedora dev box; a name check is the portable fallback.
    """
    if not shutil.which("dnf"):
        return []
    names: set[str] = set()
    for pattern in (f"*/site-packages/{module}.py",
                    f"*/site-packages/{module}/__init__.py"):
        try:
            out = subprocess.run(
                ["dnf", "--quiet", "provides", pattern],
                capture_output=True, text=True, timeout=60, check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        names.update(re.findall(r"^([a-z0-9][\w.+-]*)\s*:", out, re.M))
        names.update(re.findall(r"^([a-z0-9][\w.+-]*-[\d.]+[\w.]*)\s", out, re.M))
    return sorted(n.rsplit("-", 2)[0] if re.search(r"-[\d.]+", n) else n
                  for n in names)


def check() -> list[Finding]:
    findings: list[Finding] = []
    deps = pyproject_runtime_deps()
    declared = arch_declared_depends()

    print(f"{'dependency':18} {'arch':22} {'fedora':18} {'debian':22}")
    print("-" * 82)
    for dep in deps:
        names = _PKG_NAMES.get(dep)
        if names is None:
            findings.append(Finding(
                "GAP", dep, f"no distro package mapping — add {dep!r} to _PKG_NAMES"))
            print(f"  {dep:16} {'(unmapped)':22}")
            continue
        a_name, f_name, d_name = names
        a, f, d = in_arch(a_name), in_fedora(f_name), in_debian(d_name)
        print(f"  {dep:16} {(a or '— absent'):22} {(f or '— absent'):18} "
              f"{(d or '— absent'):22}")

        # 1. A mapped name that exists nowhere is probably a typo, and today
        #    that fails silently: we simply never depend on it.
        if not any((a, f, d)):
            findings.append(Finding(
                "GAP", dep,
                f"mapped names exist in NO distro ({a_name}/{f_name}/{d_name}) "
                f"— likely wrong names"))

        # 2. An Arch exception that is no longer true: we can and should depend.
        if a and a_name in ARCH_UNAVAILABLE:
            findings.append(Finding(
                "STALE", dep,
                f"Arch now ships {a_name} in [{a}] — remove it from "
                f"ARCH_UNAVAILABLE and add `depend = {a_name}`"))

        # 3. A hard dep Arch ships but we never declare.
        if a and a_name not in declared and a_name not in ARCH_UNAVAILABLE:
            findings.append(Finding(
                "GAP", dep,
                f"Arch ships {a_name} in [{a}] but release.yml does not "
                f"`depend =` it — pacman will not install it"))

        # 4. We vendor it into the RPM while Fedora ships a package.
        if f and dep in FEDORA_VENDORED:
            findings.append(Finding(
                "STALE", dep,
                f"Fedora now ships {f_name} ({f}) but the RPM still pip-vendors "
                f"it — depend on the package instead"))

        # 5. Capability cross-check: names lie, imports do not.  Only the
        #    module we actually import proves a package is the right one.
        module = _IMPORT_NAME.get(dep)
        if module:
            providers = fedora_provides_module(module)
            if f and f_name not in providers and providers:
                findings.append(Finding(
                    "GAP", dep,
                    f"Fedora {f_name} exists but does NOT provide `import "
                    f"{module}` — {providers} do. Wrong package mapped?"))
            if not f and providers:
                findings.append(Finding(
                    "STALE", dep,
                    f"we map Fedora to {f_name} (absent), but {providers} "
                    f"provide `import {module}` — remap and stop vendoring"))
    return findings


def main() -> int:
    print("Checking packaging claims against live distro repositories…\n")
    findings = check()
    print()
    if not findings:
        print("All packaging claims still hold.")
        return 0
    for f in findings:
        print(f"  [{f.severity}] {f.dep}: {f.message}")
    print(f"\n{len(findings)} claim(s) need attention.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

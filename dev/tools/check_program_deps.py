#!/usr/bin/env python3
"""Check every way we ship against what each target ACTUALLY provides.

A UNIVERSAL dependency check: every hard dependency x every shipping target.
Not "distros" -- we ship six ways, and they use THREE different models:

    target        deps come from              can drift?
    ------------  --------------------------  ----------
    Arch          pacman `depend =`           YES - hand-declared
    deb           apt `Depends:`              YES - hand-declared
    rpm (Fedora)  dnf `Requires:` + vendored  YES - hand-declared
    deb (legacy)  pip, in its own venv        no  - resolved from pyproject
    Windows       pip install ".[...]"        no  - resolved from pyproject
    macOS         pip install ".[...]"        no  - resolved from pyproject

Only the hand-declared three can silently omit something; the pip-resolved three
take the wheel as their source of truth. So each cell asks one of two questions,
never one blanket rule.

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
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _ROOT / "pyproject.toml"
_RELEASE_YML = _ROOT / ".github" / "workflows" / "release.yml"

_FEDORA_RELEASE = "f44"
_UBUNTU_SERIES = "questing"
#: Alpine stable branch.  Pinned like the two above: "edge" would make
#: the answer depend on the day the tool ran, and users are on a release.
_ALPINE_BRANCH = "v3.22"
_TIMEOUT = 15


# ── the name mapping (single source of truth; the offline test imports it) ──

# pyproject name -> (arch, fedora, debian) package names.
_PKG_NAMES: dict[str, tuple[str, str, str]] = {
    "PySide6":          ("pyside6", "python3-pyside6", "python3-pyside6.qtcore"),
    "numpy":            ("python-numpy", "python3-numpy", "python3-numpy"),
    "psutil":           ("python-psutil", "python3-psutil", "python3-psutil"),
    "pyusb":            ("python-pyusb", "python3-pyusb", "python3-usb"),
    "pyudev":           ("python-pyudev", "python3-pyudev", "python3-pyudev"),
    "hidapi":           ("python-hidapi", "python3-hid", "python3-hidapi"),
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

# Hard pyproject deps we DELIBERATELY do not declare for a distro, with the
# reason.  Distinct from "unavailable": the package EXISTS, and depending on it
# would cost the user more than the feature is worth.  Recording the reason is
# the point -- an unexplained optdepend reads as an oversight and gets "fixed",
# which is exactly what happened: #216 made the NVIDIA reader optional because
# it drags nvidia-utils (~938 MB) onto AMD systems, and six days later it was
# reverted to a hard depend "to fix #207" and shipped.
#
# The trade is only acceptable because the app TELLS the user how to install it
# (Platform.software_install_hint) -- see
# tests/test_packaging_entrypoints.py, which asserts BOTH halves.
DELIBERATELY_OPTIONAL: dict[str, str] = {
    "nvidia-ml-py": (
        "pulls nvidia-utils (~938 MB) on Arch / libnvidia-ml1 from contrib on "
        "Debian -- an NVIDIA driver stack for every AMD and Intel owner (#216). "
        "Optional + an OS-correct install hint instead (#207)."
    ),
}

# Packages the Fedora RPM vendors via pip instead of depending on.
# Justified only while Fedora genuinely has no package.
FEDORA_VENDORED = {"sounddevice", "nvidia-ml-py"}


def pyproject_runtime_deps(include_win32: bool = False) -> list[str]:
    """Hard runtime deps from pyproject.

    ``include_win32=False`` (the default) drops the win32-gated ones, because
    the Linux package managers must not declare them.  The universal report
    passes True so wmi / tzdata / libusb-package stop being invisible: they are
    real dependencies of a real target, and nothing checked them before.
    """
    data = tomllib.loads(_PYPROJECT.read_text())
    out: list[str] = []
    for spec in data["project"]["dependencies"]:
        if not include_win32 and "sys_platform == 'win32'" in spec:
            continue
        out.append(re.split(r"[><=;\[]", spec)[0].strip())
    return out


def pip_resolved_targets() -> dict[str, str]:
    """Targets whose deps come from the wheel, and the line that proves it.

    These cannot drift from pyproject -- pip resolves it -- so the check is
    "does this target still install the project with pip?", not "does it list
    every dep?".  If one of these ever stops pip-installing the project, its
    guarantee is gone silently.
    """
    yml = lambda n: (_ROOT / ".github" / "workflows" / n).read_text()  # noqa: E731
    return {
        "windows (PyInstaller)": yml("windows.yml"),
        "macos (PyInstaller)": yml("macos.yml"),
        "deb-legacy (venv)": _RELEASE_YML.read_text(),
    }


def arch_declared_depends() -> set[str]:
    """The `depend =` lines from the Arch .PKGINFO heredoc in release.yml."""
    text = _RELEASE_YML.read_text()
    start = text.index("          depend = python\n")
    block = text[start:text.index("          INFO", start)]
    return set(re.findall(r"^\s*depend = (\S+)", block, re.M))


def deb_declared_depends() -> set[str]:
    """The STANDARD deb's `Depends:` line (not the legacy venv deb).

    The legacy deb vendors its Python deps into /opt/trcc-linux, so its
    Depends list is deliberately short — only the standard deb declares them.
    """
    text = _RELEASE_YML.read_text()
    for line in re.findall(r"^\s*Depends: (.+)$", text, re.M):
        if "pyside6" not in line:
            continue                     # the legacy deb — vendors instead
        return {re.split(r"\s*\(", tok.strip())[0] for tok in line.split(",")}
    return set()


def rpm_declared_requires() -> set[str]:
    """The RPM spec's `Requires:` lines from the heredoc in release.yml."""
    text = _RELEASE_YML.read_text()
    return set(re.findall(r"^\s*Requires:\s+(\S+)", text, re.M))


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


def in_ubuntu(pkg: str) -> str | None:
    """Ubuntu is NOT Debian, and our deb targets both.

    python3-pynvml is published in Ubuntu and absent from Debian. Checking only
    Debian made the tool read "absent everywhere", skip the deb assertion, and
    stay green over #221 -- a bug the reporter found by hand. One wrong archive
    is the whole failure.
    """
    body = _get(
        "https://api.launchpad.net/1.0/ubuntu/+archive/primary"
        "?ws.op=getPublishedBinaries&exact_match=true&status=Published"
        f"&binary_name={pkg}"
        f"&distro_arch_series=https://api.launchpad.net/1.0/ubuntu/{_UBUNTU_SERIES}/amd64"
    )
    if not body:
        return None
    try:
        entries = json.loads(body).get("entries") or []
    except json.JSONDecodeError:
        return None
    return entries[0].get("binary_package_version") if entries else None


def in_debian(pkg: str) -> str | None:
    """Debian archive version for *pkg*, or None.

    madison appends the COMPONENT to the suite key for anything outside main:
    a main package answers ``stable``, a contrib one answers ``stable/contrib``.
    An exact-key match therefore read every contrib and non-free package as
    absent -- including python3-pynvml, nvidia-driver and virtualbox.  The
    checker was blind to precisely the component our NVIDIA advice lives in,
    so it blessed whatever was written there.  Match the prefix.
    """
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
            for want in ("stable", "testing", "unstable"):
                for key, versions in suites.items():
                    # "stable" matches "stable" and "stable/contrib", but must
                    # NOT match "oldstable" or "stable-debug".
                    if key == want or key.startswith(f"{want}/"):
                        return f"{key} {next(iter(versions))}"
    return None


# ── the external programs the app needs, and what actually ships them ──
#
# The checks above ask "is our PYTHON dependency declared by each distro spec?"
# Nothing asked the other half: when the app tells a user how to install an
# external program, does that command deliver the binary the app then probes
# for?  A hint that installs successfully and leaves the check still failing is
# worse than no hint at all -- the user follows it, nothing improves, and the
# report says the same thing twice.  Fedora shipped exactly that: our
# `dnf install p7zip` resolves to 7zip-standalone, which ships /usr/bin/7za and
# no /usr/bin/7z, so shutil.which("7z") keeps failing after a successful install.
#
# We check the STRING THE USER SEES, not an internal table.  An internal table
# can be right while the rendered command is wrong, and the rendered command is
# the thing that has to be true.

#: hint key (what ``software_install_hint`` is called with) -> the binaries
#: that hint MUST put on PATH.  Keys with no binary to deliver map to () and
#: are reported as "nothing to verify" rather than silently skipped:
#:   python  -- the check is a VERSION check on the running interpreter; no
#:              install command can change it mid-run.
#:   pynvml  -- a Python module, not a binary.  Its real file dependency is
#:              libnvidia-ml.so.1, which comes from the driver stack, not from
#:              the binding (that is #216).
_HINT_BINARIES: dict[str, tuple[str, ...]] = {
    "ffmpeg": ("ffmpeg", "ffprobe"),
    "7z": ("7z",),
    "python": (),
    "pynvml": (),
}


def hint_keys_in_source() -> set[str]:
    """Every tool the app actually advises on, read out of the source.

    Derived rather than listed, so a new ``software_install_hint("x")`` call
    cannot appear without this tool noticing: an unmapped key becomes a GAP,
    the same way an unmapped dependency does.
    """
    keys: set[str] = set()
    for path in (_ROOT / "src" / "trcc").rglob("*.py"):
        keys.update(re.findall(r'software_install_hint\(\s*"([^"]+)"',
                               path.read_text(encoding="utf-8")))
    return keys


def package_from_hint(hint: str) -> str | None:
    """The package name out of a rendered hint, or None when there is none.

    None means "nothing for a repo to verify", and covers two honest cases: a
    generic fallback naming no command, and ``pip install`` -- a Python package,
    which no distro repo can be asked about.
    """
    if not hint or hint.startswith(("Install ", "pip install")):
        return None
    pkg = hint.split()[-1]
    return pkg.rsplit("nixpkgs.", 1)[-1]        # nix-env -iA nixpkgs.<pkg>


# ── per-channel file lists: what does this package actually install? ────
#
# Each returns the basenames the package puts in a bin dir, or None for "this
# channel cannot be asked".  None is NOT "fine" -- it is reported, because an
# unverifiable channel is where wrong advice survives.

# ── the channels ──────────────────────────────────────────────────────────
#
# Five parallel free functions dispatched through a ``dict[str, tuple[str,
# object]]`` and invoked as ``probe(pkg)  # type: ignore[operator]``.  The
# ignore was the tell: a callable stored as ``object`` is a table that has
# stopped describing itself.  Adding the EL, SUSE, Alpine, Void, Nix and BSD
# channels would have made it thirteen of them.
#
# They already shared one contract -- resolve a name to a real package, then
# list the binaries it puts on PATH, with ``None`` for "cannot answer" -- and
# differed only in HOW.  That contract is now stated once.


class Channel(ABC):
    """One package channel we can ask: what binaries does this ship?"""

    #: How this channel is named in findings ("arch", "fedora", ...).
    LABEL: ClassVar[str]

    @abstractmethod
    def binaries(self, pkg: str) -> list[str] | None:
        """Basenames *pkg* puts on PATH, or None when unanswerable.

        None is "this channel cannot tell you" -- an unreachable index, a
        missing local tool, a package that is not there.  It is never "ships
        nothing", which is an empty list and a different fact.
        """


class ArchChannel(Channel):
    LABEL = "arch"

    def binaries(self, pkg: str) -> list[str] | None:
        """Arch: search resolves a `provides` name (p7zip -> 7zip), then list files.

        Resolving provides FIRST matters: `pacman -S p7zip` really does work,
        because 7zip declares `provides`/`replaces` for it.  A name-existence check
        calls that broken; the package it resolves to is what must be inspected.
        """
        body = _get(f"https://archlinux.org/packages/search/json/?q={pkg}")
        if not body:
            return None
        try:
            results = json.loads(body).get("results", [])
        except json.JSONDecodeError:
            return None
        hit = next((r for r in results
                    if r["pkgname"] == pkg or pkg in (r.get("provides") or [])), None)
        if hit is None:
            return None
        files = _get(f"https://archlinux.org/packages/{hit['repo']}/"
                     f"{hit['arch']}/{hit['pkgname']}/files/json/")
        if not files:
            return None
        try:
            listing = json.loads(files).get("files", [])
        except json.JSONDecodeError:
            return None
        return [f.rsplit("/", 1)[-1] for f in listing if "/bin/" in f and not f.endswith("/")]


class FedoraChannel(Channel):
    LABEL = "fedora"

    def binaries(self, pkg: str) -> list[str] | None:
        """Fedora: resolve provides with dnf, then list the resolved package.

        `repoquery -l p7zip` is EMPTY -- p7zip is a capability, not a package -- so
        the two steps are not optional.  Needs dnf, i.e. the Fedora dev box.
        """
        if not shutil.which("dnf"):
            return None

        # STOCK repos only.  The dev box has RPM Fusion enabled, and querying with
        # it on made `dnf install ffmpeg` verify clean while a stock Fedora answers
        # "No match for argument: ffmpeg" -- the tool was reporting on this machine
        # rather than on a user's.  Pinning the repo set is what makes the answer
        # about Fedora instead of about whoever runs the tool.
        stock = ["--disablerepo=*", "--enablerepo=fedora", "--enablerepo=updates"]

        def _run(args: list[str]) -> str:
            try:
                return subprocess.run(args, capture_output=True, text=True,
                                      timeout=120, check=False).stdout
            except (OSError, subprocess.SubprocessError):
                return ""
        names = sorted(set(_run(
            ["dnf", "repoquery", "--quiet", *stock, "--whatprovides", pkg,
             "--qf", "%{name}"]).split()))
        if not names:
            return None
        out: list[str] = []
        for name in names:
            out += [ln.rsplit("/", 1)[-1] for ln in
                    _run(["dnf", "repoquery", "--quiet", *stock, "-l",
                          name]).splitlines()
                    if "/bin/" in ln]
        return out


class DebianChannel(Channel):
    LABEL = "debian"

    def binaries(self, pkg: str, _depth: int = 0) -> list[str] | None:
        """Debian stable: the published per-package file list.

        Follows a TRANSITIONAL package to what it pulls in.  Debian's p7zip is
        `16.02+transitional.1`: it exists, ships no files at all, and depends on
        7zip -- so `apt install p7zip` really does deliver /usr/bin/7z.  Reading
        "no files" as "absent" reported that working hint as broken, which is the
        same false negative a bare name check gives (see arch_package_files).
        """
        body = _get(f"https://packages.debian.org/stable/amd64/{pkg}/filelist")
        if not body:
            return None
        found = re.findall(r"(/usr/s?bin/[\w.+-]+)", body)
        if found:
            return [f.rsplit("/", 1)[-1] for f in found]
        if _depth:                       # one hop only; transitional chains are 1 deep
            return None
        page = _get(f"https://packages.debian.org/stable/amd64/{pkg}") or ""
        out: list[str] = []
        for dep in dict.fromkeys(re.findall(r'href="/trixie/amd64/([\w.+-]+)"', page)):
            if dep != pkg:
                out += self.binaries(dep, _depth + 1) or []
        return out or None


class HomebrewChannel(Channel):
    LABEL = "homebrew"

    def binaries(self, formula: str) -> list[str] | None:
        """Homebrew publishes the executable list per formula -- no install needed.

        Load-bearing here: p7zip installs 7z/7za/7zr while sevenzip installs only
        7zz.  Since the app probes for `7z`, the DEPRECATED-looking name is the
        correct one on macOS and the modern one would break it.
        """
        body = _get(f"https://formulae.brew.sh/api/formula/{formula}.json")
        if not body:
            return None
        try:
            return list(json.loads(body).get("executables") or []) or None
        except json.JSONDecodeError:
            return None


class AlpineChannel(Channel):
    LABEL = "alpine"

    def binaries(self, pkg: str) -> list[str] | None:
        """Alpine publishes a per-package file list at pkgs.alpinelinux.org.

        Searched by NAME, giving the files a package ships; the same endpoint
        answers the inverse (which package owns a file) via ``file=``.

        Measured 2026-08-21, and the reason this channel was written first:
        ``p7zip`` does not exist in Alpine at all -- 404 in main AND community
        -- while ``7zip`` (main) ships /usr/bin/7z and /usr/bin/7zz.  So
        ``apk add p7zip``, which the app printed, fails outright.  ``ffmpeg``
        does exist but lives in **community**, not main, which is why
        provenance is a real question and not bookkeeping.
        """
        body = _get(f"https://pkgs.alpinelinux.org/contents?file=&path=&"
                    f"name={pkg}&branch={_ALPINE_BRANCH}&repo=&arch=x86_64")
        if not body:
            return None
        # The file cell is the only pre-wrap td; the package/branch/repo cells
        # are links.  Anchoring on the style keeps a layout change loud rather
        # than silently returning nothing.
        files = re.findall(r'<td style="white-space: pre-wrap;">([^<]+)</td>',
                           body)
        if not files:
            return None                        # absent, or ships no files
        return [f.rsplit("/", 1)[-1] for f in files if "/bin/" in f]


class WingetChannel(Channel):
    LABEL = "winget"

    def binaries(self, package_id: str) -> list[str] | None:
        """winget: the manifest declares the commands a package provides.

        winget has no file DATABASE, but winget-pkgs manifests carry `Commands:`
        and, for portable/zip installers, `PortableCommandAlias:` per nested file.
        That is a published binary list, so Windows is verifiable after all.
        """
        parts = package_id.split(".")
        base = ("https://raw.githubusercontent.com/microsoft/winget-pkgs/master/"
                f"manifests/{parts[0][0].lower()}/{'/'.join(parts)}")
        api = ("https://api.github.com/repos/microsoft/winget-pkgs/contents/"
               f"manifests/{parts[0][0].lower()}/{'/'.join(parts)}")
        body = _get(api)
        if not body:
            return None
        try:
            versions = [e["name"] for e in json.loads(body)
                        if e.get("type") == "dir" and e["name"][:1].isdigit()]
        except (json.JSONDecodeError, TypeError):
            return None
        if not versions:
            return None
        newest = sorted(versions, key=lambda v: [
            int(x) if x.isdigit() else 0 for x in v.split(".")])[-1]
        text = ""
        for suffix in ("installer", "locale.en-US", ""):
            name = f"{package_id}.{suffix}.yaml" if suffix else f"{package_id}.yaml"
            text += _get(f"{base}/{newest}/{name}") or ""
        cmds = re.findall(r"^\s*PortableCommandAlias:\s*(\S+)", text, re.M)
        block = re.search(r"^Commands:\n((?:\s*-\s*\S+\n)+)", text, re.M)
        if block:
            cmds += re.findall(r"-\s*(\S+)", block.group(1))
        return sorted(set(cmds)) or None


#: OS class -> the channel that serves it.  Only channels with a published
#: binary list can be verified; the rest are reported as UNVERIFIED, which is
#: an answer ("nobody has checked") and not a pass.
_VERIFIABLE_CHANNELS: dict[str, Channel] = {
    "PacmanLinux": ArchChannel(),
    "DnfLinux": FedoraChannel(),
    "AptLinux": DebianChannel(),
    "ApkLinux": AlpineChannel(),
    "MacOSPlatform": HomebrewChannel(),
    "WindowsPlatform": WingetChannel(),
}


#: Known answers for the channels, every one measured rather than assumed.
#: A channel that cannot answer these is not to be believed about anything
#: else -- and the pairs are chosen so a channel that silently returns nothing
#: fails, which a "does it return a list" check would not catch.
#:
#: (channel, package, must_contain, must_not_contain, why)
_CHANNEL_CASES: list[tuple[str, str, tuple[str, ...], tuple[str, ...], str]] = [
    ("alpine", "7zip", ("7z",), (), "Alpine ships 7z from 7zip, in main"),
    ("alpine", "p7zip", (), ("7z",),
     "p7zip does not exist in Alpine at all — 404 in main and community"),
    ("fedora", "7zip", ("7z",), (), "the package that actually ships 7z"),
    ("fedora", "p7zip", ("7za",), ("7z",),
     "resolves to 7zip-standalone: ships 7za and NO 7z, the #1 defect"),
    ("arch", "p7zip", ("7z",), (),
     "Arch's 7zip declares provides/replaces p7zip, so the hint works"),
    ("homebrew", "p7zip", ("7z",), (),
     "brew p7zip ships 7z/7za/7zr; the modern-looking sevenzip ships only 7zz"),
]


def gate() -> int:
    """Prove each channel answers its known cases before trusting a run.

    Network-bound and slow, so it is an explicit ``--gate`` rather than a
    precondition of every run: this tool is a pre-release check, not CI.
    """
    by_label = {c.LABEL: c for c in _VERIFIABLE_CHANNELS.values()}
    failures: list[str] = []
    for label, pkg, must, must_not, why in _CHANNEL_CASES:
        channel = by_label.get(label)
        if channel is None:
            failures.append(f"{label}: no channel registered")
            print(f"  [FAIL] {label:9} {pkg:8} no such channel")
            continue
        got = channel.binaries(pkg)
        shipped = set(got or ())
        ok = (all(b in shipped for b in must)
              and not any(b in shipped for b in must_not))
        print(f"  [{'ok  ' if ok else 'FAIL'}] {label:9} {pkg:8} -> "
              f"{sorted(shipped) or 'absent'} — {why}")
        if not ok:
            failures.append(f"{label}/{pkg}: expected {must}, not {must_not}, "
                            f"got {sorted(shipped) or 'absent'}")
    print()
    if failures:
        print(f"CHANNEL GATE FAILED — {len(failures)}: " + "; ".join(failures))
        return 1
    print(f"CHANNEL GATE PASSED — {len(_CHANNEL_CASES)} known answers")
    return 0


def _os_classes() -> list[object]:
    """Every concrete OS whose install hints we can render."""
    sys.path.insert(0, str(_ROOT / "src"))
    from trcc.adapters.system.bsd import FreeBsdOS, NetBsdOS, OpenBsdOS
    from trcc.adapters.system.linux import _LINUX_FAMILIES, GenericLinux
    from trcc.adapters.system.macos import MacOSPlatform
    from trcc.adapters.system.windows import WindowsPlatform
    return [*_LINUX_FAMILIES, GenericLinux, FreeBsdOS, OpenBsdOS, NetBsdOS,
            MacOSPlatform, WindowsPlatform]


def check_install_hints() -> list[Finding]:
    """Does every hint we print actually deliver the binary the app probes for?"""
    findings: list[Finding] = []

    for key in sorted(hint_keys_in_source() - set(_HINT_BINARIES)):
        findings.append(Finding(
            "GAP", key,
            f"software_install_hint({key!r}) is called in src but is not in "
            f"_HINT_BINARIES — nothing verifies what that hint delivers"))

    print(f"{'os':16} {'tool':8} {'hint':44} {'delivers':22}")
    print("-" * 94)
    for cls in _os_classes():
        name = cls.__name__
        channel = _VERIFIABLE_CHANNELS.get(name)
        for tool, needed in sorted(_HINT_BINARIES.items()):
            hint = cls().software_install_hint(tool)
            pkg = package_from_hint(hint)
            if not needed:
                continue                       # nothing on PATH to deliver
            if pkg is None:
                print(f"  {name:14} {tool:8} {hint[:42]:44} {'(no package)':22}")
                continue
            if channel is None:
                print(f"  {name:14} {tool:8} {hint[:42]:44} {'UNVERIFIED':22}")
                findings.append(Finding(
                    "GAP", f"{name}/{tool}",
                    f"no published file list for this channel — {hint!r} is "
                    f"unverified; confirm by hand that it provides "
                    f"{'/'.join(needed)}"))
                continue
            label = channel.LABEL
            shipped = channel.binaries(pkg)
            if shipped is None:
                print(f"  {name:14} {tool:8} {hint[:42]:44} {'— absent':22}")
                findings.append(Finding(
                    "STALE", f"{name}/{tool}",
                    f"{label} has no package {pkg!r} (and nothing provides it) "
                    f"— we print {hint!r}"))
                continue
            missing = [b for b in needed if b not in shipped]
            # Report the NEEDED binaries, present or not -- an alphabetical
            # sample of a 54-executable formula says nothing about ffprobe.
            got = ",".join(f"{b}{'' if b in shipped else '(MISSING)'}"
                           for b in needed)
            print(f"  {name:14} {tool:8} {hint[:42]:44} {got:22}")
            if missing:
                findings.append(Finding(
                    "STALE", f"{name}/{tool}",
                    f"{label}: {hint!r} installs {pkg!r}, which does NOT ship "
                    f"{'/'.join(missing)} — the check that printed this hint "
                    f"will still fail after the user runs it"))
    return findings


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
    # Exclude ourselves: trcc-linux "provides" pynvml.py only because the RPM
    # vendors it. Counting that made the tool advise remapping Fedora to
    # trcc-linux and dropping the vendoring that put the file there -- a
    # self-referential loop that would have deleted NVIDIA support on Fedora.
    return sorted(
        pkg for pkg in (
            n.rsplit("-", 2)[0] if re.search(r"-[\d.]+", n) else n for n in names
        ) if not pkg.startswith("trcc")
    )


# Fedora has no pynvml package (dnf provides */pynvml.py → nothing), so the RPM
# vendors it into the payload instead of depending on it.  Same for sounddevice
# historically — see FEDORA_VENDORED.  Anything else absent from a distro's
# declarations is a real hole.
_RPM_VENDORED_OK = {"nvidia-ml-py", "sounddevice"}


def check() -> list[Finding]:
    findings: list[Finding] = []
    deps = pyproject_runtime_deps()
    declared = arch_declared_depends()
    deb_declared = deb_declared_depends()
    rpm_declared = rpm_declared_requires()

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
        a, f = in_arch(a_name), in_fedora(f_name)
        # The deb targets Ubuntu 24.04+ AND Debian 13+ — two archives with
        # different package sets.  Available in EITHER means the deb can and
        # must declare it.  Checking Debian alone read python3-pynvml as
        # 'absent', skipped the assertion, and stayed green over #221.
        d_ubuntu, d_debian = in_ubuntu(d_name), in_debian(d_name)
        d = d_ubuntu or d_debian
        d_src = "ubuntu" if d_ubuntu and not d_debian else ("debian" if d_debian else "")
        d_cell = f"{d} [{d_src}]" if d and d_src else (d or "— absent")
        print(f"  {dep:16} {(a or '— absent'):22} {(f or '— absent'):18} "
              f"{d_cell:30}")

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

        # 5. Declared for Arch but NOT for deb/rpm.  This tool was built to
        #    catch exactly this and originally only checked Arch — so the deb
        #    shipped python3-pynvml as a `Recommends` (apt skips it under
        #    --no-install-recommends) and a reporter found it by hand (#221),
        #    the day after the identical Arch `optdepend` bug (#207). One distro
        #    checked is not the class checked.
        if d and d_name not in deb_declared:
            findings.append(Finding(
                "GAP", dep,
                f"Debian/Ubuntu ships {d_name} ({d}) but the deb does not "
                f"`Depends:` it — apt may skip it and the feature goes silently "
                f"missing"))
        if f and f_name not in rpm_declared and dep not in _RPM_VENDORED_OK:
            findings.append(Finding(
                "GAP", dep,
                f"Fedora ships {f_name} ({f}) but the RPM does not `Requires:` "
                f"it and does not vendor it"))

        # 6. Capability cross-check: names lie, imports do not.  Only the
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


def check_pip_resolved_targets() -> list[Finding]:
    """The three pip-resolved targets must still pip-install the project.

    That single line IS their dependency guarantee.  If a refactor ever replaces
    it (copying files, a frozen requirements list), the target silently stops
    tracking pyproject and nothing else here would notice.
    """
    findings: list[Finding] = []
    for name, text in pip_resolved_targets().items():
        if 'pip install "' not in text and "pip install trcc-linux" not in text \
                and 'pip install ".' not in text and "bin/pip install" not in text:
            findings.append(Finding(
                "GAP", name,
                "no `pip install` of the project found — this target may no "
                "longer resolve its dependencies from pyproject"))
    return findings


def report_matrix() -> None:
    """Every hard dep x every target that can drift."""
    deps = pyproject_runtime_deps(include_win32=True)
    arch, deb, rpm = arch_declared_depends(), deb_declared_depends(), rpm_declared_requires()
    print(f"{'dependency':18} {'arch':10} {'deb':10} {'rpm':10}  (hand-declared targets)")
    print("-" * 62)
    for dep in deps:
        names = _PKG_NAMES.get(dep)
        if names is None:
            print(f"  {dep:16} {'(win32/unmapped — pip-resolved targets only)'}")
            continue
        a, f, d = names
        cells = (
            "yes" if a in arch else ("vendor" if a in ARCH_UNAVAILABLE else "NO"),
            "yes" if d in deb else "NO",
            "yes" if f in rpm else ("vendor" if dep in FEDORA_VENDORED else "NO"),
        )
        print(f"  {dep:16} {cells[0]:10} {cells[1]:10} {cells[2]:10}")
    print()
    print("pip-resolved targets (deps come from the wheel — cannot drift):")
    for name in pip_resolved_targets():
        print(f"  {name}")


def main() -> int:
    if "--gate" in sys.argv:
        return gate()
    print("Checking every shipping target against what it actually provides…\n")
    report_matrix()
    print()
    findings = check() + check_pip_resolved_targets()
    print()
    print("Install hints — does the command we print deliver the binary?\n")
    findings += check_install_hints()
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

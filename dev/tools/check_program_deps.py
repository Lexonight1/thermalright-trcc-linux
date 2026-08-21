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

import gzip
import io
import json
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
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

    @abstractmethod
    def provenance(self, pkg: str) -> str | None:
        """Which repo within this channel serves *pkg*, or None if untracked.

        "Exists" and "exists in EPEL" are different answers to a user: on
        RHEL/Rocky/Alma both 7zip and ffmpeg-free are EPEL-only, so a hint that
        verifies clean still finds nothing on a box without EPEL enabled.
        Alpine has the same split (main vs community) and Debian the same
        again (main/contrib/non-free).

        Abstract rather than defaulted, for the reason the Platform port was
        made fully abstract earlier today: a concrete default lets a channel
        silently not answer, and silence here reads as "no caveat".  A channel
        whose index does not carry it returns None *in its own body*, saying
        so.
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

    def provenance(self, pkg: str) -> str | None:
        """core / extra — the search JSON already carries it."""
        body = _get(f"https://archlinux.org/packages/search/json/?q={pkg}")
        if not body:
            return None
        try:
            results = json.loads(body).get("results", [])
        except json.JSONDecodeError:
            return None
        hit = next((r for r in results
                    if r["pkgname"] == pkg or pkg in (r.get("provides") or [])),
                   None)
        return hit["repo"] if hit else None

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

    def provenance(self, pkg: str) -> str | None:
        """fedora / updates — whichever stock repo resolved it."""
        if not shutil.which("dnf"):
            return None
        try:
            out = subprocess.run(
                ["dnf", "repoquery", "--quiet", "--disablerepo=*",
                 "--enablerepo=fedora", "--enablerepo=updates",
                 "--whatprovides", pkg, "--qf", "%{repoid}\n"],
                capture_output=True, text=True, timeout=120, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        # repoid, not reponame: reponame is the human string ("Fedora 44 -
        # x86_64 - Updates") and splitting it on whitespace produces nonsense.
        # Lines, not tokens, for the same reason.
        names = sorted({ln.strip() for ln in out.splitlines() if ln.strip()})
        return "/".join(names) if names else None

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

    def provenance(self, pkg: str) -> str | None:
        """main / contrib / non-free — madison appends the component.

        ``in_debian`` already parses it: a main package answers "stable", a
        contrib one "stable/contrib".  Reusing that shape rather than a second
        parser, because the first one was wrong once already -- it matched the
        suite key exactly, so every contrib and non-free package read as
        absent, including the NVIDIA advice.
        """
        answer = in_debian(pkg)
        if answer is None:
            return None
        suite = answer.split()[0]
        return suite.split("/", 1)[1] if "/" in suite else "main"

class RpmRepoChannel(Channel):
    """Any RPM channel answerable by querying its published repodata remotely.

    Three channels now share this: EL and openSUSE ask remote repos through
    ``--repofrompath``, and both are two-step for the same reason Fedora is --
    ``repoquery -l p7zip`` is empty because p7zip is a capability, not a
    package, so the provider must be resolved before its files are listed.

    Remote query rather than downloading filelists.xml.gz: AlmaLinux
    AppStream's is 18.5 MB and this answers in a fraction of a second.  It
    needs dnf, so it returns None off an RPM box -- "cannot tell you", which
    is an answer and not a pass.
    """

    #: label -> base URL, in the order a user would get them.  The first repo
    #: that answers is the provenance.
    REPOS: ClassVar[dict[str, str]] = {}

    def _query(self, repo: str, url: str, what: str, qf: str) -> list[str]:
        if not shutil.which("dnf"):
            return []
        try:
            out = subprocess.run(
                ["dnf", "repoquery", "--quiet",
                 f"--repofrompath={repo},{url}", f"--repo={repo}",
                 "--whatprovides", what, "--qf", qf],
                capture_output=True, text=True, timeout=240, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def _files(self, repo: str, url: str, name: str) -> list[str]:
        if not shutil.which("dnf"):
            return []
        try:
            out = subprocess.run(
                ["dnf", "repoquery", "--quiet",
                 f"--repofrompath={repo},{url}", f"--repo={repo}", "-l", name],
                capture_output=True, text=True, timeout=240, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        return [ln.strip() for ln in out.splitlines() if "/bin/" in ln]

    def binaries(self, pkg: str) -> list[str] | None:
        """Binaries *pkg* ships, from the first repo that carries it."""
        for repo, url in self.REPOS.items():
            names = sorted(set(self._query(repo, url, pkg, "%{name}\n")))
            if not names:
                continue
            out: list[str] = []
            for name in names:
                out += [f.rsplit("/", 1)[-1]
                        for f in self._files(repo, url, name)]
            return sorted(set(out)) or None
        return None

    def provenance(self, pkg: str) -> str | None:
        """Which repo answered — the whole point on EL, where it is EPEL."""
        for repo, url in self.REPOS.items():
            if self._query(repo, url, pkg, "%{name}\n"):
                return repo
        return None


#: EL is three repos, not one: BaseOS and AppStream ship with the distro,
#: EPEL does not.  Ordered so the first hit is what a user gets without
#: enabling anything extra.
_EL_REPOS: dict[str, str] = {
    "baseos": "https://repo.almalinux.org/almalinux/9/BaseOS/x86_64/os/",
    "appstream": "https://repo.almalinux.org/almalinux/9/AppStream/x86_64/os/",
    "epel": "https://dl.fedoraproject.org/pub/epel/9/Everything/x86_64/",
}


class ELChannel(RpmRepoChannel):
    """RHEL / CentOS Stream / Rocky / AlmaLinux, via AlmaLinux + EPEL metadata.

    NOT wired to an OS class.  ``DnfLinux`` serves Fedora *and* EL, and
    ``current_platform()`` cannot tell them apart -- so this runs as a second
    opinion on the Fedora hints rather than replacing FedoraChannel.

    Measured 2026-08-21, and the reason provenance exists: both binaries the
    app probes for are EPEL-only on EL.

        /usr/bin/7z      -> 7zip        (epel)      — nothing in base
        /usr/bin/ffmpeg  -> ffmpeg-free (epel)      — nothing in base
        /usr/bin/python3 -> python3     (baseos)    — positive control
        /usr/bin/git     -> git-core    (appstream) — positive control
    """

    LABEL = "el"
    REPOS: ClassVar[dict[str, str]] = _EL_REPOS


class SuseChannel(RpmRepoChannel):
    """openSUSE Tumbleweed's OSS repo — same repodata shape as EL.

    Measured 2026-08-21: /usr/bin/7z comes from ``7zip``, and ``p7zip`` is a
    name it PROVIDES, so ``zypper install p7zip`` resolves and works.  Checked
    the provides rather than assuming, because assuming is what produced a
    false bug report against Alpine earlier the same day.
    """

    LABEL = "suse"
    REPOS: ClassVar[dict[str, str]] = {
        "oss": "https://download.opensuse.org/tumbleweed/repo/oss/",
    }


#: Per-run caches.  These indexes are megabytes and the tool asks a handful of
#: questions, so each is fetched once.
_BSD_INDEX: dict[str, set[str]] = {}
_VOID_INDEX: dict[str, dict] = {}
_NETBSD_PATHS: dict[str, str] = {}


class BsdChannel(Channel):
    """A BSD package repo, answered in two steps because no BSD index has both.

    Every BSD publishes what a user can install (a package directory or a
    package-site index) but none of them publish, in the same place, what each
    package puts on PATH.  So existence and files come from different sources,
    and the split is per-OS.

    Existence is the half that matters most here: both bugs this found were
    "the command names a package that is not there", not "the package ships
    the wrong binary".

    FreshPorts / openports.pl / pkgsrc.se are the browsable sites and are the
    right place to READ about a port -- but they are not the authority for
    "can this be installed today".  freshports.org/archivers/p7zip serves 192
    KB of normal-looking page for a port that says, inside, "This port has
    been deleted."  A 200 there means the port once existed.  Only the package
    repo answers the question a hint makes a promise about.
    """

    #: Directory listing or index whose contents name the installable packages.
    INDEX_URL: ClassVar[str] = ""

    def _index(self) -> set[str]:
        """Package names this repo can install, cached per run."""
        raise NotImplementedError

    def provenance(self, pkg: str) -> str | None:
        """None: each BSD here is a single package set with no sub-repo a user
        must enable.  Stated rather than inherited."""
        return None


class FreeBsdChannel(BsdChannel):
    """FreeBSD, via packagesite.pkg -- the index `pkg` itself resolves against.

    Measured 2026-08-21: `p7zip` is NOT among its 37,485 records.  The port was
    DELETED -- FreshPorts gives the reason: "Unmaintained for years and has
    known vulnerabilities" -- and replaced by `archivers/7-zip`, whose Makefile
    declares PLIST_FILES = bin/7z.  So `pkg install p7zip`, which the app
    printed, names a package that was withdrawn as vulnerable.
    """

    LABEL = "freebsd"
    INDEX_URL = "https://pkg.freebsd.org/FreeBSD:14:amd64/latest/packagesite.pkg"
    _PORTS = "https://raw.githubusercontent.com/freebsd/freebsd-ports/main"

    def _index(self) -> set[str]:
        if self.LABEL in _BSD_INDEX:
            return _BSD_INDEX[self.LABEL]
        names: set[str] = set()
        try:
            with urllib.request.urlopen(self.INDEX_URL, timeout=120) as r:
                blob = r.read()
            # zstd-compressed tar (magic 28 b5 2f fd); zstandard is not in the
            # stdlib, so shell out to the tool the OS already has.
            proc = subprocess.run(["zstd", "-d", "-c"], input=blob,
                                  capture_output=True, timeout=180, check=False)
            with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
                member = tar.extractfile("packagesite.yaml")
                text = member.read().decode("utf-8", "replace") if member else ""
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                OSError, subprocess.SubprocessError, tarfile.TarError,
                KeyError):
            text = ""
        for line in text.splitlines():
            m = re.search(r'"name":"([^"]+)"', line)
            if m:
                names.add(m.group(1))
        _BSD_INDEX[self.LABEL] = names
        return names

    def binaries(self, pkg: str) -> list[str] | None:
        index = self._index()
        if not index or pkg not in index:
            return None
        # The port declares its files; most use PLIST_FILES for a handful of
        # binaries, larger ports a pkg-plist.  Origin is not in the name, so
        # try the obvious category first and fall back to pkg-plist.
        for path in (f"archivers/{pkg}", f"multimedia/{pkg}", f"lang/{pkg}"):
            for leaf in ("Makefile", "pkg-plist"):
                body = _get(f"{self._PORTS}/{path}/{leaf}")
                if not body:
                    continue
                files = re.findall(r"\bbin/([A-Za-z0-9._+-]+)", body)
                if files:
                    return sorted(set(files))
        return []                              # present, files not locatable


def _bsd_basename(filename: str) -> str:
    """``p7zip-17.06.tgz`` -> ``p7zip``.

    Both OpenBSD and pkgsrc name packages ``name-version`` where the version
    starts with a digit -- which is the only reliable split, since names
    themselves contain dashes (``py3-foo``, ``ffmpeg7``).
    """
    stem = filename[:-4] if filename.endswith(".tgz") else filename
    m = re.match(r"^(.*?)-\d", stem)
    return m.group(1) if m else stem


class OpenBsdChannel(BsdChannel):
    """OpenBSD, via the release's package directory.

    Files come from the package itself: OpenBSD publishes no file index, and
    the .tgz is the artifact `pkg_add` installs, so it cannot disagree.

    Measured 2026-08-21: p7zip-17.06 ships bin/7z, 7za, 7zr, and ffmpeg is
    present -- both OpenBSD hints are correct.  Checked per-OS rather than as
    "BSD", because FreeBSD's p7zip is gone and NetBSD's ffmpeg is.
    """

    LABEL = "openbsd"
    _BASE = "https://cdn.openbsd.org/pub/OpenBSD/7.9/packages/amd64/"
    INDEX_URL = _BASE

    def _index(self) -> set[str]:
        if self.LABEL in _BSD_INDEX:
            return _BSD_INDEX[self.LABEL]
        body = _get(self.INDEX_URL) or ""
        names = {_bsd_basename(f)
                 for f in re.findall(r'href="([^"]+\.tgz)"', body)}
        _BSD_INDEX[self.LABEL] = names
        return names

    def _filename(self, pkg: str) -> str | None:
        body = _get(self.INDEX_URL) or ""
        for f in re.findall(r'href="([^"]+\.tgz)"', body):
            if _bsd_basename(f) == pkg:
                return f
        return None

    def binaries(self, pkg: str) -> list[str] | None:
        if pkg not in self._index():
            return None
        name = self._filename(pkg)
        if name is None:
            return None
        try:
            with urllib.request.urlopen(self._BASE + name, timeout=180) as r:
                blob = r.read()
            with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
                members = tar.getnames()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                OSError, tarfile.TarError):
            return []
        return sorted({m.split("/")[-1] for m in members
                       if m.startswith("bin/") or "/bin/" in m})


class NetBsdChannel(BsdChannel):
    """NetBSD, via the binary package directory + pkgsrc PLISTs.

    Files come from pkgsrc rather than the package, because pkgsrc publishes
    them as plain text and the mapping is available: pkg_summary.gz carries
    PKGNAME -> PKGPATH, and PKGPATH is the PLIST's directory.

    Measured 2026-08-21: there is NO package named ffmpeg -- only ffmpeg3
    through ffmpeg7 -- and each installs a VERSIONED binary (bin/ffmpeg7).  So
    `pkg_add ffmpeg`, which the app printed, fails twice over: no such package,
    and even the right one leaves `which ffmpeg` failing.  p7zip is fine and
    ships bin/7z.
    """

    LABEL = "netbsd"
    _BASE = "https://cdn.netbsd.org/pub/pkgsrc/packages/NetBSD/amd64/10.0/All/"
    _PKGSRC = "https://cdn.netbsd.org/pub/pkgsrc/current/pkgsrc"
    INDEX_URL = _BASE

    def _index(self) -> set[str]:
        if self.LABEL in _BSD_INDEX:
            return _BSD_INDEX[self.LABEL]
        body = _get(self.INDEX_URL) or ""
        names = {_bsd_basename(f)
                 for f in re.findall(r'href="([^"]+\.tgz)"', body)}
        _BSD_INDEX[self.LABEL] = names
        return names

    def _pkgpath(self, pkg: str) -> str | None:
        """PKGNAME -> category/port, from pkg_summary.gz."""
        if not _NETBSD_PATHS:
            try:
                with urllib.request.urlopen(self._BASE + "pkg_summary.gz",
                                            timeout=180) as r:
                    text = gzip.decompress(r.read()).decode("utf-8", "replace")
            except (urllib.error.HTTPError, urllib.error.URLError,
                    TimeoutError, OSError, EOFError):
                text = ""
            name = ""
            for line in text.splitlines():
                if line.startswith("PKGNAME="):
                    name = _bsd_basename(line[8:])
                elif line.startswith("PKGPATH=") and name:
                    _NETBSD_PATHS[name] = line[8:]
        return _NETBSD_PATHS.get(pkg)

    def binaries(self, pkg: str) -> list[str] | None:
        if pkg not in self._index():
            return None
        path = self._pkgpath(pkg)
        if path is None:
            return []
        body = _get(f"{self._PKGSRC}/{path}/PLIST") or ""
        return sorted({ln.split("/")[-1] for ln in body.splitlines()
                       if ln.startswith("bin/")})


class VoidChannel(Channel):
    """Void, via the xbps repodata index — no file list needed.

    xbps records what a package puts on PATH as ``cmd:`` entries in its
    ``provides``, so the binary list comes straight out of the index and there
    is nothing to scrape.

    Two transitional hops matter here, and both would read as broken to a
    name-only check.  Measured 2026-08-21:

        p7zip   -> run_depends ['7zip>=0']     (transitional dummy, 618 bytes)
        7zip    -> provides cmd:7z, 7za, 7zr, 7zz
        ffmpeg  -> run_depends ['ffmpeg6>=0']  (transitional dummy)
        ffmpeg6 -> provides cmd:ffmpeg, cmd:ffprobe

    So both Void hints work.  Following the hop is what DebianChannel already
    does; not following it is what produced a false bug report against Alpine
    earlier the same day.
    """

    LABEL = "void"

    #: repo -> repodata URL.  index.plist carries no repository field -- it IS
    #: one repo -- so provenance means "which index answered", and claiming
    #: "current" without querying the others would be a guess.
    REPOS: ClassVar[dict[str, str]] = {
        "current": "https://repo-default.voidlinux.org/current/x86_64-repodata",
        "nonfree": "https://repo-default.voidlinux.org/current/nonfree/"
                   "x86_64-repodata",
        "multilib": "https://repo-default.voidlinux.org/current/multilib/"
                    "x86_64-repodata",
    }

    def _index(self, repo: str = "current") -> dict[str, dict]:
        if repo in _VOID_INDEX:
            return _VOID_INDEX[repo]
        table: dict[str, dict] = {}
        try:
            with urllib.request.urlopen(self.REPOS[repo], timeout=180) as r:
                blob = r.read()
            raw = subprocess.run(["zstd", "-d", "-c"], input=blob,
                                 capture_output=True, timeout=180,
                                 check=False).stdout
            with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
                member = tar.extractfile("index.plist")
                if member is not None:
                    table = plistlib.loads(member.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                OSError, subprocess.SubprocessError, tarfile.TarError,
                plistlib.InvalidFileException, KeyError):
            table = {}
        _VOID_INDEX[repo] = table
        return table

    def _record(self, pkg: str) -> tuple[str, dict] | None:
        for repo in self.REPOS:
            record = self._index(repo).get(pkg)
            if record is not None:
                return repo, record
        return None

    def binaries(self, pkg: str, _depth: int = 0) -> list[str] | None:
        found = self._record(pkg)
        if found is None:
            return None
        record = found[1]
        cmds = [c.split(":", 1)[1].rsplit("-", 1)[0]
                for c in (record.get("provides") or []) if c.startswith("cmd:")]
        if cmds:
            return sorted(set(cmds))
        # Transitional dummy: follow what it depends on, bounded.
        if _depth < 2:
            for dep in (record.get("run_depends") or []):
                found = self.binaries(re.split(r"[<>=]", dep)[0], _depth + 1)
                if found:
                    return found
        return []

    def provenance(self, pkg: str) -> str | None:
        """current / nonfree / multilib — which index answered."""
        found = self._record(pkg)
        return found[0] if found else None


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

    def provenance(self, pkg: str) -> str | None:
        """None: homebrew/core is one tap, and there is no split that changes
        whether `brew install X` works.  Answered here rather than inherited
        so the absence is a statement."""
        return None

#: repo -> {provided name: real package}.  Fetched once per run; APKINDEX is
#: 500 KB for main and 2.1 MB for community, gzipped.
_ALPINE_PROVIDES: dict[str, dict[str, str]] = {}


def _alpine_provides(repo: str) -> dict[str, str]:
    """Alpine's own provides map for one repo, from APKINDEX.

    ``P:`` is the package, ``p:`` its provided names -- each optionally
    ``=version``, which is stripped.  This is Alpine's metadata rather than a
    rendered page, so it answers the question apk itself answers.
    """
    if repo in _ALPINE_PROVIDES:
        return _ALPINE_PROVIDES[repo]
    table: dict[str, str] = {}
    try:
        url = (f"https://dl-cdn.alpinelinux.org/alpine/{_ALPINE_BRANCH}/"
               f"{repo}/x86_64/APKINDEX.tar.gz")
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as r:
            raw = r.read()
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            member = tar.extractfile("APKINDEX")
            text = member.read().decode("utf-8", "replace") if member else ""
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            OSError, tarfile.TarError, KeyError):
        text = ""
    name = ""
    for line in text.splitlines():
        if line.startswith("P:"):
            name = line[2:]
        elif line.startswith("p:") and name:
            for token in line[2:].split():
                table[token.split("=")[0]] = name
    _ALPINE_PROVIDES[repo] = table
    return table


class AlpineChannel(Channel):
    LABEL = "alpine"

    def _files(self, pkg: str) -> list[str]:
        """Files ``pkg`` ships, by NAME, from the published contents index."""
        body = _get(f"https://pkgs.alpinelinux.org/contents?file=&path=&"
                    f"name={pkg}&branch={_ALPINE_BRANCH}&repo=&arch=x86_64")
        if not body:
            return []
        # The file cell is the only pre-wrap td; package/branch/repo are links.
        # Anchoring on the style keeps a layout change loud rather than
        # silently returning nothing.
        return re.findall(r'<td style="white-space: pre-wrap;">([^<]+)</td>',
                          body)

    def binaries(self, pkg: str) -> list[str] | None:
        """Binaries ``pkg`` puts on PATH, resolving `provides` first.

        Resolving provides is not optional, and getting it wrong here produced
        a false user-facing bug report on 2026-08-21: a name-only lookup finds
        no package called ``p7zip`` and concludes ``apk add p7zip`` is broken.
        Alpine's own index says otherwise --

            P:7zip
            p:7zip-virtual p7zip=24.09-r0 cmd:7z=24.09-r0 cmd:7zz=24.09-r0

        -- so apk resolves the provider and the command works.  This is the
        same trap ArchChannel documents directly above, hit while editing the
        file that documents it.

        ``cmd:7z`` in that list is Alpine's exact analogue of
        ``dnf install /usr/bin/7z``: a provider named for the binary.
        """
        files = self._files(pkg)
        if not files:
            for repo in ("main", "community"):
                owner = _alpine_provides(repo).get(pkg)
                if owner:
                    files = self._files(owner)
                    break
        if not files:
            return None                        # genuinely absent
        return [f.rsplit("/", 1)[-1] for f in files if "/bin/" in f]

    def provenance(self, pkg: str) -> str | None:
        """main / community — and the split is load-bearing here.

        ffmpeg is in community, not main, so a minimal Alpine with only main
        enabled will not find it however correct the package name is.
        """
        for repo in ("main", "community"):
            body = _get(f"https://pkgs.alpinelinux.org/contents?file=&path=&"
                        f"name={pkg}&branch={_ALPINE_BRANCH}&repo={repo}"
                        f"&arch=x86_64")
            if body and re.search(r'<td style="white-space: pre-wrap;">', body):
                return repo
            owner = _alpine_provides(repo).get(pkg)
            if owner:
                return repo
        return None

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

    def provenance(self, pkg: str) -> str | None:
        """None: winget-pkgs is a single manifest repository with no
        sub-repos a user must enable."""
        return None

#: OS class -> the channel that serves it.  Only channels with a published
#: binary list can be verified; the rest are reported as UNVERIFIED, which is
#: an answer ("nobody has checked") and not a pass.
_VERIFIABLE_CHANNELS: dict[str, Channel] = {
    "PacmanLinux": ArchChannel(),
    "DnfLinux": FedoraChannel(),
    "AptLinux": DebianChannel(),
    "ApkLinux": AlpineChannel(),
    "ZypperLinux": SuseChannel(),
    "FreeBsdOS": FreeBsdChannel(),
    "OpenBsdOS": OpenBsdChannel(),
    "NetBsdOS": NetBsdChannel(),
    "XbpsLinux": VoidChannel(),
    "MacOSPlatform": HomebrewChannel(),
    "WindowsPlatform": WingetChannel(),
}


#: Known answers for the channels, every one measured rather than assumed.
#: A channel that cannot answer these is not to be believed about anything
#: else -- and the pairs are chosen so a channel that silently returns nothing
#: fails, which a "does it return a list" check would not catch.
#:
#: (channel, package, must_contain, must_not_contain, expected_provenance, why)
#: expected_provenance is "" when the channel does not track one.
_CHANNEL_CASES: list[
    tuple[str, str, tuple[str, ...], tuple[str, ...], str, str]
] = [
    ("alpine", "7zip", ("7z",), (), "main", "Alpine ships 7z from 7zip"),
    ("alpine", "p7zip", ("7z",), (), "main",
     "no package is NAMED p7zip, but 7zip provides it — apk resolves it, so "
     "the hint works; a name-only lookup called this broken"),
    ("fedora", "7zip", ("7z",), (), "fedora/updates",
     "the package that actually ships 7z"),
    ("fedora", "p7zip", ("7za",), ("7z",), "fedora/updates",
     "resolves to 7zip-standalone: ships 7za and NO 7z, the #1 defect"),
    ("arch", "p7zip", ("7z",), (), "extra",
     "Arch's 7zip declares provides/replaces p7zip, so the hint works"),
    ("homebrew", "p7zip", ("7z",), (), "",
     "brew p7zip ships 7z/7za/7zr; the modern-looking sevenzip ships only 7zz"),
    # EL, every value read off AlmaLinux/EPEL metadata rather than from what I
    # expect the code to return -- the two python3/git rows are positive
    # controls, so a repo that silently fails to load cannot pass.
    ("el", "/usr/bin/7z", ("7z",), (), "epel",
     "EPEL-only on EL: a Fedora-clean hint finds nothing without EPEL"),
    ("el", "/usr/bin/ffmpeg", ("ffmpeg", "ffprobe"), (), "epel",
     "ffmpeg-free is EPEL on EL, unlike Fedora where it is in the base repo"),
    ("el", "/usr/bin/python3", ("python3",), (), "baseos",
     "positive control — proves BaseOS actually loaded"),
    ("el", "/usr/bin/git", ("git",), (), "appstream",
     "positive control — proves AppStream actually loaded"),
    ("suse", "/usr/bin/7z", ("7z",), (), "oss",
     "openSUSE ships 7z from 7zip, in the OSS repo"),
    ("suse", "p7zip", ("7z",), (), "oss",
     "p7zip is a name 7zip PROVIDES — zypper resolves it, so the hint works"),
    ("suse", "/usr/bin/python3", ("python3",), (), "oss",
     "positive control — proves the OSS repo actually loaded"),
    # BSD, each value read off the package repo or the package itself.  The
    # three diverge, which is why they are three channels and not one "bsd".
    ("freebsd", "p7zip", (), ("7z",), "",
     "DELETED port — FreshPorts: 'unmaintained for years and has known "
     "vulnerabilities'.  We printed `pkg install p7zip`"),
    ("freebsd", "7-zip", ("7z",), (), "",
     "the package that replaced it, per its Makefile PLIST_FILES"),
    ("freebsd", "ffmpeg", ("ffmpeg", "ffprobe"), (), "",
     "positive control — proves the 37k-record index actually loaded"),
    ("openbsd", "p7zip", ("7z",), (), "",
     "present and correct — read from the .tgz pkg_add installs"),
    ("openbsd", "ffmpeg", ("ffmpeg", "ffprobe"), (), "",
     "positive control"),
    ("netbsd", "p7zip", ("7z",), (), "",
     "present and correct, from the pkgsrc PLIST"),
    ("netbsd", "ffmpeg", (), ("ffmpeg",), "",
     "NO package named ffmpeg — only ffmpeg3..7.  We printed `pkg_add ffmpeg`"),
    ("netbsd", "ffmpeg7", ("ffmpeg7",), ("ffmpeg",), "",
     "positive control AND the second half of the bug: the binary is "
     "VERSIONED, so `which ffmpeg` fails even after installing the right one"),
    ("void", "p7zip", ("7z",), (), "current",
     "transitional dummy depending on 7zip, whose cmd: provides carry 7z"),
    ("void", "ffmpeg", ("ffmpeg", "ffprobe"), (), "current",
     "transitional dummy depending on ffmpeg6 — both Void hints work"),
    ("void", "p7zip-unrar", ("7z",), (), "nonfree",
     "positive control — proves the nonfree index loaded, not just current"),
]


def gate() -> int:
    """Prove each channel answers its known cases before trusting a run.

    Network-bound and slow, so it is an explicit ``--gate`` rather than a
    precondition of every run: this tool is a pre-release check, not CI.
    """
    by_label = {c.LABEL: c for c in _VERIFIABLE_CHANNELS.values()}
    by_label["el"] = ELChannel()          # not wired to an OS class; see there
    failures: list[str] = []
    for label, pkg, must, must_not, prov, why in _CHANNEL_CASES:
        channel = by_label.get(label)
        if channel is None:
            failures.append(f"{label}: no channel registered")
            print(f"  [FAIL] {label:9} {pkg:8} no such channel")
            continue
        got = channel.binaries(pkg)
        shipped = set(got or ())
        where = channel.provenance(pkg)
        ok = (all(b in shipped for b in must)
              and not any(b in shipped for b in must_not)
              and (not prov or where == prov))
        print(f"  [{'ok  ' if ok else 'FAIL'}] {label:9} {pkg:16} -> "
              f"{sorted(shipped) or 'absent'}"
              f"{f' ({where})' if where else ''} — {why}")
        if not ok:
            failures.append(f"{label}/{pkg}: expected {must} not {must_not}"
                            f"{f' in {prov}' if prov else ''}, got "
                            f"{sorted(shipped) or 'absent'} ({where})")
    print()
    if failures:
        print(f"CHANNEL GATE FAILED — {len(failures)}: " + "; ".join(failures))
        return 1
    print(f"CHANNEL GATE PASSED — {len(_CHANNEL_CASES)} known answers")
    return 0


#: Why an OS has no channel.  "No published file list" was true when every
#: unverified channel was unexamined; it is not true of Nix, and a gate that
#: states a false reason is worse than one that states none -- whoever picks
#: this up needs to know what exists and what it costs.
_NO_CHANNEL_REASON: dict[str, str] = {
    "NixLinux":
        "nixpkgs meta.mainProgram proves the PRIMARY binary (p7zip -> 7z) but "
        "cannot enumerate the rest, so it cannot confirm ffmpeg also ships "
        "ffprobe; returning a partial list would report a false MISSING.  The "
        "real index is nix-index-database (1.8 MB '-small' asset, 102 MB "
        "full), in a custom binary format that needs a parser",
    "GenericLinux":
        "no manager was detected, so there is no channel to ask",
}


def _os_classes() -> list[object]:
    """Every concrete OS whose install hints we can render."""
    sys.path.insert(0, str(_ROOT / "src"))
    from trcc.adapters.system.bsd import FreeBsdOS, NetBsdOS, OpenBsdOS
    from trcc.adapters.system.linux import _LINUX_FAMILIES, GenericLinux
    from trcc.adapters.system.macos import MacOSPlatform
    from trcc.adapters.system.windows import WindowsPlatform
    return [*_LINUX_FAMILIES, GenericLinux, FreeBsdOS, OpenBsdOS, NetBsdOS,
            MacOSPlatform, WindowsPlatform]


def _aliases_of(binary: str) -> tuple[str, ...]:
    """Every executable name that counts as *binary*, from the app's own table.

    Imported lazily so this tool still runs when src/ is not importable -- it
    is a pre-release check and must degrade to "the literal name only" rather
    than crash.
    """
    try:
        sys.path.insert(0, str(_ROOT / "src"))
        from trcc.core.toolchain import TOOL_ALIASES
    except ImportError:
        return (binary,)
    return TOOL_ALIASES.get(binary, (binary,))


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
                    f"{_NO_CHANNEL_REASON.get(name, 'no channel')} — {hint!r} "
                    f"is unverified; confirm by hand that it provides "
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
            # EL caveat: DnfLinux serves Fedora AND RHEL/Rocky/Alma, and
            # current_platform() cannot tell them apart.  A hint that verifies
            # against Fedora can still find nothing on EL, because both
            # binaries the app probes for are EPEL-only there.
            if name == "DnfLinux":
                where = ELChannel().provenance(pkg)
                if where == "epel":
                    findings.append(Finding(
                        "GAP", f"{name}/{tool}",
                        f"{hint!r} works on Fedora, but on RHEL/Rocky/Alma "
                        f"{pkg!r} is in EPEL — a box without EPEL enabled "
                        f"finds nothing.  DnfLinux serves both and cannot "
                        f"tell them apart"))
                elif where is None and shutil.which("dnf"):
                    findings.append(Finding(
                        "GAP", f"{name}/{tool}",
                        f"{hint!r} resolves on Fedora but NOTHING provides "
                        f"{pkg!r} in EL baseos/appstream/epel — RHEL/Rocky/"
                        f"Alma users get no package at all"))

            # A binary counts as delivered under ANY of its aliases: the app
            # resolves them (core.toolchain), so pkgsrc's ffmpeg7 satisfies
            # "ffmpeg" and Fedora's 7za satisfies "7z".  Checking the literal
            # name would report a false MISSING for a hint that works.
            #
            # IMPORTED, not restated: the alias table lives in core.toolchain
            # and a second copy here would drift from the code it describes.
            missing = [b for b in needed
                       if not any(a in shipped for a in _aliases_of(b))]
            # Report the NEEDED binaries, present or not -- an alphabetical
            # sample of a 54-executable formula says nothing about ffprobe.
            # Same alias rule as `missing` above -- computing them two ways is
            # how the row printed "ffmpeg(MISSING)" for a hint that satisfied
            # the check.  Name the alias that satisfied it, so a NetBSD row
            # reads ffmpeg7 rather than a bare ffmpeg the box does not have.
            def _shown(binary: str) -> str:
                for alias in _aliases_of(binary):
                    if alias in shipped:
                        return alias if alias == binary else f"{alias}={binary}"
                return f"{binary}(MISSING)"

            got = ",".join(_shown(b) for b in needed)
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

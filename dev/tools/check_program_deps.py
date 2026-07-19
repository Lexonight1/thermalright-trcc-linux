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
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _ROOT / "pyproject.toml"
_RELEASE_YML = _ROOT / ".github" / "workflows" / "release.yml"

_FEDORA_RELEASE = "f44"
_UBUNTU_SERIES = "questing"
_TIMEOUT = 15


# ── the name mapping (single source of truth; the offline test imports it) ──

# pyproject name -> (arch, fedora, debian) package names.
_PKG_NAMES: dict[str, tuple[str, str, str]] = {
    "PySide6":          ("pyside6", "python3-pyside6", "python3-pyside6.qtcore"),
    "numpy":            ("python-numpy", "python3-numpy", "python3-numpy"),
    "psutil":           ("python-psutil", "python3-psutil", "python3-psutil"),
    "pyusb":            ("python-pyusb", "python3-pyusb", "python3-usb"),
    "hidapi":           ("python-hidapi", "python3-hidapi", "python3-hidapi"),
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
    "hidapi": "hid",
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
    print("Checking every shipping target against what it actually provides…\n")
    report_matrix()
    print()
    findings = check() + check_pip_resolved_targets()
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

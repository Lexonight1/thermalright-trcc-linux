"""Install integrity — which trcc is actually running, and is it the one
the user thinks?

Every field here exists because a real issue was unanswerable without it:

* **version / source_version / bytecode_stale** — a stale ``.pyc`` served
  ``9.9.9`` from a file that read ``9.8.8``.  Python's timestamp check
  compares source mtime+size against the values baked into the ``.pyc``;
  a rewrite that preserves both (same-length version strings do) leaves
  the cache authoritative and wrong, silently, forever.
* **executables / duplicates** — ``shutil.which`` returns the FIRST match.
  Two ``trcc`` on PATH pointing at two interpreters is the commonest cause
  of "I upgraded and nothing changed" (#175: six weeks on v9.7.0 while
  installing 9.8.3 repeatedly; #220: three releases behind on "latest").
* **installer** — the ``INSTALLER`` file pip writes is the only honest
  answer.  The old per-OS heuristics guessed from PATH and were wrong for
  rpm / deb / venv / source checkouts alike.

Deliberately dependency-free (stdlib only) and side-effect-free: it is the
first thing a broken install runs, so it must not need a working App.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# `__version__ = "9.8.8"` — matched against the SOURCE text, never exec'd.
_VERSION_RE = re.compile(r"""^__version__\s*=\s*['"]([^'"]+)['"]""", re.MULTILINE)

_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Executable:
    """One ``trcc`` found on PATH, and the interpreter it runs under."""

    path: Path
    interpreter: str = _UNKNOWN


@dataclass(frozen=True, slots=True)
class InstallInfo:
    """Everything needed to answer "which trcc is this?"."""

    version: str
    source_version: str
    module_path: Path | None
    interpreter: str
    python: str
    installer: str
    executables: tuple[Executable, ...] = ()

    @property
    def bytecode_stale(self) -> bool:
        """Imported version disagrees with its own source file.

        Means Python is serving a cached ``.pyc`` that no longer matches
        the code on disk — so every claim about "what version is this" is
        untrustworthy until the cache is cleared.
        """
        return (
            self.source_version != _UNKNOWN
            and self.version != self.source_version
        )

    @property
    def duplicates(self) -> bool:
        """More than one ``trcc`` on PATH — an upgrade can land on either."""
        return len(self.executables) > 1

    @property
    def healthy(self) -> bool:
        return not self.bytecode_stale and not self.duplicates


def _read_source_version(module_path: Path | None) -> str:
    """Parse ``__version__`` out of the package's own ``__version__.py``.

    Text-parsed rather than imported: importing would hit the very bytecode
    cache we are trying to check, and would always agree with itself.
    """
    if module_path is None:
        return _UNKNOWN
    source = module_path / "__version__.py"
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as e:
        log.debug("_read_source_version: cannot read %s: %s", source, e)
        return _UNKNOWN
    m = _VERSION_RE.search(text)
    if m is None:
        log.warning("_read_source_version: no __version__ assignment in %s", source)
        return _UNKNOWN
    log.debug("_read_source_version: %s → %s", source, m.group(1))
    return m.group(1)


def _shebang_interpreter(path: Path) -> str:
    """The interpreter a console script runs under, from its shebang."""
    try:
        with path.open("rb") as fh:
            first = fh.readline(256)
    except OSError as e:
        log.debug("_shebang_interpreter: cannot read %s: %s", path, e)
        return _UNKNOWN
    if not first.startswith(b"#!"):
        return _UNKNOWN
    return first[2:].decode("utf-8", "replace").strip()


def _find_executables(name: str = "trcc") -> tuple[Executable, ...]:
    """Every ``name`` on PATH — not just the first.

    ``shutil.which`` stops at the first hit, which is precisely what hides a
    duplicate install from the person trying to debug one.
    """
    exts = [""]
    if os.name == "nt":
        exts = [e.lower() for e in os.environ.get("PATHEXT", ".EXE").split(os.pathsep)]
    found: list[Executable] = []
    seen: set[Path] = set()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        for ext in exts:
            candidate = Path(entry) / f"{name}{ext}"
            try:
                resolved = candidate.resolve()
                usable = candidate.is_file() and os.access(candidate, os.X_OK)
            except OSError:
                continue
            if not usable or resolved in seen:
                continue
            seen.add(resolved)
            found.append(Executable(candidate, _shebang_interpreter(candidate)))
    log.debug("_find_executables: %d %r on PATH", len(found), name)
    return tuple(found)


def detect_installer() -> str:
    """How this package got here — ``INSTALLER`` metadata over guesswork.

    Supersedes the per-OS "is trcc on PATH? then pip" heuristics, which
    answered "pip" for rpm, deb, venv and source checkouts alike.
    """
    if getattr(sys, "frozen", False):
        return "pyinstaller"
    if "pipx" in sys.prefix:
        return "pipx"
    try:
        from importlib.metadata import PackageNotFoundError, distribution
        try:
            installer = (distribution("trcc-linux").read_text("INSTALLER") or "").strip()
        except PackageNotFoundError:
            log.debug("detect_installer: trcc-linux has no distribution metadata")
            return "source"
    except (OSError, ImportError) as e:
        log.debug("detect_installer: metadata unavailable: %s", e)
        return _UNKNOWN
    if installer:
        log.debug("detect_installer: INSTALLER=%s", installer)
        return installer
    # Installed as a distribution but nobody claimed it — a distro package
    # manager (rpm/deb/pacman) drops the dist-info without an INSTALLER file.
    return "package"


def collect_install_info() -> InstallInfo:
    """Gather the running install's identity.  Never raises."""
    log.info("collect_install_info: called")
    import trcc

    module_file = getattr(trcc, "__file__", None)
    module_path = Path(module_file).parent if module_file else None
    info = InstallInfo(
        version=trcc.__version__,
        source_version=_read_source_version(module_path),
        module_path=module_path,
        interpreter=sys.executable,
        python=f"{sys.version_info.major}.{sys.version_info.minor}."
               f"{sys.version_info.micro}",
        installer=detect_installer(),
        executables=_find_executables(),
    )
    if info.bytecode_stale:
        log.warning(
            "collect_install_info: STALE BYTECODE — imported %s but %s says %s; "
            "clear __pycache__",
            info.version, module_path, info.source_version,
        )
    if info.duplicates:
        log.warning(
            "collect_install_info: %d trcc on PATH (%s) — an upgrade may land "
            "on one and leave the other running",
            len(info.executables),
            ", ".join(str(e.path) for e in info.executables),
        )
    log.info("collect_install_info: %s via %s (%s)",
             info.version, info.installer, info.interpreter)
    return info

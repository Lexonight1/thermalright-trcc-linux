"""Which executable actually provides a tool on this machine.

The app used to name its external tools literally — ``shutil.which("ffmpeg")``,
``["7z", "x", …]`` — at ten call sites.  That works on the machines where the
distro happens to use those exact names, and fails everywhere else, in a way
no install hint can repair.

Measured 2026-08-21, both ends of the same defect:

* **Fedora.**  ``dnf install p7zip`` installs a working 7-Zip whose binary is
  ``7za``.  Every one of our sites looks for ``7z``, so the tool is present and
  unusable, and the doctor prints "install p7zip" again.  Verified: ``7za``
  lists and extracts our ``.7z`` archives perfectly — the program was fine, the
  *name* was the bug.
* **NetBSD.**  There is no ``ffmpeg`` package at all: pkgsrc ships ``ffmpeg4``
  through ``ffmpeg7``, each installing a *versioned* binary (``bin/ffmpeg7``).
  So even the correct install leaves every site failing, and pointing the hint
  at ``ffmpeg7`` would make the command succeed while the check still failed —
  the Fedora pathology again.

Resolving the name removes both, and removes the whole class: no per-distro
package knowledge is needed to find a program that is already installed.

**An alias here is a claim that must be proven, not a list someone typed.**
The first draft of this table excluded ``7za`` as "a reduced build" on the
strength of its shorter format list.  It supports 7z/zip/xz/tar/gzip/bzip2 —
every format we touch — and the app only ever handles ``.7z``.  Running it
settled in seconds what reading about it got wrong.
``tests/test_toolchain.py`` runs each alias present on the box against a real
archive, with the exact commands ``data_install`` uses.
"""
from __future__ import annotations

import logging
import shutil

log = logging.getLogger(__name__)

#: Logical tool -> executables that genuinely provide it, best first.
#:
#: Order is preference, not fallback-in-desperation: the unversioned name is
#: what a normal install gives, and the rest are what specific distros ship.
#:
#: ``7zr`` is included deliberately.  It is the smallest build and supports
#: little beyond the 7z format — which is the only format we use, and it was
#: verified to list and extract our archives.  Excluding it on reputation
#: would leave a machine that has it reporting "7z not on PATH".
TOOL_ALIASES: dict[str, tuple[str, ...]] = {
    # pkgsrc versions the binary (bin/ffmpeg7); everyone else does not.
    "ffmpeg": ("ffmpeg", "ffmpeg7", "ffmpeg6", "ffmpeg5", "ffmpeg4"),
    "ffprobe": ("ffprobe", "ffprobe7", "ffprobe6", "ffprobe5", "ffprobe4"),
    # 7z: Debian/Arch/openSUSE.  7zz: modern upstream, Alpine + openSUSE.
    # 7za: what Fedora's p7zip installs.  7zr: minimal, 7z-format only.
    "7z": ("7z", "7zz", "7za", "7zr"),
}


def resolve(tool: str) -> str | None:
    """The executable name to invoke for *tool*, or None if none is present.

    Returns a NAME rather than a path: callers pass it as ``argv[0]`` and the
    OS resolves it again, which keeps behaviour identical to the hardcoded
    strings this replaced.

    An unknown tool falls back to its own name, so adding a
    ``resolve("newtool")`` call site cannot silently return None just because
    nobody added a row here.
    """
    for candidate in TOOL_ALIASES.get(tool, (tool,)):
        if shutil.which(candidate):
            if candidate != tool:
                log.info("toolchain.resolve: %s -> %s (alias)", tool, candidate)
            else:
                log.debug("toolchain.resolve: %s -> %s", tool, candidate)
            return candidate
    log.warning("toolchain.resolve: no executable for %r — tried %s",
                tool, ", ".join(TOOL_ALIASES.get(tool, (tool,))))
    return None


def present(tool: str) -> bool:
    """Whether *tool* is usable under any of its names."""
    found = resolve(tool) is not None
    log.debug("toolchain.present: %s -> %s", tool, found)
    return found

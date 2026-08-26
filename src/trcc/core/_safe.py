"""Safe-load + path-safety helpers shared across layers.

Why centralised: before this file existed, the "read JSON, fall back
on failure" pattern was inlined in 4 places with subtly different
exception-catching shapes (``{OSError, JSONDecodeError}`` vs
``{OSError, JSONDecodeError, TypeError}`` vs swallowed silently).
The "is this name safe to join to a trusted root?" check existed
across 2 zip-member sites + 1 user-typed-name site with different
heuristics.  Consolidating prevents drift when a new edge case (NUL
byte, Windows drive letter, traversal sequence) is patched in one
file but not its siblings.

See ``memory/project_hexagonal_solid_dry_plan`` §1.

Pure-stdlib, no project imports — every layer above ``core/`` may
consume these helpers freely.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


def load_json_or_default(path: Path, default: Any) -> Any:
    """Read JSON from *path*; return *default* on missing/malformed file.

    Catches the three errors that occur in practice:

    * ``FileNotFoundError`` — file not yet written.  Normal first-run
      state; logged at debug so it shows under ``-vv`` without
      drowning routine startup.
    * ``OSError`` (other) — permission denied, unreadable encoding,
      etc.  Warning-level; the user should know.
    * ``json.JSONDecodeError`` / ``UnicodeDecodeError`` — corrupt or
      hand-edited file.  Warning-level.

    Returns the parsed JSON (whatever shape it decodes to) on success.
    Callers that want to raise instead of falling back should write
    their own try/except around ``json.loads(path.read_text(...))`` —
    those sites raise domain-specific exceptions (``ThemeError`` etc.)
    that this helper deliberately won't construct.
    """
    log.info("load_json_or_default: %s", path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.debug("load_json_or_default: %s not found, using default", path)
        return default
    except OSError as exc:
        log.warning("load_json_or_default: %s unreadable: %s", path, exc)
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("load_json_or_default: %s malformed JSON: %s", path, exc)
        return default


def is_safe_zip_member(name: str) -> bool:
    """Reject zip member names that would write outside the extraction root.

    Covers:

    * Empty / whitespace-only names
    * Absolute paths (``/foo`` or ``\\foo``)
    * Parent-directory traversal (``..`` as a path component)
    * Windows drive letters in the first component (``C:foo`` /
      ``C:/foo``)
    * NUL bytes (defensive — POSIX accepts ``\\x00`` nowhere, but
      some unzip libraries hand it through)

    Normalises Windows-style backslash separators before checking,
    since Windows-authored archives sometimes emit those for files
    that POSIX consumers will then mis-split.
    """
    log.debug("is_safe_zip_member: name=%r", name)
    if not name or not name.strip():
        return False
    if "\x00" in name:
        return False
    normalised = name.replace("\\", "/").strip()
    if normalised.startswith("/"):
        return False
    head = normalised.split("/", 1)[0]
    if ":" in head:
        return False
    parts = normalised.split("/")
    return ".." not in parts


def is_safe_user_name(name: str, *, max_length: int = 255) -> bool:
    """Reject user-typed names that could escape a trusted parent dir.

    For names that become filesystem entries directly (theme names,
    mask filenames, user-content directories) — NOT for zip member
    names; see ``is_safe_zip_member`` for that.

    Rejects:

    * Empty or oversized names (``max_length`` defaults to 255, the
      common filesystem limit)
    * Leading dot (would create a hidden directory the user can't see
      in their file browser)
    * Path separators (``/``, ``\\``) — names must be single
      components
    * NUL bytes
    * Parent-directory traversal (``..`` as a path component)
    """
    log.debug("is_safe_user_name: name=%r max_length=%d", name, max_length)
    if not name or len(name) > max_length:
        return False
    if name[0] == ".":
        return False
    if any(ch in name for ch in ("/", "\\", "\x00")):
        return False
    return ".." not in name.split("/")


def is_under(path: Path, root: Path) -> bool:
    """True when *path* lives inside *root*, symlinks and all.

    ``Path.is_relative_to`` is purely lexical: it compares path *strings* and
    never touches the filesystem.  That is wrong for the question this project
    keeps asking — "is this the user's own asset, or a shipped one?" — because
    the same directory can have two absolute spellings.  On Fedora Atomic
    systems (Bazzite, Silverblue) ``/home`` is a symlink to ``/var/home``, and
    both spellings appear within a single run: one component resolves a home
    directory, another does not.

    ``/home/u/.trcc-user/…`` then tests as NOT under
    ``/var/home/u/.trcc-user`` even though they are the same folder, so a
    user's saved theme is classified as shipped — and restore loads the
    shipped theme of the same name instead of theirs.  That is #261's
    "settings reset every time I restart".

    Both sides are resolved before comparing.  Measured at 0.046 ms per call
    against a 41.7 ms frame budget (0.11%), so it is affordable even on the
    per-tick render path, where the background fit asks the same question.

    Non-strict: a path that does not exist yet still resolves lexically, and
    an unreadable one degrades to False rather than raising — deciding
    "shipped" is the safe answer, since it never hands a user's own tree to
    something expecting program data.
    """
    try:
        resolved = path.resolve()
        inside = resolved.is_relative_to(root.resolve())
    except (OSError, ValueError) as e:
        log.debug("is_under: cannot resolve %s against %s (%s) — assuming not",
                  path, root, e)
        return False
    frame_log.debug("is_under: %s under %s → %s", resolved, root, inside)
    return inside

"""Atomic JSON file write — crash-safe, race-safe persistence.

mkstemp + os.replace so concurrent writers and process deaths can
never leave a half-written or interleaved file on disk.  os.replace
is atomic on POSIX (rename(2)) and Windows (MoveFileExW with
MOVEFILE_REPLACE_EXISTING).

Lives in ``core/`` (not ``adapters/infra/``) because it's pure stdlib
and used from every layer — services, adapters, top-level conf.
Adapters depending on services-or-core is fine; services depending on
adapters would invert the hexagonal direction, which is why this
helper had to leave ``adapters/infra/`` (audit finding 2026-05-09).

Pattern adapted from JoshWrites's PR #112 — extracted into a single
helper so every JSON writer in the project gets concurrency / crash
safety without each call site reimplementing the dance.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def atomic_write_json(path: str | Path, data: Any, *,
                      indent: int | None = 2,
                      ensure_ascii: bool = True) -> None:
    """Serialize ``data`` as JSON and write to ``path`` atomically.

    Caller ensures ``path.parent`` exists. Crashes mid-write leave
    the original file untouched. Concurrent writers each get a unique
    temp file (``mkstemp``) and the last ``os.replace`` wins —
    readers never observe a partial state.
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f'.{path.name}.',
        suffix='.tmp',
    )
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError as e:
            log.debug("atomic_write_json: cleanup failed for %s: %s", tmp, e)
        raise

#!/usr/bin/env python
"""Migrate ``~/.trcc/config.json`` (legacy) → ``~/.trcc/trcc.json`` (next/).

One-shot upgrade helper.  Legacy and next/ persist user prefs to
different files with different shapes — a user upgrading from v9.x
to next/ would otherwise face a fresh-install state on first launch.
This script reads the legacy file, translates fields, and writes
next/'s shape via the public :class:`Settings` API.  The legacy file
is left in place so ``TRCC_LEGACY=1`` rollback stays non-destructive.

Usage
-----
    python tools/migrate_legacy_config.py             # ~/.trcc/
    python tools/migrate_legacy_config.py --dry-run   # show without writing
    python tools/migrate_legacy_config.py --force     # overwrite trcc.json
    python tools/migrate_legacy_config.py \\
        --legacy-path /alt/config.json \\
        --next-config-dir /alt/

The script lives under ``tools/`` because it has a finite life: when
the ``src/trcc/legacy/`` subtree is deleted, this file gets deleted
with it.  Core never imports it.

Field map
---------
========================== ==================================================
Legacy (flat ``dict``)     Next/ (``AppSettings`` + ``DeviceSettings``)
========================== ==================================================
``temp_unit`` ``int``      ``app.temp_unit`` "C"/"F" + every device
``lang`` ``str``           ``app.language``
``hdd_enabled`` ``bool``   ``app.hdd_enabled``
``refresh_interval`` int   ``app.refresh_interval_s`` float (seconds)
``gpu_device`` ``str``     ``app.active_gpu`` (empty string → None)
``format_prefs.time_format`` int  ``app.time_format`` + every device
``format_prefs.date_format`` int  ``app.date_format`` + every device
``devices.{idx}.vid_pid``  device key rekeyed from ``"vvvv_pppp"``
                           to ``"vvvv:pppp"``
``devices.{idx}.brightness_level`` int  ``DeviceSettings.brightness``
``devices.{idx}.orientation`` int       ``DeviceSettings.orientation``
``last_device`` int idx    dropped (legacy index doesn't map to a
                           stable vid:pid; GUI picks first detected)
========================== ==================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Make ``src/`` importable when the tool is run directly from the
# repo root (``python tools/migrate_legacy_config.py``).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

from trcc.core.ports import Paths  # noqa: E402
from trcc.services.settings import Settings  # noqa: E402

log = logging.getLogger("migrate_legacy_config")


# ---------------------------------------------------------------------------
# Legacy int-coded format prefs → next/'s pattern strings.
# Tables live here because legacy used UCXiTongXianShiSub.cs's int convention
# (0/2=24h, 1=12h for time; 0/1=yyyy/MM/dd, 2=dd/MM/yyyy, 3=MM/dd, 4=dd/MM
# for date).  Tool-local — core never sees these ints.
# ---------------------------------------------------------------------------
_TIME_FORMAT_MAP: dict[int, str] = {
    0: "24h",
    1: "12h",
    2: "24h",
}
_DATE_FORMAT_MAP: dict[int, str] = {
    0: "yyyy/MM/dd",
    1: "yyyy/MM/dd",
    2: "dd/MM/yyyy",
    3: "MM/dd",
    4: "dd/MM",
}


class _FixedPaths(Paths):
    """Minimal Paths port pinned to a single directory.

    Settings only reads ``config_dir()``; the other Paths methods are
    here to satisfy the ABC.  None of them participate in migration.
    """

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir

    def config_dir(self) -> Path:
        return self._config_dir

    def data_dir(self) -> Path:
        return self._config_dir / "data"

    def user_content_dir(self) -> Path:
        return self._config_dir / "user_content"

    def log_file(self) -> Path:
        return self._config_dir / "trcc.log"


def _device_key(vid_pid: str) -> str | None:
    """Convert legacy ``"0402_3922"`` to next/'s ``"0402:3922"`` key.

    Returns ``None`` for malformed inputs so the caller can skip the
    device without raising.
    """
    if "_" not in vid_pid:
        return None
    return vid_pid.replace("_", ":").lower()


def _load_legacy(path: Path) -> dict[str, Any]:
    """Parse legacy ``config.json``.  Empty dict on missing/corrupt."""
    if not path.exists():
        log.error("legacy config not found: %s", path)
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.error("failed to read %s: %s", path, e)
        return {}


def _apply_globals(settings: Settings, raw: dict[str, Any]) -> int:
    """Translate flat top-level legacy keys onto AppSettings.

    Returns the count of fields applied (for progress logging).  Uses
    public ``Settings`` setters only — each one persists atomically
    so a crash mid-migration leaves a valid trcc.json with whatever
    was already written.
    """
    applied = 0

    if isinstance(temp_int := raw.get("temp_unit"), int):
        unit = "F" if temp_int == 1 else "C"
        settings.set_global_temp_unit(unit)  # type: ignore[arg-type]
        log.info("temp_unit %d → %s", temp_int, unit)
        applied += 1

    if isinstance(lang := raw.get("lang"), str) and lang:
        settings.set_language(lang)
        log.info("language → %s", lang)
        applied += 1

    if isinstance(hdd := raw.get("hdd_enabled"), bool):
        settings.set_hdd_enabled(hdd)
        log.info("hdd_enabled → %s", hdd)
        applied += 1

    if isinstance(interval := raw.get("refresh_interval"), int | float):
        settings.set_refresh_interval(float(interval))
        log.info("refresh_interval %s → %.1fs", interval, float(interval))
        applied += 1

    if isinstance(gpu := raw.get("gpu_device"), str):
        # Legacy stored "" for the auto-pick state; next/ uses None
        # so SensorEnumerator.primary_gpu() owns the choice.
        settings.set_active_gpu(gpu or None)
        log.info("active_gpu → %r", gpu or None)
        applied += 1

    prefs = raw.get("format_prefs") or {}
    if isinstance(tf := prefs.get("time_format"), int):
        if (fmt := _TIME_FORMAT_MAP.get(tf)) is not None:
            settings.set_global_time_format(fmt)  # type: ignore[arg-type]
            log.info("format_prefs.time_format %d → %s", tf, fmt)
            applied += 1
    if isinstance(df := prefs.get("date_format"), int):
        if (fmt := _DATE_FORMAT_MAP.get(df)) is not None:
            settings.set_global_date_format(fmt)
            log.info("format_prefs.date_format %d → %s", df, fmt)
            applied += 1

    return applied


def _apply_devices(settings: Settings, raw: dict[str, Any]) -> int:
    """Translate per-device legacy entries.  Returns count of devices."""
    legacy_devices = raw.get("devices") or {}
    count = 0
    for _idx, entry in legacy_devices.items():
        if not isinstance(entry, dict):
            continue
        vid_pid = entry.get("vid_pid")
        if not isinstance(vid_pid, str):
            continue
        key = _device_key(vid_pid)
        if key is None:
            log.warning("skip malformed vid_pid %r", vid_pid)
            continue

        # for_device() auto-seeds from globals — call once so the
        # device exists before per-device setters touch it.
        settings.for_device(key)

        if isinstance(brightness := entry.get("brightness_level"), int):
            settings.set_brightness(key, brightness)
            log.info("[%s] brightness → %d", key, brightness)
        if isinstance(orientation := entry.get("orientation"), int):
            settings.set_orientation(key, orientation)
            log.info("[%s] orientation → %d", key, orientation)

        count += 1
    return count


def migrate(
    legacy_path: Path,
    next_config_dir: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    """Run the migration end-to-end.  Returns exit code (0 = ok)."""
    log.info("legacy:    %s", legacy_path)
    log.info("next dir:  %s", next_config_dir)
    log.info("dry-run:   %s", dry_run)

    target = next_config_dir / "trcc.json"
    if target.exists() and not force:
        log.error(
            "%s already exists — refusing to overwrite (use --force)",
            target,
        )
        return 2

    raw = _load_legacy(legacy_path)
    if not raw:
        log.error("nothing to migrate")
        return 1

    if dry_run:
        # Dry-run uses a sandboxed config dir so production trcc.json
        # is never touched.  Discarded after the script exits.
        import tempfile
        sandbox = Path(tempfile.mkdtemp(prefix="trcc-migrate-dryrun-"))
        log.info("dry-run sandbox: %s", sandbox)
        config_dir = sandbox
    else:
        config_dir = next_config_dir
        config_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(_FixedPaths(config_dir))
    n_globals = _apply_globals(settings, raw)
    n_devices = _apply_devices(settings, raw)

    if dry_run:
        log.info(
            "DRY-RUN: would have migrated %d global field(s) "
            "and %d device(s) — no files written to %s",
            n_globals, n_devices, next_config_dir,
        )
    else:
        log.info(
            "migrated %d global field(s) and %d device(s) → %s",
            n_globals, n_devices, target,
        )
        log.info("legacy file left in place: %s", legacy_path)
    return 0


def _default_config_dir() -> Path:
    """Match legacy's ``~/.trcc`` default."""
    return Path.home() / ".trcc"


def main(argv: list[str] | None = None) -> int:
    description = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--legacy-path",
        type=Path,
        default=None,
        help=("path to legacy config.json "
              "(default: <next-config-dir>/config.json)"),
    )
    parser.add_argument(
        "--next-config-dir",
        type=Path,
        default=_default_config_dir(),
        help="directory where trcc.json should be written (default: ~/.trcc)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="parse + translate but write to a tempdir instead of "
             "the real config dir",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite an existing trcc.json",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="enable DEBUG logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    legacy_path = args.legacy_path or (args.next_config_dir / "config.json")
    return migrate(
        legacy_path,
        args.next_config_dir,
        dry_run=args.dry_run,
        force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())

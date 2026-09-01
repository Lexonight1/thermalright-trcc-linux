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

from trcc.core.ports import Paths
from trcc.services.settings import Settings

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
        if isinstance(led_cfg := entry.get("led_config"), dict):
            _apply_led_config(settings, key, led_cfg)

        count += 1
    return count


# Legacy v5.0.x → current LED-config key aliases.  Matches
# ``legacy/services/led_config.py::_ALIASES`` so users upgrading from
# the carousel-named era don't lose their per-zone rotation state.
_LED_LEGACY_ALIASES: dict[str, str] = {
    "zone_carousel": "zone_sync",
    "zone_carousel_zones": "zone_sync_zones",
    "zone_carousel_interval": "zone_sync_interval",
}


def _apply_led_config(
    settings: Settings, key: str, led_cfg: dict[str, Any],
) -> None:
    """Translate one device's ``led_config`` block into next/'s setters.

    Mirrors ``legacy/services/led_config.py::load_led_config`` shape:
      1. apply v5.0.x aliases (zone_carousel → zone_sync etc.) so the
         older key names land on the canonical fields,
      2. dispatch each scalar through the public per-LED-field setter,
      3. resize the per-zone list to ``zones`` length then set each
         zone's mode/color/brightness/on.
    """
    # Step 1 — alias rewrites (don't overwrite the canonical key if both
    # appear; load_led_config in legacy had the same precedence).
    for old, new in _LED_LEGACY_ALIASES.items():
        if old in led_cfg and new not in led_cfg:
            led_cfg[new] = led_cfg[old]

    # Step 2 — scalar fields.  Legacy field names differ in three places
    # (segments_on→segment_on, is_timer_24h→clock_24h via setter,
    # is_week_sunday→week_sunday via setter, zone_sync_interval→
    # zone_sync_interval_ticks via setter); next/ setters take the
    # legacy semantic and persist under the canonical name.
    from trcc.core.led_models import LEDMode

    if isinstance(mode := led_cfg.get("mode"), int):
        try:
            settings.set_led_mode(key, LEDMode(mode))
            log.info("[%s] led_mode → %d", key, mode)
        except ValueError:
            log.warning("[%s] led_mode %r not a valid LEDMode — skipping",
                        key, mode)
    if isinstance(color := led_cfg.get("color"), list) and len(color) == 3:
        rgb = (int(color[0]), int(color[1]), int(color[2]))
        settings.set_led_color(key, rgb)
        log.info("[%s] led_color → %s", key, rgb)
    if isinstance(brightness := led_cfg.get("brightness"), int):
        settings.set_led_brightness(key, brightness)
        log.info("[%s] led_brightness → %d", key, brightness)
    if isinstance(global_on := led_cfg.get("global_on"), bool):
        settings.set_led_global_on(key, global_on)
        log.info("[%s] led_global_on → %s", key, global_on)
    if isinstance(temp_src := led_cfg.get("temp_source"), str) and temp_src:
        try:
            settings.set_led_temp_source(key, temp_src)
            log.info("[%s] led_temp_source → %s", key, temp_src)
        except ValueError as e:
            log.warning("[%s] %s", key, e)
    if isinstance(load_src := led_cfg.get("load_source"), str) and load_src:
        try:
            settings.set_led_load_source(key, load_src)
            log.info("[%s] led_load_source → %s", key, load_src)
        except ValueError as e:
            log.warning("[%s] %s", key, e)
    if isinstance(is_24h := led_cfg.get("is_timer_24h"), bool):
        settings.set_led_clock_24h(key, is_24h)
        log.info("[%s] led_clock_24h → %s", key, is_24h)
    if isinstance(sun_first := led_cfg.get("is_week_sunday"), bool):
        settings.set_led_week_start(key, sun_first)
        log.info("[%s] led_week_sunday → %s", key, sun_first)
    # ``disk_index`` is deliberately NOT migrated: it addressed a psutil
    # partition list while the metric came from the thermal list, so nothing
    # ever applied it.  ``SetDiskDevice`` replaced it with a stable sensor KEY,
    # which a legacy positional index cannot be translated into.
    if isinstance(mem_ratio := led_cfg.get("memory_ratio"), bool):
        settings.set_led_memory_ratio(key, mem_ratio)
        log.info("[%s] led_memory_ratio → %s", key, mem_ratio)
    if isinstance(zone_sync := led_cfg.get("zone_sync"), bool):
        settings.set_led_zone_sync(key, zone_sync)
        log.info("[%s] led_zone_sync → %s", key, zone_sync)
    if isinstance(zsi := led_cfg.get("zone_sync_interval"), int):
        settings.set_led_zone_sync_interval(key, zsi)
        log.info("[%s] led_zone_sync_interval_ticks → %d", key, zsi)

    # Per-segment on/off (legacy 'segments_on' → next/ 'segment_on')
    if isinstance(segs := led_cfg.get("segments_on"), list):
        for i, on in enumerate(segs):
            if isinstance(on, bool):
                settings.set_led_segment_on(key, i, on)
        log.info("[%s] led_segment_on → %d entries", key, len(segs))

    # Zone-sync inclusion mask (legacy carousel zones list).  Next/'s
    # default ``LedDeviceSettings.zone_sync_zones`` is an empty list
    # (zones are sized later by ``set_led_zone_count``), so write the
    # whole legacy list in one shot — anything beyond the eventual
    # zone count gets truncated naturally on the device side.
    # Bouncing zone_sync through its setter re-triggers ``_save()`` so
    # the bulk attribute write above persists even when no later setter
    # fires (e.g. a legacy config with mask but no per-zone state).
    if isinstance(zsz := led_cfg.get("zone_sync_zones"), list):
        led = settings.for_led(key)
        led.zone_sync_zones = [bool(v) for v in zsz]
        settings.set_led_zone_sync(key, led.zone_sync)
        log.info("[%s] zone_sync_zones → %d entries", key, len(zsz))

    # Per-zone state — needs the zone count to be sized first.
    if isinstance(zones := led_cfg.get("zones"), list) and zones:
        settings.set_led_zone_count(key, len(zones))
        for i, zc in enumerate(zones):
            if not isinstance(zc, dict):
                continue
            zone_mode = zc.get("mode")
            zone_color = zc.get("color")
            zone_brightness = zc.get("brightness")
            zone_on = zc.get("on")
            mode_enum: LEDMode | None = None
            if isinstance(zone_mode, int):
                try:
                    mode_enum = LEDMode(zone_mode)
                except ValueError:
                    mode_enum = None
            rgb: tuple[int, int, int] | None = None
            if isinstance(zone_color, list) and len(zone_color) == 3:
                rgb = (
                    int(zone_color[0]),
                    int(zone_color[1]),
                    int(zone_color[2]),
                )
            settings.set_led_zone(
                key, i,
                mode=mode_enum,
                color=rgb,
                brightness=(zone_brightness
                            if isinstance(zone_brightness, int) else None),
                on=(zone_on if isinstance(zone_on, bool) else None),
            )
        log.info("[%s] zones → %d entries", key, len(zones))


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

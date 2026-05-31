"""Settings — user preferences, persisted to ``trcc.json``.

Two layers:
  * AppSettings — global (language, data refresh interval, active device).
  * DeviceSettings (in core.models) — per-device (orientation, brightness,
    current theme, time/date format, temp unit, overlay enabled).

Settings is constructed with a Paths port; it owns config file location
and atomic save.  Adapters / UIs read and write through the singleton
exposed on the App hub.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Literal, cast

from ..core._safe import load_json_or_default
from ..core.errors import ConfigError
from ..core.led_models import LedDeviceSettings, LEDMode, LedZoneSettings
from ..core.models import DeviceSettings, FitMode, OverlayElement, TempUnit
from ..core.ports import Paths

log = logging.getLogger(__name__)


# =========================================================================
# AppSettings — global (non-device-specific) preferences
# =========================================================================


@dataclass
class AppSettings:
    """Global user preferences."""
    language: str = "en"
    refresh_interval_s: float = 2.0
    active_device: str | None = None
    autostart_configured: bool = False
    ui_theme: Literal["dark", "light", "system"] = "system"
    # Include HDD metrics in sensor broadcasts.  Off by default so spinning
    # disks don't spin up just to report idle stats.
    hdd_enabled: bool = False
    # Global default temp_unit — propagates to every DeviceSettings.temp_unit
    # via Settings.set_global_temp_unit so overlay renderers see a consistent
    # unit across all devices. Per-device override still possible via the
    # per-device set_temp_unit() (used by tests / non-GUI consumers).
    temp_unit: TempUnit = "C"
    # Global default clock formats — same cross-cutting pattern as
    # ``temp_unit``.  Multi-LCD users expect ONE "24h" toggle that
    # applies everywhere; ``Settings.set_global_time_format`` /
    # ``set_global_date_format`` write here and fan out to every
    # existing DeviceSettings.  ``Settings.for_device`` seeds new
    # DeviceSettings instances from these globals.  Per-device
    # override remains available via the per-device setters.
    time_format: Literal["12h", "24h"] = "24h"
    date_format: str = "yyyy/MM/dd"
    # User-selected primary GPU (e.g. 'nvidia:0', 'amd:0', or 'intel:igpu').
    # None = let SensorEnumerator.primary_gpu() pick automatically.
    active_gpu: str | None = None


# =========================================================================
# Settings — the service
# =========================================================================


_CONFIG_FILE = "trcc.json"
# Pre-cutover next/ persisted to ``trcc-next.json``; ``_load`` reads
# it as a fallback so users who started before the rename keep their
# state.  Next ``_save`` writes the new filename.
_PRE_CUTOVER_CONFIG_FILE = "trcc-next.json"


class Settings:
    """Per-app and per-device settings with JSON persistence.

    Thread-safe via RLock.  Atomic save (write to tmp, fsync, rename).
    Missing / corrupt config file falls back to defaults.
    """

    def __init__(self, paths: Paths) -> None:
        self._paths = paths
        self._lock = RLock()
        self._app = AppSettings()
        self._devices: dict[str, DeviceSettings] = {}
        self._led_devices: dict[str, LedDeviceSettings] = {}
        self._load()

    # ── AppSettings surface ───────────────────────────────────────────

    @property
    def app(self) -> AppSettings:
        return self._app

    def set_language(self, lang: str) -> None:
        log.info("set_language: lang=%s", lang)
        with self._lock:
            self._app.language = lang
            self._save()

    def set_active_device(self, key: str | None) -> None:
        log.info("set_active_device: key=%s", key)
        with self._lock:
            self._app.active_device = key
            self._save()

    def set_refresh_interval(self, seconds: float) -> None:
        log.info("set_refresh_interval: seconds=%s", seconds)
        with self._lock:
            self._app.refresh_interval_s = max(0.1, seconds)
            self._save()

    def device_keys(self) -> tuple[str, ...]:
        """Return the tuple of device keys with persisted settings.

        Snapshot — safe to iterate even if a concurrent call mutates
        the underlying dict.  Used by cross-cutting Commands that need
        to publish one per-device event after a global setter has
        already fanned the value out.
        """
        log.debug("device_keys: called")
        with self._lock:
            return tuple(self._devices)

    def set_global_temp_unit(self, unit: TempUnit) -> tuple[str, ...]:
        """Set the global default temp_unit and propagate to every device.

        Cross-cutting setter: keeps AppSettings.temp_unit and every
        DeviceSettings.temp_unit in lockstep so overlay renderers can
        read either layer and see the same answer.  Returns the tuple
        of device keys that were touched so callers can publish a
        per-device event without poking private state.
        """
        log.info("set_global_temp_unit: unit=%s", unit)
        with self._lock:
            self._app.temp_unit = unit
            for device_settings in self._devices.values():
                device_settings.temp_unit = unit
            self._save()
            return tuple(self._devices)

    def set_global_time_format(
        self, fmt: Literal["12h", "24h"],
    ) -> tuple[str, ...]:
        """Set the global default clock format and propagate to every device.

        Same lockstep shape as :meth:`set_global_temp_unit` — write
        ``app.time_format`` then fan out to every existing
        ``DeviceSettings.time_format``.  Returns the tuple of device
        keys touched so the calling Command can publish per-device
        events.
        """
        log.info("set_global_time_format: fmt=%s", fmt)
        with self._lock:
            self._app.time_format = fmt
            for device_settings in self._devices.values():
                device_settings.time_format = fmt
            self._save()
            return tuple(self._devices)

    def set_global_date_format(self, fmt: str) -> tuple[str, ...]:
        """Set the global default date pattern and propagate to every device.

        Companion to :meth:`set_global_time_format`.  ``fmt`` is the
        ICU-ish pattern (``yyyy/MM/dd``, ``dd/MM/yyyy``, etc.) that
        ``DisplayService.compute_clock`` reads per render.
        """
        log.info("set_global_date_format: fmt=%s", fmt)
        with self._lock:
            self._app.date_format = fmt
            for device_settings in self._devices.values():
                device_settings.date_format = fmt
            self._save()
            return tuple(self._devices)

    def set_active_gpu(self, gpu_key: str | None) -> None:
        """Set the user-selected primary GPU. None = auto-pick."""
        log.info("set_active_gpu: gpu_key=%s", gpu_key)
        with self._lock:
            self._app.active_gpu = gpu_key
            self._save()

    # ── DeviceSettings surface ────────────────────────────────────────

    def for_device(self, key: str) -> DeviceSettings:
        """Return the DeviceSettings for *key*, creating defaults if absent.

        A freshly-minted ``DeviceSettings`` inherits the global format
        prefs (``app.time_format`` / ``app.date_format`` / ``app.temp_unit``)
        so a newly-attached LCD picks up the user's chosen formats
        instead of falling back to the dataclass's compile-time
        defaults.  Existing devices keep whatever was persisted —
        only first-touch is seeded.
        """
        log.debug("for_device: key=%s", key)
        with self._lock:
            if key not in self._devices:
                self._devices[key] = DeviceSettings(
                    time_format=self._app.time_format,
                    date_format=self._app.date_format,
                    temp_unit=self._app.temp_unit,
                )
            return self._devices[key]

    def set_orientation(self, key: str, degrees: int) -> None:
        log.info("set_orientation: key=%s degrees=%d", key, degrees)
        with self._lock:
            self.for_device(key).orientation = degrees
            self._save()

    def set_brightness(self, key: str, percent: int) -> None:
        log.info("set_brightness: key=%s percent=%d", key, percent)
        with self._lock:
            self.for_device(key).brightness = max(0, min(100, percent))
            self._save()

    def set_current_theme(self, key: str, theme_name: str | None) -> None:
        log.info("set_current_theme: key=%s theme=%s", key, theme_name)
        with self._lock:
            self.for_device(key).current_theme = theme_name
            self._save()

    def set_temp_unit(self, key: str, unit: TempUnit) -> None:
        log.info("set_temp_unit: key=%s unit=%s", key, unit)
        with self._lock:
            self.for_device(key).temp_unit = unit
            self._save()

    def set_time_format(self, key: str, fmt: Literal["12h", "24h"]) -> None:
        log.info("set_time_format: key=%s fmt=%s", key, fmt)
        with self._lock:
            self.for_device(key).time_format = fmt
            self._save()

    def set_date_format(self, key: str, fmt: str) -> None:
        log.info("set_date_format: key=%s fmt=%s", key, fmt)
        with self._lock:
            self.for_device(key).date_format = fmt
            self._save()

    def set_overlay_enabled(self, key: str, enabled: bool) -> None:
        log.info("set_overlay_enabled: key=%s enabled=%s", key, enabled)
        with self._lock:
            self.for_device(key).overlay_enabled = enabled
            self._save()

    def set_mask_position(self, key: str,
                          position: tuple[int, int] | None) -> None:
        log.info("set_mask_position: key=%s position=%s", key, position)
        with self._lock:
            self.for_device(key).mask_position = position
            self._save()

    def set_mask_path(self, key: str, path: str | None) -> None:
        """Set the user-supplied mask path (overrides the theme's mask).

        Passing ``None`` also clears any mask-supplied overlay elements
        — picking "no mask" reverts the metric layout to the active
        theme's own elements.  Mask + its DC layout are coupled.
        """
        log.info("set_mask_path: key=%s path=%s", key, path)
        with self._lock:
            dev = self.for_device(key)
            dev.mask_path = path
            if path is None:
                dev.mask_overlay_elements = None
            self._save()

    def set_mask_overlay_elements(
        self, key: str, elements: list[OverlayElement] | None,
    ) -> None:
        """Store the mask's DC overlay-element layout for a device.

        ApplyMask calls this with the mask's parsed DC elements — the
        renderer uses them as an override over ``theme.config["elements"]``
        so the mask's metric layout survives a theme swap (cloud
        background swap, local theme reselection, etc.).
        """
        log.info("set_mask_overlay_elements: key=%s count=%s", key,
                 None if elements is None else len(elements))
        with self._lock:
            self.for_device(key).mask_overlay_elements = (
                list(elements) if elements is not None else None
            )
            self._save()

    def set_mask_visible(self, key: str, visible: bool) -> None:
        """Toggle mask visibility for the device."""
        log.info("set_mask_visible: key=%s visible=%s", key, visible)
        with self._lock:
            self.for_device(key).mask_visible = visible
            self._save()

    def set_background_path(self, key: str, path: str | None) -> None:
        """Set the cloud-background override (video path) for a device.

        Passing ``None`` clears the override — the device falls back to
        the active theme's bundled background.  LoadCloudTheme sets it;
        the GUI's local-theme click handler (and ``LoadTheme`` for any
        non-cloud path) should clear it so picking a local theme reverts
        to the theme's own background.
        """
        log.info("set_background_path: key=%s path=%s", key, path)
        with self._lock:
            self.for_device(key).background_path = path
            self._save()

    def set_fit_mode(self, key: str, mode: FitMode) -> None:
        log.info("set_fit_mode: key=%s mode=%s", key, mode)
        with self._lock:
            self.for_device(key).fit_mode = mode
            self._save()

    def set_split_mode(self, key: str, mode: int) -> None:
        """Set per-device Dynamic Island style (0=off, 1/2/3=A/B/C)."""
        log.info("set_split_mode: key=%s mode=%d", key, mode)
        with self._lock:
            self.for_device(key).split_mode = mode
            self._save()

    # ── LED-device settings ───────────────────────────────────────────

    def for_led(self, key: str) -> LedDeviceSettings:
        """Return the LedDeviceSettings for *key*, defaulting on first touch."""
        log.debug("for_led: key=%s", key)
        with self._lock:
            if key not in self._led_devices:
                self._led_devices[key] = LedDeviceSettings()
            return self._led_devices[key]

    def set_led_mode(self, key: str, mode: LEDMode) -> None:
        log.info("set_led_mode: key=%s mode=%s", key, mode)
        with self._lock:
            self.for_led(key).mode = mode
            self._save()

    def set_led_color(self, key: str, color: tuple[int, int, int]) -> None:
        log.info("set_led_color: key=%s color=%s", key, color)
        with self._lock:
            self.for_led(key).color = color
            self._save()

    def set_led_brightness(self, key: str, percent: int) -> None:
        log.info("set_led_brightness: key=%s percent=%d", key, percent)
        with self._lock:
            self.for_led(key).brightness = max(0, min(100, percent))
            self._save()

    def set_led_global_on(self, key: str, on: bool) -> None:
        log.info("set_led_global_on: key=%s on=%s", key, on)
        with self._lock:
            self.for_led(key).global_on = on
            self._save()

    def set_led_test_mode(self, key: str, enabled: bool) -> None:
        log.info("set_led_test_mode: key=%s enabled=%s", key, enabled)
        with self._lock:
            self.for_led(key).test_mode = enabled
            self._save()

    def set_led_temp_source(self, key: str, source: str) -> None:
        log.info("set_led_temp_source: key=%s source=%s", key, source)
        if source not in ("cpu", "gpu"):
            raise ValueError(f"Invalid temp source: {source!r}; expected 'cpu' or 'gpu'")
        with self._lock:
            self.for_led(key).temp_source = cast(Literal["cpu", "gpu"], source)
            self._save()

    def set_led_load_source(self, key: str, source: str) -> None:
        log.info("set_led_load_source: key=%s source=%s", key, source)
        if source not in ("cpu", "gpu"):
            raise ValueError(f"Invalid load source: {source!r}; expected 'cpu' or 'gpu'")
        with self._lock:
            self.for_led(key).load_source = cast(Literal["cpu", "gpu"], source)
            self._save()

    def set_led_zone_count(self, key: str, count: int) -> None:
        """Resize the zones list — called by Led.connect once style is known."""
        log.info("set_led_zone_count: key=%s count=%d", key, count)
        with self._lock:
            settings = self.for_led(key)
            current = len(settings.zones)
            if count == current:
                return
            if count > current:
                settings.zones.extend(
                    LedZoneSettings() for _ in range(count - current)
                )
            else:
                settings.zones = settings.zones[:count]
            self._save()

    def set_led_zone(
        self, key: str, zone: int,
        *,
        mode: LEDMode | None = None,
        color: tuple[int, int, int] | None = None,
        brightness: int | None = None,
        on: bool | None = None,
    ) -> None:
        """Update one zone's persistent state — only the given fields change."""
        log.info("set_led_zone: key=%s zone=%d fields=%s",
                 key, zone,
                 {k: v for k, v in {
                     "mode": mode, "color": color,
                     "brightness": brightness, "on": on,
                 }.items() if v is not None})
        with self._lock:
            settings = self.for_led(key)
            if not 0 <= zone < len(settings.zones):
                raise IndexError(
                    f"Zone {zone} out of range for {key} "
                    f"(have {len(settings.zones)})"
                )
            z = settings.zones[zone]
            if mode is not None:
                z.mode = mode
            if color is not None:
                z.color = color
            if brightness is not None:
                z.brightness = max(0, min(100, brightness))
            if on is not None:
                z.on = on
            self._save()

    def set_led_zone_sync(self, key: str, enabled: bool) -> None:
        log.info("set_led_zone_sync: key=%s enabled=%s", key, enabled)
        with self._lock:
            self.for_led(key).zone_sync = enabled
            self._save()

    def set_led_zone_sync_interval(self, key: str, ticks: int) -> None:
        log.info("set_led_zone_sync_interval: key=%s ticks=%d", key, ticks)
        with self._lock:
            self.for_led(key).zone_sync_interval_ticks = max(1, ticks)
            self._save()

    def set_led_selected_zone(self, key: str, zone: int) -> None:
        """Pick the active zone — UIs use this when the user clicks a fan/strip."""
        log.info("set_led_selected_zone: key=%s zone=%d", key, zone)
        with self._lock:
            settings = self.for_led(key)
            if zone < 0:
                raise ValueError(f"selected_zone must be >= 0, got {zone}")
            settings.selected_zone = zone
            self._save()

    def set_led_segment_on(self, key: str, index: int, on: bool) -> None:
        """Flip one segment on/off (segment-display devices only)."""
        log.info("set_led_segment_on: key=%s index=%d on=%s", key, index, on)
        with self._lock:
            settings = self.for_led(key)
            if index < 0:
                raise IndexError(f"segment index must be >= 0, got {index}")
            # Grow segment_on lazily so callers don't need to know the
            # segment count up front (style discovery may not have run yet).
            while len(settings.segment_on) <= index:
                settings.segment_on.append(True)
            settings.segment_on[index] = on
            self._save()

    def set_led_clock_24h(self, key: str, is_24h: bool) -> None:
        """Set the 12h/24h clock display format for LC2-style devices."""
        log.info("set_led_clock_24h: key=%s is_24h=%s", key, is_24h)
        with self._lock:
            self.for_led(key).clock_24h = is_24h
            self._save()

    def set_led_week_start(self, key: str, sunday_first: bool) -> None:
        """Pick week-start: ``True`` = Sunday-first, ``False`` = Monday-first."""
        log.info("set_led_week_start: key=%s sunday_first=%s",
                 key, sunday_first)
        with self._lock:
            self.for_led(key).week_sunday = sunday_first
            self._save()

    def set_led_memory_ratio(self, key: str, ratio_mode: bool) -> None:
        """Memory display mode: ``True`` = percentage, ``False`` = GB used."""
        log.info("set_led_memory_ratio: key=%s ratio_mode=%s",
                 key, ratio_mode)
        with self._lock:
            self.for_led(key).memory_ratio = ratio_mode
            self._save()

    def set_led_disk_index(self, key: str, index: int) -> None:
        """Pick which disk's read/write stats to surface on the LED."""
        log.info("set_led_disk_index: key=%s index=%d", key, index)
        if index < 0:
            raise ValueError(f"disk_index must be >= 0, got {index}")
        with self._lock:
            self.for_led(key).disk_index = index
            self._save()

    def set_hdd_enabled(self, enabled: bool) -> None:
        """Toggle HDD inclusion in sensor metrics broadcasts."""
        log.info("set_hdd_enabled: enabled=%s", enabled)
        with self._lock:
            self._app.hdd_enabled = enabled
            self._save()

    def set_background_mode(
        self, key: str,
        mode: Literal["theme", "color", "transparent"],
    ) -> None:
        """Pick what fills the LCD behind overlays."""
        log.info("set_background_mode: key=%s mode=%s", key, mode)
        if mode not in ("theme", "color", "transparent"):
            raise ValueError(
                f"background_mode must be 'theme' / 'color' / 'transparent', "
                f"got {mode!r}",
            )
        with self._lock:
            self.for_device(key).background_mode = mode
            self._save()

    def set_overlay_background(
        self, key: str, color: tuple[int, int, int],
    ) -> None:
        """Set the solid-color background used when background_mode='color'."""
        log.info("set_overlay_background: key=%s color=%s", key, color)
        for label, value in zip("rgb", color, strict=False):
            if not 0 <= value <= 255:
                raise ValueError(
                    f"{label} channel out of range (0-255): {value}",
                )
        with self._lock:
            self.for_device(key).overlay_background = color
            self._save()

    # ── User overlay elements ─────────────────────────────────────────

    def add_user_overlay_element(
        self, key: str, element: OverlayElement,
    ) -> None:
        """Append a user-edited overlay element.

        Caller is responsible for ensuring ``element.id`` is unique within
        this device's list (the AddOverlayElement Command does the UUID
        generation + uniqueness check).
        """
        log.info("add_user_overlay_element: key=%s id=%s type=%s",
                 key, element.id, element.type)
        with self._lock:
            self.for_device(key).user_overlay_elements.append(element)
            self._save()

    def update_user_overlay_element(
        self,
        key: str,
        element_id: str,
        **fields: object,
    ) -> OverlayElement:
        """Apply ``fields`` to the element with the given id.  Returns
        the updated element.  Raises ``KeyError`` if id is unknown."""
        log.info("update_user_overlay_element: key=%s id=%s fields=%s",
                 key, element_id, sorted(fields))
        with self._lock:
            elements = self.for_device(key).user_overlay_elements
            for idx, e in enumerate(elements):
                if e.id == element_id:
                    for name, value in fields.items():
                        if value is not None and hasattr(e, name):
                            setattr(e, name, value)
                    self._save()
                    return elements[idx]
            raise KeyError(f"Overlay element {element_id!r} not found")

    def delete_user_overlay_element(
        self, key: str, element_id: str,
    ) -> OverlayElement:
        """Remove the element by id and return it.

        Raises ``KeyError`` if id is unknown.
        """
        log.info("delete_user_overlay_element: key=%s id=%s", key, element_id)
        with self._lock:
            elements = self.for_device(key).user_overlay_elements
            for idx, e in enumerate(elements):
                if e.id == element_id:
                    removed = elements.pop(idx)
                    self._save()
                    return removed
            raise KeyError(f"Overlay element {element_id!r} not found")

    def set_user_overlay_elements(
        self, key: str, elements: list[OverlayElement],
    ) -> None:
        """Replace the user-overlay list wholesale (bulk SetOverlayConfig)."""
        log.info("set_user_overlay_elements: key=%s count=%d",
                 key, len(elements))
        with self._lock:
            self.for_device(key).user_overlay_elements = list(elements)
            self._save()

    # ── Atomic snapshot / restore (used by ExportConfig/ImportConfig) ─

    def snapshot_device(self, key: str) -> dict[str, Any]:
        """Return a JSON-ready snapshot of one device's DeviceSettings.

        Same shape used internally by ``_save`` — tuples already coerced
        to lists by ``_json_default`` is applied at JSON write time.
        Caller passes the dict to ``json.dump`` with that same default.
        """
        log.info("snapshot_device: key=%s", key)
        with self._lock:
            return asdict(self.for_device(key))

    def restore_device(self, key: str, snapshot: dict[str, Any]) -> None:
        """Replace a device's entire DeviceSettings atomically from a dict.

        Inverse of :meth:`snapshot_device`.  Tolerant of older snapshots
        (unknown fields ignored; missing fields fall back to dataclass
        defaults) so JSON exports survive schema evolution.
        """
        log.info("restore_device: key=%s fields=%d", key, len(snapshot))
        with self._lock:
            self._devices[key] = _device_settings_from_dict(snapshot)
            self._save()

    # ── Slideshow ─────────────────────────────────────────────────────

    def set_slideshow_enabled(self, key: str, enabled: bool) -> None:
        log.info("set_slideshow_enabled: key=%s enabled=%s", key, enabled)
        with self._lock:
            self.for_device(key).slideshow_enabled = enabled
            self._save()

    def configure_slideshow(
        self,
        key: str,
        *,
        themes: list[str] | None = None,
        interval_s: float | None = None,
    ) -> None:
        """Set the slideshow theme list + interval atomically."""
        log.info("configure_slideshow: key=%s themes=%s interval_s=%s",
                 key,
                 None if themes is None else len(themes), interval_s)
        with self._lock:
            s = self.for_device(key)
            if themes is not None:
                s.slideshow_themes = list(themes)
            if interval_s is not None:
                s.slideshow_interval_s = max(1.0, float(interval_s))
            self._save()

    # ── Persistence ───────────────────────────────────────────────────

    def _config_path(self) -> Path:
        return self._paths.config_dir() / _CONFIG_FILE

    def _load(self) -> None:
        """Load config from disk.  Missing/corrupt → defaults, warn only.

        Falls back to the pre-cutover ``trcc-next.json`` filename so
        users who started on next/ before the rename keep their state;
        the next ``_save`` writes the new ``trcc.json`` automatically.
        """
        path = self._config_path()
        if not path.exists():
            old_path = self._paths.config_dir() / _PRE_CUTOVER_CONFIG_FILE
            if old_path.exists():
                log.info(
                    "Reading pre-cutover config %s; next save will write %s",
                    old_path, path,
                )
                path = old_path
            else:
                log.debug("No config file at %s, using defaults", path)
                return
        raw = load_json_or_default(path, None)
        if not isinstance(raw, dict):
            return

        app_data = raw.get("app", {})
        with self._lock:
            for field_name, value in app_data.items():
                if hasattr(self._app, field_name):
                    setattr(self._app, field_name, value)
            for key, data in raw.get("devices", {}).items():
                self._devices[key] = _device_settings_from_dict(data)
            for key, data in raw.get("led_devices", {}).items():
                self._led_devices[key] = _led_settings_from_dict(data)

    def _save(self) -> None:
        """Atomic write: tmp file → fsync → rename."""
        path = self._config_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "app": asdict(self._app),
            "devices": {k: asdict(v) for k, v in self._devices.items()},
            "led_devices": {k: asdict(v) for k, v in self._led_devices.items()},
        }
        tmp = path.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=_json_default)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)
        except OSError as e:
            raise ConfigError(f"Failed to persist config to {path}: {e}") from e


# =========================================================================
# JSON helpers (tuples ↔ lists, misc coercions)
# =========================================================================


def _json_default(obj: Any) -> Any:
    """Coerce tuples → lists (JSON has no tuple type)."""
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError(f"{type(obj).__name__} is not JSON-serialisable")


def _device_settings_from_dict(data: dict[str, Any]) -> DeviceSettings:
    """Build DeviceSettings from a parsed JSON dict, tolerant of extras."""
    kwargs: dict[str, Any] = {}
    valid_fields = {f for f in DeviceSettings.__dataclass_fields__}
    for field_name, value in data.items():
        if field_name in valid_fields:
            kwargs[field_name] = value
    # Mask position: JSON loads tuples as lists → restore tuple
    pos = kwargs.get("mask_position")
    if isinstance(pos, list) and len(pos) == 2:
        kwargs["mask_position"] = (pos[0], pos[1])
    # FitMode enum from its string value
    fm = kwargs.get("fit_mode")
    if isinstance(fm, str):
        try:
            kwargs["fit_mode"] = FitMode(fm)
        except ValueError:
            kwargs.pop("fit_mode")
    # overlay_background: list[3] → tuple[r,g,b]
    bg = kwargs.get("overlay_background")
    if isinstance(bg, list) and len(bg) == 3:
        kwargs["overlay_background"] = (bg[0], bg[1], bg[2])
    # user_overlay_elements: list[dict] → list[OverlayElement]
    raw_elements = kwargs.get("user_overlay_elements")
    if isinstance(raw_elements, list):
        kwargs["user_overlay_elements"] = [
            OverlayElement.from_dict(d) if isinstance(d, dict) else d
            for d in raw_elements
        ]
    # mask_overlay_elements: list[dict] | None → list[OverlayElement] | None
    raw_mask_elements = kwargs.get("mask_overlay_elements")
    if isinstance(raw_mask_elements, list):
        kwargs["mask_overlay_elements"] = [
            OverlayElement.from_dict(d) if isinstance(d, dict) else d
            for d in raw_mask_elements
        ]
    return DeviceSettings(**kwargs)


def _led_zone_from_dict(data: dict[str, Any]) -> LedZoneSettings:
    """Build one LedZoneSettings from a parsed JSON dict."""
    kwargs: dict[str, Any] = {}
    valid = set(LedZoneSettings.__dataclass_fields__)
    for k, v in data.items():
        if k in valid:
            kwargs[k] = v
    if "mode" in kwargs and isinstance(kwargs["mode"], int):
        try:
            kwargs["mode"] = LEDMode(kwargs["mode"])
        except ValueError:
            kwargs.pop("mode")
    if isinstance(kwargs.get("color"), list) and len(kwargs["color"]) == 3:
        kwargs["color"] = tuple(kwargs["color"])
    return LedZoneSettings(**kwargs)


def _led_settings_from_dict(data: dict[str, Any]) -> LedDeviceSettings:
    """Build LedDeviceSettings from a parsed JSON dict, tolerant of extras."""
    kwargs: dict[str, Any] = {}
    valid = set(LedDeviceSettings.__dataclass_fields__)
    for k, v in data.items():
        if k in valid:
            kwargs[k] = v
    # Mode enum from its int value
    if "mode" in kwargs and isinstance(kwargs["mode"], int):
        try:
            kwargs["mode"] = LEDMode(kwargs["mode"])
        except ValueError:
            kwargs.pop("mode")
    # Color tuple restoration
    if isinstance(kwargs.get("color"), list) and len(kwargs["color"]) == 3:
        kwargs["color"] = tuple(kwargs["color"])
    # Zones (each is its own dataclass)
    if isinstance(kwargs.get("zones"), list):
        kwargs["zones"] = [_led_zone_from_dict(z) for z in kwargs["zones"]
                           if isinstance(z, dict)]
    return LedDeviceSettings(**kwargs)


# Silence ruff "field imported but not used" when this module grows
_ = field

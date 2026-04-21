"""Settings — user preferences, persisted to config.json.

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
from typing import Any, Dict, Literal, Optional, Tuple

from ..core.errors import ConfigError
from ..core.models import DeviceSettings, FitMode, TempUnit
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
    active_device: Optional[str] = None
    autostart_configured: bool = False
    ui_theme: Literal["dark", "light", "system"] = "system"


# =========================================================================
# Settings — the service
# =========================================================================


_CONFIG_FILE = "config.json"


class Settings:
    """Per-app and per-device settings with JSON persistence.

    Thread-safe via RLock.  Atomic save (write to tmp, fsync, rename).
    Missing / corrupt config file falls back to defaults.
    """

    def __init__(self, paths: Paths) -> None:
        self._paths = paths
        self._lock = RLock()
        self._app = AppSettings()
        self._devices: Dict[str, DeviceSettings] = {}
        self._load()

    # ── AppSettings surface ───────────────────────────────────────────

    @property
    def app(self) -> AppSettings:
        return self._app

    def set_language(self, lang: str) -> None:
        with self._lock:
            self._app.language = lang
            self._save()

    def set_active_device(self, key: Optional[str]) -> None:
        with self._lock:
            self._app.active_device = key
            self._save()

    def set_refresh_interval(self, seconds: float) -> None:
        with self._lock:
            self._app.refresh_interval_s = max(0.1, seconds)
            self._save()

    # ── DeviceSettings surface ────────────────────────────────────────

    def for_device(self, key: str) -> DeviceSettings:
        """Return the DeviceSettings for *key*, creating defaults if absent."""
        with self._lock:
            if key not in self._devices:
                self._devices[key] = DeviceSettings()
            return self._devices[key]

    def set_orientation(self, key: str, degrees: int) -> None:
        with self._lock:
            self.for_device(key).orientation = degrees
            self._save()

    def set_brightness(self, key: str, percent: int) -> None:
        with self._lock:
            self.for_device(key).brightness = max(0, min(100, percent))
            self._save()

    def set_current_theme(self, key: str, theme_name: Optional[str]) -> None:
        with self._lock:
            self.for_device(key).current_theme = theme_name
            self._save()

    def set_temp_unit(self, key: str, unit: TempUnit) -> None:
        with self._lock:
            self.for_device(key).temp_unit = unit
            self._save()

    def set_time_format(self, key: str, fmt: Literal["12h", "24h"]) -> None:
        with self._lock:
            self.for_device(key).time_format = fmt
            self._save()

    def set_date_format(self, key: str, fmt: str) -> None:
        with self._lock:
            self.for_device(key).date_format = fmt
            self._save()

    def set_overlay_enabled(self, key: str, enabled: bool) -> None:
        with self._lock:
            self.for_device(key).overlay_enabled = enabled
            self._save()

    def set_mask_position(self, key: str,
                          position: Optional[Tuple[int, int]]) -> None:
        with self._lock:
            self.for_device(key).mask_position = position
            self._save()

    def set_fit_mode(self, key: str, mode: FitMode) -> None:
        with self._lock:
            self.for_device(key).fit_mode = mode
            self._save()

    # ── Persistence ───────────────────────────────────────────────────

    def _config_path(self) -> Path:
        return self._paths.config_dir() / _CONFIG_FILE

    def _load(self) -> None:
        """Load config from disk.  Missing/corrupt → defaults, warn only."""
        path = self._config_path()
        if not path.exists():
            log.debug("No config file at %s, using defaults", path)
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to read %s: %s — using defaults", path, e)
            return

        app_data = raw.get("app", {})
        with self._lock:
            for field_name, value in app_data.items():
                if hasattr(self._app, field_name):
                    setattr(self._app, field_name, value)
            for key, data in raw.get("devices", {}).items():
                self._devices[key] = _device_settings_from_dict(data)

    def _save(self) -> None:
        """Atomic write: tmp file → fsync → rename."""
        path = self._config_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "app": asdict(self._app),
            "devices": {k: asdict(v) for k, v in self._devices.items()},
        }
        tmp = path.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=_json_default)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
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


def _device_settings_from_dict(data: Dict[str, Any]) -> DeviceSettings:
    """Build DeviceSettings from a parsed JSON dict, tolerant of extras."""
    kwargs: Dict[str, Any] = {}
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
    return DeviceSettings(**kwargs)


# Silence ruff "field imported but not used" when this module grows
_ = field

"""UiState — GUI-only preferences that don't belong in ``app.settings``.

Hexagonal note: ``app.settings`` is a domain-level concept (per-device
mutable state observed across CLI / API / GUI / daemon).  Things like
"what was the last device the user clicked in the sidebar?" or "should
the 4-sensor info module be visible above the preview?" are pure UI
state — only the GUI knows or cares about them.

Putting them in the GUI layer keeps the domain model clean and means
the daemon never has to deserialize widget bookkeeping it can't act on.

Persistence shape — JSON at ``paths.config_dir() / "ui_state.json"``.
Missing file = first run = defaults.  Schema-versioned via the
``_schema`` key so older configs upgrade gracefully.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from typing import TYPE_CHECKING

from ...core._safe import load_json_or_default

if TYPE_CHECKING:
    from ...core.ports import Paths

log = logging.getLogger(__name__)


_SCHEMA = 1


@dataclass
class UiState:
    """Pure-data GUI state.  Equality + replace + asdict all free."""

    # Sidebar / device selection
    last_device_key: str = ""              # vid:pid of last-active device

    # Info module above preview (4 mini sensor readouts)
    show_info_module: bool = False

    # Global format defaults for overlay elements
    time_format: int = 0                   # 0 = 24h, 1 = 12h
    date_format: int = 1                   # 1 = Y/M/D, 2 = D/M/Y, 3 = M/D, 4 = D/M
    temp_unit: int = 0                     # 0 = Celsius, 1 = Fahrenheit

    # Recorded once on first run for upgrade hints
    install_method: str = ""               # pip / pipx / rpm / deb / pacman / pyinstaller
    install_distro: str = ""               # fedora / arch / ubuntu / debian / …


class UiStateStore:
    """File-backed UiState loader/saver.

    Created with a :class:`Paths` port so the GUI doesn't need to know
    where the config dir lives on the current OS.  Read once at GUI
    boot via :meth:`load`; written immediately on each mutator call so
    crashes never lose preferences.
    """

    _FILE_NAME = "ui_state.json"

    def __init__(self, paths: Paths) -> None:
        self._path = paths.config_dir() / self._FILE_NAME
        self._state = UiState()

    # ── Lifecycle ─────────────────────────────────────────────────────

    def load(self) -> UiState:
        """Read JSON from disk; first-run returns defaults."""
        raw = load_json_or_default(self._path, None)
        if not isinstance(raw, dict):
            return self._state
        # Drop unknown keys + the schema marker (forward-compat upgrades).
        known = {f.name for f in fields(UiState)}
        clean = {k: v for k, v in raw.items() if k in known}
        self._state = UiState(**clean)
        return self._state

    def save(self) -> None:
        """Atomically write the current state to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"_schema": _SCHEMA, **asdict(self._state)}
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as e:
            log.warning("UiStateStore.save: %s write failed: %s",
                        self._path, e)

    # ── Mutators ──────────────────────────────────────────────────────

    def set_last_device_key(self, key: str) -> None:
        if self._state.last_device_key == key:
            return
        self._state.last_device_key = key
        self.save()

    def set_show_info_module(self, on: bool) -> None:
        if self._state.show_info_module == on:
            return
        self._state.show_info_module = on
        self.save()

    def set_format_pref(self, name: str, value: int) -> None:
        """Set one of ``time_format`` / ``date_format`` / ``temp_unit``."""
        if name not in {"time_format", "date_format", "temp_unit"}:
            log.debug("set_format_pref: ignoring unknown pref %r", name)
            return
        current = getattr(self._state, name)
        if current == value:
            return
        setattr(self._state, name, value)
        self.save()

    def set_install_info(self, method: str, distro: str) -> None:
        if (
            self._state.install_method == method
            and self._state.install_distro == distro
        ):
            return
        self._state.install_method = method
        self._state.install_distro = distro
        self.save()

    # ── Convenience readers ───────────────────────────────────────────

    def get_install_info(self) -> dict[str, str] | None:
        if not self._state.install_method:
            return None
        return {
            "method": self._state.install_method,
            "distro": self._state.install_distro,
        }

    def apply_format_prefs(self, overlay_config: dict) -> None:
        """Override per-element format with the saved global default.

        Legacy ``Settings.apply_format_prefs`` behaviour: when a theme
        loads, each TIME / DATE / temp-unit element gets its ``mode_sub``
        overwritten with the user's saved global default.
        """
        for cfg in overlay_config.values():
            if not isinstance(cfg, dict):
                continue
            metric = cfg.get("metric", "")
            if metric == "time":
                cfg["time_format"] = self._state.time_format
            elif metric == "date":
                cfg["date_format"] = self._state.date_format
            elif metric and metric not in {"weekday"}:
                # Hardware metric — temp unit goes in mode_sub via the
                # overlay grid's own mapping; only override if a temp
                # field is already present.
                if "temp_unit" in cfg:
                    cfg["temp_unit"] = self._state.temp_unit

    @property
    def state(self) -> UiState:
        """Read-only handle (callers go through mutators to write)."""
        return self._state

"""LED config persistence — save/load LEDState to per-device config.

Extracted from LEDService (SRP). Memento pattern — _PERSIST_FIELDS and
_ALIASES define the serialization schema.

LEDConfigService: concrete DeviceConfigService for LED.
save_led_config / load_led_config: bulk serialization functions.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..core.models import LEDMode, LEDState
from ..core.ports import DeviceConfigService

log = logging.getLogger(__name__)

# Config persistence field map: config_key → LEDState attribute.
# One map drives both save and load — add a field here once.
_PERSIST_FIELDS: dict[str, str] = {
    'mode': 'mode',
    'color': 'color',
    'brightness': 'brightness',
    'global_on': 'global_on',
    'segments_on': 'segment_on',
    'temp_source': 'temp_source',
    'load_source': 'load_source',
    'is_timer_24h': 'is_timer_24h',
    'is_week_sunday': 'is_week_sunday',
    'disk_index': 'disk_index',
    'memory_ratio': 'memory_ratio',
    'zone_sync': 'zone_sync',
    'zone_sync_interval': 'zone_sync_interval',
}

# Backward-compat aliases (v5.0.x config keys → current keys)
_ALIASES: dict[str, str] = {
    'zone_carousel': 'zone_sync',
    'zone_carousel_zones': 'zone_sync_zones',
    'zone_carousel_interval': 'zone_sync_interval',
}


def _serialize(val: Any) -> Any:
    """Convert a state value for JSON-safe config storage."""
    if isinstance(val, LEDMode):
        return val.value
    if isinstance(val, tuple):
        return list(val)
    return val


def save_led_config(
    state: LEDState,
    device_key: str,
    save_setting_fn: Callable[..., None],
) -> None:
    """Serialize LEDState to config file."""
    try:
        config: dict[str, Any] = {
            ck: _serialize(getattr(state, sa))
            for ck, sa in _PERSIST_FIELDS.items()
        }
        config['zone_sync_zones'] = state.zone_sync_zones
        config['zones'] = [
            {'mode': z.mode.value, 'color': list(z.color),
             'brightness': z.brightness, 'on': z.on}
            for z in state.zones
        ]
        save_setting_fn(device_key, 'led_config', config)
    except Exception as e:
        log.error("Failed to save LED config: %s", e)


def load_led_config(
    state: LEDState,
    device_key: str,
    get_config_fn: Callable[..., dict],
) -> None:
    """Deserialize LEDState from config file."""
    try:
        dev_config = get_config_fn(device_key)
        if not (led_config := dev_config.get('led_config', {})):
            log.debug("load_led_config: no led_config in device %s — using defaults", device_key)
            return

        # Backward-compat aliases (v5.0.x: zone_carousel → zone_sync)
        for old, new in _ALIASES.items():
            if old in led_config and new not in led_config:
                led_config[new] = led_config[old]

        # Scalar and simple-list fields
        for ck, sa in _PERSIST_FIELDS.items():
            if ck in led_config:
                val = led_config[ck]
                cur = getattr(state, sa)
                if isinstance(cur, LEDMode):
                    val = LEDMode(val)
                elif isinstance(cur, tuple):
                    val = tuple(val)
                setattr(state, sa, val)

        # Zone sync zones: partial update (saved length may differ from current)
        if 'zone_sync_zones' in led_config:
            saved = led_config['zone_sync_zones']
            for i in range(min(len(saved), len(state.zone_sync_zones))):
                state.zone_sync_zones[i] = saved[i]

        # Per-zone states
        if 'zones' in led_config and state.zones:
            for i, zc in enumerate(led_config['zones']):
                if i < len(state.zones):
                    z = state.zones[i]
                    z.mode = LEDMode(zc.get('mode', 0))
                    z.color = tuple(zc.get('color', (255, 0, 0)))
                    z.brightness = zc.get('brightness', 100)
                    z.on = zc.get('on', True)
    except Exception as e:
        log.error("Failed to load LED config: %s", e)


class LEDConfigService(DeviceConfigService):
    """LED per-device config persistence — injected into LEDDevice/LEDService.

    Inherits device_key, persist, get_config from DeviceConfigService.
    Adds save_state/load_state for bulk LEDState serialization (LED-specific).
    """

    def save_state(self, dev: Any, state: LEDState) -> None:
        """Bulk serialize LEDState. LED-specific."""
        if dev:
            save_led_config(state, self.device_key(dev), self._save_fn)

    def load_state(self, dev: Any, state: LEDState) -> None:
        """Bulk deserialize LEDState. LED-specific."""
        if dev:
            load_led_config(state, self.device_key(dev), self._get_fn)

"""Tests for services/led_config.py — LED state persistence (Memento pattern).

Covers:
- _serialize() — LEDMode enum, tuple, passthrough
- save_led_config() — serializes LEDState to config dict
- load_led_config() — deserializes config dict to LEDState
- Backward-compat aliases (zone_carousel → zone_sync)
- Partial zone restore (saved length ≠ current length)
- Per-zone state restore
- Missing/empty config graceful fallback
"""
from __future__ import annotations

from unittest.mock import MagicMock

from trcc.legacy.core.models import LEDMode, LEDState, LEDZoneState
from trcc.legacy.services.led_config import (
    _ALIASES,
    _PERSIST_FIELDS,
    _serialize,
    load_led_config,
    save_led_config,
)

# =========================================================================
# _serialize()
# =========================================================================


class TestSerialize:
    """_serialize — value conversion for config storage."""

    def test_enum_to_value(self):
        assert _serialize(LEDMode.BREATHING) == LEDMode.BREATHING.value

    def test_tuple_to_list(self):
        assert _serialize((255, 0, 0)) == [255, 0, 0]

    def test_int_passthrough(self):
        assert _serialize(42) == 42

    def test_bool_passthrough(self):
        assert _serialize(True) is True

    def test_string_passthrough(self):
        assert _serialize('cpu') == 'cpu'

    def test_list_passthrough(self):
        assert _serialize([1, 2, 3]) == [1, 2, 3]


# =========================================================================
# save_led_config()
# =========================================================================


class TestSaveLedConfig:
    """save_led_config — serializes LEDState to config file."""

    def test_saves_all_persist_fields(self):
        state = LEDState(
            mode=LEDMode.BREATHING,
            color=(0, 255, 0),
            brightness=80,
            global_on=True,
            segment_on=[True, False, True],
            temp_source='gpu',
            load_source='cpu',
            is_timer_24h=False,
            is_week_sunday=True,
            disk_index=2,
            memory_ratio=4,
            zone_sync=True,
            zone_sync_interval=20,
        )
        mock_save = MagicMock()
        save_led_config(state, 'dev_key', mock_save)

        mock_save.assert_called_once()
        args = mock_save.call_args[0]
        assert args[0] == 'dev_key'
        assert args[1] == 'led_config'
        config = args[2]

        assert config['mode'] == LEDMode.BREATHING.value
        assert config['color'] == [0, 255, 0]
        assert config['brightness'] == 80
        assert config['global_on'] is True
        assert config['temp_source'] == 'gpu'
        assert config['load_source'] == 'cpu'
        assert config['zone_sync'] is True
        assert config['zone_sync_interval'] == 20

    def test_saves_zone_states(self):
        state = LEDState()
        state.zones = [
            LEDZoneState(mode=LEDMode.STATIC, color=(255, 0, 0), brightness=100, on=True),
            LEDZoneState(mode=LEDMode.BREATHING, color=(0, 255, 0), brightness=50, on=False),
        ]
        mock_save = MagicMock()
        save_led_config(state, 'k', mock_save)

        config = mock_save.call_args[0][2]
        assert len(config['zones']) == 2
        assert config['zones'][0]['mode'] == LEDMode.STATIC.value
        assert config['zones'][1]['on'] is False

    def test_saves_zone_sync_zones(self):
        state = LEDState()
        state.zone_sync_zones = [True, False, True]
        mock_save = MagicMock()
        save_led_config(state, 'k', mock_save)

        config = mock_save.call_args[0][2]
        assert config['zone_sync_zones'] == [True, False, True]

    def test_exception_logged_not_raised(self):
        """Errors are caught and logged, not propagated."""
        mock_save = MagicMock(side_effect=RuntimeError("disk full"))
        save_led_config(LEDState(), 'k', mock_save)
        # No exception raised — error is logged


# =========================================================================
# load_led_config()
# =========================================================================


class TestLoadLedConfig:
    """load_led_config — deserializes config dict to LEDState."""

    def _mock_get(self, led_config: dict):
        return MagicMock(return_value={'led_config': led_config})

    def test_restores_scalar_fields(self):
        state = LEDState()
        mock_get = self._mock_get({
            'mode': LEDMode.BREATHING.value,
            'color': [0, 255, 0],
            'brightness': 80,
        })
        load_led_config(state, 'k', mock_get)

        assert state.mode == LEDMode.BREATHING
        assert state.color == (0, 255, 0)
        assert state.brightness == 80

    def test_restores_bool_fields(self):
        state = LEDState()
        mock_get = self._mock_get({
            'global_on': False,
            'is_timer_24h': True,
            'is_week_sunday': False,
        })
        load_led_config(state, 'k', mock_get)

        assert state.global_on is False
        assert state.is_timer_24h is True
        assert state.is_week_sunday is False

    def test_missing_fields_not_overwritten(self):
        state = LEDState(brightness=42)
        mock_get = self._mock_get({'mode': LEDMode.STATIC.value})
        load_led_config(state, 'k', mock_get)

        assert state.brightness == 42  # Not in config → not touched

    def test_empty_led_config_is_noop(self):
        state = LEDState(brightness=42)
        mock_get = self._mock_get({})
        load_led_config(state, 'k', mock_get)

        assert state.brightness == 42  # Unchanged

    def test_backward_compat_zone_carousel_alias(self):
        state = LEDState()
        mock_get = self._mock_get({
            'zone_carousel': True,
            'zone_carousel_interval': 15,
        })
        load_led_config(state, 'k', mock_get)

        assert state.zone_sync is True
        assert state.zone_sync_interval == 15

    def test_partial_zone_sync_zones(self):
        state = LEDState()
        state.zone_sync_zones = [False, False, False, False]
        mock_get = self._mock_get({
            'zone_sync_zones': [True, True],
        })
        load_led_config(state, 'k', mock_get)

        assert state.zone_sync_zones[:2] == [True, True]
        assert state.zone_sync_zones[2:] == [False, False]

    def test_restores_per_zone_states(self):
        state = LEDState()
        state.zones = [LEDZoneState(), LEDZoneState()]
        mock_get = self._mock_get({
            'zones': [
                {'mode': LEDMode.BREATHING.value, 'color': [0, 0, 255],
                 'brightness': 50, 'on': False},
            ],
        })
        load_led_config(state, 'k', mock_get)

        assert state.zones[0].mode == LEDMode.BREATHING
        assert state.zones[0].color == (0, 0, 255)
        assert state.zones[0].on is False

    def test_extra_saved_zones_ignored(self):
        state = LEDState()
        state.zones = [LEDZoneState()]
        mock_get = self._mock_get({
            'zones': [
                {'mode': 0, 'color': [255, 0, 0], 'brightness': 100, 'on': True},
                {'mode': 1, 'color': [0, 255, 0], 'brightness': 50, 'on': False},
            ],
        })
        load_led_config(state, 'k', mock_get)

        assert len(state.zones) == 1  # Only one zone exists

    def test_no_device_config_is_noop(self):
        state = LEDState(brightness=42)
        mock_get = MagicMock(return_value={})
        load_led_config(state, 'k', mock_get)

        assert state.brightness == 42

    def test_exception_logged_not_raised(self):
        """Errors are caught and logged, not propagated."""
        mock_get = MagicMock(side_effect=RuntimeError("corrupt config"))
        load_led_config(LEDState(), 'k', mock_get)
        # No exception raised


# =========================================================================
# Constants sanity checks
# =========================================================================


class TestConstants:
    """Ensure persistence schema covers expected fields."""

    def test_persist_fields_non_empty(self):
        assert len(_PERSIST_FIELDS) >= 10

    def test_aliases_map_to_known_keys(self):
        for old, new in _ALIASES.items():
            # New key should either be in _PERSIST_FIELDS or a known list key
            assert new in _PERSIST_FIELDS or new == 'zone_sync_zones', \
                f"Alias target '{new}' not in _PERSIST_FIELDS"

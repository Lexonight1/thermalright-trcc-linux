"""ControlCenterCommands — app-level settings and updates.

Covers the GUI's About/Control Center surface: autostart, temperature unit,
language, HDD toggle, metrics refresh, GPU selection, update check/install.
Plus listing of GPUs, fonts, and sensors used to populate Control Center
and overlay-element pickers.

Phase 3 delegates to `Settings` + `Platform`; the heavy lifting (subprocess
upgrade, GitHub release check) already lives in the GUI `uc_about.py` and
moves here in Phase 5.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.error import URLError
from urllib.request import Request, urlopen, urlretrieve

from ..conf import Settings
from .models.sensor import SensorInfo
from .results import AppSnapshot, OpResult, UpdateResult

if TYPE_CHECKING:
    from .events import EventBus
    from .ports import Platform

log = logging.getLogger(__name__)

_GITHUB_LATEST = (
    'https://api.github.com/repos/Lexonight1/thermalright-trcc-linux'
    '/releases/latest'
)

_PKG_INSTALL: dict[str, list[str]] = {
    'pacman': ['pkexec', 'pacman', '-U', '--noconfirm'],
    'dnf':    ['pkexec', 'dnf', 'install', '-y'],
    'apt':    ['pkexec', 'apt', 'install', '-y'],
}


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse '3.0.9' into (3, 0, 9) for comparison."""
    return tuple(int(x) for x in v.split('.') if x.isdigit())


def _delegate(callback: Any, arg: Any, *, fallback_msg: str) -> OpResult:
    """Call a ``Trcc.set_*`` callback and adapt its dict-shaped return to OpResult.

    Used by every ``ControlCenterCommands`` setter that delegates the
    cross-cutting propagation work back to ``Trcc``. Keeps the four
    delegating bodies (temp_unit / language / metrics_refresh /
    gpu_device) identical — one source of truth for the
    dict-result → OpResult lift.
    """
    result = callback(arg)
    if result.get('success'):
        return OpResult(success=True,
                        message=result.get('message', fallback_msg))
    return OpResult(success=False, error=result.get('error', ''))


class ControlCenterCommands:
    """Command surface for app-level settings and updates."""

    def __init__(self, platform: Platform, events: EventBus,
                 settings: Any,
                 *,
                 set_temp_unit: Any = None,
                 set_language: Any = None,
                 set_metrics_refresh: Any = None,
                 set_gpu_device: Any = None) -> None:
        self._platform = platform
        self._events = events
        self._settings = settings
        # Cross-cutting handlers — ``Trcc`` owns the device list + the
        # metrics loop + the sensor enumerator, so it's the only place
        # that can propagate setting changes to every consumer.
        # ``ControlCenterCommands`` lives on the UI surface and just
        # delegates back; the callbacks below let the GUI / CLI / API
        # all hit the canonical propagation paths through one method
        # name each (``trcc.set_*``).
        self._trcc_set_temp_unit = set_temp_unit
        self._trcc_set_language = set_language
        self._trcc_set_metrics_refresh = set_metrics_refresh
        self._trcc_set_gpu_device = set_gpu_device
        self._sensor_enum = None   # lazy — discover on first GPU/sensor query

    # ── Settings ─────────────────────────────────────────────────────

    def set_autostart(self, enabled: bool) -> OpResult:
        try:
            if enabled:
                self._platform.autostart_enable()
            else:
                self._platform.autostart_disable()
            self._events.publish('control_center.autostart', enabled)
            return OpResult(
                success=True,
                message=f'Autostart {"enabled" if enabled else "disabled"}',
            )
        except Exception as e:
            log.exception('set_autostart failed')
            return OpResult(success=False, error=str(e))

    def set_temp_unit(self, unit: str) -> OpResult:
        match unit.upper():
            case 'C':
                unit_int = 0
            case 'F':
                unit_int = 1
            case _:
                return OpResult(
                    success=False,
                    error=f"Invalid temp unit '{unit}' — must be 'C' or 'F'",
                )
        if self._trcc_set_temp_unit is not None:
            # Delegate to Trcc.set_temp_unit — persists the setting,
            # refreshes the metrics record in the new unit, propagates
            # ``OverlayService.set_temp_unit`` + ``LEDDevice.set_temp_unit``
            # to every connected device, wakes the metrics loop, AND
            # publishes ``Topic.CONTROL_CENTER_TEMP_UNIT``.  Skipping
            # the delegate was the root cause of "GUI toggles unit but
            # LCD still labels °C while reading °F" — the metrics
            # value flipped via the polling loop's
            # ``HardwareMetrics.with_temp_unit`` call, but the
            # overlay's ``temp_unit`` (the suffix selector) didn't.
            return _delegate(self._trcc_set_temp_unit, unit_int,
                              fallback_msg=f'Temperature unit: °{unit.upper()}')
        # Test / legacy path — no Trcc back-ref injected.  Updates
        # the persisted setting only; not used in production wiring.
        self._settings.set_temp_unit(unit_int)
        self._events.publish('control_center.temp_unit', unit.upper())
        return OpResult(success=True, message=f'Temperature unit: °{unit.upper()}')

    def set_language(self, lang: str) -> OpResult:
        if self._trcc_set_language is not None:
            # Delegate — propagates to every LCD overlay's ``set_lang``
            # so weekday abbreviations update immediately for all UIs.
            return _delegate(self._trcc_set_language, lang,
                              fallback_msg=f'Language: {lang}')
        # Test / legacy path — persists only.
        self._settings.lang = lang
        self._events.publish('control_center.language', lang)
        return OpResult(success=True, message=f'Language: {lang}')

    def set_hdd_enabled(self, enabled: bool) -> OpResult:
        self._settings.set_hdd_enabled(enabled)
        self._events.publish('control_center.hdd', enabled)
        return OpResult(
            success=True,
            message=f'HDD metrics {"enabled" if enabled else "disabled"}',
        )

    def set_metrics_refresh(self, seconds: int) -> OpResult:
        if not 1 <= seconds <= 100:
            return OpResult(
                success=False,
                error=f'Refresh must be 1-100 seconds, got {seconds}',
            )
        if self._trcc_set_metrics_refresh is not None:
            # Delegate — also calls ``wake_metrics_loop()`` so the new
            # interval takes effect on the next tick instead of waiting
            # up to the OLD interval.
            return _delegate(self._trcc_set_metrics_refresh, seconds,
                              fallback_msg=f'Refresh interval: {seconds}s')
        # Test / legacy path — persists only.
        self._settings.set_refresh_interval(seconds)
        self._events.publish('control_center.refresh', seconds)
        return OpResult(success=True, message=f'Refresh interval: {seconds}s')

    def set_gpu_device(self, gpu_key: str) -> OpResult:
        if self._trcc_set_gpu_device is not None:
            # Delegate — propagates ``enumerator.set_preferred_gpu(key)``
            # so subsequent metrics polls read from the selected card.
            # Previously every UI (GUI / CLI / API) had to remember to
            # make that second call.
            return _delegate(self._trcc_set_gpu_device, gpu_key,
                              fallback_msg=f'GPU: {gpu_key}')
        # Test / legacy path — persists only.
        self._settings.set_gpu_device(gpu_key)
        self._events.publish('control_center.gpu', gpu_key)
        return OpResult(success=True, message=f'GPU: {gpu_key}')

    # ── Updates ──────────────────────────────────────────────────────

    def check_for_update(self) -> UpdateResult:
        """Query GitHub for the latest release. Compares against current version."""
        from ...__version__ import __version__
        try:
            req = Request(_GITHUB_LATEST,
                          headers={'Accept': 'application/vnd.github+json'})
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
        except (URLError, OSError, TimeoutError, ValueError) as e:
            # ValueError covers JSONDecodeError; URLError covers HTTPError; OSError covers socket errors.
            log.warning('check_for_update: %s', e)
            return UpdateResult(
                success=False,
                error=str(e),
                current_version=__version__,
            )

        tag = data.get('tag_name', '')
        latest = tag.lstrip('v') if tag else ''
        if not latest:
            return UpdateResult(
                success=False, error='No tag_name in release',
                current_version=__version__,
            )

        assets: dict[str, str] = {}
        for asset in data.get('assets', []):
            name = asset.get('name', '')
            url = asset.get('browser_download_url', '')
            if name.endswith('.pkg.tar.zst'):
                assets['pacman'] = url
            elif name.endswith('.rpm'):
                assets['dnf'] = url
            elif name.endswith('.deb'):
                assets['apt'] = url

        has_update = _parse_version(latest) > _parse_version(__version__)
        msg = (f'Update available: {__version__} → {latest}'
               if has_update else f'Up to date ({__version__})')
        log.info('check_for_update: %s', msg)
        return UpdateResult(
            success=True,
            message=msg,
            current_version=__version__,
            latest_version=latest,
            update_available=has_update,
            assets=assets,
        )

    def run_upgrade(self) -> OpResult:
        """Upgrade via detected install method. Returns when the subprocess finishes.

        pipx/pip run in-process; pacman/dnf/apt download then pkexec install.
        """
        info = Settings.get_install_info() or {}
        method = info.get('method', 'pip')
        check = self.check_for_update()
        if not check.update_available or not check.latest_version:
            return OpResult(
                success=False,
                message=check.message or 'Already up to date',
                error=check.error,
            )

        if method == 'pipx':
            cmd = ['pipx', 'upgrade', 'trcc-linux']
        elif method == 'pip':
            cmd = [sys.executable, '-m', 'pip', 'install',
                   '--upgrade', 'trcc-linux']
        elif method in _PKG_INSTALL:
            url = check.assets.get(method)
            if not url:
                return OpResult(
                    success=False,
                    error=f'No {method} package in release assets',
                )
            # Sanitize filename — strip paths and reject traversal.
            filename = Path(url.rsplit('/', 1)[-1]).name
            if not filename or '..' in filename:
                return OpResult(success=False, error=f'Unsafe filename: {url}')
            pkg_path = Path(tempfile.mkdtemp(prefix='trcc_pkg_')) / filename
            try:
                log.info('Downloading %s', url)
                urlretrieve(url, pkg_path)
            except (URLError, OSError, TimeoutError) as e:
                log.warning('run_upgrade download failed: %s', e)
                return OpResult(
                    success=False, error=f'Download failed: {e}',
                )
            cmd = [*_PKG_INSTALL[method], str(pkg_path)]
        else:
            return OpResult(success=False, error=f'Unknown install method: {method}')

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return OpResult(
                success=True,
                message=f'Upgraded to {check.latest_version} — restart to apply',
            )
        except subprocess.CalledProcessError as e:
            return OpResult(
                success=False,
                error=f'Upgrade failed: {e.stderr.strip() if e.stderr else e}',
            )

    # ── Listing ──────────────────────────────────────────────────────

    def list_gpus(self) -> list[tuple[str, str]]:
        try:
            if self._sensor_enum is None:
                self._sensor_enum = self._platform.sensors
            return list(self._sensor_enum.get_gpu_list())
        except Exception as e:
            log.warning('list_gpus: %s', e)
            return []

    def list_fonts(self) -> list[str]:
        # Phase 5: delegate to renderer's font enumeration.
        log.debug('list_fonts (phase-3 stub)')
        return []

    def list_sensors(self) -> list[SensorInfo]:
        try:
            if self._sensor_enum is None:
                self._sensor_enum = self._platform.sensors
            return list(self._sensor_enum.get_sensors())
        except Exception as e:
            log.warning('list_sensors: %s', e)
            return []

    # ── Metrics snapshot ─────────────────────────────────────────────

    def metrics(self) -> dict:
        # Phase 5: delegate to SystemService metrics snapshot.
        return {}

    # ── Snapshot ─────────────────────────────────────────────────────

    def snapshot(self) -> AppSnapshot:
        from ...__version__ import __version__
        s = self._settings
        try:
            autostart = self._platform.autostart_enabled()
        except Exception as e:
            # Per-OS impls vary widely (registry / desktop file / launchctl); default safe + log.
            log.debug('autostart_enabled probe failed: %s', e)
            autostart = False
        install_info = Settings.get_install_info() or {}
        return AppSnapshot(
            version=__version__,
            autostart=autostart,
            temp_unit='F' if s.temp_unit else 'C',
            language=s.lang,
            hdd_enabled=s.hdd_enabled,
            refresh_interval=s.refresh_interval,
            gpu_device=s.gpu_device or None,
            gpu_list=self.list_gpus(),
            install_method=install_info.get('method', 'pip'),
            distro=install_info.get('distro', 'unknown'),
        )

"""Tests for device isolation.

Two test surfaces:
1. 15 resolutions — dir isolation (each resolution has correct asset dirs)
2. 36 handshakes — PM+SUB→FBL→resolution chain + button image identity

Dev data covers 6 of 15 resolutions. Resolutions without dev data are
marked xfail (data not yet downloaded — not a code bug).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trcc.legacy.core.models import (
    _PM_SUB_TO_FBL,
    _PM_TO_FBL_OVERRIDES,
    _VARIANT_REGISTRY,
    FBL_PROFILES,
    fbl_to_resolution,
    get_button_image,
    pm_to_fbl,
)
from trcc.legacy.core.paths import has_themes

DEV_DIR = Path(__file__).resolve().parents[2] / 'dev'
DEV_DATA = DEV_DIR / '.trcc' / 'data'
DEV_DEVICES = DEV_DIR / 'devices.json'

# ── Build all 36 handshake combos ───────────────────────────────────────

_ALL_HANDSHAKES: list[tuple[int, int, int, int, int]] = []  # (pm, sub, fbl, w, h)

for _pm in sorted(_PM_TO_FBL_OVERRIDES):
    _fbl = pm_to_fbl(_pm, 0)
    _w, _h = fbl_to_resolution(_fbl, _pm)
    _ALL_HANDSHAKES.append((_pm, 0, _fbl, _w, _h))

for (_pm, _sub), _fbl in sorted(_PM_SUB_TO_FBL.items()):
    _w, _h = fbl_to_resolution(_fbl, _pm)
    _ALL_HANDSHAKES.append((_pm, _sub, _fbl, _w, _h))

_covered_pms = set(pm for pm, _, _, _, _ in _ALL_HANDSHAKES)
for _fbl, _p in sorted(FBL_PROFILES.items()):
    if _fbl not in _covered_pms:
        _ALL_HANDSHAKES.append((_fbl, 0, _fbl, _p.resolution[0], _p.resolution[1]))

# ── 15 unique resolutions ───────────────────────────────────────────────

_seen_res: set[tuple[int, int]] = set()
_UNIQUE_RESOLUTIONS: list[tuple[int, int]] = []
for _, _, _, _w, _h in _ALL_HANDSHAKES:
    if (_w, _h) not in _seen_res:
        _seen_res.add((_w, _h))
        _UNIQUE_RESOLUTIONS.append((_w, _h))

# Resolutions with dev data downloaded — detected from filesystem so xfail
# tracks reality, not a hard-coded list (Phase 10F: dev data slimmed down)
_HAS_DEV_DATA: set[tuple[int, int]] = set()
if DEV_DATA.exists():
    for entry in DEV_DATA.iterdir():
        if entry.is_dir() and entry.name.startswith('theme'):
            digits = entry.name[len('theme'):]
            if digits.isdigit():
                # Heuristic: try splitting at common width prefixes
                for split in (3, 4):
                    if len(digits) > split:
                        try:
                            _w = int(digits[:split])
                            _h = int(digits[split:])
                            if _w > 0 and _h > 0:
                                _HAS_DEV_DATA.add((_w, _h))
                                break
                        except ValueError:
                            pass

# ── Button combos from the VID/PID-scoped variant registry ─────────────

_BUTTON_COMBOS: list[tuple[int, int, int, int, str]] = []  # (vid, pid, pm, sub, button)

for (vid, pid), pm_map in sorted(_VARIANT_REGISTRY.items()):
    for pm_key, sub_map in sorted(pm_map.items()):
        for sub_key, override in sorted(sub_map.items(), key=lambda x: (x[0] is None, x[0])):
            if sub_key is None:
                continue
            _BUTTON_COMBOS.append((vid, pid, pm_key, sub_key, override.button_image))
        if list(sub_map.keys()) == [None]:
            _BUTTON_COMBOS.append((vid, pid, pm_key, 0, sub_map[None].button_image))


# ── Dir helper ──────────────────────────────────────────────────────────

def _dirs_for_resolution(w: int, h: int) -> dict:
    sw, sh = h, w
    theme = DEV_DATA / f'theme{w}{h}'
    web = DEV_DATA / 'web' / f'{w}{h}'
    masks = DEV_DATA / 'web' / f'zt{w}{h}'

    d = {
        'w': w, 'h': h,
        'theme_dir': str(theme) if theme.exists() else None,
        'web_dir': str(web) if web.exists() else None,
        'masks_dir': str(masks) if masks.exists() else None,
        'theme_dir_portrait': None,
        'web_dir_portrait': None,
        'masks_dir_portrait': None,
    }

    if w != h:
        pt = DEV_DATA / f'theme{sw}{sh}'
        pw = DEV_DATA / 'web' / f'{sw}{sh}'
        pm = DEV_DATA / 'web' / f'zt{sw}{sh}'
        d['theme_dir_portrait'] = str(pt) if has_themes(str(pt)) else None
        d['web_dir_portrait'] = str(pw) if pw.exists() else None
        d['masks_dir_portrait'] = str(pm) if pm.exists() else None

    return d


def _maybe_xfail(w, h):
    """Mark test xfail if dev data not downloaded for this resolution."""
    if (w, h) not in _HAS_DEV_DATA:
        pytest.xfail(f'{w}x{h} dev data not yet downloaded')


# =========================================================================
# 1. DIR ISOLATION — 15 resolutions
# =========================================================================

@pytest.mark.skipif(not DEV_DATA.exists(), reason='dev data not available')
class TestDirIsolation:
    """15 unique resolutions — each has landscape dirs, non-square has portrait."""

    @pytest.mark.parametrize('w,h', _UNIQUE_RESOLUTIONS,
                             ids=[f'{w}x{h}' for w, h in _UNIQUE_RESOLUTIONS])
    def test_has_web_dir(self, w, h):
        _maybe_xfail(w, h)
        dirs = _dirs_for_resolution(w, h)
        assert dirs['web_dir'], f'{w}x{h} missing web_dir'

    @pytest.mark.parametrize('w,h', _UNIQUE_RESOLUTIONS,
                             ids=[f'{w}x{h}' for w, h in _UNIQUE_RESOLUTIONS])
    def test_has_masks_dir(self, w, h):
        _maybe_xfail(w, h)
        dirs = _dirs_for_resolution(w, h)
        assert dirs['masks_dir'], f'{w}x{h} missing masks_dir'

    @pytest.mark.parametrize('w,h', _UNIQUE_RESOLUTIONS,
                             ids=[f'{w}x{h}' for w, h in _UNIQUE_RESOLUTIONS])
    def test_non_square_has_portrait(self, w, h):
        if w == h:
            pytest.skip('square')
        _maybe_xfail(w, h)
        dirs = _dirs_for_resolution(w, h)
        assert dirs['web_dir_portrait'], f'{w}x{h} missing portrait web'
        assert dirs['masks_dir_portrait'], f'{w}x{h} missing portrait masks'

    @pytest.mark.parametrize('w,h', _UNIQUE_RESOLUTIONS,
                             ids=[f'{w}x{h}' for w, h in _UNIQUE_RESOLUTIONS])
    def test_square_no_portrait(self, w, h):
        if w != h:
            pytest.skip('non-square')
        _maybe_xfail(w, h)
        dirs = _dirs_for_resolution(w, h)
        assert not dirs['theme_dir_portrait']
        assert not dirs['web_dir_portrait']
        assert not dirs['masks_dir_portrait']

    def test_same_resolution_same_dirs(self):
        """Multiple PM values → same resolution → same dirs."""
        dirs_320 = _dirs_for_resolution(320, 320)
        for pm in (32, 100, 101, 102):
            fbl = pm_to_fbl(pm, 0)
            w, h = fbl_to_resolution(fbl, pm)
            assert (w, h) == (320, 320)
            assert _dirs_for_resolution(w, h) == dirs_320

    def test_15_unique_resolutions(self):
        assert len(_UNIQUE_RESOLUTIONS) == 15


# =========================================================================
# 2. BUTTON IDENTITY — 36 handshakes (FBL→PM→SUB)
# =========================================================================

class TestButtonIdentity:
    """36 PM+SUB handshake combos — each resolves to resolution + button image."""

    @pytest.mark.parametrize('vid,pid,pm,sub,expected', _BUTTON_COMBOS,
                             ids=[f'{vid:04x}_{pid:04x}_PM{pm}_SUB{sub}_{img}'
                                  for vid, pid, pm, sub, img in _BUTTON_COMBOS])
    def test_get_button_image(self, vid, pid, pm, sub, expected):
        result = get_button_image(vid, pid, pm, sub)
        assert result == expected

    @pytest.mark.parametrize('pm,sub,fbl,w,h', _ALL_HANDSHAKES,
                             ids=[f'PM{c[0]}_SUB{c[1]}_{c[3]}x{c[4]}'
                                  for c in _ALL_HANDSHAKES])
    def test_pm_sub_resolves_to_real_resolution(self, pm, sub, fbl, w, h):
        """Every handshake → real resolution, never (0,0)."""
        assert w > 0 and h > 0
        got_fbl = pm_to_fbl(pm, sub)
        got_w, got_h = fbl_to_resolution(got_fbl, pm)
        assert (got_w, got_h) == (w, h)

    def test_37_handshakes(self):
        assert len(_ALL_HANDSHAKES) == 37


# =========================================================================
# 3. ORIENTATION ISOLATION
# =========================================================================

# Geometry isolation moved to tests/core/test_geometry.py (post-Orientation).


# =========================================================================
# 4. MOCK DEVICES (dev/devices.json)
# =========================================================================

@pytest.mark.skipif(
    not DEV_DATA.exists() or not DEV_DEVICES.exists(),
    reason='dev data or dev/devices.json not available',
)
class TestMockDevices:

    @pytest.fixture
    def devices(self):
        return json.loads(DEV_DEVICES.read_text())

    def test_all_devices_present(self, devices):
        assert len(devices) >= 1, "devices.json is empty"
        lcd = [d for d in devices if d['type'] == 'lcd']
        led = [d for d in devices if d['type'] == 'led']
        assert len(lcd) >= 1, "no LCD devices in devices.json"
        assert len(led) >= 1, "no LED devices in devices.json"

    def test_every_lcd_pm_sub_resolves(self, devices):
        for dev in devices:
            if dev['type'] != 'lcd':
                continue
            pm, sub = dev['pm'], dev.get('sub', 0)
            fbl = pm_to_fbl(pm, sub)
            w, h = fbl_to_resolution(fbl, pm)
            ew, eh = map(int, dev['resolution'].split('x'))
            assert (w, h) == (ew, eh), (
                f"{dev['name']}: PM={pm} SUB={sub} → {w}x{h}, expected {ew}x{eh}")

    def test_every_lcd_has_dirs(self, devices):
        for dev in devices:
            if dev['type'] != 'lcd':
                continue
            w, h = map(int, dev['resolution'].split('x'))
            if (w, h) not in _HAS_DEV_DATA:
                continue
            dirs = _dirs_for_resolution(w, h)
            assert dirs['web_dir'], f"{dev['name']} missing web_dir"
            assert dirs['masks_dir'], f"{dev['name']} missing masks_dir"

    def test_led_has_no_resolution(self, devices):
        for dev in devices:
            if dev['type'] == 'led':
                assert 'resolution' not in dev

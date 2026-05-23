#!/usr/bin/env python3
"""Programmatic GUI smoke — drives TRCCApp through the rotation+web-mask path.

Mirrors dev/mock_gui.py setup, but instead of waiting for human clicks
schedules a QTimer-driven action sequence that fires the same signals
the GUI buttons would emit:

  1. Select the non-square device (1280×480 Trofeo Vision, lcd:1)
  2. Set rotation to 90° via the rotation combo
  3. Pick a portrait mask from web/zt4801280/000a/

After each step it captures geometry / dir / overlay state through the
public LCDDevice surface, then asserts the post-condition that a real
GUI session should produce.  Any failure exits non-zero.

Usage:
    PYTHONPATH=src python3 dev/smoke_rotation_mask.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast

os.environ.pop('QT_QPA_PLATFORM', None)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # headless run

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mock_bootstrap import bootstrap

_FAILURES: list[str] = []


def _check(cond: bool, label: str, detail: str = '') -> None:
    """Lightweight assertion — collects failures rather than raising."""
    if cond:
        print(f"  PASS  {label}")
    else:
        msg = f"  FAIL  {label}" + (f" — {detail}" if detail else "")
        print(msg)
        _FAILURES.append(label)


def _clear_saved_device_state() -> None:
    """Wipe per-device rotation/brightness/mask from config.json so the
    smoke runs from a known baseline regardless of prior smoke runs."""
    import json
    cfg_path = Path(__file__).resolve().parent / '.trcc' / 'config.json'
    if not cfg_path.exists():
        return
    try:
        cfg = json.loads(cfg_path.read_text())
    except (json.JSONDecodeError, OSError):
        return
    for v in cfg.get('devices', {}).values():
        for k in ('rotation', 'mask_id', 'mask_path', 'theme_name',
                  'theme_type', 'overlay'):
            v.pop(k, None)
    cfg_path.write_text(json.dumps(cfg, indent=2))


def main() -> int:
    _clear_saved_device_state()
    platform = bootstrap()

    from trcc.ui.gui.assets import _PKG_ASSETS_DIR, set_assets_dir
    set_assets_dir(platform.resolve_assets_dir(_PKG_ASSETS_DIR))

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    qapp = cast(QApplication, QApplication.instance() or QApplication(sys.argv))

    from trcc._boot import trcc as _boot_trcc
    from trcc.adapters.render.qt import QtRenderer
    renderer = QtRenderer()
    t = _boot_trcc(cast(Any, platform), renderer=renderer, discover_now=True)

    from trcc.ui.gui.trcc_app import TRCCApp as _TRCCApp
    window = _TRCCApp(platform=cast(Any, platform), decorated=False)

    from itertools import chain

    from trcc.core.events import Topic
    t.events.publish(
        Topic.DEVICE_LIST, tuple(chain(t.lcd_devices, t.led_devices)),
    )

    # We don't need metrics polling for this smoke; skip start_metrics_loop.

    # ── Test sequence ──────────────────────────────────────────────────────

    def step1_select_non_square_device() -> None:
        print("\nstep 1: select 1280x480 device (lcd:1)")
        # uc_device.devices is the list of device dicts shown in the sidebar.
        # The path looks like "mock:lcd:1:0418:5303" for the Trofeo Vision.
        target = next(
            (d for d in window.uc_device.devices
             if '0418:5303' in str(d.get('path', ''))),
            None,
        )
        if not target:
            print(f"  available device paths: "
                  f"{[d.get('path') for d in window.uc_device.devices]}")
            _check(False, "found 1280x480 device in sidebar")
            qapp.quit()
            return
        print(f"  selecting path={target.get('path')}")
        window.uc_device._select_device(target)

    def _set_rotation_via_handler(index: int) -> None:
        """Drive rotation through the same handler the combo's signal fires.

        We can't always go through the QComboBox because (a) the combo's
        currentIndex may match the device's current rotation already
        (no-op signal) and (b) ``h.set_rotation`` doesn't sync the combo.
        ``_on_rotation_change`` is the single chokepoint the GUI uses.
        """
        window._on_rotation_change(index)

    def step2_baseline_rotation_zero() -> None:
        print("\nstep 2: assert baseline geometry at rotation=0")
        h = window._active_lcd()
        if not h:
            _check(False, "active handler exists")
            qapp.quit()
            return
        # _clear_saved_device_state() above wiped the per-device rotation
        # from the on-disk config, so step 1's _restore_rotation(0) chain
        # leaves the device at 0° already.  No explicit reset needed —
        # an explicit set_rotation(0) here triggers a heavy theme/reload
        # pipeline whose nested Qt event loop can let later QTimers fire,
        # which races with step 3's setCurrentIndex(1).
        lcd = h.display
        print(f"  rotation={lcd.rotation}, canvas={lcd.canvas_resolution}")
        _check(lcd.native_resolution == (1280, 480),
               "native_resolution == (1280, 480)",
               f"got {lcd.native_resolution}")
        _check(lcd.canvas_resolution == (1280, 480),
               "canvas_resolution == (1280, 480) at rotation 0",
               f"got {lcd.canvas_resolution}")
        _check(lcd.is_rotated() is False, "is_rotated() False at 0°")
        _check(not lcd.has_portrait_themes,
               "has_portrait_themes False (only theme1280480/ on disk)")

    def step3_set_rotation_90() -> None:
        print("\nstep 3: set rotation 90 via combo")
        # Index 1 == 90° per rotation_combo.addItems(["0°", "90°", "180°", "270°"]).
        _set_rotation_via_handler(1)

    def step4_assert_canvas_swapped() -> None:
        print("\nstep 4: post-rotation geometry")
        h = window._active_lcd()
        lcd = h.display
        _check(lcd.canvas_resolution == (480, 1280),
               "canvas_resolution swaps to (480, 1280)",
               f"got {lcd.canvas_resolution}")
        _check(lcd.output_resolution == (480, 1280),
               "output_resolution == (480, 1280)",
               f"got {lcd.output_resolution}")
        _check(lcd.is_rotated() is True, "is_rotated() True at 90°")
        # has_portrait_themes is False on this device, so theme_dir stays
        # at theme1280480/ (landscape) and image_rotation fires pixel rotation.
        td = lcd.theme_dir
        _check(td is not None and 'theme1280480' in str(td.path),
               "theme_dir stays at theme1280480 (no portrait variant on disk)",
               f"got {td.path if td else None}")
        # Web masks dir DOES swap because zt4801280/ exists.
        masks = lcd.masks_dir
        _check(masks is not None and 'zt4801280' in str(masks),
               "masks_dir swaps to zt4801280",
               f"got {masks}")
        _check(lcd._display_svc.image_rotation_for(0, 0) == 90,
               "image_rotation_for == 90 (theme is landscape, needs pixel rotate)",
               f"got {lcd._display_svc.image_rotation_for(0, 0)}")

    def step5_apply_portrait_mask() -> None:
        print("\nstep 5: apply portrait web mask 000a from zt4801280/")
        mask_dir = Path(platform.data_dir()) / 'web' / 'zt4801280' / '000a'
        if not mask_dir.exists():
            _check(False, f"portrait mask dir exists: {mask_dir}")
            qapp.quit()
            return
        from trcc.core.models import MaskInfo
        mask_info = MaskInfo(
            name='000a', path=mask_dir,
            preview_path=mask_dir / 'Theme.png',
        )
        # Same signal the user clicking a mask thumbnail emits.
        window.uc_theme_mask.mask_selected.emit(mask_info)

    def step6_assert_mask_applied() -> None:
        print("\nstep 6: post-mask state (TEST 1 done)")
        h = window._active_lcd()
        lcd = h.display
        msd = lcd._display_svc.mask_source_dir
        _check(msd is not None and 'zt4801280' in str(msd) and msd.name == '000a',
               "mask_source_dir == web/zt4801280/000a",
               f"got {msd}")
        # Overlay should be sized to canvas (= output) post-collapse.
        ow, oh = lcd._display_svc.overlay.width, lcd._display_svc.overlay.height
        _check((ow, oh) == (480, 1280),
               "overlay sized to (480, 1280) — canvas after rotation",
               f"got {(ow, oh)}")
        # A render must produce a frame.
        rendered = lcd._display_svc.render_overlay()
        _check(rendered is not None,
               "render_overlay() returns a frame after mask apply")

    # ── TEST 2: mask reload landscape→portrait on rotation ─────────────────
    # Resets rotation to 0, applies a LANDSCAPE mask (zt1280480/001a),
    # then rotates back to 90.  This is the path through
    # _reload_mask_for_rotation (in lcd_theme_workflow.py) — when there's
    # an active zt mask and rotation flips, the same-named mask should
    # reload from the new masks_dir.

    def step7_reset_rotation() -> None:
        print("\nstep 7: rotate back to 0 for mask-reload test")
        _set_rotation_via_handler(0)

    def step8_apply_landscape_mask() -> None:
        print("\nstep 8: apply landscape mask 001a from zt1280480/")
        mask_dir = Path(platform.data_dir()) / 'web' / 'zt1280480' / '001a'
        if not mask_dir.exists():
            _check(False, f"landscape mask dir exists: {mask_dir}")
            return
        from trcc.core.models import MaskInfo
        mask_info = MaskInfo(
            name='001a', path=mask_dir,
            preview_path=mask_dir / 'Theme.png',
        )
        window.uc_theme_mask.mask_selected.emit(mask_info)

    def step9_assert_landscape_mask_loaded() -> None:
        print("\nstep 9: verify landscape mask state pre-rotation")
        h = window._active_lcd()
        lcd = h.display
        msd = lcd._display_svc.mask_source_dir
        _check(msd is not None and 'zt1280480' in str(msd) and msd.name == '001a',
               "mask_source_dir == web/zt1280480/001a (landscape variant)",
               f"got {msd}")
        ow, oh = lcd._display_svc.overlay.width, lcd._display_svc.overlay.height
        _check((ow, oh) == (1280, 480),
               "overlay sized to (1280, 480) — landscape canvas",
               f"got {(ow, oh)}")

    def step10_rotate_with_active_mask() -> None:
        print("\nstep 10: rotate to 90 — should reload mask from portrait variant")
        _set_rotation_via_handler(1)

    def step11_assert_mask_reloaded_to_portrait() -> None:
        print("\nstep 11: post-rotation, mask should now be the portrait variant")
        h = window._active_lcd()
        lcd = h.display
        msd = lcd._display_svc.mask_source_dir
        _check(msd is not None and 'zt4801280' in str(msd) and msd.name == '001a',
               "mask_source_dir reloaded to web/zt4801280/001a (portrait, same name)",
               f"got {msd}")
        ow, oh = lcd._display_svc.overlay.width, lcd._display_svc.overlay.height
        _check((ow, oh) == (480, 1280),
               "overlay resized to (480, 1280) post-reload",
               f"got {(ow, oh)}")
        rendered = lcd._display_svc.render_overlay()
        _check(rendered is not None,
               "render_overlay() succeeds with portrait mask after reload")

    # ── TEST 3: cloud background PNG, rotate, image_rotation pixel-rotates ─
    # Static PNG backgrounds don't auto-swap dirs (only .mp4 cloud videos
    # do via _reload_cloud_theme_for_rotation).  This verifies the static
    # path: load a PNG, rotate, the canvas swaps and the renderer applies
    # image_rotation when producing the preview frame.

    def step12_reset_to_zero_and_clear_mask() -> None:
        print("\nstep 12: rotate to 0 + clear mask for background test")
        _set_rotation_via_handler(0)
        h = window._active_lcd()
        lcd = h.display
        # Drop any active mask so the bg-only path is what we measure.
        lcd._display_svc.overlay.set_theme_mask(None)
        lcd._display_svc.mask_source_dir = None

    def step13_load_cloud_bg_image() -> None:
        print("\nstep 13: load cloud background image from web/1280480/a001.png")
        bg_path = Path(platform.data_dir()) / 'web' / '1280480' / 'a001.png'
        if not bg_path.exists():
            _check(False, f"landscape bg exists: {bg_path}")
            return
        h = window._active_lcd()
        h.display.load_image(str(bg_path))

    def step14_rotate_with_cloud_bg() -> None:
        print("\nstep 14: rotate to 90 with cloud bg loaded")
        _set_rotation_via_handler(1)

    def step15_assert_bg_pixel_rotated() -> None:
        print("\nstep 15: post-rotation, image_rotation should pixel-rotate the bg")
        h = window._active_lcd()
        lcd = h.display
        # has_portrait_themes is False → image_rotation_for returns rotation degrees.
        rot = lcd._display_svc.image_rotation_for(0, 0)
        _check(rot == 90,
               "image_rotation_for == 90 — preview pixel-rotates the static bg",
               f"got {rot}")
        # Canvas + output align to portrait now.
        _check(lcd.canvas_resolution == (480, 1280),
               "canvas_resolution == (480, 1280) at rotation 90",
               f"got {lcd.canvas_resolution}")
        # A render must still produce a frame end-to-end.
        rendered = lcd._display_svc.render_overlay()
        _check(rendered is not None,
               "render_overlay() succeeds with rotated cloud bg")

    def finish() -> None:
        print("\n" + "=" * 60)
        if _FAILURES:
            print(f"FAIL: {len(_FAILURES)} assertion(s) failed:")
            for f in _FAILURES:
                print(f"  - {f}")
        else:
            print("PASS: all assertions passed")
        print("=" * 60)
        qapp.exit(1 if _FAILURES else 0)

    # Interleave action steps and assertion steps so signal-driven side
    # effects fully process before we read state back.  Each schedule is
    # 200ms apart to let Qt event loop settle.
    QTimer.singleShot(200,  step1_select_non_square_device)
    QTimer.singleShot(400,  step2_baseline_rotation_zero)
    QTimer.singleShot(600,  step3_set_rotation_90)
    QTimer.singleShot(900,  step4_assert_canvas_swapped)
    QTimer.singleShot(1100, step5_apply_portrait_mask)
    QTimer.singleShot(1400, step6_assert_mask_applied)
    # Test 2 — landscape mask → rotate 90 → portrait reload
    QTimer.singleShot(1600, step7_reset_rotation)
    QTimer.singleShot(1800, step8_apply_landscape_mask)
    QTimer.singleShot(2100, step9_assert_landscape_mask_loaded)
    QTimer.singleShot(2300, step10_rotate_with_active_mask)
    QTimer.singleShot(2700, step11_assert_mask_reloaded_to_portrait)
    # Test 3 — cloud bg PNG, rotate, image_rotation pixel-rotates
    QTimer.singleShot(2900, step12_reset_to_zero_and_clear_mask)
    QTimer.singleShot(3100, step13_load_cloud_bg_image)
    QTimer.singleShot(3300, step14_rotate_with_cloud_bg)
    QTimer.singleShot(3600, step15_assert_bg_pixel_rotated)
    QTimer.singleShot(3900, finish)

    # Window must be shown so widget event handling is fully wired.
    window.show()
    return qapp.exec()


if __name__ == '__main__':
    sys.exit(main())

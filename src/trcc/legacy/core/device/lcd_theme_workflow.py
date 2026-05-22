"""LCDThemeWorkflow — multi-step theme + rotation-reload logic for an LCD.

LCDDevice composes one of these per device.  The workflow needs broad
access to the device's services (display, theme, persistence) and to a
few of its facade methods (``select``, ``set_config``, ``enable_overlay``,
``render_and_send``).  Rather than inject a dozen separate dependencies,
the workflow holds a back-reference to the owning ``LCDDevice`` — they're
tightly coupled by intent (the workflow IS a method group of the
device, just split into a sibling file for SRP and file-size hygiene).

Public surface (called from LCDDevice's facade methods):
    select / load_by_name / save / set_mask_from_path / export_config /
    import_config / restore_last / reload_theme_for_rotation /
    reload_mask_for_rotation / apply_overlay_from_dir
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..models import ThemeInfo, ThemeType
from ..paths import resolve_theme_dir

if TYPE_CHECKING:
    from .lcd import LCDDevice


class LCDThemeWorkflow:
    """Theme load/restore/rotation-reload + theme save/import/export."""

    __slots__ = ('_device',)

    def __init__(self, device: LCDDevice) -> None:
        self._device = device

    @property
    def log(self) -> Any:
        return self._device.log

    # ── select / load_by_name ───────────────────────────────────────────────

    def select(self, theme: Any) -> dict:
        """Select and load a theme (local or cloud)."""
        d = self._device
        d.log.debug("select: theme=%s type=%s",
                    getattr(theme, 'name', theme),
                    type(theme).__name__)
        d._theme_svc.select(theme)
        if not theme:
            return {"success": False, "error": "No theme provided"}

        if theme.theme_type == ThemeType.CLOUD:
            result = d._display_svc.load_cloud_theme(theme)
        else:
            result = d._display_svc.load_local_theme(theme)

        image = result.get('image')
        is_animated = result.get('is_animated', False)

        return {
            "success": True,
            "image": image,
            "is_animated": is_animated,
            "interval": d._display_svc.get_video_interval() if is_animated else 0,
            "status": result.get('status', ''),
            "message": (
                f"Theme: {theme.name}" if hasattr(theme, 'name')
                else "Theme loaded"
            ),
        }

    def load_by_name(self, name: str, width: int = 0, height: int = 0) -> dict:
        d = self._device
        w, h = (width, height) if width and height else d.lcd_size
        td = d.theme_dir
        theme_dir = td.path if td else Path(resolve_theme_dir(w, h))
        utd = d.user_theme_dir
        themes = d._theme_svc.discover_local_merged(theme_dir, utd, (w, h))
        match = next((t for t in themes if t.name == name), None)
        if not match:
            return {"success": False, "error": f"Theme '{name}' not found"}

        result = self.select(match)
        if not result.get("success"):
            return result

        image = result.get("image")
        is_animated = result.get("is_animated", False)

        overlay_config = None
        if match.path:
            overlay_config = self.apply_overlay_from_dir(str(match.path))
            if overlay_config and not is_animated:
                rendered = d.render_and_send()
                image = rendered.get("image") or image
                result["image"] = image
            elif not overlay_config:
                d.enable_overlay(False)
                if image and not is_animated:
                    d.send(image)
        elif image and not is_animated:
            d.send(image)
        result["overlay_config"] = overlay_config
        result["theme_path"] = match.path
        result["config_path"] = match.config_path

        dev = d._device_svc.selected if d._device_svc else None
        if dev and match.path and d._lcd_config:
            d._lcd_config.persist(dev, 'theme_name', match.name)
            d._lcd_config.persist(dev, 'theme_type', 'local')
            d._lcd_config.persist(dev, 'mask_id', '')

        return result

    # ── save / mask / import / export ───────────────────────────────────────

    def save(self, name: str) -> dict:
        ok, msg = self._device._display_svc.save_theme(name)
        return {"success": ok, "message": msg}

    def set_mask_from_path(self, path: Any) -> dict:
        d = self._device
        p = Path(path)
        if p.is_dir():
            image = d._display_svc.apply_mask(p)
            return {
                "success": True,
                "image": image,
                "message": f"Mask: {p.name}",
            }
        from ...services.image import ImageService
        from ...services.overlay import OverlayService
        r = ImageService.renderer()
        w, h = d.lcd_size
        mask_img = OverlayService.load_mask_from_path(p, r, w, h)
        if mask_img is None:
            return {"success": False, "error": f"Failed to load mask: {path}"}
        d._display_svc.overlay.set_theme_mask(mask_img)
        d._display_svc.mask_source_dir = p.parent
        return {"success": True, "message": f"Mask: {p.name}"}

    def export_config(self, path: Any) -> dict:
        ok, msg = self._device._display_svc.export_config(Path(path))
        return {"success": ok, "message": msg}

    def import_config(self, path: Any, data_dir: Any) -> dict:
        ok, msg = self._device._display_svc.import_config(
            Path(path), Path(data_dir))
        return {"success": ok, "message": msg}

    # ── restore_last + sub-resolvers ────────────────────────────────────────

    def restore_last(self) -> dict:
        """Restore theme, mask, and overlay from per-device config."""
        d = self._device
        dev = d._device_svc.selected if d._device_svc else None
        if not dev or not d._lcd_config:
            return {"success": False, "error": "No device selected"}
        cfg = d._lcd_config.get_config(dev)
        cfg = d._lcd_config.normalize_legacy_theme(cfg)

        resolved = self._resolve_restore_theme(cfg)
        if "error" in resolved:
            return resolved
        if "early_return" in resolved:
            return resolved["early_return"]
        theme = resolved["theme"]
        theme_path = resolved["path"]

        result = self.select(theme)
        if not result.get("success"):
            return {**result, "overlay_config": None,
                    "overlay_enabled": False, "is_animated": False}

        overlay_config, overlay_enabled = self._restore_mask_and_overlay(cfg)

        image = result.get("image")
        is_animated = result.get("is_animated", False)
        if not is_animated and d.connected:
            rendered = d.render_and_send()
            image = rendered.get("image") or image

        return {
            "success": True,
            "image": image,
            "is_animated": is_animated,
            "overlay_config": overlay_config,
            "overlay_enabled": overlay_enabled,
            "message": f"Restored theme: {theme_path.name}",
        }

    def _resolve_restore_theme(self, cfg: dict) -> dict:
        """Resolve saved cfg to a ThemeInfo + path, or short-circuit return."""
        d = self._device
        theme_name = cfg.get("theme_name")
        theme_type = cfg.get("theme_type", "local")
        if not theme_name:
            return {"error": "No saved theme", "success": False}

        w, h = d.lcd_size
        svc = d._display_svc

        if theme_type == "cloud":
            if not svc or not svc.web_dir:
                return {"error": "No cloud theme directory", "success": False}
            path = svc.web_dir / f"{theme_name}.mp4"
            if not path.exists():
                return {
                    "error": f"Cloud theme not found: {theme_name}",
                    "success": False,
                }
            preview = path.parent / f"{theme_name}.png"
            return {
                "theme": ThemeInfo.from_video(
                    path, preview if preview.exists() else None,
                ),
                "path": path,
            }

        if theme_type == "image":
            old_path = cfg.get("theme_path", "")
            if not old_path or not Path(old_path).exists():
                return {"error": "Image not found", "success": False}
            result = d.load_image(old_path)
            return {"early_return": {**result, "overlay_config": None,
                                     "overlay_enabled": False,
                                     "is_animated": False}}

        td = d.theme_dir
        if not td:
            return {"error": "No theme directory", "success": False}
        path = td.path / theme_name
        if not path.exists():
            utd = d.user_theme_dir
            if utd and (user_path := utd / theme_name).exists():
                d.log.info(
                    "restore_last_theme: found in user content dir: %s",
                    user_path,
                )
                path = user_path
            if not path.exists():
                return {
                    "error": f"Theme not found: {theme_name}",
                    "success": False,
                }
        return {
            "theme": d._theme_info_from_dir_fn(path, (w, h)),
            "path": path,
        }

    def _restore_mask_and_overlay(self, cfg: dict) -> tuple[dict | None, bool]:
        """Restore mask + overlay state from saved cfg.  Returns (config, enabled)."""
        d = self._device
        if not (mask_id := cfg.get("mask_id") or ""):
            if (old_path := cfg.get("mask_path")):
                mask_id = Path(old_path).name

        overlay_config: dict | None = None
        overlay_enabled = False

        if mask_id:
            base = (d.user_masks_dir if cfg.get("mask_custom", False)
                    else d.masks_dir)
            mask_dir = Path(base) / mask_id if base else None
            if mask_dir and mask_dir.exists():
                svc = d._display_svc
                if not (svc and svc.mask_source_dir == mask_dir):
                    d.load_mask_standalone(str(mask_dir))
                # Mask's config1.dc defines overlay element positions —
                # use it instead of the saved overlay config.
                if (mask_overlay := d.load_overlay_config_from_dir(str(mask_dir))):
                    overlay_config = mask_overlay
                    overlay_enabled = True
                    d.set_config(overlay_config)
                    d.enable_overlay(True)

        if not overlay_config and (overlay_cfg := cfg.get("overlay", {})):
            overlay_enabled = overlay_cfg.get("enabled", False)
            overlay_config = overlay_cfg.get("config") or None
            if overlay_config:
                d.set_config(overlay_config)
            d.enable_overlay(overlay_enabled)

        return overlay_config, overlay_enabled

    # ── apply_overlay_from_dir + rotation reloads ───────────────────────────

    def apply_overlay_from_dir(self, dir_path: str) -> dict | None:
        """Load + format-prefs + set + enable overlay from a directory.

        Returns the loaded config (or None if no DC/JSON found).  When found,
        format prefs are applied and the overlay is enabled.  Caller decides
        what to do when None is returned.
        """
        d = self._device
        overlay_cfg = d.load_overlay_config_from_dir(dir_path)
        if overlay_cfg:
            if d._lcd_config:
                d._lcd_config.apply_format_prefs(overlay_cfg)
            d.set_config(overlay_cfg)
            d.enable_overlay(True)
        return overlay_cfg

    def reload_theme_for_rotation(self) -> Any | None:
        d = self._device
        current = d.current_theme_path
        if not current:
            d.log.debug("_reload_theme_for_rotation: no current_theme_path")
            return None
        theme_name = current.name
        svc = d._display_svc
        for base in (svc.local_dir, svc.web_dir):
            if not base:
                continue
            candidate = Path(base) / theme_name
            if candidate.exists():
                d.log.info("_reload_theme_for_rotation: %s → %s",
                           theme_name, candidate)
                result = self.select(d._theme_info_from_dir_fn(candidate))
                if self.apply_overlay_from_dir(str(candidate)):
                    return svc.render_and_process()
                d.enable_overlay(False)
                return result.get('image')
        d.log.debug(
            "_reload_theme_for_rotation: theme '%s' not in new dirs",
            theme_name,
        )
        return None

    def reload_mask_for_rotation(
        self, svc: Any, saved_mask_dir: Path | None = None,
    ) -> Any | None:
        d = self._device
        old_mask_dir = saved_mask_dir or svc.mask_source_dir
        if not old_mask_dir or not svc.masks_dir:
            d.log.debug(
                "_reload_mask_for_rotation: no mask dir to reload "
                "(old=%s, masks_dir=%s)", old_mask_dir, svc.masks_dir,
            )
            return None
        mask_name = old_mask_dir.name
        new_mask_dir = Path(svc.masks_dir) / mask_name
        if not new_mask_dir.exists():
            d.log.debug(
                "_reload_mask_for_rotation: mask '%s' not in new masks dir %s",
                mask_name, svc.masks_dir,
            )
            svc.overlay.set_theme_mask(None)
            svc.mask_source_dir = None
            return None

        # Canvas always == output_resolution post-10B.0b, so the overlay is
        # already at the right dims and no special is_rotated branch is needed.
        cw, ch = svc.canvas_size
        svc.overlay.set_resolution(cw, ch)
        d.log.info("_reload_mask_for_rotation: %s → %s (overlay %dx%d)",
                   old_mask_dir, new_mask_dir, cw, ch)
        self.apply_overlay_from_dir(str(new_mask_dir))
        d.load_mask_standalone(str(new_mask_dir))
        return svc.render_and_process()

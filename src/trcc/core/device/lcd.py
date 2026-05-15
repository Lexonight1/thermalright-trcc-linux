"""Device — unified device object for LCD displays and LED controllers.

Discovered via USB scan, handshaked to get identity, DI'd to handlers.
One class — the difference between LCD and LED is which services are
injected by the builder. ProtocolTraits.is_led drives the branching.

CLI, GUI, and API all consume this directly (DIP — core/, not adapter/).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .._logging import tagged_logger
from .base import Device
from .lcd_persistence import LCDPersistence
from .lcd_theme_workflow import LCDThemeWorkflow

if TYPE_CHECKING:
    from ..ports import DeviceProtocol

log = logging.getLogger(__name__)


class LCDDevice(Device):
    """A USB LCD device. Discovered, handshaked, passed around.

    Owns image frames, themes, overlays, masks, video playback, and LCD
    persistence. Construction (via builder — the only correct way):

        device = ControllerBuilder.for_current_os().build_device(detected)
        device.connect(detected)
    """

    is_lcd = True
    is_led = False

    def __init__(
        self,
        *,
        protocol: Any = None,
        device_svc: Any = None,
        display_svc: Any = None,
        theme_svc: Any = None,
        renderer: Any = None,
        dc_config_cls: Any = None,
        load_config_json_fn: Any = None,
        theme_info_from_dir_fn: Any = None,
        lcd_config: Any = None,
        build_services_fn: Any = None,
        events: Any = None,
    ) -> None:
        self._protocol = protocol  # DI'd by name via DeviceProtocolFactory
        self._device_svc = device_svc
        self._display_svc = display_svc
        self._theme_svc = theme_svc
        self._renderer = renderer
        self._dc_config_cls = dc_config_cls
        self._load_config_json_fn = load_config_json_fn
        self._theme_info_from_dir_fn = theme_info_from_dir_fn
        self._lcd_config = lcd_config
        self._build_services_fn = build_services_fn
        self._events = events  # EventBus, injected by ControllerBuilder
        self._info: Any = None  # DeviceInfo, set during connect()
        self.log: logging.Logger = log
        self._persistence = LCDPersistence(device_svc, lcd_config)
        self._theme = LCDThemeWorkflow(self)
        # Transport-level connectivity flag. Flipped by protocol disconnect
        # notifications (errno ENODEV / EBADF / etc. after the threshold).
        # The natural lazy-open machinery handles re-opening on the next
        # send; this flag exists so callers / UI can surface state and so
        # we log the transition once instead of per-frame.
        self._protocol_connected: bool = True
        self._wire_protocol_observers()

    @property
    def protocol(self) -> DeviceProtocol | None:
        """The wire protocol (Scsi/HID/Bulk/Ly) DI'd by name at construction."""
        return self._protocol

    # ── Recovery — protocol disconnect / reconnect observers ─────────────

    def _wire_protocol_observers(self) -> None:
        """Subscribe to disconnect/reconnect notifications from the protocol.

        The protocol's ``_guarded_send`` fires ``on_state_changed("connected",
        bool)`` when the disconnect-failure threshold is breached or after
        the first successful send following recovery. We surface the
        transition at the device-facade level so logs / UI / callers have
        a single per-device signal instead of N per-frame WARNINGS.
        """
        if self._protocol is None:
            return
        # Chain rather than overwrite — protocols can have other listeners.
        prior = getattr(self._protocol, 'on_state_changed', None)

        def _chain(key: str, value: object) -> None:
            self._on_protocol_state_changed(key, value)
            if prior is not None:
                prior(key, value)

        self._protocol.on_state_changed = _chain

    def _on_protocol_state_changed(self, key: str, value: object) -> None:
        """React to protocol-level state transitions.

        Currently only ``key == 'connected'`` triggers behavior: log the
        transition with device identity, flip ``_protocol_connected``,
        and emit a typed event for UI subscribers. Re-opening the transport
        is handled by the natural lazy-open machinery on the next send.
        """
        if key != 'connected':
            return
        connected = bool(value)
        if connected == self._protocol_connected:
            return
        self._protocol_connected = connected
        info = self.device_info
        ident = (f"{info.vid:04X}:{info.pid:04X}@{info.path}"
                 if info is not None else "<unknown>")
        if connected:
            self.log.info("recovery: device %s reconnected", ident)
        else:
            self.log.warning("recovery: device %s disconnected — "
                             "next send will attempt to re-open transport",
                             ident)
        if self._events is not None:
            try:
                from ..events import Topic
                topic = (Topic.DEVICE_CONNECTED if connected
                          else Topic.DEVICE_DISCONNECTED)
                self._events.publish(topic, info)
            except (AttributeError, ImportError):
                pass

    # ══════════════════════════════════════════════════════════════════════
    # Shared lifecycle (DeviceInfo)
    # ══════════════════════════════════════════════════════════════════════

    def connect(self, detected: Any = None) -> dict:
        """Connect to device — handshake via protocol, fill DeviceInfo."""
        return self._connect_lcd(detected)

    @property
    def connected(self) -> bool:
        if self._info is not None:
            return True
        return bool(self._device_svc is not None and self._device_svc.selected is not None)

    @property
    def device_info(self) -> Any:
        if self._info is not None:
            return self._info
        if self._device_svc is not None:
            return self._device_svc.selected
        return None

    def tick(self) -> Any:
        """Core metrics loop hook — render overlay + send frame."""
        if self._display_svc:
            return self._tick_lcd()
        return None

    def cleanup(self) -> None:
        if self._display_svc:
            self._display_svc.cleanup()

    def update_metrics(self, metrics: Any) -> dict:
        if self._display_svc:
            self._display_svc.overlay.update_metrics(metrics)
        return {"success": True}

    def set_temp_unit(self, unit: int) -> dict:
        """Set temperature unit (0=Celsius, 1=Fahrenheit)."""
        if self._display_svc:
            self._display_svc.overlay.set_temp_unit(unit)
        return {"success": True, "message": f"Temp unit: {'F' if unit else 'C'}"}

    def initialize_pipeline(self, settings: Any) -> None:
        """Post-connect initialization — set resolution + data dir from settings."""
        if not self._display_svc:
            return
        info = self.device_info
        res = getattr(info, 'resolution', (0, 0))
        if res and res != (0, 0):
            w, h = res
            self.log.info("initialize_pipeline: %dx%d", w, h)
            settings.set_resolution(w, h)
            self.set_resolution(w, h)
            self.initialize(settings.user_data_dir)
        else:
            self.log.warning("initialize_pipeline: skipped — resolution is %s", res)
        # Issue #141 — seed overlay language from saved settings so a
        # fresh app shows weekday abbreviations in the right language
        # without waiting for a runtime language-change event.
        if self._display_svc.overlay is not None:
            self._display_svc.overlay.set_lang(getattr(settings, 'lang', 'en'))

    def notify_data_ready(self) -> None:
        """Data extraction completed — refresh theme dirs."""
        if self._display_svc is not None and self._display_svc.on_data_ready is not None:
            self._display_svc.on_data_ready()

    # ══════════════════════════════════════════════════════════════════════
    # LCD connect
    # ══════════════════════════════════════════════════════════════════════

    def _connect_lcd(self, detected: Any) -> dict:
        if self._device_svc is None:
            raise RuntimeError(
                "Device requires a DeviceService. "
                "Use ControllerBuilder.build_device() to wire dependencies.")

        device_path = None
        if isinstance(detected, str):
            device_path = detected
        elif detected is not None:
            device_path = getattr(detected, 'scsi_device', None) or \
                          getattr(detected, 'path', None)

        svc = self._device_svc
        svc.scan_and_select(device_path)
        if not svc.selected:
            return {"success": False, "error": "No LCD device found"}

        self._build_services(svc)
        dev = svc.selected
        self._info = dev

        self._tag_loggers_with_device(dev)
        self.log.info("connected: %s [%04X:%04X] %dx%d FBL=%s PM=%d SUB=%d",
                      dev.path, dev.vid, dev.pid, *dev.resolution,
                      dev.fbl_code, dev.pm_byte, dev.sub_byte)

        w, h = dev.resolution
        if w and h and self._display_svc:
            self._display_svc.set_resolution(w, h)
            self._persist_dirs()
        return {
            "success": True,
            "resolution": dev.resolution,
            "device_path": dev.path,
        }

    def _tag_loggers_with_device(self, dev: Any) -> None:
        """Re-tag this device's loggers with full identity from handshake."""
        vid = int(dev.vid) if isinstance(dev.vid, int) else 0
        pid = int(dev.pid) if isinstance(dev.pid, int) else 0
        label = (f'lcd:{dev.device_index} [{vid:04X}:{pid:04X} '
                 f'FBL={dev.fbl_code} PM={dev.pm_byte} SUB={dev.sub_byte}]')
        self.log = tagged_logger(__name__, label)
        if self._display_svc:
            self._display_svc.log = tagged_logger('trcc.services.display', label)
            if self._display_svc.overlay:
                self._display_svc.overlay.log = tagged_logger(
                    'trcc.services.overlay', label)

    def _build_services(self, device_svc: Any) -> None:
        """Wire up all services from a DeviceService via injected factory."""
        if self._build_services_fn is None:
            raise RuntimeError(
                "Device requires build_services_fn. "
                "Use ControllerBuilder.build_device() to wire dependencies.")
        result = self._build_services_fn(device_svc, self._renderer)
        self._device_svc = device_svc
        self._display_svc = result['display_svc']
        self._theme_svc = result['theme_svc']
        self._renderer = result['renderer']
        self._dc_config_cls = result['dc_config_cls']
        self._load_config_json_fn = result['load_config_json_fn']

    @classmethod
    def from_service(cls, device_svc: Any, renderer: Any = None,
                     build_services_fn: Any = None) -> LCDDevice:
        """Create a fully-wired LCD Device from an existing DeviceService."""
        device = cls(renderer=renderer, build_services_fn=build_services_fn)
        device._build_services(device_svc)
        return device

    # ══════════════════════════════════════════════════════════════════════
    # LCD tick
    # ══════════════════════════════════════════════════════════════════════

    def _tick_lcd(self) -> Any:
        """Render overlay + send frame to LCD device."""
        if not self._display_svc or self._display_svc.is_video_playing():
            return None
        new_frame = None
        if self._display_svc.overlay.enabled and self._display_svc.overlay.would_change(
            self._display_svc.overlay.metrics
        ):
            new_frame = self._display_svc.render_overlay()
            if new_frame:
                self.log.debug("tick: new overlay frame rendered")
                self._last_overlay_frame = new_frame
        image = new_frame or getattr(self, '_last_overlay_frame', None) or self._display_svc.current_image
        if image:
            self.send(image)
        return new_frame

    # ══════════════════════════════════════════════════════════════════════
    # Observer / SSoT — frame publication chokepoint
    # ══════════════════════════════════════════════════════════════════════

    def _publish_frame(self, image: Any) -> None:
        """Publish ``Topic.FRAME`` for the current preview surface.

        The Observer pattern's chokepoint: every state change on this
        LCDDevice (rotation, brightness, split mode, theme select, …) ends
        with a single call here, so any subscriber (GUI preview, API
        WebSocket, IPC) sees the rendered output without recomputing it.

        ``image`` is the post-render preview surface (Qt QImage today —
        framework-neutral once the renderer port matures).  When the caller
        also mutates device state, the surface here is what the device
        will display on its next physical refresh; consumers can rely on
        observed-pixels = device-pixels.

        No-op if events were not injected (CLI / standalone Trcc that
        skips the bus).
        """
        if not self._events or image is None:
            return
        path = self.device_path or '?'
        try:
            # Topic.FRAME contract is (device_path, preview_image) —
            # GUI handler subscribes with that exact arity.
            self._events.publish('frame', path, image)
        except Exception:
            self.log.exception("publish FRAME for %s raised", path)


    # ══════════════════════════════════════════════════════════════════════
    # LCD properties
    # ══════════════════════════════════════════════════════════════════════

    @property
    def lcd_size(self) -> tuple[int, int]:
        if self._display_svc:
            return (self._display_svc.lcd_width, self._display_svc.lcd_height)
        return (0, 0)

    @property
    def resolution(self) -> tuple[int, int]:
        return self.lcd_size

    @property
    def device_path(self) -> str | None:
        dev = self.device_info
        return dev.path if dev else None

    @property
    def current_image(self) -> Any:
        return self._display_svc.current_image if self._display_svc else None

    @current_image.setter
    def current_image(self, value: Any) -> None:
        if self._display_svc:
            self._display_svc.current_image = value

    @property
    def current_theme_path(self) -> Any:
        return self._display_svc.current_theme_path if self._display_svc else None

    @property
    def auto_send(self) -> bool:
        return self._display_svc.auto_send if self._display_svc else False

    @auto_send.setter
    def auto_send(self, value: bool) -> None:
        if self._display_svc:
            self._display_svc.auto_send = value

    @property
    def device_service(self) -> Any:
        """Direct DeviceService access (for IPC frame capture wiring)."""
        return self._device_svc

    # ══════════════════════════════════════════════════════════════════════
    # LCD — geometry delegates (replace dev.orientation.X)
    # ══════════════════════════════════════════════════════════════════════

    @property
    def canvas_size(self) -> tuple[int, int]:
        """Render-target size — native, swapped on 90°/270°.

        Reads from ``device_info`` (single source of truth — derived from
        ``resolution + rotation``). Falls back to ``_display_svc`` for
        legacy paths where ``device_info`` is not yet populated.
        """
        if (info := self.device_info) is not None:
            return info.canvas_size
        return self._display_svc.canvas_size if self._display_svc else (0, 0)

    def is_rotated(self) -> bool:
        if (info := self.device_info) is not None:
            return info.is_rotated
        return bool(self._display_svc and self._display_svc.is_rotated())

    @property
    def has_portrait_themes(self) -> bool:
        return bool(self._display_svc and self._display_svc.has_portrait_themes)

    def image_rotation_for(self, overlay_w: int, overlay_h: int) -> int:
        if not self._display_svc:
            return 0
        return self._display_svc.image_rotation_for(overlay_w, overlay_h)

    @property
    def theme_dir(self) -> Any | None:
        return self._display_svc.theme_dir if self._display_svc else None

    @property
    def local_dir(self) -> Path | None:
        return self._display_svc.local_dir if self._display_svc else None

    @property
    def web_dir(self) -> Path | None:
        return self._display_svc.web_dir if self._display_svc else None

    @property
    def masks_dir(self) -> Path | None:
        return self._display_svc.masks_dir if self._display_svc else None

    @property
    def user_theme_dir(self) -> Path | None:
        return self._display_svc.user_theme_dir if self._display_svc else None

    @property
    def user_masks_dir(self) -> Path | None:
        return self._display_svc.user_masks_dir if self._display_svc else None

    @property
    def rotation(self) -> int:
        """Current rotation degrees (0/90/180/270)."""
        return self._display_svc.rotation if self._display_svc else 0

    # ══════════════════════════════════════════════════════════════════════
    # LCD — connection helpers
    # ══════════════════════════════════════════════════════════════════════

    def set_data_ready_callback(self, fn: Any) -> None:
        if self._display_svc is not None:
            self._display_svc.on_data_ready = fn

    # ══════════════════════════════════════════════════════════════════════
    # LCD — frame ops
    # ══════════════════════════════════════════════════════════════════════

    def send_image(self, image_path: str) -> dict:
        if not os.path.exists(image_path):
            return {"success": False, "error": f"File not found: {image_path}"}
        from ...services import ImageService
        w, h = self.lcd_size
        img = ImageService.open_and_resize(image_path, w, h)
        self._device_svc.send_frame(img, w, h)
        return {"success": True, "image": img, "message": f"Sent {image_path}"}

    def send_color(self, r: int, g: int, b: int) -> dict:
        from ...services import ImageService
        w, h = self.lcd_size
        img = ImageService.solid_color(r, g, b, w, h)
        self._device_svc.send_frame(img, w, h)
        return {"success": True, "image": img,
                "message": f"Sent color #{r:02x}{g:02x}{b:02x}"}

    def send(self, image: Any) -> dict:
        """Encode and async-send image to LCD device."""
        self.log.debug("send: image=%s", type(image).__name__ if image else None)
        if not self._device_svc.selected:
            return {"success": False, "error": "No device selected"}
        w, h = self.lcd_size
        self._device_svc.send_frame_async(image, w, h)
        return {"success": True}

    def send_frame(self, image: Any) -> bool:
        """Synchronously send image to LCD device."""
        w, h = self.lcd_size
        return self._device_svc.send_frame(image, w, h)

    def send_async(self, image: Any, width: int, height: int) -> None:
        if self._device_svc.is_busy:
            return
        self._device_svc.send_frame_async(image, width, height)

    def load_image(self, path: Any) -> dict:
        image = self._display_svc.load_image_file(Path(path))
        if image:
            return {"success": True, "image": image,
                    "message": f"Loaded: {Path(path).name}"}
        return {"success": False, "error": f"Failed to load: {path}"}

    def reset(self) -> dict:
        from ...services import ImageService
        w, h = self.lcd_size
        img = ImageService.solid_color(255, 0, 0, w, h)
        self._device_svc.send_frame(img, w, h)
        return {"success": True, "image": img, "message": "Device reset — RED"}

    # ══════════════════════════════════════════════════════════════════════
    # LCD — display settings
    # ══════════════════════════════════════════════════════════════════════

    def _persist(self, field: str, value: object) -> None:
        """Single-field config write — delegates to LCDPersistence."""
        self._persistence.persist(field, value)

    def _persist_dirs(self) -> None:
        """Snapshot current per-orientation dirs to config."""
        if not self._display_svc:
            return
        self._persistence.persist_dirs(
            theme_dir=self.theme_dir,
            web_dir=self.web_dir,
            masks_dir=self.masks_dir,
            native_resolution=self.lcd_size,
        )

    def refresh_dirs(self) -> None:
        """Re-probe filesystem dirs and update config."""
        if self._display_svc:
            self._display_svc.refresh_dirs()
        self._persist_dirs()

    def restore_device_settings(self) -> None:
        """Restore brightness + rotation from per-device config."""
        saved = self._persistence.restored_settings()
        if not saved:
            return
        self.set_brightness(saved['brightness_level'])
        if saved['rotation'] is not None:
            self.set_rotation(saved['rotation'])

    def set_brightness(self, percent: int) -> dict:
        """Set LCD brightness."""
        if not 0 <= percent <= 100:
            return {"success": False,
                    "error": f"Brightness must be 0–100, got {percent}"}
        image = self._display_svc.set_brightness(percent)
        self._persist('brightness_level', percent)
        self._publish_frame(image)
        return {"success": True, "image": image,
                "message": f"Brightness set to {percent}%"}

    def set_rotation(self, degrees: int) -> dict:
        if degrees not in (0, 90, 180, 270):
            return {"success": False,
                    "error": "Rotation must be 0, 90, 180, or 270"}
        svc = self._display_svc
        old_canvas = svc.canvas_size
        old_theme_dir = svc.theme_dir
        saved_mask_dir = svc.mask_source_dir
        self.log.debug("set_rotation: %d° saved_mask_dir=%s old_theme_dir=%s",
                  degrees, saved_mask_dir,
                  old_theme_dir.path if old_theme_dir else None)
        image = svc.set_rotation(degrees)
        self._persist('rotation', degrees)

        new_theme_dir = svc.theme_dir
        # ThemeDir is a value object without __eq__, so compare paths.
        old_path = old_theme_dir.path if old_theme_dir else None
        new_path = new_theme_dir.path if new_theme_dir else None
        theme_dir_changed = (old_path != new_path)
        if old_canvas != svc.canvas_size and theme_dir_changed:
            self.log.info("set_rotation: theme dir changed %s→%s, reloading",
                     old_theme_dir.path if old_theme_dir else None,
                     new_theme_dir.path if new_theme_dir else None)
            reloaded = self._reload_theme_for_rotation()
            if reloaded is not None:
                image = reloaded
        elif old_canvas != svc.canvas_size:
            self.log.info("set_rotation: canvas changed %s→%s but theme dir "
                     "unchanged — pixel-rotating only",
                     old_canvas, svc.canvas_size)

        w, h = self.lcd_size
        if w != h and saved_mask_dir:
            is_zt_mask = saved_mask_dir.parent.name.startswith('zt')
            if is_zt_mask:
                self.log.info("set_rotation: reloading zt mask from saved_mask_dir=%s",
                         saved_mask_dir)
                image = self._reload_mask_for_rotation(svc, saved_mask_dir) or image
            else:
                self.log.debug("set_rotation: skipping mask reload — "
                          "theme built-in mask %s", saved_mask_dir)

        self._publish_frame(image)
        return {"success": True, "image": image,
                "message": f"Rotation set to {degrees}°"}

    def _apply_overlay_from_dir(self, dir_path: str) -> dict | None:
        """Delegate to LCDThemeWorkflow."""
        return self._theme.apply_overlay_from_dir(dir_path)

    def _reload_theme_for_rotation(self) -> Any | None:
        """Delegate to LCDThemeWorkflow."""
        return self._theme.reload_theme_for_rotation()

    def _reload_mask_for_rotation(
        self, svc: Any, saved_mask_dir: Path | None = None,
    ) -> Any | None:
        """Delegate to LCDThemeWorkflow."""
        return self._theme.reload_mask_for_rotation(svc, saved_mask_dir)

    def set_split_mode(self, mode: int) -> dict:
        if mode not in (0, 1, 2, 3):
            return {"success": False,
                    "error": "Split mode must be 0, 1, 2, or 3"}
        image = self._display_svc.set_split_mode(mode)
        self._persist('split_mode', mode)
        self._publish_frame(image)
        return {"success": True, "image": image,
                "message": f"Split mode: {'off' if mode == 0 else f'style {mode}'}"}

    def set_resolution(self, width: int, height: int) -> dict:
        self._display_svc.set_resolution(width, height)
        self._theme_svc.set_directories(
            local_dir=self._display_svc.local_dir,
            web_dir=self._display_svc.web_dir,
            masks_dir=self._display_svc.masks_dir,
        )
        if width and height:
            self._theme_svc.load_local_themes((width, height))
        return {"success": True, "resolution": (width, height),
                "message": f"Resolution: {width}x{height}"}

    # ══════════════════════════════════════════════════════════════════════
    # LCD — theme ops
    # ══════════════════════════════════════════════════════════════════════

    def restore_last_theme(self) -> dict:
        """Restore theme, mask, and overlay from per-device config."""
        return self._theme.restore_last()

    def select(self, theme: Any) -> dict:
        """Select and load a theme (local or cloud)."""
        return self._theme.select(theme)

    def load_theme_by_name(self, name: str,
                           width: int = 0, height: int = 0) -> dict:
        return self._theme.load_by_name(name, width, height)

    def save(self, name: str) -> dict:
        return self._theme.save(name)

    def set_mask_from_path(self, path: Any) -> dict:
        return self._theme.set_mask_from_path(path)

    def export_config(self, path: Any) -> dict:
        return self._theme.export_config(path)

    def import_config(self, path: Any, data_dir: Any) -> dict:
        return self._theme.import_config(path, data_dir)

    # ══════════════════════════════════════════════════════════════════════
    # LCD — standalone overlay/mask (CLI)
    # ══════════════════════════════════════════════════════════════════════

    def render_overlay_from_dc(self, dc_path: str, *, send: bool = False,
                               output: str | None = None,
                               metrics: Any = None) -> dict:
        from ...services import OverlayService

        if not os.path.exists(dc_path):
            return {"success": False, "error": f"Path not found: {dc_path}"}
        if not self.connected:
            return {"success": False, "error": "Device not connected"}
        if not self._display_svc:
            return {"success": False, "error": "Display service not initialized"}
        w, h = self._display_svc.canvas_size
        result_img, elements, display_opts = OverlayService.render_dc_standalone(
            Path(dc_path),
            width=w, height=h,
            renderer=self._renderer,
            load_config_json_fn=self._load_config_json_fn,
            dc_config_cls=self._dc_config_cls,
            metrics=metrics,
        )

        messages = []
        if output:
            result_img.save(output)
            messages.append(f"Saved overlay render to {output}")
        if send and self.connected:
            self.send(result_img)
            messages.append(f"Sent overlay to {self.device_path}")

        return {
            "success": True,
            "image": result_img,
            "elements": elements,
            "display_opts": display_opts,
            "message": "; ".join(messages) if messages else
                       f"Overlay config loaded: {elements} elements ({w}x{h})",
        }

    def load_mask_standalone(self, mask_path: str) -> dict:
        if not os.path.exists(mask_path):
            return {"success": False, "error": f"Path not found: {mask_path}"}
        if not self._display_svc:
            return {"success": False, "error": "Display service not initialized"}

        result = self._display_svc.apply_standalone_mask(
            Path(mask_path), self._dc_config_cls,
            is_rotated=self.is_rotated(),
        )
        if not result.get("success"):
            return result

        result_img = result["image"]
        if self.connected:
            self.send(result_img)
        return {
            "success": True,
            "image": result_img,
            "message": f"Sent mask {result['mask_file'].name} to {self.device_path}",
        }

    # ══════════════════════════════════════════════════════════════════════
    # LCD — overlay ops
    # ══════════════════════════════════════════════════════════════════════

    def set_flash_index(self, index: int) -> dict:
        if self._display_svc and self._display_svc.overlay:
            self._display_svc.overlay.flash_skip_index = index
        return {"success": True, "index": index}

    def set_mask_position(self, x: int, y: int) -> dict:
        if self._display_svc and self._display_svc.overlay:
            self._display_svc.overlay.set_mask_position((x, y))
        return {"success": True, "message": f"Mask position: ({x}, {y})"}

    def render_and_send(self) -> dict:
        if not self._display_svc:
            return {"success": False, "image": None}
        image = self._display_svc.render_overlay()
        if image and self.auto_send and self.connected:
            self.send(image)
        return {"success": True, "image": image}

    def enable_overlay(self, on: bool) -> dict:
        self._display_svc.overlay.enabled = on
        return {"success": True, "enabled": on,
                "message": f"Overlay: {'on' if on else 'off'}"}

    def set_config(self, config: dict) -> dict:
        ovl = self._display_svc.overlay
        w, h = ovl.width, ovl.height
        ovl.set_config_resolution(w, h)
        self._display_svc.overlay.set_config(config)
        return {"success": True, "message": f"Overlay config: {len(config)} elements"}

    def set_background(self, image: Any) -> dict:
        self._display_svc.overlay.set_background(image)
        if image is not None:
            self._display_svc.set_clean_background(
                self._display_svc.overlay.background)
        return {"success": True, "message": "Overlay background set"}

    def set_mask(self, image: Any,
                 position: tuple[int, int] | None = None) -> dict:
        self._display_svc.overlay.set_theme_mask(image, position)
        return {"success": True, "message": "Mask set"}

    def set_mask_visible(self, visible: bool) -> dict:
        self._display_svc.overlay.set_mask_visible(visible)
        return {"success": True,
                "message": f"Mask: {'visible' if visible else 'hidden'}"}

    def render(self) -> dict:
        image = self._display_svc.render_overlay()
        return {"success": True, "image": image}

    def update_video_cache_text(self, metrics: Any) -> dict:
        self._display_svc.update_video_cache_text(metrics)
        return {"success": True}

    @property
    def enabled(self) -> bool:
        return self._display_svc.overlay.enabled if self._display_svc else False

    @property
    def last_metrics(self) -> Any:
        return self._display_svc.overlay.metrics if self._display_svc else None

    def load_overlay_config_from_dir(self, theme_dir: str) -> dict | None:
        p = Path(theme_dir)
        overlay_config: dict | None = None

        json_path = p / 'config.json'
        if json_path.exists() and self._load_config_json_fn is not None:
            try:
                result = self._load_config_json_fn(str(json_path))
                if result is not None:
                    candidate = result[0]
                    # Legacy expects overlay_config to be a dict keyed by
                    # metric name.  Older next/ builds wrote a list-shape
                    # JSON here — treat that as invalid, fall through to
                    # config1.dc below so the theme still loads.
                    if isinstance(candidate, dict):
                        overlay_config = candidate
                        self.log.debug("load_overlay_config_from_dir: loaded from config.json")
                    else:
                        self.log.warning(
                            "load_overlay_config_from_dir: config.json has %s shape "
                            "(expected dict); falling back to config1.dc",
                            type(candidate).__name__,
                        )
            except Exception as e:
                self.log.warning("load_overlay_config_from_dir: config.json parse failed: %s", e)

        if overlay_config is None and self._dc_config_cls is not None:
            dc_path = p / 'config1.dc'
            if dc_path.exists():
                try:
                    overlay_config = self._dc_config_cls(dc_path).to_overlay_config()
                    self.log.debug("load_overlay_config_from_dir: loaded from config1.dc")
                except Exception as e:
                    self.log.warning("load_overlay_config_from_dir: config1.dc parse failed: %s", e)

        return overlay_config or None

    # ══════════════════════════════════════════════════════════════════════
    # LCD — video playback
    # ══════════════════════════════════════════════════════════════════════

    def load(self, path: Any) -> dict:
        self._display_svc.invalidate_video_cache()
        success = self._display_svc.media.load(Path(path))
        if success:
            self._display_svc.convert_media_frames()
        if success:
            return {
                "success": True,
                "state": self._display_svc.media.state,
                "message": f"Loaded: {Path(path).name}",
            }
        return {"success": False, "error": f"Failed to load: {path}"}

    def play(self) -> dict:
        self._display_svc.media.play()
        return {"success": True, "state": "playing", "message": "Playing"}

    def stop(self) -> dict:
        self._display_svc.media.stop()
        return {"success": True, "state": "stopped", "message": "Stopped"}

    def pause(self) -> dict:
        self._display_svc.media.toggle()
        playing = self._display_svc.media.is_playing
        return {
            "success": True,
            "state": "playing" if playing else "paused",
            "message": "Playing" if playing else "Paused",
        }

    def seek(self, percent: float) -> dict:
        self._display_svc.media.seek(percent)
        return {"success": True, "message": f"Seek: {percent:.0%}"}

    def video_tick(self) -> dict | None:
        if not self._display_svc:
            return None
        return self._display_svc.video_tick()

    def set_fit_mode(self, mode: str) -> dict:
        image = self._display_svc.set_video_fit_mode(mode)
        return {"success": True, "image": image, "message": f"Fit mode: {mode}"}

    @property
    def interval(self) -> int:
        return self._display_svc.get_video_interval()

    @property
    def has_frames(self) -> bool:
        return self._display_svc.media.has_frames if self._display_svc else False

    @property
    def playing(self) -> bool:
        return self._display_svc.is_video_playing() if self._display_svc else False

    def play_video_loop(
        self,
        video_path: Any,
        *,
        overlay_config: dict | None = None,
        mask_path: Any | None = None,
        metrics_fn: Any | None = None,
        on_frame: Any | None = None,
        on_progress: Any | None = None,
        loop: bool = True,
        duration: float = 0,
    ) -> dict:
        if not self._display_svc:
            return {"success": False, "error": "DisplayService not initialized"}
        self.log.info("play_video_loop: %s overlay=%s mask=%s",
                 video_path, bool(overlay_config), bool(mask_path))
        return self._display_svc.run_video_loop(
            Path(video_path),
            overlay_config=overlay_config,
            mask_path=Path(mask_path) if mask_path else None,
            metrics_fn=metrics_fn,
            on_frame=on_frame,
            on_progress=on_progress,
            loop=loop,
            duration=duration,
        )

    def keep_alive_loop(
        self,
        *,
        interval: float = 0.150,
        duration: float = 0,
        metrics_fn: Any | None = None,
        on_frame: Any | None = None,
    ) -> dict:
        if not self._display_svc:
            return {"success": False, "error": "DisplayService not initialized"}
        return self._display_svc.run_static_loop(
            interval=interval, duration=duration,
            metrics_fn=metrics_fn, on_frame=on_frame,
        )

    # ══════════════════════════════════════════════════════════════════════
    # LCD — lifecycle
    # ══════════════════════════════════════════════════════════════════════

    def initialize(self, data_dir: Any) -> dict:
        if not self._display_svc:
            return {"success": False, "error": "Not connected"}
        data_dir = Path(data_dir)
        self.log.debug("Device: initializing, data_dir=%s", data_dir)
        self._display_svc.initialize(data_dir)
        self._theme_svc.set_directories(
            local_dir=self._display_svc.local_dir,
            web_dir=self._display_svc.web_dir,
            masks_dir=self._display_svc.masks_dir,
        )
        w, h = self.lcd_size
        if w and h:
            self._theme_svc.load_local_themes((w, h))
        devices = self._device_svc.detect()
        return {
            "success": True,
            "resolution": (w, h),
            "devices": len(devices),
            "message": f"Initialized: {w}x{h}, {len(devices)} device(s)",
        }


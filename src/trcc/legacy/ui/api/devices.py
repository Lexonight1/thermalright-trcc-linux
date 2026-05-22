"""Device detection, selection, and image send endpoints — Trcc-native.

Selection state lives in this module (`_selected_index`) since FastAPI
endpoints are stateless and mock_api needs the selection to survive
between requests. The Trcc itself is the device registry — this module
just owns the per-process "currently selected" pointer.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, UploadFile

from trcc.legacy.services import ImageService
from trcc.legacy.ui.api.models import DeviceResponse

if TYPE_CHECKING:
    from trcc.legacy.core.device.lcd import LCDDevice
    from trcc.legacy.core.device.led import LEDDevice
    from trcc.legacy.core.models import DeviceInfo

log = logging.getLogger(__name__)

router = APIRouter()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# Per-process selection pointer. Endpoints look up the active device by
# index from Trcc; this just remembers which one is "current".
_selected_index: int | None = None


# ── Helpers ────────────────────────────────────────────────────────────

def _device_to_response(idx: int, info: DeviceInfo) -> DeviceResponse:
    return DeviceResponse(
        id=idx,
        name=info.name,
        vid=info.vid,
        pid=info.pid,
        protocol=info.protocol or "scsi",
        resolution=info.resolution or (0, 0),
        path=info.path or "",
    )


def _all_devices() -> list[LCDDevice | LEDDevice]:
    """All connected devices via Trcc — LCDs first, then LEDs (stable
    indexing for /devices/{id})."""
    from trcc.legacy._boot import trcc
    return list(trcc())


def _get_device_by_id(device_id: int) -> LCDDevice | LEDDevice:
    """Look up Device by index in Trcc, raise 404 if out of range."""
    devs = _all_devices()
    if device_id < 0 or device_id >= len(devs):
        raise HTTPException(
            status_code=404, detail=f"Device {device_id} not found")
    return devs[device_id]


# ── Endpoints ──────────────────────────────────────────────────────────


@router.get("/devices")
def list_devices() -> list[DeviceResponse]:
    """List currently discovered devices."""
    return [
        _device_to_response(i, d.device_info) for i, d in enumerate(_all_devices())
        if d.device_info is not None
    ]


@router.post("/devices/detect")
def detect_devices() -> list[DeviceResponse]:
    """Rescan USB for devices via Trcc.discover()."""
    from trcc.legacy._boot import trcc
    trcc().discover()
    return list_devices()


@router.post("/devices/{device_id}/select")
def select_device(device_id: int) -> dict:
    """Select a device by index. Initializes the dispatcher and restores
    last theme + device settings."""
    global _selected_index
    import trcc.legacy.ui.api as api

    device = _get_device_by_id(device_id)  # raises 404 if invalid
    info = device.device_info

    # Skip teardown when re-selecting the same device that's already active
    if _selected_index == device_id and api._device_dispatcher is not None:
        return {
            "selected": info.name if info else "",
            "resolution": info.resolution if info else (0, 0),
        }

    _selected_index = device_id

    # Stop any background tasks from a previous device
    api.stop_video_playback()
    api.stop_overlay_loop()

    # API drives the device directly via the Trcc-built object. In daemon
    # mode the API process IS a TrccProxy client (via _boot.trcc()), so
    # the same path covers both deployments — no separate routing needed.
    api._device_dispatcher = device

    from trcc.legacy.core.device.lcd import LCDDevice
    if isinstance(device, LCDDevice) and info is not None:
        w, h = info.resolution or (0, 0)

        # Background-prefetch theme assets for this resolution
        if w and h:
            from trcc.legacy._boot import trcc
            trcc()._ensure_data_background(device, w, h)

        api.set_current_image(ImageService.solid_color(0, 0, 0, w, h))

        device.restore_device_settings()
        if (result := device.restore_last_theme()) and result.get("image"):
            api.set_current_image(result["image"])
            log.info("Restored last theme for preview")

    if info is not None:
        w, h = info.resolution or (0, 0)
        if w and h:
            api.mount_static_dirs(w, h)
        return {"selected": info.name, "resolution": info.resolution}
    return {"selected": "", "resolution": (0, 0)}


@router.get("/devices/{device_id}")
def get_device(device_id: int) -> DeviceResponse:
    """Get details for a specific device."""
    device = _get_device_by_id(device_id)
    if device.device_info is None:
        raise HTTPException(
            status_code=404, detail=f"Device {device_id} has no info")
    return _device_to_response(device_id, device.device_info)


@router.post("/devices/{device_id}/send")
async def send_image(device_id: int, image: UploadFile, rotation: int = 0,
                     brightness: int = 100) -> dict:
    """Send an image to the device LCD.

    Accepts image file upload. Validates size and format before processing.
    Routes through LCDDevice for consistent encoding and send behavior.
    """
    import tempfile
    from pathlib import Path

    from trcc.legacy.ui.api.display import _get_display

    _get_device_by_id(device_id)  # validate device exists
    lcd = _get_display()

    # Read and validate upload
    data = await image.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 10 MB limit")

    # Save upload to temp file, load via LCDDevice
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(data)
        tmp_path = Path(f.name)

    try:
        result = lcd.load_image(tmp_path)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error", "Invalid image"))

        if rotation:
            lcd.set_rotation(rotation)
        if brightness != 100:
            lcd.set_brightness(brightness)

        send_result = lcd.send(result["image"])
        if not send_result.get("success"):
            raise HTTPException(status_code=500, detail="Send failed (device busy or error)")
    finally:
        tmp_path.unlink(missing_ok=True)

    w, h = lcd.lcd_size
    return {"sent": True, "resolution": (w, h)}

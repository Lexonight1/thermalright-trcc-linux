"""Control Center API endpoints — app-level settings + updates + snapshots.

All endpoints go through the Trcc command layer via the API singleton.
Same commands GUI and CLI use — parity rule enforced at the HTTP boundary
by `asdict(result)` returning the same Result dataclass shape.
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from trcc.legacy._boot import trcc

router = APIRouter(prefix='/app', tags=['Control Center'])


# =========================================================================
# Request bodies
# =========================================================================

class TempUnitRequest(BaseModel):
    unit: str   # 'C' | 'F'


class LanguageRequest(BaseModel):
    lang: str   # ISO 639-1


class BoolFlagRequest(BaseModel):
    enabled: bool


class RefreshRequest(BaseModel):
    seconds: int


class GpuRequest(BaseModel):
    gpu_key: str


# =========================================================================
# Endpoints
# =========================================================================

@router.get('/snapshot')
def snapshot() -> dict:
    """Return the full Control Center state as JSON."""
    return asdict(trcc().control_center.snapshot())


@router.put('/temp-unit')
def set_temp_unit(req: TempUnitRequest) -> dict:
    result = trcc().control_center.set_temp_unit(req.unit)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return asdict(result)


@router.put('/language')
def set_language(req: LanguageRequest) -> dict:
    result = trcc().control_center.set_language(req.lang)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return asdict(result)


@router.put('/autostart')
def set_autostart(req: BoolFlagRequest) -> dict:
    result = trcc().control_center.set_autostart(req.enabled)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return asdict(result)


@router.put('/hdd')
def set_hdd_enabled(req: BoolFlagRequest) -> dict:
    result = trcc().control_center.set_hdd_enabled(req.enabled)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return asdict(result)


@router.put('/refresh')
def set_refresh_interval(req: RefreshRequest) -> dict:
    result = trcc().control_center.set_metrics_refresh(req.seconds)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return asdict(result)


@router.put('/gpu')
def set_gpu_device(req: GpuRequest) -> dict:
    result = trcc().control_center.set_gpu_device(req.gpu_key)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return asdict(result)


@router.get('/gpus')
def list_gpus() -> list[dict]:
    gpus = trcc().control_center.list_gpus()
    return [{'key': k, 'name': n} for k, n in gpus]


@router.get('/sensors')
def list_sensors() -> list[dict]:
    return [asdict(s) for s in trcc().control_center.list_sensors()]


@router.post('/update/check')
def check_for_update() -> dict:
    return asdict(trcc().control_center.check_for_update())


@router.post('/update/install')
def run_upgrade() -> dict:
    result = trcc().control_center.run_upgrade()
    if not result.success:
        raise HTTPException(status_code=500, detail=result.error)
    return asdict(result)


@router.get('/status')
def unified_status() -> dict:
    """Unified snapshot — app + every connected LCD + every connected LED.

    Mirrors the `trcc status --json` CLI command.
    """
    t = trcc()
    # pylint: disable=protected-access
    return {
        'app': asdict(t.control_center.snapshot()),
        'lcd_devices': [
            asdict(t.lcd.snapshot(i))
            for i in range(len(t._lcd_devices))
        ],
        'led_devices': [
            asdict(t.led.snapshot(i))
            for i in range(len(t._led_devices))
        ],
    }

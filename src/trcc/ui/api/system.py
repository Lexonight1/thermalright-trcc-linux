"""System metrics and diagnostic report endpoints."""
from __future__ import annotations

import dataclasses
import logging

from fastapi import APIRouter

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


def _get_system_svc():
    """Get the shared SystemService instance (initialized by configure_app())."""
    from fastapi import HTTPException

    import trcc.ui.api as api
    if api._system_svc is None:
        raise HTTPException(status_code=503, detail="System service not initialized")
    return api._system_svc


@router.get("/metrics")
def get_metrics() -> dict:
    """All system metrics as JSON (CPU, GPU, memory, disk, network, fans)."""
    from trcc._boot import trcc
    m = trcc().os.metrics
    return dataclasses.asdict(m)


@router.get("/metrics/{category}")
def get_metrics_by_category(category: str) -> dict:
    """Filtered metrics by category (cpu, gpu, mem, disk, net, fan)."""
    from fastapi import HTTPException

    prefix_map = {
        "cpu": "cpu_",
        "gpu": "gpu_",
        "mem": "mem_",
        "memory": "mem_",
        "disk": "disk_",
        "net": "net_",
        "network": "net_",
        "fan": "fan_",
    }

    if not (prefix := prefix_map.get(category.lower())):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown category '{category}'. Use: {', '.join(sorted(prefix_map.keys()))}",
        )

    from trcc._boot import trcc
    m = trcc().os.metrics
    all_data = dataclasses.asdict(m)
    return {k: v for k, v in all_data.items() if k.startswith(prefix)}


@router.get("/gpu")
def get_gpu_list() -> dict:
    """List available GPUs and current selection via Trcc."""
    from trcc._boot import trcc
    snap = trcc().control_center.snapshot()
    return {
        "gpus": [{"key": k, "name": n} for k, n in snap.gpu_list],
        "selected": snap.gpu_device,
    }


@router.put("/gpu")
def set_gpu(gpu_key: str) -> dict:
    """Set the active GPU for metrics via Trcc."""
    from fastapi import HTTPException

    from trcc._boot import trcc
    t = trcc()
    valid_keys = {k for k, _ in t.control_center.list_gpus()}
    if gpu_key not in valid_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown GPU '{gpu_key}'. Available: {', '.join(sorted(valid_keys))}",
        )
    result = t.control_center.set_gpu_device(gpu_key)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    # Also tell the running system service to use this GPU going forward
    try:
        svc = _get_system_svc()
        svc.enumerator.set_preferred_gpu(gpu_key)
    except Exception as e:
        log.warning("Could not update system service GPU preference: %s", e)
    log.info("API: GPU set to %s", gpu_key)
    return {"selected": gpu_key}


@router.get("/report")
def get_report() -> dict:
    """Generate diagnostic report for bug reports."""
    from trcc.adapters.infra.debug_report import DebugReport

    rpt = DebugReport()
    rpt.collect()
    return {"report": str(rpt)}


@router.get("/perf")
def get_perf() -> dict:
    """Run CPU + memory performance benchmarks."""
    from trcc.services.perf import run_benchmarks

    report = run_benchmarks()
    return report.to_dict()


@router.get("/perf/device")
def get_perf_device() -> dict:
    """Benchmark connected hardware (USB handshake, frame latency, FPS)."""
    from trcc._boot import trcc as _trcc
    from trcc.services.perf import run_device_benchmarks

    t = _trcc()
    report = run_device_benchmarks(
        detect_fn=t.detect,
        get_protocol=t.protocol_for,
        get_protocol_info=t.protocol_info_for,
        probe_led_fn=t.probe_led,
    )
    return report.to_dict()

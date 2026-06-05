"""Reference dumper — parse every config1.dc with the LEGACY parser.

Run with the legacy worktree on the path so ``trcc.legacy.*`` imports:

    PYTHONPATH=/tmp/trcc-legacy/src python3.12 dev/_dc_legacy_dump.py <dc_root> > /tmp/legacy_dc.json

Emits ``{dc_path: [canonical_element, ...]}`` where each canonical element is
normalised to the SAME shape the current-side differ produces, so divergences
are real (not shape noise).  Legacy is the reference because it matched the
Windows app on real hardware.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from trcc.legacy.adapters.infra.dc_parser import DcParser

# Legacy metric ids → canonical sensor key (current uses ':'/'primary').
_CANON = {
    "cpu_temp": "cpu_temp", "cpu_percent": "cpu_usage", "cpu_usage": "cpu_usage",
    "cpu_freq": "cpu_freq", "cpu_power": "cpu_power",
    "gpu_temp": "gpu_temp", "gpu_usage": "gpu_usage", "gpu_clock": "gpu_clock",
    "gpu_power": "gpu_power",
}


def _canon_metric(m: str) -> str:
    return _CANON.get(m, m.replace(":primary", "").replace(":", "_"))


def _canonical(entry: dict) -> dict:
    x, y = entry.get("x", 0), entry.get("y", 0)
    color = (entry.get("color") or "").lower()
    if "text" in entry:
        return {"kind": "text", "id": entry["text"], "fmt": None,
                "x": x, "y": y, "color": color}
    metric = entry.get("metric")
    if metric == "time":
        return {"kind": "clock", "id": "time",
                "fmt": entry.get("time_format"), "x": x, "y": y, "color": color}
    if metric == "date":
        return {"kind": "clock", "id": "date",
                "fmt": entry.get("date_format"), "x": x, "y": y, "color": color}
    if metric == "weekday":
        return {"kind": "clock", "id": "weekday", "fmt": None,
                "x": x, "y": y, "color": color}
    if metric:
        return {"kind": "metric", "id": _canon_metric(metric), "fmt": None,
                "x": x, "y": y, "color": color}
    return {"kind": "?", "id": repr(entry)[:40], "fmt": None,
            "x": x, "y": y, "color": color}


def dump(root: Path) -> dict:
    out: dict = {}
    for dc in sorted(root.rglob("config1.dc")):
        try:
            cfg = DcParser.parse(str(dc))
            overlay = DcParser.to_overlay_config(cfg)
        except Exception as e:
            out[str(dc)] = {"__error__": f"{type(e).__name__}: {e}"}
            continue
        # Legacy keeps disabled slots in to_overlay_config and skips them at
        # RENDER time (_draw_text_elements: `if not enabled: continue`).
        # Current filters at parse time, so compare enabled-only to be fair.
        out[str(dc)] = [_canonical(v) for v in overlay.values()
                        if isinstance(v, dict) and v.get("enabled", True)]
    return out


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".trcc"
    json.dump(dump(root), sys.stdout, ensure_ascii=False)

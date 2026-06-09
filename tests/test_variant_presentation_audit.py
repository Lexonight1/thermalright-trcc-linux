"""End-to-end guard: every variant SHOWS PROPERLY in the real GUI.

``dev/mock_gui`` fakes ONLY the vid/pid + fbl/sub handshake reply; everything
downstream is the real app, so a variant that's summoned but doesn't show
properly is a real app bug.  ``dev/audit_present.py`` drives the real
``run_gui`` composition, summons all ~119 catalog variants via the actual
``VariantPanel`` click path, and asserts each connects, gets the right handler,
resolves a canvas, and fans live metrics to the active handler without error.

This is the heavyweight E2E guard: it builds the GUI and renders per variant
(~5 min).  So it is **opt-in** — set ``TRCC_GUI_AUDIT=1`` to run it (a slower CI
lane / pre-release gate / after any metrics, handler, or render change).  The
fast per-logic guards that run on every ``pytest tests/`` cover the same ground
piecewise: ``test_device_catalog_smoke`` (connect + canvas, every variant),
``test_sensors`` / ``test_metrics_personalize`` (the metrics logic).

    TRCC_GUI_AUDIT=1 PYTHONPATH=src pytest tests/test_variant_presentation_audit.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_AUDIT = _REPO / "dev" / "audit_present.py"


@pytest.mark.skipif(
    not os.environ.get("TRCC_GUI_AUDIT"),
    reason="heavyweight GUI E2E (~5 min) — set TRCC_GUI_AUDIT=1 to run",
)
def test_every_variant_shows_properly_in_the_gui() -> None:
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONPATH": str(_REPO / "src"),
    }
    proc = subprocess.run(
        [sys.executable, "-u", str(_AUDIT)],
        cwd=str(_REPO), env=env, capture_output=True, text=True,
        timeout=600, check=False,
    )
    tail = proc.stdout[-4000:]
    # The audit exits non-zero iff any variant failed an invariant.
    assert proc.returncode == 0, (
        f"variant presentation audit failed (rc={proc.returncode}):\n{tail}\n"
        f"--- stderr ---\n{proc.stderr[-1500:]}"
    )
    assert "show properly" in proc.stdout, f"audit produced no summary:\n{tail}"
    assert "] FAIL " not in proc.stdout, f"audit reported failures:\n{tail}"

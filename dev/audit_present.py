#!/usr/bin/env python3
"""Audit: every catalog variant must SHOW PROPERLY in the real GUI.

The principle (user's): ``dev/mock_gui`` fakes ONLY the vid/pid + fbl/sub
handshake reply — everything downstream (ConnectDevice → activate → present →
metrics → render) is the real app on the real hexagonal path.  So any variant
that's summoned but doesn't show what the user wants is a **real app bug**,
never a mock limitation.

This drives the EXACT path a ``VariantPanel`` click runs
(``set_active_reply`` → real ``ConnectDevice`` → ``_add_handler`` →
``_activate_device``) for every variant in ``device_catalog()``, then checks:

  1. connect  — ``ConnectDevice`` returns ok (the wire parsed this pm/sub/fbl)
  2. handler  — the right type for the wire (LED→LEDHandler, else LCDHandler)
  3. canvas   — LCD wires resolve a non-zero canvas (LED has none — segments)
  4. metrics  — a REAL OS snapshot fans out to the active handler without
                error and renders real values (the ``--`` bug class: a shim that
                made ``metrics.cpu_temp`` None → ``f"{None:.0f}"`` crashed
                ``update_metrics`` → the panel stayed on its ``--`` default).

Exit 0 = every variant shows properly; exit 1 = a bug table is printed.

    PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3.12 dev/audit_present.py
    PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3.12 dev/audit_present.py -v
"""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mock_bootstrap import bootstrap

log = logging.getLogger("dev.audit_present")

_NUMERIC = re.compile(r"^-?\d")
# Findings live at module scope so main() can read them after run_gui returns
# (on_ready runs inside the same process, before qapp.exec()).
_FINDINGS: list[Finding] = []


@dataclass
class Finding:
    """One variant's audit outcome — empty ``failures`` means it shows properly."""
    model: str
    key: str
    pm: int
    sub: int
    fbl: int
    wire: str
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _summon_and_check(window: Any, d: dict, snapshot: Any) -> Finding:
    """Run the real VariantPanel summon for one variant + assert it shows properly."""
    from trcc.core.commands import ConnectDevice
    from trcc.core.models import Wire
    from trcc.ui.gui.lcd_handler import LCDHandler
    from trcc.ui.gui.led_handler import LEDHandler

    app = window._app
    vid, pid, pm, sub, fbl = d["vid"], d["pid"], d["pm"], d["sub"], d["fbl"]
    key = f"{vid:04x}:{pid:04x}"
    wire = d["protocol"]
    f = Finding(model=d["model"], key=key, pm=pm, sub=sub, fbl=fbl, wire=wire)

    try:
        # Exact VariantPanel._on_click path.
        app.platform.set_active_reply(vid, pid, pm=pm, sub=sub, fbl=fbl)
        window._remove_handler(key)
        result = app.dispatch(ConnectDevice(key=key))

        # 1. connect
        if not getattr(result, "ok", False):
            f.failures.append(f"connect: {getattr(result, 'message', 'not ok')}")
            return f
        device = app.devices.get(key)
        if device is None or not device.is_connected:
            f.failures.append("connect: not connected after handshake")
            return f

        window._add_handler(device)
        window._active_key = ""
        window._activate_device(key)

        # 2. handler type matches the wire
        handler = window._handlers.get(key)
        is_led = wire == Wire.LED.value
        want = LEDHandler if is_led else LCDHandler
        if not isinstance(handler, want):
            f.failures.append(
                f"handler: got {type(handler).__name__}, want {want.__name__}")
            return f

        # 3. canvas (LCD wires only — LED is segment displays, no canvas)
        if not is_led:
            prof = getattr(device, "profile", None)
            res = getattr(prof, "resolution", None) if prof else None
            if not (res and res[0] > 0 and res[1] > 0):
                f.failures.append(f"canvas: invalid resolution {res}")

        # 4. metrics — a real OS snapshot fans out to the active handler and the
        #    panel WIDGET renders real values.  Read the actual gauge ``_text``
        #    (what the user sees), NOT format_sensor_gauges — checking the
        #    recompute was the false-pass that let the `app=None` bug through.
        window._last_metrics = snapshot
        try:
            window._fan_out_metrics(reason=f"audit:{f.model}")
        except Exception as e:
            f.failures.append(f"metrics: fan-out raised {type(e).__name__}: {e}")
            return f
        # LED segment panels carry the M1-M6 gauges; assert the always-readable
        # usage/clock fields show a live number (only when CPU is live, else
        # the check is vacuous — a genuinely-absent temp renders "NC", correct).
        if is_led and snapshot.cpu_percent > 0:
            imgs = getattr(getattr(handler, "_panel", None), "_info_images", {})
            if not imgs:
                f.failures.append("metrics: LED panel has no _info_images")
            for gk in ("cpu_usage", "cpu_clock"):
                txt = getattr(imgs.get(gk), "_text", None)
                if not (txt and _NUMERIC.match(txt)):
                    f.failures.append(
                        f"metrics: gauge {gk} _text={txt!r} (panel not populated)")
    except Exception as e:
        f.failures.append(f"EXC during summon: {type(e).__name__}: {e}")
        log.exception("audit: %s (%s) raised", f.model, key)
    return f


def _run_audit(window: Any) -> None:
    """on_ready hook — summon every variant, print the report, quit the loop."""
    from PySide6.QtWidgets import QApplication

    from dev_console import _variant_dicts

    from trcc.services.metrics_personalize import personalize_metrics

    app = window._app
    s = app.settings.app

    # This audit checks PRESENTATION (connect / handler / canvas / metrics), not
    # theme-render-from-downloaded-data (render was explicitly out of scope).
    # Cut the network so 119 summons don't each block on on-demand downloads —
    # several resolutions 404 with retries, which blew the run past its timeout.
    # The fetcher raises HttpFetchError on failure (the installer catches it), so
    # raising instantly = a fast no-op download.  Everything else stays real.
    from trcc.adapters.repo.http import HttpFetchError, UrllibHttpFetcher
    UrllibHttpFetcher.fetch = (  # type: ignore[method-assign]
        lambda self, url, *a, **k: (_ for _ in ()).throw(
            HttpFetchError(f"audit: network disabled ({url})")))

    # One real OS snapshot for every variant's metrics check — the dispatcher's
    # object, personalized exactly as MetricsLoop would publish it.
    snapshot = personalize_metrics(
        app.platform.sensors().snapshot(),
        temp_unit=s.temp_unit, hdd_enabled=s.hdd_enabled,
    )
    live = (snapshot.cpu_percent > 0 or snapshot.cpu_temp > 0)
    log.info("audit: live sensors=%s (cpu %.0f%% %.0f°, gpu %.0f° %.0fMHz)",
             "yes" if live else "NO", snapshot.cpu_percent, snapshot.cpu_temp,
             snapshot.gpu_temp, snapshot.gpu_clock)

    variants = _variant_dicts()
    findings: list[Finding] = []
    for i, d in enumerate(variants):
        f = _summon_and_check(window, d, snapshot)
        findings.append(f)
        # Flushed per-variant so progress + any hang is visible even if the run
        # is killed before the summary prints.
        mark = "OK  " if f.ok else "FAIL"
        print(f"[{i + 1:3}/{len(variants)}] {mark} {f.model:22} {f.key} "
              f"{'; '.join(f.failures)}", flush=True)
    _FINDINGS.extend(findings)

    failed = [f for f in findings if not f.ok]
    print(f"\n=== Variant presentation audit: "
          f"{len(findings) - len(failed)}/{len(findings)} show properly ===\n")
    if failed:
        print(f"  {'MODEL':22} {'VID:PID':11} {'PM/SUB':7} {'WIRE':5} FAILURE")
        for f in failed:
            print(f"  {f.model:22} {f.key:11} "
                  f"{f'{f.pm}/{f.sub}':7} {f.wire:5} {'; '.join(f.failures)}")
    else:
        print("  Every variant connects, gets the right handler, resolves a "
              "canvas, and renders live metrics.")
    if not live:
        print("\n  NOTE: no live CPU reading on this box — metric checks were "
              "vacuously skipped.  Re-run where sensors are available.")

    qapp = QApplication.instance()
    if qapp is not None:
        qapp.quit()


def _catalog_specs() -> list[dict]:
    """One spec per distinct catalog vid:pid — forces a ``DevMockPlatform``
    (which has ``set_active_reply``) regardless of whether a local, gitignored
    ``dev/devices.json`` exists, so the audit is self-contained under CI.  The
    dev rule keeps ``scan_devices() == []``, so this auto-connects nothing —
    each variant is summoned explicitly via its own reply."""
    from _mock_bootstrap import device_catalog

    seen: set[tuple[int, int]] = set()
    specs: list[dict] = []
    for vids, *_rest in device_catalog():
        vid, pid = vids[0]
        if (vid, pid) not in seen:
            seen.add((vid, pid))
            specs.append({"vid": f"{vid:04x}", "pid": f"{pid:04x}"})
    return specs


def main() -> None:
    verbosity = sys.argv.count("-v")
    platform = bootstrap(verbosity=verbosity, all_devices=False,
                         specs=_catalog_specs())

    from trcc.ui.gui import run_gui
    run_gui(cast(Any, platform), decorated=False, single_instance=False,
            ipc=False, force_exit=False, start_hidden=True, on_ready=_run_audit)

    failed = [f for f in _FINDINGS if not f.ok]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

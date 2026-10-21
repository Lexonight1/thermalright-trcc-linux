"""Windows: what one GdiScreenCapture grab costs, and the BGRA->RGB step inside it.

    python gdi_bench.py <src dir> [grabs]

A cast frame is capture + compose + encode + send; on the win11 VM (2026-10-20)
frames started ~142 ms apart, 14 ms of it on the wire and 60 ms the driver's
wait.  This times the capture alone with the real class -- touches nothing of
TRCC's, sends nothing to the panel.
"""
import sys
import time

if sys.platform != "win32":
    raise SystemExit("gdi_bench runs on Windows (the VM)")
sys.path.insert(0, sys.argv[1])
from trcc.adapters.screencast.gdi import GdiScreenCapture

grabs = int(sys.argv[2]) if len(sys.argv) > 2 else 100
capture = GdiScreenCapture()
for size in (320, 640):
    capture.grab_region(0, 0, size, size)                  # bind + warm up
    took = []
    for _ in range(grabs):
        start = time.perf_counter()
        capture.grab_region(0, 0, size, size)
        took.append((time.perf_counter() - start) * 1000)
    took.sort()
    print(f"{size}x{size}: {grabs} grabs, ms p50 {took[len(took) // 2]:.1f} "
          f"p90 {took[int(len(took) * .9)]:.1f} max {took[-1]:.1f}")

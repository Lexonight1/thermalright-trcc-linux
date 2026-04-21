"""Services — pure-Python business logic.

Services depend on ports (Platform, Renderer, SensorEnumerator) via DI
and know nothing about Qt, pyusb, FastAPI, typer, or any adapter.  They
compose into Commands, which UIs dispatch.

One module per responsibility:
    settings.py   — per-device user preferences (persisted JSON)
    theme.py      — theme discovery + parsing
    display.py    — theme + sensors → frame bytes (Phase 5c, needs Renderer)
    overlay.py    — compositing + text (Phase 5c, needs Renderer)
    media.py      — video / animation decode (Phase 5c)
    led.py        — LED state + segment masks (Phase 8, lands with Led device)
    sensors.py    — sensor polling + metric mapping (Phase 5, uses Platform)
    data.py       — theme / mask download + extraction (Phase 12)
"""

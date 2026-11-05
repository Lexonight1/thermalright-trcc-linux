"""hudgui -- the gui, redesigned as a HUD (his direction, 2026-11-05).

A fourth face beside gui, qtgui and the CLI/API: a frameless window whose top
edge is a silver chrome menu bar, the live panel preview as its body, and each
menu item's panel dropping down over it, on glass.  It is a thin layer over
shared code (``ui/qt_*.py``, ``ui/presentation/``) and the Command bus -- it
never reaches into another skin.  Launched by ``trcc hud`` (``ui/_uis.HudUI.launch``).
"""

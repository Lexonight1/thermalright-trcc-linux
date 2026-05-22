"""UI package — driving adapters for the TRCC core.

Three UI flavors, one core:
  trcc.ui.cli   — Typer CLI (terminal interface)
  trcc.ui.api   — FastAPI REST (headless + remote)
  trcc.ui.gui   — PySide6 desktop GUI

All three consume the same `Trcc` command facade (core/trcc.py) so
that every capability is reachable through every interface. Parity
rule: a method reachable from one UI must be reachable from all three.
"""

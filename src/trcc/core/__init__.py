"""Core domain — pure Python, no I/O, no framework deps.

Everything here compiles without pyusb, PySide6, FastAPI, or anything
adapter-ish.  Adapter and UI code depend on this package; this package
depends on nothing in the app.
"""

"""The open-source work TRCC is built on -- the one list every UI thanks.

The gui's control centre shows it under the GPU card, qtgui's About panel
lists it, ``trcc credits`` prints it and ``GET /credits`` serves it.  The
README's "Built on the Work of" says the same at length;
``tests/test_credits.py`` holds every name here to it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Credit:
    """One project: its name, who made it, its licence, and what TRCC
    takes from it."""
    name: str
    author: str
    licence: str
    used_for: str

    @property
    def line(self) -> str:
        """The project in one line -- what a list shows."""
        by = f" by {self.author}" if self.author else ""
        licence = f" ({self.licence})" if self.licence else ""
        log.debug("Credit.line: %s", self.name)
        return f"{self.name}{by}{licence} -- {self.used_for}"


#: A licence only where it is one licence -- each checked against the project
#: (GitHub's licence field, 2026-10-14).  FFmpeg's depends on how it is built,
#: 7-Zip's carries an unRAR clause and Qt's is a choice, so none is stated.
#: In the README's order.  Thermalright's own Windows app is the reference
#: for every device, not open source, so the README thanks it on its own.
CREDITS: tuple[Credit, ...] = (
    Credit("LibreHardwareMonitor", "", "MPL-2.0", "Windows CPU and GPU sensors"),
    Credit("HWiNFO", "", "", "its shared sensors, read from namazso's notes"),
    Credit("iSMC", "dkorunic", "GPL-3.0", "Apple Silicon temperatures"),
    Credit("macmon", "vladkens", "MIT", "how macOS sensors are read"),
    Credit("Stats", "exelban", "MIT", "how macOS sensors are read"),
    Credit("libusb-package", "pyOCD", "Apache-2.0", "USB for pip installs on Windows"),
    Credit("FFmpeg", "", "", "video trimming, decoding and thumbnails"),
    Credit("7-Zip", "Igor Pavlov", "", "unpacking the theme archives"),
    Credit("Qt for Python (PySide6)", "The Qt Company", "",
           "both windows and the frame renderer"),
    Credit("OpenRGB", "Adam Honse and contributors", "GPL-2.0-or-later",
           "OpenRGB devices, Corsair RAM and ASUS Aura lighting"),
    Credit("3D Pipes", "Microsoft", "",
           "how TRCC Pipes is lit and framed, from its NT 4.0 SDK sample"),
    Credit("projectM", "", "LGPL-2.1", "the MilkDrop visual (MilkDrop: Ryan Geiss)"),
    Credit("SoundCard", "Bastian Bechtold", "BSD-3-Clause",
           "hearing what Windows plays"),
    Credit("catap", "Stephen Betko", "MIT", "hearing what macOS plays"),
    Credit("glcontext", "the ModernGL contributors", "MIT",
           "how the visuals open OpenGL on each OS"),
    Credit("Cavalier", "Fyodor Sobolev", "MIT", "the eleven Cavalier visuals"),
    Credit("CAVA", "Karl Stavestrand", "MIT", "the audio engine under them"),
    Credit("KarmaViz", "Jason Swint", "MIT",
           "the KarmaViz visual: its shaders, palettes and presets"),
)

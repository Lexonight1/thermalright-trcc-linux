"""What TRCC does, who inspired it, and the work it is built on -- the one
About every UI shows.

The gui's About panel (beside Control Center in the sidebar) and qtgui's
About panel show it, ``trcc thanks`` prints it and ``GET /system/credits``
serves it.  The
README's "Built on the Work of" says the same at length;
``tests/test_credits.py`` holds every name here to it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .credits_libraries import LIBRARIES

log = logging.getLogger(__name__)


#: What TRCC does -- the About panel's first words.
WHAT_TRCC_DOES = ("TRCC Linux finds your Thermalright devices and makes pictures "
                  "appear on their screens, or lights the LED segments on them.")

#: Over the list: we don't thank them, we love them.
WE_LOVE = "We love you, open-source community!"

@dataclass(frozen=True, slots=True)
class Inspiration:
    """Someone TRCC is inspired by: what they did, and their picture -- with
    the picture's own author, licence and page (Wikimedia Commons)."""
    name: str
    #: Three sentences: what they did, and what it means to TRCC.
    did: str
    picture: str                 # assets/inspired/<picture>
    picture_by: str
    picture_licence: str
    picture_page: str


_COMMONS = "https://commons.wikimedia.org/wiki/File:"

#: Who TRCC is inspired by, in the maintainer's order: the Creator first.
INSPIRATIONS: tuple[Inspiration, ...] = (
    Inspiration("God", "The Creator. In the beginning He created the heavens and the earth. "
                "Everything we make is made from what He made first.", "god.png",
                "Michelangelo, The Creation of Adam (cropped)", "Public domain",
                _COMMONS + "Michelangelo_-_Creation_of_Adam_(cropped).jpg"),
    Inspiration("Jesus", "Son of the Creator. He taught us to love one another. We try to "
                "build the same way: freely, and for everyone.", "jesus.png",
                "Deesis mosaic, Hagia Sophia; photograph by Myrabella (cropped)",
                "Public domain",
                _COMMONS + "Christ_Pantocrator_Deesis_mosaic_Hagia_Sophia.jpg"),
    Inspiration("Charles Babbage",
                "Designed the first real calculator, the Difference Engine. Then he "
                "designed the Analytical Engine, a general-purpose computer, a century "
                "before one was built. Every machine TRCC runs on descends from his idea.",
                "charles_babbage.png", "Unknown author, 1860 (cropped)", "Public domain",
                _COMMONS + "Charles_Babbage_-_1860.jpg"),
    Inspiration("Ada Lovelace",
                "Wrote the first published computer program, for Babbage's Analytical "
                "Engine. She saw that a computer could work with more than numbers: "
                "music, pictures, anything written in symbols. TRCC putting pictures on "
                "a screen is her idea at work.",
                "ada_lovelace.png", "Alfred Edward Chalon (cropped)", "Public domain",
                _COMMONS + "Ada_Lovelace_portrait.jpg"),
    Inspiration("Andrew S. Tanenbaum",
                "Wrote the books and MINIX that taught a generation operating systems. "
                "Linus learned on MINIX and on Tanenbaum's book. His teaching is in every "
                "operating system since.",
                "andrew_s_tanenbaum.png", "Jantangring (cropped)", "CC BY-SA 4.0",
                _COMMONS + "Andrew_S._Tanenbaum_2012.jpg"),
    Inspiration("Richard Stallman",
                "Built GNU, the programs a kernel can't do without, and wrote the GPL. "
                "He started the free software movement in 1983. TRCC is free software "
                "under his GPL.",
                "richard_stallman.png", "Thesupermat (cropped)", "CC BY-SA 3.0",
                _COMMONS + "Richard_Stallman_-_F%C3%AAte_de_l%27Humanit%C3%A9_2014_-_010.jpg"),
    Inspiration("Linus Torvalds", "Created Linux in 1991, and Git in 2005. Linux runs "
                "the machines TRCC was born on. Git holds every line of TRCC's history.",
                "linus_torvalds.png",
                "Krd (cropped)", "CC BY-SA 4.0",
                _COMMONS + "LinuxCon_Europe_Linus_Torvalds_03.jpg"),
)


def inspired_line() -> str:
    """"Inspired by A, B ... and Z." -- the About's first words, in every UI."""
    names = [i.name for i in INSPIRATIONS]
    log.debug("inspired_line: %d", len(names))
    return f"Inspired by {', '.join(names[:-1])} and {names[-1]}."


#: Licence names packages state as classifiers, to their SPDX ids.
_SPDX = {"MIT License": "MIT", "Apache Software License": "Apache-2.0",
         "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0", "ISC License (ISCL)": "ISC",
         "The Unlicense (Unlicense)": "Unlicense", "Public domain": "",
         "Python Software Foundation License": "PSF-2.0"}


@dataclass(frozen=True, slots=True)
class Credit:
    """One work TRCC uses: who made it, its licence -- and, for the work TRCC
    is built from, what it gave."""
    name: str
    author: str
    licence: str
    #: What it gives TRCC -- for the work TRCC is built from; a library's
    #: credit is pure attribution and leaves it empty.
    used_for: str = ""

    @property
    def licence_url(self) -> str:
        """The licence's text at spdx.org -- "" where the licence is not one
        standard licence (a bare "BSD License" does not say which BSD)."""
        if cc := re.fullmatch(r"CC (BY(?:-SA)?) (\d\.\d)", self.licence):
            url = (f"https://creativecommons.org/licenses/{cc.group(1).lower()}/"
                   f"{cc.group(2)}/")
            log.debug("Credit.licence_url: %s -> %s", self.licence, url)
            return url
        first = re.split(r" OR | AND |, | / | \(| with ", self.licence)[0].strip()
        spdx = _SPDX.get(first, first)
        url = (f"https://spdx.org/licenses/{spdx}.html"
               if re.fullmatch(r"(MIT|BSD-[23]-Clause|Apache-2\.0|L?GPL-[23]\.[01]"
                               r"(-only|-or-later)?|MPL-2\.0|ISC|PSF-2\.0|Unlicense|"
                               r"0BSD|MIT-0|Zlib|BSL-1\.0)", spdx) else "")
        log.debug("Credit.licence_url: %s -> %s", self.licence, url or "none")
        return url

    @property
    def line(self) -> str:
        """The work in one line -- what a list shows."""
        by = f" by {self.author}" if self.author else ""
        licence = f" ({self.licence})" if self.licence else ""
        used = f" -- {self.used_for}" if self.used_for else ""
        link = f"  {self.licence_url}" if self.licence_url else ""
        log.debug("Credit.line: %s", self.name)
        return f"{self.name}{by}{licence}{used}{link}"


#: Every author and licence checked against the project's own licence file,
#: source headers or site (2026-10-14).  FFmpeg's is the GPL-3.0 of the builds
#: TRCC ships (gyan.dev, Homebrew), not upstream's LGPL default.
#: In the README's order.  Thermalright's own Windows app is the reference
#: for every device, not open source, so the README thanks it on its own.
BUILT_FROM: tuple[Credit, ...] = (
    Credit("LibreHardwareMonitor",
           "LibreHardwareMonitor and Contributors; Michael M\u00f6ller (Open Hardware Monitor)",
           "MPL-2.0", "Windows CPU and GPU sensors"),
    Credit("HWiNFO", "namazso (the shared-memory notes)", "",
           "its shared sensors, read from namazso's notes"),
    Credit("iSMC", "Dinko Korunic", "GPL-3.0-only", "Apple Silicon temperatures"),
    Credit("macmon", "vladkens", "MIT", "how macOS sensors are read"),
    Credit("Stats", "Serhiy Mytrovtsiy", "MIT", "how macOS sensors are read"),
    Credit("libusb-package", "Chris Reed (pyOCD)", "Apache-2.0",
           "USB for pip installs on Windows"),
    Credit("FFmpeg", "the FFmpeg developers", "GPL-3.0 (the builds TRCC ships)",
           "video trimming, decoding and thumbnails"),
    Credit("7-Zip", "Igor Pavlov", "LGPL-2.1, with the unRAR restriction",
           "unpacking the theme archives"),
    Credit("Qt for Python (PySide6)", "The Qt Company", "LGPL-3.0",
           "both windows and the frame renderer"),
    Credit("OpenRGB", "Adam Honse, OpenRGB Team", "GPL-2.0-or-later",
           "OpenRGB devices, Corsair RAM and ASUS Aura lighting"),
    Credit("3D Pipes", "Microsoft", "",
           "how TRCC Pipes is lit and framed, from its NT 4.0 SDK sample"),
    Credit("projectM", "projectM Team", "LGPL-2.1-or-later",
           "the MilkDrop visual (MilkDrop: Ryan Geiss)"),
    Credit("SoundCard", "Bastian Bechtold", "BSD-3-Clause", "hearing what Windows plays"),
    Credit("catap", "Sage Betko", "MIT", "hearing what macOS plays"),
    Credit("glcontext", "the ModernGL contributors", "MIT",
           "how the visuals open OpenGL on each OS"),
    Credit("Cavalier", "Fyodor Sobolev", "MIT", "the eleven Cavalier visuals"),
    Credit("CAVA", "Karl Stavestrand", "MIT", "the audio engine under them"),
    Credit("KarmaViz", "Jason Swint", "MIT",
           "the KarmaViz visual: its shaders, palettes and presets"),
)


#: Python, and every library TRCC runs on (generated: core/credits_libraries).
RUNS_ON: tuple[Credit, ...] = (
    Credit("Python", "Guido van Rossum and the Python Software Foundation", "PSF-2.0"),
    *(Credit(name, author, licence) for name, author, licence in LIBRARIES),
)

#: What the Windows and macOS builds bundle, and the system libraries TRCC
#: calls, beyond what is credited above.
SHIPPED_WITH: tuple[Credit, ...] = (
    Credit("libusb", "Johannes Erdfelt, Daniel Drake, Peter Stuge, Nathan Hjelm, "
           "Pete Batard and contributors", "LGPL-2.1-or-later"),
    Credit("hidapi", "Alan Ott, Signal 11 Software; the libusb/hidapi team",
           "GPL-3.0 / BSD"),
    Credit("Mesa llvmpipe", "Brian Paul and the Mesa contributors", "MIT"),
    Credit("HidSharp", "James F. Bellinger", "Apache-2.0"),
    Credit("Aga.Controls", "Andrey Gliznetsov", "BSD-3-Clause"),
    Credit("PawnIO.Modules", "namazso", "LGPL-2.1"),
    Credit("OxyPlot", "OxyPlot contributors", "MIT"),
    Credit("TaskScheduler", "David Hall", "MIT"),
    Credit("DiskInfoToolkit, RAMSPDToolkit", "Florian K.", "MPL-2.0"),
    Credit("libudev (systemd)", "the systemd contributors", "LGPL-2.1-or-later"),
    Credit("PipeWire", "Wim Taymans and contributors", "MIT"),
    Credit("libglvnd", "NVIDIA Corporation", "MIT"),
)

#: The tools that made TRCC: to read the original, to build it, to check it.
MADE_WITH: tuple[Credit, ...] = (
    Credit("Ghidra", "National Security Agency", "Apache-2.0"),
    Credit("ILSpy", "AlphaSierraPapa for the ILSpy team", "MIT"),
    Credit("dnSpy", "0xd4d and contributors", "GPL-3.0-or-later"),
    Credit("PyInstaller", "PyInstaller Development Team; Giovanni Bajo",
           "GPL-2.0-or-later, with the Bootloader Exception"),
    Credit("Inno Setup", "Jordan Russell and Martijn Laan", "Inno Setup License"),
    Credit("create-dmg", "Andrey Tarantsov, Andrew Janke", "MIT"),
    Credit("Hatch", "Ofek Lev", "MIT"),
    Credit("pytest", "Holger Krekel and others", "MIT"),
    Credit("pytest-qt", "Bruno Oliveira and others", "MIT"),
    Credit("Ruff", "Charles Marsh (Astral)", "MIT"),
    Credit("Pyright", "Microsoft Corporation", "MIT"),
    Credit("Xvfb (X.Org)", "the X.Org Foundation and contributors", "MIT"),
    Credit("Docker", "Docker, Inc.", "Apache-2.0"),
    Credit("Podman", "the Podman contributors", "Apache-2.0"),
)

#: The pictures above: whose they are and under what licence.
PICTURES: tuple[Credit, ...] = tuple(
    Credit(f"Picture of {i.name}", i.picture_by, i.picture_licence) for i in INSPIRATIONS)

#: The licensing, in the order every UI shows it.
GROUPS: tuple[tuple[str, tuple[Credit, ...]], ...] = (
    ("Built from", BUILT_FROM),
    ("Runs on", RUNS_ON),
    ("Shipped with", SHIPPED_WITH),
    ("Made with", MADE_WITH),
    ("Pictures", PICTURES),
)

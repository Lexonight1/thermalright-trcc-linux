"""The artwork libraries one device reads from.

A resolution does not identify an artwork library.  Two coolers can ship the
same panel and different chrome, and the C# picks the directory accordingly:
``1600720u`` / ``1600720l`` / ``1600720`` by the SUB byte crossed with
orientation (``FormCZTV.cs:1290-1353``), and ``zt480480y`` by PM
(``FormCZTV.cs:5746``).  So "which directory" is a question about the DEVICE,
not about its pixel count.

Threading that answer through as an extra argument was tried first and is what
this class exists to undo.  Six call sites had been converted by hand and had
already grown three different ways of reaching the same handshake --
``self._artwork_variants()``, ``_device_variants(app, key)``, and a bare
``getattr(device, "handshake", None)`` -- with four more sites still to go.
That is one fact expressed three times, and the site that eventually forgets it
does not fail: it silently reads the generic library, which looks exactly like
correct behaviour.

So the suffix is resolved ONCE, at the point where a device and its handshake
are both in hand, and handed onward as an object whose ``theme_dir(w, h)``
already knows the answer.  Call sites keep the shape they always had and cannot
forget what they are never asked to remember.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .ports import Paths

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeviceLibraries:
    """One device's view of the shipped + cloud artwork directories.

    Wraps :class:`Paths` rather than replacing it: only the three library
    lookups vary per SKU, and everything else a caller wants (user dirs, log
    file, config) is reached through :attr:`paths` unchanged.

    ``variant`` / ``mask_variant`` are empty for every panel that has no
    artwork of its own, which is nearly all of them -- so the default instance
    resolves exactly what the bare ``Paths`` methods always did.
    """

    paths: Paths
    variant: str = ""
    mask_variant: str = ""

    def theme_dir(self, width: int, height: int) -> Path:
        """Stock themes for this device — its own library, else the generic."""
        log.debug("theme_dir: %dx%d variant=%r", width, height, self.variant)
        return self._resolve(self.paths.theme_dir(width, height), self.variant)

    def cloud_theme_dir(self, width: int, height: int) -> Path:
        """Cloud backgrounds for this device."""
        log.debug("cloud_theme_dir: %dx%d variant=%r",
                  width, height, self.variant)
        return self._resolve(
            self.paths.cloud_theme_dir(width, height), self.variant)

    def cloud_mask_dir(self, width: int, height: int) -> Path:
        """Cloud masks for this device — the one axis PM can also move."""
        log.debug("cloud_mask_dir: %dx%d variant=%r",
                  width, height, self.mask_variant)
        return self._resolve(
            self.paths.cloud_mask_dir(width, height), self.mask_variant)

    def _resolve(self, base: Path, variant: str) -> Path:
        """*base*'s per-SKU sibling if it is on disk, else *base*.

        The suffixed libraries are a separate download.  A panel whose archive
        has not landed -- new SKU, failed fetch, or an install predating those
        archives -- must fall back rather than browse a directory that is not
        there.  Reading an absent directory is the one way this feature could
        be WORSE than not having it, so the fallback lives here, once, instead
        of at each caller.
        """
        if not variant:
            return base
        candidate = base.with_name(base.name + variant)
        if candidate.is_dir():
            log.debug("DeviceLibraries: using %s", candidate.name)
            return candidate
        log.info("DeviceLibraries: %s not on disk — falling back to %s",
                 candidate.name, base.name)
        return base

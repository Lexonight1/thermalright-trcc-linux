"""Ports for the decompile-miner — ABCs at every boundary (hexagonal).

The tool has one job: turn a compiled Thermalright binary into the *data tables*
buried inside it (rotation switches, wire-command byte arrays, resolution
constants), so a human can drop them into the clean app instead of eyeballing a
60k-line decompile and hand-copying a ``switch`` (which is exactly how the
widescreen rotation baseline got transcribed wrong).

Dependencies point inward only: adapters depend on these ports; the extraction
core (``core/``) depends on nothing but the domain models.

    Decompiler  — binary/source → decompiled TEXT   (Ghidra, or a pre-made file)
    TableSink   — extracted tables → an output sink  (JSON, stdout, Python)

Extractors themselves live in ``core`` as pure functions over text; they are the
business logic and touch no I/O.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .core.models import Extraction


class Decompiler(ABC):
    """Turn an input artifact into decompiled source text.

    Template Method: :meth:`decompile` is the public contract; concrete
    adapters implement :meth:`_run`.  The input is a ``str`` path (an ``.exe``,
    a DLL, or an already-decompiled file/dir) so the same miner works whether
    you feed it a raw binary (Ghidra adapter) or a decompile you already have
    (source-file adapter).
    """

    def decompile(self, artifact: str) -> str:
        """Return the decompiled source text for ``artifact``."""
        return self._run(artifact)

    @abstractmethod
    def _run(self, artifact: str) -> str:
        """Adapter-specific decompilation. Returns the source text."""
        raise NotImplementedError


class TableSink(ABC):
    """Emit an :class:`Extraction` (all mined tables) to some destination."""

    @abstractmethod
    def emit(self, extraction: Extraction) -> None:
        """Write the extracted tables out (JSON file, stdout, …)."""
        raise NotImplementedError

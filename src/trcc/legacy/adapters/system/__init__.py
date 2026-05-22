"""System integration adapters — sensors, info, config, and the Platform
factory that picks the right concrete adapter from the environment.

The factory pattern mirrors ``DeviceProtocolFactory`` from the device layer:
one ABC with ``@PlatformFactory.register(...)`` self-registering subclasses,
and a single ``PlatformFactory.current()`` chokepoint that every composition
root (production ``__main__``, ``dev/mock_*``, tests) calls to receive the
right ``Platform`` for the host. Past this point, no ``sys.platform`` branch
exists anywhere in the codebase — polymorphism does the rest.

Open/Closed: new OS support = one new ``PlatformFactory`` subclass with one
decorator. Zero touchpoints elsewhere.
"""
from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from trcc.legacy.core.ports import Platform


class PlatformFactory(ABC):
    """Abstract factory for ``Platform`` — one subclass per supported OS.

    Subclasses self-register via ``@PlatformFactory.register('win32' | ...)``
    so the dispatch table is built by import side-effect. The 4 OS factory
    classes are defined in this module so the registry is fully populated
    before any caller invokes ``current()``.

    Each subclass defers the *Platform* import to ``make()`` — Windows code
    never touches Linux modules and vice versa.
    """

    _registry: ClassVar[dict[str, type[PlatformFactory]]] = {}

    @classmethod
    def register(cls, key: str):
        """Mark a factory subclass as the one to use for ``sys.platform == key``."""
        def deco(sub: type[PlatformFactory]) -> type[PlatformFactory]:
            cls._registry[key] = sub
            return sub
        return deco

    @classmethod
    def current(cls) -> Platform:
        """Build the Platform for the current process.

        Honors ``TRCC_MOCK``:
            ``TRCC_MOCK=1`` — MockPlatform with default device specs
            ``TRCC_MOCK=path/to/devs.json`` — MockPlatform with file-loaded specs

        Otherwise dispatches via the OS registry.
        """
        if mock_spec := os.environ.get('TRCC_MOCK'):
            return _make_mock_platform(mock_spec)
        key = cls._resolve_key()
        factory_cls = cls._registry.get(key) or cls._registry['linux']
        return factory_cls().make()

    @staticmethod
    def _resolve_key() -> str:
        """Map ``sys.platform`` to a registry key (folds all *BSDs to 'bsd')."""
        return 'bsd' if 'bsd' in sys.platform else sys.platform

    @abstractmethod
    def make(self) -> Platform:
        """Return a fresh ``Platform`` instance for this OS."""


@PlatformFactory.register('win32')
class WindowsFactory(PlatformFactory):
    """Build ``WindowsPlatform`` — picked when ``sys.platform == 'win32'``."""

    def make(self) -> Platform:
        from trcc.legacy.adapters.system.windows_platform import WindowsPlatform
        return WindowsPlatform()


@PlatformFactory.register('darwin')
class MacOSFactory(PlatformFactory):
    """Build ``MacOSPlatform`` — picked when ``sys.platform == 'darwin'``."""

    def make(self) -> Platform:
        from trcc.legacy.adapters.system.macos_platform import MacOSPlatform
        return MacOSPlatform()


@PlatformFactory.register('linux')
class LinuxFactory(PlatformFactory):
    """Build ``LinuxPlatform`` — picked when ``sys.platform == 'linux'`` (default)."""

    def make(self) -> Platform:
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        return LinuxPlatform()


@PlatformFactory.register('bsd')
class BSDFactory(PlatformFactory):
    """Build ``BSDPlatform`` — picked for FreeBSD / OpenBSD / NetBSD."""

    def make(self) -> Platform:
        from trcc.legacy.adapters.system.bsd_platform import BSDPlatform
        return BSDPlatform()


# ── TRCC_MOCK support — separate from the OS factory branch ─────────────

def _make_mock_platform(spec: str) -> Platform:
    """Build ``MockPlatform`` from a ``TRCC_MOCK`` spec ('1' or JSON path)."""
    import json
    _ensure_repo_root_on_path()
    from tests.mock_platform import (  # type: ignore[import-not-found]
        DEFAULT_DEVICES,
        MockPlatform,
    )
    if spec.strip() in ('1', 'true', 'yes'):
        return MockPlatform(list(DEFAULT_DEVICES))
    specs_path = Path(spec)
    if not specs_path.is_file():
        raise RuntimeError(
            f"TRCC_MOCK={spec!r} is neither '1' nor a readable specs file"
        )
    return MockPlatform(json.loads(specs_path.read_text()))


def _ensure_repo_root_on_path() -> None:
    """Add repo root to ``sys.path`` so ``tests.mock_platform`` imports as a package.

    Critical: ``isinstance(p, MockPlatform)`` checks across pytest, dev
    scripts, and ``python -m trcc`` must all resolve to the SAME module
    object — fragile if ``mock_platform`` is loaded twice via different
    import paths.
    """
    here = Path(__file__).resolve()
    # Walk up: adapters/system → adapters → trcc → src → repo root (has tests/)
    for parent in here.parents:
        if (parent / 'tests' / 'mock_platform.py').is_file():
            root = str(parent)
            if root not in sys.path:
                sys.path.insert(0, root)
            return


__all__ = ['PlatformFactory']

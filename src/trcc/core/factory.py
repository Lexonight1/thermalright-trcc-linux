"""One keyed registry, reused by every factory in the app.

``PlatformFactory`` and ``DeviceFactory`` were the same class written twice:
a ``ClassVar`` dict, a ``register(key)`` decorator that closes over it, and a
lookup.  The *only* real difference was what happens when a key is missing —
Device raises, Platform falls back to Linux with a warning.

So that difference is the parameter, and everything else is written once here.
Adding a factory becomes a declaration:

    PLATFORMS = Registry("platform", on_missing=FallBackTo("linux"))
    DEVICES   = Registry("wire",     on_missing=Reject(DeviceNotFoundError))

**Core-safe by construction.**  This module names no concrete adapter and
imports nothing below it — it is generic in the key and the value, so the
adapter layer stays the only place that knows ``LinuxPlatform`` or ``ScsiLcd``
(see CLAUDE.md, "Two-Factory Chain").

A miss is a *ternary*, not a boolean, and each of the three is a deliberate
choice rather than an accident of whoever wrote the lookup:

===============  =========================================================
``Reject``       raise — an unregistered key is a bug (unknown wire)
``FallBackTo``   substitute another key + WARN — degrade, don't crash
``find()``       return ``None`` — absence is a normal answer (unknown VID/PID)
===============  =========================================================
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping
from typing import Generic, TypeVar

log = logging.getLogger(__name__)

K = TypeVar("K")
V = TypeVar("V")


class MissPolicy(ABC, Generic[K, V]):
    """What a registry does when a key is not registered."""

    @abstractmethod
    def resolve(self, name: str, key: K, table: Mapping[K, V]) -> V:
        """Return a value for the missing *key*, or raise."""


class Reject(MissPolicy[K, V]):
    """Raise: the key should have been registered, so a miss is a defect.

    ``exc`` is the exception *type* to raise; it receives one message
    argument.  Kept injectable so each factory keeps the error its callers
    already catch, rather than this module inventing a new one.
    """

    def __init__(self, exc: type[Exception]) -> None:
        self._exc = exc

    def resolve(self, name: str, key: K, table: Mapping[K, V]) -> V:
        log.warning("%s: no entry for %r (have: %s)", name, key,
                    sorted(map(str, table)))
        raise self._exc(
            f"No {name} registered for {key!r} "
            f"(registered: {sorted(map(str, table))})"
        )


class FallBackTo(MissPolicy[K, V]):
    """Substitute another key and WARN — degrade rather than crash.

    Used where an unknown key is survivable: a debug session on a niche OS
    should reach a usable app rather than die at the composition root.  The
    warning is mandatory, because a silent substitution is how "it ran, but
    on the wrong platform" becomes an hour of confusion.
    """

    def __init__(self, key: K) -> None:
        self._key = key

    def resolve(self, name: str, key: K, table: Mapping[K, V]) -> V:
        log.warning("%s: %r not registered — falling back to %r",
                    name, key, self._key)
        try:
            return table[self._key]
        except KeyError:
            raise KeyError(
                f"{name}: fallback {self._key!r} is itself unregistered "
                f"(registered: {sorted(map(str, table))})"
            ) from None


class Registry(Generic[K, V]):
    """A ``key → value`` table with self-registration and one miss policy.

    Supports ``in``, ``len()`` and iteration so the data describes itself —
    callers ask the registry what it holds instead of reaching for a private
    dict.
    """

    def __init__(self, name: str, *, on_missing: MissPolicy[K, V]) -> None:
        self._name = name
        self._on_missing = on_missing
        self._table: dict[K, V] = {}

    def register(self, key: K) -> Callable[[V], V]:
        """Decorator: register the decorated value under *key*.

        Returns the value unchanged, so it decorates a class without
        altering it.
        """
        def deco(value: V) -> V:
            if key in self._table:
                log.warning("%s: %r re-registered, replacing %s",
                            self._name, key, self._table[key])
            self._table[key] = value
            log.debug("%s: registered %s for %r", self._name,
                      getattr(value, "__name__", value), key)
            return value
        return deco

    def __getitem__(self, key: K) -> V:
        """``registry[key]`` — the lookup, applying the miss policy if absent.

        A registry *is* a mapping, so it reads as one.  This is the primary
        accessor; :meth:`get` is kept as a named alias for call sites where
        the verb reads better than the subscript.
        """
        try:
            return self._table[key]
        except KeyError:
            return self._on_missing.resolve(self._name, key, self._table)

    def get(self, key: K) -> V:
        """Alias for ``registry[key]``."""
        return self[key]

    def find(self, key: K) -> V | None:
        """Return the value for *key*, or ``None`` — the miss policy is skipped.

        For callers where absence is a normal answer rather than a fault.
        """
        return self._table.get(key)

    def keys(self) -> list[K]:
        """Every registered key — for diagnostics and error messages."""
        return list(self._table)

    def __contains__(self, key: object) -> bool:
        return key in self._table

    def __len__(self) -> int:
        return len(self._table)

    def __iter__(self) -> Iterator[K]:
        return iter(self._table)

    def __repr__(self) -> str:
        return f"<Registry {self._name}: {len(self._table)} entries>"

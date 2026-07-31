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
``.get()``       return ``None`` — absence is a normal answer (unknown VID/PID)
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
        log.debug("Reject: misses will raise %s", exc.__name__)
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
        log.debug("FallBackTo: misses will substitute %r", key)
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


class Registry(Mapping[K, V]):
    """A ``key → value`` table with self-registration and one miss policy.

    **It is a ``Mapping``**, so it behaves like one: ``in``, ``len()``,
    iteration, ``keys()`` / ``values()`` / ``items()`` all come from the stdlib
    ABC rather than being hand-written here.  The data describes itself, and
    callers never reach for a private dict.
    """

    def __init__(self, name: str, *, on_missing: MissPolicy[K, V]) -> None:
        log.debug("Registry %r created (on_missing=%s)",
                  name, type(on_missing).__name__)
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

    # ── Mapping protocol ──────────────────────────────────────────────
    # Subclassing ``Mapping`` means these three are the ONLY accessors we
    # write; ``keys`` / ``values`` / ``items`` / ``__contains__`` / ``__eq__``
    # come from the stdlib ABC, correct and view-based, for free.

    def __getitem__(self, key: K) -> V:
        """``registry[key]`` — the lookup, applying the miss policy if absent.

        Subscript is where the policy lives, exactly as ``dict[k]`` is where
        ``KeyError`` lives: the caller who subscripts is asserting the key is
        there, so a miss is theirs to hear about.
        """
        try:
            value = self._table[key]
        except KeyError:
            return self._on_missing.resolve(self._name, key, self._table)
        log.debug("%s[%r] -> %s", self._name, key,
                  getattr(value, "__name__", value))
        return value

    def __iter__(self) -> Iterator[K]:
        return iter(self._table)

    def __len__(self) -> int:
        return len(self._table)

    def get(self, key: K, default: V | None = None) -> V | None:
        """``registry.get(key)`` — **never raises**, the miss policy is skipped.

        Overridden rather than inherited because ``Mapping.get`` catches only
        ``KeyError``, and ``Reject`` raises a domain error (``TrccError``) that
        would sail straight through it — a class advertising the Mapping
        protocol while violating it.

        The split is Python's own, kept honest: ``registry[key]`` is the
        assertion, ``registry.get(key)`` is the question.
        """
        value = self._table.get(key, default)
        log.debug("%s.get(%r) -> %s", self._name, key,
                  getattr(value, "__name__", value))
        return value

    def __repr__(self) -> str:
        return f"<Registry {self._name}: {len(self._table)} entries>"

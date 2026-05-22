"""@command decorator — collapses facade-method bookkeeping.

A facade method (LCDCommands.set_brightness, LEDCommands.set_color, …)
historically did four things by hand:

    1. Bounds-check the device index, return error if out of range
    2. Call the device's underlying method
    3. Wrap the device's dict result in a typed dataclass
    4. Publish a topic on success

That's 8-10 lines per method × 50+ methods = ~400 lines of bookkeeping
in `lcd_commands.py` + `led_commands.py`. This decorator collapses
items 3 and 4. The body does the call; the decorator wraps the result
and publishes the topic.

Body shape after::

    @command(result_cls=FrameResult, topic=Topic.LCD_BRIGHTNESS,
             include_frame=True)
    def set_brightness(self, lcd: int, percent: int) -> dict:
        if (dev := self._get(lcd)) is None:
            return {'success': False, 'error': f'LCD {lcd} not found'}
        return dev.set_brightness(percent)

Callers see ``set_brightness(lcd, percent) -> FrameResult``; bodies
return the raw device dict. Lean.
"""
from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

if TYPE_CHECKING:
    from .results import OpResult

log = logging.getLogger(__name__)

P = ParamSpec('P')
R = TypeVar('R', bound='OpResult')


def command(
    *,
    result_cls: type[R],
    topic: str | None = None,
    include_frame: bool = False,
    publish_args: tuple[int, ...] | None = None,
    publish_kwargs: tuple[str, ...] = (),
    extras_rename: dict[str, str] | None = None,
) -> Callable[[Callable[P, Any]], Callable[P, R]]:
    """Wrap a facade method.

    Args:
        result_cls: Dataclass to wrap the device dict in (`OpResult`,
            `FrameResult`, `ThemeResult`, `LEDResult`, …).
        topic: EventBus topic to publish on success. ``None`` = no publish.
        include_frame: When True, copy the device result's ``image`` into
            the result dataclass's ``frame`` field via ``Frame(native=…)``.
        publish_args: Positional arg indices (post-self) to pass as the
            payload to ``events.publish(topic, *payload)``. Default
            ``None`` → all positional args.
        publish_kwargs: Keyword arg names appended to the publish payload
            after ``publish_args``. Used by LED methods that publish a
            ``zone`` kwarg as the trailing payload element.
        extras_rename: Dict mapping device-result-dict keys to
            result-dataclass field names. Used by LED methods where the
            device returns ``colors`` but ``LEDResult.display_colors`` is
            the dataclass field.

    Decorated method body should return the device's raw result dict.
    """
    rename = extras_rename or {}

    def decorator(method: Callable) -> Callable:
        @functools.wraps(method)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> OpResult:
            try:
                r = method(self, *args, **kwargs)
            except Exception:
                # Defensive: a facade body raising is a contract bug, not
                # an expected failure. Log + re-raise so the caller gets a
                # real traceback instead of a silent broken result.
                log.exception(
                    "@command(%s) body raised — facade contract violation",
                    method.__qualname__,
                )
                raise
            if not isinstance(r, dict):
                # Body returned a result dataclass already (e.g., early-exit
                # FrameResult on bounds-check failure) — pass through.
                log.debug(
                    "@command(%s) early-exit: returning %s directly",
                    method.__qualname__, type(r).__name__,
                )
                return r
            success = r.get('success', False)
            log.debug(
                "@command(%s) → success=%s%s",
                method.__qualname__, success,
                f" topic={topic!r}" if (success and topic) else "",
            )
            out: dict[str, Any] = {
                'success': success,
                'message': r.get('message', ''),
                'error': r.get('error'),
            }
            if include_frame:
                from .results import Frame
                img = r.get('image')
                out['frame'] = Frame(native=img) if img is not None else None
            # Subclass-specific extras travel through if the dict has them
            # and the result_cls accepts them. Cheap to attempt. Renames
            # apply for fields whose dict key differs from dataclass field.
            for k in ('is_animated', 'interval_ms', 'overlay_config',
                      'overlay_enabled', 'colors', 'display_colors'):
                if k in r:
                    out[rename.get(k, k)] = r[k]
            if success and topic:
                payload = list(
                    args if publish_args is None
                    else tuple(args[i] for i in publish_args)
                )
                payload.extend(kwargs.get(kw) for kw in publish_kwargs)
                self._events.publish(topic, *payload)
            try:
                return result_cls(**out)
            except TypeError as e:
                # result_cls doesn't accept one of the extras — strip them
                # and try the minimal constructor. Log so the field-rename
                # mismatch is visible during development; production keeps
                # working via fallback.
                log.warning(
                    "@command(%s): %s rejected extras (%s) — falling back "
                    "to minimal constructor: %s",
                    method.__qualname__, result_cls.__name__,
                    sorted(set(out) - {'success', 'message', 'error'}), e,
                )
                return result_cls(
                    success=success,
                    message=r.get('message', ''),
                    error=r.get('error'),
                )
        return wrapper
    return decorator

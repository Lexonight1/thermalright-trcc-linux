"""Where per-frame log lines live, so they can be silenced as one group.

THE RULE gives every function a log line, and the file keeps DEBUG at every
verbosity, because ``trcc report`` is the entire diagnosis for hardware we do
not own.  Both of those are worth keeping.  What is not worth keeping is
paying for them once per rendered frame.

Measured on the real device ``0402:3922`` (320x320 video theme), in
instructions retired per rendered frame — CPU%% cannot answer this on a
frequency-scaling box, where the same build reads 2.1 or 4.7 GHz depending on
load and can look 21%% *faster* while doing 58%% more work:

    v9.9.2 (19 Jul)          41.4 M / frame
    HEAD                     64.9 M / frame      +57%
    HEAD, logging off        43.8 M / frame

Logging was **82-90%% of the whole regression**, at 44 DEBUG records per frame
and 688 records/second.  But the cost and the value sit in different places:

* **36 emitters fire >100 times per run — 92%% of the records.**  All of them
  are per-frame: the frame composite, the wire write, the settings lookup the
  render path makes every tick.
* **109 emitters fire <=8 times — 0.6%% of the records.**  These are the
  diagnostic gold: which sysfs path the device resolved to, which transport
  opened, which distro family was detected, why a download was skipped, the
  SCSI timeout actually used.  They cost nothing, and they are what a reporter
  on macOS or BSD — where we cannot reproduce — sends us.

So the split is by **frequency, not by severity**.  Silencing DEBUG wholesale
would throw away the 0.6%% to save the 92%%, and re-break exactly what the
always-DEBUG rule was written to fix.  Instead, per-frame lines log to a
logger under one shared parent, and the parent's level is set once at
configuration: INFO normally (so ``.debug()`` short-circuits in
``isEnabledFor`` and the record is never even constructed — which is where the
saving comes from, not in the writing), DEBUG under ``-vvv``.

That rung moved from ``-v`` to ``-vvv`` when the verbosity ladder was made the
rule: the firehose is *deep internals*, not the *granular state* DEBUG means.
See :func:`levels_for`, which is the one definition.

Using it, in a module whose lines fire per frame::

    from ..core.logs import per_frame

    log = logging.getLogger(__name__)          # one-shot lines, as before
    frame_log = per_frame(__name__)            # per-frame lines

Everything else keeps ``log`` and keeps appearing in every report.  Lives in
core because the name is a domain fact that adapters configure and services
emit through; core imports no adapter to provide it.
"""
from __future__ import annotations

import logging
from typing import NamedTuple

# ── TRACE — the fourth rung of the verbosity ladder ─────────────────────────
#
# The ladder, and the ONLY definition of it:
#
#   (no flag)  terminal WARNING   silent unless something needs attention
#   -v         terminal INFO      major milestones
#   -vv        terminal DEBUG     granular state, variable values, loop steps
#   -vvv       terminal TRACE     deep internals: raw payloads, thread hooks
#
# The FILE is not on this ladder.  It keeps DEBUG at every rung, because
# ``trcc report`` is the whole diagnosis for hardware we do not own — a level
# that rises with a flag means a reporter who did not know the flag sends a log
# with the evidence already discarded.  That was the bug the always-DEBUG rule
# was written to fix; the ladder governs the TERMINAL.  ``-vvv`` is the one rung
# that also lowers the file, because TRACE sits *below* DEBUG and would
# otherwise be unreachable in the artifact we read.
#
# stdlib has no TRACE, so it is registered here once.
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def trace(logger: logging.Logger, msg: str, *args: object) -> None:
    """Log at TRACE — deep internals, only ever seen under ``-vvv``."""
    if logger.isEnabledFor(TRACE):
        logger.log(TRACE, msg, *args)


class Verbosity(NamedTuple):
    """What one ``-v`` count means, everywhere."""

    terminal: int
    file: int
    per_frame: bool


def levels_for(verbosity: int) -> Verbosity:
    """The ladder, resolved.  The ONE place ``-v`` counts become levels.

    It is a function rather than a chain inside the CLI callback because a rule
    nothing can call is a rule nothing can gate: the mapping lived in
    ``ui/cli/main._root`` and no test asserted it, so ``per_frame=verbose > 0``
    could have read ``>= 99`` and the suite would have stayed green.

    The file floor is DEBUG at every rung — ``trcc report`` is the whole
    diagnosis for hardware we do not own, and a file level that rises with a
    flag means a reporter who did not know the flag sends a log with the
    evidence already discarded.  ``-vvv`` is the sole rung that lowers it,
    because TRACE sits BELOW debug and would otherwise never reach the artifact.
    """
    terminal = (
        TRACE if verbosity >= 3
        else logging.DEBUG if verbosity >= 2
        else logging.INFO if verbosity == 1
        else logging.WARNING
    )
    resolved = Verbosity(
        terminal=terminal,
        file=min(logging.DEBUG, terminal),
        # The firehose: 92% of records and ~90% of the CPU regression since
        # v9.9.2.  Under this ladder that is TRACE — deep internals — not the
        # "granular state" DEBUG describes.
        per_frame=verbosity >= 3,
    )
    logging.getLogger(__name__).debug(
        "levels_for: -v x%d -> terminal=%s file=%s per_frame=%s",
        verbosity, logging.getLevelName(resolved.terminal),
        logging.getLevelName(resolved.file), resolved.per_frame,
    )
    return resolved

log = logging.getLogger(__name__)

#: Parent of every per-frame logger.  Levels propagate to children that set
#: none of their own, so ``configure_logging`` silences the whole family with
#: a single ``setLevel`` and does not need a registry of who belongs to it.
PER_FRAME_ROOT = "trcc.frame"


def per_frame(module_name: str) -> logging.Logger:
    """The per-frame logger for *module_name* (pass ``__name__``).

    The returned logger is a child of :data:`PER_FRAME_ROOT`, so it is
    silenced or enabled with every other per-frame logger at once.  The
    module's own path is preserved in the logger name, so a line still
    reports where it came from — ``trcc.frame.services.display``.
    """
    name = f"{PER_FRAME_ROOT}.{module_name.removeprefix('trcc.')}"
    log.debug("per_frame: %s → %s", module_name, name)
    return logging.getLogger(name)

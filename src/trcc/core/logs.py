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
saving comes from, not in the writing), DEBUG under ``-v``.

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

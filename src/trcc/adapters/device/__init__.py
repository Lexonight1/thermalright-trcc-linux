"""Device adapters — transport + concrete Device subclasses, keyed by wire.

The registry IS the OCP chokepoint for device support: a new wire protocol adds
one class under ``adapters/device/`` that names its wire in its own class line::

    class ScsiLcd(BaseDevice[ScsiTransport], wire=Wire.SCSI): ...

``BaseDevice.__init_subclass__`` registers it — there is no decorator to forget
and no separate registration line to drift from the class.  Importing this
package fires the side-effect imports of every device module, which is what
defines those classes, which is what populates :data:`DEVICES`.

Look a wire up by subscripting, because a registry is a mapping::

    DEVICES[info.wire]            # -> the Device subclass, or DeviceNotFoundError

``core/ports.Device`` stays a pure ABC; the ``Wire → class`` table lives here in
the adapter layer — not in ``app.py`` (where it was an ad-hoc dict), not in core
(which must never name a concrete adapter class).

Note: the cutover unified legacy's separate Protocol + Device layers into one
``Device`` ABC, so this is the only device-construction registry — there is no
separate ProtocolFactory.
"""

from __future__ import annotations

import logging

from ._base import DEVICES

log = logging.getLogger(__name__)

# Side-effect imports: load each device module so defining its class registers
# it.  Anything that imports ``trcc.adapters.device`` triggers them all.
from . import ali_lcd as _ali_lcd  # noqa: E402, F401
from . import bulk_lcd as _bulk_lcd  # noqa: E402, F401
from . import hid_lcd as _hid_lcd  # noqa: E402, F401
from . import led as _led  # noqa: E402, F401
from . import ly_lcd as _ly_lcd  # noqa: E402, F401
from . import scsi_lcd as _scsi_lcd  # noqa: E402, F401

__all__ = ["DEVICES"]

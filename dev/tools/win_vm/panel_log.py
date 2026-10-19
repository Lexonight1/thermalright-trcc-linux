"""Read a SCSI panel's poll buffer -- the firmware's own log text, when it holds one.

    python panel_log.py [VID PID]      # hex; default 0402 3922

Sends ONE poll -- ``cmd 0xF5``, 57,600 bytes data-in, exactly what the vendor's
USBLCD.exe and TRCC's handshake send on every start -- and nothing else: no
init, no frame.  Through TRCC's service when not elevated, directly when it is
(``WindowsPlatform._open_scsi`` picks).  The panel takes one owner: stop the
App first (``trcc kill``).

Found 2026-10-19 on the win11 VM: with the panel showing black while it ACKed
every frame, the poll reply carried ASCII after the FBL byte --
``W/USBDDRV [01-01 07:44:41.167 t:06,22] (usbd_rea...`` -- an Android-style log
line from the firmware's USB driver.  The App logs only the first 16 bytes.

Writes the raw reply to C:\\trccdev\\panel_poll.bin and prints its readable runs.
"""
import re
import sys
from pathlib import Path

from trcc.adapters.device.scsi_lcd import _POLL_CMD, _POLL_SIZE, ScsiLcd
from trcc.adapters.system.windows import WindowsPlatform

vid, pid = (int(v, 16) for v in (sys.argv[1:3] or ["0402", "3922"]))
transport = WindowsPlatform()._open_scsi(vid, pid)
transport.open()
try:
    reply = transport.read_cdb(ScsiLcd._build_cdb(_POLL_CMD, _POLL_SIZE),
                               _POLL_SIZE, 10000)
finally:
    transport.close()
Path(r"C:\trccdev\panel_poll.bin").write_bytes(reply)
print(f"{vid:04x}:{pid:04x} poll reply: {len(reply)} bytes, first 16: {reply[:16].hex(' ')}")
nonzero = sum(1 for b in reply if b)
print(f"non-zero bytes: {nonzero}")
for run in re.findall(rb"[\t\n\r -~]{6,}", reply):
    print(run.decode("ascii").rstrip())

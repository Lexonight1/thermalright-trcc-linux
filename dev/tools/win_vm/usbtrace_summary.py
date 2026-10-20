"""Count what one USB trace of the panel shows: frames, Windows' media checks, stalls, resets.

    python3 dev/tools/win_vm/usbtrace_summary.py ~/.cache/trcc-win-vm/inbox/usbreset.csv [...]

Reads the CSV ``tracerpt`` writes from the usbtrace_start/stop recipes (USBXHCI,
UCX, USBHUB3, StorPort) on the host, where the 450 MB file fits.  Each command
to a bulk-only panel opens with a 31-byte command block; what the next transfer
on the same USB slot asks for names the command:

* **frame** -- TRCC's 204,800-byte write ends in an 8,192-byte transfer
  (3 x 65,536 + 8,192), so one tail is one frame;
* **media check** -- Windows' TEST UNIT READY moves no data, so its status
  arrives in the 512-byte buffer the stack posts (13 bytes come back);
* **sense read** -- the 18-byte REQUEST SENSE after a check the panel failed
  ("no medium");
* **stall** -- UCX completes a transfer with USBD_STATUS_STALL_PID (0xC0000004);
* **port reset** -- USBHUB3 starts IOCTL_INTERNAL_USB_RESET_PORT (0x220007);
* **CHECK CONDITION** -- StorPort completes a request with SRB status 0x84 and
  SCSI status 2, on ANY disk: it does not say whether the request reached a
  wire, so read it beside the USB rows, never alone.

The 2026-10-20 baseline (68.6 min, Theme1): 4068 frames, 4114 checks, 4151 sense
reads, 38 stalls, 1 port reset -- every reset began at a sense read that stalled.
After TRCC's IOCTL_STORAGE_MCN_CONTROL (453f08fb, 11.2 min): 667 frames and
nothing else on the panel's slot -- 0 checks, 0 sense reads, 0 stalls.  CHECK
CONDITIONs stay at ~60/min: System still asks once a second, but the request
fails in 1-5 ms with no USB transfer, so it never reaches the panel.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_CBW = 31
_KIND_AFTER_CBW = {8192: "frame", 512: "media check", 18: "sense read"}
_STALL = "0xC0000004"
_RESET_PORT = "0x220007"
_SRB_ERROR_AUTOSENSE = "0x84"              # SRB_STATUS_ERROR | SRB_STATUS_AUTOSENSE_VALID
_CHECK_CONDITION = "2"


def summarize(csv: Path) -> tuple[Counter[str], float]:
    """The counts in *csv*, and the minutes its transfers span."""
    counts: Counter[str] = Counter()
    pending: dict[str, bool] = {}           # slot -> a command block awaits its next transfer
    first = last = 0
    with csv.open(encoding="utf-8-sig", errors="replace") as rows:
        next(rows)
        for line in rows:
            head = line[:60]
            if not any(p in head for p in ("USBXHCI", "USB-UCX", "USBHUB3", "StorPort")):
                continue
            f = [x.strip() for x in line.split(",")]
            if len(f) < 21:
                continue
            if "USBXHCI" in f[0] and f[2] == "41":        # transfer initialized: slot, ep, _, length
                slot, length = f[19], int(f[22])
                first, last = first or int(f[16]), int(f[16])
                if length == _CBW:
                    pending[slot] = True
                elif pending.pop(slot, False) and (kind := _KIND_AFTER_CBW.get(length)):
                    counts[kind] += 1
                elif length == 8192:
                    counts["frame"] += 1
            elif "USB-UCX" in f[0] and f[2] == "27" and len(f) > 26 and f[26] == _STALL:
                counts["stall"] += 1
            elif "USBHUB3" in f[0] and f[2] == "90" and f[20] == _RESET_PORT:
                counts["port reset"] += 1
            elif ("StorPort" in f[0] and f[2] == "201" and len(f) > 25
                  and f[22] == _SRB_ERROR_AUTOSENSE and f[25] == _CHECK_CONDITION):
                counts["CHECK CONDITION"] += 1
    return counts, (last - first) / 1e7 / 60


def main(paths: list[str]) -> None:
    for path in paths:
        counts, minutes = summarize(Path(path))
        print(f"{path}: {minutes:.1f} min")
        for kind in ("frame", "media check", "sense read", "stall", "port reset",
                     "CHECK CONDITION"):
            n = counts[kind]
            print(f"  {kind:15} {n:6}  ({n / minutes if minutes else 0:6.2f}/min)")


if __name__ == "__main__":
    main(sys.argv[1:])

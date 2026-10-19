"""Every USB disk is found by the socket libusb names it -- the twins check (#287).

    python drive_probe.py VID PID [VID PID ...]      # hex

For each model: libusb's name for every unit (``usb_path``, what the scan keys
twins by), then ``find_physical_drive(vid, pid, unit)`` for each.  PASS needs
every unit to find a drive, no two the same, and a socket nothing is in to find
none.  For a TRCC panel it then opens the drive through TRCC's service, by its
unit and by a wrong one.

Twins without two panels -- from the host, live only (gone at VM shutdown);
``chcon`` because SELinux keeps the VM out of the scratchpad:

    qemu-img create -f raw twinA.img 32M; qemu-img create -f raw twinB.img 32M
    chcon -t virt_content_t twinA.img twinB.img
    virsh -c qemu:///session qemu-monitor-command win11 --hmp \\
        "device_add qemu-xhci,id=xhci3,bus=pci.6,p2=15,p3=15"
    ... --hmp "drive_add 0 if=none,id=twinA,file=$PWD/twinA.img,format=raw,readonly=on"
    ... --hmp "device_add usb-storage,bus=usb.0,port=5,drive=twinA,id=dtwinA"
    (the same for twinB on bus=xhci3.0, port=5)

Measured 2026-10-18: libusb 1-5 / 2-5 -> PHYSICALDRIVE2 / 3; the panel 1-18 ->
PHYSICALDRIVE1, ~2 ms per lookup.
"""
import sys
import time

sys.path.insert(0, r"C:\trccdev\tree\src")
from trcc.adapters.device._pyusb_find import find, usb_path
from trcc.adapters.system._service_broker import SCSI_PANELS
from trcc.adapters.system._service_client import ServiceScsiTransport
from trcc.adapters.system._windows_drives import find_physical_drive
from trcc.core.errors import TransportError

ok = True
args = sys.argv[1:]
for vid, pid in zip(args[::2], args[1::2], strict=True):
    vid, pid = int(vid, 16), int(pid, 16)
    units = sorted(usb_path(dev) or "" for dev in find(find_all=True, idVendor=vid,
                                                       idProduct=pid))
    print(f"== {vid:04x}:{pid:04x}  libusb units {units}")
    drives = {}
    for unit in units:
        start = time.perf_counter()
        drives[unit] = find_physical_drive(vid, pid, unit)
        print(f"  {unit} -> {drives[unit]}  ({1000 * (time.perf_counter() - start):.0f} ms)")
    empty = find_physical_drive(vid, pid, "9-9")
    print(f"  9-9 (nothing there) -> {empty}")
    found = [d for d in drives.values() if d]
    ok &= bool(units) and len(found) == len(units) == len(set(found)) and empty is None
    if (vid, pid) in SCSI_PANELS and units:
        for unit in (units[0], "9-" + units[0].split("-", 1)[1]):
            transport = ServiceScsiTransport(vid, pid, unit)
            try:
                opened = transport.open()
                transport.close()
            except TransportError as e:
                opened = False
                print(f"  service, unit {unit}: refused -- {e}")
            else:
                print(f"  service, unit {unit}: opened={opened}")
            ok &= opened == (unit == units[0])
print("PASS" if ok else "FAIL")

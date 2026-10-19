"""Which \\\\.\\PhysicalDriveN is a TRCC panel -- found through SetupAPI, not WMI.

Linux walks from /dev/sgN up sysfs to the USB device and reads its VID:PID
(``linux._walk_sysfs_for_vid_pid``).  This is the same walk on Windows: every
present disk interface, up its device-instance parents to the one whose ID is
``USB\\VID_xxxx&PID_xxxx\\...``, then the disk's own number from
IOCTL_STORAGE_GET_DEVICE_NUMBER.  Opening a disk to ask its number needs no
access rights, so this works for a non-admin process, and nothing here depends
on the WMI service -- whose COM connection failed inside TRCC's service with an
RPC internal error (2026-10-18).

Two identical panels (#287) are told apart by the socket they are in: the scan
names each by libusb's ``usb_path`` (``bus-port.port``), and the walk gives each
disk the same name from what Windows says about its USB device -- the port chain
from its location path, the bus from its root hub.  Measured on the win11 VM with
two identical USB disks on port 5 of two controllers: libusb ``1-5`` / ``2-5``,
the walk ``1-5`` / ``2-5``; the panel ``1-18`` / ``1-18``.
"""
from __future__ import annotations

import ctypes
import logging
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, TypeVar

log = logging.getLogger(__name__)

_T = TypeVar("_T")

_USB_ID = re.compile(r"^USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})(?:\\|&|$)", re.I)
_ROOT_HUB = "USB\\ROOT_HUB"          # ROOT_HUB, ROOT_HUB20, ROOT_HUB30
_PORT = re.compile(r"#USB\((\d+)\)")
#: How far up the device tree to look: disk -> USB device (-> its composite
#: parent) -> up to five hubs, the USB limit -> root hub.  Bounded, so a cycle
#: cannot loop.
_MAX_DEPTH = 10


@dataclass(frozen=True)
class UsbDisk:
    """The USB device a disk hangs off: its ids, and the unit libusb names it
    by (``usb_path``); ``""`` when Windows does not say where it is plugged."""

    vid: int
    pid: int
    unit: str


def usb_ids(instance_id: str) -> tuple[int, int] | None:
    """``(vid, pid)`` of a ``USB\\VID_xxxx&PID_xxxx\\...`` device-instance ID."""
    m = _USB_ID.match(instance_id)
    log.debug("usb_ids: %s -> %s", instance_id, m.groups() if m else None)
    return (int(m.group(1), 16), int(m.group(2), 16)) if m else None


def ancestors(start: _T, parent_of: Callable[[_T], _T | None]) -> Iterator[_T]:
    """*start*, then its parents up to the root -- at most ``_MAX_DEPTH``."""
    log.debug("ancestors: from %s", start)
    node: _T | None = start
    for _ in range(_MAX_DEPTH):
        if node is None:
            return
        yield node
        node = parent_of(node)


def port_chain(location_paths: Iterable[str]) -> str | None:
    """The hub ports below the root hub, as ``usb_path`` joins them:
    ``PCIROOT(0)#PCI(0201)#USBROOT(0)#USB(3)#USB(2)`` -> ``3.2``."""
    for path in location_paths:
        _, root, below = path.partition("#USBROOT(")
        if root and (ports := _PORT.findall(below)):
            log.debug("port_chain: %s -> %s", path, ports)
            return ".".join(ports)
    log.debug("port_chain: no USB port in %s", list(location_paths))
    return None


def usb_disk(chain: Sequence[str], location_paths: Callable[[str], list[str]],
             root_hubs: Sequence[str]) -> UsbDisk | None:
    """What a disk hangs off, from its *chain* (the disk, then its parents).

    The unit is libusb's name for the device: the bus is the root hub's place
    in *root_hubs* plus one -- libusb 1.0.29 and later number buses in the
    order its hub pass meets each root hub (``windows_winusb.c``, HUB_PASS) --
    then the port chain from the USB device's location path.
    """
    device = next((i for i in chain if usb_ids(i)), None)
    if device is None:
        log.debug("usb_disk: no USB device above %s", chain[:1])
        return None
    vid, pid = usb_ids(device) or (0, 0)
    root = next((i for i in chain if i.upper().startswith(_ROOT_HUB)), None)
    ports = port_chain(location_paths(device))
    unit = f"{root_hubs.index(root) + 1}-{ports}" if root in root_hubs and ports else ""
    log.debug("usb_disk: %s under %s -> unit %s", device, root, unit or "(unnamed)")
    return UsbDisk(vid, pid, unit)


# ── The Windows calls (ctypes; Windows-only) ─────────────────────────────

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("InterfaceClassGuid", _GUID),
                ("Flags", ctypes.c_uint32), ("Reserved", ctypes.c_size_t)]


class _SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("ClassGuid", _GUID),
                ("DevInst", ctypes.c_uint32), ("Reserved", ctypes.c_size_t)]


class _STORAGE_DEVICE_NUMBER(ctypes.Structure):
    _fields_ = [("DeviceType", ctypes.c_uint32), ("DeviceNumber", ctypes.c_uint32),
                ("PartitionNumber", ctypes.c_uint32)]


def _guid(text: str) -> _GUID:
    """A ``_GUID`` from its ``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`` text."""
    log.debug("_guid: %s", text)
    h = text.replace("-", "")
    return _GUID(int(h[:8], 16), int(h[8:12], 16), int(h[12:16], 16),
                 (ctypes.c_ubyte * 8)(*bytes.fromhex(h[16:])))


_GUID_DEVINTERFACE_DISK = _guid("53f56307-b6bf-11d0-94f2-00a0c91efb8b")
_GUID_DEVINTERFACE_USB_HUB = _guid("f18a0e88-c30c-11d0-8815-00a0c906bed8")
_DIGCF_PRESENT, _DIGCF_DEVICEINTERFACE = 0x2, 0x10
_IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D1080
_CM_DRP_LOCATION_PATHS = 0x24
_INVALID_HANDLE = ctypes.c_void_p(-1).value
#: SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize: the DWORD plus one WCHAR, padded.
_DETAIL_CBSIZE = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6


def _windll(name: str) -> Any:
    log.debug("_windll: %s", name)
    return getattr(ctypes.windll, name)  # pyright: ignore[reportAttributeAccessIssue]


def _setupapi() -> Any:
    """setupapi with real prototypes: HDEVINFO is a POINTER, and ctypes' default
    int return would truncate it on 64-bit Windows."""
    api = _windll("setupapi")
    vp, u32 = ctypes.c_void_p, ctypes.c_uint32
    api.SetupDiGetClassDevsW.restype = vp
    api.SetupDiGetClassDevsW.argtypes = [vp, vp, vp, u32]
    api.SetupDiEnumDeviceInterfaces.argtypes = [vp, vp, vp, u32, vp]
    api.SetupDiEnumDeviceInfo.argtypes = [vp, u32, vp]
    api.SetupDiGetDeviceInterfaceDetailW.argtypes = [vp, vp, vp, u32, vp, vp]
    api.SetupDiDestroyDeviceInfoList.argtypes = [vp]
    log.debug("_setupapi: prototypes set")
    return api


@contextmanager
def _device_set(api: Any, interface: _GUID) -> Iterator[Any]:
    """The present devices exposing *interface*, freed on exit; ``None`` when
    SetupAPI refuses."""
    info = api.SetupDiGetClassDevsW(ctypes.byref(interface), None, None,
                                    _DIGCF_PRESENT | _DIGCF_DEVICEINTERFACE)
    if info in (None, _INVALID_HANDLE, -1):
        log.warning("_device_set: SetupDiGetClassDevsW failed (WinError %d)",
                    ctypes.GetLastError())  # pyright: ignore[reportAttributeAccessIssue]
        yield None
        return
    log.debug("_device_set: opened")
    try:
        yield info
    finally:
        api.SetupDiDestroyDeviceInfoList(info)


def _instance_id(devinst: int) -> str:
    """The device-instance ID of *devinst* (``CM_Get_Device_IDW``)."""
    buf = ctypes.create_unicode_buffer(512)
    if _windll("cfgmgr32").CM_Get_Device_IDW(devinst, buf, len(buf), 0) != 0:
        log.debug("_instance_id: %s failed", devinst)
        return ""
    return buf.value


def _parent(devinst: int) -> int | None:
    """The parent device instance (``CM_Get_Parent``), or None at the root."""
    parent = ctypes.c_uint32()
    if _windll("cfgmgr32").CM_Get_Parent(ctypes.byref(parent), devinst, 0) != 0:
        log.debug("_parent: %s has none", devinst)
        return None
    return parent.value


def _location_paths(devinst: int) -> list[str]:
    """*devinst*'s location paths (``CM_DRP_LOCATION_PATHS``, a multi-string)."""
    size = ctypes.c_uint32(4096)
    buf = ctypes.create_string_buffer(size.value)
    if _windll("cfgmgr32").CM_Get_DevNode_Registry_PropertyW(
            devinst, _CM_DRP_LOCATION_PATHS, None, buf, ctypes.byref(size), 0) != 0:
        log.debug("_location_paths: %s has none", devinst)
        return []
    return [p for p in buf.raw[:size.value].decode("utf-16-le").split("\0") if p]


def _root_hubs() -> list[str]:
    """Every root hub's instance ID, in the order libusb numbers buses."""
    api = _setupapi()
    hubs: list[str] = []
    with _device_set(api, _GUID_DEVINTERFACE_USB_HUB) as info:
        index = 0
        while info is not None:
            data = _SP_DEVINFO_DATA(cbSize=ctypes.sizeof(_SP_DEVINFO_DATA))
            if not api.SetupDiEnumDeviceInfo(info, index, ctypes.byref(data)):
                break
            index += 1
            if (instance := _instance_id(data.DevInst)).upper().startswith(_ROOT_HUB):
                hubs.append(instance)
    log.debug("_root_hubs: %s", hubs)
    return hubs


def _disk_number(device_path: str) -> int | None:
    """``N`` of \\\\.\\PhysicalDriveN for a disk interface -- opened with no
    access rights, which IOCTL_STORAGE_GET_DEVICE_NUMBER does not need."""
    k32 = _windll("kernel32")
    handle = k32.CreateFileW(device_path, 0, 0x3, None, 3, 0, None)
    if handle in (None, _INVALID_HANDLE, -1):
        log.debug("_disk_number: cannot open %s", device_path)
        return None
    try:
        number = _STORAGE_DEVICE_NUMBER()
        returned = ctypes.c_uint32()
        if not k32.DeviceIoControl(handle, _IOCTL_STORAGE_GET_DEVICE_NUMBER, None, 0,
                                   ctypes.byref(number), ctypes.sizeof(number),
                                   ctypes.byref(returned), None):
            log.debug("_disk_number: %s answered no number", device_path)
            return None
        return number.DeviceNumber
    finally:
        k32.CloseHandle(handle)


def _disks() -> Iterator[tuple[str, int]]:
    """Every present disk interface: ``(device path, device instance)``."""
    setupapi = _setupapi()
    with _device_set(setupapi, _GUID_DEVINTERFACE_DISK) as info:
        index = 0
        while info is not None:
            iface = _SP_DEVICE_INTERFACE_DATA(cbSize=ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA))
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    info, None, ctypes.byref(_GUID_DEVINTERFACE_DISK), index,
                    ctypes.byref(iface)):
                break
            index += 1
            required = ctypes.c_uint32()
            setupapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(iface), None, 0,
                                                     ctypes.byref(required), None)
            detail = ctypes.create_string_buffer(max(required.value, _DETAIL_CBSIZE + 2))
            ctypes.c_uint32.from_buffer(detail).value = _DETAIL_CBSIZE
            devinfo = _SP_DEVINFO_DATA(cbSize=ctypes.sizeof(_SP_DEVINFO_DATA))
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    info, ctypes.byref(iface), detail, len(detail), None,
                    ctypes.byref(devinfo)):
                log.debug("_disks: interface %d has no detail", index - 1)
                continue
            yield ctypes.wstring_at(ctypes.addressof(detail) + 4), devinfo.DevInst


def _disk_usb(devinst: int, root_hubs: Sequence[str]) -> UsbDisk | None:
    """What the disk *devinst* hangs off, walking up its parents."""
    nodes = {_instance_id(node): node for node in ancestors(devinst, _parent)}
    log.debug("_disk_usb: %s", list(nodes))

    def paths_of(instance: str) -> list[str]:
        log.debug("_disk_usb.paths_of: %s", instance)
        return _location_paths(nodes[instance])
    return usb_disk(list(nodes), paths_of, root_hubs)


def find_physical_drive(vid: int, pid: int, unit: str = "") -> str | None:
    r"""``\\.\PhysicalDriveN`` of the panel *vid*:*pid*, or None.

    *unit* names which of several identical panels (#287); empty means the
    only one.
    """
    log.info("find_physical_drive: %04x:%04x unit=%s", vid, pid, unit or "(only)")
    hubs = _root_hubs()
    for path, devinst in _disks():
        disk = _disk_usb(devinst, hubs)
        if disk is None or (disk.vid, disk.pid) != (vid, pid):
            continue
        if unit and disk.unit != unit:
            log.info("find_physical_drive: %s is unit %s, not %s", path,
                     disk.unit or "(unnamed)", unit)
            continue
        number = _disk_number(path)
        if number is None:
            continue
        drive = rf"\\.\PHYSICALDRIVE{number}"
        log.info("find_physical_drive: %04x:%04x unit %s -> %s", vid, pid,
                 disk.unit or "(unnamed)", drive)
        return drive
    log.info("find_physical_drive: %04x:%04x unit=%s not present", vid, pid,
             unit or "(only)")
    return None

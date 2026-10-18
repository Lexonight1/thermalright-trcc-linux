"""Which \\\\.\\PhysicalDriveN is a TRCC panel -- found through SetupAPI, not WMI.

Linux walks from /dev/sgN up sysfs to the USB device and reads its VID:PID
(``linux._walk_sysfs_for_vid_pid``).  This is the same walk on Windows: every
present disk interface, up its device-instance parents to the one whose ID is
``USB\\VID_xxxx&PID_xxxx\\...``, then the disk's own number from
IOCTL_STORAGE_GET_DEVICE_NUMBER.  Opening a disk to ask its number needs no
access rights, so this works for a non-admin process, and nothing here depends
on the WMI service -- whose COM connection failed inside TRCC's service with an
RPC internal error (2026-10-18).
"""
from __future__ import annotations

import ctypes
import logging
import re
from collections.abc import Callable, Iterator
from typing import Any

log = logging.getLogger(__name__)

_USB_ID = re.compile(r"^USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})(?:\\|&|$)", re.I)
#: How far up the device tree to look: disk -> USBSTOR -> (interface ->) device.
_MAX_PARENTS = 4


def usb_ids(instance_id: str) -> tuple[int, int] | None:
    """``(vid, pid)`` of a ``USB\\VID_xxxx&PID_xxxx\\...`` device-instance ID."""
    m = _USB_ID.match(instance_id)
    log.debug("usb_ids: %s -> %s", instance_id, m.groups() if m else None)
    return (int(m.group(1), 16), int(m.group(2), 16)) if m else None


def usb_ancestor(instance_id: str, parent_of: Callable[[str], str | None],
                 ) -> tuple[int, int] | None:
    """The VID:PID of the first USB device at or above *instance_id*."""
    current: str | None = instance_id
    for _ in range(_MAX_PARENTS + 1):
        if current is None:
            break
        ids = usb_ids(current)
        if ids is not None:
            return ids
        current = parent_of(current)
    log.debug("usb_ancestor: no USB device above %s", instance_id)
    return None


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


# {53f56307-b6bf-11d0-94f2-00a0c91efb8b}
_GUID_DEVINTERFACE_DISK = _GUID(0x53F56307, 0xB6BF, 0x11D0,
                                (ctypes.c_ubyte * 8)(0x94, 0xF2, 0x00, 0xA0,
                                                     0xC9, 0x1E, 0xFB, 0x8B))
_DIGCF_PRESENT, _DIGCF_DEVICEINTERFACE = 0x2, 0x10
_IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D1080
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
    api.SetupDiGetDeviceInterfaceDetailW.argtypes = [vp, vp, vp, u32, vp, vp]
    api.SetupDiDestroyDeviceInfoList.argtypes = [vp]
    log.debug("_setupapi: prototypes set")
    return api


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
    info = setupapi.SetupDiGetClassDevsW(ctypes.byref(_GUID_DEVINTERFACE_DISK), None,
                                         None, _DIGCF_PRESENT | _DIGCF_DEVICEINTERFACE)
    if info in (None, _INVALID_HANDLE, -1):
        log.warning("_disks: SetupDiGetClassDevsW failed (WinError %d)",
                    ctypes.GetLastError())  # pyright: ignore[reportAttributeAccessIssue]
        return
    try:
        index = 0
        while True:
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
            path = ctypes.wstring_at(ctypes.addressof(detail) + 4)
            yield path, devinfo.DevInst
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(info)


def _disk_usb_ids(devinst: int) -> tuple[int, int] | None:
    """VID:PID of the USB device a disk hangs off, walking up its parents."""
    instances: dict[str, int] = {}

    def parent_of(instance: str) -> str | None:
        parent = _parent(instances[instance])
        if parent is None:
            log.debug("_disk_usb_ids: %s is a root", instance)
            return None
        parent_id = _instance_id(parent)
        instances[parent_id] = parent
        return parent_id

    disk_id = _instance_id(devinst)
    instances[disk_id] = devinst
    ids = usb_ancestor(disk_id, parent_of)
    log.debug("_disk_usb_ids: %s -> %s", disk_id, ids)
    return ids


def find_physical_drive(vid: int, pid: int) -> str | None:
    r"""``\\.\PhysicalDriveN`` of the panel *vid*:*pid*, or None."""
    log.info("find_physical_drive: %04x:%04x", vid, pid)
    for path, devinst in _disks():
        if _disk_usb_ids(devinst) != (vid, pid):
            continue
        number = _disk_number(path)
        if number is None:
            continue
        drive = rf"\\.\PHYSICALDRIVE{number}"
        log.info("find_physical_drive: %04x:%04x -> %s", vid, pid, drive)
        return drive
    log.info("find_physical_drive: %04x:%04x not present", vid, pid)
    return None

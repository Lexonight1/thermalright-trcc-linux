"""Cross-OS Platform smoke — verify the OS abstraction layer on every supported OS.

Runs on the dev box (Linux) but exercises all four Platform subclasses
(LinuxPlatform / WindowsPlatform / MacOSPlatform / BSDPlatform) by:

1. Importing each subclass — works because each defers OS-specific imports
   (``winreg``, ``wmi``, ``IOKit``, ``ctypes``) to method bodies, not module top.
2. Constructing one — ``Platform.__init__`` is trivial.
3. Asserting every ABC abstract method is overridden.
4. Calling methods that don't need real OS syscalls (signatures, ABC shape,
   config dirs, doctor/report config dataclasses).
5. Verifying the chain: factory registry has all 5 protocols and each lambda
   accepts a typed ``DeviceInfo`` end-to-end.

This is the canary for OS conformance after architecture changes: rename a
Platform method, add an abstract method, drift a signature — the smoke
fires on the wrong OS *without* leaving your dev box.

Run:
    PYTHONPATH=src python dev/smoke_platforms.py
"""
from __future__ import annotations

import inspect
import sys
from dataclasses import dataclass
from pathlib import Path

# Make src/ importable when run as a script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from trcc.legacy.adapters.system import PlatformFactory
from trcc.legacy.adapters.system.bsd_platform import BSDPlatform
from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
from trcc.legacy.adapters.system.macos_platform import MacOSPlatform
from trcc.legacy.adapters.system.windows_platform import WindowsPlatform
from trcc.legacy.core.models import (
    ALL_DEVICES,
    DetectedDevice,
    DeviceInfo,
)
from trcc.legacy.core.ports import Platform


# ── Result type — visual report at end ──────────────────────────────────

@dataclass(slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        mark = "[ OK ]" if self.passed else "[FAIL]"
        suffix = f" — {self.detail}" if self.detail else ""
        return f"    {mark}  {self.name}{suffix}"


# ── Per-Platform checks — feed in a fresh instance, get a CheckResult ───

def check_construct(cls: type[Platform]) -> CheckResult:
    """The Platform subclass instantiates cleanly on the dev box."""
    try:
        instance = cls()
        return CheckResult("construct", True, type(instance).__name__)
    except Exception as e:
        return CheckResult("construct", False, f"{type(e).__name__}: {e}")


def check_abstract_methods_overridden(cls: type[Platform]) -> CheckResult:
    """Every Platform ABC abstractmethod is implemented (LSP)."""
    if abstracts := getattr(cls, "__abstractmethods__", set()):
        return CheckResult("abstract methods", False,
                           f"still abstract: {sorted(abstracts)}")
    return CheckResult("abstract methods", True, "all overridden")


def check_scsi_transport_signature(platform: Platform) -> CheckResult:
    """``create_scsi_transport`` accepts the Phase 2 ``usb_address`` kwarg.

    Verifies the SCSI protocol's uniformity with HID/Bulk/LY/LED — every
    protocol now threads ``usb_address`` so the factory contract is the
    same shape for all 5.
    """
    sig = inspect.signature(platform.create_scsi_transport)
    if "usb_address" not in sig.parameters:
        return CheckResult("scsi transport signature", False,
                           "missing usb_address kwarg")
    if sig.parameters["usb_address"].kind != inspect.Parameter.KEYWORD_ONLY:
        return CheckResult("scsi transport signature", False,
                           "usb_address must be keyword-only")
    return CheckResult("scsi transport signature", True,
                       "(path, vid, pid, *, usb_address)")


def check_detect_devices_signature(platform: Platform) -> CheckResult:
    """``detect_devices`` is the uniform per-OS discovery method.

    Inspects the signature without invoking — calling would hit real WMI
    / sysfs / IOKit / sysctl. The signature shape is what the chain needs:
    ``() -> list[DetectedDevice]``.
    """
    sig = inspect.signature(platform.detect_devices)
    # No positional args other than self (bound out).
    positional = [p for p in sig.parameters.values()
                  if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    if positional:
        return CheckResult("detect_devices signature", False,
                           f"unexpected args: {[p.name for p in positional]}")
    return CheckResult("detect_devices signature", True, "() -> list[DetectedDevice]")


def check_config_dirs(platform: Platform) -> CheckResult:
    """``config_dir`` / ``data_dir`` are concrete shared methods — return strings."""
    try:
        c, d = platform.config_dir(), platform.data_dir()
    except Exception as e:
        return CheckResult("config dirs", False, f"{type(e).__name__}: {e}")
    if not (isinstance(c, str) and isinstance(d, str)):
        return CheckResult("config dirs", False,
                           f"types: config={type(c).__name__} data={type(d).__name__}")
    return CheckResult("config dirs", True, f"config={c}")


def check_doctor_config(platform: Platform) -> CheckResult:
    """``doctor_config`` returns a populated DoctorPlatformConfig dataclass."""
    try:
        cfg = platform.doctor_config()
    except Exception as e:
        return CheckResult("doctor_config", False, f"{type(e).__name__}: {e}")
    if not hasattr(cfg, "distro_name"):
        return CheckResult("doctor_config", False, "missing distro_name")
    return CheckResult("doctor_config", True, f"distro={cfg.distro_name!r}")


def check_report_config(platform: Platform) -> CheckResult:
    """``report_config`` returns ReportPlatformConfig with required fields."""
    try:
        cfg = platform.report_config()
    except Exception as e:
        return CheckResult("report_config", False, f"{type(e).__name__}: {e}")
    if not hasattr(cfg, "distro_name"):
        return CheckResult("report_config", False, "missing distro_name")
    return CheckResult("report_config", True, f"distro={cfg.distro_name!r}")


def check_minimize_on_close(platform: Platform) -> CheckResult:
    """``minimize_on_close`` returns a bool — drives GUI close behavior."""
    try:
        v = platform.minimize_on_close()
    except Exception as e:
        return CheckResult("minimize_on_close", False, f"{type(e).__name__}: {e}")
    if not isinstance(v, bool):
        return CheckResult("minimize_on_close", False,
                           f"returned {type(v).__name__}, expected bool")
    return CheckResult("minimize_on_close", True, f"{v}")


_PLATFORM_CHECKS = (
    check_abstract_methods_overridden,
    check_scsi_transport_signature,
    check_detect_devices_signature,
    check_config_dirs,
    check_doctor_config,
    check_report_config,
    check_minimize_on_close,
)


def run_platform(cls: type[Platform]) -> list[CheckResult]:
    """Run the full battery against one Platform subclass."""
    results = [check_construct(cls)]
    if not results[0].passed:
        return results
    platform = cls()
    results.append(check_abstract_methods_overridden(cls))
    for check in _PLATFORM_CHECKS[1:]:
        results.append(check(platform))
    return results


# ── Chain integrity (OS-agnostic) ───────────────────────────────────────

def check_platform_factory_registry() -> CheckResult:
    """``PlatformFactory._registry`` has all 4 OS factories self-registered.

    Self-registration is the OCP win: a new OS = one new decorated subclass.
    This check fires if anyone removes a ``@PlatformFactory.register(...)``
    decorator or forgets to add one for a new OS.
    """
    expected = {"win32", "darwin", "linux", "bsd"}
    actual = set(PlatformFactory._registry)
    if missing := expected - actual:
        return CheckResult("PlatformFactory registry", False, f"missing={missing}")
    if extra := actual - expected:
        return CheckResult("PlatformFactory registry", False, f"unexpected={extra}")
    return CheckResult("PlatformFactory registry", True, "4/4 OS factories registered")


def check_platform_factory_dispatch() -> CheckResult:
    """``PlatformFactory.current()`` returns a real Platform on the dev box.

    Validates the live dispatch path (``sys.platform → registry → make()``)
    that every composition root hits at startup.
    """
    try:
        instance = PlatformFactory.current()
    except Exception as e:
        return CheckResult("PlatformFactory dispatch", False, f"{type(e).__name__}: {e}")
    if not isinstance(instance, Platform):
        return CheckResult("PlatformFactory dispatch", False,
                           f"returned non-Platform: {type(instance).__name__}")
    return CheckResult("PlatformFactory dispatch", True,
                       f"current() → {type(instance).__name__}")


def check_factory_registry_complete() -> CheckResult:
    """All 5 protocols (scsi, hid, bulk, ly, led) are factory-registered."""
    from trcc.legacy.adapters.device.factory import DeviceProtocolFactory
    expected = {"scsi", "hid", "bulk", "ly", "led"}
    actual = set(DeviceProtocolFactory._PROTOCOL_REGISTRY)
    if missing := expected - actual:
        return CheckResult("factory registry complete", False, f"missing={missing}")
    if extra := actual - expected:
        return CheckResult("factory registry complete", False, f"unexpected={extra}")
    return CheckResult("factory registry complete", True, "5/5 protocols")


def check_protocol_factory_subclasses() -> CheckResult:
    """``ProtocolFactory._registry`` has all 5 ``@register``-decorated subclasses.

    Mirrors ``check_platform_factory_registry`` — same idiom in two places.
    Catches anyone forgetting the ``@ProtocolFactory.register(name)`` line
    on a new protocol or removing one by accident.
    """
    from trcc.legacy.adapters.device.factory import ProtocolFactory
    expected = {"scsi", "hid", "bulk", "ly", "led"}
    actual = set(ProtocolFactory._registry)
    if missing := expected - actual:
        return CheckResult("ProtocolFactory registry", False, f"missing={missing}")
    if extra := actual - expected:
        return CheckResult("ProtocolFactory registry", False, f"unexpected={extra}")
    return CheckResult("ProtocolFactory registry", True,
                       "5/5 @ProtocolFactory.register subclasses present")


def check_protocol_factory_for_info() -> CheckResult:
    """``ProtocolFactory.for_info(info)`` dispatches by name to the right subclass.

    Walks each protocol kind: builds a sample ``DeviceInfo`` carrying that
    protocol name, asks ``ProtocolFactory.for_info`` for the protocol, and
    verifies the type matches what the corresponding factory subclass would
    produce. Locks the dispatch-by-name contract Phase 4 depends on.
    """
    from trcc.legacy.adapters.device.bulk_protocol import BulkProtocol
    from trcc.legacy.adapters.device.factory import ProtocolFactory
    from trcc.legacy.adapters.device.hid_protocol import HidProtocol
    from trcc.legacy.adapters.device.led_protocol import LedProtocol
    from trcc.legacy.adapters.device.ly_protocol import LyProtocol
    from trcc.legacy.adapters.device.scsi_protocol import ScsiProtocol
    samples: dict[str, tuple[int, int, bool, type]] = {
        "scsi": (0x0402, 0x3922, True,  ScsiProtocol),
        "hid":  (0x0416, 0x5302, False, HidProtocol),
        "bulk": (0x87AD, 0x70DB, False, BulkProtocol),
        "ly":   (0x0416, 0x5408, False, LyProtocol),
        "led":  (0x0416, 0x8001, False, LedProtocol),
    }
    for proto, (vid, pid, scsi, expected_cls) in samples.items():
        detected = DetectedDevice(
            vid=vid, pid=pid,
            vendor_name="Smoke", product_name="Test",
            usb_path="usb:1:5",
            scsi_device="/dev/sg0" if scsi else None,
            protocol=proto, device_type=2,
            implementation="generic",
        )
        info = DeviceInfo.from_detected(detected)
        instance = ProtocolFactory.for_info(info)
        if not isinstance(instance, expected_cls):
            return CheckResult("ProtocolFactory.for_info dispatch", False,
                               f"{proto!r} returned {type(instance).__name__} "
                               f"(expected {expected_cls.__name__})")
    return CheckResult("ProtocolFactory.for_info dispatch", True,
                       "5/5 protocols dispatch by name to correct subclass")


def check_factory_lambdas_accept_deviceinfo() -> CheckResult:
    """Every factory lambda builds a Protocol from a real DeviceInfo.

    Catches the v9.5.2 half-fix trap structurally — any protocol whose
    lambda reaches for a missing field would crash here, before any user
    plugs in a real device.
    """
    from trcc.legacy.adapters.device.factory import DeviceProtocolFactory

    # Use the canonical conversion chokepoint so the DeviceInfo carries
    # ``usb_address`` exactly as a real detector would produce.
    samples: dict[str, tuple[int, int, bool]] = {
        # protocol → (vid, pid, has_scsi_path)
        "scsi": (0x0402, 0x3922, True),
        "hid":  (0x0416, 0x5302, False),
        "bulk": (0x87AD, 0x70DB, False),
        "ly":   (0x0416, 0x5408, False),
        "led":  (0x0416, 0x8001, False),
    }
    failures: list[str] = []
    for proto, (vid, pid, scsi) in samples.items():
        detected = DetectedDevice(
            vid=vid, pid=pid,
            vendor_name="Smoke", product_name="Test",
            usb_path="usb:1:5",
            scsi_device="/dev/sg0" if scsi else None,
            protocol=proto, device_type=2,
            implementation="generic",
        )
        info = DeviceInfo.from_detected(detected)
        fn = DeviceProtocolFactory._PROTOCOL_REGISTRY[proto]
        try:
            fn(info)
        except Exception as e:
            failures.append(f"{proto}: {type(e).__name__}: {e}")
    if failures:
        return CheckResult("factory lambdas accept DeviceInfo", False,
                           "; ".join(failures))
    return CheckResult("factory lambdas accept DeviceInfo", True,
                       f"all {len(samples)} protocols built")


def check_usb_address_threaded_for_every_device() -> CheckResult:
    """ALL_DEVICES round-trip DetectedDevice → DeviceInfo with usb_address set
    for every non-SCSI entry. Closes #130/#131 structurally.
    """
    failures: list[str] = []
    for (vid, pid), entry in ALL_DEVICES.items():
        if entry.protocol == "scsi":
            continue
        detected = DetectedDevice(
            vid=vid, pid=pid,
            vendor_name=entry.vendor, product_name=entry.product,
            usb_path="usb:3:12", scsi_device=None,
            protocol=entry.protocol, device_type=entry.device_type,
            implementation=entry.implementation,
            button_image=entry.button_image, model=entry.model,
        )
        info = DeviceInfo.from_detected(detected)
        if info.usb_address is None:
            failures.append(f"{vid:04x}:{pid:04x} ({entry.protocol})")
    if failures:
        return CheckResult("usb_address threaded (#130/#131)", False,
                           f"missing on: {failures}")
    non_scsi = sum(1 for e in ALL_DEVICES.values() if e.protocol != "scsi")
    return CheckResult("usb_address threaded (#130/#131)", True,
                       f"{non_scsi} non-SCSI devices all carry usb_address")


def check_device_factory_subclasses() -> CheckResult:
    """``DeviceFactory._registry`` has both LCD + LED ``@register``-decorated subclasses.

    Third leg of the three-factory chain (PlatformFactory + ProtocolFactory +
    DeviceFactory). Same idiom as the others — catches anyone forgetting the
    ``@DeviceFactory.register(kind)`` line.
    """
    from trcc.legacy.core.device.factory import DeviceFactory
    expected = {"lcd", "led"}
    actual = set(DeviceFactory._registry)
    if missing := expected - actual:
        return CheckResult("DeviceFactory registry", False, f"missing={missing}")
    if extra := actual - expected:
        return CheckResult("DeviceFactory registry", False, f"unexpected={extra}")
    return CheckResult("DeviceFactory registry", True,
                       "2/2 @DeviceFactory.register subclasses present")


def check_device_abc_subclass_shape() -> CheckResult:
    """LCDDevice + LEDDevice implement the Device ABC (Phase 1 contract)."""
    from trcc.legacy.core.device import Device, LCDDevice, LEDDevice
    for sub in (LCDDevice, LEDDevice):
        if not issubclass(sub, Device):
            return CheckResult("Device ABC subclasses", False,
                               f"{sub.__name__} is not a Device subclass")
        if abstracts := getattr(sub, "__abstractmethods__", set()):
            return CheckResult("Device ABC subclasses", False,
                               f"{sub.__name__} still abstract: {sorted(abstracts)}")
    return CheckResult("Device ABC subclasses", True,
                       "LCDDevice + LEDDevice fulfill the ABC")


def check_device_protocol_di() -> CheckResult:
    """Phase 4: ``Device(protocol=…)`` wires through to ``device.protocol``.

    The chain step "device gets the protocol it needs" is structurally
    enforced here. A sentinel proves the constructor argument is stored
    and surfaced via the ABC's ``protocol`` property — wrong wiring
    (e.g. naming drift between ``self._proto`` vs ``self._protocol``)
    fires this check.
    """
    from types import SimpleNamespace

    from trcc.legacy.core.device import LCDDevice, LEDDevice
    # SimpleNamespace gives us a real ``__dict__`` so ``LCDDevice.__init__``
    # → ``_wire_protocol_observers`` can ``setattr(protocol,
    # 'on_state_changed', …)`` without crashing on a bare ``object()``.
    # Plain ``object()`` instances have no ``__dict__`` and reject attribute
    # writes — but real protocols (DeviceProtocol subclasses) do, so the
    # SimpleNamespace stub is closer to production shape.
    for cls in (LCDDevice, LEDDevice):
        sentinel = SimpleNamespace()
        try:
            instance = cls(protocol=sentinel)
        except Exception as e:
            return CheckResult("Device.protocol DI", False,
                               f"{cls.__name__} ctor failed: {type(e).__name__}: {e}")
        if instance.protocol is not sentinel:
            return CheckResult("Device.protocol DI", False,
                               f"{cls.__name__}.protocol did not return injected value")
    return CheckResult("Device.protocol DI", True,
                       "LCDDevice + LEDDevice accept & expose protocol")


_CHAIN_CHECKS = (
    check_platform_factory_registry,
    check_platform_factory_dispatch,
    check_protocol_factory_subclasses,
    check_protocol_factory_for_info,
    check_device_factory_subclasses,
    check_device_abc_subclass_shape,
    check_device_protocol_di,
    check_factory_registry_complete,
    check_factory_lambdas_accept_deviceinfo,
    check_usb_address_threaded_for_every_device,
)


# ── Runner ──────────────────────────────────────────────────────────────

_PLATFORMS: tuple[type[Platform], ...] = (
    LinuxPlatform, WindowsPlatform, MacOSPlatform, BSDPlatform,
)


def main() -> int:
    print("\n  TRCC 4-OS Platform Smoke")
    print("  ────────────────────────")
    failed = 0
    for cls in _PLATFORMS:
        print(f"\n  ▸ {cls.__name__}")
        for r in run_platform(cls):
            print(r)
            failed += not r.passed
    print("\n  ▸ Chain integrity (OS-agnostic)")
    for check in _CHAIN_CHECKS:
        r = check()
        print(r)
        failed += not r.passed
    print()
    if failed:
        print(f"  {failed} check(s) failed")
        return 1
    total = len(_PLATFORMS) * (len(_PLATFORM_CHECKS) + 1) + len(_CHAIN_CHECKS)
    print(f"  All {total} checks passed — chain holds across all 4 OSes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

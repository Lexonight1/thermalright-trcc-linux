"""Shared bootstrap for ``dev/mock_*.py`` — runs the real next/ GUI / CLI
/ API but rooted at ``dev/.trcc/`` instead of ``~/.trcc/`` so a dev
session never touches the user's real config.

The post-cutover ``Platform`` port is the only seam we need.
``DevPlatform`` subclasses the host's production platform (just
``LinuxPlatform`` here — Mac/Windows/BSD can extend later) and overrides
only ``paths()`` to point at the dev directories.  Real USB, real
sensors, real autostart — same code paths a packaged install runs, just
isolated to a throwaway data root.

For a hardware-less smoke (CI, ergonomics dev box without a real LCD),
plug in ``FakePlatform`` from ``tests/conftest.py`` instead — the
shape is identical.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trcc.core.models import DeviceInfo
    from trcc.core.ports import (
        BulkTransport,
        HotplugMonitor,
        Platform,
        ScsiTransport,
    )

# Make src/ importable without requiring the caller to set PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / 'src'))
sys.path.insert(0, str(_REPO_ROOT))


# ─── Dev paths (every mock_* script writes here, not ~/.trcc) ────────────────

_DEV_DIR = Path(__file__).resolve().parent
DEV_TRCC = _DEV_DIR / '.trcc'
DEV_DATA = DEV_TRCC / 'data'
DEV_USER = _DEV_DIR / '.trcc-user'
DEV_TRCC.mkdir(exist_ok=True)
DEV_DATA.mkdir(exist_ok=True)
DEV_USER.mkdir(exist_ok=True)

DEVICES_JSON = _DEV_DIR / 'devices.json'  # survives .trcc wipe

log = logging.getLogger(__name__)


# ─── Device spec loading ─────────────────────────────────────────────────────

def _specs_from_report(report_path: str) -> list[dict]:
    """Parse a ``trcc report`` file into device-spec dicts.

    Kept for callers that want to record + replay a user's hardware set
    — the diagnose tool already understands the file format.  Not used
    by ``mock_gui.py`` against real hardware (Platform.scan_devices
    returns the real attached set), only by ``mock_cli`` / ``mock_api``
    style harnesses that want a fixed device list.
    """
    sys.path.insert(0, str(_REPO_ROOT / 'dev' / 'tools'))
    from diagnose import parse_report  # type: ignore[import-not-found]

    text = Path(report_path).read_text()
    report = parse_report(text)
    if report.os_name:
        print(f"User OS: {report.os_name}")
    if report.trcc_version:
        print(f"User trcc version: {report.trcc_version}")
    specs: list[dict] = []
    for dev in report.devices:
        spec: dict[str, Any] = {
            "vid": f"{dev.vid:04x}",
            "pid": f"{dev.pid:04x}",
            "name": f"User device ({dev.vid:04x}:{dev.pid:04x})",
        }
        # The report records the device's handshake reply bytes (PM + sub_byte),
        # not just vid:pid — carry them so the mock reproduces the EXACT device,
        # not just its USB id.
        if getattr(dev, "pm", 0):
            spec["pm"] = dev.pm
        if getattr(dev, "sub", 0):
            spec["sub"] = dev.sub
        if dev.width and dev.height:
            spec["resolution"] = f"{dev.width}x{dev.height}"
        specs.append(spec)
    return specs


def load_device_specs(report_path: str | None = None) -> list[dict]:
    """Resolve device specs from a report file or ``dev/devices.json``.

    Returns an empty list when neither source is present — the real
    ``Platform.scan_devices`` then enumerates whatever's attached.
    """
    if report_path:
        return _specs_from_report(report_path)
    if DEVICES_JSON.exists():
        try:
            raw = json.loads(DEVICES_JSON.read_text())
            if isinstance(raw, list):
                return raw
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: bad devices.json: {e} — ignoring")
    return []


# ─── Simulated GPU fleet (``--gpus``) ────────────────────────────────────────

# Default discreteness per vendor.  Intel is the CPU's integrated graphics;
# nvidia/amd are add-in cards unless told otherwise.
_GPU_DISCRETE_DEFAULT = {"nvidia": True, "amd": True, "intel": False}


def _pop_gpu_flag() -> str | None:
    """Pull ``--gpus VALUE`` out of argv.

    Popped in :func:`bootstrap` — the one seam EVERY harness (gui / cli / api /
    qtgui) funnels through — so the flag works for all four without teaching
    four argument parsers about it.
    """
    if "--gpus" not in sys.argv:
        return None
    i = sys.argv.index("--gpus")
    if i + 1 >= len(sys.argv):
        print("Error: --gpus requires a value, e.g. --gpus nvidia,amd,intel",
              file=sys.stderr)
        raise SystemExit(1)
    value = sys.argv[i + 1]
    del sys.argv[i:i + 2]
    return value


def parse_gpu_specs(raw: str) -> list[tuple[str, bool]]:
    """``'nvidia,amd,intel'`` → ``[('nvidia', True), ('amd', True), ('intel', False)]``.

    ``vendor[:discrete|:igpu]`` overrides the per-vendor default, because the
    interesting bugs live in the exceptions: ``amd:discrete`` is the #157 APU
    that reports a large UMA framebuffer and so claims to be a discrete card.
    """
    specs: list[tuple[str, bool]] = []
    for token in raw.split(","):
        if not (token := token.strip().lower()):
            continue
        vendor, _, kind = token.partition(":")
        if kind == "igpu":
            discrete = False
        elif kind == "discrete":
            discrete = True
        elif kind:
            print(f"Error: --gpus {token!r} — kind must be 'discrete' or 'igpu'",
                  file=sys.stderr)
            raise SystemExit(1)
        else:
            discrete = _GPU_DISCRETE_DEFAULT.get(vendor, True)
        specs.append((vendor, discrete))
    return specs


def apply_fake_gpus(platform: Platform, specs: list[tuple[str, bool]]) -> None:
    """Swap the GPU list for a simulated fleet; every other sensor stays real.

    Why swap rather than rebuild: ``build_linux_sensors`` composes the GPUs
    beside real hwmon cpu/fan/disk/dram sources, and only ``cpu`` / ``memory``
    / ``gpus`` / ``fans`` are readable back off the enumerator — reconstructing
    it would silently drop disks, DRAM and the memory clock.  Replacing the one
    list keeps every other reading honest, and re-sorting through the real
    ``_gpu_order`` means the auto-pick under test is the shipping one.

    This is the GPU counterpart to ``devices.json``: it lets a single-GPU dev
    box drive the real GUI as a multi-GPU machine, which is the only way to see
    the Control Center's GPU select (it renders a plain label at one GPU).
    """
    from trcc.adapters.sensors.aggregator import _gpu_order

    from tests.conftest import FakeGpu

    per_vendor: dict[str, int] = {}
    gpus = []
    for vendor, discrete in specs:
        index = per_vendor.get(vendor, 0)
        per_vendor[vendor] = index + 1
        gpus.append(FakeGpu(index, discrete=discrete, vendor=vendor))

    enumerator = platform.sensors()
    enumerator._gpus = sorted(gpus, key=_gpu_order)
    log.info("apply_fake_gpus: %s", [(g.key, g.is_discrete) for g in gpus])
    print(f"Mock GPUs: {len(gpus)} simulated — "
          + ", ".join(f"{g.key} ({'discrete' if g.is_discrete else 'integrated'})"
                      for g in enumerator._gpus))


# ─── Device catalog (the REAL device space — variants, not bare vid:pid) ─────

def device_catalog() -> list[tuple[list[tuple[int, int]], int, int | None, str, tuple[int, int]]]:
    """Every cooler variant the app knows: ``(vids, pm, sub, model, (w, h))``.

    The true device space is the per-(PM, SUB) ``VariantOverride`` table, NOT
    the handful of USB vid:pid rows — one vid:pid (e.g. the bulk 0x87AD:0x70DB)
    fronts dozens of distinct coolers that the handshake PM/SUB fingerprint
    tells apart.  The 4 bulk vid:pids share one PM table, so it's listed once
    with all its vid:pids in ``vids``.  ``model`` is the registry button-image
    name; the resolution is resolved through the same model path connect() uses.
    """
    from trcc.core.protocol import get_profile, pm_to_fbl
    from trcc.core.variants import _VARIANT_REGISTRY

    by_table: dict[int, tuple[dict, list[tuple[int, int]]]] = {}
    for (vid, pid), table in _VARIANT_REGISTRY.items():
        by_table.setdefault(id(table), (table, []))[1].append((vid, pid))

    rows: list[tuple[list[tuple[int, int]], int, int | None, str, tuple[int, int]]] = []
    for table, vids in by_table.values():
        for pm in sorted(table):
            for sub, override in table[pm].items():
                resolved_sub = sub if sub is not None else 0
                res = get_profile(pm_to_fbl(pm, resolved_sub), pm).resolution
                rows.append((vids, pm, sub, override.button_image, res))
    return rows


def all_variant_specs() -> list[dict]:
    """One ``devices.json`` spec per cooler variant — simulate the WHOLE catalog.

    Bulk variants are emitted once (under their first shared vid:pid) so the
    fleet has no 4x duplicate models.
    """
    specs: list[dict] = []
    for vids, pm, sub, model, _res in device_catalog():
        vid, pid = vids[0]
        spec: dict[str, Any] = {
            "vid": f"{vid:04x}", "pid": f"{pid:04x}", "pm": pm, "name": model,
        }
        if sub is not None:
            spec["sub"] = sub
        specs.append(spec)
    return specs


# ─── DevPaths / DevPlatform — production Platform with paths redirected ──────

def _build_dev_platform(specs: list[dict] | None = None) -> Platform:
    """Return a Platform rooted at ``dev/.trcc/`` for the host OS.

    Always subclasses the production host Platform so sensors, autostart,
    setup, distro detection — everything except the filesystem layout — run
    as the real packaged app does.

    * No ``specs`` → ``DevPlatform``: real USB enumeration + real hotplug too.
      "Drive the real GUI against my own hardware without polluting ~/.trcc."
    * ``specs`` → ``DevMockPlatform``: additionally overrides ONLY the USB
      seam (``scan_devices`` + scripted ``open_scsi``/``open_bulk``) and forces
      Noop hotplug (a simulated fleet has no live attach).  Sensors/autostart/
      setup/distro stay the real host code, so the mock IS the real app with
      three methods swapped — which is exactly why it surfaces real bugs.
    """
    from trcc.core.ports import Paths

    class DevPaths(Paths):
        """Paths port rooted at the dev tree.

        Subpaths (``theme_dir`` / ``cloud_theme_dir`` / etc.) inherit
        their concrete behaviour from the ABC, so we only override the
        four roots.
        """

        def config_dir(self) -> Path:
            return DEV_TRCC

        def data_dir(self) -> Path:
            return DEV_DATA

        def user_content_dir(self) -> Path:
            return DEV_USER

        def log_file(self) -> Path:
            return DEV_TRCC / "trcc.log"

    # Pick the host's production Platform impl as the base so sensors +
    # autostart + setup + distro all work the way the packaged app does.
    # Mirrors production launch (ui/gui/__init__.run_gui + _boot).
    from trcc.adapters.system import current_platform
    host = current_platform()
    # ``Any`` so pyright accepts the runtime-computed concrete base class
    # (it can't see that ``type(host)`` is a concrete LinuxPlatform, not the
    # abstract ``Platform``).
    host_cls: Any = type(host)
    dev_paths = DevPaths()

    if not specs:
        class DevPlatform(host_cls):
            """Production host platform with paths redirected to ``dev/.trcc/``."""

            def paths(self) -> Paths:
                return dev_paths

        return DevPlatform()

    # Simulated fleet — extend the real host, override ONLY the USB seam.
    # The scripted scan/handshake logic is shared with the unit-test mock
    # (tests.mock_platform) so there is exactly one source for it.
    from tests.mock_platform import (
        DeviceSpec,
        scripted_bulk_transport,
        scripted_scsi_transport,
    )
    parsed = [DeviceSpec.parse(s) for s in specs]
    by_key = {sp.key: sp for sp in parsed}

    class DevMockPlatform(host_cls):
        """Real host platform with the USB seam swapped for a scripted fleet."""

        def __init__(self) -> None:
            super().__init__()
            # Exact handshake reply the dev console pins per vid:pid.  Read at
            # open_*() time, so a set_active_reply() + reconnect re-presents.
            self._reply_override: dict[tuple[int, int], tuple[int, int, int]] = {}

        def paths(self) -> Paths:
            return dev_paths

        def hotplug(self) -> HotplugMonitor:
            from trcc.adapters.system._hotplug import NoopHotplugMonitor
            return NoopHotplugMonitor(reason="DevMockPlatform simulated fleet")

        def scan_devices(self) -> list[DeviceInfo]:
            # Dev rule: NO auto-handshake.  The splash discovers nothing, so the
            # GUI boots blank + fast (no 119-device handshake storm); the dev
            # variant panel's button clicks are the ONLY thing that attaches +
            # handshakes a device (via ConnectDevice).  The scripted transports
            # below still serve those click-driven handshakes.
            return []

        def set_active_reply(self, vid: int, pid: int, *,
                             pm: int, sub: int, fbl: int) -> None:
            """Pin the exact handshake reply a vid:pid returns on next connect.

            The dev console calls this, then re-runs ConnectDevice so the app
            re-handshakes against the injected reply and re-presents.
            """
            self._reply_override[(vid, pid)] = (pm, sub, fbl)
            log.info("DevMockPlatform.set_active_reply: %04x:%04x pm=%d sub=%d fbl=%d",
                     vid, pid, pm, sub, fbl)

        # Only the two OPENERS are scripted — ``BaseOS.open_transport`` and its
        # Wire→opener table are inherited from the real host platform, so the
        # mock exercises the production dispatch rather than a copy of it.
        def _open_scsi(self, vid: int, pid: int,
                       serial: str | None = None) -> ScsiTransport:
            return scripted_scsi_transport(by_key, vid, pid, self._reply_override)

        def _open_bulk(self, vid: int, pid: int,
                       serial: str | None = None) -> BulkTransport:
            return scripted_bulk_transport(by_key, vid, pid, self._reply_override)

    log.info("DevMockPlatform: %d simulated device(s) on real %s base",
             len(parsed), host_cls.__name__)
    return DevMockPlatform()


# ─── Main bootstrap ──────────────────────────────────────────────────────────

def bootstrap(report_path: str | None = None,
              verbosity: int = 0, all_devices: bool = False,
              specs: list[dict] | None = None) -> Platform:
    """Wire up logging + paths and return a ``Platform`` rooted at
    ``dev/.trcc/``.

    The caller (``mock_gui.py`` / ``mock_cli.py`` / ``mock_api.py``)
    drives the rest of the composition root — same shape production
    uses, just with the dev platform instance.

    Spec source precedence: an explicit ``specs`` list (e.g. the ``device=``
    CLI form) wins; then ``all_devices`` (the WHOLE variant catalog); then a
    ``--report`` file; then ``dev/devices.json``.
    """
    from trcc.adapters.infra.logging import configure_logging

    # Specs present → simulate that fleet with scripted USB on a real host base
    # (DevMockPlatform).  No specs → drive real attached hardware (DevPlatform).
    # The mock is the tool to GUI-verify device-specific render/geometry (#136
    # portrait panels, widescreen, LED) with zero hardware; the real path stays
    # the default so the harness still works against a plugged-in cooler.
    if specs:
        source = "device= CLI"
    elif all_devices:
        source = "--all catalog"
        specs = all_variant_specs()
    else:
        source = "--report" if report_path else "devices.json"
        specs = load_device_specs(report_path)
    platform = _build_dev_platform(specs or None)

    # Same policy the shipping app uses (``ui/cli/main:_root``): the FILE
    # always keeps DEBUG, only the terminal level rises with -v.  This harness
    # kept the old ``DEBUG if -v else INFO`` rule, so a mock run's log was
    # missing exactly the branch/decision lines it exists to show — and the
    # qtgui path calls ``bootstrap()`` with no verbosity at all, so -v could
    # not reach it even when passed.
    configure_logging(
        platform.paths().log_file(),
        level=logging.DEBUG,
        stderr_level=logging.DEBUG if verbosity >= 2
        else logging.INFO if verbosity == 1
        else logging.WARNING,
    )
    log.info(
        "dev bootstrap: platform=%s paths.config=%s specs=%d",
        type(platform).__name__, platform.paths().config_dir(), len(specs),
    )
    # A simulated GPU fleet is orthogonal to the device fleet — it fakes what
    # the box HAS, not what's plugged into USB — so it applies to either
    # platform, after logging so the swap is visible in the log.
    if (raw_gpus := _pop_gpu_flag()):
        apply_fake_gpus(platform, parse_gpu_specs(raw_gpus))
    if specs:
        print(f"Mock fleet: {len(specs)} device spec(s) from {source} — "
              "scripted on a real host platform (no hardware needed).")
    return platform

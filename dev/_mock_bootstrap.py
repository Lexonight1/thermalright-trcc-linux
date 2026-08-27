"""Shared bootstrap for ``dev/mock_*.py`` — runs the real next/ GUI / CLI
/ API but rooted at ``dev/.trcc/`` instead of ``~/.trcc/`` so a dev
session never touches the user's real config.

The post-cutover ``Platform`` port is the only seam we need.
``DevPlatform`` subclasses the host's production platform (just
``LinuxOS`` here — Mac/Windows/BSD can extend later) and overrides
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
    from trcc.core.models import DeviceInfo, Wire
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

NO_PANEL: tuple[int, int] = (0, 0)
"""Resolution reported for a wire that drives no panel (LED segment displays)."""


def variant_resolution(wire: Wire, pm: int, sub: int) -> tuple[int, int]:
    """Geometry a ``(pm, sub)`` handshake on *wire* resolves to, without USB.

    This must agree with what that wire's adapter computes at ``connect()``,
    and is gated by ``test_dev_catalog_agrees_with_the_connect_path``.

    Bulk is the reason this function exists.  Resolving it as
    ``get_profile(pm_to_fbl(pm, sub), pm)`` — which is what this module used
    to do — disagrees with the shipping path for every square bulk cooler:
    :func:`bulk_profile` diverts any PM outside ``_BULK_KNOWN_PMS`` to the
    480x480 base *precisely so* an unknown PM is not echoed into
    ``get_profile`` as a bogus FBL.  Skipping it mocked GRAND VISION, CORE
    VISION, HYPER VISION and PA120 at 320x320 while the app drove them at
    480x480, and manufactured a bogus-FBL warning for each.

    That divergence is the exact failure ``bulk_profile``'s own docstring
    records — a hand-copy of these rules in the auditor drifted, invented a
    phantom FBL 6, and reported our reporter-confirmed FW360 rotation as a
    180° bug.  Ask the shipping resolver; never restate its rules.
    """
    from trcc.adapters.device.bulk_lcd import bulk_profile
    from trcc.core.models import Wire
    from trcc.core.protocol import get_profile, pm_to_fbl

    if wire is Wire.LED:
        return NO_PANEL
    if wire is Wire.BULK:
        return bulk_profile(pm, sub, "catalog")[1].resolution
    return get_profile(pm_to_fbl(pm, sub), pm).resolution


def device_catalog() -> list[tuple[list[tuple[int, int]], int, int | None, str, tuple[int, int]]]:
    """Every cooler variant the app knows: ``(vids, pm, sub, model, (w, h))``.

    The true device space is the per-(PM, SUB) ``VariantOverride`` table, NOT
    the handful of USB vid:pid rows — one vid:pid (e.g. the bulk 0x87AD:0x70DB)
    fronts dozens of distinct coolers that the handshake PM/SUB fingerprint
    tells apart.  vid:pids sharing a PM table are listed once, under all of
    them in ``vids``.  ``model`` is the registry button-image name.

    Geometry comes from :func:`variant_resolution`, keyed on the wire of
    ``vids[0]`` — the vid:pid ``all_variant_specs`` actually simulates.  A
    shared table can span wires (the bulk PM table is aliased by two SCSI
    vid:pids), and the same PM resolves differently per wire, so the row
    describes the device the mock fleet builds.  LED rows carry
    :data:`NO_PANEL`: a segment display has no canvas, and asking the FBL
    tables for one is a category error that used to answer 320x320.
    """
    from trcc.core.models import Wire
    from trcc.core.registry import find_product
    from trcc.core.variants import _VARIANT_REGISTRY

    by_table: dict[int, tuple[dict, list[tuple[int, int]]]] = {}
    for (vid, pid), table in _VARIANT_REGISTRY.items():
        by_table.setdefault(id(table), (table, []))[1].append((vid, pid))

    rows: list[tuple[list[tuple[int, int]], int, int | None, str, tuple[int, int]]] = []
    for table, vids in by_table.values():
        product = find_product(*vids[0])
        wire = product.wire if product is not None else Wire.SCSI
        for pm in sorted(table):
            for sub, override in table[pm].items():
                res = variant_resolution(wire, pm, sub if sub is not None else 0)
                rows.append((vids, pm, sub, override.button_image, res))
    return rows


def _fingerprint_is_catalogued(wire: Wire, pm: int, sub: int) -> bool:
    """Would this ``(pm, sub)`` resolve on this wire WITHOUT the catalog guessing?

    A vid:pid may share a variant table with a different wire — the two SCSI ids
    alias the bulk PM table — and a PM that means something on bulk is just an
    unknown FBL on SCSI, where the convention is PM == FBL.  Simulating those
    produces devices that only exercise ``_warn_unknown``'s 320x320 fallback,
    which floods the log and tests the guess rather than a panel.

    So the fleet only emits fingerprints the shipping tables actually know.
    ``get_profile`` warns exactly when the FBL is absent from ``FBL_PROFILES``;
    bulk is the one wire with its own fallback (unknown PM → FBL 72), so it is
    asked its own question.
    """
    from trcc.adapters.device.bulk_lcd import _BULK_KNOWN_PMS
    from trcc.core.models import Wire as _W
    from trcc.core.protocol import FBL_PROFILES, pm_to_fbl

    if wire is _W.BULK:
        return pm in _BULK_KNOWN_PMS or (pm == 1 and sub in (48, 49))
    return pm_to_fbl(pm, sub) in FBL_PROFILES


def all_variant_specs() -> list[dict]:
    """One spec per distinct PANEL, across every USB id and every wire.

    Sourced from what we can SIMULATE — the device registry crossed with the
    PM→geometry tables — not from what we can NAME.

    It used to iterate :func:`device_catalog`, which is built from
    ``_VARIANT_REGISTRY`` (the marketing-name table) and groups vid:pids that
    SHARE a PM table, emitting only ``vids[0]``.  Both choices cost coverage:

      * five of the ten registry vid:pids have no variant rows at all, so LY,
        ALI and two HID panels were never simulated;
      * the two SCSI ids alias the bulk PM table, so they collapsed into the
        bulk group and the SCSI wire never ran either.

    Net effect: ``--all`` was 132 specs across **3** vid:pids and 3 wires.  A
    mock does not need a cooler's name to fake it — it needs a vid:pid and a
    handshake fingerprint, both of which we have for every supported panel.

    Deduped on ``(resolution, portrait-mounted)`` per device: two PMs that land
    on the same geometry and mount are the same panel to everything downstream,
    and emitting both only doubles the sidebar.

    LED devices are the exception and keep every PM.  A segment display has no
    canvas — every row is :data:`NO_PANEL` — so a geometry key collapses all 31
    catalogued LED models into one, discarding the axis that actually
    distinguishes them.  For them the PM *is* the identity.
    """
    from trcc.core.protocol import _FBL_192_BY_PM, _FBL_224_BY_PM
    from trcc.core.registry import ALL_DEVICES
    from trcc.core.variants import _VARIANT_REGISTRY

    # PMs the geometry tables name but no variant row reaches — 960x320,
    # 800x480, 1920x440, 640x172, 1280x480 …  These are exactly the panels
    # with open questions against the C#, so they matter most.
    table_pms = sorted(set(_FBL_224_BY_PM) | set(_FBL_192_BY_PM))

    specs: list[dict] = []
    for (vid, pid), info in sorted(ALL_DEVICES.items()):
        fingerprints: set[tuple[int, int]] = set()
        for pm, subs in _VARIANT_REGISTRY.get((vid, pid), {}).items():
            for sub in subs:
                fingerprints.add((pm, sub if sub is not None else 0))
        fingerprints.update((pm, 0) for pm in table_pms)
        if not fingerprints:                      # no table reaches this id
            fingerprints.add((info.fbl or 0, 0))

        # Key shape differs by device kind (see the LED note above), so
        # this is deliberately a heterogeneous set of tuples.
        seen: set[tuple] = set()
        for pm, sub in sorted(fingerprints):
            if not _fingerprint_is_catalogued(info.wire, pm, sub):
                continue
            try:
                res = variant_resolution(info.wire, pm, sub)
            except Exception:                     # unsupported fingerprint
                continue
            # A panel is identified by its geometry + mount; an LED by its PM.
            sig = ((pm, sub) if res == NO_PANEL else (res, sub >= 5))
            if sig in seen:
                continue
            seen.add(sig)
            label = "no panel" if res == NO_PANEL else f"{res[0]}x{res[1]}"
            spec: dict[str, Any] = {
                "vid": f"{vid:04x}", "pid": f"{pid:04x}", "pm": pm,
                "name": f"{info.wire.value} {label} pm{pm}"
                        + (f" sub{sub}" if sub else ""),
            }
            if sub:
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
    # (it can't see that ``type(host)`` is a concrete LinuxOS, not the
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
              specs: list[dict] | None = None,
              hardware: bool = False) -> Platform:
    """Wire up logging + paths and return a ``Platform`` rooted at
    ``dev/.trcc/``.

    The caller (``mock_gui.py`` / ``mock_cli.py`` / ``mock_api.py``)
    drives the rest of the composition root — same shape production
    uses, just with the dev platform instance.

    Spec source precedence, narrowest first::

        specs=          the ``device=VID:PID`` CLI form — one device
        hardware=True   drive the real cooler plugged into this box
        report_path=    replay a reporter's fleet from their trcc report
        devices.json    a local hand-written fleet, when the file exists
        (default)       THE WHOLE FLEET — every vid:pid, every wire

    **The whole fleet is the default.**  It used to be ``devices.json``, falling
    back to real hardware — which meant the harness silently tested whatever
    three devices happened to be in a gitignored local file.  On this box that
    was 2 of the 15 panel geometries, and nothing said so.  A mock exists to
    cover the device space; covering it should not require remembering a flag.

    Real hardware is still reachable, now by asking for it (``--hardware``).
    That path is worth keeping — the harness can drive a plugged-in cooler with
    dev paths — but it is the exception, not what you get by forgetting.
    """
    from trcc.adapters.infra.logging import configure_logging

    # Specs → simulate that fleet with scripted USB on a real host base
    # (DevMockPlatform).  No specs at all → DevPlatform, which drives whatever
    # cooler is really plugged in.  Only ``--hardware`` reaches that now.
    if specs:
        source = "device= CLI"
    elif hardware:
        source = "--hardware (real attached device)"
        specs = None
    elif report_path:
        source = "--report"
        specs = load_device_specs(report_path)
    elif all_devices:
        # --all still means something now that the fleet is the default: it
        # OVERRIDES a local devices.json, so you can get full coverage without
        # moving your own file out of the way.
        source = "full fleet (--all)"
        specs = all_variant_specs()
    elif (local := load_device_specs(None)):
        source = "devices.json"
        specs = local
    else:
        source = "full fleet (default)"
        specs = all_variant_specs()
    platform = _build_dev_platform(specs or None)

    # Say what was NOT covered.  The old banner named the source and stopped
    # there, so a 3-device devices.json read as "the mock" rather than as 2 of
    # 15 geometries.  A harness that reports what it looked at owes you what it
    # skipped — the same failing that let 95% audit coverage mean one binary
    # out of three.
    if specs and not source.startswith("full fleet"):
        full = len(all_variant_specs())
        if len(specs) < full:
            log.info("fleet: %d of %d simulable device(s) — run without "
                     "--hardware/--report and with no dev/devices.json for "
                     "the full fleet", len(specs), full)

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
        # Mirror ``ui/cli/main:_root`` here too.  Omitting this left the
        # per-frame family at its INFO default in BOTH directions, so a mock
        # run measured 4,497 records at default and 4,557 with -v -- the gate
        # looked like it did nothing, when it was simply never switched on.
        per_frame=verbosity > 0,
    )
    log.info(
        "dev bootstrap: platform=%s paths.config=%s source=%s specs=%d",
        type(platform).__name__, platform.paths().config_dir(), source,
        len(specs or []),          # --hardware leaves specs None on purpose
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

"""Sensors, diagnostics, setup, config, snapshots, update, slideshow, platform Commands."""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from .._version import is_newer
from ..errors import (
    DeviceNotConnectedError,
    DeviceNotFoundError,
    HttpFetchError,
)
from ..events import (
    DateFormatChanged,
    GpuDeviceChanged,
    LanguageChanged,
    RefreshIntervalChanged,
    TempUnitChanged,
    TimeFormatChanged,
)
from ..models import (
    AUTOSTART_TARGETS,
    MAX_REFRESH_INTERVAL_S,
    MIN_REFRESH_INTERVAL_S,
    PanelConfig,
    SensorBinding,
)
from ..results import (
    AutostartResult,
    ControlCenterSnapshotResult,
    DaemonResult,
    DateFormatResult,
    DebugReportPayload,
    DiskDeviceResult,
    DiskEntry,
    DiskSensorEntry,
    DiskSensorsResult,
    DisksListResult,
    DoctorResultPayload,
    FanEntry,
    FansListResult,
    FirstRunStatusResult,
    FontsListResult,
    GpuDeviceResult,
    GpuEntry,
    GpusListResult,
    HealthReportResult,
    KeepaliveResult,
    LanguageEntry,
    LanguageResult,
    LanguagesListResult,
    MemorySlotEntry,
    MemorySlotsResult,
    PathsResult,
    PlatformInfoResult,
    QuickstartResult,
    QuickstartStepEntry,
    RefreshIntervalResult,
    SensorDashboardResult,
    SensorInfoEntry,
    SensorsListResult,
    SensorsResult,
    SetupResult,
    SlideshowAdvanceResult,
    SlideshowResult,
    TempUnitResult,
    TimeFormatResult,
    UpdateCheckResult,
    UpgradeResult,
)
from ._base import Command, Query
from ._helpers import (
    _autostart_path,
    _drive_slideshow,
    _health_entries,
    _publish_slideshow,
    _require_connected_device,
    _resolve_oriented_resolution,
    _slideshow_snapshot,
)
from .theme import RestoreDeviceState

if TYPE_CHECKING:
    from ...app import App

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SetTimeFormat(Command[TimeFormatResult]):
    """Set the LCD-overlay clock format (12h or 24h).

    ``key=None`` (the default) sets the global ``AppSettings.time_format``
    and fans out to every device; a specific ``key`` sets just that
    device (per-device override).  Either way one
    :class:`TimeFormatChanged` is published per affected device so
    ``DeviceRenderObserver`` re-renders immediately.

    Distinct from :class:`SetClockFormat` (LED-segment LC2-style
    displays, which write ``led_clock_24h``).
    """
    fmt: str   # "12h" or "24h"
    key: str | None = None

    def execute(self, app: App) -> TimeFormatResult:
        log.info("SetTimeFormat.execute: fmt=%s key=%s", self.fmt, self.key)
        if self.fmt not in ("12h", "24h"):
            log.warning("SetTimeFormat.execute: invalid fmt %r", self.fmt)
            return TimeFormatResult(
                ok=False, key=self.key or "", fmt=self.fmt,
                message=f"fmt must be '12h' or '24h', got {self.fmt!r}",
            )
        if self.key is None:
            keys = app.settings.set_global_time_format(self.fmt)  # type: ignore[arg-type]
            scope = f"global ({len(keys)} device(s))"
        else:
            app.settings.set_time_format(self.key, self.fmt)  # type: ignore[arg-type]
            keys = [self.key]
            scope = self.key
        for key in keys:
            app.display.invalidate(key)
            app.events.publish(TimeFormatChanged(key=key, fmt=self.fmt))
        return TimeFormatResult(
            ok=True, key=self.key or "", fmt=self.fmt,
            message=f"time format set to {self.fmt} for {scope}",
        )

@dataclass(frozen=True, slots=True)
class SetDateFormat(Command[DateFormatResult]):
    """Set the LCD-overlay date pattern.

    ``key=None`` (the default) sets the global default + fans out to
    every device; a specific ``key`` sets just that device.  Pattern
    uses ICU-ish tokens (``yyyy/MM/dd``, ``dd.MM.yyyy``) translated by
    ``_clock._translate_date_pattern`` to a Python strftime string.
    """
    fmt: str
    key: str | None = None

    def execute(self, app: App) -> DateFormatResult:
        log.info("SetDateFormat.execute: fmt=%r key=%s", self.fmt, self.key)
        if not self.fmt:
            log.warning("SetDateFormat.execute: empty fmt")
            return DateFormatResult(
                ok=False, key=self.key or "", fmt=self.fmt,
                message="fmt must not be empty",
            )
        if self.key is None:
            keys = app.settings.set_global_date_format(self.fmt)
            scope = f"global ({len(keys)} device(s))"
        else:
            app.settings.set_date_format(self.key, self.fmt)
            keys = [self.key]
            scope = self.key
        for key in keys:
            app.display.invalidate(key)
            app.events.publish(DateFormatChanged(key=key, fmt=self.fmt))
        return DateFormatResult(
            ok=True, key=self.key or "", fmt=self.fmt,
            message=f"date format set to {self.fmt!r} for {scope}",
        )

@dataclass(frozen=True, slots=True)
class ListFonts(Query[FontsListResult]):
    """List font families the renderer can draw with.

    Delegates to the injected ``Renderer`` port (``app.renderer.list_fonts``) —
    the same source the GUI font picker reads.  Core never imports a GUI
    toolkit; the Qt-specific font enumeration (and its offscreen-QGuiApplication
    bootstrap) lives in ``QtRenderer``.  Returns an empty list (not an error)
    when no renderer is attached, so headless callers can probe safely.
    """

    def execute(self, app: App) -> FontsListResult:
        try:
            renderer = app.renderer
        except RuntimeError:
            log.info("ListFonts.execute: no renderer attached — empty list")
            return FontsListResult(
                ok=True, fonts=[],
                message="no renderer — no fonts enumerable",
            )
        fonts = renderer.list_fonts()
        log.info("ListFonts.execute: %d font(s)", len(fonts))
        return FontsListResult(
            ok=True, fonts=fonts,
            message=f"{len(fonts)} font(s)",
        )

@dataclass(frozen=True, slots=True)
class ListDisks(Query[DisksListResult]):
    """Enumerate mounted PARTITIONS — a filesystem view, not a sensor one.

    Partitions, not drives: one physical disk supplies several, so this list
    is longer than ``Platform.disk_info()``'s and the two share no key.

    Reaches the filesystem through ``app.platform``.  It used to
    ``import psutil`` in this module — the only hardware probe imported
    anywhere in ``core`` or ``services``, and the hexagon says core consumes
    the port instead.  The probe itself did not move far (``BaseOS`` carries
    the one shared body); what changed is which side of the boundary it sits.
    """

    def execute(self, app: App) -> DisksListResult:
        partitions = app.platform.disk_partitions()
        disks = [
            DiskEntry(index=index, device=device, mountpoint=mountpoint)
            for index, (device, mountpoint) in enumerate(partitions)
        ]
        log.debug("ListDisks.execute: %d disk(s): %s",
                  len(disks), [d.device for d in disks])
        return DisksListResult(
            ok=True, disks=disks,
            message=f"{len(disks)} disk(s)",
        )

@dataclass(frozen=True, slots=True)
class ListDiskSensors(Query[DiskSensorsResult]):
    """Every drive thermal sensor, with its current reading and the active pick.

    **This is the list a disk picker must show**, because it is the list the
    ``disk_temp`` metric comes from.  ``ListDisks`` enumerates psutil
    PARTITIONS and ``Platform.disk_info()`` enumerates PHYSICAL drives; on a
    typical box those are three different lengths with no shared key, so a
    picker fed by either cannot address what the LED displays.
    """

    def execute(self, app: App) -> DiskSensorsResult:
        sensors = app.platform.sensors()
        disks = [
            DiskSensorEntry(key=d.key, name=d.name, temp=d.temp())
            for d in sensors.disks()
        ]
        active = app.settings.app.active_disk
        log.info("ListDiskSensors.execute: %d sensor(s), active=%s",
                 len(disks), active or "(hottest)")
        return DiskSensorsResult(
            ok=True, disks=disks, active=active,
            message=f"{len(disks)} disk sensor(s)",
        )


@dataclass(frozen=True, slots=True)
class SetDiskDevice(Command[DiskDeviceResult]):
    """Pin which drive supplies ``disk_temp`` (empty string = hottest).

    Mirrors :class:`SetGpuDevice`, including the TWO-HOP that matters: persist
    the choice AND push it into the live enumerator.  Omitting the second hop
    is precisely the bug ``disk_index`` had — the value was saved, reported
    back correctly, and the metric ignored it.
    """
    disk_key: str

    def execute(self, app: App) -> DiskDeviceResult:
        normalized: str | None = self.disk_key.strip() or None
        app.settings.set_active_disk(normalized)
        app.platform.sensors().set_preferred_disk(normalized)
        log.info("SetDiskDevice.execute: active disk -> %s",
                 normalized or "(hottest)")
        return DiskDeviceResult(
            ok=True, disk_key=normalized,
            message=(f"disk_temp now follows {normalized}" if normalized
                     else "disk_temp follows the hottest drive"),
        )


@dataclass(frozen=True, slots=True)
class ListMemorySlots(Query[MemorySlotsResult]):
    """Enumerate DRAM slots — identity on every OS, timings on Linux.

    The LC1-style memory panel reads the six timing fields; the identity
    fields answer "what DIMMs does this box have", which ``trcc report``
    could not show at all before this existed.

    Field availability is per-OS by nature (only Linux enriches with SPD/IMC
    timings), so an absent value arrives as ``""`` and a UI renders "NC" —
    the convention ``Platform.memory_info`` already documents for the whole
    list.
    """

    def execute(self, app: App) -> MemorySlotsResult:
        raw = app.platform.memory_info()
        slots = [
            MemorySlotEntry(**{
                f.name: str(d.get(f.name, ""))
                for f in dataclasses.fields(MemorySlotEntry)
            })
            for d in raw
        ]
        log.info("ListMemorySlots.execute: %d slot(s)", len(slots))
        return MemorySlotsResult(
            ok=True, slots=slots,
            message=f"{len(slots)} memory slot(s)",
        )


@dataclass(frozen=True, slots=True)
class ListGpus(Query[GpusListResult]):
    """Enumerate every GPU the sensors aggregator exposes."""

    def execute(self, app: App) -> GpusListResult:
        sensors = app.platform.sensors()
        # `gpus()` is the SensorEnumerator port method — every enumerator
        # implements it and returns ``list[GpuSource]`` (empty if none).
        # The old `getattr(sensors, "_gpus") or getattr(sensors, "gpus")`
        # grabbed the bound METHOD as the fallback whenever `_gpus` was
        # empty, then tried to iterate it → "'method' object is not
        # iterable" crash on any machine with no GpuSource discovered.
        gpus = [
            GpuEntry(key=g.key, name=g.name, is_discrete=g.is_discrete)
            for g in sensors.gpus()
        ]
        if not gpus:
            log.warning("ListGpus.execute: enumerator exposed NO GPU — "
                        "metrics keyed on the GPU slot will read zero")
        else:
            log.debug("ListGpus.execute: %d GPU(s): %s", len(gpus),
                      [(g.key, g.is_discrete) for g in gpus])
        return GpusListResult(
            ok=True, gpus=gpus,
            message=f"{len(gpus)} GPU(s) detected",
        )

@dataclass(frozen=True, slots=True)
class ListFans(Query[FansListResult]):
    """Enumerate every fan the sensors aggregator exposes, with live readings.

    Read-only diagnostic (#145/#207): Linux has no reliable ``fanN_label``, so
    ``snapshot()`` fills the theme's CPU/SSD/SYS2 slots from the device's fans
    automatically and the GPU slot follows the picked GPU.  This surfaces the
    raw fan list for debugging "which fans does my box even expose".
    """

    def execute(self, app: App) -> FansListResult:
        sensors = app.platform.sensors()
        fans = [
            FanEntry(key=f.key, name=f.name, rpm=f.rpm(), percent=f.percent())
            for f in sensors.fans()
        ]
        log.debug("ListFans.execute: %d fan(s): %s", len(fans),
                  [(f.key, f.rpm, f.percent) for f in fans])
        return FansListResult(
            ok=True, fans=fans,
            message=f"{len(fans)} fan(s) detected",
        )

@dataclass(frozen=True, slots=True)
class ControlCenterSnapshot(Query[ControlCenterSnapshotResult]):
    """App-wide settings snapshot.

    Polled by UIs to refresh state — logged at DEBUG.
    """

    def execute(self, app: App) -> ControlCenterSnapshotResult:
        a = app.settings.app
        log.debug("ControlCenterSnapshot.execute: lang=%s unit=%s gpu=%s "
                  "refresh=%ss hdd=%s",
                  a.language, a.temp_unit, a.active_gpu,
                  a.refresh_interval_s, a.hdd_enabled)
        return ControlCenterSnapshotResult(
            ok=True,
            language=a.language,
            temp_unit=a.temp_unit,
            active_gpu=a.active_gpu,
            refresh_interval_s=a.refresh_interval_s,
            hdd_enabled=a.hdd_enabled,
            message="App settings snapshot",
        )

@dataclass(frozen=True, slots=True)
class ReadSensors(Query[SensorsResult]):
    """Return current sensor readings — personalized to user prefs.

    Pulls descriptor metadata (label / unit / category) from
    ``discover()`` and fresh values from ``read_all()``, applies user
    prefs through :func:`metrics_personalize.personalize_readings`,
    then merges so every returned ``SensorReading`` carries the
    personalized value.

    Same conversion + filter path as ``MetricsLoop._publish_once`` —
    one-shot callers (CLI / API / tests / GUI view-switch one-shot)
    receive the same shape the periodic broadcast carries.  When the
    user disables HDD, ``disk:*`` readings are excluded entirely
    (matches legacy's ``_populated.discard`` semantics); when the
    user picks °F, temp readings carry °F values AND ``unit="°F"``
    (so callers that key off ``.unit`` don't have to know temp_unit
    separately).

    Polled per refresh tick — logged at DEBUG so a default INFO run
    isn't drowned.
    """


    def execute(self, app: App) -> SensorsResult:
        from ...services.metrics_personalize import (
            personalize_metrics,
            personalize_readings,
            personalize_unit,
        )
        from ..models import SensorReading

        enum = app.platform.sensors()
        descriptors = enum.discover()
        raw = enum.read_all()
        s = app.settings.app
        personalized = personalize_readings(
            raw,
            temp_unit=s.temp_unit,
            hdd_enabled=s.hdd_enabled,
        )
        # Typed snapshot from the SAME enumerator, personalized the same
        # way — one-shot callers (GUI view-switch) get the typed object
        # the periodic broadcast carries, so periodic + one-shot agree.
        metrics = personalize_metrics(
            enum.snapshot(),
            temp_unit=s.temp_unit,
            hdd_enabled=s.hdd_enabled,
        )
        # Filter descriptors to only those that survived
        # personalization (HDD-disable drops disk:* keys entirely so
        # callers don't see them at value=0).  The unit SYMBOL comes from
        # ``personalize_unit`` rather than a rule spelled here: the GUIs
        # cache this catalog and refresh only its VALUES from the
        # broadcast, so both paths have to label a reading identically or
        # one of them renders "122.0 °C".
        readings: list[SensorReading] = [
            SensorReading(
                sensor_id=d.sensor_id,
                category=d.category,
                value=personalized[d.sensor_id],
                unit=personalize_unit(d.sensor_id, d.unit,
                                      temp_unit=s.temp_unit),
                label=d.label,
            )
            for d in descriptors if d.sensor_id in personalized
        ]
        # The gap between descriptors and readings is where a metric goes
        # missing, and it is normal (hdd_enabled=False drops every disk:* key)
        # right up until it is not.  Counts at DEBUG; nothing surviving at all
        # is a fault and says so with the keys that WERE discovered, so a
        # reporter can spot the mismatch without another round-trip.
        if descriptors and not readings:
            log.warning("ReadSensors.execute: %d descriptor(s) discovered but "
                        "NONE survived personalisation — discovered=%s",
                        len(descriptors), [d.sensor_id for d in descriptors][:20])
        else:
            log.debug("ReadSensors.execute: %d descriptor(s) -> %d reading(s) "
                      "(unit=%s hdd=%s), %d dropped",
                      len(descriptors), len(readings), s.temp_unit,
                      s.hdd_enabled, len(descriptors) - len(readings))
        return SensorsResult(
            ok=True,
            message=f"{len(readings)} sensor(s)",
            readings=readings,
            metrics=metrics,
        )

@dataclass(frozen=True, slots=True)
class ListSensors(Query[SensorsListResult]):
    """Enumerate every sensor the platform knows — descriptors only.

    Distinct from :class:`ReadSensors` which carries fresh values.
    Use this for sensor-picker dropdowns / API discovery endpoints
    that only need ``(sensor_id, category, unit, label)`` and don't
    want to pay the polling cost.  No personalisation: returns the
    raw sensor identity regardless of user prefs (hdd_enabled,
    temp_unit) — those affect *values*, not which sensors exist.
    """

    def execute(self, app: App) -> SensorsListResult:
        log.info("ListSensors.execute")
        descriptors = app.platform.sensors().discover()
        entries = [
            SensorInfoEntry(
                sensor_id=d.sensor_id,
                category=d.category,
                unit=d.unit,
                label=d.label,
            )
            for d in descriptors
        ]
        return SensorsListResult(
            ok=True,
            sensors=entries,
            message=f"{len(entries)} sensor(s) registered",
        )

def _copy_panels(panels: list[PanelConfig]) -> list[PanelConfig]:
    """Deep-copy the dashboard layout so a caller cannot write through it.

    ``app.sysinfo.panels`` is App state.  Handing the live objects to a UI
    would let a widget rebind a row by assignment and never dispatch, which
    is precisely the bypass this pair of Commands exists to remove — and it
    would only work in-process, since the daemon socket hands back fresh
    objects rebuilt from JSON.  Copying makes both paths behave identically.
    """
    log.debug("_copy_panels: panels=%d", len(panels))
    return [
        PanelConfig(
            category_id=p.category_id,
            name=p.name,
            sensors=[
                SensorBinding(label=b.label, sensor_id=b.sensor_id, unit=b.unit)
                for b in p.sensors
            ],
        )
        for p in panels
    ]

@dataclass(frozen=True, slots=True)
class GetSensorDashboard(Query[SensorDashboardResult]):
    """Read the sensor-dashboard layout, auto-mapping any unbound row.

    The legacy ``UCSystemInfoOptions`` grid — 4-row panels (CPU / GPU /
    Memory / Disk / Network / Fan + custom) each bound to a sensor id,
    persisted as ``<config_dir>/system_config.json``.  Until this Command
    existed the GUI imported the persistence adapter directly, so cli / api /
    qtgui could not read the file at all.

    Auto-mapping runs HERE rather than in each UI, from
    :meth:`SensorEnumerator.discover` — every sensor on the host, with
    values, unfiltered by user prefs.  Both halves matter:

    * **Unfiltered**, because auto-map answers "what exists on this box", not
      "what does the user want shown".  A personalised read drops ``disk:*``
      for a user who merely turned the Disk panel off, and the four disk rows
      would then silently bind to nothing.
    * **With values**, because the fan fallback orders candidates by whether
      they are spinning, and that ordering IS the #145 fix — a super-I/O chip
      publishes every header whether or not a fan is plugged into it.

    **Pure read: it does not save.**  What comes back may therefore differ
    from what is on disk (``auto_mapped`` says by how many rows); persisting
    it is :class:`SetSensorDashboard`'s job, and the UI dispatches that when
    the user actually edits something.  The upshot is that auto-mapping
    re-derives every launch, so a dashboard heals itself when a fan is
    replaced or a drive appears, instead of staying frozen at whatever the
    very first run happened to see.
    """

    def execute(self, app: App) -> SensorDashboardResult:
        log.info("GetSensorDashboard.execute")
        app.sysinfo.load()
        auto_mapped = app.sysinfo.auto_map(app.platform.sensors().discover())
        panels = _copy_panels(app.sysinfo.panels)
        unbound = sum(
            1 for p in panels for b in p.sensors if not b.sensor_id
        )
        # An unbound row renders "--" forever, and the reason is always
        # host-specific (no DDR5 SPD temp, no SMART disk temp, fewer fan
        # headers than slots).  Name them, or the next report says only
        # "some rows are blank".
        if unbound:
            log.info(
                "GetSensorDashboard.execute: %d row(s) unbound on this host: %s",
                unbound,
                [f"{p.name}/{i}" for p in panels
                 for i, b in enumerate(p.sensors) if not b.sensor_id],
            )
        return SensorDashboardResult(
            ok=True,
            panels=panels,
            auto_mapped=auto_mapped,
            message=(f"{len(panels)} panel(s), {auto_mapped} row(s) auto-mapped, "
                     f"{unbound} unbound"),
        )

@dataclass(frozen=True, slots=True)
class SetSensorDashboard(Command[SensorDashboardResult]):
    """Replace the sensor-dashboard layout wholesale and persist it.

    One bulk verb rather than four (rebind / add / delete / rename), matching
    the ``SetOverlayConfig`` precedent: the UI already holds the whole layout
    it just edited, and a per-row verb would need a panel address that
    ``PanelConfig`` does not carry (custom panels share ``category_id=0``).

    Rows may be left unbound — ``sensor_id=""`` is how the user says "nothing
    here", and the next :class:`GetSensorDashboard` will offer to auto-map it
    again.
    """
    panels: tuple[PanelConfig, ...] = ()

    def execute(self, app: App) -> SensorDashboardResult:
        log.info("SetSensorDashboard.execute: panels=%d", len(self.panels))
        if not self.panels:
            # Saving an empty list would make ``load`` fall back to defaults
            # on the next read — a wipe dressed up as a write.  Refuse it.
            log.warning("SetSensorDashboard.execute: refusing an empty layout")
            return SensorDashboardResult(
                ok=False, panels=_copy_panels(app.sysinfo.panels),
                message="A dashboard needs at least one panel",
            )
        app.sysinfo.panels = _copy_panels(list(self.panels))
        app.sysinfo.save()
        bound = sum(1 for p in app.sysinfo.panels
                    for b in p.sensors if b.sensor_id)
        log.info("SetSensorDashboard.execute: saved %d panel(s), %d bound row(s) → %s",
                 len(app.sysinfo.panels), bound, app.sysinfo.path)
        return SensorDashboardResult(
            ok=True,
            panels=_copy_panels(app.sysinfo.panels),
            message=f"Saved {len(app.sysinfo.panels)} panel(s), {bound} bound row(s)",
        )

@dataclass(frozen=True, slots=True)
class DaemonStatus(Query[DaemonResult]):
    """Is the background daemon running, and where is its socket?

    The daemon is the process that owns USB, polls the sensors and drives the
    render loop.  Every UI depends on that being exactly ONE process — two
    would poll the hardware twice and fight over the same handles — yet only
    cli and api could ask, by importing ``ipc.daemon_running`` directly.

    Cheap: a connect-test against the socket, no dispatch.
    """

    def execute(self, app: App) -> DaemonResult:
        del app
        import os

        from ...daemon import is_this_process_the_daemon, uptime_s
        from ...ipc import daemon_running, socket_path

        running = daemon_running()
        path = socket_path()
        # "A daemon is running" and "I am it" are different facts.  Reporting
        # the caller's pid for the daemon's sends an ops script to signal the
        # wrong process; 0 says "ask the daemon" and dispatching this over the
        # socket does exactly that, because it then executes there.
        am_daemon = is_this_process_the_daemon()
        pid = os.getpid() if am_daemon else 0
        uptime = uptime_s()
        from ...__version__ import __version__
        log.info("DaemonStatus.execute: running=%s am_daemon=%s pid=%d "
                 "uptime=%ds socket=%s version=%s", running, am_daemon, pid,
                 uptime, path, __version__)
        return DaemonResult(
            ok=True, running=running, socket_path=str(path),
            pid=pid, uptime_seconds=uptime, version=__version__,
            message=("Daemon is running" if running
                     else "No daemon is running"),
        )

@dataclass(frozen=True, slots=True)
class EnsureDaemon(Command[DaemonResult]):
    """Guarantee a daemon is reachable, starting one if it is not.

    The whole shape a UI wants at boot: *is the daemon up?  no -- create it;
    yes -- talk to it.*  Idempotent, so every UI can dispatch it
    unconditionally without checking first.

    **This is the lever for CPU.**  Today each UI that starts builds its own
    ``App``: its own sensor poll, its own render pipeline, its own USB
    handles.  Two UIs open means two of everything for one machine.  One
    daemon and N thin clients means the work happens once no matter how many
    windows, terminals or REST callers are attached.

    Not a ``Platform`` capability -- the daemon is this application's own
    software, not an OS facility, so it lives on the bus with everything else.
    """
    timeout: float = 10.0

    def execute(self, app: App) -> DaemonResult:
        del app
        from ...daemon import ensure_daemon
        from ...ipc import daemon_running, socket_path

        was_running = daemon_running()
        log.info("EnsureDaemon.execute: running=%s — ensuring (timeout=%.1fs)",
                 was_running, self.timeout)
        # Through ensure_daemon even when one answers: it replaces an App
        # older than this install.
        started = ensure_daemon(timeout=self.timeout)
        if was_running and started:
            return DaemonResult(
                ok=True, running=True, spawned=False,
                socket_path=str(socket_path()),
                message="Daemon already running",
            )
        if not started:
            # A UI that silently carries on in-process here would double the
            # hardware work and never say so.
            log.warning("EnsureDaemon.execute: spawn did not come up within "
                        "%.1fs", self.timeout)
        return DaemonResult(
            ok=started, running=started, spawned=started,
            socket_path=str(socket_path()),
            message=("Started the background daemon" if started
                     else f"Daemon did not come up within {self.timeout:.0f}s"),
        )

@dataclass(frozen=True, slots=True)
class StopDaemon(Command[DaemonResult]):
    """Ask a running daemon to shut down.

    Idempotent: no daemon is a successful outcome, not an error, because the
    caller's intent ("there should be no daemon") is already satisfied.
    """
    timeout: float = 5.0

    def execute(self, app: App) -> DaemonResult:
        del app
        from ...daemon import kill_daemon
        from ...ipc import socket_path

        log.info("StopDaemon.execute: timeout=%.1fs", self.timeout)
        stopped = kill_daemon(timeout=self.timeout)
        if not stopped:
            log.warning("StopDaemon.execute: daemon still reachable after "
                        "%.1fs", self.timeout)
        return DaemonResult(
            ok=stopped, running=not stopped, socket_path=str(socket_path()),
            message=("No daemon is running" if stopped
                     else f"Daemon still running after {self.timeout:.0f}s"),
        )

@dataclass(frozen=True, slots=True)
class RunSetup(Command[SetupResult]):
    """OS-specific one-time setup (udev, WinUSB guide, etc.).

    ``dry_run`` prints what would be done and changes nothing.  It was
    ``interactive`` (inverted) until 2026-09-10 — see ``Platform.setup``.
    """
    dry_run: bool = False

    def execute(self, app: App) -> SetupResult:
        # Check permissions AFTER setup so the warnings reflect what setup just
        # installed — not the pre-install state.  Snapshotting before setup
        # made a first run report "udev rules not installed" right next to
        # "exit code 0" (legacy's run_setup checks-then-installs, never reports
        # a pre-install warning as a result).
        code = app.platform.setup(dry_run=self.dry_run)
        warnings = app.platform.check_permissions()
        log.info("RunSetup.execute: exit=%d, %d permission warning(s): %s",
                 code, len(warnings), warnings)
        return SetupResult(
            ok=code == 0,
            message=f"Setup completed with exit code {code}",
            exit_code=code,
            warnings=warnings,
        )

@dataclass(frozen=True, slots=True)
class RunHealthCheck(Query[HealthReportResult]):
    """Run the full health check suite and return the structured report.

    Cheap (every check times out fast) — safe to call from a GUI panel
    on a refresh button.
    """

    def execute(self, app: App) -> HealthReportResult:
        report = app.diagnostics.health()
        log.info("RunHealthCheck.execute: %d check(s), %d fail, %d warn, worst=%s",
                 len(report.checks), report.fail_count, report.warn_count,
                 report.worst_severity)
        return HealthReportResult(
            ok=report.fail_count == 0,
            checks=_health_entries(report.checks),
            fail_count=report.fail_count,
            warn_count=report.warn_count,
            worst_severity=report.worst_severity,
            message=(f"{report.fail_count} fail / {report.warn_count} warn"
                     f" / {len(report.checks)} checks"),
        )

@dataclass(frozen=True, slots=True)
class RunDoctor(Query[DoctorResultPayload]):
    """Run health checks + render a CLI-friendly summary + exit code."""

    def execute(self, app: App) -> DoctorResultPayload:
        doctor = app.diagnostics.doctor()
        log.info("RunDoctor.execute: healthy=%s fail=%d warn=%d exit=%d",
                 doctor.is_healthy, doctor.report.fail_count,
                 doctor.report.warn_count, doctor.exit_code)
        return DoctorResultPayload(
            ok=doctor.is_healthy,
            checks=_health_entries(doctor.report.checks),
            fail_count=doctor.report.fail_count,
            warn_count=doctor.report.warn_count,
            exit_code=doctor.exit_code,
            rendered=app.diagnostics.render_doctor(doctor.report),
            message=("Healthy" if doctor.is_healthy
                     else f"{doctor.report.fail_count} check(s) failed"),
        )

@dataclass(frozen=True, slots=True)
class GenerateDebugReport(Command[DebugReportPayload]):
    """Build a debug report bundle for the user to paste into a GitHub issue.

    With ``output_path`` set, the bundle lands on disk at that path and
    the rendered text comes back in the result.  With no output_path,
    the bundle is rendered into memory only — useful for the API to
    return the text body directly.
    """
    output_path: Path | None = None
    log_tail_lines: int = 1000

    def execute(self, app: App) -> DebugReportPayload:
        rendered = app.diagnostics.debug_report(self.log_tail_lines)
        out: str = ""
        if self.output_path is not None:
            try:
                written = app.diagnostics.write_debug_report(
                    rendered, self.output_path,
                )
            except OSError as e:
                log.warning("GenerateDebugReport.execute: write to %s failed: %s",
                            self.output_path, e)
                return DebugReportPayload(
                    ok=False, output_path=str(self.output_path),
                    rendered_text=rendered,
                    message=f"Generated report but write failed: {e}",
                )
            out = str(written)
        log.info("GenerateDebugReport.execute: %d char(s), %d log line(s), -> %s",
                 len(rendered), self.log_tail_lines, out or "(in-memory)")
        return DebugReportPayload(
            ok=True, output_path=out, rendered_text=rendered,
            message=(f"Wrote debug report to {out}" if out
                     else "Generated debug report (in-memory)"),
        )

@dataclass(frozen=True, slots=True)
class RunQuickstart(Command[QuickstartResult]):
    """Walk the new-user happy path: doctor → scan.

    Each step's outcome is returned as a structured entry so any UI
    renders the same sequence.  Stops at the first FAIL.  Doesn't
    attempt a hardware handshake on its own — callers decide whether
    to ``ConnectDevice`` on the first-found device based on user
    confirmation.
    """

    def execute(self, app: App) -> QuickstartResult:
        report = app.quickstart.run_all()
        log.info("RunQuickstart.execute: %d step(s), completed=%s, failed=%s",
                 len(report.steps), report.completed_ok,
                 report.failed_step.name if report.failed_step else None)
        steps = [
            QuickstartStepEntry(
                name=s.name, status=s.status,
                message=s.message, next_step_hint=s.next_step_hint,
            )
            for s in report.steps
        ]
        return QuickstartResult(
            ok=report.completed_ok or not report.failed_step,
            steps=steps,
            completed_ok=report.completed_ok,
            device_key=report.device_key_connected,
            message=(
                "Quickstart complete." if report.completed_ok
                else (
                    f"Quickstart stopped at: {report.failed_step.name}"
                    if report.failed_step
                    else "Quickstart finished with warnings."
                )
            ),
        )

@dataclass(frozen=True, slots=True)
class GetFirstRunStatus(Query[FirstRunStatusResult]):
    """Has trcc finished onboarding on this machine?

    GUI uses this on launch to decide whether to surface a welcome
    screen; CLI users see it via ``trcc system first-run-status``.
    """

    def execute(self, app: App) -> FirstRunStatusResult:
        log.debug("GetFirstRunStatus.execute: first_run=%s marker=%s",
                  app.first_run.is_first_run(), app.first_run.marker_path)
        return FirstRunStatusResult(
            ok=True,
            is_first_run=app.first_run.is_first_run(),
            marker_path=str(app.first_run.marker_path),
            message=(
                "Welcome — looks like this is your first run."
                if app.first_run.is_first_run()
                else "Setup already completed previously."
            ),
        )

@dataclass(frozen=True, slots=True)
class MarkFirstRunDone(Command[FirstRunStatusResult]):
    """Tell next/ the onboarding flow has been completed.

    GUI calls this after the welcome panel; CLI users almost never
    need to call it directly (the doctor / setup commands could choose
    to mark it, but we keep that intentional rather than implicit).
    """

    def execute(self, app: App) -> FirstRunStatusResult:
        app.first_run.mark_completed()
        log.info("MarkFirstRunDone.execute: marker written to %s",
                 app.first_run.marker_path)
        return FirstRunStatusResult(
            ok=True, is_first_run=False,
            marker_path=str(app.first_run.marker_path),
            message="First-run marker written.",
        )

@dataclass(frozen=True, slots=True)
class CheckForUpdate(Query[UpdateCheckResult]):
    """Ask GitHub Releases whether a newer trcc-linux is available.

    Network call — uses the App's shared HttpFetcher.  Comparison is
    coarse semver (X.Y.Z) tolerant of v-prefix and pre-release suffixes.
    """

    def execute(self, app: App) -> UpdateCheckResult:
        # `trcc/__init__` rebinds the name `__version__` from the submodule to
        # the version STRING, so this import yields a str, not a module.  The
        # old `getattr(mod, "__version__", "0.0.0")` therefore always fell
        # through to "0.0.0" and told every user, on every release, that an
        # update was available — including users already on the newest one.
        from ... import __version__ as local
        try:
            latest = app.github_releases.latest()
        except HttpFetchError as e:
            log.warning("CheckForUpdate.execute: fetch failed: %s", e)
            return UpdateCheckResult(
                ok=False, local_version=local,
                message=f"Update check failed: {e}",
            )
        available = is_newer(latest.version, local)
        log.info("CheckForUpdate.execute: local=%s latest=%s -> available=%s",
                 local, latest.version, available)
        msg = (
            f"Update available: {latest.tag} (you have {local})"
            if available
            else f"Up to date at {local}"
        )
        return UpdateCheckResult(
            ok=True,
            local_version=local,
            latest_version=latest.version,
            latest_tag=latest.tag,
            release_url=latest.html_url,
            update_available=available,
            message=msg,
        )

@dataclass(frozen=True, slots=True)
class RunUpgrade(Command[UpgradeResult]):
    """Run the OS package-manager upgrade for trcc-linux.

    Maps the detected package manager to the right command and spawns a
    subprocess.  We never pipe untrusted input — the argv is a fixed
    list per package manager.  ``dry_run=True`` returns the command
    without executing it so UIs can show the user what would run.
    """
    dry_run: bool = False

    def execute(self, app: App) -> UpgradeResult:
        import subprocess

        # pm is observed (the diagnostics port reports it, and now delegates to
        # the OS for the answer); the RECIPE comes from the OS itself, which
        # used to be a module table keyed on the probed string.
        pm = app.diagnostics.package_manager()
        if not pm:
            log.warning("RunUpgrade.execute: no package manager detected — "
                        "cannot self-upgrade on this system")
            return UpgradeResult(
                ok=False, package_manager="",
                message="No supported package manager detected on this system",
            )
        cmd = app.platform.upgrade_command()
        if not cmd:
            log.warning("RunUpgrade.execute: package manager %r has no upgrade "
                        "recipe", pm)
            return UpgradeResult(
                ok=False, package_manager=pm,
                message=f"No upgrade recipe for package manager {pm!r}",
            )
        log.info("RunUpgrade.execute: pm=%s cmd=%s", pm, cmd)
        if self.dry_run:
            return UpgradeResult(
                ok=True, package_manager=pm, command=list(cmd),
                message=f"Would run: {' '.join(cmd)}",
            )
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=600.0, check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
            return UpgradeResult(
                ok=False, package_manager=pm, command=list(cmd),
                message=f"Upgrade subprocess failed: {type(e).__name__}: {e}",
            )
        return UpgradeResult(
            ok=proc.returncode == 0,
            package_manager=pm,
            command=list(cmd),
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            message=(f"Upgrade completed (exit {proc.returncode})"
                     if proc.returncode == 0
                     else f"Upgrade failed (exit {proc.returncode})"),
        )

@dataclass(frozen=True, slots=True)
class AdvanceSlideshow(Command[SlideshowAdvanceResult]):
    """Advance a device's slideshow one tick; report the theme to load next.

    The config comes from ``DeviceSettings.slideshow_*`` — the thing
    ``ConfigureSlideshow`` persists — so the caller supplies a key and nothing
    else.  The gui used to build a ``SlideshowConfig`` from its own panel
    state and hand it to ``app.slideshow.advance`` directly, which made the
    panel a second source for a fact settings already owned, and put the only
    rotation driver inside one UI.

    ``theme_name is None`` is the ordinary answer — the interval has not
    elapsed.  The caller (``SlideshowDriver``) resolves the name and loads it
    with ``LoadTheme``.

    Per-tick, so logged at DEBUG.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG
    key: str

    def execute(self, app: App) -> SlideshowAdvanceResult:
        from ...services.slideshow import SlideshowConfig

        s = app.settings.for_device(self.key)
        if not s.slideshow_enabled or not s.slideshow_themes:
            log.debug("AdvanceSlideshow: %s not running (enabled=%s themes=%d)",
                      self.key, s.slideshow_enabled, len(s.slideshow_themes))
            return SlideshowAdvanceResult(
                ok=True, key=self.key, due=False, running=False,
                message="No slideshow configured",
            )
        if self.key in app.held:
            # Not due, so neither timer loads a theme over the push -- and the
            # cursor stays put rather than skipping a slide it never showed.
            log.debug("AdvanceSlideshow: %s held by a pushed frame", self.key)
            return SlideshowAdvanceResult(
                ok=True, key=self.key, due=False, running=True,
                message="Held by a pushed frame",
            )
        name = app.slideshow.advance(self.key, SlideshowConfig(
            enabled=True,
            interval_s=float(s.slideshow_interval_s),
            themes=list(s.slideshow_themes),
        ))
        if name is None:
            log.debug("AdvanceSlideshow: %s — interval not elapsed", self.key)
            return SlideshowAdvanceResult(
                ok=True, key=self.key, due=False, running=True,
                message="Not due yet",
            )
        log.info("AdvanceSlideshow: %s → next theme %r", self.key, name)
        return SlideshowAdvanceResult(
            ok=True, key=self.key, theme_name=name, due=True, running=True,
            message=f"Next theme: {name}",
        )


@dataclass(frozen=True, slots=True)
class ConfigureSlideshow(Command[SlideshowResult]):
    """Set the slideshow theme list + interval for a device.

    Either field can be omitted to leave it untouched.  Resets the
    SlideshowService cursor so the next tick picks up the new list
    from index 0.
    """
    key: str
    themes: tuple[str, ...] | None = None
    interval_s: float | None = None

    def execute(self, app: App) -> SlideshowResult:
        if self.interval_s is not None and self.interval_s < 1.0:
            log.warning("ConfigureSlideshow.execute: %s rejected interval %.3fs "
                        "(minimum 1s)", self.key, self.interval_s)
            return SlideshowResult(
                ok=False, key=self.key,
                message=(f"interval_s must be >= 1, got {self.interval_s}"),
            )
        log.info("ConfigureSlideshow.execute: %s themes=%s interval=%ss",
                 self.key,
                 len(self.themes) if self.themes is not None else "(unchanged)",
                 self.interval_s if self.interval_s is not None else "(unchanged)")
        app.settings.configure_slideshow(
            self.key,
            themes=list(self.themes) if self.themes is not None else None,
            interval_s=self.interval_s,
        )
        app.slideshow.reset(self.key)
        _publish_slideshow(app, self.key)
        return _slideshow_snapshot(app.settings, self.key)

@dataclass(frozen=True, slots=True)
class SetSlideshow(Command[SlideshowResult]):
    """Switch the slideshow on or off — and with it, the rotation.

    On means rotating: the driver is registered here, not by a second Command
    each UI paired by hand (``StartSlideshowDriver`` / ``StopSlideshowDriver``,
    deleted), which the gui never did -- it ran its own timer instead.  The
    driver also dispatches this, with ``enabled=False``, when another source
    takes the panel.
    """
    key: str
    enabled: bool

    def execute(self, app: App) -> SlideshowResult:
        log.info("SetSlideshow.execute: %s enabled=%s", self.key, self.enabled)
        app.settings.set_slideshow_enabled(self.key, self.enabled)
        if self.enabled:
            app.slideshow.reset(self.key)
        _drive_slideshow(app, self.key)
        _publish_slideshow(app, self.key)
        return _slideshow_snapshot(app.settings, self.key)

@dataclass(frozen=True, slots=True)
class KeepAliveLoop(Command[KeepaliveResult]):
    """Confirm or hold a device's screen keepalive.

    Bulk/LY firmware reverts to the logo after ~2-3 s without a fresh frame.
    The per-device send worker now keepalive-resends the cached frame
    automatically (intrinsic, ~150 ms) for as long as the App lives, so this
    Command no longer runs the resend loop itself — it verifies a frame is
    cached + the device connected, then:

      * ``count >= 1`` — returns immediately ("keepalive active"); the worker
        is already doing the work.  (API / one-shot.)
      * ``count == 0`` — blocks until ``KeyboardInterrupt`` so a headless CLI
        process (and its worker) stays alive.  ``trcc display keepalive <key>``.

    ``interval_s`` now only sets the block-loop sleep granularity;
    ``metric_interval_s`` is accepted for API compatibility but unused — the
    metrics observer already re-renders on its own cadence.
    """
    USES_DEVICE: ClassVar[bool] = True
    key: str
    count: int = 0
    interval_s: float = 0.150
    metric_interval_s: float = 1.0

    def execute(self, app: App) -> KeepaliveResult:
        import time

        if self.count < 0:
            log.warning("KeepAliveLoop.execute: %s rejected count=%d",
                        self.key, self.count)
            return KeepaliveResult(
                ok=False, key=self.key,
                message=f"count must be >= 0, got {self.count}",
            )
        # The device BEFORE the cache: a failed connect leaves no cache either,
        # and "No cached frame" hid the reason ("permission denied …").
        try:
            _require_connected_device(app, self.key)
        except (DeviceNotFoundError, DeviceNotConnectedError) as e:
            log.warning("KeepAliveLoop.execute: %s — %s", self.key, e)
            return KeepaliveResult(ok=False, key=self.key, message=str(e))
        sender = app.senders.get(self.key)
        if sender is None or sender.last() is None:
            # Nothing to resend: show the saved display once, and resend THAT.
            # A panel with a frame -- a push, a theme already showing -- is left
            # alone.  The CLI used to restore unconditionally before this, and
            # since RestoreDeviceState always restores, `trcc display keepalive`
            # threw a pushed image away in daemon mode (#267).  Deciding here
            # makes the CLI and the API the same one Command.
            log.info("KeepAliveLoop.execute: %s has nothing to resend — "
                     "showing its saved display first", self.key)
            restore = app.dispatch(RestoreDeviceState(key=self.key))
            if not restore.ok:
                return KeepaliveResult(ok=False, key=self.key,
                                       message=restore.message)
            sender = app.senders.get(self.key)
        if sender is None or sender.last() is None:
            log.warning("KeepAliveLoop.execute: %s has no cached frame "
                        "(sender=%s) — render once before keepalive",
                        self.key, "absent" if sender is None else "empty")
            return KeepaliveResult(
                ok=False, key=self.key,
                message=("No cached frame for keepalive — render at least "
                         "once first"),
            )

        if self.count >= 1:
            return KeepaliveResult(
                ok=True, key=self.key,
                message="Keepalive active — the send worker resends the frame",
            )
        # Foreground CLI form: the worker keeps the screen alive; we just keep
        # the process (and thus the worker) running until Ctrl-C.
        try:
            while True:
                time.sleep(max(0.05, self.interval_s))
        except KeyboardInterrupt:
            return KeepaliveResult(
                ok=True, key=self.key, message="Keepalive stopped",
            )

@dataclass(frozen=True, slots=True)
class GetPlatformInfo(Query[PlatformInfoResult]):
    """Snapshot of OS identity + paths + permission warnings.

    Used by diagnostic UIs (`trcc info`, GUI about panel).  Keeps UIs
    from reaching directly into `app.platform` — they dispatch this and
    render the Result like any other Command.
    """

    def execute(self, app: App) -> PlatformInfoResult:
        p = app.platform
        paths = p.paths()
        # One-shot and pure gold in a report: which distro, how it was
        # installed, and every path the app resolved to on THIS machine.
        log.info("GetPlatformInfo.execute: distro=%s install=%s config=%s "
                 "data=%s user=%s log=%s warnings=%s minimize_on_close=%s",
                 p.distro_name(), p.install_method(), paths.config_dir(),
                 paths.data_dir(), paths.user_content_dir(), paths.log_file(),
                 p.check_permissions(), p.minimize_on_close())
        return PlatformInfoResult(
            ok=True,
            message=f"Platform: {p.distro_name()}",
            distro_name=p.distro_name(),
            install_method=p.install_method(),
            config_dir=str(paths.config_dir()),
            data_dir=str(paths.data_dir()),
            user_content_dir=str(paths.user_content_dir()),
            log_file=str(paths.log_file()),
            permission_warnings=p.check_permissions(),
            no_devices_hint=p.no_devices_hint(),
            minimize_on_close=p.minimize_on_close(),
        )

@dataclass(frozen=True, slots=True)
class GetAutostartStatus(Query[AutostartResult]):
    """Report whether auto-launch-on-login is enabled."""

    def execute(self, app: App) -> AutostartResult:
        mgr = app.platform.autostart()
        enabled = mgr.is_enabled()
        path = _autostart_path(app)
        target = mgr.installed_target() or ""
        configured = app.settings.app.autostart_configured
        log.debug("GetAutostartStatus.execute: enabled=%s target=%s path=%s "
                  "configured=%s", enabled, target, path, configured)
        return AutostartResult(
            ok=True,
            message=(f"enabled ({target})" if enabled and target
                     else "enabled" if enabled else "disabled"),
            enabled=enabled, path=path, target=target, configured=configured,
        )

@dataclass(frozen=True, slots=True)
class EnableAutostart(Command[AutostartResult]):
    """Install the OS-specific autostart entry (per-user, no sudo).

    ``target`` names WHICH ui login brings up — ``None`` keeps the platform
    default.  All four ship, so autostart that could only ever launch ``gui``
    made the other three unreachable at login.
    """
    target: str | None = None

    def execute(self, app: App) -> AutostartResult:
        # Validated HERE, not in the adapter: ``autostart_argv`` raises
        # KeyError for an unknown target, and a KeyError reaching a UI is not
        # an answer.  A Result naming the valid set is.
        if self.target is not None and self.target not in AUTOSTART_TARGETS:
            log.warning("EnableAutostart: unknown target %r", self.target)
            return AutostartResult(
                ok=False, enabled=False,
                message=(f"unknown autostart target {self.target!r} — "
                         f"expected one of {sorted(AUTOSTART_TARGETS)}"),
            )
        mgr = app.platform.autostart()
        mgr.enable(self.target)
        app.settings.mark_autostart_configured()
        installed = mgr.installed_target() or ""
        log.info("EnableAutostart.execute: enabled=%s target=%s at %s",
                 mgr.is_enabled(), installed, _autostart_path(app))
        return AutostartResult(
            ok=True, message=f"autostart enabled ({installed or 'default'})",
            enabled=mgr.is_enabled(), path=_autostart_path(app),
            target=installed, configured=True,
        )

@dataclass(frozen=True, slots=True)
class DisableAutostart(Command[AutostartResult]):
    """Remove the OS-specific autostart entry."""

    def execute(self, app: App) -> AutostartResult:
        mgr = app.platform.autostart()
        mgr.disable()
        app.settings.mark_autostart_configured()
        log.info("DisableAutostart.execute: now enabled=%s at %s",
                 mgr.is_enabled(), _autostart_path(app))
        return AutostartResult(
            ok=True, message="autostart disabled",
            enabled=mgr.is_enabled(), path=_autostart_path(app),
            configured=True,
        )

@dataclass(frozen=True, slots=True)
class RefreshAutostart(Command[AutostartResult]):
    """Re-render an existing autostart entry so it picks up a new launch path.

    The repair for #201: an entry written by an older install keeps its old
    ``Exec=`` forever, so the app autostarts from a path that may no longer
    exist.  ``refresh()`` re-renders it IN PLACE and is a no-op when no entry
    is installed — it never enables autostart the user did not ask for, which
    is what separates it from :class:`EnableAutostart`.

    Whether it does anything is per-OS and lives in the adapter — all three
    now re-render, because the command CAN change: ``--resume`` (#201) and the
    autostart target both alter it, and an entry written by an older install
    would otherwise keep its old launch line forever.  Each re-renders only
    when an entry already exists, and with the target ALREADY installed, so a
    repair never changes which ui the user chose.  It exists as a Command
    because ``refresh()`` was
    the one :class:`~trcc.core.ports.AutostartManager` method no Command
    reached, so the only caller able to repair a stale entry was the gui,
    holding the port directly.
    """

    def execute(self, app: App) -> AutostartResult:
        mgr = app.platform.autostart()
        mgr.refresh()
        enabled = mgr.is_enabled()
        log.info("RefreshAutostart.execute: enabled=%s at %s",
                 enabled, _autostart_path(app))
        return AutostartResult(
            ok=True,
            message="autostart refreshed" if enabled else "no autostart entry to refresh",
            enabled=enabled, path=_autostart_path(app),
        )

@dataclass(frozen=True, slots=True)
class SetTempUnit(Command[TempUnitResult]):
    """Set the global temperature unit ("C" or "F") and propagate to every device.

    Cross-cutting setter — keeps AppSettings.temp_unit + every connected
    device's DeviceSettings.temp_unit in lockstep so overlay renderers
    see a consistent unit regardless of which device emits the next
    frame.
    """
    unit: str

    def execute(self, app: App) -> TempUnitResult:
        if self.unit not in ("C", "F"):
            log.warning("SetTempUnit.execute: rejected unit %r", self.unit)
            return TempUnitResult(
                ok=False, unit=self.unit,
                message=f"unit must be 'C' or 'F', got {self.unit!r}",
            )
        log.info("SetTempUnit.execute: %s (global)", self.unit)
        app.settings.set_global_temp_unit(self.unit)   # type: ignore[arg-type]
        app.events.publish(TempUnitChanged(unit=self.unit))
        return TempUnitResult(
            ok=True, unit=self.unit,
            message=f"temp unit set to {self.unit}",
        )

@dataclass(frozen=True, slots=True)
class ListLanguages(Query[LanguagesListResult]):
    """Enumerate every language code the i18n table supports.

    Pure read — no I/O.  UIs use the returned list to populate language
    pickers; CLI users see it via ``trcc system list-languages``.
    """

    def execute(self, app: App) -> LanguagesListResult:
        del app
        from ..i18n import LANGUAGE_NAMES, TRANSLATIONS
        entries: list[LanguageEntry] = []
        for code in sorted(LANGUAGE_NAMES):
            entries.append(LanguageEntry(
                code=code,
                name=LANGUAGE_NAMES[code],
                translated_keys=len(TRANSLATIONS.get(code, {})),
            ))
        log.debug("ListLanguages.execute: %d language(s): %s", len(entries),
                  [(e.code, e.translated_keys) for e in entries])
        return LanguagesListResult(
            ok=True, languages=entries,
            message=f"{len(entries)} language(s) registered",
        )

@dataclass(frozen=True, slots=True)
class SetLanguage(Command[LanguageResult]):
    """Set the UI language code (ISO 639-1, e.g. 'en', 'zh', 'fr').

    Validates against the i18n table so unknown codes are rejected with a
    structured error instead of silently persisting and breaking
    ``tr()`` lookups for every subsequent string.
    """
    language: str

    def execute(self, app: App) -> LanguageResult:
        from ..i18n import LANGUAGE_NAMES
        lang = self.language.strip()
        if not lang:
            log.warning("SetLanguage.execute: empty language code")
            return LanguageResult(
                ok=False, language=self.language,
                message="language code cannot be empty",
            )
        if lang not in LANGUAGE_NAMES:
            log.warning("SetLanguage.execute: unknown code %r — known: %s",
                        lang, sorted(LANGUAGE_NAMES))
            return LanguageResult(
                ok=False, language=self.language,
                message=(f"unknown language code {lang!r}; "
                         "use `system list-languages` to see supported codes"),
            )
        log.info("SetLanguage.execute: %s (%s)", lang, LANGUAGE_NAMES[lang])
        app.settings.set_language(lang)
        app.events.publish(LanguageChanged(language=lang))
        return LanguageResult(
            ok=True, language=lang,
            message=f"language set to {lang} ({LANGUAGE_NAMES[lang]})",
        )

@dataclass(frozen=True, slots=True)
class SetGpuDevice(Command[GpuDeviceResult]):
    """Pick the primary GPU by sensor key (e.g. 'nvidia:0', 'amd:0').

    Pass an empty string to clear the override and let
    ``SensorEnumerator.primary_gpu()`` pick automatically.
    """
    gpu_key: str

    def execute(self, app: App) -> GpuDeviceResult:
        normalized: str | None = self.gpu_key.strip() or None
        app.settings.set_active_gpu(normalized)
        # Push the choice into the live (singleton) enumerator so the metric
        # actually re-routes — the universal hop every UI shares.  Without
        # this the selection only persisted and primary_gpu() ignored it.
        app.platform.sensors().set_preferred_gpu(normalized)
        log.info("SetGpuDevice.execute: active gpu -> %s",
                 normalized or "(auto)")
        app.events.publish(GpuDeviceChanged(gpu_key=normalized))
        return GpuDeviceResult(
            ok=True, gpu_key=normalized,
            message=(f"active gpu set to {normalized}" if normalized
                     else "active gpu cleared (auto)"),
        )

@dataclass(frozen=True, slots=True)
class SetRefreshInterval(Command[RefreshIntervalResult]):
    """Set the global metrics-refresh interval — when metric data is polled
    and updated (the render reads the cached snapshot between polls).

    Validated to the GUI's data-refresh-rate range
    [``MIN_REFRESH_INTERVAL_S``, ``MAX_REFRESH_INTERVAL_S``] = [1, 100] s —
    below the minimum starves the CPU (sub-second polls + sends), above the
    maximum makes the metrics feel frozen.
    """
    seconds: float

    def execute(self, app: App) -> RefreshIntervalResult:
        log.info("SetRefreshInterval.execute: seconds=%.2f", self.seconds)
        if not MIN_REFRESH_INTERVAL_S <= self.seconds <= MAX_REFRESH_INTERVAL_S:
            log.warning(
                "SetRefreshInterval.execute: out-of-range %.2f rejected "
                "(allowed [%.1f, %.1f])", self.seconds,
                MIN_REFRESH_INTERVAL_S, MAX_REFRESH_INTERVAL_S,
            )
            return RefreshIntervalResult(
                ok=False, seconds=self.seconds,
                message=(f"refresh interval must be in "
                         f"[{MIN_REFRESH_INTERVAL_S}, {MAX_REFRESH_INTERVAL_S}] "
                         f"seconds, got {self.seconds}"),
            )
        old = app.settings.app.refresh_interval_s
        app.settings.set_refresh_interval(self.seconds)
        app.events.publish(RefreshIntervalChanged(seconds=self.seconds))
        log.info(
            "SetRefreshInterval.execute: settings.app.refresh_interval_s "
            "%.2f -> %.2f (RefreshIntervalChanged published)",
            old, self.seconds,
        )
        return RefreshIntervalResult(
            ok=True, seconds=self.seconds,
            message=f"refresh interval set to {self.seconds:.2f}s",
        )


@dataclass(frozen=True, slots=True)
class GetPaths(Query[PathsResult]):
    """Report where this install keeps things.  Read-only.

    Every UI needs a directory sometimes — stage an upload, open the log, point
    a picker at the user's backgrounds — and each one reached
    ``app.platform.paths()`` to get it.  That attribute does not exist on the
    ``AppProxy`` a daemon-mode UI holds, so all of it raised under
    ``TRCC_DAEMON=1`` (#249).  Asking the bus works in every mode.

    ``resolution`` fills the resolution-scoped fields (theme / mask /
    background / cloud dirs, which are per-canvas-size).  Omit it and those
    come back empty rather than guessed at.

    ``key`` names a DEVICE and makes the answer that device's own.  It does two
    things a bare resolution cannot: it supplies the resolution when none is
    given (oriented, so a portrait panel reports the directory it will really
    read), and it resolves the three LIBRARY dirs through that cooler's
    artwork libraries -- a SUB-3 1600x720 panel reads ``theme1600720l``.

    This is a diagnostic before it is anything else: users answer "where did my
    theme go?" from this output and paste it into issues.  Naming the generic
    library for a per-SKU cooler is not a small inaccuracy there, it points the
    reporter (and us) at a directory the app never opens.

    The three USER dirs stay generic on purpose -- the user's own art is one
    directory per resolution, with no per-SKU split.
    """
    resolution: tuple[int, int] | None = None
    key: str = ""

    def execute(self, app: App) -> PathsResult:
        p = app.platform.paths()
        resolution = self.resolution
        if self.key and app.devices.get(self.key) is None:
            # Answering generically here would look exactly like a correct
            # answer, which is how a caller that forgot to connect ends up
            # reading a directory the app never opens.  Say it instead.
            log.warning(
                "GetPaths: %s is not attached — reporting the GENERIC "
                "libraries, not that device's (connect it first)", self.key,
            )
        if resolution is None and self.key:
            resolution = _resolve_oriented_resolution(app, self.key)
            log.debug("GetPaths.execute: %s → resolution=%s", self.key,
                      resolution)
        scoped: dict[str, str] = {}
        if resolution is not None:
            w, h = resolution
            # Generic when key is "" (no device → no variant), so the
            # resolution-only answer is exactly what it has always been.
            libs = app.libraries(self.key)
            scoped = {
                "theme_dir": str(libs.theme_dir(w, h)),
                "user_theme_dir": str(p.user_theme_dir(w, h)),
                "user_mask_dir": str(p.user_mask_dir(w, h)),
                "user_background_dir": str(p.user_background_dir(w, h)),
                "cloud_theme_dir": str(libs.cloud_theme_dir(w, h)),
                "cloud_mask_dir": str(libs.cloud_mask_dir(w, h)),
            }
        log.debug("GetPaths.execute: resolution=%s key=%s scoped=%d",
                  resolution, self.key or "(none)", len(scoped))
        return PathsResult(
            ok=True,
            config_dir=str(p.config_dir()),
            data_dir=str(p.data_dir()),
            user_content_dir=str(p.user_content_dir()),
            user_data_dir=str(p.user_data_dir()),
            log_file=str(p.log_file()),
            uploads_dir=str(p.user_content_dir() / "uploads"),
            media_player_dir=str(p.user_media_player_dir()),
            screencast_dir=str(p.user_screencast_dir()),
            message=f"paths for {p.config_dir()}",
            **scoped,
        )

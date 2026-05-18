"""Commands — the universal UI contract.

Every user action is one Command class.  UIs build Commands, hand them
to App.dispatch, and render the returned Result.  Adding a new UI = new
adapter over the same Command classes.  Adding a new action = new
Command class.

Commands own their orchestration: they call services, talk to devices,
publish events, return a Result.  They are the business-logic layer
between UIs and the domain.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Generic, TypeVar

from .errors import (
    DeviceNotConnectedError,
    DeviceNotFoundError,
    HandshakeError,
    ThemeError,
    TransportError,
)
from .events import (
    BrightnessChanged,
    DeviceConnected,
    DeviceDisconnected,
    DeviceDiscovered,
    ErrorOccurred,
    FitModeChanged,
    FrameSent,
    GpuDeviceChanged,
    LanguageChanged,
    LedColorsChanged,
    OrientationChanged,
    OverlayChanged,
    RefreshIntervalChanged,
    SplitModeChanged,
    TempUnitChanged,
    ThemeLoaded,
)
from .models import FitMode
from .registry import find_product
from .results import (
    AutostartResult,
    BrightnessResult,
    ConnectResult,
    DisconnectResult,
    DiscoverResult,
    FitModeResult,
    GpuDeviceResult,
    LanguageResult,
    LedColorsResult,
    OrientationResult,
    OverlayResult,
    PlatformInfoResult,
    RefreshIntervalResult,
    RenderResult,
    Result,
    SendResult,
    SensorsResult,
    SetupResult,
    SplitModeResult,
    TempUnitResult,
    ThemeResult,
)

if TYPE_CHECKING:
    from ..app import App


# =========================================================================
# Base
# =========================================================================


R_co = TypeVar("R_co", bound=Result, covariant=True)


class Command(ABC, Generic[R_co]):
    """A user action.  Exactly one execute method; returns one Result.

    Parameterised on the concrete Result subclass so that
    ``app.dispatch(DiscoverDevices())`` is typed as ``DiscoverResult``,
    not the Result base — callers get the subclass's fields (products,
    readings, etc.) without casting.
    """

    @abstractmethod
    def execute(self, app: App) -> R_co: ...


# =========================================================================
# Discovery / connection
# =========================================================================


@dataclass(frozen=True, slots=True)
class DiscoverDevices(Command[DiscoverResult]):
    """List attached devices that match the product registry."""

    def execute(self, app: App) -> DiscoverResult:
        live = app.platform.scan_devices()
        products = []
        for info in live:
            product = find_product(info.vid, info.pid)
            if product is not None:
                products.append(product)
                app.events.publish(DeviceDiscovered(
                    key=info.key, product_name=product.product,
                ))
        return DiscoverResult(
            ok=True,
            message=f"{len(products)} device(s) found",
            products=products,
            devices=live,
        )


@dataclass(frozen=True, slots=True)
class ConnectDevice(Command[ConnectResult]):
    """Attach + handshake with a discovered device."""
    key: str

    def execute(self, app: App) -> ConnectResult:
        try:
            vid_str, pid_str = self.key.split(":")
            vid, pid = int(vid_str, 16), int(pid_str, 16)
        except ValueError:
            return ConnectResult(
                ok=False, key=self.key,
                message=f"Invalid device key: {self.key!r} (expected 'vvvv:pppp')",
            )

        try:
            device = app.attach(vid, pid)
        except DeviceNotFoundError as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="not_found",
                                             key=self.key))
            return ConnectResult(ok=False, key=self.key, message=str(e))

        try:
            handshake = device.connect()
        except (HandshakeError, TransportError) as e:
            app.detach(self.key)
            app.events.publish(ErrorOccurred(message=str(e), kind="handshake",
                                             key=self.key))
            return ConnectResult(ok=False, key=self.key, message=str(e))

        app.events.publish(DeviceConnected(
            key=self.key, resolution=handshake.resolution,
        ))
        return ConnectResult(
            ok=True, key=self.key,
            message=f"Connected: {handshake.resolution}",
            handshake=handshake,
        )


@dataclass(frozen=True, slots=True)
class DisconnectDevice(Command[DisconnectResult]):
    """Close the transport and drop the device."""
    key: str

    def execute(self, app: App) -> DisconnectResult:
        if self.key not in app.devices:
            return DisconnectResult(
                ok=False, key=self.key,
                message=f"Not attached: {self.key}",
            )
        app.detach(self.key)
        app.events.publish(DeviceDisconnected(key=self.key))
        return DisconnectResult(ok=True, key=self.key, message="Disconnected")


# =========================================================================
# LCD — frames, orientation, brightness
# =========================================================================


@dataclass(frozen=True, slots=True)
class SendFrame(Command[SendResult]):
    """Push already-built frame bytes to the device.

    Bypasses the theme/render pipeline (Phase 5+) — useful for scripts
    and end-to-end smoke tests.
    """
    key: str
    data: bytes

    def execute(self, app: App) -> SendResult:
        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return SendResult(ok=False, key=self.key, message=str(e))
        if not device.is_connected:
            raise DeviceNotConnectedError(
                f"{self.key} not connected — dispatch ConnectDevice first"
            )
        try:
            ok = device.send(self.data)
        except TransportError as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="transport",
                                             key=self.key))
            return SendResult(ok=False, key=self.key, message=str(e))
        bytes_sent = len(self.data) if ok else 0
        if ok:
            app.events.publish(FrameSent(key=self.key, bytes_sent=bytes_sent))
        return SendResult(
            ok=ok, key=self.key,
            message=f"Sent {bytes_sent} bytes" if ok else "Send returned False",
            bytes_sent=bytes_sent,
        )


@dataclass(frozen=True, slots=True)
class SendColor(Command[SendResult]):
    """Push a solid-color frame to a connected LCD device.

    Bypasses the theme/render pipeline — useful as a primary diagnostic
    (``trcc-next display color 0402:3922 ff0000`` turns the screen red)
    and as the smallest path that exercises every link in the wire chain:
    handshake-derived profile → DisplayService.build_solid_color_frame →
    Device.send.
    """
    key: str
    r: int
    g: int
    b: int

    def execute(self, app: App) -> SendResult:
        for label, value in (("r", self.r), ("g", self.g), ("b", self.b)):
            if not 0 <= value <= 255:
                return SendResult(
                    ok=False, key=self.key, bytes_sent=0,
                    message=f"{label} out of range (0-255): {value}",
                )

        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              message=str(e))
        if not device.is_connected:
            raise DeviceNotConnectedError(
                f"{self.key} not connected — dispatch ConnectDevice first"
            )

        try:
            frame = app.display.build_solid_color_frame(
                info=device.info,
                color=(self.r, self.g, self.b),
                profile=device.profile,
            )
            ok = device.send(frame)
        except TransportError as e:
            app.events.publish(ErrorOccurred(
                message=str(e), kind="transport", key=self.key,
            ))
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              message=str(e))

        bytes_sent = len(frame) if ok else 0
        if ok:
            app.events.publish(FrameSent(key=self.key, bytes_sent=bytes_sent))
        return SendResult(
            ok=ok, key=self.key, bytes_sent=bytes_sent,
            message=(f"Sent {bytes_sent} bytes (#{self.r:02x}{self.g:02x}{self.b:02x})"
                     if ok else "Send returned False"),
        )


@dataclass(frozen=True, slots=True)
class RenderAndSend(Command[RenderResult]):
    """Render the device's active theme with live sensors, push to the wire.

    Called by tickers — GUI QTimer, CLI `display play` loop, API tick
    endpoint — every ~AppSettings.refresh_interval_s.  Uses the
    DisplayService scene cache so only the changed layer rebuilds per
    tick (sensors moved → redraw overlay; video cursor advanced →
    rebuild bg; otherwise pure cache hit + composite).
    """
    key: str

    def execute(self, app: App) -> RenderResult:
        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return RenderResult(ok=False, key=self.key, message=str(e))
        if not device.is_connected:
            raise DeviceNotConnectedError(
                f"{self.key} not connected — dispatch ConnectDevice first"
            )

        theme = app.active_themes.get(self.key)
        if theme is None:
            return RenderResult(
                ok=False, key=self.key,
                message="No active theme — dispatch LoadTheme first",
            )

        sensors = app.platform.sensors().read_all()

        try:
            frame = app.display.build_frame(
                info=device.info, theme=theme, sensors=sensors,
                profile=device.profile,
            )
            ok = device.send(frame)
        except TransportError as e:
            app.events.publish(ErrorOccurred(
                message=str(e), kind="transport", key=self.key,
            ))
            return RenderResult(
                ok=False, key=self.key, theme_name=theme.name,
                message=str(e),
            )

        if ok:
            app.events.publish(FrameSent(key=self.key, bytes_sent=len(frame)))
        return RenderResult(
            ok=ok, key=self.key,
            bytes_sent=len(frame), theme_name=theme.name,
            message=(f"Rendered + sent {len(frame)} bytes"
                     if ok else "Render built frame but send returned False"),
        )


@dataclass(frozen=True, slots=True)
class LoadTheme(Command[ThemeResult]):
    """Parse a theme, persist it, render the first frame, and send it.

    If the device isn't attached, the theme is still persisted so it
    takes effect on next connect.  If no Renderer is attached to the
    App, the send step is skipped (parse + persist only).
    """
    key: str
    path: Path

    def execute(self, app: App) -> ThemeResult:
        try:
            theme = app.themes.load(self.path)
        except ThemeError as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="theme",
                                             key=self.key))
            return ThemeResult(ok=False, key=self.key, message=str(e))

        app.settings.set_current_theme(self.key, theme.name)
        app.active_themes[self.key] = theme
        app.display.invalidate(self.key)  # drop stale scene cache from prev theme
        app.media.unload(self.key)        # drop stale video frames
        app.events.publish(ThemeLoaded(key=self.key, theme_name=theme.name))

        # If device is attached + connected + Renderer available, send an
        # immediate first frame.  Otherwise the theme is saved for the
        # next connect / tick.
        device = app.devices.get(self.key)
        if device is None or not device.is_connected:
            return ThemeResult(
                ok=True, key=self.key, theme_name=theme.name,
                message=f"Theme '{theme.name}' saved (device not connected)",
            )
        if app._renderer is None:  # pyright: ignore[reportPrivateUsage]
            return ThemeResult(
                ok=True, key=self.key, theme_name=theme.name,
                message=f"Theme '{theme.name}' saved (no Renderer attached)",
            )

        try:
            frame = app.display.build_frame(
                info=device.info, theme=theme, sensors={},
                profile=device.profile,
            )
            sent = device.send(frame)
        except (TransportError, Exception) as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="render",
                                             key=self.key))
            return ThemeResult(
                ok=False, key=self.key, theme_name=theme.name,
                message=f"Render/send failed: {e}",
            )

        if sent:
            app.events.publish(FrameSent(key=self.key, bytes_sent=len(frame)))
        return ThemeResult(
            ok=sent, key=self.key, theme_name=theme.name,
            message=(f"Theme '{theme.name}' loaded and sent ({len(frame)} bytes)"
                     if sent else f"Theme '{theme.name}' rendered but send failed"),
        )


@dataclass(frozen=True, slots=True)
class SetOrientation(Command[OrientationResult]):
    """Set per-device rotation (0 / 90 / 180 / 270).

    Validates against the product registry — device need not be
    connected yet (users often configure before plugging in).
    """
    key: str
    degrees: int

    def execute(self, app: App) -> OrientationResult:
        try:
            vid_str, pid_str = self.key.split(":")
            vid, pid = int(vid_str, 16), int(pid_str, 16)
        except ValueError:
            return OrientationResult(
                ok=False, key=self.key, degrees=self.degrees,
                message=f"Invalid device key: {self.key!r}",
            )
        info = find_product(vid, pid)
        if info is None:
            return OrientationResult(
                ok=False, key=self.key, degrees=self.degrees,
                message=f"Unknown device: {self.key}",
            )
        if self.degrees not in info.orientations:
            return OrientationResult(
                ok=False, key=self.key, degrees=self.degrees,
                message=f"Unsupported orientation for {self.key}: {self.degrees}",
            )
        app.settings.set_orientation(self.key, self.degrees)
        app.events.publish(OrientationChanged(key=self.key, degrees=self.degrees))
        return OrientationResult(
            ok=True, key=self.key, degrees=self.degrees,
            message=f"Orientation set to {self.degrees}°",
        )


@dataclass(frozen=True, slots=True)
class SetBrightness(Command[BrightnessResult]):
    """Set per-device display brightness (0–100)."""
    key: str
    percent: int

    def execute(self, app: App) -> BrightnessResult:
        if not 0 <= self.percent <= 100:
            return BrightnessResult(
                ok=False, key=self.key, percent=self.percent,
                message="Brightness out of range (0–100)",
            )
        app.settings.set_brightness(self.key, self.percent)
        app.events.publish(BrightnessChanged(key=self.key, percent=self.percent))
        return BrightnessResult(
            ok=True, key=self.key, percent=self.percent,
            message=f"Brightness set to {self.percent}%",
        )


# ── Display tweaks (fit mode / overlay / split mode) ────────────────


def _invalidate_scene(app: App, key: str) -> None:
    """Drop the per-device scene cache if the display service is wired.

    Settings changes that affect rendering (fit, mask, overlay, split)
    need to bust the cache so the next render rebuilds with the new
    setting. Pure settings writes don't need it; this helper is the
    seam.
    """
    if app._renderer is not None:  # pyright: ignore[reportPrivateUsage]
        app.display.invalidate(key)


@dataclass(frozen=True, slots=True)
class SetFitMode(Command[FitModeResult]):
    """Set how the background image/video fits the device canvas.

    Accepts the FitMode value strings — ``"width"`` (letterbox top/
    bottom), ``"height"`` (pillarbox left/right), ``"stretch"`` (fill,
    distort).
    """
    key: str
    mode: str

    def execute(self, app: App) -> FitModeResult:
        try:
            parsed = FitMode(self.mode)
        except ValueError:
            valid = ", ".join(m.value for m in FitMode)
            return FitModeResult(
                ok=False, key=self.key, mode=self.mode,
                message=f"mode must be one of: {valid} — got {self.mode!r}",
            )
        app.settings.set_fit_mode(self.key, parsed)
        _invalidate_scene(app, self.key)
        app.events.publish(FitModeChanged(key=self.key, mode=parsed.value))
        return FitModeResult(
            ok=True, key=self.key, mode=parsed.value,
            message=f"fit mode set to {parsed.value}",
        )


@dataclass(frozen=True, slots=True)
class EnableOverlay(Command[OverlayResult]):
    """Toggle the metric overlay layer for a device.

    When disabled, RenderAndSend skips the text/metric layer entirely —
    just the bg+mask renders. Useful for users who want a clean wallpaper
    without sensor readouts.
    """
    key: str
    enabled: bool

    def execute(self, app: App) -> OverlayResult:
        app.settings.set_overlay_enabled(self.key, self.enabled)
        _invalidate_scene(app, self.key)
        app.events.publish(OverlayChanged(key=self.key, enabled=self.enabled))
        return OverlayResult(
            ok=True, key=self.key, enabled=self.enabled,
            message=(f"overlay {'enabled' if self.enabled else 'disabled'} "
                     f"for {self.key}"),
        )


@dataclass(frozen=True, slots=True)
class SetSplitMode(Command[SplitModeResult]):
    """Set the Dynamic Island style for widescreen panels.

    Value range: 0 (off), 1 (style A), 2 (style B, default), 3 (style C).
    Stored per-device regardless of whether the device is widescreen;
    rendering consults the device profile + resolution to decide whether
    to composite the overlay.
    """
    key: str
    mode: int

    def execute(self, app: App) -> SplitModeResult:
        if self.mode not in (0, 1, 2, 3):
            return SplitModeResult(
                ok=False, key=self.key, mode=self.mode,
                message=(f"split mode must be 0 (off), 1, 2, or 3 — "
                         f"got {self.mode}"),
            )
        app.settings.set_split_mode(self.key, self.mode)
        _invalidate_scene(app, self.key)
        app.events.publish(SplitModeChanged(key=self.key, mode=self.mode))
        return SplitModeResult(
            ok=True, key=self.key, mode=self.mode,
            message=(f"split mode set to {self.mode} for {self.key}"
                     if self.mode else f"split mode disabled for {self.key}"),
        )


# =========================================================================
# LED
# =========================================================================


@dataclass(frozen=True, slots=True)
class SetLedColors(Command[LedColorsResult]):
    """Set LED color array + on/off + brightness on a connected Led device."""
    key: str
    colors: list[tuple[int, int, int]]
    global_on: bool = True
    brightness: int = 100

    def execute(self, app: App) -> LedColorsResult:
        from ..adapters.device.led import Led, LedPayload

        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=list(self.colors),
                message=str(e),
            )

        if not isinstance(device, Led):
            return LedColorsResult(
                ok=False, key=self.key, colors=list(self.colors),
                message=f"{self.key} is not an LED device",
            )
        if not device.is_connected:
            raise DeviceNotConnectedError(
                f"{self.key} not connected — dispatch ConnectDevice first"
            )

        payload = LedPayload(
            colors=list(self.colors),
            global_on=self.global_on,
            brightness=self.brightness,
        )
        try:
            ok = device.send(payload)
        except TransportError as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="transport",
                                             key=self.key))
            return LedColorsResult(
                ok=False, key=self.key, colors=list(self.colors),
                message=str(e),
            )

        if ok:
            app.events.publish(LedColorsChanged(
                key=self.key, color_count=len(self.colors),
            ))
        return LedColorsResult(
            ok=ok, key=self.key, colors=list(self.colors),
            message=(f"Sent {len(self.colors)} LED color(s)"
                     if ok else "LED send returned False"),
        )


# =========================================================================
# Sensors
# =========================================================================


@dataclass(frozen=True, slots=True)
class ReadSensors(Command[SensorsResult]):
    """Return current sensor readings.

    Pulls descriptor metadata (label / unit / category) from
    `discover()` and fresh values from `read_all()`, then merges the
    two so every returned `SensorReading` carries the current value.
    """

    def execute(self, app: App) -> SensorsResult:
        from .models import SensorReading
        enum = app.platform.sensors()
        descriptors = enum.discover()
        current = enum.read_all()
        readings = [
            SensorReading(
                sensor_id=d.sensor_id,
                category=d.category,
                value=current.get(d.sensor_id, 0.0),
                unit=d.unit,
                label=d.label,
            )
            for d in descriptors
        ]
        return SensorsResult(
            ok=True,
            message=f"{len(readings)} sensor(s)",
            readings=readings,
        )


# =========================================================================
# System
# =========================================================================


@dataclass(frozen=True, slots=True)
class RunSetup(Command[SetupResult]):
    """OS-specific one-time setup (udev, WinUSB guide, etc.)."""
    interactive: bool = True

    def execute(self, app: App) -> SetupResult:
        warnings = app.platform.check_permissions()
        code = app.platform.setup(interactive=self.interactive)
        return SetupResult(
            ok=code == 0,
            message=f"Setup completed with exit code {code}",
            exit_code=code,
            warnings=warnings,
        )


@dataclass(frozen=True, slots=True)
class GetPlatformInfo(Command[PlatformInfoResult]):
    """Snapshot of OS identity + paths + permission warnings.

    Used by diagnostic UIs (`trcc info`, GUI about panel).  Keeps UIs
    from reaching directly into `app.platform` — they dispatch this and
    render the Result like any other Command.
    """

    def execute(self, app: App) -> PlatformInfoResult:
        p = app.platform
        paths = p.paths()
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
        )


# ── Autostart ────────────────────────────────────────────────────────


def _autostart_path(app: App) -> str:
    """Extract the manager's filesystem path when available."""
    mgr = app.platform.autostart()
    return str(getattr(mgr, "path", "")) or ""


@dataclass(frozen=True, slots=True)
class GetAutostartStatus(Command[AutostartResult]):
    """Report whether auto-launch-on-login is enabled."""

    def execute(self, app: App) -> AutostartResult:
        mgr = app.platform.autostart()
        enabled = mgr.is_enabled()
        path = _autostart_path(app)
        return AutostartResult(
            ok=True,
            message="enabled" if enabled else "disabled",
            enabled=enabled, path=path,
        )


@dataclass(frozen=True, slots=True)
class EnableAutostart(Command[AutostartResult]):
    """Install the OS-specific autostart entry (per-user, no sudo)."""

    def execute(self, app: App) -> AutostartResult:
        mgr = app.platform.autostart()
        mgr.enable()
        return AutostartResult(
            ok=True, message="autostart enabled",
            enabled=mgr.is_enabled(), path=_autostart_path(app),
        )


@dataclass(frozen=True, slots=True)
class DisableAutostart(Command[AutostartResult]):
    """Remove the OS-specific autostart entry."""

    def execute(self, app: App) -> AutostartResult:
        mgr = app.platform.autostart()
        mgr.disable()
        return AutostartResult(
            ok=True, message="autostart disabled",
            enabled=mgr.is_enabled(), path=_autostart_path(app),
        )


# =========================================================================
# Control-center settings (app-global; no device key)
# =========================================================================


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
            return TempUnitResult(
                ok=False, unit=self.unit,
                message=f"unit must be 'C' or 'F', got {self.unit!r}",
            )
        app.settings.set_global_temp_unit(self.unit)   # type: ignore[arg-type]
        app.events.publish(TempUnitChanged(unit=self.unit))
        return TempUnitResult(
            ok=True, unit=self.unit,
            message=f"temp unit set to {self.unit}",
        )


@dataclass(frozen=True, slots=True)
class SetLanguage(Command[LanguageResult]):
    """Set the UI language code (ISO 639-1, e.g. 'en', 'zh', 'fr')."""
    language: str

    def execute(self, app: App) -> LanguageResult:
        lang = self.language.strip()
        if not lang:
            return LanguageResult(
                ok=False, language=self.language,
                message="language code cannot be empty",
            )
        app.settings.set_language(lang)
        app.events.publish(LanguageChanged(language=lang))
        return LanguageResult(
            ok=True, language=lang, message=f"language set to {lang}",
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
        app.events.publish(GpuDeviceChanged(gpu_key=normalized))
        return GpuDeviceResult(
            ok=True, gpu_key=normalized,
            message=(f"active gpu set to {normalized}" if normalized
                     else "active gpu cleared (auto)"),
        )


@dataclass(frozen=True, slots=True)
class SetRefreshInterval(Command[RefreshIntervalResult]):
    """Set the global metrics-refresh / render-and-send tick interval.

    Clamped to [0.1, 60.0] seconds — anything outside that range either
    starves the CPU (too low) or makes the LCD feel unresponsive (too
    high).
    """
    seconds: float

    def execute(self, app: App) -> RefreshIntervalResult:
        if not 0.1 <= self.seconds <= 60.0:
            return RefreshIntervalResult(
                ok=False, seconds=self.seconds,
                message=(f"refresh interval must be in [0.1, 60.0] seconds, "
                         f"got {self.seconds}"),
            )
        app.settings.set_refresh_interval(self.seconds)
        app.events.publish(RefreshIntervalChanged(seconds=self.seconds))
        return RefreshIntervalResult(
            ok=True, seconds=self.seconds,
            message=f"refresh interval set to {self.seconds:.2f}s",
        )

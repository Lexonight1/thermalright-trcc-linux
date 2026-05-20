"""Result dataclasses returned by Commands.

Results are the Command API's return values — the universal language UIs
render.  Every Command has one concrete Result type.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    DeviceInfo,
    HandshakeResult,
    LedHandshakeResult,
    ProductInfo,
    SensorReading,
)


@dataclass(frozen=True, slots=True)
class Result:
    """Base result — every Command returns one of these (or a subclass)."""
    ok: bool = True
    message: str = ""


@dataclass(frozen=True, slots=True)
class DiscoverResult(Result):
    products: list[ProductInfo] = field(default_factory=list)
    devices: list[DeviceInfo] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ConnectResult(Result):
    key: str = ""
    handshake: HandshakeResult | None = None
    led_handshake: LedHandshakeResult | None = None


@dataclass(frozen=True, slots=True)
class DisconnectResult(Result):
    key: str = ""


@dataclass(frozen=True, slots=True)
class SendResult(Result):
    key: str = ""
    bytes_sent: int = 0


@dataclass(frozen=True, slots=True)
class RenderResult(Result):
    """Built + sent one frame through the render pipeline."""
    key: str = ""
    bytes_sent: int = 0
    theme_name: str = ""


@dataclass(frozen=True, slots=True)
class ThemeResult(Result):
    key: str = ""
    theme_name: str = ""


@dataclass(frozen=True, slots=True)
class OrientationResult(Result):
    key: str = ""
    degrees: int = 0


@dataclass(frozen=True, slots=True)
class BrightnessResult(Result):
    key: str = ""
    percent: int = 100


@dataclass(frozen=True, slots=True)
class FitModeResult(Result):
    key: str = ""
    mode: str = "width"


@dataclass(frozen=True, slots=True)
class OverlayResult(Result):
    key: str = ""
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class SplitModeResult(Result):
    key: str = ""
    mode: int = 0


@dataclass(frozen=True, slots=True)
class MaskApplyResult(Result):
    key: str = ""
    path: str = ""


@dataclass(frozen=True, slots=True)
class MaskPositionResult(Result):
    key: str = ""
    position: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class MaskVisibilityResult(Result):
    key: str = ""
    visible: bool = True


@dataclass(frozen=True, slots=True)
class ThemeExportResult(Result):
    theme_name: str = ""
    archive_path: str = ""


@dataclass(frozen=True, slots=True)
class ThemeImportResult(Result):
    theme_name: str = ""
    path: str = ""


@dataclass(frozen=True, slots=True)
class VideoResult(Result):
    key: str = ""
    path: str = ""
    frame_count: int = 0


@dataclass(frozen=True, slots=True)
class BootAnimationResult(Result):
    """One compressed boot-animation upload to a SCSI LCD."""
    key: str = ""
    frames_uploaded: int = 0
    frames_total: int = 0


@dataclass(frozen=True, slots=True)
class LedColorsResult(Result):
    key: str = ""
    colors: list[tuple[int, int, int]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SensorsResult(Result):
    readings: list[SensorReading] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SetupResult(Result):
    exit_code: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AutostartResult(Result):
    """Current autostart state + path for diagnostic UIs."""
    enabled: bool = False
    path: str = ""


@dataclass(frozen=True, slots=True)
class TempUnitResult(Result):
    unit: str = "C"


@dataclass(frozen=True, slots=True)
class LanguageResult(Result):
    language: str = "en"


@dataclass(frozen=True, slots=True)
class GpuDeviceResult(Result):
    gpu_key: str | None = None


@dataclass(frozen=True, slots=True)
class RefreshIntervalResult(Result):
    seconds: float = 2.0


@dataclass(frozen=True, slots=True)
class PlatformInfoResult(Result):
    """Snapshot of identity + path + permission info for diagnostic UIs."""
    distro_name: str = ""
    install_method: str = ""
    config_dir: str = ""
    data_dir: str = ""
    user_content_dir: str = ""
    log_file: str = ""
    permission_warnings: list[str] = field(default_factory=list)

# TRCC Universal Command Contract

One command surface. Three UI tiers. Same methods, same signatures, same typed results.

## Parity Law

**If it exists in GUI, it exists in CLI and API — same method, same signature, same result.**

No UI gets shortcuts. No UI gets left behind. Adding a capability means adding it to the command layer; all three UIs pick it up.

## Architecture

```text
┌─────────────────────────────────────────────────┐
│  GUI handlers    CLI subcommands    API endpoints│   UI tier — dumb pipes
│       ↘              ↓                  ↙       │
│                Trcc (facade)                     │
│        ┌──────────┬─────────────────┐            │
│        ↓          ↓                 ↓            │
│   LCDCommands  LEDCommands  ControlCenterCommands│   the commands
│                    EventBus                      │
└──────────────────┬──────────────────────────────┘
                   ↓
              Services (rendering, compositing, theme loading)
                   ↓
              Protocols (SCSI/HID/Bulk/LED-HID)
                   ↓
              Device (dumb endpoint: send(bytes))
                   ↑
              Platform (OS)
```

**UI tier calls Trcc. API translates HTTP → Trcc call → `asdict(result)`. GUI and CLI import Trcc directly.**

## Widget flow rule

Widgets never read `conf.settings`, never import `core.device.Device`, never call services.

```text
widget input → command → result → widget paints result
                  ↓
              EventBus → handler subscribes → widget paints event payload
```

Three consequences:

1. **First paint** comes from a snapshot (`trcc.lcd.snapshot(idx)` / `trcc.led.snapshot(idx)` / `trcc.control_center.snapshot()`).
2. **Every interaction** round-trips: user action → command → result → widget updates visuals from `result`.
3. **Async updates** come from EventBus. No polling, no `QTimer.singleShot` on settings, no widget reaching into device state.

## File layout

```text
core/
  trcc.py                      — facade: composes the three + EventBus
  lcd_commands.py              — LCDCommands (28 methods)
  led_commands.py              — LEDCommands (17 methods)
  control_center_commands.py   — ControlCenterCommands (13 methods)
  events.py                    — EventBus
  results.py                   — Result + Info dataclasses, Frame
  ports.py                     — Platform + Renderer ABCs (exists)
adapters/
  system/
    linux_platform.py, windows_platform.py, macos_platform.py, bsd_platform.py
  ui/
    gui/  cli.py  api.py
```

Command files mirror the OS files: one responsibility per file, ~200–500 LOC each.

## Trcc facade

```python
class Trcc:
    lcd: LCDCommands
    led: LEDCommands
    control_center: ControlCenterCommands
    events: EventBus

    def __init__(self, platform: Platform): ...
    @classmethod
    def for_current_os(cls) -> Trcc: ...
    def discover(self) -> DiscoveryResult: ...
    def cleanup(self) -> None: ...
```

## Result dataclasses (`core/results.py`)

All `frozen=True`, JSON-serializable via `dataclasses.asdict`.

```python
@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    pixels: bytes                     # raw RGBA
    encoded: bytes | None = None      # RGB565 pre-encoded for device

@dataclass(frozen=True)
class OpResult:
    success: bool
    message: str = ''
    error: str | None = None

@dataclass(frozen=True)
class FrameResult(OpResult):
    frame: Frame | None = None

@dataclass(frozen=True)
class ThemeResult(OpResult):
    frame: Frame | None = None
    is_animated: bool = False
    interval_ms: int = 0
    overlay_config: dict | None = None
    overlay_enabled: bool = False

@dataclass(frozen=True)
class VideoTickResult:
    frame: Frame | None
    frame_index: int
    progress_percent: float
    current_time: str
    total_time: str

@dataclass(frozen=True)
class LEDResult(OpResult):
    display_colors: list[tuple[int, int, int]] = field(default_factory=list)

@dataclass(frozen=True)
class DiscoveryResult(OpResult):
    lcd_devices: list[DeviceInfo] = field(default_factory=list)
    led_devices: list[DeviceInfo] = field(default_factory=list)

@dataclass(frozen=True)
class UpdateResult(OpResult):
    current_version: str = ''
    latest_version: str | None = None
    update_available: bool = False
    assets: dict[str, str] = field(default_factory=dict)   # {pkg_mgr: url}

@dataclass(frozen=True)
class LCDSnapshot:
    connected: bool; playing: bool; auto_send: bool
    overlay_enabled: bool
    brightness: int; rotation: int; split_mode: int; fit_mode: str
    resolution: tuple[int, int]
    current_theme: str | None

@dataclass(frozen=True)
class LEDSnapshot:
    connected: bool
    style_id: int; mode: int
    color: tuple[int, int, int]; brightness: int; global_on: bool
    zones: list[dict]
    zone_sync: bool; zone_sync_interval: int
    selected_zone: int; segment_on: list[bool]
    clock_24h: bool; week_sunday: bool
    memory_ratio: int
    test_mode: bool

@dataclass(frozen=True)
class AppSnapshot:
    version: str
    autostart: bool
    temp_unit: str                    # 'C' | 'F'
    language: str
    hdd_enabled: bool
    refresh_interval: int
    gpu_device: str | None
    gpu_list: list[tuple[str, str]]
    install_method: str               # 'pipx' | 'pip' | 'pacman' | 'dnf' | 'apt'
    distro: str
```

## Info dataclasses

```python
@dataclass(frozen=True)
class ThemeInfo:
    name: str; path: Path
    preview_path: Path | None
    is_animated: bool
    is_user: bool
    source: str                       # 'local' | 'user' | 'cloud'

@dataclass(frozen=True)
class MaskInfo:
    name: str; path: Path
    preview_path: Path | None
    is_custom: bool

@dataclass(frozen=True)
class BackgroundInfo:
    name: str; path: Path

@dataclass(frozen=True)
class OverlayElement:
    kind: str                         # 'hardware' | 'time' | 'date' | 'custom' | 'weekday'
    x: int; y: int
    color: tuple[int, int, int]
    font_name: str
    font_size: int
    font_style: int = 0               # bold/italic bitfield
    format: int = 0
    format_sub: int = 0
    text: str = ''
    source_key: str = ''              # sensor key for 'hardware'

@dataclass(frozen=True)
class LEDStyleInfo:
    style_id: int; name: str
    segment_count: int; zone_count: int
    supported_modes: list[str]

@dataclass(frozen=True)
class SensorInfo:
    key: str; label: str
    unit: str
    category: str                     # 'cpu' | 'gpu' | 'mem' | 'disk' | 'net'

@dataclass(frozen=True)
class DiskInfo:
    index: int; name: str; path: str
```

## LCDCommands — 28 methods

```python
class LCDCommands:
    # Display settings
    def set_brightness(self, lcd: int, percent: int) -> FrameResult
    def set_rotation(self, lcd: int, degrees: int) -> FrameResult
    def set_split_mode(self, lcd: int, mode: int) -> FrameResult
    def set_fit_mode(self, lcd: int, mode: str) -> FrameResult

    # Themes
    def load_theme(self, lcd: int, path: Path) -> ThemeResult
    def load_cloud_theme(self, lcd: int, theme_id: str) -> ThemeResult
    def load_image(self, lcd: int, path: Path) -> FrameResult
    def save_theme(self, lcd: int, name: str) -> OpResult
    def delete_theme(self, lcd: int, path: Path) -> OpResult
    def export_config(self, lcd: int, path: Path) -> OpResult
    def import_config(self, lcd: int, path: Path) -> OpResult
    def restore_last_theme(self, lcd: int) -> ThemeResult

    # Masks
    def apply_mask(self, lcd: int, path: Path, *, is_custom: bool = False) -> FrameResult
    def upload_custom_mask(self, lcd: int, png: bytes) -> FrameResult
    def set_mask_position(self, lcd: int, x: int, y: int) -> FrameResult
    def set_mask_visible(self, lcd: int, visible: bool) -> FrameResult

    # Overlay
    def enable_overlay(self, lcd: int, enabled: bool) -> FrameResult
    def set_overlay_config(self, lcd: int, config: dict) -> FrameResult
    def add_overlay_element(self, lcd: int, element: OverlayElement) -> FrameResult
    def update_overlay_element(
        self, lcd: int, index: int, *,
        x: int | None = None, y: int | None = None,
        color: tuple[int, int, int] | None = None,
        font_name: str | None = None, font_size: int | None = None,
        font_style: int | None = None,
        format: int | None = None, format_sub: int | None = None,
        text: str | None = None,
    ) -> FrameResult
    def delete_overlay_element(self, lcd: int, index: int) -> FrameResult
    def flash_overlay_element(self, lcd: int, index: int, duration_ms: int = 980) -> FrameResult
    def set_overlay_background(self, lcd: int, png: bytes) -> FrameResult

    # Video
    def load_video(self, lcd: int, path: Path) -> OpResult
    def play_video(self, lcd: int) -> OpResult
    def pause_video(self, lcd: int) -> OpResult
    def stop_video(self, lcd: int) -> OpResult
    def seek_video(self, lcd: int, percent: float) -> OpResult

    # Screencast + background + slideshow
    def start_screencast(self, lcd: int, x: int, y: int, w: int, h: int,
                         *, audio: bool = False) -> OpResult
    def stop_screencast(self, lcd: int) -> OpResult
    def set_background_mode(self, lcd: int, enabled: bool) -> FrameResult
    def configure_slideshow(self, lcd: int, themes: list[str], interval_s: int) -> OpResult
    def set_slideshow(self, lcd: int, enabled: bool) -> OpResult

    # Rendering
    def render_and_send(self, lcd: int, *, send: bool = True) -> FrameResult
    def send_color(self, lcd: int, r: int, g: int, b: int) -> OpResult
    def reset(self, lcd: int) -> OpResult

    # Listing
    def list_themes(self, lcd: int, *, source: str = 'all') -> list[ThemeInfo]
    def list_masks(self, lcd: int, *, source: str = 'all') -> list[MaskInfo]
    def list_backgrounds(self, lcd: int) -> list[BackgroundInfo]

    # Snapshot
    def snapshot(self, lcd: int) -> LCDSnapshot
```

## LEDCommands — 17 methods

```python
class LEDCommands:
    # Color / mode / brightness (global or zoned)
    def set_color(self, led: int, r: int, g: int, b: int,
                  *, zone: int | None = None) -> LEDResult
    def set_mode(self, led: int, mode: LEDMode | str | int,
                 *, zone: int | None = None) -> LEDResult
    def set_brightness(self, led: int, percent: int,
                       *, zone: int | None = None) -> LEDResult
    def toggle(self, led: int, on: bool, *, zone: int | None = None) -> LEDResult
    def toggle_segment(self, led: int, index: int, on: bool) -> LEDResult

    # Zones
    def select_zone(self, led: int, zone: int) -> OpResult
    def set_zone_sync(self, led: int, enabled: bool,
                      *, zones: list[int] | None = None,
                      interval_s: int | None = None) -> OpResult

    # Display modes
    def set_clock_format(self, led: int, is_24h: bool) -> OpResult
    def set_week_start(self, led: int, sunday: bool) -> OpResult
    def set_memory_ratio(self, led: int, ratio: int) -> OpResult
    def set_disk_device(self, disk_key: str) -> OpResult   # app-wide, not per-LED
    def set_test_mode(self, led: int, enabled: bool) -> OpResult
    def set_sensor_source(self, led: int, source: str) -> OpResult

    # Listing
    def list_styles(self) -> list[LEDStyleInfo]
    def list_modes(self, led: int) -> list[str]
    def list_disks(self) -> list[DiskInfo]

    # Snapshot
    def snapshot(self, led: int) -> LEDSnapshot
```

## ControlCenterCommands — 13 methods

```python
class ControlCenterCommands:
    # Settings
    def set_autostart(self, enabled: bool) -> OpResult
    def set_temp_unit(self, unit: str) -> OpResult                # 'C' | 'F'
    def set_language(self, lang: str) -> OpResult
    def set_hdd_enabled(self, enabled: bool) -> OpResult
    def set_metrics_refresh(self, seconds: int) -> OpResult
    def set_gpu_device(self, gpu_key: str) -> OpResult

    # Updates
    def check_for_update(self) -> UpdateResult
    def run_upgrade(self) -> OpResult

    # Listing
    def list_gpus(self) -> list[tuple[str, str]]                   # (key, display_name)
    def list_fonts(self) -> list[str]
    def list_sensors(self) -> list[SensorInfo]

    # Metrics snapshot
    def metrics(self) -> dict

    # Snapshot
    def snapshot(self) -> AppSnapshot
```

## EventBus (`core/events.py`)

```python
class EventBus:
    def subscribe(self, event: str, callback: Callable) -> int    # → sub_id
    def unsubscribe(self, sub_id: int) -> None
    def publish(self, event: str, payload: Any) -> None           # internal
```

Event types:

| Event | Payload | Emitted by |
|---|---|---|
| `'frame'` | `(device_idx: int, frame: Frame)` | video tick, LED tick |
| `'metrics'` | `dict` | sensor service tick |
| `'device.connected'` | `DeviceInfo` | discover / hotplug |
| `'device.disconnected'` | `DeviceInfo` | USB unplug, sleep |
| `'data.ready'` | `None` | theme data finished extracting |
| `'update.available'` | `UpdateResult` | background version check |

## Usage

### GUI handler

```python
class LCDHandler:
    def __init__(self, app: Trcc, lcd_idx: int, widgets):
        self._app = app
        self._idx = lcd_idx
        self._w = widgets
        # First paint — from snapshot
        self._paint_from_snapshot(app.lcd.snapshot(lcd_idx))
        # Live updates — subscribe
        app.events.subscribe('frame', self._on_frame)

    def on_brightness_change(self, percent: int):
        result = self._app.lcd.set_brightness(self._idx, percent)
        if result.frame:
            self._w['preview'].set_frame(result.frame)
```

### CLI

```python
@lcd.command()
def brightness(idx: int, percent: int):
    app = Trcc.for_current_os()
    result = app.lcd.set_brightness(idx, percent)
    print(result.message)
```

### API

```python
@router.put('/lcd/{idx}/brightness')
def set_brightness(idx: int, req: BrightnessRequest):
    return asdict(trcc.lcd.set_brightness(idx, req.percent))
```

Same call. Three rendering shells.

## Totals

- **55 methods** across 3 command classes
- **13 Result / Snapshot dataclasses**
- **7 Info dataclasses**
- **1 EventBus**
- **6 event types**

Every GUI action is reachable from CLI and API. The parity rule is mechanically enforced — a Trcc method without a CLI subcommand or API endpoint is a gap to fix.

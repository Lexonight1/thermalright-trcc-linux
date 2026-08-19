# Port reference

**Generated — do not edit.** `PYTHONPATH=src python3 dev/gen_ports_reference.py`

Every abstract contract in the tree: what a new implementation must write, what it inherits for free, and who already implements it.

Ordered **cheapest to extend first** — the ports at the top are where this codebase welcomes a contributor, the ones at the bottom are where it does not yet.

31 ports.

| port | implement | inherit | implementations |
|---|---|---|---|
| [`Command`](#command) | 1 | 0 | 122 |
| [`DataInstaller`](#datainstaller) | 1 | 0 | 1 |
| [`HttpFetcher`](#httpfetcher) | 1 | 0 | 1 |
| [`MissPolicy`](#misspolicy) | 1 | 0 | 2 |
| [`Query`](#query) | 1 | 0 | 28 |
| [`ScreenCapture`](#screencapture) | 1 | 0 | 1 |
| [`_HidBinding`](#_hidbinding) | 1 | 0 | 2 |
| [`DataInstallRunner`](#datainstallrunner) | 2 | 0 | 2 |
| [`_MappingPort`](#_mappingport) | 2 | 0 | 2 |
| [`BaseBulkDevice`](#basebulkdevice) | 3 | 0 | 4 |
| [`BaseDevice`](#basedevice) | 3 | 4 | 5 |
| [`Device`](#device) | 3 | 12 | 5 |
| [`DiskSource`](#disksource) | 3 | 0 | 2 |
| [`DramSource`](#dramsource) | 3 | 0 | 1 |
| [`HotplugMonitor`](#hotplugmonitor) | 3 | 0 | 5 |
| [`SendScheduler`](#sendscheduler) | 3 | 0 | 2 |
| [`AutostartManager`](#autostartmanager) | 4 | 0 | 4 |
| [`CloudCatalog`](#cloudcatalog) | 4 | 0 | 1 |
| [`FanSource`](#fansource) | 4 | 0 | 3 |
| [`MemorySource`](#memorysource) | 4 | 0 | 2 |
| [`Paths`](#paths) | 4 | 9 | 5 |
| [`SendTask`](#sendtask) | 4 | 0 | 1 |
| [`BulkTransport`](#bulktransport) | 5 | 0 | 2 |
| [`CpuSource`](#cpusource) | 5 | 0 | 10 |
| [`ScsiTransport`](#scsitransport) | 5 | 0 | 3 |
| [`Diagnostics`](#diagnostics) | 7 | 0 | 1 |
| [`BaseOS`](#baseos) | 8 | 8 | 7 |
| [`SensorEnumerator`](#sensorenumerator) | 9 | 3 | 1 |
| [`GpuSource`](#gpusource) | 10 | 0 | 10 |
| [`Platform`](#platform) | 10 | 9 | 7 |
| [`Renderer`](#renderer) | 13 | 6 | 1 |

---

## Command

`core/commands/_base.py`

A user action.  Exactly one execute method; returns one Result.

**You implement (1):**

```python
execute(app: 'App') -> R_co
```

**Implementations (122):** `AddOverlayElement` · `ApplyMask` · `BuildPreview` · `CheckForUpdate` · `ConfigureSlideshow` · `ConnectDevice` · `ControlCenterSnapshot` · `DeleteOverlayElement` · `DeleteTheme` · `DeviceConnectionIssues` · `DeviceState` · `DisableAutostart` · `DisconnectDevice` · `DiscoverDevices` · `EnableAutostart` · `EnableLedTestMode` · `EnableOverlay` · `EnsureConnected` · `EnsureDataDownload` · `ExportConfig` · `ExportDcTheme` · `ExportOverlay` · `ExportTheme` · `FlashOverlayElement` · `GenerateDebugReport` · `GetAutostartStatus` · `GetFirstRunStatus` · `GetPaths` · `GetPlatformInfo` · `ImportConfig` · `ImportTheme` · `InitializeLed` · `KeepAliveLoop` · `LcdSnapshot` · `LedSnapshot` · `ListCloudThemes` · `ListDisks` · `ListFans` · `ListFonts` · `ListGpus` · `ListLanguages` · `ListLedModes` · `ListLedStyles` · `ListMasks` · `ListSensors` · `ListThemes` · `ListWebThemes` · `LoadCloudTheme` · `LoadImage` · `LoadTheme` · `LoadVideo` · `LoopVideo` · `MarkFirstRunDone` · `PauseVideo` · `PlayVideo` · `ReadSensors` · `RenderAndSend` · `RenderDcStandalone` · `RenderLed` · `ResetDevice` · `ResolveOverlay` · `RestoreDeviceState` · `RestoreLastTheme` · `RunDoctor` · `RunHealthCheck` · `RunQuickstart` · `RunSetup` · `RunUpgrade` · `SaveTheme` · `SeekVideo` · `SelectZone` · `SendColor` · `SendFrame` · `SendImage` · `SetActiveDevice` · `SetBackground` · `SetBackgroundMode` · `SetBrightness` · `SetClockFormat` · `SetDateFormat` · `SetDiskIndex` · `SetFitMode` · `SetGpuDevice` · `SetHddEnabled` · `SetLanguage` · `SetLedBrightness` · `SetLedColor` · `SetLedColors` · `SetLedLoadSource` · `SetLedMode` · `SetLedTempSource` · `SetLedZoneBrightness` · `SetLedZoneColor` · `SetLedZoneMode` · `SetLedZoneSync` · `SetLedZoneSyncInterval` · `SetLedZoneSyncZones` · `SetMaskPosition` · `SetMaskVisible` · `SetMediaPlayer` · `SetMemoryRatio` · `SetOrientation` · `SetOverlayBackground` · `SetOverlayConfig` · `SetRefreshInterval` · `SetSlideshow` · `SetSplitMode` · `SetTempUnit` · `SetTimeFormat` · `SetWeekStart` · `SleepDevice` · `StartScreencast` · `StopScreencast` · `StopVideo` · `TickDisplay` · `ToggleLed` · `ToggleSegment` · `ToggleVideo` · `UpdateOverlayElement` · `UploadBootAnimation` · `UploadCustomMask` · `VideoStatus`

## DataInstaller

`core/ports.py`

Port for installing on-demand data archives (themes / web / masks).

**You implement (1):**

```python
install(archive_name: 'str', target_dir: 'Path', subpath: 'str' = '') -> bool
```

**Implementations (1):** `HttpDataInstaller`

## HttpFetcher

`core/ports.py`

Tiny port for "fetch bytes from URL" used by cloud-theme adapters.

**You implement (1):**

```python
fetch(url: 'str', timeout_s: 'float' = 30.0) -> bytes
```

**Implementations (1):** `UrllibHttpFetcher`

## MissPolicy

`core/factory.py`

What a registry does when a key is not registered.

**You implement (1):**

```python
resolve(name: 'str', key: 'K', table: 'Mapping[K, V]') -> V
```

**Implementations (2):** `FallBackTo` · `Reject`

## Query

`core/commands/_base.py`

A question.  Answers, and changes nothing.

**You implement (1):**

```python
execute(app: 'App') -> R_co
```

**Implementations (28):** `BuildPreview` · `CheckForUpdate` · `ControlCenterSnapshot` · `DeviceConnectionIssues` · `DeviceState` · `GetAutostartStatus` · `GetFirstRunStatus` · `GetPaths` · `GetPlatformInfo` · `LcdSnapshot` · `LedSnapshot` · `ListCloudThemes` · `ListDisks` · `ListFans` · `ListFonts` · `ListGpus` · `ListLanguages` · `ListLedModes` · `ListLedStyles` · `ListMasks` · `ListSensors` · `ListThemes` · `ListWebThemes` · `ReadSensors` · `ResolveOverlay` · `RunDoctor` · `RunHealthCheck` · `VideoStatus`

## ScreenCapture

`core/ports.py`

Port for "grab a rectangle off the desktop right now".

**You implement (1):**

```python
grab_region(x: 'int', y: 'int', width: 'int', height: 'int') -> RawFrame
```

**Implementations (1):** `QtScreenCapture`

## _HidBinding

`adapters/device/transport.py`

One ``hid`` python binding.  Children differ only in how a handle is opened and put into blocking mode; everything downstream is shared.

**You implement (1):**

```python
open(vid: 'int', pid: 'int', serial: 'str | None') -> Any
```

**Implementations (2):** `_ApmortonHidBinding` · `_CythonHidBinding`

## DataInstallRunner

`core/ports.py`

Runs per-resolution data installs OFF the caller's thread.

**You implement (2):**

```python
shutdown() -> None
submit(resolution: 'tuple[int, int]') -> None
```

**Implementations (2):** `SyncDataInstallRunner` · `ThreadDataInstallRunner`

## _MappingPort

`adapters/sensors/_hwinfo.py`

Adapter contract for the HWiNFO shared-memory backing store.

**You implement (2):**

```python
close() -> None
read(offset: 'int', length: 'int') -> bytes
```

**Implementations (2):** `_BytesMapping` · `_HWiNFOMapping`

## BaseBulkDevice

`adapters/device/_base.py`

Shared vocabulary for the wires that speak raw USB bulk endpoints.

**You implement (3):**

```python
_do_handshake() -> HandshakeResult
_prepare_frame(payload: 'Any') -> bytes
_write_frame(frame: 'bytes') -> bool
```

**Implementations (4):** `BulkLcd` · `HidLcd` · `Led` · `LyLcd`

## BaseDevice

`adapters/device/_base.py`

Shared lifecycle for every concrete wire :class:`Device`.

**You implement (3):**

```python
_do_handshake() -> HandshakeResult
_prepare_frame(payload: 'Any') -> bytes
_write_frame(frame: 'bytes') -> bool
```

**You inherit (4):** `connect` · `disconnect` · `profile` · `send`

**Implementations (5):** `BulkLcd` · `HidLcd` · `Led` · `LyLcd` · `ScsiLcd`

## Device

`core/ports.py`

A physical USB device we control.

**Extend `BaseDevice / BaseBulkDevice (adapters/device/_base.py)`**, not this port directly — it already implements the shared half.

**Register by naming your key in the class line:**

```python
class MyLcd(BaseDevice[BulkTransport], wire=Wire.MINE):
```

**You implement (3):**

```python
connect() -> HandshakeResult
disconnect() -> None
send(payload: 'Any') -> bool
```

**You inherit (12):** `can_boot_animate` · `handshake` · `is_connected` · `is_led` · `key` · `led_handshake` · `needs_keepalive` · `profile` · `quirks` · `send_boot_animation` · `set_permission_hint` · `set_quirks`

**Implementations (5):** `BulkLcd` · `HidLcd` · `Led` · `LyLcd` · `ScsiLcd`

## DiskSource

`core/ports.py`

One storage device's thermal sensor (NVMe / SATA SSD / HDD).

**You implement (3):**

```python
key() -> str
name() -> str
temp() -> float | None
```

**Implementations (2):** `HwmonDisk` · `LhmDisk`

## DramSource

`core/ports.py`

One memory module's SPD-hub thermal sensor.

**You implement (3):**

```python
key() -> str
name() -> str
temp() -> float | None
```

**Implementations (1):** `HwmonDram`

## HotplugMonitor

`core/ports.py`

Background listener that pushes hardware events onto the EventBus.

**You implement (3):**

```python
is_running() -> bool
start(bus: 'EventBus') -> None
stop() -> None
```

**Implementations (5):** `FreeBSDHotplugMonitor` · `LinuxHotplugMonitor` · `NoopHotplugMonitor` · `PollingHotplugMonitor` · `WindowsHotplugMonitor`

## SendScheduler

`core/ports.py`

Drives :class:`SendTask` instances.  One impl per execution model.

**You implement (3):**

```python
add(task: 'SendTask') -> None
remove(key: 'str') -> None
shutdown() -> None
```

**Implementations (2):** `SyncSendScheduler` · `ThreadSendScheduler`

## AutostartManager

`core/ports.py`

Helper class that provides a standard way to create an ABC using inheritance.

**You implement (4):**

```python
disable() -> None
enable() -> None
is_enabled() -> bool
refresh() -> None
```

**Implementations (4):** `MacOSAutostart` · `NoopAutostart` · `WindowsAutostart` · `XdgDesktopAutostart`

## CloudCatalog

`core/ports.py`

Port for the hosted cloud theme catalog.

**You implement (4):**

```python
categories() -> tuple[CloudCategory, ...]
download_preview(theme_id: 'str', resolution: 'str | None' = None) -> Path
download_theme(theme_id: 'str', resolution: 'str | None' = None) -> Path
list_themes(category: 'str' = 'all') -> list[CloudThemeEntry]
```

**Implementations (1):** `CzhordeCatalog`

## FanSource

`core/ports.py`

One fan — may be role-mapped (cpu/gpu/sys1) or anonymous.

**You implement (4):**

```python
key() -> str
name() -> str
percent() -> float | None
rpm() -> int | None
```

**Implementations (3):** `HwmonFan` · `SmcFan` · `SysctlFan`

## MemorySource

`core/ports.py`

System RAM.

**You implement (4):**

```python
available() -> float | None
percent() -> float | None
total() -> float | None
used() -> float | None
```

**Implementations (2):** `MemorySourceChain` · `PsutilMemory`

## Paths

`core/ports.py`

Filesystem locations.  Each OS resolves these differently.

**Extend `BasePaths (adapters/system/_base.py) — implements all four`**, not this port directly — it already implements the shared half.

**You implement (4):**

```python
config_dir() -> Path
data_dir() -> Path
log_file() -> Path
user_content_dir() -> Path
```

**You inherit (9):** `cloud_mask_dir` · `cloud_theme_dir` · `theme_dir` · `user_background_dir` · `user_data_dir` · `user_mask_dir` · `user_media_player_dir` · `user_screencast_dir` · `user_theme_dir`

**Implementations (5):** `BSDPaths` · `BasePaths` · `LinuxPaths` · `MacOSPaths` · `WindowsPaths`

## SendTask

`core/ports.py`

One unit of work a :class:`SendScheduler` drives on its own cadence.

**You implement (4):**

```python
key() -> str
run_once(now: 'float') -> float
wait(timeout: 'float') -> None
wake() -> None
```

**Implementations (1):** `DeviceSender`

## BulkTransport

`core/ports.py`

Abstract USB bulk/interrupt transport.  One per open device handle.

**You implement (5):**

```python
close() -> None
is_open() -> bool
open() -> bool
read(endpoint: 'int', length: 'int', timeout_ms: 'int' = 100) -> bytes
write(endpoint: 'int', data: 'WriteBuffer', timeout_ms: 'int' = 100) -> int
```

**Implementations (2):** `HidApiTransport` · `PyUsbBulkTransport`

## CpuSource

`core/ports.py`

Primary CPU.  usage/freq nearly always present; temp/power may be None.

**You implement (5):**

```python
freq() -> float | None
name() -> str
power() -> float | None
temp() -> float | None
usage() -> float | None
```

**Implementations (10):** `CpuSourceChain` · `HwinfoCpu` · `HwmonCpu` · `LhmCpu` · `MacosHidCpu` · `PowermetricsCpu` · `PsutilCpu` · `SmcCpu` · `SysctlCpu` · `WmiAcpiCpu`

## ScsiTransport

`core/ports.py`

Abstract SCSI transport.  One per open device handle.

**You implement (5):**

```python
close() -> None
is_open() -> bool
open() -> bool
read_cdb(cdb: 'bytes', length: 'int', timeout_ms: 'int' = 5000) -> bytes
send_cdb(cdb: 'bytes', data: 'bytes', timeout_ms: 'int' = 5000) -> bool
```

**Implementations (3):** `LinuxScsiTransport` · `UsbBotScsiTransport` · `WindowsScsiTransport`

## Diagnostics

`core/ports.py`

Port for system diagnostics.  Concrete: ``DiagnosticsAdapter`` (``adapters/diagnostics/adapter.py``).

**You implement (7):**

```python
debug_report(log_tail_lines: 'int') -> str
doctor() -> DoctorResult
gpu_reader_state() -> GpuReaderState
health() -> HealthReport
package_manager() -> str | None
render_doctor(report: 'HealthReport') -> str
write_debug_report(rendered: 'str', path: 'Path') -> Path
```

**Implementations (1):** `DiagnosticsAdapter`

## BaseOS

`adapters/system/_base.py`

Shared skeleton for every concrete OS :class:`Platform`.

**You implement (8):**

```python
_build_autostart() -> AutostartManager
_build_hotplug() -> HotplugMonitor
_build_sensors() -> SensorEnumerator
_make_paths() -> Paths
_open_scsi(vid: 'int', pid: 'int', serial: 'str | None' = None) -> ScsiTransport
check_permissions() -> list[str]
distro_name() -> str
setup(interactive: 'bool' = True) -> int
```

**You inherit (8):** `autostart` · `hotplug` · `install_method` · `open_transport` · `paths` · `scan_devices` · `sensors` · `software_install_hint`

**Implementations (7):** `BsdOS` · `FreeBsdOS` · `LinuxPlatform` · `MacOSPlatform` · `NetBsdOS` · `OpenBsdOS` · `WindowsPlatform`

## SensorEnumerator

`core/ports.py`

OS-level sensor root.  Each OS has one implementation.

**You implement (9):**

```python
cpu() -> CpuSource
discover() -> list[SensorReading]
fans() -> list[FanSource]
gpus() -> list[GpuSource]
memory() -> MemorySource
read_all() -> dict[str, float]
read_one(sensor_id: 'str') -> float | None
start_polling(interval_s: 'float' = 2.0) -> None
stop_polling() -> None
```

**You inherit (3):** `primary_gpu` · `set_preferred_gpu` · `snapshot`

**Implementations (1):** `BaselineSensors`

## GpuSource

`core/ports.py`

One GPU — NVIDIA/AMD/Intel/Apple, discrete or integrated.

**You implement (10):**

```python
clock() -> float | None
fan() -> float | None
is_discrete() -> bool
key() -> str
name() -> str
power() -> float | None
temp() -> float | None
usage() -> float | None
vram_total() -> float | None
vram_used() -> float | None
```

**Implementations (10):** `AmdGpu` · `GpuSourceChain` · `HwinfoGpu` · `IntelGpu` · `LhmGpu` · `MacosHidGpu` · `NvidiaGpu` · `PowermetricsGpu` · `SmcGpu` · `WmiVideoControllerGpu`

## Platform

`core/ports.py`

OS abstraction.  DI'd into App at startup.

**Extend `BaseOS (adapters/system/_base.py)`**, not this port directly — it already implements the shared half.

**Register by naming your key in the class line:**

```python
class MyPlatform(BaseOS, key="myos"):
```

**You implement (10):**

```python
autostart() -> AutostartManager
check_permissions() -> list[str]
distro_name() -> str
hotplug() -> HotplugMonitor
install_method() -> str
open_transport(wire: 'Wire', vid: 'int', pid: 'int', serial: 'str | None' = None) -> Transport
paths() -> Paths
scan_devices() -> list[DeviceInfo]
sensors() -> SensorEnumerator
setup(interactive: 'bool' = True) -> int
```

**You inherit (9):** `configure_stdout` · `disk_info` · `memory_info` · `minimize_on_close` · `no_devices_hint` · `permission_denied_hint` · `software_install_hint` · `usb_power_state` · `worker_thread_context`

**Implementations (7):** `BsdOS` · `FreeBsdOS` · `LinuxPlatform` · `MacOSPlatform` · `NetBsdOS` · `OpenBsdOS` · `WindowsPlatform`

## Renderer

`core/ports.py`

Rendering backend.  Concrete: QtRenderer (adapters/render/qt.py).

**You implement (13):**

```python
apply_brightness(surface: 'Any', percent: 'int') -> Any
composite(base: 'Any', overlay: 'Any', position: 'tuple[int, int]', mask: 'Any | None' = None) -> Any
create_surface(width: 'int', height: 'int', color: 'tuple[int, ...] | None' = None) -> Any
decode_image(data: 'bytes') -> Any
draw_text(surface: 'Any', x: 'int', y: 'int', text: 'str', color: 'str', size: 'int', bold: 'bool' = False, italic: 'bool' = False, family: 'str' = '') -> None
encode_jpeg(surface: 'Any', quality: 'int' = 95, max_size: 'int' = 0) -> bytes
encode_rgb565(surface: 'Any', byte_order: 'str' = '>') -> bytes
flip_horizontal(surface: 'Any') -> Any
from_raw_rgb24(frame: 'RawFrame') -> Any
open_image(path: 'Path') -> Any
resize(surface: 'Any', width: 'int', height: 'int') -> Any
rotate(surface: 'Any', degrees: 'int') -> Any
surface_size(surface: 'Any') -> tuple[int, int]
```

**You inherit (6):** `bg_fit` · `build_frame` · `encode_payload` · `encode_png` · `get_pixels_rgb` · `list_fonts`

**Implementations (1):** `QtRenderer`


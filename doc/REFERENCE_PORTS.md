# Port reference

**Generated — do not edit.** `PYTHONPATH=src python3 dev/gen_ports_reference.py`

Every abstract contract in the tree: what a new implementation must write, what it inherits for free, and who already implements it.

Ordered **cheapest to extend first** — the ports at the top are where this codebase welcomes a contributor, the ones at the bottom are where it does not yet.

80 ports.

| port | implement | inherit | implementations |
|---|---|---|---|
| [`BaseSendTask`](#basesendtask) | 1 | 4 | 6 |
| [`Command`](#command) | 1 | 1 | 164 |
| [`CpuSource`](#cpusource) | 1 | 5 | 11 |
| [`DataInstaller`](#datainstaller) | 1 | 0 | 1 |
| [`HttpFetcher`](#httpfetcher) | 1 | 0 | 2 |
| [`MissPolicy`](#misspolicy) | 1 | 0 | 2 |
| [`ModuleCommand`](#modulecommand) | 1 | 1 | 6 |
| [`ModuleQuery`](#modulequery) | 1 | 0 | 2 |
| [`PasswordPort`](#passwordport) | 1 | 0 | 1 |
| [`Query`](#query) | 1 | 0 | 46 |
| [`ReadingsPort`](#readingsport) | 1 | 0 | 1 |
| [`ScreenCapture`](#screencapture) | 1 | 1 | 4 |
| [`ThemesPort`](#themesport) | 1 | 0 | 1 |
| [`UserInterface`](#userinterface) | 1 | 11 | 5 |
| [`_QtUI`](#_qtui) | 1 | 3 | 2 |
| [`BaseGlSurface`](#baseglsurface) | 2 | 1 | 3 |
| [`BaseModule`](#basemodule) | 2 | 0 | 1 |
| [`DataInstallRunner`](#datainstallrunner) | 2 | 0 | 2 |
| [`FilesPort`](#filesport) | 2 | 0 | 1 |
| [`FrameSource`](#framesource) | 2 | 0 | 1 |
| [`FramesPort`](#framesport) | 2 | 0 | 1 |
| [`GpuPort`](#gpuport) | 2 | 0 | 1 |
| [`IdentifiedSource`](#identifiedsource) | 2 | 0 | 25 |
| [`Module`](#module) | 2 | 9 | 1 |
| [`PanelsPort`](#panelsport) | 2 | 0 | 1 |
| [`RgbHardware`](#rgbhardware) | 2 | 0 | 2 |
| [`SingleFileTheme`](#singlefiletheme) | 2 | 0 | 1 |
| [`TlsIdentity`](#tlsidentity) | 2 | 0 | 1 |
| [`VideoExportRunner`](#videoexportrunner) | 2 | 0 | 2 |
| [`_HidBinding`](#_hidbinding) | 2 | 0 | 2 |
| [`_MappingPort`](#_mappingport) | 2 | 0 | 2 |
| [`BackgroundKind`](#backgroundkind) | 3 | 0 | 1 |
| [`BaseBulkDevice`](#basebulkdevice) | 3 | 0 | 4 |
| [`BaseDevice`](#basedevice) | 3 | 4 | 5 |
| [`BoardTempSource`](#boardtempsource) | 3 | 0 | 2 |
| [`CommandBus`](#commandbus) | 3 | 1 | 7 |
| [`Device`](#device) | 3 | 15 | 5 |
| [`DiskSource`](#disksource) | 3 | 0 | 3 |
| [`DramSource`](#dramsource) | 3 | 0 | 1 |
| [`FanSource`](#fansource) | 3 | 2 | 4 |
| [`GpuSource`](#gpusource) | 3 | 10 | 14 |
| [`HotplugMonitor`](#hotplugmonitor) | 3 | 0 | 5 |
| [`IpcChannel`](#ipcchannel) | 3 | 1 | 2 |
| [`LedColorStream`](#ledcolorstream) | 3 | 0 | 1 |
| [`ModuleHost`](#modulehost) | 3 | 11 | 1 |
| [`RamAccess`](#ramaccess) | 3 | 0 | 2 |
| [`RgbMirror`](#rgbmirror) | 3 | 1 | 3 |
| [`SendScheduler`](#sendscheduler) | 3 | 0 | 2 |
| [`SoundInput`](#soundinput) | 3 | 0 | 3 |
| [`SoundPort`](#soundport) | 3 | 0 | 1 |
| [`VoltageSource`](#voltagesource) | 3 | 0 | 1 |
| [`_DeviceIoPort`](#_deviceioport) | 3 | 0 | 1 |
| [`_SharedRotatingFileHandler`](#_sharedrotatingfilehandler) | 3 | 4 | 2 |
| [`BackgroundPort`](#backgroundport) | 4 | 0 | 1 |
| [`BaseScsiTransport`](#basescsitransport) | 4 | 5 | 4 |
| [`CloudCatalog`](#cloudcatalog) | 4 | 0 | 1 |
| [`GlSurface`](#glsurface) | 4 | 0 | 3 |
| [`HardwarePort`](#hardwareport) | 4 | 0 | 1 |
| [`MemorySource`](#memorysource) | 4 | 0 | 2 |
| [`PackageManager`](#packagemanager) | 4 | 0 | 2 |
| [`PaintedVisual`](#paintedvisual) | 4 | 3 | 13 |
| [`Paths`](#paths) | 4 | 12 | 5 |
| [`SendTask`](#sendtask) | 4 | 1 | 7 |
| [`SettingsPort`](#settingsport) | 4 | 0 | 1 |
| [`SmBus`](#smbus) | 4 | 0 | 1 |
| [`BaseVisual`](#basevisual) | 5 | 0 | 16 |
| [`BulkTransport`](#bulktransport) | 5 | 0 | 2 |
| [`GlVisual`](#glvisual) | 5 | 0 | 2 |
| [`RamLights`](#ramlights) | 5 | 0 | 1 |
| [`ScsiTransport`](#scsitransport) | 5 | 0 | 4 |
| [`SuperIoChip`](#superiochip) | 5 | 0 | 3 |
| [`VisualEngine`](#visualengine) | 5 | 0 | 16 |
| [`AutostartManager`](#autostartmanager) | 6 | 0 | 4 |
| [`Diagnostics`](#diagnostics) | 7 | 0 | 1 |
| [`SensorEnumerator`](#sensorenumerator) | 11 | 8 | 1 |
| [`BaseOS`](#baseos) | 12 | 19 | 8 |
| [`Renderer`](#renderer) | 13 | 9 | 1 |
| [`Platform`](#platform) | 25 | 8 | 8 |
| [`ModuleServices`](#moduleservices) | 30 | 0 | 1 |
| [`ContentStore`](#contentstore) | 32 | 0 | 1 |

---

## BaseSendTask

`services/_send_task.py`

A :class:`SendTask` driven on a fixed cadence for one device.

**You implement (1):**

```python
_run(now: 'float') -> float
```

**You inherit (4):** `key` · `run_once` · `wait` · `wake`

**Implementations (6):** `GameModeTask` · `ReconnectWatcher` · `ScreencastDriver` · `SlideshowDriver` · `SourceDriver` · `StreamDriver`

## Command

`core/commands/_base.py`

A user action.  Exactly one execute method; returns one Result.

**You implement (1):**

```python
execute(app: 'App') -> R_co
```

**You inherit (1):** `refusal`

**Implementations (164):** `AddOverlayElement` · `AdvanceSlideshow` · `ApplyMask` · `BlinkAuraHeader` · `BuildPreview` · `CaptureScreencastFrame` · `CaptureSourceFrame` · `CaptureStreamFrame` · `CheckForUpdate` · `ClearModuleSource` · `ConfigureSlideshow` · `ConnectDevice` · `ControlCenterSnapshot` · `CurrentFrame` · `DaemonStatus` · `DeleteOverlayElement` · `DeleteTheme` · `DeviceCanvas` · `DeviceConnectionIssues` · `DeviceState` · `DisableAutostart` · `DisconnectDevice` · `DiscoverDevices` · `DownloadCloudTheme` · `EnableAutostart` · `EnableLedTestMode` · `EnableOverlay` · `EnsureConnected` · `EnsureDataDownload` · `ExportCurrentTheme` · `ExportDcTheme` · `ExportOverlay` · `ExportTheme` · `ExportVideoClip` · `GenerateDebugReport` · `GetAutostartStatus` · `GetFirstRunStatus` · `GetPaths` · `GetPlatformInfo` · `GetSensorDashboard` · `ImportTheme` · `InitializeLed` · `KeepAliveLoop` · `LcdSnapshot` · `LedSnapshot` · `ListCloudThemes` · `ListDevices` · `ListDiskSensors` · `ListDisks` · `ListFans` · `ListFonts` · `ListGpus` · `ListLanguages` · `ListLedModes` · `ListLedStyles` · `ListMasks` · `ListMemorySlots` · `ListModules` · `ListSensors` · `ListThemes` · `ListVisuals` · `ListWebThemes` · `LoadCloudTheme` · `LoadImage` · `LoadTheme` · `LoadVideo` · `LoopVideo` · `MarkFirstRunDone` · `PauseVideo` · `PlayVideo` · `PlayVisual` · `PreviewSize` · `ProbeVideoDuration` · `ProvideApiTls` · `RamLighting` · `ReadSensors` · `RecentColors` · `RefreshAutostart` · `RememberColor` · `RenderAndSend` · `RenderDcStandalone` · `RenderLed` · `ResetDevice` · `ResolveOverlay` · `ResolveThemeDirectories` · `RestoreDeviceState` · `RgbFollow` · `RgbLights` · `RunDoctor` · `RunHealthCheck` · `RunQuickstart` · `RunSetup` · `RunUpgrade` · `SaveTheme` · `ScanRgbLights` · `SeekVideo` · `SelectZone` · `SendColor` · `SendFrame` · `SendImage` · `SendScreencastFrame` · `SetAuraEffect` · `SetAuraLeds` · `SetBackground` · `SetBackgroundMode` · `SetBrightness` · `SetClockFormat` · `SetDateFormat` · `SetDiskDevice` · `SetFitMode` · `SetGameMode` · `SetGpuDevice` · `SetHddEnabled` · `SetLanguage` · `SetLedBrightness` · `SetLedColor` · `SetLedColors` · `SetLedLoadSource` · `SetLedMode` · `SetLedTempSource` · `SetLedZoneBrightness` · `SetLedZoneColor` · `SetLedZoneMode` · `SetLedZoneSync` · `SetLedZoneSyncInterval` · `SetLedZoneSyncZones` · `SetLinkedColor` · `SetMaskPosition` · `SetMaskVisible` · `SetMediaPlayer` · `SetMemoryRatio` · `SetModuleEnabled` · `SetOrientation` · `SetOverlayBackground` · `SetOverlayConfig` · `SetRamEffect` · `SetRamLighting` · `SetRefreshInterval` · `SetRgbFollow` · `SetScreencastRegion` · `SetSensorDashboard` · `SetSlideshow` · `SetSplitMode` · `SetTempUnit` · `SetTimeFormat` · `SetVisualCenter` · `SetVisualPicture` · `SetVisualSettings` · `SetWeekStart` · `SleepDevice` · `StartScreencast` · `StopDaemon` · `StopScreencast` · `StopVideo` · `TickDisplay` · `TickGameMode` · `ToggleLed` · `ToggleVideo` · `UpdateOverlayElement` · `UpdateStatus` · `UploadBootAnimation` · `UploadCustomMask` · `VideoStatus` · `VisualTile`

## CpuSource

`core/ports.py`

Primary CPU.  usage/freq nearly always present; temp/power may be None.

**You implement (1):**

```python
name() -> str
```

**You inherit (5):** `freq` · `power` · `temp` · `temp_label` · `usage`

**Implementations (11):** `CpuSourceChain` · `HwinfoCpu` · `HwmonCpu` · `LhmCpu` · `MacosHidCpu` · `PawnioCpu` · `PowermetricsCpu` · `PsutilCpu` · `SmcCpu` · `SysctlCpu` · `WmiAcpiCpu`

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
fetch(url: 'str', timeout_s: 'float' = 30.0, max_bytes: 'int | None' = None) -> bytes
```

**Implementations (2):** `OfflineHttpFetcher` · `UrllibHttpFetcher`

## MissPolicy

`core/factory.py`

What a registry does when a key is not registered.

**You implement (1):**

```python
resolve(name: 'str', key: 'K', table: 'Mapping[K, V]') -> V
```

**Implementations (2):** `FallBackTo` · `Reject`

## ModuleCommand

`core/commands/_base.py`

A Command a module owns, in that module's ``core/`` -- the front door of its mini hexagon.  It runs against the module's own root (its ``app.py`` class: its services, its host), never the App, so a module's work reaches the App only through the ports its host offers.

**You implement (1):**

```python
run(module: 'M') -> R_co
```

**You inherit (1):** `execute`

**Implementations (6):** `ListVisuals` · `PlayVisual` · `SetVisualCenter` · `SetVisualPicture` · `SetVisualSettings` · `VisualTile`

## ModuleQuery

`core/commands/_base.py`

A question a module answers -- a :class:`Query` (changes nothing, logs at DEBUG) that runs against the module, like :class:`ModuleCommand`.

**You implement (1):**

```python
run(module: 'M') -> R_co
```

**Implementations (2):** `ListVisuals` · `VisualTile`

## PasswordPort

`core/ports.py`

The ``password`` capability: system access the user grants once.

**You implement (1):**

```python
memory_access() -> RamAccess
```

**Implementations (1):** `_Password`

## Query

`core/commands/_base.py`

A question.  Answers, and changes nothing.

**You implement (1):**

```python
execute(app: 'App') -> R_co
```

**Implementations (46):** `BuildPreview` · `CheckForUpdate` · `ControlCenterSnapshot` · `CurrentFrame` · `DaemonStatus` · `DeviceCanvas` · `DeviceConnectionIssues` · `DeviceState` · `GetAutostartStatus` · `GetFirstRunStatus` · `GetPaths` · `GetPlatformInfo` · `GetSensorDashboard` · `LcdSnapshot` · `LedSnapshot` · `ListCloudThemes` · `ListDevices` · `ListDiskSensors` · `ListDisks` · `ListFans` · `ListFonts` · `ListGpus` · `ListLanguages` · `ListLedModes` · `ListLedStyles` · `ListMasks` · `ListMemorySlots` · `ListModules` · `ListSensors` · `ListThemes` · `ListVisuals` · `ListWebThemes` · `PreviewSize` · `ProbeVideoDuration` · `RamLighting` · `ReadSensors` · `RecentColors` · `ResolveOverlay` · `ResolveThemeDirectories` · `RgbFollow` · `RgbLights` · `RunDoctor` · `RunHealthCheck` · `UpdateStatus` · `VideoStatus` · `VisualTile`

## ReadingsPort

`core/ports.py`

The ``readings`` capability: the computer's sensor readings.

**You implement (1):**

```python
latest() -> Mapping[str, float] | None
```

**Implementations (1):** `_Readings`

## ScreenCapture

`core/ports.py`

Port for "grab a rectangle off the desktop right now".

**You implement (1):**

```python
grab_region(x: 'int', y: 'int', width: 'int', height: 'int') -> RawFrame
```

**You inherit (1):** `stop`

**Implementations (4):** `GdiScreenCapture` · `PipeWireScreenCapture` · `QtNativeCapture` · `ToolCapture`

## ThemesPort

`core/ports.py`

The ``themes`` capability: backgrounds themes carry.

**You implement (1):**

```python
add_kind(kind: 'BackgroundKind') -> None
```

**Implementations (1):** `_Themes`

## UserInterface

`ui/_base.py`

One face of the one app — CLI, API, GUI, qtgui, daemon.

**You implement (1):**

```python
run() -> int
```

**You inherit (11):** `announce` · `bring_up` · `compose` · `dispatch` · `events` · `on_app_gone` · `preflight` · `remote` · `start` · `stop` · `teardown`

**Implementations (5):** `ApiUI` · `CliUI` · `DaemonUI` · `GuiUI` · `QtGuiUI`

## _QtUI

`ui/_uis.py`

Shared base for the two widget skins.  Intermediate — not registered.

**You implement (1):**

```python
run() -> int
```

**You inherit (3):** `announce` · `compose` · `stop`

**Implementations (2):** `GuiUI` · `QtGuiUI`

## BaseGlSurface

`adapters/visual/_gl.py`

What every OS's context shares: its calls bound (``gl``), and the check that its OpenGL can draw at all.

**You implement (2):**

```python
address(name: 'str') -> int
close() -> None
```

**You inherit (1):** `describe`

**Implementations (3):** `CglSurface` · `EglSurface` · `WglSurface`

## BaseModule

`adapters/modules/_base.py`

What a module extends: registers itself under the ``key=`` it names. ``key=None`` is an intermediate base, not registered.

**You implement (2):**

```python
_disable(host: 'ModuleHost') -> None
_enable(host: 'ModuleHost') -> None
```

**Implementations (1):** `VisualizerModule`

## DataInstallRunner

`core/ports.py`

Runs per-resolution data installs OFF the caller's thread.

**You implement (2):**

```python
shutdown() -> None
submit(resolution: 'tuple[int, int]', variant: 'str' = '', mask_variant: 'str' = '') -> None
```

**Implementations (2):** `SyncDataInstallRunner` · `ThreadDataInstallRunner`

## FilesPort

`core/ports.py`

The ``files`` capability: TRCC's data folder, and pictures.

**You implement (2):**

```python
data_root() -> Path
encode_png(frame: 'RawFrame') -> bytes
```

**Implementations (1):** `_Files`

## FrameSource

`core/ports.py`

Pictures for a panel's background, drawn by someone other than the App -- a module's audio visual.  The App takes the newest each tick, puts the theme's mask and metrics on top, and sends it (``CaptureSourceFrame``). A source draws on its own thread and keeps only its newest picture.

**You implement (2):**

```python
close() -> None
latest() -> RawFrame
```

**Implementations (1):** `VisualReader`

## FramesPort

`core/ports.py`

The ``frames`` capability: the pictures the LCD panels are sent.

**You implement (2):**

```python
unwatch(handler: 'Callable[[PanelPicture], None]') -> None
watch(handler: 'Callable[[PanelPicture], None]') -> None
```

**Implementations (1):** `_Frames`

## GpuPort

`core/ports.py`

The ``gpu`` capability: the graphics card.

**You implement (2):**

```python
substitute(cls: '_C') -> _C
surface() -> type[GlSurface] | None
```

**Implementations (1):** `_Gpu`

## IdentifiedSource

`core/ports.py`

A sensor the OS can enumerate SEVERAL of, each one identifiable.

**You implement (2):**

```python
key() -> str
name() -> str
```

**Implementations (25):** `AdlGpu` · `AmdGpu` · `D3dkmtGpu` · `GpuSourceChain` · `HwinfoGpu` · `HwmonDisk` · `HwmonDram` · `HwmonFan` · `HwmonVoltage` · `IgclGpu` · `IntelGpu` · `LhmDisk` · `LhmGpu` · `MacosHidGpu` · `NouveauGpu` · `NvidiaGpu` · `PowermetricsGpu` · `PsutilBoardTemp` · `SmcFan` · `SmcGpu` · `SuperIoFan` · `SuperIoTemp` · `SysctlFan` · `WindowsDisk` · `WmiVideoControllerGpu`

## Module

`core/ports.py`

One feature a user switches on or off on the Modules page -- in every UI, through ``SetModuleEnabled``.

**You implement (2):**

```python
_disable(host: 'ModuleHost') -> None
_enable(host: 'ModuleHost') -> None
```

**You inherit (9):** `disable` · `enable` · `post_disable` · `post_enable` · `pre_disable` · `pre_enable` · `problem` · `start` · `stop`

**Implementations (1):** `VisualizerModule`

## PanelsPort

`core/ports.py`

The ``panels`` capability: the LCD panels attached.

**You implement (2):**

```python
attached() -> tuple[str, ...]
size(key: 'str') -> tuple[int, int] | None
```

**Implementations (1):** `_Panels`

## RgbHardware

`core/ports.py`

What Find reads to spot RGB controllers.

**You implement (2):**

```python
board() -> BoardIdentity | None
hid_interfaces() -> tuple[HidInterface, ...]
```

**Implementations (2):** `SysfsRgbHardware` · `_NoRgbHardware`

## SingleFileTheme

`core/ports.py`

A one-file theme directory being assembled — yielded by :meth:`ContentStore.single_file_theme`.

**You implement (2):**

```python
adopt(produced: 'Path', filename: 'str') -> Path
install(source: 'Path', filename: 'str') -> Path
```

**Implementations (1):** `FileSingleFileTheme`

## TlsIdentity

`core/ports.py`

Port for the LAN API's TLS files.  Concrete: ``CryptographyTls`` (``adapters/infra/tls.py``).

**You implement (2):**

```python
self_signed(directory: 'Path', bind_host: 'str') -> TlsFiles
supplied(cert: 'Path', key: 'Path') -> TlsFiles
```

**Implementations (1):** `CryptographyTls`

## VideoExportRunner

`core/ports.py`

Encodes ``Theme.zt`` clips OFF the caller's thread.

**You implement (2):**

```python
shutdown() -> None
submit(token: 'str', request: 'VideoExportRequest') -> None
```

**Implementations (2):** `SyncVideoExportRunner` · `ThreadVideoExportRunner`

## _HidBinding

`adapters/device/transport.py`

One ``hid`` python binding.  Children differ only in how a handle is opened and put into blocking mode; everything downstream is shared.

**You implement (2):**

```python
open(vid: 'int', pid: 'int', serial: 'str | None') -> Any
open_path(path: 'bytes') -> Any
```

**Implementations (2):** `_ApmortonHidBinding` · `_CythonHidBinding`

## _MappingPort

`adapters/sensors/_hwinfo.py`

Adapter contract for the HWiNFO shared-memory backing store.

**You implement (2):**

```python
close() -> None
read(offset: 'int', length: 'int') -> bytes
```

**Implementations (2):** `_BytesMapping` · `_HWiNFOMapping`

## BackgroundKind

`core/ports.py`

A kind of panel background a module draws, as a theme names it: a background ref ``<prefix>/<id>`` whose folder holds its settings (``config.json``), not a picture.  The module registers one (``ModuleHost.add_background_kind``); themes load, save and restore it through here, never knowing what draws it.

**You implement (3):**

```python
play(key: 'str', payload: 'Mapping[str, Any]') -> Result
resume(key: 'str') -> None
snapshot(key: 'str') -> dict[str, Any] | None
```

**Implementations (1):** `VisualBackground`

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

## BoardTempSource

`core/ports.py`

One motherboard / super-I/O temperature input.

**You implement (3):**

```python
key() -> str
name() -> str
temp() -> float | None
```

**Implementations (2):** `PsutilBoardTemp` · `SuperIoTemp`

## CommandBus

`core/ports.py`

What every UI talks to: send a Command, observe the bus.

**You implement (3):**

```python
dispatch(cmd: 'Command[_R]') -> _R
events() -> EventBus
remote() -> bool
```

**You inherit (1):** `on_app_gone`

**Implementations (7):** `ApiUI` · `App` · `AppProxy` · `CliUI` · `DaemonUI` · `GuiUI` · `QtGuiUI`

## Device

`core/ports.py`

A physical USB device we control.

**Extend `BaseDevice` (`adapters/device/_base.py`)**, not this port directly — it answers all 3, leaving you 3 of its own to write (listed under [`BaseDevice`](#basedevice)).

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

**You inherit (15):** `can_boot_animate` · `handshake` · `is_connected` · `is_led` · `keepalive_interval` · `key` · `led_handshake` · `needs_keepalive` · `profile` · `quirks` · `send_boot_animation` · `set_permission_hint` · `set_quirks` · `set_state_dir` · `set_unit`

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

**Implementations (3):** `HwmonDisk` · `LhmDisk` · `WindowsDisk`

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

## FanSource

`core/ports.py`

One fan — may be role-mapped (cpu/gpu/sys1) or anonymous.

**You implement (3):**

```python
key() -> str
name() -> str
rpm() -> int | None
```

**You inherit (2):** `on_gpu` · `percent`

**Implementations (4):** `HwmonFan` · `SmcFan` · `SuperIoFan` · `SysctlFan`

## GpuSource

`core/ports.py`

One GPU — NVIDIA/AMD/Intel/Apple, discrete or integrated.

**You implement (3):**

```python
is_discrete() -> bool
key() -> str
name() -> str
```

**You inherit (10):** `clock` · `fan` · `fan_rpm` · `hotspot` · `mem_temp` · `power` · `temp` · `usage` · `vram_total` · `vram_used`

**Implementations (14):** `AdlGpu` · `AmdGpu` · `D3dkmtGpu` · `GpuSourceChain` · `HwinfoGpu` · `IgclGpu` · `IntelGpu` · `LhmGpu` · `MacosHidGpu` · `NouveauGpu` · `NvidiaGpu` · `PowermetricsGpu` · `SmcGpu` · `WmiVideoControllerGpu`

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

## IpcChannel

`core/ports.py`

The channel every UI uses to reach the one App (``ipc.py``).

**You implement (3):**

```python
address() -> str
connect(timeout: 'float | None') -> Any
listen() -> Any
```

**You inherit (1):** `reachable`

**Implementations (2):** `NamedPipeChannel` · `UnixSocketChannel`

## LedColorStream

`core/ports.py`

Colours another program streams for a cooler's LEDs -- OpenRGB, over DDP (#160).  The ``OPENRGB`` LED mode shows them; nothing listens until a cooler is in that mode.

**You implement (3):**

```python
colors(count: 'int') -> list[tuple[int, int, int]]
listening() -> bool
stop() -> None
```

**Implementations (1):** `DdpReceiver`

## ModuleHost

`core/ports.py`

What a module may do to the App -- and nothing more.

**You implement (3):**

```python
publish(event: 'Event') -> None
subscribe(event_type: 'type[_E]', handler: 'Callable[[_E], None]') -> None
unsubscribe(event_type: 'type[_E]', handler: 'Callable[[_E], None]') -> None
```

**You inherit (11):** `background` · `files` · `frames` · `gpu` · `hardware` · `panels` · `password` · `readings` · `settings` · `sound` · `themes`

**Implementations (1):** `ModuleScope`

## RamAccess

`core/ports.py`

The opt-in grant that lets this user reach RGB memory's bus.

**You implement (3):**

```python
disable() -> RamAccessStatus
enable() -> RamAccessStatus
status() -> RamAccessStatus
```

**Implementations (2):** `LinuxRamAccess` · `_NoRamAccess`

## RgbMirror

`core/ports.py`

Another RGB system whose devices follow the cooler's colours.

**You implement (3):**

```python
close() -> None
devices() -> tuple[RgbMirrorDevice, ...]
show(device: 'RgbMirrorDevice', colors: 'Sequence[tuple[int, int, int]]') -> None
```

**You inherit (1):** `release`

**Implementations (3):** `AuraMainboard` · `CorsairDramMirror` · `OpenRgbMirror`

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

## SoundInput

`core/ports.py`

What an audio visual hears: the computer's sound, live.

**You implement (3):**

```python
close() -> None
open() -> None
read(frames: 'int') -> bytes
```

**Implementations (3):** `LoopbackSoundInput` · `MonitorSoundInput` · `TapSoundInput`

## SoundPort

`core/ports.py`

The ``sound`` capability: what the computer is playing.

**You implement (3):**

```python
input() -> SoundInput
problem() -> str
worker_thread() -> AbstractContextManager[None]
```

**Implementations (1):** `_Sound`

## VoltageSource

`core/ports.py`

One motherboard / super-I/O voltage input (#259).

**You implement (3):**

```python
key() -> str
name() -> str
volts() -> float | None
```

**Implementations (1):** `HwmonVoltage`

## _DeviceIoPort

`adapters/sensors/_pawnio.py`

The three Win32 calls PawnIO needs -- a seam, so a test scripts them.

**You implement (3):**

```python
close(handle: 'int') -> None
ioctl(handle: 'int', code: 'int', payload: 'bytes', out_size: 'int') -> bytes
open(path: 'str') -> int
```

**Implementations (1):** `_Kernel32DeviceIo`

## _SharedRotatingFileHandler

`adapters/infra/logging.py`

A rotating handler several PROCESSES may write to at once.

**You implement (3):**

```python
_acquire() -> None
_claim_run(fd: 'int') -> None
_release() -> None
```

**You inherit (4):** `close` · `doRollover` · `emit` · `shouldRollover`

**Implementations (2):** `PosixSharedLogHandler` · `WindowsSharedLogHandler`

## BackgroundPort

`core/ports.py`

The ``background`` capability: drawing a panel's background.

**You implement (4):**

```python
draws(key: 'str') -> bool
end(key: 'str', keep_choice: 'bool' = False) -> None
show(key: 'str', source: 'FrameSource', fps: 'float') -> None
source(key: 'str') -> FrameSource | None
```

**Implementations (1):** `_Background`

## BaseScsiTransport

`adapters/device/scsi_transport.py`

Open/close, the per-frame log, failure policy and single owner -- once.

**You implement (4):**

```python
_close_handle() -> None
_open_handle() -> bool
_read(cdb: 'bytes', length: 'int', timeout_ms: 'int') -> CdbResult
_send(cdb: 'bytes', data: 'bytes', timeout_ms: 'int') -> CdbResult
```

**You inherit (5):** `close` · `is_open` · `open` · `read_cdb` · `send_cdb`

**Implementations (4):** `LinuxScsiTransport` · `ServiceScsiTransport` · `UsbBotScsiTransport` · `WindowsScsiTransport`

## CloudCatalog

`core/ports.py`

Port for the hosted cloud theme catalog.

**You implement (4):**

```python
categories() -> tuple[CloudCategory, ...]
download_theme(theme_id: 'str', resolution: 'str | None' = None) -> Path
downloaded(resolution: 'str') -> tuple[str, ...]
list_themes(category: 'str' = 'all') -> list[CloudThemeEntry]
```

**Implementations (1):** `CzhordeCatalog`

## GlSurface

`core/ports.py`

An OpenGL context with nowhere on screen to draw: current on the thread that made it, until :meth:`close`.  Each OS names its kind (``Platform.GL_SURFACE``: EGL, WGL, CGL); a module draws on it through ``host.gpu`` and never imports one.

**You implement (4):**

```python
address(name: 'str') -> int
close() -> None
describe() -> str
open() -> GlSurface
```

**Implementations (3):** `CglSurface` · `EglSurface` · `WglSurface`

## HardwarePort

`core/ports.py`

The ``hardware`` capability: lighting chips TRCC does not drive as a panel -- each reached through the OS, so a stand-in platform's scripted twin is all a test or a mock run touches.

**You implement (4):**

```python
ids() -> RgbHardware
open_hid(vid: 'int', pid: 'int', report_id: 'int') -> Transport
smbuses() -> tuple[SmBus, ...]
substitute(cls: '_C') -> _C
```

**Implementations (1):** `_Hardware`

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

## PackageManager

`core/ports.py`

What the OS's package manager can tell us about a missing tool.

**You implement (4):**

```python
install_argv(package: 'str') -> tuple[str, ...]
installed(package: 'str') -> bool
owns(path: 'str') -> str | None
provides(path: 'str') -> str | None
```

**Implementations (2):** `NoPackageManager` · `Rpm`

## PaintedVisual

`modules/visualizer/adapters/_painted.py`

A frame painted over ``BACKGROUND``, the picture's fire behind it.

**You implement (4):**

```python
_bass() -> float
_paint(painter: 'Any') -> None
adjust(settings: 'Mapping[str, float]') -> None
feed(pcm: 'bytes') -> None
```

**You inherit (3):** `close` · `open` · `render`

**Implementations (13):** `CavalierBars` · `CavalierBarsCircle` · `CavalierLevels` · `CavalierLevelsCircle` · `CavalierParticles` · `CavalierParticlesCircle` · `CavalierSpine` · `CavalierSpineCircle` · `CavalierSplitter` · `CavalierVisual` · `CavalierWave` · `CavalierWaveCircle` · `SpikeCircle`

## Paths

`core/ports.py`

Filesystem locations.  Each OS resolves these differently.

**Extend `BasePaths` (`adapters/system/_base.py`)**, not this port directly — it implements all 4 of these.

**You implement (4):**

```python
config_dir() -> Path
data_dir() -> Path
log_file() -> Path
user_content_dir() -> Path
```

**You inherit (12):** `cloud_mask_dir` · `cloud_theme_dir` · `is_user_content` · `theme_dir` · `user_background_dir` · `user_data_dir` · `user_mask_dir` · `user_media_player_dir` · `user_module_dir` · `user_module_theme_dir` · `user_screencast_dir` · `user_theme_dir`

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

**You inherit (1):** `runs_on_caller`

**Implementations (7):** `DeviceSender` · `GameModeTask` · `ReconnectWatcher` · `ScreencastDriver` · `SlideshowDriver` · `SourceDriver` · `StreamDriver`

## SettingsPort

`core/ports.py`

The ``settings`` capability: the module's own saved settings.

**You implement (4):**

```python
app() -> dict[str, Any]
panel(key: 'str') -> dict[str, Any]
save_app(data: 'Mapping[str, Any]') -> None
save_panel(key: 'str', data: 'Mapping[str, Any]') -> None
```

**Implementations (1):** `_Settings`

## SmBus

`core/ports.py`

One SMBus: byte reads and writes, and block writes, to one address.

**You implement (4):**

```python
close() -> None
read_byte_data(address: 'int', register: 'int') -> int
write_block_data(address: 'int', register: 'int', data: 'bytes') -> None
write_byte_data(address: 'int', register: 'int', value: 'int') -> None
```

**Implementations (1):** `LinuxSmBus`

## BaseVisual

`modules/visualizer/adapters/_base.py`

An audio visual: what it is (``INFO``), and whether this machine can draw it.  ``key=None`` in a class line means an intermediate base.

**You implement (5):**

```python
adjust(settings: 'Mapping[str, float]') -> None
close() -> None
feed(pcm: 'bytes') -> None
open(spec: 'VisualSpec') -> None
render() -> RawFrame
```

**Implementations (16):** `CavalierBars` · `CavalierBarsCircle` · `CavalierLevels` · `CavalierLevelsCircle` · `CavalierParticles` · `CavalierParticlesCircle` · `CavalierSpine` · `CavalierSpineCircle` · `CavalierSplitter` · `CavalierVisual` · `CavalierWave` · `CavalierWaveCircle` · `KarmaViz` · `MilkdropVisual` · `SpikeCircle` · `TrccPipes`

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

## GlVisual

`modules/visualizer/adapters/_base.py`

A visual drawn with OpenGL, on the context its platform names (:func:`drawn_on`).  Listed wherever it is registered; where no context opens -- or none is named -- the gallery shows it with the reason.

**You implement (5):**

```python
adjust(settings: 'Mapping[str, float]') -> None
close() -> None
feed(pcm: 'bytes') -> None
open(spec: 'VisualSpec') -> None
render() -> RawFrame
```

**Implementations (2):** `KarmaViz` · `TrccPipes`

## RamLights

`core/ports.py`

RGB memory TRCC drives itself: it follows, and it keeps effects.

**You implement (5):**

```python
apply_effect(device: 'RgbMirrorDevice', settings: 'RamEffectSettings') -> None
close() -> None
devices() -> tuple[RgbMirrorDevice, ...]
found() -> tuple[RgbMirrorDevice, ...] | None
show(device: 'RgbMirrorDevice', colors: 'Sequence[tuple[int, int, int]]') -> None
```

**Implementations (1):** `CorsairDramMirror`

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

**Implementations (4):** `LinuxScsiTransport` · `ServiceScsiTransport` · `UsbBotScsiTransport` · `WindowsScsiTransport`

## SuperIoChip

`adapters/sensors/_superio.py`

One vendor's chip family: its config-mode key, its chip ids, and how its monitor is found and read.  :class:`SuperIoReader` owns the order of everything; a family only knows its own chips.

**You implement (5):**

```python
enter(lpc: 'Lpc') -> None
identify(lpc: 'Lpc', chip_id: 'int') -> FoundChip | str
leave(lpc: 'Lpc', chip_id: 'int') -> None
measure(found: 'FoundChip') -> dict[str, Any]
name(chip_id: 'int') -> str | None
```

**Implementations (3):** `Ite` · `Nct668x` · `Nuvoton`

## VisualEngine

`modules/visualizer/core/ports.py`

Turns sound into pictures: one audio visual.

**You implement (5):**

```python
adjust(settings: 'Mapping[str, float]') -> None
close() -> None
feed(pcm: 'bytes') -> None
open(spec: 'VisualSpec') -> None
render() -> RawFrame
```

**Implementations (16):** `CavalierBars` · `CavalierBarsCircle` · `CavalierLevels` · `CavalierLevelsCircle` · `CavalierParticles` · `CavalierParticlesCircle` · `CavalierSpine` · `CavalierSpineCircle` · `CavalierSplitter` · `CavalierVisual` · `CavalierWave` · `CavalierWaveCircle` · `KarmaViz` · `MilkdropVisual` · `SpikeCircle` · `TrccPipes`

## AutostartManager

`core/ports.py`

Start-with-the-computer, per OS.

**You implement (6):**

```python
disable() -> None
enable(target: 'str | None' = None) -> None
entry_location() -> str
installed_target() -> str | None
is_enabled() -> bool
refresh() -> None
```

**Implementations (4):** `MacOSAutostart` · `NoopAutostart` · `WindowsAutostart` · `XdgDesktopAutostart`

## Diagnostics

`core/ports.py`

Port for system diagnostics.  Concrete: ``DiagnosticsAdapter`` (``adapters/diagnostics/adapter.py``).

**You implement (7):**

```python
debug_report(log_tail_lines: 'int', held: 'Mapping[str, Mapping[str, str]] | None' = None, probe: 'bool' = True) -> str
doctor() -> DoctorResult
gpu_reader_state() -> GpuReaderState
health() -> HealthReport
package_manager() -> str | None
render_doctor(report: 'HealthReport') -> str
write_debug_report(rendered: 'str', path: 'Path') -> Path
```

**Implementations (1):** `DiagnosticsAdapter`

## SensorEnumerator

`core/ports.py`

OS-level sensor root.  Each OS has one implementation.

**You implement (11):**

```python
cpu() -> CpuSource
discover() -> list[SensorReading]
disks() -> list[DiskSource]
fans() -> list[FanSource]
gpus() -> list[GpuSource]
memory() -> MemorySource
read_all() -> dict[str, float]
read_one(sensor_id: 'str') -> float | None
start_polling(interval_s: 'float' = 2.0, on_sweep: 'Callable[[], None] | None' = None) -> None
stop_polling() -> None
unsupported() -> frozenset[str]
```

**You inherit (8):** `fan_slots` · `preferred_disk` · `primary_gpu` · `set_fan_slot_pins` · `set_interval` · `set_preferred_disk` · `set_preferred_gpu` · `snapshot`

**Implementations (1):** `BaselineSensors`

## BaseOS

`adapters/system/_base.py`

Shared skeleton for every concrete OS :class:`Platform`.

**You implement (12):**

```python
_build_autostart() -> AutostartManager
_build_hotplug() -> HotplugMonitor
_build_sensors() -> SensorEnumerator
_make_paths() -> Paths
_open_scsi(vid: 'int', pid: 'int', serial: 'str | None' = None, unit: 'str' = '') -> ScsiTransport
check_permissions() -> list[str]
disk_info() -> list[dict[str, str]]
distro_name() -> str
memory_info() -> list[dict[str, str]]
no_devices_hint() -> str
permission_denied_hint() -> str
setup(dry_run: 'bool' = False) -> int
```

**You inherit (19):** `IPC_CHANNEL` · `autostart` · `configure_stdout` · `disk_partitions` · `display_session` · `hotplug` · `http_fetcher` · `install_method` · `open_transport` · `package_manager` · `packages` · `paths` · `scan_devices` · `screen_capture` · `sensors` · `software_install_hint` · `upgrade_hint` · `usb_power_state` · `worker_thread_context`

**Implementations (8):** `BsdOS` · `FreeBsdOS` · `GenericBsd` · `LinuxOS` · `MacOSPlatform` · `NetBsdOS` · `OpenBsdOS` · `WindowsPlatform`

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
from_raw_rgb24(frame: 'RawFrame') -> Any
open_image(path: 'Path') -> Any
resize(surface: 'Any', width: 'int', height: 'int') -> Any
rotate(surface: 'Any', degrees: 'int') -> Any
surface_nbytes(surface: 'Any') -> int
surface_size(surface: 'Any') -> tuple[int, int]
```

**You inherit (9):** `bg_fit` · `build_frame` · `draw_spectrum` · `encode_payload` · `encode_png` · `fill_rect` · `get_pixels_rgb` · `list_fonts` · `raw_argb32`

**Implementations (1):** `QtRenderer`

## Platform

`core/ports.py`

OS abstraction.  DI'd into App at startup.

**Extend `BaseOS` (`adapters/system/_base.py`)**, not this port directly — it answers 18 of these 25, leaving you 12 of its own to write (listed under [`BaseOS`](#baseos)).

**Register by naming your key in the class line:**

```python
class MyPlatform(BaseOS, key="myos"):
```

**You implement (25):**

```python
autostart() -> AutostartManager
check_permissions() -> list[str]
configure_stdout() -> None
disk_info() -> list[dict[str, str]]
disk_partitions() -> list[tuple[str, str]]
display_session() -> DisplaySession
distro_name() -> str
hotplug() -> HotplugMonitor
http_fetcher() -> HttpFetcher
install_method() -> str
memory_info() -> list[dict[str, str]]
no_devices_hint() -> str
open_transport(wire: 'Wire', vid: 'int', pid: 'int', serial: 'str | None' = None, unit: 'str' = '', hid_reports: 'bool' = False, report_id: 'int' = 0) -> Transport
package_manager() -> str
packages() -> PackageManager
paths() -> Paths
permission_denied_hint() -> str
scan_devices() -> list[DeviceInfo]
screen_capture() -> ScreenCapture
sensors() -> SensorEnumerator
setup(dry_run: 'bool' = False) -> int
software_install_hint(tool: 'str') -> str
upgrade_hint() -> str
usb_power_state(vid: 'int', pid: 'int', unit: 'str' = '') -> UsbPowerState | None
worker_thread_context() -> AbstractContextManager[None]
```

**You inherit (8):** `adopt_session` · `hard_exit` · `ram_access` · `replace_process` · `rgb_hardware` · `smbuses` · `sound_input` · `substitute`

**Implementations (8):** `BsdOS` · `FreeBsdOS` · `GenericBsd` · `LinuxOS` · `MacOSPlatform` · `NetBsdOS` · `OpenBsdOS` · `WindowsPlatform`

## ModuleServices

`core/ports.py`

The App's side of every :class:`ModuleHost`: the same calls, each told which module is asking where it matters.  ``ModuleScope`` binds the key, checks the capability, and takes back on switch-off what the module still holds.  The App implements it; a test passes a fake.

**You implement (30):**

```python
add_background_kind(module: 'str', kind: 'BackgroundKind') -> None
data_root() -> Path
encode_png(frame: 'RawFrame') -> bytes
end_source(key: 'str', keep_choice: 'bool' = False) -> None
gl_surface() -> type[GlSurface] | None
hardware_ids() -> RgbHardware
latest_readings() -> Mapping[str, float] | None
memory_access() -> RamAccess
module_settings(module: 'str') -> dict[str, Any]
open_hid(vid: 'int', pid: 'int', report_id: 'int') -> Transport
panel_keys() -> tuple[str, ...]
panel_settings(module: 'str', key: 'str') -> dict[str, Any]
panel_size(key: 'str') -> tuple[int, int] | None
panel_source(key: 'str') -> str | None
publish(event: 'Event') -> None
remove_background_kind(prefix: 'str') -> None
resume_backgrounds(module: 'str') -> None
save_module_settings(module: 'str', data: 'Mapping[str, Any]') -> None
save_panel_settings(module: 'str', key: 'str', data: 'Mapping[str, Any]') -> None
show_source(module: 'str', key: 'str', source: 'FrameSource', fps: 'float') -> None
smbuses() -> tuple[SmBus, ...]
sound_input() -> SoundInput
sound_problem() -> str
source(key: 'str') -> FrameSource | None
subscribe(event_type: 'type[_E]', handler: 'Callable[[_E], None]') -> None
substitute(cls: '_C') -> _C
unsubscribe(event_type: 'type[_E]', handler: 'Callable[[_E], None]') -> None
unwatch_frames(handler: 'Callable[[PanelPicture], None]') -> None
watch_frames(handler: 'Callable[[PanelPicture], None]') -> None
worker_thread() -> AbstractContextManager[None]
```

**Implementations (1):** `App`

## ContentStore

`core/ports.py`

Where themes, masks, backgrounds and capture configs are kept.

**You implement (32):**

```python
background_config(ref: 'str') -> dict[str, Any] | None
background_path(theme: 'Theme') -> Path | None
copy_preview(src_theme_dir: 'Path', dst_theme_dir: 'Path') -> bool
delete(directory: 'Path', name: 'str') -> Path
discover_masks(cloud_masks_dir: 'Path | None' = None, user_masks_dir: 'Path | None' = None) -> builtins.list[DiscoveredMask]
export(theme_path: 'Path', archive_path: 'Path', background: 'Path | None' = None) -> None
export_dc(theme_dir: 'Path', output_path: 'Path', elements: 'list[dict] | None' = None, flags: 'dict | None' = None) -> Path
export_unsaved(manifest: 'dict', archive_path: 'Path', background: 'Path | bytes | None', mask: 'Path | None', preview: 'bytes | None') -> None
import_(archive_path: 'Path', into_dir: 'Path') -> Theme
is_theme_dir(path: 'Path') -> bool
list(directory: 'Path') -> builtins.list[Theme]
list_web_previews(web_dir: 'Path') -> builtins.list[WebPreviewInfo]
load(path: 'Path') -> Theme
mask_path(theme: 'Theme') -> Path | None
media_player_uri(theme: 'Theme') -> str | None
module_background(theme: 'Theme') -> tuple[str, str, dict[str, Any]] | None
move_theme(theme_dir: 'Path', into: 'Path') -> Path
preview_path(theme: 'Theme') -> Path | None
resolve_ref(ref: 'str') -> Path | None
screencast_region(theme: 'Theme') -> tuple[int, int, int, int, bool] | None
single_file_theme(source: 'Path', kind: 'str') -> AbstractContextManager[SingleFileTheme]
stage(target: 'Path') -> AbstractContextManager[Path]
store_background(data: 'bytes', ext: 'str', width: 'int', height: 'int') -> str
store_background_config(prefix: 'str', payload: 'Mapping[str, Any]') -> str
store_mask(image: 'bytes', width: 'int', height: 'int', dc: 'bytes | None' = None, name: 'str | None' = None) -> str
store_media_player(uri: 'str') -> str
store_screencast(region: 'tuple[int, int, int, int, bool]') -> str
tile_path(theme_dir: 'Path') -> Path | None
user_theme_folders() -> builtins.list[Path]
video_path(theme: 'Theme') -> Path | None
write_manifest(theme_dir: 'Path', manifest: 'dict') -> Path
write_preview(theme_dir: 'Path', png: 'bytes') -> Path
```

**Implementations (1):** `FileContentStore`


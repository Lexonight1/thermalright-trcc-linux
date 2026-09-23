# Command reference

**Generated — do not edit.** `PYTHONPATH=src python3 dev/gen_commands_reference.py`

Every capability in TRCC, as the one surface all four UIs dispatch against. A new UI — a browser client, a VR panel, a TUI — needs only this page and an event subscription; it never imports a service or an adapter.

**145 total: 106 Commands and 39 Queries.** A *Query* is a read and nothing else, which is why it is named separately — a missing read should be obvious rather than archaeological.

## Dispatching one

```python
from trcc._boot import trcc
from trcc.core.commands import SendColor

app = trcc()                     # in-process, or a daemon client
result = app.dispatch(SendColor(key="0402:3922", r=255, g=0, b=0))
result.ok, result.message
```

`trcc()` returns an in-process `App`, or an `AppProxy` speaking to the daemon when `TRCC_DAEMON=1`. Both expose `dispatch(cmd) -> Result` and nothing else, so a UI written against this page works in either mode.

Over the socket the same call is one line of JSON:

```json
{"command": "SendColor", "kwargs": {"key": "0402:3922", "r": 255, "g": 0, "b": 0}}
```

Every Command is a frozen dataclass, so its fields below *are* its keyword arguments. Results are dataclasses too, and every one carries `ok` and `message`.

## Devices, display and frames

### `AddOverlayElement`

Add a user-edited element to a device's overlay layer.

*Command* → `OverlayElementResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `type` | `str` | no |
| `x` | `int` | no |
| `y` | `int` | no |
| `color` | `str` | no |
| `size` | `int` | no |
| `font` | `str` | no |
| `bold` | `bool` | no |
| `italic` | `bool` | no |
| `text` | `str` | no |
| `metric` | `str` | no |
| `format` | `str` | no |
| `show_unit` | `bool` | no |
| `source` | `str` | no |
| `element_id` | `str` | no |

### `ApplyMask`

Set a user-supplied mask image that overrides the active theme's mask.

*Command* → `MaskApplyResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `path` | `Path` | yes |

### `BuildPreview`

Render the device's active theme and stop before the wire.

*Query* → `PreviewResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `encode` | `Literal[, png, jpeg]` | no |
| `sample_cols` | `int` | no |

### `CaptureScreencastFrame`

Grab the device's screencast region once, encode it, put it on the wire.

*Command* → `ScreencastResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `ConnectDevice`

Attach + handshake with a discovered device.

*Command* → `ConnectResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `CurrentFrame`

The frame the device is showing RIGHT NOW — without rendering one.

*Query* → `PreviewResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `DeleteOverlayElement`

Remove a user-edited overlay element by id.

*Command* → `OverlayElementDeleteResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `element_id` | `str` | yes |

### `DeviceCanvas`

The panel's NATIVE pixels — the size to AUTHOR an asset for.

*Query* → `DeviceCanvasResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `DeviceConnectionIssues`

Query devices that were found but failed to connect (with per-OS hints).

*Query* → `ConnectionIssuesResult`

Takes no arguments.

### `DeviceState`

Report what a device is — identity, connection, handshake geometry.

*Query* → `DeviceStateResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `DisconnectDevice`

Close the transport and drop the device.

*Command* → `DisconnectResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `DiscoverDevices`

List attached devices that match the product registry.

*Command* → `DiscoverResult`

Takes no arguments.

### `EnableOverlay`

Toggle the metric overlay layer for a device.

*Command* → `OverlayResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `enabled` | `bool` | yes |

### `EnsureConnected`

Bring *key* up for a wire command only if it isn't already connected.

*Command* → `ConnectResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `FlashOverlayElement`

Briefly highlight one element so the user can locate it on screen.

*Command* → `OverlayElementResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `element_id` | `str` | yes |
| `duration_ms` | `int` | no |

### `LcdSnapshot`

Per-device LCD state snapshot — what settings.for_device holds.

*Query* → `LcdSnapshotResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `ListDevices`

Every attached device — the answer to "what is here right now?".

*Query* → `DevicesListResult`

Takes no arguments.

### `LoopVideo`

Toggle whether playback wraps to frame 0 or sticks at the last frame.

*Command* → `LoopVideoResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `loop` | `bool` | yes |

### `OrientedThemeTarget`

After a rotation swaps the catalog, which theme should be reloaded.

*Query* → `OrientedThemeTargetResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `active_theme` | `Path` | yes |

### `PauseVideo`

Toggle the per-device video playback pause flag.

*Command* → `PauseVideoResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `paused` | `bool` | yes |

### `PlayVideo`

Decode a video into a per-device playback override.

*Command* → `VideoResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `path` | `Path` | yes |
| `fps` | `int` | no |

### `PreviewSize`

How big a UI should draw this device's preview (#136).

*Query* → `PreviewSizeResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `RenderAndSend`

Render the device's active theme with live sensors, push to the wire.

*Command* → `RenderResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `RenderDcStandalone`

Render a DC config standalone — no active device, no theme load.

*Command* → `RenderDcResult`

| Field | Type | Required |
|---|---|---|
| `dc_path` | `Path` | yes |
| `output_path` | `Path` | yes |
| `width` | `int` | no |
| `height` | `int` | no |

### `ResetDevice`

Power-cycle one device: disconnect, reconnect, put its display back.

*Command* → `DisconnectResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `ResolveOverlay`

Ask what is on the device's screen — the read side of overlay.

*Query* → `OverlayLayoutResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `ResolveThemeDirectories`

Which browser directories this device's theme/mask panels point at.

*Query* → `ThemeDirectoriesResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `SeekVideo`

Jump the playback cursor to a specific frame.

*Command* → `SeekVideoResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `frame` | `int` | yes |

### `SendColor`

Push a solid-color frame to a connected LCD device.

*Command* → `SendResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `r` | `int` | yes |
| `g` | `int` | yes |
| `b` | `int` | yes |

### `SendFrame`

Push already-built frame bytes to the device.

*Command* → `SendResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `data` | `bytes` | yes |

### `SendImage`

Push an image file to the LCD without staging it as a theme.

*Command* → `SendResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `path` | `Path` | yes |

### `SendScreencastFrame`

Encode one already-captured frame for the device and put it on the wire.

*Command* → `ScreencastResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `frame` | `RawFrame` | yes |

### `SetBackground`

Apply a file as the device's persistent background override.

*Command* → `BackgroundResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `path` | `Path` | yes |

### `SetBackgroundMode`

Pick what fills the LCD behind overlays.

*Command* → `BackgroundModeResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `mode` | `str` | yes |

### `SetBrightness`

Set per-device display brightness (0–100).

*Command* → `BrightnessResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `percent` | `int` | yes |

### `SetFitMode`

Set how the background image/video fits the device canvas.

*Command* → `FitModeResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `mode` | `str` | yes |

### `SetMaskPosition`

Set the mask offset within the canvas, or pass None to reset to (0, 0).

*Command* → `MaskPositionResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `x` | `int | None` | yes |
| `y` | `int | None` | yes |

### `SetMaskVisible`

Toggle the mask overlay visibility for a device.

*Command* → `MaskVisibilityResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `visible` | `bool` | yes |

### `SetMediaPlayer`

Set the device's media-player source — a URI (a resource on the computer) or a URL (a resource on the web / a stream).

*Command* → `MediaPlayerResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `uri` | `str` | yes |

### `SetOrientation`

Set per-device rotation (0 / 90 / 180 / 270).

*Command* → `OrientationResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `degrees` | `int` | yes |

### `SetOverlayBackground`

Set the solid color used when background_mode='color'.

*Command* → `OverlayBackgroundResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `color` | `tuple[int, int, int]` | yes |

### `SetOverlayConfig`

Replace the user-overlay layer wholesale.

*Command* → `OverlayConfigResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `elements` | `tuple[dict, Ellipsis]` | no |

### `SetSplitMode`

Set the Dynamic Island style for widescreen panels.

*Command* → `SplitModeResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `mode` | `int` | yes |

### `SleepDevice`

Blank a connected panel so it goes dark on shutdown / quit (#143).

*Command* → `SendResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `StartScreencast`

Begin a screen-capture session for a device.

*Command* → `ScreencastResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `x` | `int` | yes |
| `y` | `int` | yes |
| `w` | `int` | yes |
| `h` | `int` | yes |
| `audio` | `bool` | no |

### `StartScreencastDriver`

Drive ``CaptureScreencastFrame`` on a cadence until stopped.

*Command* → `ScreencastResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `interval_s` | `float` | no |

### `StopScreencast`

End the screen-capture session for a device.

*Command* → `ScreencastResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `StopScreencastDriver`

Stop the cadence started by :class:`StartScreencastDriver`.

*Command* → `ScreencastResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `StopVideo`

Clear the device's playback override AND the persisted bg override.

*Command* → `VideoResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `keep_override` | `bool` | no |

### `TickDisplay`

Advance a video playback one frame, then render + send (one tick).

*Command* → `RenderResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `ToggleVideo`

Flip video playback between paused / playing — single-verb helper.

*Command* → `PauseVideoResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `UpdateOverlayElement`

Mutate fields on an existing user-edited overlay element.

*Command* → `OverlayElementResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `element_id` | `str` | yes |
| `x` | `int | None` | no |
| `y` | `int | None` | no |
| `color` | `str | None` | no |
| `size` | `int | None` | no |
| `font` | `str | None` | no |
| `bold` | `bool | None` | no |
| `italic` | `bool | None` | no |
| `text` | `str | None` | no |
| `metric` | `str | None` | no |
| `format` | `str | None` | no |
| `show_unit` | `bool | None` | no |
| `source` | `str | None` | no |

### `UploadBootAnimation`

Upload a multi-frame compressed boot animation to a SCSI LCD's flash.

*Command* → `BootAnimationResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `frame_paths` | `list[Path]` | yes |
| `delays_ds` | `list[int]` | yes |

### `VideoStatus`

Ask what a device's video playback is doing.  Read-only.

*Query* → `VideoStatusResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

## Themes, masks and media

### `DeleteTheme`

Delete the theme directory at ``path``.

*Command* → `DeleteThemeResult`

| Field | Type | Required |
|---|---|---|
| `path` | `Path` | yes |

### `DownloadCloudTheme`

Fetch a cloud background into the local cache WITHOUT applying it.

*Command* → `CloudThemeLoadResult`

| Field | Type | Required |
|---|---|---|
| `theme_id` | `str` | yes |
| `resolution` | `tuple[int, int]` | yes |

### `EnsureDataDownload`

Force-install the theme + cloud + mask archives for a resolution.

*Command* → `EnsureDataDownloadResult`

| Field | Type | Required |
|---|---|---|
| `width` | `int` | yes |
| `height` | `int` | yes |

### `ExportConfig`

Write one device's ``DeviceSettings`` to a JSON file.

*Command* → `ExportConfigResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `output_path` | `Path` | yes |

### `ExportDcTheme`

Write a theme out as a legacy-compatible ``config1.dc`` file.

*Command* → `ThemeDcExportResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `theme_name` | `str` | yes |
| `output_path` | `Path` | yes |

### `ExportOverlay`

Copy a theme's overlay config file to ``output_path``.

*Command* → `ThemeExportResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `theme_name` | `str` | yes |
| `output_path` | `Path` | yes |

### `ExportTheme`

Zip a theme under ``user_theme_dir(w, h) / theme_name`` to an archive path.

*Command* → `ThemeExportResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `theme_name` | `str` | yes |
| `archive_path` | `Path` | yes |

### `ExportVideoClip`

Encode a clip of *path* into a loose ``Theme.zt`` for *key*'s panel.

*Command* → `VideoExportResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `path` | `Path` | yes |
| `start_ms` | `int` | no |
| `end_ms` | `int | None` | no |
| `rotation` | `int` | no |
| `fit_mode` | `FitMode | None` | no |

### `ImportConfig`

Restore one device's ``DeviceSettings`` from an :class:`ExportConfig` JSON.

*Command* → `ImportConfigResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `input_path` | `Path` | yes |

### `ImportTheme`

Unpack a theme archive into ``user_theme_dir(w, h) / name``.

*Command* → `ThemeImportResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `archive_path` | `Path` | yes |
| `name` | `str` | no |

### `ListCloudThemes`

List themes available in Thermalright's hosted catalog.

*Query* → `CloudThemesListResult`

| Field | Type | Required |
|---|---|---|
| `category` | `str` | no |
| `resolution` | `tuple[int, int] | None` | no |

### `ListMasks`

Enumerate masks for a device resolution.

*Query* → `MasksListResult`

| Field | Type | Required |
|---|---|---|
| `resolution` | `tuple[int, int] | None` | no |
| `directory` | `Path | None` | no |
| `key` | `str` | no |

### `ListThemes`

Enumerate themes for a device resolution.

*Query* → `ThemesListResult`

| Field | Type | Required |
|---|---|---|
| `resolution` | `tuple[int, int] | None` | no |
| `directory` | `Path | None` | no |

### `ListWebThemes`

List the downloaded cloud-theme previews for a resolution.

*Query* → `WebThemesListResult`

| Field | Type | Required |
|---|---|---|
| `width` | `int` | yes |
| `height` | `int` | yes |
| `key` | `str` | no |

### `LoadCloudTheme`

Download a cloud video and apply it as the device's background.

*Command* → `CloudThemeLoadResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `theme_id` | `str` | yes |

### `LoadImage`

Show one image on the LCD as the background of a single-image theme.

*Command* → `ThemeResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `path` | `Path` | yes |

### `LoadTheme`

Parse a theme, persist it, render the first frame, and send it.

*Command* → `ThemeResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `path` | `Path` | yes |
| `reset_overrides` | `bool` | no |

### `LoadVideo`

Play a video on the LCD as a single-video theme.

*Command* → `ThemeResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `path` | `Path` | yes |
| `start_ms` | `int` | no |
| `end_ms` | `int | None` | no |
| `rotation` | `int` | no |

### `ProbeVideoDuration`

How long is this video file, in milliseconds?

*Query* → `VideoDurationResult`

| Field | Type | Required |
|---|---|---|
| `path` | `Path` | yes |

### `RestoreDeviceState`

Ensure *key* has a renderable display state from persisted settings.

*Command* → `ThemeResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `RestoreLastTheme`

Re-load the theme persisted in Settings for *key*.

*Command* → `ThemeResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `SaveTheme`

Save the device's CURRENT rendered state as a new theme directory.

*Command* → `ThemeResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `name` | `str` | yes |
| `overwrite` | `bool` | no |

### `UploadCustomMask`

Copy a mask image into the user-mask dir for the device's resolution.

*Command* → `MaskUploadResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `source` | `Path` | yes |

## LED

### `EnableLedTestMode`

Enable / disable the 4-color diagnostic test cycle.

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `enabled` | `bool` | yes |

### `InitializeLed`

Connect + render one initial LED frame in a single dispatch.

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `LedSnapshot`

Per-device LED state snapshot.

*Query* → `LedSnapshotResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `ListLedModes`

Enumerate the LEDMode enum names (STATIC, BREATHING, RAINBOW, …).

*Query* → `LedModesListResult`

Takes no arguments.

### `ListLedStyles`

Enumerate every LED style the PM registry can resolve.

*Query* → `LedStylesListResult`

Takes no arguments.

### `RenderLed`

Compute one LED frame from current settings + sensors and send it.

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `color` | `tuple[int, int, int] | None` | no |
| `phase` | `int` | no |
| `advance` | `bool` | no |

### `SelectZone`

Pick the active zone (UI selection state).

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `zone` | `int` | yes |

### `SetClockFormat`

12h/24h clock display for LC2-style LED segment devices.

*Command* → `ClockFormatResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `is_24h` | `bool` | yes |

### `SetHddEnabled`

Toggle HDD metrics inclusion in sensor broadcasts.

*Command* → `HddEnabledResult`

| Field | Type | Required |
|---|---|---|
| `enabled` | `bool` | yes |

### `SetLedBrightness`

Set the global LED brightness percent (0–100).

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `percent` | `int` | yes |

### `SetLedColor`

Set the LED colour.

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `color` | `tuple[int, int, int]` | yes |

### `SetLedColors`

Set LED color array + on/off + brightness on a connected Led device.

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `colors` | `list[tuple[int, int, int]]` | yes |
| `global_on` | `bool` | no |
| `brightness` | `int` | no |

### `SetLedLoadSource`

Pick the sensor source for LOAD_LINKED mode (``'cpu'`` or ``'gpu'``).

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `source` | `str` | yes |

### `SetLedMode`

Set the LED animation mode.

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `mode` | `LEDMode` | yes |

### `SetLedTempSource`

Pick the sensor source for TEMP_LINKED mode (``'cpu'`` or ``'gpu'``).

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `source` | `str` | yes |

### `SetLedZoneBrightness`

Set one zone's persistent brightness (0-100).

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `zone` | `int` | yes |
| `percent` | `int` | yes |

### `SetLedZoneColor`

Set one zone's persistent color — mirrors legacy zone-aware setters.

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `zone` | `int` | yes |
| `color` | `tuple[int, int, int]` | yes |

### `SetLedZoneMode`

Set one zone's persistent LED mode.

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `zone` | `int` | yes |
| `mode` | `LEDMode` | yes |

### `SetLedZoneSync`

Enable/disable the zone-sync carousel for a device.

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `enabled` | `bool` | yes |

### `SetLedZoneSyncInterval`

Set how many ticks between zone-sync rotations.

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `ticks` | `int` | yes |

### `SetLedZoneSyncZones`

Set which pages/zones participate in the zone-sync carousel.

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `zones` | `tuple[bool, Ellipsis]` | yes |

### `SetMemoryRatio`

Set the DDR memory multiplier (1, 2, or 4) for the LED memory gauge.

*Command* → `MemoryRatioResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `ratio` | `int` | yes |

### `SetWeekStart`

Week-start convention: ``True`` = Sunday-first, ``False`` = Monday-first.

*Command* → `WeekStartResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `sunday_first` | `bool` | yes |

### `ToggleLed`

Toggle an LED device on/off — global, or one zone if ``zone`` is given.

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `on` | `bool` | yes |
| `zone` | `int | None` | no |

### `ToggleSegment`

Flip one segment's on/off state (segment-display devices).

*Command* → `LedColorsResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `index` | `int` | yes |
| `on` | `bool` | yes |

## System, settings and diagnostics

### `AdvanceSlideshow`

Advance a device's slideshow one tick; report the theme to load next.

*Command* → `SlideshowAdvanceResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

### `CheckForUpdate`

Ask GitHub Releases whether a newer trcc-linux is available.

*Query* → `UpdateCheckResult`

Takes no arguments.

### `ConfigureSlideshow`

Set the slideshow theme list + interval for a device.

*Command* → `SlideshowResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `themes` | `tuple[str, Ellipsis] | None` | no |
| `interval_s` | `float | None` | no |

### `ControlCenterSnapshot`

App-wide settings snapshot.

*Query* → `ControlCenterSnapshotResult`

Takes no arguments.

### `DaemonStatus`

Is the background daemon running, and where is its socket?

*Query* → `DaemonResult`

Takes no arguments.

### `DisableAutostart`

Remove the OS-specific autostart entry.

*Command* → `AutostartResult`

Takes no arguments.

### `EnableAutostart`

Install the OS-specific autostart entry (per-user, no sudo).

*Command* → `AutostartResult`

| Field | Type | Required |
|---|---|---|
| `target` | `str | None` | no |

### `EnsureDaemon`

Guarantee a daemon is reachable, starting one if it is not.

*Command* → `DaemonResult`

| Field | Type | Required |
|---|---|---|
| `timeout` | `float` | no |

### `GenerateDebugReport`

Build a debug report bundle for the user to paste into a GitHub issue.

*Command* → `DebugReportPayload`

| Field | Type | Required |
|---|---|---|
| `output_path` | `Path | None` | no |
| `log_tail_lines` | `int` | no |

### `GetAutostartStatus`

Report whether auto-launch-on-login is enabled.

*Query* → `AutostartResult`

Takes no arguments.

### `GetFirstRunStatus`

Has trcc finished onboarding on this machine?

*Query* → `FirstRunStatusResult`

Takes no arguments.

### `GetPaths`

Report where this install keeps things.  Read-only.

*Query* → `PathsResult`

| Field | Type | Required |
|---|---|---|
| `resolution` | `tuple[int, int] | None` | no |
| `key` | `str` | no |

### `GetPlatformInfo`

Snapshot of OS identity + paths + permission warnings.

*Query* → `PlatformInfoResult`

Takes no arguments.

### `GetSensorDashboard`

Read the sensor-dashboard layout, auto-mapping any unbound row.

*Query* → `SensorDashboardResult`

Takes no arguments.

### `KeepAliveLoop`

Confirm or hold a device's screen keepalive.

*Command* → `KeepaliveResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `count` | `int` | no |
| `interval_s` | `float` | no |
| `metric_interval_s` | `float` | no |

### `ListDiskSensors`

Every drive thermal sensor, with its current reading and the active pick.

*Query* → `DiskSensorsResult`

Takes no arguments.

### `ListDisks`

Enumerate mounted PARTITIONS — a filesystem view, not a sensor one.

*Query* → `DisksListResult`

Takes no arguments.

### `ListFans`

Enumerate every fan the sensors aggregator exposes, with live readings.

*Query* → `FansListResult`

Takes no arguments.

### `ListFonts`

List font families the renderer can draw with.

*Query* → `FontsListResult`

Takes no arguments.

### `ListGpus`

Enumerate every GPU the sensors aggregator exposes.

*Query* → `GpusListResult`

Takes no arguments.

### `ListLanguages`

Enumerate every language code the i18n table supports.

*Query* → `LanguagesListResult`

Takes no arguments.

### `ListMemorySlots`

Enumerate DRAM slots — identity on every OS, timings on Linux.

*Query* → `MemorySlotsResult`

Takes no arguments.

### `ListSensors`

Enumerate every sensor the platform knows — descriptors only.

*Query* → `SensorsListResult`

Takes no arguments.

### `MarkFirstRunDone`

Tell next/ the onboarding flow has been completed.

*Command* → `FirstRunStatusResult`

Takes no arguments.

### `ReadSensors`

Return current sensor readings — personalized to user prefs.

*Query* → `SensorsResult`

Takes no arguments.

### `RefreshAutostart`

Re-render an existing autostart entry so it picks up a new launch path.

*Command* → `AutostartResult`

Takes no arguments.

### `RunDoctor`

Run health checks + render a CLI-friendly summary + exit code.

*Query* → `DoctorResultPayload`

Takes no arguments.

### `RunHealthCheck`

Run the full health check suite and return the structured report.

*Query* → `HealthReportResult`

Takes no arguments.

### `RunQuickstart`

Walk the new-user happy path: doctor → scan.

*Command* → `QuickstartResult`

Takes no arguments.

### `RunSetup`

OS-specific one-time setup (udev, WinUSB guide, etc.).

*Command* → `SetupResult`

| Field | Type | Required |
|---|---|---|
| `dry_run` | `bool` | no |

### `RunUpgrade`

Run the OS package-manager upgrade for trcc-linux.

*Command* → `UpgradeResult`

| Field | Type | Required |
|---|---|---|
| `dry_run` | `bool` | no |

### `SetDateFormat`

Set the LCD-overlay date pattern.

*Command* → `DateFormatResult`

| Field | Type | Required |
|---|---|---|
| `fmt` | `str` | yes |
| `key` | `str | None` | no |

### `SetDiskDevice`

Pin which drive supplies ``disk_temp`` (empty string = hottest).

*Command* → `DiskDeviceResult`

| Field | Type | Required |
|---|---|---|
| `disk_key` | `str` | yes |

### `SetGpuDevice`

Pick the primary GPU by sensor key (e.g. 'nvidia:0', 'amd:0').

*Command* → `GpuDeviceResult`

| Field | Type | Required |
|---|---|---|
| `gpu_key` | `str` | yes |

### `SetLanguage`

Set the UI language code (ISO 639-1, e.g. 'en', 'zh', 'fr').

*Command* → `LanguageResult`

| Field | Type | Required |
|---|---|---|
| `language` | `str` | yes |

### `SetRefreshInterval`

Set the global metrics-refresh interval — when metric data is polled and updated (the render reads the cached snapshot between polls).

*Command* → `RefreshIntervalResult`

| Field | Type | Required |
|---|---|---|
| `seconds` | `float` | yes |

### `SetSensorDashboard`

Replace the sensor-dashboard layout wholesale and persist it.

*Command* → `SensorDashboardResult`

| Field | Type | Required |
|---|---|---|
| `panels` | `tuple[PanelConfig, Ellipsis]` | no |

### `SetSlideshow`

Toggle the slideshow on or off without changing the theme list.

*Command* → `SlideshowResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `enabled` | `bool` | yes |

### `SetTempUnit`

Set the global temperature unit ("C" or "F") and propagate to every device.

*Command* → `TempUnitResult`

| Field | Type | Required |
|---|---|---|
| `unit` | `str` | yes |

### `SetTimeFormat`

Set the LCD-overlay clock format (12h or 24h).

*Command* → `TimeFormatResult`

| Field | Type | Required |
|---|---|---|
| `fmt` | `str` | yes |
| `key` | `str | None` | no |

### `StartSlideshowDriver`

Rotate a configured slideshow on a cadence until stopped.

*Command* → `SlideshowResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |
| `interval_s` | `float` | no |

### `StopDaemon`

Ask a running daemon to shut down.

*Command* → `DaemonResult`

| Field | Type | Required |
|---|---|---|
| `timeout` | `float` | no |

### `StopSlideshowDriver`

Stop the cadence started by :class:`StartSlideshowDriver`.

*Command* → `SlideshowResult`

| Field | Type | Required |
|---|---|---|
| `key` | `str` | yes |

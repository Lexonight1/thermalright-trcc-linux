# API reference

**Generated — do not edit.** `PYTHONPATH=src python3 dev/gen_api_reference.py`

A REST interface to the same command bus every other UI uses. Each endpoint builds a Command, dispatches it, and returns the Result as JSON — so anything here is also reachable from the CLI, the GUI, or your own client. The Commands themselves are documented in [`REFERENCE_COMMANDS.md`](REFERENCE_COMMANDS.md).

**138 endpoints.**

## Running it

```bash
trcc api                              # http://127.0.0.1:8080
trcc api --port 9000                  # another port
trcc api --token random:32            # require X-API-Token
trcc api --host 0.0.0.0 --token ...   # a public bind REQUIRES a token
```

Without `--token` on loopback the API is unauthenticated (dev mode). Binding any other interface without one is refused rather than allowed — an open device-control API on a LAN is not a default worth having. `--pair` prints a one-time 6-character code that a remote device exchanges for the token via `POST /pair`.

Devices are addressed by **key** — the `vid:pid` string, e.g. `0402:3922` — the same identifier the CLI and the wire use. Every response carries `ok` and `message`.

Interactive docs are served at `/docs` while the API is running.

## Devices

| Endpoint | Returns | Description |
|---|---|---|
| `GET /devices` | `DiscoverResponse` | — |
| `GET /devices/issues` | `ConnectionIssuesView` | Every device that failed to connect, and why. |
| `GET /devices/{key}` | `ProductSchema` | Detail for one discovered device — 404 if not currently present. |
| `GET /devices/{key}/canvas` | `DeviceCanvasResult` | The panel's NATIVE pixels — the size to AUTHOR an asset for. |
| `POST /devices/{key}/connect` | `ConnectView` | Connect *key*.  Returns the full handshake — including the raw device response as hex, the field issue triage always asks for. |
| `POST /devices/{key}/disconnect` | `DisconnectResult` | — |
| `POST /devices/{key}/reset` | `DisconnectResult` | Power-cycle the device: disconnect, reconnect, restore its display. |

## Display, themes and frames

| Endpoint | Returns | Description |
|---|---|---|
| `POST /devices/{key}/display/background` | `BackgroundResult` | Set a FILE as the device's persistent background override. |
| `POST /devices/{key}/display/background-mode` | `BackgroundModeResult` | Pick what fills the LCD behind overlays (theme/color/transparent). |
| `POST /devices/{key}/display/boot-animation` | `BootAnimationResult` | Upload a multi-frame compressed boot animation to a SCSI LCD's flash. |
| `POST /devices/{key}/display/brightness` | `BrightnessResult` | — |
| `POST /devices/{key}/display/color` | `SendResult` | Push a solid-color frame to a connected LCD device. |
| `POST /devices/{key}/display/create-theme` | `CreateThemeResponse` | Create + apply a custom theme from uploaded multipart files. |
| `POST /devices/{key}/display/export-video` | `VideoExportResult` | Encode a clip into a loose ``Theme.zt`` sized for the device's panel. |
| `POST /devices/{key}/display/fit-mode` | `FitModeResult` | — |
| `POST /devices/{key}/display/keepalive` | `KeepaliveResult` | Run a keepalive burst (resend the last frame N times). |
| `POST /devices/{key}/display/load-video` | `ThemeResult` | Stage a video as a one-file theme and apply it to the device. |
| `POST /devices/{key}/display/loop-video` | `LoopVideoResult` | Toggle whether playback wraps or sticks at the last frame. |
| `POST /devices/{key}/display/mask` | `MaskApplyResult` | Apply a user-supplied mask. |
| `POST /devices/{key}/display/mask-position` | `MaskPositionResult` | — |
| `POST /devices/{key}/display/mask-visible` | `MaskVisibilityResult` | — |
| `POST /devices/{key}/display/media-player` | `MediaPlayerResult` | Set the media-player source for *key* — a local file or a web URL/stream. |
| `POST /devices/{key}/display/orientation` | `OrientationResult` | — |
| `POST /devices/{key}/display/overlay` | `OverlayResult` | — |
| `POST /devices/{key}/display/overlay-background` | `OverlayBackgroundResult` | Set the solid background color used when background-mode=color. |
| `GET /devices/{key}/display/overlay-elements` | `OverlayLayoutResult` | What is currently drawn on the device -- the read half of overlay. |
| `POST /devices/{key}/display/overlay-elements` | `OverlayElementResult` | Add a user-edited overlay element. |
| `PUT /devices/{key}/display/overlay-elements` | `OverlayConfigResult` | Bulk replace the user-overlay element list. |
| `DELETE /devices/{key}/display/overlay-elements/{element_id}` | `OverlayElementDeleteResult` | Remove an overlay element by id. |
| `PATCH /devices/{key}/display/overlay-elements/{element_id}` | `OverlayElementResult` | Mutate fields on an existing user-edited overlay element. |
| `POST /devices/{key}/display/overlay-elements/{element_id}/flash` | `OverlayElementResult` | Briefly highlight an overlay element in the GUI. |
| `POST /devices/{key}/display/pause-video` | `PauseVideoResult` | Pause / resume video playback. |
| `POST /devices/{key}/display/play-video` | `VideoResult` | Start a video playback override on the device. |
| `GET /devices/{key}/display/preview` | — | Return the device's current rendered frame as a PNG image. |
| `WS /devices/{key}/display/preview/stream` | — | Stream JPEG-encoded preview frames over a WebSocket at ~5 fps. |
| `POST /devices/{key}/display/push-image` | `SendResult` | Push a server-side image to the panel ONCE — nothing staged or persisted. |
| `POST /devices/{key}/display/render-dc` | `RenderDcResult` | Render a legacy DC config to an image with no device and no theme load. |
| `POST /devices/{key}/display/reset` | `SendResult` | Reset the display — stop any active video, then send a solid red frame. |
| `POST /devices/{key}/display/restore-theme` | `ThemeResponse` | Restore the device's display state (persisted theme + background). |
| `POST /devices/{key}/display/screencast/start` | `ScreencastResult` | Begin a screen-capture session for *key*. |
| `POST /devices/{key}/display/screencast/stop` | `ScreencastResult` | End the screen-capture session for *key*. |
| `POST /devices/{key}/display/seek-video` | `SeekVideoResult` | Jump to a specific frame. |
| `POST /devices/{key}/display/send-image` | `ThemeResponse` | One-shot image-to-LCD via multipart upload. |
| `POST /devices/{key}/display/sleep` | `SendResult` | Blank the panel so it goes dark — the shutdown / turn-off action. |
| `POST /devices/{key}/display/slideshow` | `SlideshowResult` | Turn the device's slideshow on / off. |
| `PUT /devices/{key}/display/slideshow` | `SlideshowResult` | Set the theme list + interval for a device's slideshow. |
| `POST /devices/{key}/display/slideshow/drive` | `SlideshowResult` | Start or stop actually ROTATING the configured slideshow. |
| `GET /devices/{key}/display/snapshot` | `LcdSnapshotResult` | Return the persisted LCD state for one device. |
| `POST /devices/{key}/display/split-mode` | `SplitModeResult` | — |
| `POST /devices/{key}/display/stop-video` | `VideoResult` | Clear the video playback override on the device. |
| `POST /devices/{key}/display/theme` | `ThemeResponse` | — |
| `POST /devices/{key}/display/tick` | `RenderResult` | Render the active theme with live sensors + send one frame. |
| `POST /devices/{key}/display/upload-mask` | `MaskUploadResult` | Upload a mask file (server-side path) + apply it. |
| `GET /devices/{key}/display/video-status` | `VideoStatusResponse` | Current playback state for the device's video background override. |
| `GET /display/masks` | `MasksListResult` | List masks for a device resolution. |
| `GET /display/video-duration` | `VideoDurationResult` | How long a video file is, in milliseconds (server-side path). |

## Theme library

| Endpoint | Returns | Description |
|---|---|---|
| `DELETE /theme` | `DeleteThemeResult` | Delete a theme directory at an absolute path. |
| `GET /theme/cloud` | `CloudThemesListResult` | List Thermalright cloud catalog (offline — catalog is static). |
| `POST /theme/cloud/download` | `CloudThemeLoadResult` | Cache a cloud theme locally WITHOUT applying it to a device. |
| `POST /theme/cloud/{key}` | `CloudThemeLoadResult` | Download a cloud theme + apply it to *key*. |
| `POST /theme/config/import-upload` | `ImportConfigResponse` | Restore a device's settings from an uploaded JSON snapshot. |
| `POST /theme/export` | `ThemeExportResult` | — |
| `POST /theme/export-overlay` | `ThemeExportResult` | Copy a theme's overlay config file out to *output_path*. |
| `POST /theme/import` | `ThemeImportResult` | Import a theme archive from a server-side path. |
| `POST /theme/import-upload` | `ThemeImportResult` | Import a theme archive uploaded via multipart form-data. |
| `POST /theme/init` | `EnsureDataDownloadResult` | Prefetch theme/web/mask archives for a resolution (idempotent). |
| `GET /theme/list` | `ThemesListResult` | List themes for a device resolution. |
| `POST /theme/save` | `ThemeResponse` | Save the device's active theme, refusing a name collision at 409. |
| `GET /theme/web` | `list` | Cloud-theme preview gallery for a resolution (e.g. ``320x320``). |
| `GET /theme/{key}/config-download` | — | Stream a device's settings snapshot as a JSON download. |
| `GET /theme/{key}/{theme_name}/download` | — | Stream a theme archive as a multipart download. |
| `POST /theme/{name}/export-dc` | `ThemeDcExportResult` | Write a theme out as legacy ``config1.dc``. |

## LED

| Endpoint | Returns | Description |
|---|---|---|
| `POST /devices/{key}/led/brightness` | `LedColorsResult` | — |
| `POST /devices/{key}/led/clock-format` | `ClockFormatResult` | Set the 12h/24h clock display. |
| `POST /devices/{key}/led/color` | `LedColorsResult` | — |
| `POST /devices/{key}/led/colors` | `LedColorsResult` | — |
| `POST /devices/{key}/led/load-source` | `LedColorsResult` | — |
| `POST /devices/{key}/led/memory-ratio` | `MemoryRatioResult` | Set the DDR memory multiplier (1, 2, or 4). |
| `POST /devices/{key}/led/mode` | `LedColorsResult` | — |
| `POST /devices/{key}/led/render` | `LedColorsResult` | One tick — engine reads Settings, advances counters, sends a frame. |
| `POST /devices/{key}/led/select-zone` | `LedColorsResult` | Pick the currently-active zone. |
| `GET /devices/{key}/led/snapshot` | `LedSnapshotResult` | Return the persisted LED state for one device. |
| `POST /devices/{key}/led/temp-source` | `LedColorsResult` | — |
| `POST /devices/{key}/led/test-mode` | `LedColorsResult` | — |
| `POST /devices/{key}/led/toggle` | `LedColorsResult` | Turn the LED device (or one zone) on/off. |
| `POST /devices/{key}/led/toggle-segment` | `LedColorsResult` | Flip one segment on/off. |
| `POST /devices/{key}/led/week-start` | `WeekStartResult` | Pick the week-start day (Sunday-first vs Monday-first). |
| `POST /devices/{key}/led/zone-brightness` | `LedColorsResult` | Set one zone's persistent brightness (0-100). |
| `POST /devices/{key}/led/zone-color` | `LedColorsResult` | Set one zone's persistent color. |
| `POST /devices/{key}/led/zone-mode` | `LedColorsResult` | Set one zone's persistent LED mode. |
| `POST /devices/{key}/led/zone-sync` | `LedColorsResult` | Enable/disable the zone-sync carousel (optionally set interval). |
| `POST /devices/{key}/led/zone-sync-zones` | `LedColorsResult` | Choose WHICH zones take part in the zone-sync carousel. |
| `GET /led/modes` | `LedModesListResult` | Enumerate animation modes. |
| `GET /led/styles` | `LedStylesListResult` | Enumerate every LED style in the PM byte registry. |

## System and diagnostics

| Endpoint | Returns | Description |
|---|---|---|
| `GET /system/autostart` | `AutostartResult` | Snapshot the autostart entry — whether it's installed + its path. |
| `POST /system/autostart` | `AutostartResult` | Toggle the OS autostart entry (per-user, no sudo). |
| `POST /system/autostart/refresh` | `AutostartResult` | Re-render an existing autostart entry so it picks up a new launch path. |
| `GET /system/check-update` | `UpdateCheckResult` | Ask GitHub whether a newer version of trcc-linux is published. |
| `GET /system/dashboard` | `SensorDashboardResult` | The sensor-dashboard layout — the grid the GUI's System Info screen edits. |
| `POST /system/dashboard` | `SensorDashboardResult` | Replace the sensor-dashboard layout wholesale. |
| `POST /system/debug-report` | `DebugReportPayload` | Generate a debug report bundle. |
| `GET /system/disk-sensors` | `DiskSensorsResult` | Drive thermal sensors — the list ``disk_temp`` comes from. |
| `POST /system/disk-sensors/active` | `DiskDeviceResult` | Pin which drive supplies ``disk_temp``.  Empty key = hottest. |
| `GET /system/disks` | `DisksListResult` | List mounted partitions — NOT the drive list `disk_temp` comes from. |
| `GET /system/doctor` | `DoctorResultPayload` | Same as `/health` but adds an exit code + a rendered text view. |
| `GET /system/fans` | `FansListResult` | List fans the sensors aggregator exposes, with live readings. |
| `GET /system/first-run-status` | `FirstRunStatusResult` | Has trcc finished onboarding on this machine? |
| `GET /system/fonts` | `FontsListResult` | List font families Qt can see. |
| `GET /system/gpus` | `GpusListResult` | List GPUs exposed by the sensors aggregator. |
| `POST /system/hdd-enabled` | `HddEnabledResult` | Toggle inclusion of HDD metrics in sensor broadcasts. |
| `GET /system/health` | `HealthReportResult` | Run the health check suite + return structured results. |
| `GET /system/info` | `dict` | — |
| `GET /system/language` | `LanguageResult` | Return the currently active UI language (ISO 639-1 code). |
| `GET /system/languages` | `LanguagesListResult` | Enumerate UI languages the i18n table supports. |
| `POST /system/mark-setup-done` | `FirstRunStatusResult` | Mark the first-run flow as completed. |
| `GET /system/memory-slots` | `MemorySlotsResult` | DRAM slots — identity everywhere, timings on Linux (empty = not probed). |
| `GET /system/metrics` | `dict` | Raw flat metric map: ``sensor_id`` → current (personalized) value. |
| `POST /system/quickstart` | `QuickstartResult` | Walk the new-user happy path — doctor, then scan — as one sequence. |
| `GET /system/sensors` | `SensorsResult` | — |
| `GET /system/sensors/catalog` | `SensorsListResult` | Every sensor this machine can measure — identities, no values. |
| `GET /system/sensors/{category}` | `SensorsResult` | Filter the live sensor list by category prefix. |
| `POST /system/setup` | `SetupResult` | — |
| `GET /system/snapshot` | `ControlCenterSnapshotResult` | Return the AppSettings snapshot. |
| `GET /system/status` | `AppStatusResponse` | Unified snapshot: app-level prefs + per-device attach list. |
| `POST /system/upgrade` | `UpgradeResult` | Upgrade trcc-linux via the detected package manager. |

## Preferences

| Endpoint | Returns | Description |
|---|---|---|
| `POST /config/date-format` | `DateFormatResult` | — |
| `POST /config/gpu` | `GpuDeviceResult` | — |
| `POST /config/language` | `LanguageResult` | — |
| `POST /config/refresh-interval` | `RefreshIntervalResult` | — |
| `POST /config/temp-unit` | `TempUnitResult` | — |
| `POST /config/time-format` | `TimeFormatResult` | — |

## Daemon control

| Endpoint | Returns | Description |
|---|---|---|
| `POST /trcc/ensure` | `DaemonKillResponse` | Start the TRCC daemon if it is not already running. |
| `POST /trcc/kill` | `DaemonKillResponse` | Stop the running TRCC daemon. |
| `GET /trcc/status` | `DaemonStatusResponse` | Snapshot of the running daemon: pid, uptime, device counts. |

## Meta

| Endpoint | Returns | Description |
|---|---|---|
| `GET /` | `dict` | — |
| `GET /health` | `dict` | Liveness probe — always reachable, no auth required. |
| `POST /pair` | — | Exchange the terminal pairing code for the persistent API token. |

## (Untagged)

| Endpoint | Returns | Description |
|---|---|---|
| `WS /events` | — | Push bus events to the client as JSON, one object per message. |

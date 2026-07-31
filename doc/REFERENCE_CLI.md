# CLI Reference

<!-- GENERATED FILE -- do not edit by hand.
     Source: the Typer command tree in src/trcc/ui/cli/.
     Regenerate: PYTHONPATH=src python3 dev/gen_cli_reference.py
     A command's text here IS its docstring; edit that. -->

TRCC — Thermalright LCD/LED cooler control (clean-slate build).

Commands that act on a device take its **`KEY`** — the USB `VID:PID` shown by `trcc detect`, e.g. `0402:3922` — as the first argument.

```bash
trcc [OPTIONS] COMMAND [ARGS]...
```

## Global options

| Option | Description |
|---|---|
| `--verbose`, `-v` `VERBOSE` | Terminal log verbosity: -v shows INFO, -vv shows DEBUG. Without it the terminal stays quiet (warnings + errors only); the rotating log file always keeps the detail. |
| `--version`, `-V` | Print the installed TRCC version and exit. |

## Contents

- [`trcc api`](#trcc-api)
- [`trcc daemon`](#trcc-daemon)
- [`trcc daemon-status`](#trcc-daemon-status)
- [`trcc detect`](#trcc-detect)
- [`trcc doctor`](#trcc-doctor)
- [`trcc gui`](#trcc-gui)
- [`trcc kill`](#trcc-kill)
- [`trcc qtgui`](#trcc-qtgui)
- [`trcc quickstart`](#trcc-quickstart)
- [`trcc report`](#trcc-report)
- [`trcc sensors`](#trcc-sensors)
- [`trcc serve`](#trcc-serve)
- [`trcc setup`](#trcc-setup)
- [`trcc shell`](#trcc-shell)
- [`trcc status`](#trcc-status)
- [`trcc version`](#trcc-version)
- [`trcc config`](#trcc-config) — command group
- [`trcc device`](#trcc-device) — command group
- [`trcc display`](#trcc-display) — command group
- [`trcc led`](#trcc-led) — command group
- [`trcc system`](#trcc-system) — command group
- [`trcc theme`](#trcc-theme) — command group

## Commands

### `trcc api`

Launch the REST API (FastAPI + uvicorn). Three operating modes: * `trcc api` — loopback only (127.0.0.1), no auth. Dev default. * `trcc api --host 0.0.0.0 --token <secret>` — public bind, token required on every request. Use a long random secret. * `trcc api --host 0.0.0.0 --token <secret> --pair` — same as above plus the pairing endpoint; a 6-char code is shown so a remote app can fetch the token without out-of-band copy/paste. Refusal: `--host` other than `127.0.0.1` / `localhost` without `--token` exits 2 — would otherwise expose every endpoint to LAN.

```bash
trcc api [OPTIONS]
```

| Option | Description |
|---|---|
| `--host`, `-H` `HOST` | Bind address |
| `--port`, `-p` `PORT` | Bind port |
| `--token`, `-t` `TOKEN` | Persistent API token. When set, every request must carry `X-API-Token: <token>`. When omitted with --host 127.0.0.1, the API is unauth'd (loopback dev mode). Omitting it with any other --host is REJECTED — refusing to bind a public interface without auth. Use --token random:<n> to generate. |
| `--pair` | Show a one-time 6-char pairing code in the terminal. Remote devices POST it to /pair to exchange for the API token. Requires --token. |

### `trcc daemon`

Run the background daemon that owns USB + serves CLI/API clients. One process per user. Binds a Unix socket at `$XDG_RUNTIME_DIR/trcc.sock` and serves Commands until SIGTERM / SIGINT or a remote `trcc kill`. Sets `TRCC_DAEMON=1` to route clients through this daemon.

```bash
trcc daemon
```

### `trcc daemon-status`

Report whether the background daemon socket is reachable. Replaces the previous top-level `status` command, which conflated daemon reachability with app state. Use `trcc status` for the unified app + device snapshot.

```bash
trcc daemon-status
```

### `trcc detect`

Alias for `trcc device list` — list attached devices.

```bash
trcc detect
```

### `trcc doctor`

Alias for `trcc system doctor` — health checks.

```bash
trcc doctor
```

### `trcc gui`

Launch the legacy Windows-style GUI (port in progress). Today's shell hosts the device sidebar + a diagnostic content area — enough to prove the legacy-on-next/-bus pattern end to end on real hardware. Real feature panels (LCD handler, theme settings, mask, video, LED) land in subsequent passes. `--resume` starts hidden in the tray (XDG autostart-on-login); bare `trcc gui` shows the window.

```bash
trcc gui [OPTIONS]
```

| Option | Description |
|---|---|
| `--resume`, `--tray`, `--minimized` | Start hidden in the system tray instead of showing the window — used by XDG autostart on login. The last-used theme is restored automatically. |

### `trcc kill`

Ask the running daemon to shut down, return when its socket is gone.

```bash
trcc kill
```

### `trcc qtgui`

Launch the Qt-native GUI (clean-slate, layout-driven). This is the rebuild's GUI — built up over G1–G5 and used during development. See `gui` for the legacy Windows-style port.

```bash
trcc qtgui
```

### `trcc quickstart`

Guided first-session flow for new users. Runs the doctor, scans for devices, and walks you through what to do next. Pass `--yes` to also test-connect to the first device found. Safe to re-run any time.

```bash
trcc quickstart [OPTIONS]
```

| Option | Description |
|---|---|
| `--yes`, `-y` | If a device is found, also connect + push a green test frame. Default: stop after scan so you can inspect what's there. |

### `trcc report`

Alias for `trcc system debug-report` — full diagnostic dump.

```bash
trcc report [OPTIONS]
```

| Option | Description |
|---|---|
| `--output`, `-o` `OUTPUT` | Write the report to this path instead of stdout. |
| `--log-lines` `LOG_LINES` | How many trailing log lines to include. |

### `trcc sensors`

Alias for `trcc system sensors` — print sensor readings.

```bash
trcc sensors
```

### `trcc serve`

Alias for `trcc api` — launches the REST API + uvicorn. The `serve` name matches legacy CLI ergonomics; `api` still works for backwards-compat with existing scripts.

```bash
trcc serve [OPTIONS]
```

| Option | Description |
|---|---|
| `--host`, `-H` `HOST` | Bind address |
| `--port`, `-p` `PORT` | Bind port |
| `--token`, `-t` `TOKEN` | Same semantics as `trcc api --token` — see `trcc api --help`. |
| `--pair` | Same semantics as `trcc api --pair` — see `trcc api --help`. |

### `trcc setup`

Alias for `trcc system setup` — OS-specific setup (udev rules on Linux, WinUSB guide on Windows). New users reach for the short form. (#194)

```bash
trcc setup [OPTIONS]
```

| Option | Description |
|---|---|
| `--yes`, `-y` | Non-interactive (assume yes to prompts) |

### `trcc shell`

Open an interactive prompt sharing one App across commands. Each line is parsed as if it were a fresh `trcc` invocation, but the App is built once and reused — no per-command handshake. In daemon mode the App is an AppProxy that round-trips each line to the running daemon. Ctrl-D or `exit` quits.

```bash
trcc shell
```

### `trcc status`

Show unified app + LCD + LED state. Composes `ControlCenterSnapshot` (app prefs) with per-device `LcdSnapshot` / `LedSnapshot` — one round-trip for "what state is everything in right now?". Pass `--json` for scripts. Use `trcc daemon-status` for daemon reachability checks.

```bash
trcc status [OPTIONS]
```

| Option | Description |
|---|---|
| `--json` | Emit JSON instead of human text. |

### `trcc version`

Print the installed TRCC version.

```bash
trcc version
```

## `trcc config`

App-global preferences: temp unit, language, GPU, refresh interval.

### `trcc config date-format`

Set the global LCD overlay date format.

```bash
trcc config date-format FMT
```

| Argument | Description |
|---|---|
| `FMT` | LCD date format, e.g. 'yyyy/MM/dd', 'dd.MM.yyyy', 'MM/dd/yyyy' |

### `trcc config gpu`

Pick the primary GPU for sensor overlays. Empty string = auto.

```bash
trcc config gpu [KEY]
```

| Argument | Description |
|---|---|
| `KEY` | GPU sensor key (e.g. 'nvidia:0') or '' to clear *(optional)* |

### `trcc config language`

Set the UI language.

```bash
trcc config language LANG
```

| Argument | Description |
|---|---|
| `LANG` | ISO 639-1 code, e.g. 'en', 'zh', 'fr' |

### `trcc config refresh-interval`

Set the global metrics-refresh / render-and-send tick interval.

```bash
trcc config refresh-interval SECONDS
```

| Argument | Description |
|---|---|
| `SECONDS` | Seconds between metric refreshes (1 to 100) |

### `trcc config temp-unit`

Set the global temperature unit (propagates to every device).

```bash
trcc config temp-unit UNIT
```

| Argument | Description |
|---|---|
| `UNIT` | Either 'C' or 'F' |

### `trcc config time-format`

Set the global LCD overlay clock format.

```bash
trcc config time-format FMT
```

| Argument | Description |
|---|---|
| `FMT` | LCD clock format: '12h' or '24h' |

## `trcc device`

Discover and connect to TRCC devices.

### `trcc device connect`

Open USB transport and perform the wire-protocol handshake.

```bash
trcc device connect KEY
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |

### `trcc device disconnect`

Close the transport and drop the device.

```bash
trcc device disconnect KEY
```

| Argument | Description |
|---|---|
| `KEY` | -- |

### `trcc device issues`

Show devices that failed to connect, and why. A connect can fail before anything is watching the bus, so the failure is pulled with a query rather than only published — the same one the GUIs and `GET /devices/issues` use.

```bash
trcc device issues
```

### `trcc device list`

List devices currently attached to the host.

```bash
trcc device list
```

### `trcc device reset`

Disconnect + clear cached state for a device. Use this when the LCD seems stuck — drops any cached frame, theme, and runtime counters. Re-running `connect` after this starts completely fresh.

```bash
trcc device reset KEY
```

| Argument | Description |
|---|---|
| `KEY` | -- |

### `trcc device select`

Persist the active-device selection by ordinal. Multi-device hosts (e.g. two LCDs + one LED controller) need a way to point CLI commands at "the one I'm steering today". Resolves the ordinal against `device list` and stores the resulting key in `AppSettings.active_device`.

```bash
trcc device select ORDINAL
```

| Argument | Description |
|---|---|
| `ORDINAL` | 1-based ordinal of the attached device to mark active (matches `device list` output) |

## `trcc display`

Configure device display (theme / orientation / brightness).

### `trcc display apply-mask`

Override the active theme's mask with a user-supplied image.

```bash
trcc display apply-mask KEY PATH
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `PATH` | Image file path (png/jpg/jpeg/bmp/webp) |

### `trcc display background-mode`

Pick what fills the LCD behind overlays.

```bash
trcc display background-mode KEY MODE
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `MODE` | 'theme' / 'color' / 'transparent' |

### `trcc display boot-anim`

Upload a multi-frame compressed boot animation to a SCSI LCD's flash. The animation plays from device flash on every boot until overwritten. Only SCSI panels with 240×240 / 240×320 / 320×240 / 320×320 resolution support boot animations. Frame files are picked up in alphabetical order from *frames_dir* — PNG / JPG / JPEG / BMP / WebP. Each frame uses the same dwell time via --delay (per-frame delays via the API only).

```bash
trcc display boot-anim [OPTIONS] KEY FRAMES_DIR
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 (SCSI only) |
| `FRAMES_DIR` | Directory of image frames (sorted alphabetically; 1–248 frames) |

| Option | Description |
|---|---|
| `--delay`, `-d` `DELAY_DS` | Dwell time per frame in deciseconds (10 = 1.0 s, max 25 = 2.5 s) |

### `trcc display color`

Display a single solid color on the LCD. Smallest path that exercises the full wire chain (handshake-derived profile + DisplayService encoder + Device.send). Useful diagnostic for confirming a device class works end-to-end on real hardware.

```bash
trcc display color KEY HEX_COLOR
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `HEX_COLOR` | Hex color (e.g. ff0000 for red) |

### `trcc display configure-slideshow`

Set the slideshow theme list + interval.

```bash
trcc display configure-slideshow [OPTIONS] KEY THEMES
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `THEMES` | Theme names (directories under user_content_dir) — order matters |

| Option | Description |
|---|---|
| `--interval`, `-i` `INTERVAL` | Seconds between theme swaps (default 60). |

### `trcc display keepalive`

Periodically resend the device's last frame. Workaround for Bulk/LY firmware that drops the displayed image when the internal buffer ages out. Render at least once before starting the loop so there's a cached frame to resend. `count=0` (default) runs open-ended and exits cleanly on Ctrl-C — the Command itself owns the loop + signal handling so the CLI doesn't need a user-space `while` wrapper.

```bash
trcc display keepalive [OPTIONS] KEY
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |

| Option | Description |
|---|---|
| `--interval`, `-i` `INTERVAL` | Seconds between resends. Bulk/LY firmware reverts to the built-in logo after ~2-3 s without a frame; default 0.150 s keeps the screen pinned. |
| `--count`, `-c` `COUNT` | Number of resends; 0 means loop forever (until Ctrl-C). |
| `--metric-interval` `METRIC_INTERVAL` | Seconds between overlay re-renders (live sensor refresh). 0 disables — last frame's metrics stay frozen on screen. |

### `trcc display list-masks`

List mask images for the device's resolution. By default, scans both the cloud-downloaded mask dir (`data/web/zt{W}{H}`) and the user-created mask dir (`user_content_dir/data/web/zt{W}{H}`).

```bash
trcc display list-masks [OPTIONS] [KEY]
```

| Argument | Description |
|---|---|
| `KEY` | Device key (e.g. 0402:3922) — its resolution scopes the scan. Required unless --dir is given. *(optional)* |

| Option | Description |
|---|---|
| `--dir`, `-d` `DIRECTORY` | Override: scan an explicit directory instead of the device's mask dirs |

### `trcc display load-image`

Show a single image on the LCD. Stages the image as a one-file theme so the existing render pipeline handles fit + brightness + rotation. Re-runnable: subsequent loads of the same image are cheap (no re-copy).

```bash
trcc display load-image KEY PATH
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `PATH` | Image file (PNG / JPG / JPEG / BMP / WEBP) |

### `trcc display load-theme`

Load a theme: parse, persist, render+send if device is connected.

```bash
trcc display load-theme KEY PATH
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `PATH` | Theme directory |

### `trcc display load-video`

Play a video on the LCD as a single-video theme. Transcodes the source to a `Theme.zt` matching the device's native resolution (.zt inputs are copied as-is), stages a one-file theme, then dispatches LoadTheme. Device must be attached so we know the target resolution.

```bash
trcc display load-video [OPTIONS] KEY PATH
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `PATH` | Video file (MP4 / MOV / WEBM / MKV / AVI / ZT) |

| Option | Description |
|---|---|
| `--start`, `-s` `START_MS` | Clip start in milliseconds (default: 0). |
| `--end`, `-e` `END_MS` | Clip end in milliseconds (default: probe duration, fallback 10s). |
| `--rotation`, `-r` `ROTATION` | Rotation in degrees: 0 / 90 / 180 / 270. |

### `trcc display loop-video`

Toggle whether video wraps at the end or sticks at the last frame.

```bash
trcc display loop-video KEY STATE
```

| Argument | Description |
|---|---|
| `KEY` | Device key |
| `STATE` | 'on' (loop) or 'off' (single-pass) |

### `trcc display mask-position`

Position the mask overlay within the canvas.

```bash
trcc display mask-position KEY X Y
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `X` | X offset in pixels (≥ 0) |
| `Y` | Y offset in pixels (≥ 0) |

### `trcc display mask-visible`

Toggle mask visibility.

```bash
trcc display mask-visible KEY STATE
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `STATE` | 'on' or 'off' |

### `trcc display media-player`

Set the media-player source — a local file or a web URL/stream. Wraps `SetMediaPlayer`: a local file plays through the video pipeline; a web URL is referenced (persisted so `theme save` captures it). An empty URI clears the source.

```bash
trcc display media-player KEY [URI]
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `URI` | Source: a local file path, or a web URL/stream. '' clears. *(optional)* |

### `trcc display overlay`

Toggle the metric overlay layer.

```bash
trcc display overlay KEY STATE
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `STATE` | 'on' or 'off' |

### `trcc display overlay-add`

Add a user-edited overlay element to a device.

```bash
trcc display overlay-add [OPTIONS] KEY TYPE_
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `TYPE_` | 'text' / 'metric' / 'clock' |

| Option | Description |
|---|---|
| `--x` `X` | X position |
| `--y` `Y` | Y position |
| `--text` `TEXT` | Text content (type=text) |
| `--metric` `METRIC` | Metric id (type=metric) |
| `--format` `FMT` | Metric format string |
| `--source` `SOURCE` | Clock source: time / weekday / date |
| `--color` `COLOR` | -- |
| `--size` `SIZE` | -- |
| `--bold` | -- |
| `--italic` | -- |
| `--show-unit` | Draw the metric's unit (°C/%/MHz/RPM) after the number, or the bare number when the unit is baked into the theme art |
| `--id` `ELEMENT_ID` | Explicit element id (default: auto-generated UUID) |

### `trcc display overlay-background`

Set the solid color used when background-mode=color.

```bash
trcc display overlay-background KEY HEX_COLOR
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `HEX_COLOR` | Hex color (e.g. 000000 for black) |

### `trcc display overlay-delete`

Remove a user-edited overlay element by id.

```bash
trcc display overlay-delete KEY ELEMENT_ID
```

| Argument | Description |
|---|---|
| `KEY` | -- |
| `ELEMENT_ID` | ID returned by overlay-add |

### `trcc display overlay-flash`

Briefly highlight an overlay element in the GUI.

```bash
trcc display overlay-flash [OPTIONS] KEY ELEMENT_ID
```

| Argument | Description |
|---|---|
| `KEY` | -- |
| `ELEMENT_ID` | -- |

| Option | Description |
|---|---|
| `--duration`, `-d` `DURATION_MS` | Flash duration in milliseconds |

### `trcc display overlay-render`

Render a DC config to a PNG preview — no active device required. Mirrors legacy `trcc overlay` — composites every element from `config1.dc` onto a solid-black canvas at *width × height* and writes the result as PNG. Useful when iterating on a theme's metric positions without unplugging the device or sending frames.

```bash
trcc display overlay-render [OPTIONS] DC_PATH
```

| Argument | Description |
|---|---|
| `DC_PATH` | DC file or theme directory containing config1.dc |

| Option | Description |
|---|---|
| `--output`, `-o` `OUTPUT` | Output PNG path for the rendered preview. |
| `--width`, `-w` `WIDTH` | Render canvas width (px) |
| `--height`, `-h` `HEIGHT` | Render canvas height (px) |

### `trcc display overlay-update`

Mutate fields on an existing user-edited overlay element.

```bash
trcc display overlay-update [OPTIONS] KEY ELEMENT_ID
```

| Argument | Description |
|---|---|
| `KEY` | -- |
| `ELEMENT_ID` | ID returned by overlay-add |

| Option | Description |
|---|---|
| `--x` `X` | -- |
| `--y` `Y` | -- |
| `--color` `COLOR` | -- |
| `--size` `SIZE` | -- |
| `--text` `TEXT` | -- |
| `--metric` `METRIC` | -- |
| `--format` `FMT` | -- |
| `--source` `SOURCE` | -- |
| `--bold` | -- |
| `--italic` | -- |
| `--show-unit` | Draw the metric's unit (°C/%/MHz/RPM) after the number, or the bare number when the unit is baked into the theme art |

### `trcc display pause-video`

Pause or resume video playback.

```bash
trcc display pause-video KEY STATE
```

| Argument | Description |
|---|---|
| `KEY` | Device key |
| `STATE` | 'on' (pause) or 'off' (resume) |

### `trcc display play`

Run the render-and-send ticker until Ctrl-C. Dispatches TickDisplay every tick with live sensors — that Command advances an active video playback and renders, so the loop needs no handle on MediaService. Keeps SCSI devices from timing out (static-blink fix). Stops cleanly on SIGINT.

```bash
trcc display play [OPTIONS] KEY
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |

| Option | Description |
|---|---|
| `--interval`, `-i` `INTERVAL` | Tick interval in seconds (default: AppSettings.refresh_interval_s) |

### `trcc display play-video`

Decode a video and start playing it on the device. Overrides the active theme's background until `stop-video` runs. Frames advance on each `display play` tick.

```bash
trcc display play-video [OPTIONS] KEY PATH
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `PATH` | Video path (mp4/mov/webm/mkv/avi/zt) |

| Option | Description |
|---|---|
| `--fps` `FPS` | Decode FPS (default: 15) |

### `trcc display restore-theme`

Reload the device's persisted theme — convenience after restart.

```bash
trcc display restore-theme KEY
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |

### `trcc display resume`

Send each detected device's last-used theme (headless, no GUI). Use case: cron / systemd unit / udev hook that runs at boot or after a suspend cycle. Enumerates every TRCC-known device on the bus, connects, and replays the saved theme so the displays come back to their pre-boot / pre-suspend state without the GUI. Bulk/LY devices fade after ~2-3 s without a fresh frame — pair this with `trcc display keepalive` per device for those, or `trcc display play` for the full render-loop.

```bash
trcc display resume [OPTIONS]
```

| Option | Description |
|---|---|
| `--retries` `RETRIES` | Discovery attempts before giving up (1 attempt = 2 s delay) |

### `trcc display screencast`

Stream a screen region to the LCD until interrupted. Wraps `StartScreencast` — the GUI `ScreencastHandler` subscriber drives the per-frame Qt capture timer. Ctrl-C calls `StopScreencast` for clean teardown.

```bash
trcc display screencast [OPTIONS] KEY X Y W H
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `X` | Top-left X coordinate of capture region (px) |
| `Y` | Top-left Y coordinate of capture region (px) |
| `W` | Capture region width (px) |
| `H` | Capture region height (px) |

| Option | Description |
|---|---|
| `--audio` | Pipe system audio alongside the video feed (Linux: PipeWire) |

### `trcc display seek-video`

Jump the playback cursor to a specific frame.

```bash
trcc display seek-video KEY FRAME
```

| Argument | Description |
|---|---|
| `KEY` | Device key |
| `FRAME` | Frame index to jump to |

### `trcc display send-image`

Push an image to the LCD once — no theme staging, no persistence. Companion to `load-image` (which materialises a single-image theme and persists `DeviceSettings.current_theme`). Use this when you want ephemeral display: boot logos, quick previews, API upload pipelines.

```bash
trcc display send-image KEY PATH
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `PATH` | Image file (PNG/JPG/BMP/WEBP) |

### `trcc display set-brightness`

Set per-device display brightness.

```bash
trcc display set-brightness KEY PERCENT
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `PERCENT` | Brightness 0–100 |

### `trcc display set-fit-mode`

Set how the background fits the canvas.

```bash
trcc display set-fit-mode KEY MODE
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `MODE` | Fit mode: 'width' (letterbox), 'height' (pillarbox), 'stretch' |

### `trcc display set-orientation`

Set per-device rotation.

```bash
trcc display set-orientation KEY DEGREES
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `DEGREES` | Rotation: 0, 90, 180, or 270 |

### `trcc display sleep`

Blank the panel so it goes dark (the shutdown / turn-off action). Sends a solid-black frame (LCD) or an all-off payload (LED) — the same Command the GUI + daemon fire at PC shutdown so the screen doesn't hold its last image lit. Idempotent; auto-connects in a fresh process.

```bash
trcc display sleep KEY
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |

### `trcc display slideshow`

Toggle the per-device slideshow on/off.

```bash
trcc display slideshow KEY STATE
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `STATE` | 'on' / 'off' |

### `trcc display slideshow-run`

Foreground slideshow over a directory of themes. Different from `slideshow` / `configure-slideshow` (which persist state). This is a one-shot loop: blocks until Ctrl-C, swaps to the next theme each tick. Useful for demos + smoke tests; the persisted flow is what production users want.

```bash
trcc display slideshow-run [OPTIONS] KEY THEMES_DIR
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `THEMES_DIR` | Directory containing theme subdirectories |

| Option | Description |
|---|---|
| `--interval`, `-i` `INTERVAL` | Seconds between theme switches (default: 30.0) |

### `trcc display snapshot`

Print the persisted LCD state for a device.

```bash
trcc display snapshot [OPTIONS] KEY
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |

| Option | Description |
|---|---|
| `--json` | Emit JSON instead of human text. |

### `trcc display split-mode`

Set the Dynamic Island style (widescreen panels only).

```bash
trcc display split-mode KEY MODE
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `MODE` | 0 (off), 1 (style A), 2 (B), 3 (C) |

### `trcc display stop-screencast`

Stop an active screencast started by another process (daemon/API).

```bash
trcc display stop-screencast KEY
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |

### `trcc display stop-video`

Clear the video playback override (returns to the active theme).

```bash
trcc display stop-video KEY
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |

### `trcc display test`

Color-cycle the LCD: red → green → blue → black. Smallest end-to-end exercise of the wire chain. Useful when porting a new device class to confirm handshake → frame build → USB send all work before fighting overlay/theme bugs.

```bash
trcc display test [OPTIONS] KEY
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |

| Option | Description |
|---|---|
| `--seconds`, `-s` `SECONDS` | Hold each color for this many seconds. |

### `trcc display test-lcd`

Print an ANSI true-color preview of the LCD's current render. Same pipeline as `display play` but stops at the renderer surface — no wire send. Useful for headless / sshell debugging where you can't see the physical device.

```bash
trcc display test-lcd [OPTIONS] KEY
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |

| Option | Description |
|---|---|
| `--cols`, `-c` `COLS` | Width of the ANSI preview in terminal cells. |

### `trcc display toggle-video`

Flip video playback between paused / playing (single-verb helper).

```bash
trcc display toggle-video KEY
```

| Argument | Description |
|---|---|
| `KEY` | Device key |

### `trcc display upload-mask`

Copy a mask into user_content_dir/masks and apply it to the device.

```bash
trcc display upload-mask KEY SOURCE
```

| Argument | Description |
|---|---|
| `KEY` | Device key |
| `SOURCE` | Mask image file to copy + apply |

### `trcc display video-status`

Report what the device's video playback is doing (read-only). Answers "is a video loaded, where is it, how fast" without rendering a frame — the query half of the video surface, alongside `play-video` / `stop-video` / `pause-video` / `seek-video` / `loop-video`.

```bash
trcc display video-status KEY
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |

## `trcc led`

RGB LED control.

### `trcc led brightness`

Set the global LED brightness (persists).

```bash
trcc led brightness KEY PERCENT
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |
| `PERCENT` | Brightness 0-100 |

### `trcc led clock-format`

Set the 12h/24h clock display for LC2-style segment devices.

```bash
trcc led clock-format KEY FMT
```

| Argument | Description |
|---|---|
| `KEY` | LED device key |
| `FMT` | '12h' or '24h' |

### `trcc led color`

Set the LED color used by STATIC / BREATHING / COLORFUL modes.

```bash
trcc led color KEY COLOR
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |
| `COLOR` | Hex color (#rrggbb) |

### `trcc led disk-index`

Pick which disk's read/write stats to surface.

```bash
trcc led disk-index KEY INDEX
```

| Argument | Description |
|---|---|
| `KEY` | LED device key |
| `INDEX` | Disk index (0-based) |

### `trcc led initialize`

Connect + render one initial frame in a single dispatch. Convenience for boot scripts — equivalent to `device connect` followed by `led render`, but in one Command so the caller only inspects one Result. Use this on app start; use the individual commands for finer control.

```bash
trcc led initialize KEY
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |

### `trcc led list-modes`

List every animation mode (STATIC, BREATHING, RAINBOW, …).

```bash
trcc led list-modes
```

### `trcc led list-styles`

List every LED style registered in the PM byte registry.

```bash
trcc led list-styles
```

### `trcc led load-source`

Pick the sensor source for LOAD_LINKED mode.

```bash
trcc led load-source KEY SOURCE
```

| Argument | Description |
|---|---|
| `KEY` | LED device key |
| `SOURCE` | 'cpu' or 'gpu' |

### `trcc led memory-ratio`

Set the DDR memory multiplier for the LED memory gauge.

```bash
trcc led memory-ratio KEY RATIO
```

| Argument | Description |
|---|---|
| `KEY` | LED device key |
| `RATIO` | DDR multiplier: 1, 2, or 4 |

### `trcc led mode`

Set the LED animation mode (persists).

```bash
trcc led mode KEY MODE
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |
| `MODE` | One of: static, breathing, colorful, rainbow, temp_linked, load_linked |

### `trcc led play`

Run the LED render ticker until Ctrl-C. Mirrors `display play` — dispatches `RenderLed` every tick so BREATHING / COLORFUL / RAINBOW animations advance. Stops cleanly on SIGINT.

```bash
trcc led play [OPTIONS] KEY
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |

| Option | Description |
|---|---|
| `--interval`, `-i` `INTERVAL` | Tick interval in seconds (default: AppSettings.refresh_interval_s) |

### `trcc led render`

Render one LED frame from current settings + sensors and send. Reads the device's saved mode / color / brightness from Settings, advances the engine's phase counters on `app.led_runtime`, and sends one tick. Pass `--color` to override the saved color (treated as STATIC at full brightness — diagnostic shape).

```bash
trcc led render [OPTIONS] KEY
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |

| Option | Description |
|---|---|
| `--color`, `-c` `COLOR` | Override hex color (#rrggbb); omit to use the saved color |
| `--phase`, `-p` `PHASE` | Rotation phase for multi-phase displays |

### `trcc led select-zone`

Set the currently-selected zone (UI state).

```bash
trcc led select-zone KEY ZONE
```

| Argument | Description |
|---|---|
| `KEY` | LED device key |
| `ZONE` | Zone index to select |

### `trcc led set-colors`

Push a full LED color update.

```bash
trcc led set-colors [OPTIONS] KEY COLORS
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |
| `COLORS` | Hex colors (#rrggbb), one per LED |

| Option | Description |
|---|---|
| `--brightness`, `-b` `BRIGHTNESS` | Global brightness 0–100 |
| `--off` | Force all LEDs off (overrides colors) |

### `trcc led snapshot`

Print the persisted LED state for a device.

```bash
trcc led snapshot [OPTIONS] KEY
```

| Argument | Description |
|---|---|
| `KEY` | LED device key |

| Option | Description |
|---|---|
| `--json` | Emit JSON instead of human text. |

### `trcc led temp-source`

Pick the sensor source for TEMP_LINKED mode.

```bash
trcc led temp-source KEY SOURCE
```

| Argument | Description |
|---|---|
| `KEY` | LED device key |
| `SOURCE` | 'cpu' or 'gpu' |

### `trcc led test-led`

Print an ANSI true-color preview of the LED zones in the terminal. Reads the current zone color list from `LedSnapshot` and paints each zone as a coloured square — handy for visualising multi-zone strips during headless debugging.

```bash
trcc led test-led KEY
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |

### `trcc led test-mode`

Toggle the 4-color diagnostic test cycle.

```bash
trcc led test-mode KEY ON
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |
| `ON` | Enable (true) or disable (false) |

### `trcc led toggle`

Turn the LED device (or one zone) on/off.

```bash
trcc led toggle [OPTIONS] KEY STATE
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |
| `STATE` | 'on' or 'off' (or use --zone N to target one zone) |

| Option | Description |
|---|---|
| `--zone`, `-z` `ZONE` | Toggle a single zone (omit for global toggle) |

### `trcc led toggle-segment`

Flip one segment on/off (segment-display devices).

```bash
trcc led toggle-segment KEY INDEX STATE
```

| Argument | Description |
|---|---|
| `KEY` | LED device key |
| `INDEX` | Segment index |
| `STATE` | 'on' or 'off' |

### `trcc led week-start`

Pick the week-start day on devices that show a day-of-week display.

```bash
trcc led week-start KEY DAY
```

| Argument | Description |
|---|---|
| `KEY` | LED device key |
| `DAY` | 'sunday' or 'monday' |

### `trcc led zone-brightness`

Set one zone's persistent brightness.

```bash
trcc led zone-brightness KEY ZONE PERCENT
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |
| `ZONE` | Zone index (0-based) |
| `PERCENT` | Brightness 0-100 |

### `trcc led zone-color`

Set one zone's persistent color.

```bash
trcc led zone-color KEY ZONE COLOR
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |
| `ZONE` | Zone index (0-based) |
| `COLOR` | Hex color (#rrggbb) |

### `trcc led zone-mode`

Set one zone's persistent animation mode.

```bash
trcc led zone-mode KEY ZONE MODE
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |
| `ZONE` | Zone index (0-based) |
| `MODE` | One of: static, breathing, colorful, rainbow, temp_linked, load_linked |

### `trcc led zone-sync`

Toggle the zone-sync carousel (optionally set the interval).

```bash
trcc led zone-sync [OPTIONS] KEY STATE
```

| Argument | Description |
|---|---|
| `KEY` | LED device key |
| `STATE` | 'on' or 'off' |

| Option | Description |
|---|---|
| `--interval`, `-i` `INTERVAL` | Set ticks-per-rotation alongside the toggle |

## `trcc system`

System-level operations (setup, sensors, info).

### `trcc system autostart`

Manage auto-launch-on-login (XDG .desktop on Linux).

```bash
trcc system autostart
```

### `trcc system check-update`

Ask GitHub Releases whether a newer version is available.

```bash
trcc system check-update
```

### `trcc system debug-report`

Generate a debug report bundle for GitHub issues.

```bash
trcc system debug-report [OPTIONS]
```

| Option | Description |
|---|---|
| `--output`, `-o` `OUTPUT` | Write the report to this path instead of stdout. Recommended when filing a GitHub issue — attach the file. |
| `--log-lines` `LOG_LINES` | How many trailing log lines to include (default 1000). |

### `trcc system doctor`

Run health checks — exits 1 on any FAIL. The reporter-friendly summary tells you what's wrong + how to fix it. For a copy-paste GitHub-issue dump, use `system debug-report` instead.

```bash
trcc system doctor
```

### `trcc system download`

Pre-fetch the theme + cloud + mask archives for a resolution. DiscoverDevices runs this implicitly the first time a device of a given resolution attaches. Call it directly to populate the local cache while you have network — handy for headless setups that'll later run offline. Idempotent.

```bash
trcc system download WIDTH HEIGHT
```

| Argument | Description |
|---|---|
| `WIDTH` | Display width (px), e.g. 320 |
| `HEIGHT` | Display height (px), e.g. 320 |

### `trcc system first-run-status`

Show whether trcc has been set up on this machine yet.

```bash
trcc system first-run-status
```

### `trcc system hdd-enabled`

Toggle inclusion of HDD metrics in sensor broadcasts.

```bash
trcc system hdd-enabled STATE
```

| Argument | Description |
|---|---|
| `STATE` | 'on' or 'off' |

### `trcc system health`

Quick read-only health report — same checks as `doctor`, no exit code.

```bash
trcc system health
```

### `trcc system hid-debug`

Connect to *key* + print handshake details for a GitHub issue paste. Composes `ConnectDevice` (returns handshake bytes + parsed resolution / model id / serial) and `LcdSnapshot` to dump the persisted state. Output is plain text — copy + paste-friendly.

```bash
trcc system hid-debug KEY
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |

### `trcc system info`

Show current sensor metrics (CPU/GPU/fan/disk/net readings). Mirrors legacy `trcc info` — dispatches `ReadSensors` and prints each reading. Use `--metric <prefix>` to narrow the output; pass no args for everything. For paths / install info / permissions, see `trcc system platform-info`.

```bash
trcc system info [OPTIONS]
```

| Option | Description |
|---|---|
| `--metric`, `-m` `METRIC` | Filter readings whose `sensor_id` startswith this prefix (e.g. cpu, gpu, mem, disk, net, fan, time). |

### `trcc system lang`

Print the currently-active UI language code. Read-only — for "what language is TRCC in right now?" without digging through `snapshot`. Use `set-language` to change it.

```bash
trcc system lang
```

### `trcc system led-debug`

LED device handshake + zone/segment dump + optional test cycle. Composes `ConnectDevice` and `LedSnapshot` for a one- shot diagnostic. Pass `--test-colors` to cycle the device's test pattern so you can confirm wire-up visually.

```bash
trcc system led-debug [OPTIONS] KEY
```

| Argument | Description |
|---|---|
| `KEY` | LED device key, e.g. 0416:8001 |

| Option | Description |
|---|---|
| `--test-colors` | After handshake, enable the 4-color test cycle (Ctrl-C to stop). |

### `trcc system list-disks`

List disk partitions (for use with `led disk-index`).

```bash
trcc system list-disks
```

### `trcc system list-endpoints`

Enumerate every HTTP route the REST API exposes. Builds the FastAPI app (no uvicorn) and walks its router so the output reflects what `trcc api` / `trcc serve` would serve.

```bash
trcc system list-endpoints
```

### `trcc system list-fans`

List fans the sensors aggregator exposes, with live readings. Read-only diagnostic (#145/#207) — snapshot() maps fans to theme slots automatically (GPU slot follows the picked GPU); this shows what the box exposes.

```bash
trcc system list-fans
```

### `trcc system list-fonts`

List font families Qt can see.

```bash
trcc system list-fonts
```

### `trcc system list-gpus`

List GPUs exposed by the sensors aggregator.

```bash
trcc system list-gpus
```

### `trcc system list-languages`

List every UI language the i18n table supports.

```bash
trcc system list-languages
```

### `trcc system list-sensors`

Print every sensor the platform enumerates — descriptors only. Read-only enumeration: no polling, no values. Pair with `system sensors` (or `system info --metric <prefix>`) when you want the current readings instead.

```bash
trcc system list-sensors
```

### `trcc system mark-setup-done`

Tell trcc the welcome flow has been completed.

```bash
trcc system mark-setup-done
```

### `trcc system paths`

Show where this install keeps config, data, logs and user content. Answers "where did my theme go?" and "which log do I attach to an issue?" without the user guessing at `~/.trcc` versus `~/.trcc-user`.

```bash
trcc system paths [OPTIONS]
```

| Option | Description |
|---|---|
| `--resolution`, `-r` `RESOLUTION` | WxH (e.g. 854x480) — also show the per-resolution theme/mask dirs |

### `trcc system platform-info`

Show platform info (distro, install method, config dir, permissions).

```bash
trcc system platform-info
```

### `trcc system sensors`

Print current sensor readings.

```bash
trcc system sensors
```

### `trcc system setup`

Run the OS-specific setup (udev rules on Linux, WinUSB guide on Windows).

```bash
trcc system setup [OPTIONS]
```

| Option | Description |
|---|---|
| `--yes`, `-y` | Non-interactive (assume yes to prompts) |

### `trcc system snapshot`

Print the AppSettings snapshot (language, GPU, refresh interval).

```bash
trcc system snapshot [OPTIONS]
```

| Option | Description |
|---|---|
| `--json` | Emit JSON instead of human text. |

### `trcc system upgrade`

Upgrade trcc-linux via the detected package manager.

```bash
trcc system upgrade [OPTIONS]
```

| Option | Description |
|---|---|
| `--yes`, `-y` | Skip confirmation and run the upgrade subprocess. |
| `--dry-run` | Print the command that would run, don't execute it. |

## `trcc theme`

Save / export / import themes.

### `trcc theme cloud-list`

List themes in Thermalright's hosted catalog.

```bash
trcc theme cloud-list [OPTIONS]
```

| Option | Description |
|---|---|
| `--category`, `-c` `CATEGORY` | Category prefix: 'all' / 'a' / 'b' / 'c' / 'd' / 'e' / 'y' |

### `trcc theme cloud-load`

Download a cloud theme and load it on a device.

```bash
trcc theme cloud-load KEY THEME_ID
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `THEME_ID` | Cloud theme id, e.g. a001 |

### `trcc theme create`

One-shot theme builder: bg + optional mask + overlay metrics → save. Mirrors legacy `trcc theme --save`. Dispatches a chain of existing Commands: `LoadImage` for the background, `UploadCustomMask` if `--mask` given, `AddOverlayElement` per `--metric` arg, then `SaveTheme` to persist the result. Stops on the first failure and leaves the device in whatever state was reached.

```bash
trcc theme create [OPTIONS] KEY NAME
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `NAME` | Theme name to save as |

| Option | Description |
|---|---|
| `--bg`, `-b` `BACKGROUND` | Background image (PNG/JPG/BMP/WEBP) |
| `--mask` `MASK` | Optional mask PNG to overlay (custom_<name>/01.png) |
| `--metric`, `-m` `METRIC` | Overlay metric spec: 'metric_key:x,y[:color[:size]]' — e.g. 'cpu:temp:160,90:#ff8800:24'. color defaults to '#ffffff', size defaults to 16. Repeatable. |

### `trcc theme delete`

Delete a theme directory. Path-based to match legacy's `delete_theme(lcd, path)` — the caller already has the resolved path from `theme list` output.

```bash
trcc theme delete PATH
```

| Argument | Description |
|---|---|
| `PATH` | Absolute path to the theme directory to delete |

### `trcc theme export`

Zip a theme into an archive file.

```bash
trcc theme export KEY THEME_NAME ARCHIVE_PATH
```

| Argument | Description |
|---|---|
| `KEY` | Device key (e.g. 0402:3922) whose resolution scopes the lookup |
| `THEME_NAME` | Theme name (directory under user_theme_dir(w, h)) |
| `ARCHIVE_PATH` | Destination archive path (e.g. theme.tr) |

### `trcc theme export-config`

Snapshot one device's settings to a JSON file. Captures everything in `DeviceSettings`: active theme path, brightness, orientation, overlay edits, mask choice, format prefs. Pair with `trcc theme import-config` to restore on another host or after a wipe.

```bash
trcc theme export-config KEY OUTPUT_PATH
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `OUTPUT_PATH` | Destination JSON path (e.g. mydevice.json) |

### `trcc theme export-dc`

Write a theme out as legacy `config1.dc` for Windows TRCC users.

```bash
trcc theme export-dc KEY THEME_NAME OUTPUT_PATH
```

| Argument | Description |
|---|---|
| `KEY` | Device key (e.g. 0402:3922) — its resolution scopes the lookup and layers the user's overlay elements into the export |
| `THEME_NAME` | Theme name (directory under user_theme_dir(w, h)) |
| `OUTPUT_PATH` | Where to write the config1.dc file |

### `trcc theme export-overlay`

Export just a theme's overlay layout (the metric grid) for sharing — lighter than the whole-theme zip and distinct from the DC binary.

```bash
trcc theme export-overlay KEY THEME_NAME OUTPUT_PATH
```

| Argument | Description |
|---|---|
| `KEY` | Device key (e.g. 0402:3922) — its resolution scopes the lookup |
| `THEME_NAME` | Theme name (directory under user_theme_dir(w, h)) |
| `OUTPUT_PATH` | Where to write the overlay layout file |

### `trcc theme import`

Unpack a theme archive into the device's per-resolution theme dir.

```bash
trcc theme import KEY ARCHIVE_PATH [NAME]
```

| Argument | Description |
|---|---|
| `KEY` | Device key (e.g. 0402:3922) whose resolution scopes the target |
| `ARCHIVE_PATH` | Archive to unpack |
| `NAME` | Theme name (defaults to archive filename stem) *(optional)* |

### `trcc theme import-config`

Restore one device's settings from an export-config JSON file.

```bash
trcc theme import-config KEY INPUT_PATH
```

| Argument | Description |
|---|---|
| `KEY` | Device key, e.g. 0402:3922 |
| `INPUT_PATH` | Source JSON written by `trcc theme export-config` |

### `trcc theme list`

List themes for a device resolution. By default scans both `data/theme{W}{H}` (pkg + GitHub-downloaded) and `user_content_dir/data/theme{W}{H}` (legacy user-saved location) so installed-user themes show up alongside fresh downloads.

```bash
trcc theme list [OPTIONS] [KEY]
```

| Argument | Description |
|---|---|
| `KEY` | Device key (e.g. 0402:3922) — its resolution scopes the scan. Required unless --dir is given. *(optional)* |

| Option | Description |
|---|---|
| `--dir`, `-d` `DIRECTORY` | Override: scan an explicit directory instead of the device's theme dirs. |

### `trcc theme save`

Duplicate the device's active theme directory under a new name.

```bash
trcc theme save KEY NAME
```

| Argument | Description |
|---|---|
| `KEY` | Device key whose active theme to save |
| `NAME` | New theme name (directory under user_content_dir) |

## Files

| Path | Contents |
|---|---|
| `~/.trcc/` | Program + cloud data and config (`config.json`, logs) |
| `~/.trcc-user/` | User-authored themes, backgrounds, and masks |

Report bugs at <https://github.com/Lexonight1/thermalright-trcc-linux/issues> — include the output of `trcc report`.

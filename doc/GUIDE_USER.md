# User Guide

Everything you need to know about using TRCC Linux — GUI, CLI, themes, overlays, LED, media player, and more.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [The GUI](#the-gui)
3. [Themes](#themes)
4. [Overlay Editor](#overlay-editor)
5. [Media Player](#media-player)
6. [Screencast](#screencast)
7. [Audio Visuals](#audio-visuals)
8. [LED Control](#led-control)
9. [RGB Lighting](#rgb-lighting)
10. [System Monitoring](#system-monitoring)
11. [Control Center, Modules and About](#control-center-modules-and-about)
12. [Closing the Window vs Quitting](#closing-the-window-vs-quitting)
13. [CLI Basics](#cli-basics)
14. [REST API](#rest-api)
15. [Configuration](#configuration)
16. [Tips and Tricks](#tips-and-tricks)

---

## Getting Started

After installing (see [Install Guide](GUIDE_INSTALL.md) or [New to Linux](GUIDE_NEW_TO_LINUX.md)):

```bash
# Set up device permissions (one time) — run as your own user; it asks for sudo itself
trcc system setup

# Unplug and replug your LCD

# Launch the GUI
trcc gui
```

Your device should appear in the sidebar. Click it to select it.

---

## The GUI

The GUI matches the Windows TRCC app layout. Here's what each area does:

### Sidebar (Left)
- **Sensor** (top) — live system metrics panel (see [System Monitoring](#system-monitoring))
- **Device buttons** — one per connected LCD/LED device. Click to select.
- **RGB** — the [RGB Lighting](#rgb-lighting) page
- **About** — opens the [Control Center](#control-center-modules-and-about): version, update check, language and app settings

### Preview (Left of the tabs)
- Shows what's currently displayed on your LCD
- Click overlay elements to select and edit them
- Drag elements to reposition
- Video playback controls appear when a video is playing
- Drop an image file on it to crop it as the background, or a video to play it in the media player

Below the preview: the rotation picker (0°/90°/180°/270°), the brightness slider, the device line (name · VID:PID · FBL/PM/SUB, selectable for bug reports), and the theme name box with its **Save** button.

### Tabs (Right)
Five tabs across the top: **Local themes**, **Cloud masks**, **Cloud backgrounds** (the Gallery), **Audio visuals**, and **Settings**.

### Settings Tab
Four mode panels at the bottom:
1. **Layer Mask** — apply decorative masks over themes
2. **Background** — load a static image or video as background
3. **Screencast** — capture a region of your screen
4. **Media Player** — play any video file or web video on your LCD

Toggle a panel ON to enable it. Only one of Background/Screencast/Media Player can be active at a time.

### Overlay Editor (Settings tab, upper)
- Add text overlays: time, date, CPU temp, GPU usage, etc.
- Each element has position (X/Y), font, size, and color
- Click an element in the grid to select it, then edit properties

---

## Themes

### Local Themes
Click a theme thumbnail to apply it. Themes are stored per resolution (320x320, 480x480, etc.).

On first launch, TRCC downloads theme packs automatically. If themes are missing,
fetch the pack for your panel's resolution:
```bash
trcc system download 320 320
```

CLI (`0402:3922` is an example key — use your own from `trcc detect`):
```bash
trcc theme list 0402:3922                  # list local themes
trcc display load-theme 0402:3922 ~/.trcc/data/theme320320/Theme1   # load by its directory
trcc display list-masks 0402:3922          # list available masks
```

### Cloud Themes
The **Cloud backgrounds** tab (titled **Gallery**) shows video backgrounds available for download from the cloud. Click to download and apply.

### Masks
Masks are decorative overlays (borders, shapes, gauges) that sit on top of your theme. Browse them in the **Cloud Masks** tab or load your own PNG.

Masks are listed from both cloud cache (`~/.trcc/data/web/zt{W}{H}/`) and user-created (`~/.trcc-user/data/web/zt{W}{H}/`).

### Theme Carousel
Set up automatic theme rotation:
1. On the **Local themes** tab, turn on the slideshow button in the header
2. Select up to 6 themes (click the lower half of a thumbnail)
3. Set the interval (seconds) in the box beside the button

Themes cycle automatically at the configured interval.

### Game Mode
The game button on the **Local themes** header, with the CPU % box beside it (default 75): while CPU usage stays above that threshold, the panel shows only its overlay, on black, until usage falls back. CLI: `trcc display game 0402:3922 on --threshold 80`.

### Saving and Exporting
- **Save** — saves your current display state as a custom theme to `~/.trcc-user/` (survives uninstall and data re-downloads)
- **Export** — creates a `.tr` archive you can share with others
- **Import** — loads a `.tr` archive from someone else

All three interfaces (GUI, CLI, API) save through the same `SaveTheme` command.

---

## Overlay Editor

The overlay editor lets you put live system data on your LCD.

### Adding Elements
1. Click the **+** cell in the overlay grid
2. Choose a type: **Hardware Data** (then pick the metric), **Time**, **Weekday**, **Date**, or **Custom Text**
3. Position it by dragging on the preview or entering X/Y coordinates
4. Set font, size, and color

### Available Metrics
- **Time** — 24h (HH:mm) or 12h (hh:mm AM/PM)
- **Date** — yyyy/MM/dd, dd/MM/yyyy, MM/dd, dd/MM
- **CPU** — temperature, usage %, clock, power
- **GPU** — temperature, usage %, clock, power
- **MEM** — temperature (DDR5 only), usage %, clock, available
- **HDD** — temperature, activity, read, write
- **NET** — upload/download rate, total uploaded/downloaded
- **FAN** — RPM: CPU fan, GPU fan, two more fan slots (if sensors available)

### Format Preferences
- Temperature unit: Celsius or Fahrenheit — one setting for every device (`trcc config temp-unit`)
- Time and date format: each clock element keeps its own, saved with the theme. Select the element and use its format buttons; `trcc config time-format` / `trcc config date-format` set every clock element on every device at once.

---

## Media Player

Play any video file on your LCD:

1. Toggle the **Media Player** panel ON
2. Click the **Load Video** button
3. Pick a file (.mp4, .avi, .mkv, .mov, .gif)
4. The video plays on your LCD with looping

Or click **Web Video** and enter an http, https or rtsp address (a video file or a live stream), or drop a video file on the preview.

**Controls** (appear on the preview):
- Play / Pause button
- Seek slider
- Width-fit / Height-fit toggle
- Progress bar with time display

Toggle the panel OFF to return to your last theme.

---

## Screencast

Mirror a region of your screen to the LCD:

1. Toggle the **Screencast** panel ON
2. Set the capture region: X, Y, Width, Height
3. The LCD shows that region of your screen in real-time

**Options:**
- **Border toggle** — show/hide the capture region border on screen
- **Audio toggle** — enable mic visualization (spectrum bars at the bottom of the frame)

Changing W or H keeps the region's aspect ratio.

Toggle the panel OFF to return to your last theme.

### Wayland Note
On Wayland desktops, TRCC uses the PipeWire screen-cast portal for screen capture. You'll see a permission dialog the first time — allow it and it remembers.

---

## Audio Visuals

The **Audio visuals** tab is a gallery of sound-driven animations: one tile per visual (and per preset). Clicking one makes it the panel's background; picking any other background ends it. CLI: `trcc display visuals` lists them, `trcc display visual` plays one.

---

## LED Control

If you have a Thermalright LED device (AX120, PA120, etc.):

1. Click the LED device in the sidebar
2. Choose a mode: Solid, Breathe, Color Cycle, Rainbow, Temp Linked, Load Linked, or OpenRGB (the colours OpenRGB sends)
3. Set color and brightness
4. Multi-zone devices: control each zone independently or sync them

### Segment Display
Some LED devices have 7-segment displays showing temperature. Configure:
- Temperature source (CPU, GPU, etc.)
- Unit (C/F)
- Clock mode (show time instead of temp)

---

## RGB Lighting

The **RGB** button in the sidebar opens the RGB Lighting page, for lights that are not a Thermalright cooler: RGB memory, the motherboard's lights (ASUS Aura headers and onboard LEDs), and devices in OpenRGB.

- **Left:** the devices — **Memory**, **Motherboard**, **OpenRGB** — and **Find lights**, which looks for them (RGB memory on the SMBus, OpenRGB's devices through its SDK server).
- **Right:** the lights of the device you picked, and its source:
  - **Leave alone** — TRCC sends nothing.
  - **Built-in effect** (Memory and Motherboard) — Static, Colour Shift, Colour Pulse, Rainbow, Rainbow Wave, Colour Wave, Visor, Rain, Marquee, Sequential. The list ends with **CPU temp**, **CPU load**, **GPU temp**, **GPU load**: a colour that goes green → yellow → orange → red as the reading rises. On memory, an effect is saved on the stick and keeps running after TRCC closes; a linked colour is live only while TRCC runs.
  - **Follow a device** — the lights take their colours from any connected LCD's picture or from an LED cooler.
- **Apply** changes only the device shown. Tick the lights it should change: each RAM stick can run its own effect.

Memory lighting is off until you allow it (the page offers this; it asks for your password). CLI: `trcc system ram-lighting enable`, `trcc system rgb --scan`, `trcc system ram-effect`, `trcc system aura-effect`, `trcc system linked-color`, `trcc system follow`.

---

## System Monitoring

The **Sensor** panel (top of the sidebar) shows live metrics in panels of four rows. By default:
- CPU: temp, usage, clock, power
- GPU: temp, usage, clock, power (NVIDIA works out of the box — the reader ships with TRCC)
- Memory: temp, usage, clock, available
- HDD: temp, activity, read, write
- Network: up/down rate, total up/down
- Fan: CPU fan, GPU fan, two more fan slots

The arrow beside a row picks a different sensor for it, and **+** adds a custom panel.

These same metrics power the overlay elements on your LCD.

---

## Control Center, Modules and About

The **About** button at the bottom of the sidebar opens the **Control Center**:
- Start automatically, temperature unit (°C/°F), hard disk information, data refresh time, graphics card, language selection
- Software version and software update
- Right-click the page for **Save diagnostic report…** (the same report as `trcc report`)

The gold bar at the top also has:
- **Modules** — every module with an On/Off switch, what it does, and why it cannot run on this computer when it cannot. CLI: `trcc system modules list` / `on` / `off`.
- **About** — who TRCC is inspired by, what it does, and the licence of every work it uses.

---

## Closing the Window vs Quitting

One TRCC process runs your panels; the GUI, `trcc qtgui`, the CLI and the API are all remote controls for it, and the first one you start starts it.
- Closing the window hides it to the tray. The tray's **Exit** closes the window only — the panels keep playing.
- `trcc kill` stops TRCC itself.
- `trcc status` shows what every device is doing; `trcc daemon-status` says whether TRCC is running.

---

## CLI Basics

Everything the GUI does, the CLI can do too. Useful for scripting, headless servers, or SSH sessions.

Commands that act on a device take its `VID:PID` key as the first argument —
`trcc detect` prints the keys.

```bash
# Device management
trcc detect                                   # list connected devices
trcc device state 0402:3922                   # identity, handshake + profile

# Display
trcc display send-image 0402:3922 image.png   # send an image to the LCD
trcc display load-theme 0402:3922 ~/.trcc/data/theme320320/Theme1   # load a theme
trcc display set-brightness 0402:3922 80      # set brightness to 80%
trcc display set-orientation 0402:3922 90     # rotate display

# LED
trcc led color 0416:8001 ff0000               # set LED to red
trcc led mode 0416:8001 breathing             # breathing effect
trcc led toggle 0416:8001 off                 # turn off LEDs

# System
trcc doctor                                   # check dependencies
trcc report                                   # generate diagnostic report
trcc system info                              # show system info

# Interactive
trcc shell                                    # interactive shell with tab completion
trcc serve                                    # start REST API server
```

### Interactive Prompt

`trcc shell` opens a `trcc>` prompt: type any command without the leading `trcc`. Tab completes command names, the up arrow brings back earlier lines (kept in `~/.local/state/trcc/shell_history`), `help` lists the commands, and `exit` or Ctrl-D leaves.

```
$ trcc shell
trcc> detect
trcc> display color 0402:3922 "#ff0000"
trcc> exit
```

Every line goes to the same running TRCC as the GUI and the API, so there is no start-up wait per command.

See the full [CLI Reference](REFERENCE_CLI.md) for all commands.

---

## REST API

Control TRCC from any language, script, or home automation system.

```bash
# Start the server
trcc serve                    # http://127.0.0.1:8080
trcc serve --token secret     # with authentication (send it as the X-API-Token header)

# Examples
curl http://127.0.0.1:8080/devices
curl -X POST http://127.0.0.1:8080/devices/0402:3922/display/brightness \
     -H 'Content-Type: application/json' -d '{"percent": 80}'
curl http://127.0.0.1:8080/system/metrics
```

Interactive Swagger docs at `http://127.0.0.1:8080/docs` when the server is running.

See the full [API Reference](REFERENCE_API.md) for every endpoint.

---

## Configuration

Config lives in `~/.trcc/`:

| File | Purpose |
|------|---------|
| `trcc.json` | All user settings (themes, overlays, brightness, etc.) |
| `trcc.log` | Application log (for bug reports) |
| `data/` | Downloaded themes, masks, web content |

Your own saved themes and uploaded masks live separately in `~/.trcc-user/`.

To reset settings and downloads (your own themes in `~/.trcc-user/` are kept):
```bash
rm -rf ~/.trcc
```

To change language:
```bash
trcc config language de          # German
trcc config language ja          # Japanese
trcc config language zh          # Chinese
trcc system list-languages       # show all available languages
```

---

## Tips and Tricks

### Autostart on Login
```bash
trcc system autostart enable     # per-user, no sudo
trcc system autostart status     # check whether it is on
```
Installs the autostart entry. TRCC starts minimized to tray and restores your last theme.

### Run in Background (Headless)
```bash
trcc display load-theme 0402:3922 ~/.trcc/data/theme320320/Theme1
trcc display resume
```
No GUI needed — theme plays directly on the LCD. `resume` re-discovers the
device and restores its last theme. `trcc system setup` also installs TRCC as
your user's systemd service (`trccd.service`, running `trcc daemon`), which
shows the saved display by itself at login.

### Debug Mode
```bash
trcc -vv gui
```
`-v`/`-vv`/`-vvv` make the terminal more verbose. The log file `~/.trcc/trcc.log`
always keeps full DEBUG detail, so for a bug report just run `trcc report`.

### Window Will Not Move
```bash
trcc gui --decorated
```
Draws the window with your desktop's normal title bar and borders instead of
the frameless Windows-style shell.

### Multiple Devices
TRCC supports multiple LCD/LED devices simultaneously. Each device gets its own config, theme, and overlay settings. Click a device in the sidebar to switch.

### Brightness Schedule
Set brightness from the CLI or API on a cron job:
```bash
# Low brightness at night (crontab -e)
0 22 * * * trcc display set-brightness 0402:3922 30
0 8  * * * trcc display set-brightness 0402:3922 100
```

### Custom Overlay Fonts
The overlay editor uses system fonts. Install any TrueType font on your system and it'll appear in the font picker.

---

## Documentation Index

| Document | Description |
|----------|-------------|
| [New to Linux](GUIDE_NEW_TO_LINUX.md) | Coming from Windows? Start here |
| [Install Guide](GUIDE_INSTALL.md) | Detailed installation instructions |
| [User Guide](GUIDE_USER.md) | This document — how to use everything |
| [CLI Reference](REFERENCE_CLI.md) | Every CLI command (generated from the code) |
| [API Reference](REFERENCE_API.md) | Every REST API endpoint |
| [Troubleshooting](GUIDE_TROUBLESHOOTING.md) | Common problems and fixes |
| [Device Testing](GUIDE_DEVICE_TESTING.md) | Testing with specific hardware |
| [Supported Devices](REFERENCE_DEVICES.md) | Full device compatibility list |
| [Changelog](CHANGELOG.md) | Version history |

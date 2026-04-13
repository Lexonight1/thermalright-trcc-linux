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
7. [LED Control](#led-control)
8. [System Monitoring](#system-monitoring)
9. [CLI Basics](#cli-basics)
10. [REST API](#rest-api)
11. [Configuration](#configuration)
12. [Tips and Tricks](#tips-and-tricks)

---

## Getting Started

After installing (see [Install Guide](GUIDE_INSTALL.md) or [New to Linux](GUIDE_NEW_TO_LINUX.md)):

```bash
# Set up device permissions (one time)
sudo trcc setup-udev

# Unplug and replug your LCD

# Launch the GUI
trcc gui
```

Your device should appear in the sidebar. Click it to select it.

---

## The GUI

The GUI matches the Windows TRCC app layout. Here's what each area does:

### Sidebar (Left)
- **Device buttons** — one per connected LCD/LED device. Click to select.
- **About** — version info, update check, language selection
- **System Info** — live system metrics panel

### Preview (Center)
- Shows what's currently displayed on your LCD
- Click overlay elements to select and edit them
- Drag elements to reposition
- Video playback controls appear when a video is playing

### Settings Panel (Right)
Four mode panels at the bottom:
1. **Layer Mask** — apply decorative masks over themes
2. **Background** — load a static image or video as background
3. **Screencast** — capture a region of your screen
4. **Media Player** — play any video file on your LCD

Toggle a panel ON to enable it. Only one of Background/Screencast/Media Player can be active at a time.

### Overlay Editor (Right, upper)
- Add text overlays: time, date, CPU temp, GPU usage, etc.
- Each element has position (X/Y), font, size, and color
- Click an element in the grid to select it, then edit properties

---

## Themes

### Local Themes
Click a theme thumbnail to apply it. Themes are stored per resolution (320x320, 480x480, etc.).

On first launch, TRCC downloads theme packs automatically. If themes are missing:
```bash
trcc download
```

CLI:
```bash
trcc theme-list                   # list local themes
trcc theme-load Theme1            # load by name
trcc theme-load Theme1 -p        # load with live ANSI preview
trcc mask-list                    # list available masks
```

### Cloud Themes
The **Gallery** tab shows themes available for download from the cloud. Click to download and apply.

### Masks
Masks are decorative overlays (borders, shapes, gauges) that sit on top of your theme. Browse them in the **Cloud Masks** tab or load your own PNG.

Masks are listed from both cloud cache (`~/.trcc/data/web/zt{W}{H}/`) and user-created (`~/.trcc-user/data/web/zt{W}{H}/`).

### Theme Carousel
Set up automatic theme rotation:
1. Enable carousel in the theme settings
2. Select up to 6 themes
3. Set the rotation interval

Themes cycle automatically at the configured interval.

### Saving and Exporting
- **Save** — saves your current display state as a custom theme to `~/.trcc-user/` (survives uninstall and data re-downloads)
- **Export** — creates a `.tr` archive you can share with others
- **Import** — loads a `.tr` archive from someone else

All three interfaces (GUI, CLI, API) save through the same code path (`LCDDevice.save()`).

---

## Overlay Editor

The overlay editor lets you put live system data on your LCD.

### Adding Elements
1. Click the **+** button in the overlay grid
2. Choose a metric: time, date, CPU temp, GPU usage, RAM, etc.
3. Position it by dragging on the preview or entering X/Y coordinates
4. Set font, size, and color

### Available Metrics
- **Time** — 24h (HH:MM), 12h (h:MM), 24h with seconds
- **Date** — multiple formats (yyyy/MM/dd, MM/dd/yyyy, etc.)
- **CPU** — temperature, usage %, frequency
- **GPU** — temperature, usage %, VRAM, clock speed
- **RAM** — usage %, used/total
- **Disk** — usage %, used/total
- **Network** — upload/download speed
- **Fan** — RPM (if sensor available)
- **Power** — wattage (Intel RAPL / AMD)

### Format Preferences
- Temperature unit: Celsius or Fahrenheit
- Time format: 24h or 12h
- Date format: multiple options

These are saved per device and restored automatically.

---

## Media Player

Play any video file on your LCD:

1. Toggle the **Media Player** panel ON
2. Click the **Load Video** button
3. Pick a file (.mp4, .avi, .mkv, .mov, .gif)
4. The video plays on your LCD with looping

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
- **Aspect lock** — maintain aspect ratio when adjusting W/H

Toggle the panel OFF to return to your last theme.

### Wayland Note
On Wayland desktops (GNOME 44+, KDE Plasma 6), TRCC uses PipeWire for screen capture. You'll see a permission dialog the first time — allow it and it remembers.

---

## LED Control

If you have a Thermalright LED device (AX120, PA120, etc.):

1. Click the LED device in the sidebar
2. Choose an effect mode (static, breathing, colorful, wave, etc.)
3. Set color, brightness, and speed
4. Multi-zone devices: control each zone independently or sync them

### Segment Display
Some LED devices have 7-segment displays showing temperature. Configure:
- Temperature source (CPU, GPU, etc.)
- Unit (C/F)
- Clock mode (show time instead of temp)

---

## System Monitoring

The **System Info** panel shows live metrics:
- CPU: temp, usage, frequency, per-core
- GPU: temp, usage, VRAM, clock (NVIDIA needs `nvidia-ml-py`)
- RAM: used/total/percent
- Disk: usage per mount
- Network: up/down speed
- Power: CPU/GPU wattage

These same metrics power the overlay elements on your LCD.

---

## CLI Basics

Everything the GUI does, the CLI can do too. Useful for scripting, headless servers, or SSH sessions.

```bash
# Device management
trcc detect              # list connected devices
trcc detect --all        # list all devices + numbers
trcc select 1            # select device by number (from detect --all)

# Display
trcc send image.png      # send an image to the LCD
trcc theme-load Theme1   # load a theme by name
trcc brightness 3        # brightness preset (1=25%, 2=50%, 3=100%)
trcc rotation 90         # rotate display

# LED
trcc led-color ff0000    # set LED to red
trcc led-mode breathing  # breathing effect
trcc led-off             # turn off LEDs

# System
trcc doctor              # check dependencies
trcc report              # generate diagnostic report
trcc info                # show system info

# Interactive
trcc shell               # interactive shell with tab completion
trcc serve               # start REST API server
```

See the full [CLI Reference](REFERENCE_CLI.md) for all commands.

---

## REST API

Control TRCC from any language, script, or home automation system.

```bash
# Start the server
trcc serve                    # localhost:9876
trcc serve --token secret     # with authentication
trcc serve --tls              # HTTPS

# Examples
curl http://localhost:9876/devices
curl -X POST http://localhost:9876/display/brightness -d '{"level": 80}'
curl http://localhost:9876/system/metrics
```

Interactive Swagger docs at `http://localhost:9876/docs` when the server is running.

See the full [API Reference](REFERENCE_API.md) for all 49 endpoints.

---

## Configuration

All config lives in `~/.trcc/`:

| File | Purpose |
|------|---------|
| `config.json` | All user settings (themes, overlays, brightness, etc.) |
| `trcc.log` | Application log (for bug reports) |
| `data/` | Downloaded themes, masks, web content |

To reset everything:
```bash
rm -rf ~/.trcc
```

To change language:
```bash
trcc lang-set de    # German
trcc lang-set ja    # Japanese
trcc lang-set zh    # Chinese
trcc lang-list      # show all available languages
```

---

## Tips and Tricks

### Autostart on Login
```bash
trcc install-desktop
```
Creates a `.desktop` file and autostart entry. TRCC starts minimized to tray and restores your last theme.

### Run in Background (Headless)
```bash
trcc theme-load Theme1
trcc resume
```
No GUI needed — theme plays directly on the LCD.

### Debug Mode
```bash
trcc gui -vv
```
Verbose logging — useful for bug reports. Check `~/.trcc/trcc.log` for details.

### Multiple Devices
TRCC supports multiple LCD/LED devices simultaneously. Each device gets its own config, theme, and overlay settings. Click a device in the sidebar to switch.

For details (including how inactive LCDs keep video playing), see `doc/GUIDE_MULTI_DISPLAY.md`.

### Brightness Schedule
Set brightness from the CLI or API on a cron job:
```bash
# Low brightness at night (crontab -e)
0 22 * * * trcc brightness 30
0 8  * * * trcc brightness 100
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
| [CLI Reference](REFERENCE_CLI.md) | All 60 CLI commands |
| [API Reference](REFERENCE_API.md) | All 55 REST API endpoints |
| [Multi-Display](GUIDE_MULTI_DISPLAY.md) | Multiple LCD devices at once |
| [Troubleshooting](GUIDE_TROUBLESHOOTING.md) | Common problems and fixes |
| [Device Testing](GUIDE_DEVICE_TESTING.md) | Testing with specific hardware |
| [Supported Devices](REFERENCE_DEVICES.md) | Full device compatibility list |
| [Changelog](CHANGELOG.md) | Version history |

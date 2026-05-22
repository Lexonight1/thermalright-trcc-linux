# TRCC Linux — Quickstart for next/

This guide is for users trying the **next/ rebuild** of TRCC Linux on
real hardware.  If you've used legacy TRCC Linux before, next/ is the
new generation — same hardware, cleaner architecture, mostly the same
features.  Both can coexist on the same machine.

## What you need

* A Thermalright LCD or LED cooler with a USB cable to the PC.
* Linux with Python 3.11 or newer.
* The right USB permissions so non-root users can talk to the device.
  next/ installs udev rules for you on first run — see "First run"
  below.

## Install

If you already have legacy TRCC Linux installed:

```bash
pip install --upgrade trcc-linux
```

Fresh install:

```bash
pip install trcc-linux
```

Or pick the native package for your distro from the
[releases page](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest).

This installs **two** binaries:
* `trcc`        — the legacy app (still the default).
* `trcc-next`   — the rebuild you'll use for this guide.

next/ does **not** clobber legacy state.  It stores its settings in
`~/.trcc/trcc.json` (legacy stays at `~/.trcc/config.json`).  Use both
freely — they don't fight over the same files.  Settings written by
pre-cutover next/ (`~/.trcc/trcc-next.json`) are read on first launch
and promoted to the new name on the next save.

## First run

Open a terminal:

```bash
trcc-next system doctor
```

You'll see a checklist.  Each line is either OK, WARN, or FAIL.

* **FAIL** lines must be fixed before next/ works — each one tells you
  the command to run (e.g. install ffmpeg, lay down udev rules).
* **WARN** lines are nice-to-have.  "No Thermalright devices detected
  on USB" is normal if your device is unplugged.

If `doctor` flags missing udev rules on Linux, run setup:

```bash
trcc-next system setup
```

This drops `/etc/udev/rules.d/99-trcc.rules` and reloads udev so your
user account can write to the device without `sudo`.

## Plug in your device

After plugging in the cooler, scan:

```bash
trcc-next device list
```

You'll get a list like:

```
0402:3922  —  Thermalright FW360  (scsi, 320×320)
```

The `0402:3922` part is the **device key**.  Every command takes a
device key — this is how next/ tells your devices apart if you have
more than one.

## Try it works (the smallest test)

Push a solid red screen so you can confirm the wire works:

```bash
trcc-next device connect 0402:3922
trcc-next display color 0402:3922 ff0000
```

Your LCD should turn red.  If it doesn't:

```bash
trcc-next system debug-report --output ~/trcc-bug.txt
```

Attach the resulting `trcc-bug.txt` to a GitHub issue —
https://github.com/Lexonight1/thermalright-trcc-linux/issues

## Use the GUI

```bash
trcc-next gui
```

You'll see a sidebar on the left and a content pane on the right:

* **Devices** — scan, connect, disconnect.
* **Display** — orientation, brightness, "load this theme".
* **Themes** — browse + apply themes from your `~/.trcc/themes/`.
* **Cloud themes** — browse + download Thermalright's hosted catalog.
* **Masks** — apply transparent-window overlays.
* **Overlay editor** — add live text, sensor metrics, or clock displays
  on top of your theme.
* **Configuration** — fit mode, split mode, background mode, slideshow.
* **LED** — single-color + brightness for RGB LED controllers.
* **Status** — what each device currently shows + rolling event log.
* **System** — health, sensors, debug bundle.
* **About** — version + links.

## Common workflows

### Pick a cloud theme

1. Devices → click **Scan** → click on your device.
2. Sidebar → **Cloud themes**.
3. Type your device key in the field (e.g. `0402:3922`).
4. Pick a category and double-click a theme.
5. Wait a few seconds — the MP4 downloads and applies.

### Add live CPU temperature on top of a theme

1. Sidebar → **Overlay editor**.
2. Type your device key, click **Load**.
3. Click **Add element…**.
4. Pick **Metric** as the type.
5. Set position (try `x=10`, `y=10`).
6. Set metric id to `cpu:temp` and format to `{value:.0f}°C`.
7. OK.

The CPU temp shows on the device on the next render tick.

### Slideshow your themes

1. Sidebar → **Configuration**.
2. Type your device key, click **Load current settings**.
3. In the Slideshow group, paste theme names (one per line, the order
   you want them to rotate).
4. Set interval to something like `60` seconds.
5. Set Slideshow state to **On**.
6. Click **Apply all settings**.

## Where files live

* Config: `~/.trcc/trcc.json`
* Log: `~/.trcc/trcc.log` (rotates at 1 MB × 5 backups)
* Themes you import: `~/.trcc/themes/<name>/`
* Masks you upload: `~/.trcc/masks/`
* Cloud theme cache: `~/.local/share/trcc/cloud_themes/<resolution>/`

You can wipe `~/.trcc/trcc.json` to reset next/ to defaults — your
themes and masks stay where they are.

## Reporting bugs

Always include a debug report:

```bash
trcc-next system debug-report --output ~/trcc-bug.txt
```

That bundle has: distro, Python version, install method, the paths
next/ is using, the devices on your USB bus, your sensors, your
current settings, health-check results, and the last 1000 log lines.
Paste it into a GitHub issue and someone will look.

## Switching between legacy and next/

Run whichever you prefer — they don't clash.

* Run legacy: `trcc gui`
* Run next/: `trcc-next gui`

Themes saved in one format work in the other (next/ can read legacy's
`config1.dc` files and export back to them — see `trcc-next theme
export-dc`).

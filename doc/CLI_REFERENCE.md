# CLI Reference

Complete reference for the `trcc` command-line interface.

## Usage

```
trcc [--version] [-v] <command> [options]
```

### Global Options

| Option | Description |
|--------|-------------|
| `--version` | Show version and exit |
| `-v` | Increase verbosity (`-v` info, `-vv` debug) |

---

## Commands

### `trcc gui`

Launch the graphical interface.

```bash
trcc gui
trcc gui --decorated
trcc gui -vv          # debug logging
```

| Option | Description |
|--------|-------------|
| `--decorated`, `-d` | Show window with titlebar (can minimize/resize) |

The default window is frameless (matching the Windows TRCC layout). Use `--decorated` for debugging or if your window manager has trouble with frameless windows.

---

### `trcc detect`

Detect connected LCD devices.

```bash
trcc detect            # show active device
trcc detect --all      # list all detected devices
```

| Option | Description |
|--------|-------------|
| `--all`, `-a` | List all devices (not just the active one) |

**Example output:**

```
* [1] /dev/sg2 — Thermalright LCD Display [87cd:70db] (SCSI)
  [2] /dev/sg3 — ALi Corp LCD Display [0416:5406] (SCSI)
```

The `*` marks the currently active device.

---

### `trcc select`

Switch the active device (when multiple LCDs are connected).

```bash
trcc select 2          # select device number 2
```

Device numbers correspond to the `[N]` shown in `trcc detect --all`.

---

### `trcc send`

Send an image to the LCD.

```bash
trcc send image.png
trcc send photo.jpg --device /dev/sg2
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (default: auto-detect) |

The image is automatically resized and cropped to fit the LCD resolution.

---

### `trcc color`

Display a solid color on the LCD.

```bash
trcc color ff0000      # red
trcc color 00ff00      # green
trcc color '#0000ff'   # blue (quote the # in shell)
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (default: auto-detect) |

---

### `trcc test`

Test the display with a color cycle (red, green, blue, yellow, magenta, cyan, white).

```bash
trcc test
trcc test --loop       # cycle continuously until Ctrl+C
trcc test --device /dev/sg2
```

| Option | Description |
|--------|-------------|
| `--loop`, `-l` | Loop colors continuously (Ctrl+C to stop) |
| `--device`, `-d` | Device path (default: auto-detect) |

---

### `trcc reset`

Reset/reinitialize the LCD device. Sends a red test frame with force-init.

```bash
trcc reset
trcc reset --device /dev/sg2
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (default: auto-detect) |

---

### `trcc info`

Show live system metrics (CPU, GPU, memory, date/time).

```bash
trcc info
```

**Example output:**

```
System Information
========================================

CPU:
  cpu_temp: 52°C
  cpu_percent: 12%
  cpu_freq: 3.6 GHz

GPU:
  gpu_temp: 45°C

Memory:
  mem_percent: 34%
  mem_used: 5.4 GB
  mem_total: 16.0 GB

Date/Time:
  date: 2026-02-07
  time: 14:30:00
  weekday: Saturday
```

---

### `trcc setup-udev`

Install udev rules and USB storage quirks (required once after first install).

```bash
# Preview what will be written
trcc setup-udev --dry-run

# Install (auto-prompts for sudo)
trcc setup-udev
```

| Option | Description |
|--------|-------------|
| `--dry-run` | Print rules without installing (no root needed) |

**What this does:**

1. Creates `/etc/udev/rules.d/99-trcc-lcd.rules` — grants your user permission to access the LCD
2. Creates `/etc/modprobe.d/trcc-lcd.conf` — USB quirk that forces bulk-only transport (required for device detection)
3. Reloads udev rules

After running, **unplug and replug the USB cable** (or reboot).

---

### `trcc hid-debug`

HID handshake diagnostic — prints hex dump and resolved device info for bug reports.

```bash
trcc hid-debug
```

**Example output:**

```
HID Debug — Handshake Diagnostic
============================================================

Device: ALi Corp LCD Display
  VID:PID = 0416:52e2
  Type = 2
  Implementation = hid_lcd

  Attempting handshake...
  Handshake OK!
  PM byte  = 100 (0x64)
  SUB byte = 0 (0x00)
  FBL      = 100 (0x64)
  Serial   = ABCDEF0123456789
  Resolution = 320x320
  Button image = A1FROZEN WARFRAME PRO
  FBL 100 = known resolution

  Raw handshake response (first 64 bytes):
  0000: da db dc dd 64 00 00 00 ...
```

---

### `trcc download`

Download cloud theme packs.

```bash
trcc download              # list available packs
trcc download --list       # same as above
trcc download themes-320   # download 320x320 theme pack
trcc download themes-320 --force   # re-download even if exists
trcc download themes-320 --info    # show pack details
```

| Option | Description |
|--------|-------------|
| `--list`, `-l` | List available theme packs |
| `--force`, `-f` | Force re-download |
| `--info`, `-i` | Show pack info without downloading |

---

### `trcc uninstall`

Remove all TRCC configuration, udev rules, and autostart files.

```bash
trcc uninstall             # remove user config files

# Also remove udev rules (requires root — run from the repo directory)
sudo PYTHONPATH=src python3 -m trcc.cli uninstall
```

**Removes:**

| Item | Path |
|------|------|
| Config directory | `~/.config/trcc/` |
| Legacy config | `~/.trcc/` |
| Autostart entry | `~/.config/autostart/trcc.desktop` |
| Desktop shortcut | `~/.local/share/applications/trcc.desktop` |
| Udev rules (root) | `/etc/udev/rules.d/99-trcc-lcd.rules` |
| USB quirks (root) | `/etc/modprobe.d/trcc-lcd.conf` |

To fully uninstall the Python package as well:

```bash
trcc uninstall
sudo PYTHONPATH=src python3 -m trcc.cli uninstall
pip uninstall trcc-linux
```

---

## Troubleshooting

### `trcc: command not found`

pip installs to `~/.local/bin/` which may not be on your PATH. Either:

- **Open a new terminal** (Fedora/Ubuntu add `~/.local/bin` to PATH on shell startup if the directory exists)
- Run directly: `PYTHONPATH=src python3 -m trcc.cli gui`
- Add to PATH permanently: `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc`

### `sudo trcc: command not found` / `No module named 'trcc'` with sudo

This was fixed in v1.2.0 — `trcc setup-udev` now automatically re-invokes itself with sudo and the correct PYTHONPATH. Just run:

```bash
trcc setup-udev
```

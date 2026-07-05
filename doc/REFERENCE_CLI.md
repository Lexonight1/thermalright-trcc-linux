# CLI Reference

Complete reference for the `trcc` command-line interface.

## Usage

```bash
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
trcc gui --resume     # autostart: start hidden in tray, restore last theme
trcc gui -vv          # debug logging
```

| Option | Description |
|--------|-------------|
| `--decorated`, `-d` | Show window with titlebar (can minimize/resize) |
| `--resume` | Start hidden in system tray and restore the last-used theme (for autostart) |

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

```text
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
trcc send image.png --preview       # show ANSI preview in terminal
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (default: auto-detect) |
| `--preview`, `-p` | Show ANSI art preview in terminal (for headless/SSH) |

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
| `--preview`, `-p` | Show ANSI art preview in terminal |

---

### `trcc test`

Test the display with a color cycle (red, green, blue, yellow, magenta, cyan, white).

```bash
trcc test
trcc test --loop       # cycle continuously until Ctrl+C
trcc test --preview    # show ANSI preview for each color
```

| Option | Description |
|--------|-------------|
| `--loop`, `-l` | Loop colors continuously (Ctrl+C to stop) |
| `--device`, `-d` | Device path (default: auto-detect) |
| `--preview`, `-p` | Show ANSI art preview in terminal |

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
| `--preview`, `-p` | Show ANSI art preview in terminal |

---

### `trcc info`

Show live system metrics (CPU, GPU, memory, date/time).

```bash
trcc info
```

**Example output:**

```text
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

### `trcc system setup`

Install udev rules and USB storage quirks (required once after first install).

```bash
# Install (auto-prompts for sudo)
trcc system setup

# Non-interactive (assume yes to prompts)
trcc system setup --yes
```

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Non-interactive — assume yes to prompts |

**What this does:**

1. Creates `/etc/udev/rules.d/99-trcc-lcd.rules` — grants your user permission to access the LCD, plus read access to the CPU package-power (RAPL) counter so CPU wattage works without root
2. Creates `/etc/modprobe.d/trcc-lcd.conf` — USB quirk that forces bulk-only transport (required for device detection)
3. Creates `/etc/modules-load.d/trcc-rapl.conf` and loads `intel_rapl_msr` — exposes the CPU power counter (the same module on AMD and Intel)
4. Reloads udev rules

After running, **unplug and replug the USB cable** (or reboot).

---

### `trcc install-desktop`

Install the application menu entry (`.desktop` file) and icon so TRCC appears in your app launcher.

```bash
trcc install-desktop
```

Creates:
- `~/.local/share/applications/trcc.desktop`
- Copies the TRCC icon to the appropriate location

---

### `trcc resume`

Send the last-used theme to each detected device, then exit. Designed for headless use (autostart, cron, scripts).

```bash
trcc resume
```

This is what `~/.config/autostart/trcc.desktop` calls on login with `trcc gui --resume` (which launches the GUI minimized to tray and sends the last theme).

---

### `trcc report`

Generate a full diagnostic report for bug reports. Collects everything needed to diagnose device issues in one command — users can copy-paste the entire output into a GitHub issue.

```bash
trcc report
```

**What it collects:**

- TRCC version, Python version, OS/kernel info
- `lsusb` output (all USB devices)
- `trcc detect --all` (detected TRCC devices with protocol info)
- HID handshake results (PM byte, resolution, serial)
- Udev rules status
- CPU package-power (RAPL) status — whether the counter exists and is readable (diagnoses blank CPU wattage)
- USB descriptor details for relevant devices

---

### `trcc system hid-debug`

HID handshake diagnostic — prints hex dump and resolved device info for bug reports.

```bash
trcc system hid-debug
trcc system hid-debug --test-frame   # send red test frame after handshake
```

| Option | Description |
|--------|-------------|
| `--test-frame`, `-t` | Send a solid red test frame after handshake |

**Example output:**

```text
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

### `trcc system led-debug`

Diagnose an LED device — performs handshake and reports PM byte, style, and segment count.

```bash
trcc system led-debug             # handshake only
trcc system led-debug --test      # handshake + send test colors
```

| Option | Description |
|--------|-------------|
| `--test` | Send test colors to the device after handshake |

---

### `trcc perf`

Run CPU + memory performance benchmarks. Measures rendering, encoding, and compositing pipeline times.

```bash
trcc perf                  # software benchmarks only
trcc perf --device         # include hardware USB I/O benchmarks
```

| Option | Description |
|--------|-------------|
| `--device` | Include hardware device send benchmarks (requires connected LCD) |

Software benchmarks run without a device. Hardware benchmarks pause any running GUI daemon, take exclusive device access, then resume.

---

### `trcc setup-winusb`

Guide users through WinUSB driver installation for bulk USB devices on Windows.

```bash
trcc setup-winusb
```

Detects devices needing WinUSB (bulk protocol devices like GrandVision, Stream Vision, Wonder Vision) and provides step-by-step Zadig instructions. SCSI devices use the default Windows USB Mass Storage driver and don't need this.

> **Windows only.** On Linux/macOS this command exits with a message that WinUSB is not needed.

---

### `trcc setup`

Interactive setup wizard — checks system dependencies, GPU packages, udev rules, and desktop integration. Offers to install anything missing.

```bash
trcc setup             # interactive (prompts for each missing dep)
trcc setup --yes       # auto-accept all (non-interactive)
```

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Accept all defaults without prompting |

**Steps:**
1. System dependencies (Python modules + binaries)
2. GPU detection (NVIDIA/AMD/Intel) and optional sensor packages
3. USB device permissions (udev rules)
4. Desktop integration (app menu entry)

---

### `trcc setup-gui`

Launch the setup wizard as a PySide6 GUI. Shows the same checks as `trcc setup` with Install buttons and a terminal output pane.

```bash
trcc setup-gui
```

If trcc-linux is not installed, shows a dialog offering to install it via pip. After installing, Re-check reveals the full system checks.

---

### `trcc setup-polkit`

Install a polkit policy for passwordless `dmidecode` and `smartctl` access. Avoids repeated sudo prompts when the GUI reads motherboard/disk info.

```bash
trcc setup-polkit
```

**What this does:**

1. Installs an XML polkit policy (`com.trcc.pkexec.policy`) allowing your user to run `dmidecode` and `smartctl` without a password
2. On XFCE (where `allow_active=yes` doesn't work), installs a JavaScript `.rules` file scoped to your user
3. Runs `restorecon` on SELinux systems to fix file contexts

After running, hardware info panels in the GUI will populate without sudo prompts.

---

### `trcc setup-selinux`

Install a SELinux policy module that allows USB device access on SELinux-enforcing systems (Bazzite, Silverblue, Fedora Atomic).

```bash
trcc setup-selinux
```

**What this does:**

1. Checks if SELinux is enforcing (`getenforce`)
2. Compiles and installs the `trcc_usb` policy module that allows `unconfined_t` to access USB device files
3. Auto-elevates with sudo if not root

After running, **unplug and replug the USB cable**.

**Prerequisites:** `checkmodule` and `semodule_package` must be installed:
```bash
# Fedora / Bazzite
sudo dnf install checkpolicy

# Debian / Ubuntu
sudo apt install checkpolicy semodule-utils

# Arch
sudo pacman -S checkpolicy semodule-utils
```

> **When is this needed?** Only on SELinux-enforcing systems. Run `getenforce` — if it says "Enforcing" and bulk devices fail with EBUSY, you need this. Non-SELinux distros can skip it entirely.

---

### `trcc doctor`

Check dependencies, libraries, and permissions. Useful for diagnosing installation issues.

```bash
trcc doctor
```

Reports status of: Python version, PySide6, PIL/Pillow, numpy, pyusb, sg_raw, udev rules, and device access permissions.

---

### `trcc shell`

Interactive TRCC shell with tab completion. Type commands without the `trcc` prefix.

```bash
trcc shell
```

Inside the shell:
```text
trcc> detect
  [1] 0402:3922  Frozen Warframe  (SCSI)  path=/dev/sg0
trcc> brightness 80
  Brightness: 80%
trcc> theme-list
  Theme1  Theme2  Theme3
trcc> exit
```

Features:
- Tab completion for all commands
- Command history (saved to `~/.trcc/data/shell_history`)
- No need to type `trcc` before each command

---

### `trcc serve`

Start the REST API server for remote LCD/LED control.

```bash
trcc serve                              # localhost:9876
trcc serve --host 0.0.0.0 --port 3000  # LAN on custom port
trcc serve --token mysecret             # require auth token
trcc serve --tls                        # HTTPS with auto-generated self-signed cert
trcc serve --cert cert.pem --key key.pem  # custom TLS certificate
```

| Option | Description |
|--------|-------------|
| `--host` | Bind address (default: `127.0.0.1`, use `0.0.0.0` for LAN) |
| `--port` | Listen port (default: `9876`) |
| `--token` | API bearer token for authentication |
| `--tls` | Enable HTTPS with auto-generated self-signed certificate |
| `--cert` | Path to custom TLS certificate (PEM) |
| `--key` | Path to custom TLS private key (PEM) |

**QR Code:** When the `qrcode` package is installed (`pip install qrcode`), a terminal QR code is printed at startup containing connection details (host, port, token, TLS) as compact JSON. Scan with the TRCC Remote app to connect instantly. When bound to `0.0.0.0` or `::`, the QR code auto-detects your LAN IP.

---

### `trcc api`

List all REST API endpoints with method, path, and description.

```bash
trcc api
```

---

### `trcc lang`

Show the current application language.

```bash
trcc lang
```

**Example output:**
```text
en (English)
```

---

### `trcc lang-set`

Set the application language by ISO 639-1 code.

```bash
trcc lang-set de        # German
trcc lang-set ja        # Japanese
trcc lang-set zh        # Chinese
```

Persists to config. Affects GUI labels and localized assets.

---

### `trcc lang-list`

List all available languages with ISO codes and native names.

```bash
trcc lang-list
```

**Example output:**
```text
Available languages (38):
  de     Deutsch
  en     English
  es     Español
  fr     Français
  ja     日本語
  ko     한국어
  ...
```

---

### `trcc brightness`

Set display brightness level.

```bash
trcc brightness 1       # 25%
trcc brightness 2       # 50%
trcc brightness 3       # 100%
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (default: auto-detect) |

Persists to per-device config.

---

### `trcc rotation`

Set display rotation.

```bash
trcc rotation 0         # no rotation
trcc rotation 90        # 90° clockwise
trcc rotation 180       # 180°
trcc rotation 270       # 270° clockwise
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (default: auto-detect) |

Persists to per-device config.

---

### `trcc video`

Play a video, GIF, or ZT file on the LCD. No overlay support — for overlays, use `trcc theme`.

```bash
trcc video clip.mp4
trcc video animation.gif --no-loop
trcc video clip.mp4 --duration 30
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (default: auto-detect) |
| `--no-loop` | Play once instead of looping |
| `--duration` | Stop after N seconds (default: 0 = forever) |
| `--preview`, `-p` | Animate ANSI preview in terminal |

---

### `trcc theme`

Play a background (image, GIF, or video) with optional mask overlay and live system metrics. Same flags as `trcc theme-save` — use `--save` to persist as a reusable theme.

```bash
# Static image with metrics overlay
trcc theme --background wallpaper.png \
  --metric "cpu_temp:10,20" \
  --metric "gpu_temp:10,50"

# Animated GIF with mask + metrics
trcc theme --background animated.gif \
  --mask ~/.trcc/data/web/zt320320/001a \
  --metric "time:50,30:ffffff:24" \
  --metric "cpu_temp:30,60:ff0000:14"

# Custom font, color, and size
trcc theme --background animated.gif \
  --metric "gpu_usage:10,20" \
  --font Arial --font-style bold --font-size 18 --color 00ff00

# Play + save in one shot
trcc theme --background animated.gif \
  --mask ~/.trcc/data/web/zt320320/001a \
  --metric "time:50,30:ffffff:24" \
  --save MyTheme
```

| Option | Description |
|--------|-------------|
| `--background`, `-b` | Background image/video/GIF (required) |
| `--metric`, `-m` | Overlay metric: `key:x,y[:color[:size]]` (repeatable) |
| `--mask` | Mask PNG file or directory (auto-resized to LCD) |
| `--font` | Font family name (default: Microsoft YaHei) |
| `--font-style` | Font style: `regular` or `bold` (default: regular) |
| `--font-size` | Font size in pixels (default: 14) |
| `--color`, `-c` | Hex color for overlay text (default: ffffff) |
| `--temp-unit` | Temperature unit: 0=Celsius, 1=Fahrenheit |
| `--time-format` | Time format: 0=24h HH:MM, 1=12h hh:MM |
| `--date-format` | Date format: 0=yyyy/MM/dd, 2=dd/MM/yyyy |
| `--save`, `-s` | Save as named theme (e.g. `--save MyTheme`) |
| `--device`, `-d` | Device path (default: auto-detect) |
| `--no-loop` | Play once instead of looping |
| `--duration` | Stop after N seconds (default: 0 = forever) |
| `--preview`, `-p` | Animate ANSI preview in terminal |

**Metric spec format:** `key:x,y[:color[:size[:font[:style]]]]`

- `gpu_temp:10,20` — uses global defaults
- `cpu_temp:10,50:ff0000` — red, global font size
- `time:150,10:ffffff:24` — white, 24px
- `gpu_temp:10,20:ff0000:18:Arial:bold` — red, 18px, Arial bold
- `cpu_temp:10,50::16:Courier` — default color, 16px, Courier

Per-metric values override the globals (`--color`, `--font-size`, `--font`, `--font-style`). Empty fields (double colon `::`) fall through to the global default.

**Available metric keys:**

| Category | Keys |
|----------|------|
| CPU | `cpu_temp`, `cpu_percent`, `cpu_freq`, `cpu_power` |
| GPU | `gpu_temp`, `gpu_usage`, `gpu_clock`, `gpu_power` |
| Memory | `mem_percent`, `mem_clock`, `mem_available`, `mem_temp` |
| Disk | `disk_read`, `disk_write`, `disk_activity`, `disk_temp` |
| Network | `net_down`, `net_up`, `net_total_down`, `net_total_up` |
| Fan | `fan_cpu`, `fan_gpu`, `fan_ssd`, `fan_sys2` |
| Time | `time`, `date`, `weekday` |

---

### `trcc theme-save` *(deprecated)*

Alias for `trcc theme --save NAME`. Use `trcc theme --save` instead.

```bash
# These are equivalent:
trcc theme-save MyTheme --background animated.gif --metric "cpu_temp:10,20"
trcc theme --save MyTheme --background animated.gif --metric "cpu_temp:10,20"
```

---

### `trcc screencast`

Stream a screen region to the LCD.

```bash
trcc screencast                           # full screen
trcc screencast --x 100 --y 100 --w 320 --h 320
trcc screencast --fps 15
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (default: auto-detect) |
| `--x`, `--y` | Top-left corner of capture region |
| `--w`, `--h` | Width/height of capture region (0 = full screen) |
| `--fps` | Frames per second (default: 10) |
| `--preview`, `-p` | Animate ANSI preview in terminal |

---

### `trcc mask`

Load a mask overlay and send to LCD.

```bash
trcc mask /path/to/mask.png
trcc mask /path/to/theme/dir
trcc mask --clear                 # remove mask (send solid black)
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (default: auto-detect) |
| `--clear` | Clear mask (send solid black) |
| `--preview`, `-p` | Show ANSI art preview in terminal |

---

### `trcc overlay`

Render overlay from a DC config file.

```bash
trcc overlay /path/to/config1.dc
trcc overlay /path/to/theme/dir --send
trcc overlay /path/to/theme/dir --output rendered.png
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (default: auto-detect) |
| `--send`, `-s` | Send rendered result to LCD |
| `--output`, `-o` | Save rendered image to file |
| `--preview`, `-p` | Show ANSI art preview in terminal |

---

### `trcc theme-list`

List available themes for the current device resolution.

```bash
trcc theme-list                   # local themes
trcc theme-list --cloud           # cloud themes
trcc theme-list --cloud --category a   # gallery category only
```

| Option | Description |
|--------|-------------|
| `--cloud`, `-c` | List cloud themes instead of local |
| `--category` | Filter by category (a=Gallery, b=Tech, c=HUD, d=Light, e=Nature, y=Aesthetic) |

Scans both `~/.trcc/data/` (stock themes) and `~/.trcc-user/` (user-created themes).

---

### `trcc mask-list`

List available mask overlays for the current device resolution.

```bash
trcc mask-list
```

Scans both cloud masks (`~/.trcc/data/web/zt{W}{H}/`) and user masks (`~/.trcc-user/data/web/zt{W}{H}/`). Custom masks are tagged `[custom]`.

---

### `trcc theme-load`

Load a theme by name and send to LCD.

```bash
trcc theme-load 003a              # exact name
trcc theme-load "Custom_MyTheme"  # custom theme
trcc theme-load warframe          # partial match
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (default: auto-detect) |
| `--preview`, `-p` | Live ANSI preview in terminal (updates with metrics) |

Handles both static and animated themes. Loads overlay config (DC/config.json), wires metrics, applies mask. Static themes enter a keep-alive loop; animated themes play video with overlay. `-p` shows live-updating ANSI preview for both.

---

### `trcc theme-save`

Save current display state as a custom theme.

```bash
trcc theme-save MyTheme
trcc theme-save AnimTheme --video /path/to/clip.mp4
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (default: auto-detect) |
| `--video`, `-v` | Video path for animated theme |

Saves to `~/.trcc/data/theme{W}{H}/Custom_{name}/`.

---

### `trcc theme-export`

Export a theme as a `.tr` file.

```bash
trcc theme-export 003a /tmp/mytheme.tr
trcc theme-export Custom_MyTheme ~/backup.tr
```

---

### `trcc theme-import`

Import a theme from a `.tr` file.

```bash
trcc theme-import /tmp/mytheme.tr
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (determines resolution for import target) |

---

### `trcc led-color`

Set LED static color.

```bash
trcc led-color ff0000      # red
trcc led-color 00ff00      # green
```

| Option | Description |
|--------|-------------|
| `--preview`, `-p` | Show LED zone colors as ANSI blocks in terminal |

---

### `trcc led-mode`

Set LED effect mode.

```bash
trcc led-mode static       # solid color
trcc led-mode breathing    # fade in/out (Ctrl+C to stop)
trcc led-mode colorful     # cycle colors (Ctrl+C to stop)
trcc led-mode rainbow      # rotating hue (Ctrl+C to stop)
```

| Option | Description |
|--------|-------------|
| `--preview`, `-p` | Show LED zone colors as ANSI blocks in terminal (animates for breathing/colorful/rainbow) |

Animated modes run until Ctrl+C.

---

### `trcc led-brightness`

Set LED brightness.

```bash
trcc led-brightness 50     # 50%
trcc led-brightness 100    # full
```

| Option | Description |
|--------|-------------|
| `--preview`, `-p` | Show LED zone colors as ANSI blocks in terminal |

Range: 0-100.

---

### `trcc led-off`

Turn LEDs off.

```bash
trcc led-off
```

---

### `trcc led-sensor`

Set sensor source for temperature/load linked LED modes.

```bash
trcc led-sensor cpu        # CPU temp/load drives LED color
trcc led-sensor gpu        # GPU temp/load drives LED color
```

---

### `trcc led-zone-color`

Set color for a specific LED zone.

```bash
trcc led-zone-color 0 ff0000      # zone 0 = red
trcc led-zone-color 1 00ff00      # zone 1 = green
```

| Option | Description |
|--------|-------------|
| `--preview`, `-p` | Show ANSI terminal preview |

Zone indices are 0-based.

---

### `trcc led-zone-mode`

Set effect mode for a specific LED zone.

```bash
trcc led-zone-mode 0 static       # zone 0 = solid color
trcc led-zone-mode 1 breathing    # zone 1 = fade in/out
trcc led-zone-mode 2 colorful     # zone 2 = cycle colors
trcc led-zone-mode 3 rainbow      # zone 3 = rotating hue
```

| Option | Description |
|--------|-------------|
| `--preview`, `-p` | Show ANSI terminal preview |

---

### `trcc led-zone-brightness`

Set brightness for a specific LED zone.

```bash
trcc led-zone-brightness 0 50     # zone 0 at 50%
trcc led-zone-brightness 1 100    # zone 1 at full
```

| Option | Description |
|--------|-------------|
| `--preview`, `-p` | Show ANSI terminal preview |

Range: 0-100.

---

### `trcc led-zone-toggle`

Toggle a specific LED zone on or off.

```bash
trcc led-zone-toggle 0 true       # turn zone 0 on
trcc led-zone-toggle 1 false      # turn zone 1 off
```

---

### `trcc led-zone-sync`

Enable or disable LED zone sync (circulate/select-all mode).

```bash
trcc led-zone-sync true            # enable zone sync
trcc led-zone-sync false           # disable zone sync
trcc led-zone-sync true --interval 2  # sync every 2 seconds
```

| Option | Description |
|--------|-------------|
| `--interval`, `-i` | Sync interval in seconds |

---

### `trcc led-segment`

Toggle a specific LED segment on or off.

```bash
trcc led-segment 0 true           # turn segment 0 on
trcc led-segment 3 false          # turn segment 3 off
```

Segment indices are 0-based.

---

### `trcc led-clock`

Set LED segment display clock format.

```bash
trcc led-clock true                # 24-hour format
trcc led-clock false               # 12-hour format
```

---

### `trcc led-temp-unit`

Set LED segment display temperature unit.

```bash
trcc led-temp-unit C               # Celsius
trcc led-temp-unit F               # Fahrenheit
```

---

### `trcc split`

Set split mode (Dynamic Island) for widescreen displays.

```bash
trcc split 0                       # off
trcc split 1                       # Dynamic Island style 1
trcc split 2                       # Dynamic Island style 2
trcc split 3                       # Dynamic Island style 3
```

| Option | Description |
|--------|-------------|
| `--device`, `-d` | Device path (default: auto-detect) |

Only applies to non-square widescreen LCDs.

---

### `trcc test-led`

Test LED ANSI preview with real system metrics. No device needed.

```bash
trcc test-led                      # cycle all modes
trcc test-led static               # test static mode only
trcc test-led breathing --duration 10
trcc test-led --segments 30        # simulate 30-LED device
```

| Option | Description |
|--------|-------------|
| `--segments`, `-s` | Number of LED segments to simulate (default: 64) |
| `--duration`, `-t` | Animation duration in seconds (default: auto) |

---

### `trcc test-lcd`

Test LCD ANSI preview with real system metrics. No device needed.

```bash
trcc test-lcd
trcc test-lcd --cols 80            # wider terminal output
```

| Option | Description |
|--------|-------------|
| `--cols`, `-c` | Terminal width in columns (default: 60) |

---

### `trcc download`

Download theme packs for all supported LCD resolutions.

```bash
trcc download                        # list available packs
trcc download --list                 # same as above
trcc download themes-320x320        # download 320x320 theme pack
trcc download themes-320             # shorthand for 320x320 (square)
trcc download themes-240x320        # non-square resolution
trcc download themes-320x320 --force # re-download even if exists
trcc download themes-320x320 --info  # show pack details
```

Pack names follow the format `themes-{W}x{H}`. Square resolutions have a shorthand alias (e.g., `themes-320` → `themes-320x320`).

| Option | Description |
|--------|-------------|
| `--list`, `-l` | List available theme packs |
| `--force`, `-f` | Force re-download |
| `--info`, `-i` | Show pack info without downloading |

---

### `trcc uninstall`

Remove all TRCC configuration, udev rules, autostart files, and the pip package.

```bash
trcc uninstall             # interactive (prompts before pip uninstall)
trcc uninstall --yes       # skip all prompts (for scripts / GUI)
```

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Skip confirmation prompts (non-interactive) |

**Removes:**

| Item | Path |
|------|------|
| Config + data directory | `~/.trcc/` |
| Autostart entry | `~/.config/autostart/trcc*.desktop` |
| Desktop shortcut | `~/.local/share/applications/trcc*.desktop` |
| Udev rules (root) | `/etc/udev/rules.d/99-trcc-lcd.rules` |
| USB quirks (root) | `/etc/modprobe.d/trcc-lcd.conf` |
| pip package | `trcc-linux` |

Auto-elevates with sudo for root files. The `--yes` flag is used by `trcc setup-gui` for non-interactive uninstall via pkexec.

---

## Troubleshooting

### `trcc: command not found`

pip installs to `~/.local/bin/` which may not be on your PATH. Either:

- **Open a new terminal** (Fedora/Ubuntu add `~/.local/bin` to PATH on shell startup if the directory exists)
- Run directly: `PYTHONPATH=src python3 -m trcc.cli gui`
- Add to PATH permanently: `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc`

### `sudo trcc: command not found` / `No module named 'trcc'` with sudo

This was fixed in v1.2.0 — `trcc system setup` now automatically re-invokes itself with sudo and the correct PYTHONPATH. Just run:

```bash
trcc system setup
```

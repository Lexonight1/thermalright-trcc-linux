# Troubleshooting

Quick reference for common TRCC Linux issues. For full installation instructions, see the [Install Guide](GUIDE_INSTALL.md).

---

## Table of Contents

1. [Command Not Found](#trcc-command-not-found)
2. [No Device Detected](#no-device-detected)
   - [A device answered, but not as a Thermalright panel](#a-device-answered-but-not-as-a-thermalright-panel)
3. [Permission Denied](#permission-denied-when-accessing-the-device)
4. [SELinux / Immutable Distros](#selinux--immutable-distros)
5. [PySide6 Issues](#pyside6-issues)
6. [HID Device Issues](#hid-device-issues)
7. [Sensors & GPU metrics](#sensors--gpu-metrics)
8. [Display Issues](#display-issues)
9. [Video / Media Issues](#video--media-issues)
10. [NixOS-Specific](#nixos-specific)

---

## `trcc: command not found`

**Cause:** `pip install` puts the `trcc` script in `~/.local/bin/`, which isn't in your shell's `PATH` on many distros.

**Fix:** Open a **new terminal**, or add `~/.local/bin` to your PATH permanently:

```bash
# Bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc

# Zsh (Arch, Garuda, some Manjaro)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc

# Fish
fish_add_path ~/.local/bin
```

| Distro | `~/.local/bin` in PATH by default? |
|--------|-----------------------------------|
| Fedora | Yes |
| Ubuntu / Debian | Conditionally (only if dir exists at login) |
| Arch / Manjaro / EndeavourOS | No |
| openSUSE | No |
| Void / Alpine | No |

---

## Old pip/pipx install shadows the system package

**Cause:** You previously installed TRCC via pip or pipx, then switched to a native package (`.deb`, `.rpm`, `.pkg.tar.zst`). The old `~/.local/bin/trcc` takes priority over `/usr/bin/trcc`, so you're running the old version — or it fails because the old venv is broken.

**Symptoms:** Wrong version, missing modules, or strange errors after upgrading to a native package.

**Fix:**

```bash
# Remove the old pip/pipx install
pipx uninstall trcc-linux 2>/dev/null
rm -f ~/.local/bin/trcc

# Verify the system package is being used
which trcc    # should show /usr/bin/trcc
trcc --version
```

---

## No device detected

**Symptom:** `trcc detect` prints "No devices connected." (or `trcc display resume` prints "No compatible TRCC device detected.").

**Cause (SCSI panels — 0402:3922, 87cd:70db):** The LCD isn't showing up as a SCSI device.

**Fix:**
1. Make sure the USB cable is plugged in (both ends)
2. Run udev setup: `trcc system setup`
3. **Unplug and replug the USB cable** (or reboot)
4. Check if the device appears: `ls /dev/sg*`
5. Check kernel messages: `dmesg | tail -20` right after plugging in

> **HID, LY and Bulk devices** (0416:5302, 0416:5406, 0418:5303, 0418:5304, 0416:8001, 0416:5408, 0416:5409, 87ad:70db) don't use `/dev/sg*`. Use `trcc detect` instead.

### A device answered, but not as a Thermalright panel

**Symptom:** the connect fails with "`KEY` answered, but not as a Thermalright panel does".

**Cause:** TRCC recognises a cooler by its USB id alone, and some of those ids belong to chip makers whose ids other products use too. The device replied, but not the way any Thermalright panel TRCC knows does. Either it is your cooler on a firmware TRCC has not met yet, or it is another product that shares the id.

**Fix:**
- **It is your cooler:** run `trcc report` and [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues) with it. The report includes the start of the device's reply, which is where support for a new firmware begins.
- **It is another product:** `trcc device disconnect KEY` makes TRCC leave it alone, across restarts and replugs. TRCC gives the device back its system driver as it lets go, so the product works normally again. To undo it: `trcc device connect KEY`.

---

## Permission denied when accessing the device

**Cause:** Udev rules not set up, or stale rules from an older version.

**Fix:**
```bash
# Upgrade to latest version first
pip install --upgrade trcc-linux

# Re-generate udev rules
trcc system setup

# Unplug/replug USB cable, or reboot
```

### SELinux / Immutable Distros

**Affects:** Bazzite, Fedora Silverblue, Fedora Kinoite, Aurora, Bluefin, and any SELinux-enforcing distro.

Versions before v1.2.16 used `TAG+="uaccess"` in udev rules, which relies on systemd-logind ACLs. **SELinux blocks these ACLs**, so the device stays root-only even after `trcc system setup`.

**Symptoms:**
- `ls -la /dev/sgX` shows `crw-rw----. 1 root root` (no ACL `+` marker)
- `getenforce` returns `Enforcing`
- Device works with `sudo` but not as regular user

**Fix:** Upgrade to v1.2.16+ which uses `MODE="0666"`:
```bash
pip install --upgrade trcc-linux
trcc system setup    # as your own user — it asks for sudo itself
# Unplug/replug USB cable
```

**Verify:** After replug, check permissions:
```bash
ls -la /dev/sg*
# Should show: crw-rw-rw- (world-readable/writable)
```

### Bulk Device EBUSY on SELinux

**Affects:** Bulk USB devices (87AD:70DB — GrandVision 360, Mjolnir Vision, Wonder Vision Pro 360) on SELinux-enforcing distros (Bazzite, Silverblue, Fedora Atomic).

**Symptoms:**
- `[Errno 16] Resource busy` in handshake/frame send
- `detach_kernel_driver()` silently blocked by SELinux
- Device works on non-SELinux distros but fails on Bazzite

**Fix (v4.2.0+):**
```bash
pip install --upgrade trcc-linux
trcc system setup    # installs SELinux policy module (auto-elevates with sudo)
# Unplug/replug USB cable
trcc report           # verify handshake succeeds
```

If `checkmodule` is not found:
```bash
sudo dnf install checkpolicy    # Fedora/Bazzite
```

---

## PySide6 Issues

### PySide6 is missing

**Symptom:** `trcc gui` stops with a Python import error naming `PySide6`. (PySide6 is a declared dependency, so this means the install skipped it.)

**Fix:** Install for your distro:
```bash
# Fedora
sudo dnf install python3-pyside6

# Ubuntu / Debian
sudo apt install python3-pyside6

# Arch
sudo pacman -S python-pyside6

# Or via pip as fallback
pip install PySide6
```

### Segmentation fault (core dumped)

**Cause:** Most commonly, a missing Qt6 xcb dependency. On Ubuntu/Mint, `libxcb-cursor0` is not installed by default but Qt6 requires it — without it, Qt segfaults silently on startup.

**Fix 1 — Install missing library** (try this first):
```bash
sudo apt install libxcb-cursor0
```

**Fix 2 — Use a virtual environment** (if Fix 1 doesn't help):
```bash
python3 -m venv ~/.local/share/trcc-venv
~/.local/share/trcc-venv/bin/pip install trcc-linux
~/.local/share/trcc-venv/bin/trcc gui
```

**Diagnostic:** If neither fix works:
```bash
# Show Qt plugin loading errors
QT_DEBUG_PLUGINS=1 trcc gui 2>&1 | head -30
```

---

## HID Device Issues

### HID device not listed by `trcc detect`

1. Verify USB connection: `lsusb | grep -i "0416\|0418\|87"`
2. Run `trcc system setup` and unplug/replug
3. Check if another process holds the USB device (VM, Windows TRCC in dual-boot)

### Device detected but the handshake fails

The device was detected but didn't respond to the handshake query — `trcc device issues` lists every device that failed to connect, and why. Common causes:

1. **Old version** — v1.2.9 rewrote the HID handshake with retry logic, 5s timeout, and endpoint auto-detect. Upgrade first:
   ```bash
   pip install --upgrade trcc-linux
   ```
2. **Firmware consumed the handshake** — some devices only respond once per USB power cycle. Unplug the USB header, wait 5 seconds, replug, then immediately run `trcc system hid-debug KEY` (your key from `trcc detect`, e.g. `0416:5302`)
3. **Unknown device** — if the device model isn't in our mapping table, [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues) with the `trcc system hid-debug KEY` output

### No USB backend (pyusb `NoBackendError`)

**Cause:** pyusb (installed with TRCC) cannot find the system libusb library.

**Fix:** Install libusb:
```bash
sudo apt install libusb-1.0-0-dev    # Debian/Ubuntu
sudo dnf install libusb1-devel       # Fedora
sudo pacman -S libusb                # Arch
```

### HID "Permission denied"

See [Permission denied](#permission-denied-when-accessing-the-device) above — same fix applies. Make sure you're on v1.2.16+ for SELinux compatibility.

### GUI shows empty themes with HID device

Themes are resolution-specific. If the handshake failed, the app doesn't know your screen resolution, so it can't load the right theme pack. Fix the handshake first — themes will populate automatically once the resolution is detected.

---

## Sensors & GPU metrics

### GPU readings are empty / "No NVIDIA GPU detected"

NVIDIA GPU metrics use the NVML python bindings (`nvidia-ml-py`, imported as `pynvml`). **As of v9.8.3 this is a core dependency** — every install channel (pip, pipx, the distro packages, the frozen builds) ships it, so GPU metrics work out of the box with no separate install and no prompt to accept. It's pure-Python and does nothing on machines without an NVIDIA card.

If GPU readings are still empty on a current version, it's almost always the NVIDIA **driver**, not the reader: check `nvidia-smi` runs, and if it reports a version mismatch, reboot (or reload the driver module). Only if you're on an **older install that predates v9.8.3** — or an environment that deliberately stripped the dependency — would you need to add the reader into trcc's own interpreter:

```bash
# pip / venv install — install into the running interpreter
pip install nvidia-ml-py
```

Then **restart trcc** — `pynvml` is imported once at startup, so a freshly installed reader only takes effect on the next launch.

AMD and Intel GPU temperatures come from `hwmon` and need no extra package.

### CPU power shows blank / `--`

CPU package power is read from the kernel's RAPL energy counter, which is **root-only on most distros** (a side-channel mitigation), so the reading comes back empty. Grant read access (and load the counter's kernel module) with:

```bash
trcc system setup
```

This works the same on AMD (Zen) and Intel — the counter lives at the vendor-agnostic `intel-rapl` node either way. To see what's going on, `trcc report` now includes a **CPU power (RAPL)** section showing whether the counter exists and is readable.

---

## Display Issues

### LCD stays blank or shows old image

```bash
trcc device reset 0402:3922    # your key from `trcc detect`
```

### GUI looks wrong / elements overlapping

**Cause:** HiDPI scaling interfering with the fixed-size layout.

```bash
QT_AUTO_SCREEN_SCALE_FACTOR=0 trcc gui
```

### Colors are wrong (red/blue swapped)

**Cause:** RGB565 byte order mismatch. Different protocols and resolutions use different byte orders (big-endian vs little-endian). Fixed in v5.0.8 for HID Type 2 devices. Upgrade:

```bash
pip install --upgrade trcc-linux
```

If the issue persists after upgrading, do a clean reinstall (there is no
`trcc uninstall` — remove the pieces directly):
```bash
pip uninstall trcc-linux
rm -rf ~/.trcc                 # keeps ~/.trcc-user (your own themes)
pip install trcc-linux
trcc system setup
```

If colors are still wrong, [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues) with `trcc report` output.

### Device detected but nothing displays / sg_raw errors

**Cause:** UAS (USB Attached SCSI) kernel driver interferes with LCD devices.

**Fix:** Verify the USB quirk file exists:
```bash
cat /etc/modprobe.d/trcc-lcd.conf
```

If missing, recreate:
```bash
trcc system setup
# Unplug/replug or reboot
```

If the problem persists, manually blacklist UAS:
```bash
echo "options usb-storage quirks=0402:3922:u,87cd:70db:u" | sudo tee /etc/modprobe.d/trcc-lcd.conf
sudo dracut --force       # Fedora/RHEL
# or
sudo update-initramfs -u  # Debian/Ubuntu
```

### Headless / server hosts (Proxmox, etc.) — panel dark despite a "successful" send

**Symptom:** on a headless server — especially a **Proxmox** host (`root@pve`) — a
command like `trcc display color KEY ff0000` reports success (`Sent … bytes`, no
errors), but the panel stays dark.

**Cause:** two things combine. Servers are power-tuned and enable aggressive
**USB autosuspend**, and TRCC panels are designed to *sleep when the USB link
suspends* (that's the intended "screen off on shutdown" behaviour). A solid
colour is a **single frame**: once it is written nothing more crosses the link,
so the panel can suspend right after. The frame was delivered to a panel that's
now asleep → successful send, dark screen. (This is a *power* issue, not a bug
in the send path.)

**Fixes, quickest first:**

1. **Keep a persistent send running instead of a one-shot frame.**
   `trcc display play KEY` renders and sends every tick until Ctrl-C (it keeps
   SCSI panels from timing out); `trcc display keepalive KEY` resends the last
   frame. If the panel stays lit with one of these running but not after
   `trcc display color`, autosuspend is confirmed.
2. **Check the device's power state** (look for `suspended`):
   ```bash
   for d in /sys/bus/usb/devices/*; do \
     printf '%s %s:%s ctrl=%s %s\n' "$d" \
       "$(cat $d/idVendor 2>/dev/null)" "$(cat $d/idProduct 2>/dev/null)" \
       "$(cat $d/power/control 2>/dev/null)" "$(cat $d/power/runtime_status 2>/dev/null)"; \
   done | grep -i "0416\|0402\|0418\|87"
   ```
3. **Disable USB autosuspend** system-wide and retest — add
   `usbcore.autosuspend=-1` inside the quotes on `GRUB_CMDLINE_LINUX_DEFAULT`
   in `/etc/default/grub`, then `sudo update-grub && sudo reboot`.
4. **Confirm the udev rules are installed** — `trcc system setup` lays down the
   power-management rules for the panel; without them the device runs on the
   host's (more aggressive) defaults. A "run `trcc system setup`" hint in the
   log means they're missing.

---

## Video / Media Issues

### Video/GIF playback not working

**Cause:** FFmpeg not installed.

```bash
# Fedora / Nobara (stock Fedora's package is ffmpeg-free; with RPM Fusion it is ffmpeg)
sudo dnf install /usr/bin/ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Arch / CachyOS / Garuda
sudo pacman -S ffmpeg
```

### Screen cast shows black on Wayland

**Cause:** GNOME/KDE Wayland require PipeWire portal for screen capture.

```bash
# Fedora
sudo dnf install python3-gobject python3-dbus \
    gstreamer1-plugins-base pipewire-gstreamer

# Ubuntu / Debian
sudo apt install python3-gi python3-dbus python3-gst-1.0 \
    gir1.2-gst-plugins-base-1.0 gstreamer1.0-pipewire

# Arch
sudo pacman -S python-gobject python-dbus gst-python \
    gst-plugins-base gst-plugin-pipewire
```

> **The GStreamer PipeWire plugin is the one people miss.** TRCC reads the
> portal's video through a `pipewiresrc` element, which lives in its own
> package — `pipewire-gstreamer`, `gstreamer1.0-pipewire` or
> `gst-plugin-pipewire` above. Without it the permission dialog appears, you
> approve it, your desktop briefly shows "screen is being shared", and then the
> session drops and the panel stays black. Check you have it with:
>
> ```bash
> gst-inspect-1.0 pipewiresrc
> ```
>
> `gst-plugins-base` also matters: it carries the `GstVideo` typelib TRCC uses
> to read each frame's true row layout.

> On X11 no portal is involved — capture uses Qt's own screen grab, then `scrot`/`maim`/`import` (or `spectacle`/`gnome-screenshot`) if that fails.
> On **every** Wayland compositor, including wlroots ones, capture goes through the
> PipeWire portal, so you also need your compositor's portal backend:
> `xdg-desktop-portal-gnome`, `xdg-desktop-portal-kde` or `xdg-desktop-portal-wlr`.

The first launch asks permission to share your screen. Tick **"Remember this
selection"** and TRCC stores the grant, so later launches start without asking.

### Screen cast freezes on one image (Sway, Hyprland, river, Wayfire)

**This is an upstream bug in `xdg-desktop-portal-wlr`, not in TRCC.**

The panel shows the first captured frame and then never updates. Measured on
`xdg-desktop-portal-wlr` 0.8.4 with sway 1.11: three identical runs delivered
**211 frames, then 1, then 1** — so it is intermittent rather than constant.
(Three runs is a small sample; treat it as "sometimes works", not as a rate.)
The compositor itself is healthy throughout (`grim`, which uses
the older `wlr-screencopy` protocol, captures normally); the fault is in the
portal's newer `ext-image-copy-capture-v1` path.

TRCC says so in the log when it happens:

```
screencast: the portal session is running but has produced no new frame for 5.0s
— the panel is showing a frozen image.
```

**What you can do:**

- Restart the screen cast — a fresh session often streams normally.
- Try a newer `xdg-desktop-portal-wlr` if your distro has one. (We have not
  confirmed which version fixes it — only that 0.8.4 is affected.)
- `force_mod_linear=1` in `~/.config/xdg-desktop-portal-wlr/config` does **not**
  help this particular problem (tested).

Please don't file this against TRCC — but **do** file it if the log line above
is absent while your panel is frozen, because that means something else.

---

## NixOS-Specific

### `trcc system setup` doesn't work

NixOS manages udev rules declaratively, so don't hand-write them — enable the
flake's module, which installs TRCC's own generated rules (LCD/LED devices, the
CPU-power counter and the motherboard RGB controllers), loads `sg`, and sets the
usb-storage quirk:

```nix
# configuration.nix (with inputs.trcc-linux.url = "github:Lexonight1/thermalright-trcc-linux")
{ inputs, ... }:
{
  imports = [ inputs.trcc-linux.nixosModules.default ];
  programs.trcc-linux.enable = true;
}
```

See [Install Guide → NixOS](GUIDE_INSTALL.md#nixos) for the full setup.

**RGB memory lighting** cannot be switched on from the RGB page on NixOS: add
`programs.trcc-linux.ramLighting = true;` beside `enable`, rebuild, and restart.

Then rebuild: `sudo nixos-rebuild switch`

---

## Quick Diagnostic

```bash
trcc doctor          # health checks only, changes nothing
trcc system setup    # applies the setup (udev rules, quirks, …), then lists any permission warnings
```

---

## Still Stuck?

1. Run `trcc report` and copy the full output
2. [Open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues) with the report
3. Include your distro, kernel version (`uname -r`), and what you've tried

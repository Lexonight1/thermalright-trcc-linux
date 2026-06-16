# Welcome to Linux — A Guide for Windows TRCC Users

So you've been using the Windows TRCC app to control your Thermalright LCD cooler, and now you've switched to Linux. Welcome! This guide will get you from zero to a working LCD in about 10 minutes.

Don't worry — you don't need to be a Linux expert. If you can copy-paste commands into a terminal, you're good.

---

## What's Different on Linux?

On Windows, you downloaded a `.exe`, double-clicked it, and it worked. Linux is a little different, but not harder — just different.

| Windows | Linux |
|---------|-------|
| Download `.exe` installer | Install a package (like an app store) |
| Right-click "Run as Administrator" | Type `sudo` before a command |
| Device Manager | `trcc detect` (we handle it) |
| Task Manager | `htop` or `top` |
| `.exe` files | No file extensions needed |
| Control Panel | Settings app + terminal commands |

---

## Step 1: Open a Terminal

The terminal is your command line. It's like PowerShell or Command Prompt, but more powerful.

**How to open it:**
- Press `Ctrl + Alt + T` (works on most distros)
- Or search "Terminal" in your app menu

You'll see something like:
```text
user@computer:~$
```

That's your prompt. You type commands here and press Enter.

---

## Step 2: Figure Out Your Distro

A "distro" is your flavor of Linux — Ubuntu, Fedora, Arch, etc. Different distros use different install commands.

Not sure what you're running? Type this:
```bash
cat /etc/os-release
```

Look for the `NAME=` line. Common ones:
- **Fedora** / **CachyOS** / **Nobara** → uses `dnf`
- **Ubuntu** / **Debian** / **Pop!_OS** / **Linux Mint** → uses `apt`
- **Arch** / **Manjaro** / **EndeavourOS** / **CachyOS** → uses `pacman`

---

## Step 3: Install TRCC

Pick your distro and copy-paste **one line**. It auto-detects the latest version — no manual editing needed.

### Arch / Manjaro / CachyOS / EndeavourOS
```bash
curl -s https://api.github.com/repos/Lexonight1/thermalright-trcc-linux/releases/latest | grep -o 'https://.*pkg.tar.zst' | xargs wget -c && sudo pacman -U trcc-linux-*.pkg.tar.zst
```

### Fedora / Nobara
```bash
curl -s https://api.github.com/repos/Lexonight1/thermalright-trcc-linux/releases/latest | grep -o 'https://.*noarch.rpm' | xargs wget -c && sudo dnf install ./trcc-linux-*.rpm
```

### Ubuntu (24.04+) / Debian 13+ / Pop!_OS / Linux Mint 22+
```bash
curl -s https://api.github.com/repos/Lexonight1/thermalright-trcc-linux/releases/latest | grep -o 'https://.*_all.deb' | head -1 | xargs wget -c && sudo dpkg -i trcc-linux_*_all.deb && sudo apt install -f -y
```

### Ubuntu 22.04 / Debian 12 / Linux Mint 21 (legacy)
```bash
curl -s https://api.github.com/repos/Lexonight1/thermalright-trcc-linux/releases/latest | grep -o 'https://.*legacy_all.deb' | xargs wget -c && sudo dpkg -i trcc-linux_*legacy_all.deb && sudo apt install -f -y
```

### pip (any distro, if packages don't work)
```bash
pip install trcc-linux
```

---

## Step 4: Set Up Permissions

On Windows, the app just talks to USB devices. On Linux, you need to tell the system "this user is allowed to talk to this USB device." It's a one-time setup:

```bash
sudo trcc system setup
```

Then unplug and replug your LCD. That's it.

---

## Step 5: Launch TRCC

```bash
trcc gui
```

You should see the same familiar interface you know from Windows. Click your device, pick a theme, done.

**Want it to start automatically when you log in?**
```bash
trcc install-desktop
```

This creates a desktop shortcut and autostart entry.

---

## Step 6: Verify Everything Works

Run the built-in doctor to check all dependencies:
```bash
trcc doctor
```

You should see all `[OK]` entries. If something says `[FAIL]`, follow the suggestion it gives you.

To see your device:
```bash
trcc detect
```

---

## Common Linux Concepts You'll See

**`sudo`** — "Super User DO." It runs a command as administrator. Linux will ask for your password. This is normal and safe — it's the same as "Run as Administrator" on Windows.

**Package manager** — Your app store for the terminal. `dnf`, `apt`, and `pacman` are the most common ones. They download, install, and update software.

**`/dev/sg0`** — Linux represents hardware as files. Your LCD shows up as `/dev/sg0` or similar. TRCC finds it automatically — you never need to type this.

**udev rules** — Linux permission rules for USB devices. The `trcc system setup` command creates these for you. Without them, only the root user can talk to your LCD.

**`~`** — Shorthand for your home folder. `~/.trcc/` means `/home/yourname/.trcc/`.

**`pip`** — Python's package installer. If you installed via pip, you update with `pip install --upgrade trcc-linux`.

---

## Troubleshooting

### "Permission denied" or device not found
```bash
sudo trcc system setup
```
Then unplug and replug your LCD.

### Screen stays on the splash/boot logo
Run `trcc doctor` and check for `[FAIL]` entries. Most common fix:
```bash
sudo trcc system setup
```

### "command not found: trcc"
If you installed via pip, make sure `~/.local/bin` is in your PATH:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### GUI looks weird or won't start
Try with window decorations:
```bash
trcc gui --decorated
```

### Device disconnects every 30 seconds
Update your udev rules — this was fixed in v9.2.10:
```bash
sudo trcc system setup
```

### Need more help?
```bash
trcc report
```
This generates a diagnostic report. Paste it in a [GitHub issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues) and we'll help you out.

---

## Coming from the Windows TRCC App?

Everything you could do on Windows works on Linux:

| Windows Feature | Linux Equivalent |
|----------------|-----------------|
| Main GUI | `trcc gui` |
| Theme browser | Same — click themes in the GUI |
| Overlay editor | Same — drag elements on the preview |
| LED control | Same — click your LED device |
| System monitoring | Same — CPU, GPU, RAM, temps |
| Media player | Same — toggle media player, load a video |
| Screencast | Same — toggle screencast, set region |
| Cloud themes | Same — download from the gallery |

**Bonus features only on Linux:**
- Full CLI — control everything from the terminal
- REST API — control your LCD from scripts, Home Assistant, etc.
- Wayland support — works on modern Linux desktops
- Audio visualization — mic spectrum bars on screencast
- No telemetry, no ads, no bloat

---

## Quick Reference

| What you want | Command |
|---------------|---------|
| Launch GUI | `trcc gui` |
| Detect devices | `trcc detect` |
| Check health | `trcc doctor` |
| Set up permissions | `sudo trcc system setup` |
| Generate bug report | `trcc report` |
| Update (Arch) | `sudo pacman -U trcc-linux-*.pkg.tar.zst` |
| Update (Fedora) | `sudo dnf install ./trcc-linux-*.rpm` |
| Update (Ubuntu) | `sudo dpkg -i trcc-linux_*.deb` |
| Update (pip) | `pip install --upgrade trcc-linux` |
| Start API server | `trcc serve` |
| Interactive shell | `trcc shell` |

---

Still stuck? Open an issue at [github.com/Lexonight1/thermalright-trcc-linux/issues](https://github.com/Lexonight1/thermalright-trcc-linux/issues) — we're friendly and we answer fast.

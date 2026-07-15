# TRCC Linux — Installation Guide

A step-by-step guide for every major Linux distro. Each section is self-contained — find your distro, copy-paste the commands, done.

> **New to Linux?** See [New to Linux?](GUIDE_NEW_TO_LINUX.md) for a quick primer on terminals, package managers, and other concepts used here.

---

## Table of Contents

1. [Before You Start](#before-you-start)

**Find your distro and click through:**

| Distribution | Package | PyPI / pip |
|-------------|---------|------------|
| Fedora / Nobara | [Native RPM](#fedora--nobara) | [pip](#fedora--nobara-pip) |
| Debian 13+ / Mint 22+ / Pop!_OS / Zorin | [Native DEB](#ubuntu--debian--mint--pop_os--zorin) | [pip](#ubuntu--debian--mint--pop_os--zorin-pip) |
| Ubuntu 24.04 / Ubuntu 22.04 / Mint 21.x / Debian 12 (older) | [Legacy DEB](#ubuntu-2204--mint-21x--debian-12-legacy-deb) | [pip](#ubuntu--debian--mint--pop_os--zorin-pip) |
| Arch / CachyOS / Manjaro / EndeavourOS / Garuda | [Native pkg](#arch--cachyos--manjaro--endeavouros--garuda) | [pip](#arch--cachyos--manjaro--endeavouros--garuda-pip) |
| openSUSE | [Native RPM](#opensuse) | [pip](#opensuse-pip) |
| NixOS | [Flake](#nixos) | — |
| Gentoo | [Ebuild](#gentoo) | — |
| Bazzite / Bluefin / Aurora / Fedora Atomic | [rpm-ostree](#bazzite--aurora--bluefin--fedora-atomic) | — |
| SteamOS (Steam Deck) | — | [pip / Distrobox](#steamos-steam-deck) |
| Vanilla OS | — | [apx subsystem](#vanilla-os) |
| ChromeOS (Crostini) | — | [pip](#chromeos-crostini) |
| Void Linux | — | [pip](#void-linux) |
| Alpine Linux | — | [pip](#alpine-linux) |
| Solus | — | [pip](#solus) |
| Clear Linux | — | [pip](#clear-linux) |
| Windows 10/11 | [Installer](#windows-experimental) | — |
| macOS 11+ (Apple Silicon) | [DMG](#macos-experimental) | — |
| macOS 11+ (Intel) | — | [pipx](#macos-experimental) |
| FreeBSD | — | [pip](#freebsd-experimental) |
| Asahi Linux (Apple Silicon) | — | [pip](#asahi-linux-apple-silicon) |
| Raspberry Pi / ARM SBCs | — | [pip](#raspberry-pi--arm-sbcs) |
| WSL2 | — | [pip](#wsl2-windows-subsystem-for-linux) |

**Other:**
- [Git Clone Install](#git-clone-install)
- [After Installing](#after-installing)
- [Verify Your Download](#verify-your-download)
- [What Each Package Does](#what-each-package-does)
- [Troubleshooting](#troubleshooting)
- [Uninstalling](#uninstalling)
- [Getting Help](#getting-help)

---

## Before You Start

You need:
- A Linux computer with internet access
- A Thermalright cooler with LCD or LED display, connected via the included USB cable
- About 5 minutes

### How to open a terminal

This whole guide uses the terminal (the command-line app). Here's how to open it:

| Desktop | How to open |
|---------|-------------|
| GNOME (Ubuntu, Fedora, Pop!_OS) | Press `Ctrl+Alt+T`, or search "Terminal" in Activities |
| KDE Plasma (Kubuntu, Fedora KDE, Manjaro) | Press `Ctrl+Alt+T`, or search "Konsole" in the app menu |
| XFCE (Xubuntu, Mint XFCE) | Press `Ctrl+Alt+T`, or find "Terminal Emulator" in the app menu |
| Cinnamon (Linux Mint) | Press `Ctrl+Alt+T` |
| Any distro | Search "terminal" in your app launcher |

Once the terminal is open, you can paste commands with `Ctrl+Shift+V` (not `Ctrl+V` — that doesn't work in most Linux terminals).

---

> **Not sure which distro you're running?** Open a terminal and type `cat /etc/os-release` — the `ID=` line tells you. Find it in the table above.

---

### Fedora / Nobara

Covers: Fedora 39+, Nobara 39+

**One-liner** (download + install in one command):
```bash
sudo dnf install https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest/download/trcc-linux-latest.noarch.rpm
```

**Or manually:** Download the `.rpm` file from the [latest release](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest), then:

```bash
cd ~/Downloads
sudo dnf install ./trcc-linux-*.noarch.rpm
```

It will ask for your password — type it and press Enter (the password won't show as you type, that's normal).

**Step 3 — Launch:**

```bash
trcc gui
```

That's it! If your device isn't detected, restart your computer and try again — that's usually all it takes. Still nothing? See the [Device Testing Guide](GUIDE_DEVICE_TESTING.md) or run `trcc report` and [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues/new) with the output.

---

### Ubuntu / Debian / Mint / Pop!_OS / Zorin

Covers: Ubuntu 24.04+ (including **26.04 / Python 3.14**), Debian 13+, Linux Mint 22+, Pop!_OS 24.04+, Zorin OS 17+, KDE neon, Kubuntu, Xubuntu, Lubuntu

> **Older versions** (Ubuntu 22.04, Mint 21.x, Debian 11/12, Pop!_OS 22.04, elementary OS 7) — use the [Legacy DEB](#ubuntu-2204--mint-21x--debian-12-legacy-deb) instead, which bundles its own Python environment.

> **On Ubuntu 26.04 (Python 3.14):** use **v9.7.5 or newer** — earlier `.deb`s installed to a Python-version-specific path and failed with `ModuleNotFoundError: No module named 'trcc'`.

**One-liner** (download + install in one command):
```bash
curl -LO https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest/download/trcc-linux-latest_all.deb && sudo apt install ./trcc-linux-latest_all.deb
```

`apt install ./file.deb` installs the package **and** pulls in its dependencies in
one step. (The `./` matters — without it, apt looks for a package by that name.)

**Or manually:** Download the `.deb` file from the [latest release](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest), then:

```bash
cd ~/Downloads
sudo apt install ./trcc-linux_*_all.deb
```

**Step 3 — Launch:**

```bash
trcc gui
```

That's it! If your device isn't detected, restart your computer and try again — that's usually all it takes. Still nothing? See the [Device Testing Guide](GUIDE_DEVICE_TESTING.md) or run `trcc report` and [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues/new) with the output.

> **`python3-pyside6` not found?** On Ubuntu 24.04 it may be in the `universe` repository — enable it, then install again:
> ```bash
> sudo add-apt-repository universe
> sudo apt update
> sudo apt install ./trcc-linux-latest_all.deb
> ```

---

### Ubuntu 22.04 / Mint 21.x / Debian 12 (Legacy DEB)

Covers: Ubuntu 22.04 LTS, Linux Mint 21.x, Linux Mint 22.x, Debian 12 (Bookworm), Pop!_OS 22.04, elementary OS 7

The standard `.deb` requires `python3-pyside6` and other packages that aren't in Ubuntu 22.04's repos. This legacy package installs Python dependencies into `/opt/trcc-linux` via pip — no system Python conflicts, no `--break-system-packages`.

**Step 1 — Download** the `*legacy*_all.deb` file from the [latest release](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest).

**Step 2 — Install:**

```bash
cd ~/Downloads
sudo apt install ./trcc-linux_*legacy*_all.deb
```

**Step 3 — Run setup:**

```bash
trcc system setup
```

**Step 4 — Launch:**

```bash
trcc gui
```

> To uninstall: `sudo dpkg -r trcc-linux` — this removes `/opt/trcc-linux` and the wrapper scripts automatically.

---

### Arch / CachyOS / Manjaro / EndeavourOS / Garuda

Covers: Arch Linux, CachyOS, Manjaro, EndeavourOS, Garuda Linux, Artix Linux, ArcoLinux, BlackArch

**One-liner** (download + install in one command):
```bash
curl -LO https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest/download/trcc-linux-latest-any.pkg.tar.zst && sudo pacman -U trcc-linux-latest-any.pkg.tar.zst
```

**Or manually:** Download the `.pkg.tar.zst` file from the [latest release](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest), then:

```bash
cd ~/Downloads
sudo pacman -U trcc-linux-*-any.pkg.tar.zst
```

**Step 3 — Launch:**

```bash
trcc gui
```

That's it! If your device isn't detected, restart your computer and try again — that's usually all it takes. Still nothing? See the [Device Testing Guide](GUIDE_DEVICE_TESTING.md) or run `trcc report` and [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues/new) with the output.

---

### openSUSE

Covers: openSUSE Tumbleweed, openSUSE Leap 15.5+

**Step 1 — Download** the `.rpm` file from the [latest release](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest).

**Step 2 — Open a terminal** and run:

```bash
cd ~/Downloads
sudo zypper install ./trcc-linux-*.noarch.rpm
```

**Step 3 — Launch:**

```bash
trcc gui
```

---

### NixOS

Covers: NixOS 24.05+, NixOS unstable

NixOS is different from other distros — you declare packages in a config file instead of downloading them. Add to your `flake.nix` and `configuration.nix`:

```nix
# flake.nix
{
  inputs.trcc-linux.url = "github:Lexonight1/thermalright-trcc-linux";
  # ...
}
```

```nix
# configuration.nix
{ inputs, ... }:
{
  imports = [
    inputs.trcc-linux.nixosModules.default
  ];

  programs.trcc-linux.enable = true;
}
```

Then rebuild:

```bash
sudo nixos-rebuild switch
```

Then:

```bash
trcc gui
```

> **NixOS note:** The `trcc system setup` command won't work because NixOS manages udev rules declaratively. If you need manual udev rules, add them to your `configuration.nix`:
> ```nix
> services.udev.extraRules = ''
>   SUBSYSTEM=="scsi_generic", ATTRS{idVendor}=="87cd", ATTRS{idProduct}=="70db", MODE="0666"
>   SUBSYSTEM=="scsi_generic", ATTRS{idVendor}=="87ad", ATTRS{idProduct}=="70db", MODE="0666"
>   SUBSYSTEM=="scsi_generic", ATTRS{idVendor}=="0416", ATTRS{idProduct}=="5406", MODE="0666"
>   SUBSYSTEM=="scsi_generic", ATTRS{idVendor}=="0402", ATTRS{idProduct}=="3922", MODE="0666"
>   SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0416", ATTRS{idProduct}=="5302", MODE="0666"
>   SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0418", ATTRS{idProduct}=="5303", MODE="0666"
>   SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0418", ATTRS{idProduct}=="5304", MODE="0666"
>   SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0416", ATTRS{idProduct}=="8001", MODE="0666"
> '';
> ```

---

### Gentoo

Covers: Gentoo Linux, Funtoo, Calculate Linux

An ebuild is provided in the repo:

```bash
# Copy the ebuild to your local overlay
sudo mkdir -p /var/db/repos/local/app-misc/trcc-linux
sudo cp packaging/gentoo/trcc-linux-*.ebuild /var/db/repos/local/app-misc/trcc-linux/
cd /var/db/repos/local/app-misc/trcc-linux
sudo ebuild trcc-linux-*.ebuild manifest

# Install
sudo emerge --ask app-misc/trcc-linux
```

Then:

```bash
trcc gui
```

---

## PyPI Install (Alternative)

If native packages aren't available for your distro, or you prefer pip. This requires installing system dependencies first, then the Python package.

---

### Fedora / Nobara (pip)

```bash
# Step 1: Install system dependencies
sudo dnf install pipx sg3_utils python3-pyside6 portaudio ffmpeg

# Step 2: Install optional extras (hardware sensors, Wayland screen capture)
sudo dnf install lm_sensors grim python3-gobject python3-dbus pipewire-devel

# Step 3: Install TRCC
pipx install trcc-linux

# Step 4: Run the setup wizard (device permissions, desktop shortcut)
trcc system setup

# Step 5: Launch
trcc gui
```

> To upgrade later: `pipx upgrade trcc-linux`

> **RHEL / Rocky / Alma:** Enable EPEL and CRB first:
> ```bash
> sudo dnf install epel-release
> sudo dnf config-manager --set-enabled crb
> ```
> Then run the commands above. If `python3-pyside6` isn't available, `pipx install trcc-linux` will pull it in automatically.

---

### Ubuntu / Debian / Mint / Pop!_OS / Zorin (pip)

> **Alternative for all distros**, and the only option for very old distros (Ubuntu 20.04, Debian 11, elementary OS 6) where even the legacy `.deb` won't work. `pipx` creates an isolated environment and handles all Python dependencies automatically.

```bash
# Step 1: Install system dependencies
sudo apt update
sudo apt install pipx libusb-1.0-0 libportaudio2 sg3-utils p7zip-full libxcb-cursor0 ffmpeg

# Step 2: Install optional extras (hardware sensors, Wayland screen capture)
sudo apt install lm-sensors grim python3-gi python3-dbus python3-gst-1.0

# Step 3: Install TRCC
pipx install trcc-linux

# Step 4: Run the setup wizard
trcc system setup

# Step 5: Launch
trcc gui
```

> To upgrade later: `pipx upgrade trcc-linux`

> **`pipx` not available?** On very old systems (Ubuntu 20.04, Debian 10), fall back to pip:
> ```bash
> sudo apt install python3-pip python3-venv
> pip install trcc-linux
> ```
> If pip shows an "externally-managed-environment" error, use `pip install --break-system-packages trcc-linux`.

---

### Arch / CachyOS / Manjaro / EndeavourOS / Garuda (pip)

Arch-based distros enforce PEP 668 — use `pipx` instead of `pip`:

```bash
# Step 1: Install system dependencies
sudo pacman -S python-pipx sg3_utils python-pyside6 portaudio ffmpeg

# Step 2: Install optional extras (hardware sensors, Wayland screen capture)
sudo pacman -S lm_sensors grim python-gobject python-dbus python-gst

# Step 3: Install TRCC via pipx
pipx install trcc-linux

# Step 4: Run the setup wizard
trcc system setup

# Step 5: Launch
trcc gui
```

> To upgrade later: `pipx upgrade trcc-linux`

---

### openSUSE (pip)

```bash
# Step 1: Install system dependencies
sudo zypper install python3-pipx sg3_utils python3-pyside6 portaudio ffmpeg

# Step 2: Install optional extras (hardware sensors, Wayland screen capture)
sudo zypper install sensors grim python3-gobject python3-dbus-python python3-gstreamer

# Step 3: Install TRCC
pipx install trcc-linux

# Step 4: Run the setup wizard
trcc system setup

# Step 5: Launch
trcc gui
```

> To upgrade later: `pipx upgrade trcc-linux`

> **openSUSE MicroOS:** Use `transactional-update` instead of `zypper`:
> ```bash
> sudo transactional-update pkg install sg3_utils python3-pipx python3-pyside6 ffmpeg
> sudo reboot
> ```
> Then continue from Step 3.

---

### Void Linux

```bash
# Step 1: Install system dependencies
sudo xbps-install sg3_utils python3-pipx python3-pyside6 portaudio ffmpeg

# Step 2: Install optional extras
sudo xbps-install lm_sensors grim python3-gobject python3-dbus python3-gst

# Step 3: Install TRCC
pipx install trcc-linux

# Step 4: Run the setup wizard
trcc system setup

# Step 5: Launch
trcc gui
```

> To upgrade later: `pipx upgrade trcc-linux`

> **Void musl:** If packages fail to build, you may need:
> ```bash
> sudo xbps-install python3-devel gcc
> ```

> If `python3-pyside6` isn't in the repo:
> ```bash
> sudo xbps-install qt6-base
> pip install PySide6
> ```

---

### Alpine Linux

```bash
# Step 1: Install system dependencies
sudo apk add python3 pipx sg3_utils py3-pyside6 portaudio ffmpeg

# Step 2: Install optional extras
sudo apk add lm-sensors grim py3-gobject3 py3-dbus

# Step 3: Install TRCC
pipx install trcc-linux

# Step 4: Run the setup wizard
trcc system setup

# Step 5: Launch
trcc gui
```

> To upgrade later: `pipx upgrade trcc-linux`

> **Alpine musl:** If `py3-pyside6` isn't available:
> ```bash
> sudo apk add python3-dev gcc musl-dev qt6-qtbase-dev
> pip install PySide6
> ```

---

### Solus

```bash
# Step 1: Install system dependencies
sudo eopkg install sg3_utils python3-pip portaudio ffmpeg

# Step 2: Install optional extras
sudo eopkg install lm-sensors grim python3-gobject python3-dbus

# Step 3: Install pipx, then TRCC
pip install pipx
pipx install trcc-linux

# Step 4: Run the setup wizard
trcc system setup

# Step 5: Launch
trcc gui
```

> To upgrade later: `pipx upgrade trcc-linux`

---

### Clear Linux

```bash
# Step 1: Install system dependencies
sudo swupd bundle-add python3-basic devpkg-sg3_utils devpkg-portaudio ffmpeg

# Step 2: Install optional extras
sudo swupd bundle-add sysadmin-basic devpkg-pipewire

# Step 3: Install pipx, then TRCC
pip install pipx
pipx install trcc-linux

# Step 4: Run the setup wizard
trcc system setup

# Step 5: Launch
trcc gui
```

> To upgrade later: `pipx upgrade trcc-linux`

---

## Git Clone Install

For developers or if you want the latest code:

```bash
git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
sudo ./install.sh
```

The script auto-detects your distro, installs system packages, Python deps, udev rules, and a desktop shortcut. After it finishes: restart your computer, then run `trcc gui`.

To uninstall: `trcc uninstall` (or `sudo ./install.sh --uninstall`).

---

## Immutable / Atomic Distros

These distros have read-only root filesystems. Standard package installation works differently.

---

### Bazzite / Aurora / Bluefin / Fedora Atomic

Covers: Bazzite, Aurora, Bluefin, Fedora Silverblue, Fedora Kinoite, and all Universal Blue / Fedora Atomic desktops

These use an immutable root filesystem — you can't `sudo dnf install` like normal Fedora.

**Option A — Native RPM (recommended):**

**One-liner** (download + install, requires reboot):
```bash
rpm-ostree install https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest/download/trcc-linux-latest.noarch.rpm && systemctl reboot
```

**Or manually:** Download the `.rpm` from the [latest release](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest), then:
```bash
cd ~/Downloads
rpm-ostree install ./trcc-linux-*.noarch.rpm
systemctl reboot
```

After reboot: `trcc gui`

> If you have a bulk USB device, you may also need SELinux policy: `trcc system setup`
> (if `checkmodule` is not found: `rpm-ostree install checkpolicy` and reboot first)

**Option B — pip in a venv (if RPM doesn't work):**

```bash
# Step 1: Layer sg3_utils (needed for SCSI USB devices — requires reboot)
rpm-ostree install sg3_utils
systemctl reboot
```

After rebooting:

```bash
# Step 2: Create a Python virtual environment (system Python is read-only)
python3 -m venv ~/trcc-env
source ~/trcc-env/bin/activate

# Step 3: Install TRCC
pip install trcc-linux

# Step 4: Set up device permissions
trcc system setup

# Step 5: Launch
trcc gui
```

**To launch TRCC in the future:**
```bash
source ~/trcc-env/bin/activate
trcc gui
```

> **Tip:** Add an alias so you don't have to type the activate command every time:
> ```bash
> echo 'alias trcc-start="source ~/trcc-env/bin/activate && trcc gui"' >> ~/.bashrc
> source ~/.bashrc
> # Now just type: trcc-start
> ```

**Optional desktop shortcut** (launches from your app menu):
```bash
trcc system setup
```
Say `y` when it asks about the desktop entry — it installs the `.desktop` file and TRCC icon to your app menu.

**Optional: Wayland screen capture** (for screen cast / eyedropper features):
```bash
source ~/trcc-env/bin/activate
pip install dbus-python PyGObject
```

**Alternative — Distrobox** (avoids layering anything onto the host):
```bash
distrobox create --name trcc --image fedora:latest
distrobox enter trcc

# Inside the container — normal Fedora commands work
sudo dnf install python3-pip sg3_utils python3-pyside6 portaudio ffmpeg
pip install trcc-linux
exit

# Back on the host — set up device permissions
trcc system setup
# Launch

# Run from Distrobox
distrobox enter trcc -- trcc gui
```

---

### SteamOS (Steam Deck)

Switch to Desktop Mode first: hold the Power button > Desktop Mode, then open Konsole.

**Option A — Direct install** (simpler, but lost on SteamOS updates):

```bash
# Unlock the read-only filesystem
sudo steamos-readonly disable

# Set a password if you haven't already
passwd

# Install dependencies
sudo pacman -S --needed sg3_utils python-pip python-pyside6 portaudio ffmpeg

# Install TRCC
pip install --break-system-packages trcc-linux

# Set up device permissions
sudo trcc system setup

# Re-enable read-only (recommended)
sudo steamos-readonly enable

# Launch
trcc gui
```

> **Warning:** System packages installed with `pacman` are lost when SteamOS updates. You'll need to re-run the `steamos-readonly disable` and `pacman` steps after each update. The `pip install` persists in your home directory.

**Option B — Distrobox** (survives updates):

```bash
distrobox create --name trcc --image archlinux:latest
distrobox enter trcc

# Inside the container
sudo pacman -S python-pip sg3_utils python-pyside6 portaudio ffmpeg
pip install trcc-linux
exit

# Set up udev on the HOST (requires temporary unlock)
sudo steamos-readonly disable
sudo trcc system setup
sudo steamos-readonly enable

# Run
distrobox enter trcc -- trcc gui
```

---

### Vanilla OS

Covers: Vanilla OS 2.x (Orchid)

```bash
# Create a Fedora subsystem
apx subsystems create --name trcc-system --stack fedora

# Install dependencies inside the subsystem
apx trcc-system install python3-pip sg3_utils python3-pyside6 ffmpeg

# Enter the subsystem and install TRCC
apx trcc-system enter
pip install trcc-linux
exit

# Set up udev rules on the host
trcc system setup

# Run
apx trcc-system run -- trcc gui
```

---

### ChromeOS (Crostini)

1. Enable Linux: **Settings > Advanced > Developers > Turn On Linux development environment**
2. Open the Linux terminal:

```bash
# Install dependencies
sudo apt update
sudo apt install python3-pip python3-venv sg3-utils python3-pyside6 libportaudio2 ffmpeg

# Install TRCC
pip install --break-system-packages trcc-linux

# Set up device permissions
trcc system setup

# Launch
trcc gui
```

> **ChromeOS USB passthrough:** Go to **Settings > Advanced > Developers > Linux > Manage USB devices** and enable your Thermalright LCD device. You may need to replug after enabling.

---

## Windows (experimental)

Download [`trcc-latest-setup.exe`](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest/download/trcc-latest-setup.exe) from the [latest release](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest) and run the installer.

**What you get:**
- **TRCC** shortcut in Start Menu — launches the GUI
- **`trcc`** command in Command Prompt/PowerShell — CLI access (installer adds it to PATH)
- **7z, ffmpeg, libusb** bundled — no extra downloads needed

**Requirements:**
- Windows 10 or 11
- For NVIDIA GPU sensors: install [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) — trcc reads its sensors automatically
- For bulk USB devices (GrandVision, Mjolnir Vision, Stream Vision, Wonder Vision): install [Zadig](https://zadig.akeo.ie/) and switch the driver to WinUSB. Run `trcc system setup-winusb` for guidance. SCSI devices (most models) work with the default Windows driver.
- Run as Administrator for full hardware access

**Verify:**
```powershell
trcc detect --all
trcc doctor
```

---

## macOS (experimental)

Download [`trcc-latest-macos.dmg`](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest/download/trcc-latest-macos.dmg) from the [latest release](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest), open the DMG, and drag **TRCC** to Applications.

> **Intel Macs:** the DMG is built for Apple Silicon (arm64) only — it won't run
> on an Intel Mac. Install via [`pipx`](https://pipx.pypa.io) instead (confirmed
> working on Intel Macs and Hackintosh):
>
> ```bash
> brew install pipx libusb p7zip ffmpeg
> pipx install trcc-linux
> trcc gui
> ```
>
> To upgrade later: `pipx upgrade trcc-linux`

**Requirements:**
- macOS 11+ (Big Sur or later)
- Install `libusb`: `brew install libusb`
- LCD devices using SCSI (most models) need `sudo` to detach the kernel driver — HID devices work without root
- On Apple Silicon Macs, sensor reading requires `sudo` for `powermetrics` access

**CLI access:**
```bash
# Add to your shell profile for CLI access:
alias trcc='/Applications/TRCC.app/Contents/MacOS/TRCC'
```

---

## FreeBSD (experimental)

```bash
# CLI + API only
pkg install py311-pip libusb py311-pyusb py311-hid
pip install trcc-linux
trcc system setup
trcc serve    # or trcc detect, trcc lcd, etc.
```

```bash
# GUI (adds Qt6/PySide6)
pkg install py311-pip libusb py311-pyusb py311-hid py311-pyside6 p7zip ffmpeg
pip install trcc-linux
trcc system setup
trcc gui
```

**Notes:**
- SCSI devices use `/dev/pass*` via `camcontrol` (part of base system)
- CPU temp requires `kldload coretemp` (Intel) or `kldload amdtemp` (AMD)
- HID devices work via hidapi
- Run as root for full hardware access
- No native package — install from PyPI
- GUI is untested on BSD — if you get it working, please [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues/new) and let us know

---

## Special Hardware

---

### Asahi Linux (Apple Silicon)

Covers: Fedora Asahi Remix on Apple M1/M2/M3/M4 Macs

Follow the [Fedora / Nobara (pip)](#fedora--nobara-pip) instructions — Asahi is Fedora-based:

```bash
sudo dnf install python3-pip sg3_utils python3-pyside6 portaudio ffmpeg
pip install trcc-linux
trcc system setup
trcc gui
```

> USB-A ports on Apple Silicon Macs work through Thunderbolt hubs/docks. Make sure your cooler's USB cable goes through a compatible hub or USB-C adapter.

---

### Raspberry Pi / ARM SBCs

Covers: Raspberry Pi OS (Bookworm), Ubuntu for Raspberry Pi, Armbian

TRCC works on ARM64 (aarch64) systems:

```bash
# Raspberry Pi OS / Armbian (Debian-based)
sudo apt install python3-pip python3-venv sg3-utils python3-pyside6 libportaudio2 ffmpeg
pip install --break-system-packages trcc-linux
trcc system setup
trcc gui
```

> **No display?** If you're running headless (no monitor), the CLI still works:
> ```bash
> trcc send /path/to/image.png
> trcc display color ff0000
> trcc display test
> ```

> **ARM PySide6:** If `pip install PySide6` fails, use the system package (`python3-pyside6`). The CLI commands work without PySide6 — only the GUI needs it.

---

### WSL2 (Windows Subsystem for Linux)

> **You probably want the Windows version instead.** WSL2 has limited USB support. But if you want to try:

1. **On Windows** — Install [usbipd-win](https://github.com/dorssel/usbipd-win)
2. **On Windows** (PowerShell as admin):
   ```powershell
   usbipd list                          # Find your Thermalright device
   usbipd bind --busid <BUSID>          # Bind it
   usbipd attach --wsl --busid <BUSID>  # Attach to WSL
   ```
3. **Inside WSL2** — Follow the [Ubuntu / Debian (pip)](#ubuntu--debian--mint--pop_os--zorin-pip) instructions

> You need to re-attach the USB device every time you restart WSL or unplug it. GUI apps require WSLg (Windows 11 or recent Windows 10).

---

## After Installing

### Verify your device is detected

```bash
trcc detect
```

You should see something like:
```json
[1] 0402:3922  ALi Corp  (SCSI)  path=/dev/sg1
```
or for HID devices:
```json
[1] 0416:5302  USBDISPLAY  (HID)  path=1-8.1
```

Use `trcc detect --all` to see all connected devices.

### Quick test

Send a test pattern to make sure everything works:

```bash
trcc display test
```

This cycles through red, green, blue, yellow, magenta, cyan, and white on the LCD. If you see the colors, you're all set.

### `trcc: command not found`?

**Open a new terminal.** When you install with pip, the `trcc` command goes to `~/.local/bin/` which only gets added to your PATH when you open a new terminal session.

If it still doesn't work after opening a new terminal, add it manually:

```bash
# For bash (most distros)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# For zsh (Arch, Garuda, some Manjaro)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# For fish
fish_add_path ~/.local/bin
```

| Distro | `~/.local/bin` in PATH by default? |
|--------|-----------------------------------|
| Fedora | Yes |
| Ubuntu / Debian | Only if the directory exists at login time |
| Arch / Manjaro / EndeavourOS | No |
| openSUSE | No |
| Void / Alpine | No |

### Create a desktop shortcut (optional)

So you can launch TRCC from your app menu instead of typing a command:

```bash
trcc system setup
```

Say `y` when it asks about the desktop entry — it installs the `.desktop` file and TRCC icon to your app menu.

---

## Verify Your Download

Every release includes a `SHA256SUMS.txt` file. Download it from the same release page, then:

```bash
cd ~/Downloads
sha256sum -c SHA256SUMS.txt --ignore-missing
```

If you see `OK` next to your package — it's clean. Source code is GPL-3.0, fully auditable — no binaries, no obfuscation, no telemetry.

---

## What Each Package Does

| Package | Why it's needed |
|---------|----------------|
| `python3-pip` | Installs Python packages (like TRCC itself) |
| `sg3_utils` / `sg3-utils` | Sends data to the LCD over USB (SCSI commands) — **required for SCSI devices** |
| `PySide6` / `python3-pyside6` | The graphical user interface (GUI) toolkit |
| `ffmpeg` | Video and GIF playback on the LCD |
| `lm-sensors` / `lm_sensors` | Hardware sensor readings (CPU/GPU temps, fan speeds) — optional but recommended |
| `grim` | Screen capture on Wayland desktops — optional |
| `python3-gobject` / `python3-dbus` | PipeWire screen capture for GNOME/KDE on Wayland — optional |
| `portaudio` / `libportaudio2` | Audio I/O library — required for the audio visualizer feature |
| `pyusb` + `libusb` | USB communication for HID LCD/LED devices (pulled in by pip automatically) |

---

## Troubleshooting

Something not working? See the **[Troubleshooting Guide](GUIDE_TROUBLESHOOTING.md)** for common issues and fixes.

---

## Uninstalling

### Quick uninstall

```bash
trcc uninstall
```

Removes config, autostart, desktop files, udev rules (auto-elevates with sudo), and the pip package. Use `--yes` to skip prompts.

### Manual removal (if `trcc` command is unavailable)

```bash
pip uninstall trcc-linux
sudo rm /etc/udev/rules.d/99-trcc-lcd.rules
sudo rm /etc/modprobe.d/trcc-lcd.conf
sudo udevadm control --reload-rules
rm -rf ~/.config/trcc ~/.trcc
rm -f ~/.config/autostart/trcc*.desktop
rm -f ~/.local/share/applications/trcc*.desktop
```

---

## Getting Help

- Run `trcc doctor` to check your system for missing dependencies
- See the [Device Testing Guide](GUIDE_DEVICE_TESTING.md) for verifying your setup
- Run `trcc report` and [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues/new) with the output — this gives us everything we need to help you
- Check the [Troubleshooting Guide](GUIDE_TROUBLESHOOTING.md) for common issues and fixes
- For verbose output: `trcc gui -v` (or `trcc gui -vv` for debug)

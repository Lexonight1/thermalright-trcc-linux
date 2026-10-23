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
| Ubuntu 25.10+ / Debian 13+ | [Native DEB](#ubuntu--debian--mint--pop_os--zorin) | [pip](#ubuntu--debian--mint--pop_os--zorin-pip) |
| Ubuntu 22.04 / 24.04, Mint 21–22, Pop!_OS, Zorin, Debian 12 | [Legacy DEB](#ubuntu-2204--2404--mint-21x--22x--debian-12-legacy-deb) | [pip](#ubuntu--debian--mint--pop_os--zorin-pip) |
| Arch / CachyOS / Manjaro / EndeavourOS / Garuda | [Native pkg](#arch--cachyos--manjaro--endeavouros--garuda) | [pip](#arch--cachyos--manjaro--endeavouros--garuda-pip) |
| openSUSE | — | [pipx](#opensuse-pip) |
| NixOS | [Flake](#nixos) | — |
| Gentoo | [pipx](#gentoo) | — |
| Bazzite / Bluefin / Aurora / Fedora Atomic | [rpm-ostree](#bazzite--aurora--bluefin--fedora-atomic) | — |
| SteamOS (Steam Deck) | — | [one-line script](#steamos-steam-deck) |
| Vanilla OS | — | [apx subsystem](#vanilla-os) |
| ChromeOS (Crostini) | — | [pip](#chromeos-crostini) |
| Void Linux | — | [pip](#void-linux) |
| Alpine Linux | — | [pip](#alpine-linux) |
| Solus | — | [pip](#solus) |
| Clear Linux | — | [pip](#clear-linux) |
| Windows 10/11 | [Installer](#windows-experimental) | — |
| macOS 11+ (Apple Silicon) | [DMG](#macos-experimental) | — |
| macOS 11+ (Intel) | [DMG](#macos-experimental) | [pipx](#macos-experimental) |
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

Covers: Fedora 43+, and Nobara built on it. Older Fedora: use [pip](#fedora--nobara-pip) — the RPM is built for Fedora's current Python.

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

Covers: Ubuntu 25.10+ (including **26.04 / Python 3.14**) and its flavours (Kubuntu, Xubuntu, Lubuntu), Debian 13+

> **Ubuntu 24.04 and everything built on it** (Linux Mint 22, Pop!_OS 24.04, Zorin OS 18, KDE neon, elementary OS 8), **and older releases** (Ubuntu 22.04, Mint 21.x, Debian 11/12, Pop!_OS 22.04, Zorin OS 17, elementary OS 7) — use the [Legacy DEB](#ubuntu-2204--2404--mint-21x--22x--debian-12-legacy-deb) instead, which bundles its own Python environment. Ubuntu 24.04 does not ship PySide6 at all, so this package cannot install there (#316).

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

> **`python3-pyside6.qtcore … is not installable`?** Your release does not ship PySide6 (Ubuntu 24.04 and the distros built on it) — enabling `universe` does not help. Use the [Legacy DEB](#ubuntu-2204--2404--mint-21x--22x--debian-12-legacy-deb).

---

### Ubuntu 22.04 / 24.04 / Mint 21.x / 22.x / Debian 12 (Legacy DEB)

Covers: Ubuntu 22.04 LTS, Ubuntu 24.04 LTS, Linux Mint 21.x, Linux Mint 22.x, Debian 12 (Bookworm), Pop!_OS 22.04 / 24.04, Zorin OS 17 / 18, KDE neon, elementary OS 7 / 8

The standard `.deb` requires `python3-pyside6` and other packages that aren't in Ubuntu 22.04's repos. This legacy package installs Python dependencies into `/opt/trcc-linux` via pip — no system Python conflicts, no `--break-system-packages`. It builds that environment from the system `python3`, and TRCC needs Python 3.10 or newer — check with `python3 --version`.

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

> **Optional HID backend:** Arch ships two Python HID bindings that conflict, so the package depends on neither. Install `python-hidapi`, or keep `python-hid` if it is already there (SteamOS ships it) — one, never both.

**Step 3 — Launch:**

```bash
trcc gui
```

That's it! If your device isn't detected, restart your computer and try again — that's usually all it takes. Still nothing? See the [Device Testing Guide](GUIDE_DEVICE_TESTING.md) or run `trcc report` and [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues/new) with the output.

---

### openSUSE

There is no native openSUSE package: the `.rpm` on the releases page is built
for Fedora, and on openSUSE `zypper` refuses it (`nothing provides 'libusb1'`),
and its files target a different Python. Use **[pipx](#opensuse-pip)** —
measured working on Tumbleweed.

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

> **NixOS note:** The `trcc system setup` command won't work because NixOS manages udev rules declaratively. You don't need it: `programs.trcc-linux.enable = true;` installs the packaged udev rules (`packaging/udev/70-trcc-lcd.rules` and `70-trcc-rgb.rules`), creates the `trcc` group, loads the `sg` module and sets the usb-storage quirks. Don't write your own rules for TRCC devices. The person logged in at the computer reaches the coolers; for TRCC to keep driving them after you log out, or on a machine nobody logs in to, add `users.users.<you>.extraGroups = [ "trcc" ];`.

> **RGB memory lighting on NixOS:** the RGB page cannot switch it on here, because NixOS builds `/etc` from your configuration. Add `programs.trcc-linux.ramLighting = true;` beside `enable`, run `sudo nixos-rebuild switch`, then restart. It lets the person logged in at the computer open the chipset SMBus, which also holds the memory's own settings chips, so every program they run could reach them. To remove it, set it to `false`, rebuild and restart.

---

### Gentoo

Covers: Gentoo Linux, Funtoo, Calculate Linux

Install with pipx -- there is no maintained ebuild:

```bash
# Step 1: Install system dependencies
sudo emerge --ask dev-python/pipx sys-apps/sg3_utils media-video/ffmpeg media-libs/portaudio

# Step 2: Install TRCC
pipx install trcc-linux

# Step 3: Run the setup wizard (device permissions, desktop shortcut)
trcc system setup

# Step 4: Launch
trcc gui
```

> To upgrade later: `pipx upgrade trcc-linux`

---

## PyPI Install (Alternative)

If native packages aren't available for your distro, or you prefer pip. This requires installing system dependencies first, then the Python package.

---

### Fedora / Nobara (pip)

```bash
# Step 1: Install system dependencies
sudo dnf install pipx sg3_utils python3-pyside6 portaudio /usr/bin/ffmpeg /usr/bin/7z

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

> **Alternative for all distros.** TRCC needs Python 3.10 or newer — check with `python3 --version`. `pipx` creates an isolated environment and handles all Python dependencies automatically.

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

> **`pipx` not available?** Fall back to pip (it still needs Python 3.10 or newer):
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
sudo pacman -S python-pipx sg3_utils pyside6 portaudio ffmpeg p7zip

# Step 2: Install optional extras (hardware sensors, Wayland screen capture)
sudo pacman -S lm_sensors grim python-gobject python-dbus gst-python

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
sudo zypper install python3-pipx sg3_utils libgthread-2_0-0 portaudio ffmpeg 7zip

# Step 2: Install optional extras (hardware sensors, Wayland screen capture)
sudo zypper install sensors grim python3-gobject python3-dbus-python python3-gst

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
sudo xbps-install sg3_utils python3-pipx python3-pyside6 portaudio ffmpeg p7zip

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
sudo apk add python3 pipx sg3_utils py3-pyside6 portaudio ffmpeg p7zip

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

The script installs pip if it is missing, installs TRCC and its Python deps (in a virtual environment on immutable distros), then runs `trcc system setup` as root to install the udev rules. It does not install other system packages — add 7-Zip, ffmpeg and PortAudio with your package manager. After it finishes: restart your computer, then run `trcc gui`.

To uninstall: `sudo ./install.sh --uninstall`.

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
# Step 1: Create a Python virtual environment (system Python is read-only)
python3 -m venv ~/trcc-env
source ~/trcc-env/bin/activate

# Step 2: Install TRCC
pip install trcc-linux

# Step 3: Set up device permissions
trcc system setup

# Step 4: Launch
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
It installs the `.desktop` file and TRCC icon to your app menu without asking (skipped when a distro package already provides the menu entry).

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
sudo dnf install python3-pip sg3_utils python3-pyside6 portaudio /usr/bin/ffmpeg /usr/bin/7z
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

For SteamOS 3 — the Steam Deck's system, and SteamOS on other PCs. (SteamOS 1 and 2 were Debian releases Valve discontinued; their Python is too old for TRCC.)

A SteamOS update replaces the whole system with Valve's new image, so anything installed with `pacman` is gone after the next update. TRCC therefore installs into your home folder, which updates leave alone. SteamOS already ships what TRCC needs beside it: Python, 7-Zip, ffmpeg, PortAudio and libusb.

Switch to Desktop Mode (hold the Power button > Switch to Desktop), open Konsole, and run this as yourself — not with sudo:

```bash
curl -fsSL https://raw.githubusercontent.com/Lexonight1/thermalright-trcc-linux/main/install-steamos.sh | bash
```

It asks for your password once, for the device rules. Never set one? Run `passwd` first.

What it does:
1. Creates a Python environment in `~/.local/share/trcc-linux` and installs TRCC into it.
2. Links `trcc` into `~/.local/bin`.
3. Runs `trcc system setup`: the device rules in `/etc` — plus `/etc/atomic-update.conf.d/trcc-linux.conf`, which keeps them through SteamOS updates — the app-menu entry, and starting TRCC at login.

Then open **TRCC** from the application menu. To update, run the same line again.

To remove it:

```bash
trcc system ram-lighting disable          # first, if you enabled RAM lighting (switching RGB Lighting off does it too)
systemctl --user disable --now trccd.service && rm ~/.config/systemd/user/trccd.service
rm -rf ~/.local/share/trcc-linux ~/.local/bin/trcc ~/.local/share/applications/trcc-linux.desktop
sudo rm -f /etc/udev/rules.d/70-trcc-lcd.rules /etc/udev/rules.d/99-trcc-lcd.rules /etc/udev/rules.d/70-trcc-rgb.rules \
    /etc/modprobe.d/trcc-lcd.conf /etc/modules-load.d/trcc-sg.conf /etc/modules-load.d/trcc-rapl.conf \
    /etc/atomic-update.conf.d/trcc-linux.conf
```

Your settings (`~/.trcc`) and your own themes (`~/.trcc-user`) stay; delete those folders too for a clean slate.

> **Tested in an Arch container set up like SteamOS, not yet on a Steam Deck.** If something goes wrong, please open an issue with the output of `trcc report`.

**Alternative — Distrobox**, if you would rather keep TRCC in a container (`distrobox` and `podman` come with SteamOS 3.5 and later):

```bash
distrobox create --name trcc --image archlinux:latest
distrobox enter trcc

# Inside the container (it is disposable, so a system-wide pip install is fine here)
sudo pacman -Syu --needed python-pip sg3_utils pyside6 portaudio ffmpeg p7zip
sudo pip install --break-system-packages trcc-linux
exit

# Set up device access on the HOST (requires temporary unlock).
# The app is installed inside the container, so the host can't run `trcc system setup`;
# it installs the same files the Arch package does, straight from the repo.
sudo steamos-readonly disable
base=https://raw.githubusercontent.com/Lexonight1/thermalright-trcc-linux/main/packaging
sudo curl -fsSLo /etc/udev/rules.d/70-trcc-lcd.rules "$base/udev/70-trcc-lcd.rules"
sudo curl -fsSLo /etc/udev/rules.d/70-trcc-rgb.rules "$base/udev/70-trcc-rgb.rules"
sudo curl -fsSLo /etc/modprobe.d/trcc-lcd.conf "$base/modprobe/trcc-lcd.conf"
sudo curl -fsSLo /etc/modules-load.d/trcc-sg.conf "$base/modprobe/trcc-sg.conf"
sudo curl -fsSLo /etc/modules-load.d/trcc-rapl.conf "$base/modprobe/trcc-rapl.conf"
# The rules give the coolers to you at the seat and to the group named "trcc"
sudo groupadd -f --system trcc && sudo usermod -aG trcc "$USER"
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo modprobe sg; sudo modprobe intel_rapl_msr
# Keep those files through SteamOS updates, which reset /etc to the new image
printf '%s\n' /etc/udev/rules.d/70-trcc-lcd.rules /etc/udev/rules.d/70-trcc-rgb.rules \
    /etc/modprobe.d/trcc-lcd.conf /etc/modules-load.d/trcc-sg.conf /etc/modules-load.d/trcc-rapl.conf \
    | sudo tee /etc/atomic-update.conf.d/trcc-linux.conf >/dev/null
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
sudo apt install python3-pip python3-venv sg3-utils python3-pyside6 libportaudio2 ffmpeg p7zip

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
- **[PawnIO](https://pawnio.eu)** — the installer lays down this signed driver (skipped if another app already did), and TRCC reads your CPU's temperature and power through it. It is shared with other monitoring tools, so uninstalling TRCC leaves it; remove it from Windows' **Apps** list if you want it gone. FACEIT's anti-cheat currently refuses PawnIO's driver, so FACEIT players may need to uninstall it while they play.

**Requirements:**
- Windows 10 or 11
- NVIDIA GPU sensors work with nothing extra — they come from NVIDIA's own driver
- For bulk USB devices (GrandVision, Mjolnir Vision, Stream Vision, Wonder Vision): install [Zadig](https://zadig.akeo.ie/) and switch the driver to WinUSB. Run `trcc system setup` for guidance. SCSI devices (most models) work with the default Windows driver.
- No administrator needed: the installer adds TRCC's Windows service (TRCCService), which drives SCSI panels and reads PawnIO on TRCC's behalf

**Verify:**
```powershell
trcc detect
trcc doctor
```

---

## macOS (experimental)

Download [`trcc-latest-macos.dmg`](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest/download/trcc-latest-macos.dmg) from the [latest release](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest), open the DMG, and drag **TRCC** to Applications.

> **Intel Macs:** download
> [`trcc-latest-macos-intel.dmg`](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest/download/trcc-latest-macos-intel.dmg)
> instead — the plain DMG is Apple Silicon (arm64) only.  The Intel DMG lasts
> as long as GitHub offers an Intel build machine (until August 2027); `pipx`
> works on Intel Macs and Hackintosh either way:
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
- Nothing extra to install for the DMG — it bundles libusb, 7-Zip and ffmpeg
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
trcc serve    # or trcc detect, trcc display …, etc.
```

```bash
# GUI (adds Qt6/PySide6)
pkg install py311-pip libusb py311-pyusb py311-hid py311-pyside6 7-zip ffmpeg
pip install trcc-linux
trcc system setup
trcc gui
```

**Notes:**
- SCSI devices are driven over libusb (USB bulk-only transport), which needs root to detach the umass kernel driver
- CPU temp requires `kldload coretemp` (Intel) or `kldload amdtemp` (AMD)
- HID devices work over libusb (pyusb)
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
sudo dnf install python3-pip sg3_utils python3-pyside6 portaudio /usr/bin/ffmpeg /usr/bin/7z
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
sudo apt install python3-pip python3-venv sg3-utils python3-pyside6 libportaudio2 ffmpeg p7zip
pip install --break-system-packages trcc-linux
trcc system setup
trcc gui
```

> **No display?** If you're running headless (no monitor), the CLI still works:
> ```bash
> trcc detect                                  # find your device's key, e.g. 0402:3922
> trcc display load-image 0402:3922 /path/to/image.png
> trcc display color 0402:3922 ff0000
> trcc display test 0402:3922
> ```

> **ARM PySide6:** PySide6 is a required dependency, so `pip install trcc-linux` needs it to install. The CLI needs it too: TRCC draws every LCD frame with Qt, so display commands fail without it.

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
```text
2 device(s) found:
  0402:3922  Thermalright LCD Display  (wire=scsi, resolution=320×320 (catalog))
      this USB id covers several coolers — 'trcc device connect 0402:3922' names yours
  0416:5302  Winbond USBDISPLAY  (wire=hid, resolution=unknown)
      this USB id covers several coolers — 'trcc device connect 0416:5302' names yours
Resolutions above come from the catalog, not the panel.  Run 'trcc device connect <key>' to read the panel's own.
```

The first column is the device key that other commands take.

`trcc detect` always lists every connected device; it exits non-zero when it
finds none.

### Quick test

Send a test pattern to make sure everything works, using your device's key from `trcc detect`:

```bash
trcc display test 0402:3922
```

This cycles through red, green, blue, and black on the LCD. If you see the colors, you're all set.

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

It installs the `.desktop` file and TRCC icon to your app menu without asking (skipped when a distro package already provides the menu entry).

---

## Verify Your Download

Every release (from 9.11.2) includes `SHA256SUMS.txt` — the SHA-256 of every file in the release: the Linux packages and their `latest` copies, the wheel and sdist, and the Windows and macOS installers — and `SHA256SUMS.txt.sig`, the maintainer's signature on that list. Download both from the same release page, next to your file.

**1. Check the list is genuine** (OpenSSL 3). Fetch the public key once and keep it:

```bash
cd ~/Downloads
curl -fsSLO https://raw.githubusercontent.com/Lexonight1/thermalright-trcc-linux/main/packaging/keys/trcc-release-main.pub
openssl pkeyutl -verify -pubin -inkey trcc-release-main.pub -rawin -in SHA256SUMS.txt -sigfile SHA256SUMS.txt.sig
```

`Signature Verified Successfully` means the list came from the maintainer: the key that signs it never leaves their computer, so even someone who broke into the GitHub project could not forge it. Keep the `.pub` you fetched — a copy you already have cannot be swapped later.

**2. Check your file against the list:**

```bash
sha256sum -c SHA256SUMS.txt --ignore-missing
```

`OK` next to your file means it is exactly what was released. Source code is GPL-3.0, fully auditable — no binaries, no obfuscation, no telemetry.

---

## What Each Package Does

| Package | Why it's needed |
|---------|----------------|
| `python3-pip` | Installs Python packages (like TRCC itself) |
| `sg3_utils` / `sg3-utils` | Not used by TRCC — it talks to SCSI panels directly (the kernel's SG_IO interface). The distro packages still list it as a dependency |
| `PySide6` / `python3-pyside6` | The GUI toolkit — TRCC also draws every LCD frame with it, so the CLI needs it too |
| `ffmpeg` | Video and GIF playback on the LCD |
| `p7zip` / `7zip` / `/usr/bin/7z` | Unpacks the theme, cloud and mask archives — without it the theme browser stays empty |
| `lm-sensors` / `lm_sensors` | Provides `sensors-detect`, which finds the driver your motherboard's fan chip needs (`trcc doctor` suggests it when fans can't be read) — optional. TRCC reads the sensors from sysfs itself |
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

There is no `trcc uninstall` command — it was dropped in the rebuild and has
not been restored. Remove the pieces directly:

```bash
trcc system autostart disable                 # drop the autostart entry
trcc system ram-lighting disable              # BEFORE uninstalling: take back the RAM-bus grant, if you enabled it (switching RGB Lighting off does it too)
systemctl --user disable --now trccd.service && rm ~/.config/systemd/user/trccd.service   # the App's user service setup added
pipx uninstall trcc-linux                     # or pip uninstall trcc-linux, or your package manager
sudo rm -f /etc/udev/rules.d/70-trcc-lcd.rules /etc/udev/rules.d/99-trcc-lcd.rules /etc/udev/rules.d/70-trcc-rgb.rules   # udev rules (99-: before 2026-10)
sudo groupdel trcc                            # the device group setup made (optional)
sudo rm -f /etc/modprobe.d/trcc-lcd.conf
sudo rm -f /etc/modules-load.d/trcc-sg.conf /etc/modules-load.d/trcc-rapl.conf
rm -f ~/.local/share/applications/trcc-linux.desktop   # menu entry setup added (pip/source only)
sudo rm -f /usr/bin/trcc-ram-access /usr/bin/trcc-ram-access-off \
           /usr/share/polkit-1/actions/com.github.lexonight1.trcc.policy   # setup's polkit files (pip/source only)
rm -rf ~/.trcc ~/.trcc-user                   # config, data, your themes
```

The distro packages (rpm, deb, Arch) and `sudo ./install.sh --uninstall` remove
the RAM-lighting grant themselves; `pip`/`pipx uninstall` cannot, so run
`ram-lighting disable` first.

`~/.trcc-user/` holds themes and backgrounds **you** authored — back it up
first if you want to keep them.

### Manual removal (if `trcc` command is unavailable)

```bash
systemctl --user disable --now trccd.service && rm ~/.config/systemd/user/trccd.service
pip uninstall trcc-linux                      # or pipx uninstall trcc-linux
sudo rm -f /etc/udev/rules.d/70-trcc-lcd.rules /etc/udev/rules.d/99-trcc-lcd.rules /etc/udev/rules.d/70-trcc-rgb.rules
sudo rm /etc/modprobe.d/trcc-lcd.conf
sudo rm -f /etc/modules-load.d/trcc-sg.conf /etc/modules-load.d/trcc-rapl.conf
sudo rm -f /etc/udev/rules.d/70-trcc-ram-lighting.rules /etc/modules-load.d/trcc-i2c.conf   # RAM-lighting grant; the bus access itself ends at the next restart
sudo udevadm control --reload-rules
rm -rf ~/.trcc ~/.trcc-user                   # back up ~/.trcc-user first to keep your themes
rm -f ~/.config/autostart/trcc*.desktop
rm -f ~/.local/share/applications/trcc*.desktop
```

---

## Getting Help

- Run `trcc doctor` to check your system for missing dependencies
- See the [Device Testing Guide](GUIDE_DEVICE_TESTING.md) for verifying your setup
- Run `trcc report` and [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues/new) with the output — this gives us everything we need to help you
- Check the [Troubleshooting Guide](GUIDE_TROUBLESHOOTING.md) for common issues and fixes
- For verbose output: `trcc -v gui` (or `trcc -vv gui` for debug)

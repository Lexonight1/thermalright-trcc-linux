# New to Linux?

If you're coming from Windows or Mac, here's a quick primer on the Linux concepts you'll see in the [Install Guide](INSTALL_GUIDE.md).

## Key Concepts

**Terminal** — The command-line app where you type commands. Think Command Prompt or PowerShell on Windows, or Terminal.app on Mac. To open it: press `Ctrl+Alt+T`, or search "Terminal" in your app menu.

**sudo** — Runs a command as administrator (like "Run as Administrator" on Windows). It will ask for your password. Example: `sudo dnf install ffmpeg` installs FFmpeg with admin privileges.

**Package manager** — An app store for the command line. Each distro has its own: `dnf` (Fedora), `apt` (Ubuntu/Debian), `pacman` (Arch). When you see `sudo apt install ...`, that's installing software from your distro's repository.

**Distro** — Short for "distribution" — the flavor of Linux you're running (Ubuntu, Fedora, Arch, etc.). If you're not sure which one you have, open a terminal and run:
```bash
cat /etc/os-release
```
Or check **Settings > About** in your desktop environment.

**git clone** — Downloads a project from the internet. Similar to downloading a ZIP, but smarter — you can update later with `git pull` instead of re-downloading.

**pip** — Python's package installer. When you see `pip install -e .`, it's installing TRCC and its Python dependencies. The `-e` flag means changes to the code take effect immediately without reinstalling.

**udev rules** — Linux uses these to set permissions on hardware devices. Without them, only root can talk to your LCD. The `trcc setup-udev` command creates these automatically.

**/dev/sgX** — The device file for your LCD. Linux represents hardware as files — `/dev/sg0`, `/dev/sg1`, etc. TRCC finds yours automatically.

# TRCC service files

Optional OS-level service registration. Two independent units live here:

| Unit | What it does |
|------|--------------|
| `trccd.service` | the IPC daemon that owns USB and serves CLI/API/GUI clients |
| `trcc-display.service` | the render-and-send ticker, so the panel shows live metrics after a reboot |

**Install one or the other, not both.** Only one process may hold a panel's USB
interface; running both makes them fight over it.

The daemon auto-spawns when any UI calls it, so installing `trccd.service` is
**not required** — it just makes the daemon survive reboots / logout.

## Linux (systemd user unit)

```bash
# Install the unit (one-time)
mkdir -p ~/.config/systemd/user
cp trccd.service ~/.config/systemd/user/

# Enable + start
systemctl --user daemon-reload
systemctl --user enable --now trccd.service

# Check status
systemctl --user status trccd

# Tail logs (the daemon also writes ~/.trcc/trcc.log)
journalctl --user -u trccd -f
```

To uninstall:

```bash
systemctl --user disable --now trccd.service
rm ~/.config/systemd/user/trccd.service
systemctl --user daemon-reload
```

## LCD stats ticker (systemd user unit)

Drives the panel with the active theme on the configured refresh interval, from
login. Without it the LCD only updates while the GUI or `trcc display play` is
open, so it goes stale after a reboot.

```bash
# Install the unit + its helper (one-time)
mkdir -p ~/.config/systemd/user
cp trcc-display.service ~/.config/systemd/user/
sudo install -Dm755 trcc-display-ticker /usr/bin/trcc-display-ticker

systemctl --user daemon-reload
systemctl --user enable --now trcc-display.service

systemctl --user status trcc-display
journalctl --user -u trcc-display -f
```

The helper runs `trcc detect` and drives the first panel it finds, so the unit
carries no hardcoded VID:PID. To pin a specific panel, or to point at a
pip/pipx install rather than `/usr/bin/trcc`:

```bash
mkdir -p ~/.config/trcc
cat > ~/.config/trcc/ticker.env <<'EOF'
TRCC_DEVICE=0416:5408
TRCC_BIN=/home/you/.local/bin/trcc
EOF
systemctl --user restart trcc-display
```

To use the GUI while the ticker is running, stop it first — the ticker holds the
USB interface, and the GUI blocks on its splash rather than reporting the device
is busy:

```bash
systemctl --user stop trcc-display
trcc qtgui
systemctl --user restart trcc-display
```

To uninstall:

```bash
systemctl --user disable --now trcc-display.service
rm ~/.config/systemd/user/trcc-display.service
sudo rm /usr/bin/trcc-display-ticker
systemctl --user daemon-reload
```

## macOS (LaunchAgent)

```bash
cp ../launchd/com.thermalright.trccd.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.thermalright.trccd.plist
```

To uninstall:

```bash
launchctl unload ~/Library/LaunchAgents/com.thermalright.trccd.plist
rm ~/Library/LaunchAgents/com.thermalright.trccd.plist
```

## Windows

A scheduled task is installed by `trcc system setup` (Phase 11 wires this in).
Manual install:

```powershell
schtasks /Create /SC ONLOGON /TN "TRCC Daemon" /TR "trcc daemon" /F
```

## Verifying

After install, in any terminal:

```bash
# Should print info about the running daemon (or auto-spawn one)
trcc detect

# The IPC socket should exist:
ls -la $XDG_RUNTIME_DIR/trcc-linux.sock      # Linux
ls -la /tmp/trcc-linux.sock                  # macOS / fallback
```

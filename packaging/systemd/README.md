# trccd service files

Optional OS-level service registration for the TRCC daemon. The daemon
auto-spawns when any UI calls it, so installing these is **not required**
— they just make the daemon survive reboots / logout.

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

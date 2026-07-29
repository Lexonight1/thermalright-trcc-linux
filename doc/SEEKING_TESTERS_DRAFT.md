# Seeking testers — `TRCC_DAEMON=1` daemon mode (HID / Bulk / LY / LED)

> **DRAFT — not yet posted.** Review before publishing as a GitHub issue
> on `Lexonight1/thermalright-trcc-linux`.

A new background-process architecture has landed on the
`feat/trcc-singleton-service` branch — single daemon owns USB, CLI / API
/ GUI clients talk to it over a Unix domain socket. Detailed design in
`CLAUDE.md` (Daemon Mode section).

The daemon mode is **opt-in** (off by default during the donor-test
cycle) and verified end-to-end on a SCSI LCD (Frozen Warframe Pro
`0402:3922`). We need owners of the other protocols to confirm the wire
layer doesn't break the existing in-process behaviour for their device.

## Hardware we're seeking testers for

| Protocol | Sample devices | Status |
|---|---|---|
| **HID LCD** | Trofeo Vision (`0418:5303`), Wonder Vision 360 UB (`87ad:70db`), any other HID-protocol LCD | seeking |
| **Bulk LCD** | (any `LY`-style or generic bulk transport) | seeking |
| **LY LCD** | (any `LY` device) | seeking |
| **LED segment displays** | PA120, AX120, AK120, LC1/LC2, LF8/LF10/LF11/LF12, CZ1 | seeking |
| **Multi-LCD** | two LCDs simultaneously (any combination) | seeking |

## What to test (5 min per device)

1. **Install the branch:**

   ```bash
   git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
   cd thermalright-trcc-linux
   git checkout feat/trcc-singleton-service
   pip install --force-reinstall --no-deps .
   ```

2. **Start the daemon** in one terminal:

   ```bash
   trcc daemon
   ```

3. **In another terminal**, set the flag and run a few commands:

   ```bash
   export TRCC_DAEMON=1
   trcc detect                            # should list your device
   trcc brightness 2 --lcd 0              # LCD: should set brightness
   trcc led-color ff0000 --led 0          # LED: should turn red
   trcc led-mode breathing --led 0
   ```

4. **Status check:**

   ```bash
   trcc serve --port 9876 --token x &
   curl -H "X-API-Token: x" http://127.0.0.1:9876/trcc/status
   # Should show running: true, pid, uptime, device counts
   ```

5. **Stop:**

   ```bash
   trcc kill
   ```

## What we need back

In a comment on this issue:

- **Your VID:PID + protocol** (the line from `trcc detect`)
- **Did each command produce visible/audible effect on the device?** (yes/no per command)
- **Anything in `~/.trcc/trcc.log` that looks wrong** (paste tracebacks if any)
- **Time it took for `trcc kill` to return** (should be < 500 ms)

## Known issues (already tracked)

- GUI under `TRCC_DAEMON=1` refuses with a clear message. **Do not test
  GUI in daemon mode** — that's Phase 9 work, not part of this round.
- Windows < build 17063 silently falls back to in-process mode
  (`AF_UNIX` not available).

Thanks — you'll be in the v9.6.x release notes if your tests close one
of the protocol rows above. ☕

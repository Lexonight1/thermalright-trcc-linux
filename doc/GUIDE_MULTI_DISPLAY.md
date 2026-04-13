# Multi-Display (Multiple LCD Devices)

TRCC Linux can connect to multiple compatible LCD devices at the same time.
Each connected LCD has its own render pipeline (`Device` → `DisplayService`),
and the GUI creates one `LCDHandler` per LCD.

## What Works

- Multiple LCDs show up in the GUI device sidebar.
- You can switch which LCD is *active* (the one shown in the preview/settings panels).
- Inactive LCDs keep their physical display updated (including video playback).

## Important Detail: Shared GUI Widgets

The GUI uses a single set of preview/settings widgets for the *active* device.
Inactive devices must **not** update those widgets, but they still need to keep
their own video timer running to pump frames to hardware.

```mermaid
sequenceDiagram
  participant App as "TrccApp (core metrics loop)"
  participant GUI as "GUI (TrccMainWindow)"
  participant H1 as "LCDHandler A"
  participant H2 as "LCDHandler B"
  participant D1 as "Device A"
  participant D2 as "Device B"

  App->>GUI: DEVICES_CHANGED([Device A, Device B])
  GUI->>H1: create handler(Device A)
  GUI->>H2: create handler(Device B)
  GUI->>H1: activate (UI owns widgets)
  GUI->>H2: restore_inactive_state()
  loop video timers
    H1->>D1: video_tick() + send frame
    H2->>D2: video_tick() + send frame
  end
  Note over H1,H2: Only the active handler updates shared preview/progress widgets
```

## CLI Targeting

Most LCD commands accept `--device/-d` to target a specific device.

- Use a device path: `trcc send -d /dev/sg2 image.png`
- Or use the 1-based device number from `trcc detect --all`: `trcc send -d 2 image.png`

## Known Limitations

- Slideshow/carousel timers are tied to the active device UI state. When you switch
  away from an LCD, its slideshow is paused. Video playback continues.


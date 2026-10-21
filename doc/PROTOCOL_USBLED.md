# FormLED.cs — HID LED Protocol Reference

Reverse-engineered from `FormLED.cs`, `UCDevice.cs`, and `UCScreenLED.cs` (.NET/ILSpy decompilation; the C# oracle release is the one named by `ORACLE_VERSION` in `dev/decompiler/core/csharp.py`). This covers the **HID LED protocol** used by RGB LED devices (VID:PID `0416:8001`).

For SCSI LCD devices, see the SCSI section of [REFERENCE_TECHNICAL.md](REFERENCE_TECHNICAL.md#scsi-commands).
For HID LCD devices, see [PROTOCOL_USBLCDNEW.md](PROTOCOL_USBLCDNEW.md).

## Overview

```text
Windows:  TRCC.exe → FormLED.cs (effects) → UCDevice.cs (HID sender) → USB HID 64-byte reports
Linux:    trcc → LEDEffectEngine (effects) → Led (packet + chunking) → pyusb/hidapi → USB HID
```

LED devices use 64-byte HID reports (not SCSI) for RGB LED control. The protocol consists of:
1. **Handshake** — init packet (cmd=1), device responds with PM byte identifying model
2. **LED data** — 20-byte header (cmd=2) + N×3 bytes RGB payload, chunked to 64-byte HID reports

## USB Device

| Property | Value |
|---|---|
| VID | `0x0416` |
| PID | `0x8001` |
| Protocol | HID 64-byte reports |
| Write Endpoint | `0x02` |
| Read Endpoint | `0x01` (0x81 with IN flag) |
| Report Size | 64 bytes |

Windows code: `UsbHidDevice(1046, 32769, hidNameList1, 64)` in UCDevice.cs.

## Handshake

### Init Packet (Host → Device)

64 bytes, mostly zero-padded:

```text
Offset  Size  Value         Description
0-3     4     DA DB DC DD   Magic header
4-11    8     00 × 8        Reserved
12      1     01            Command: init/handshake
13-63   51    00 × 51       Padding to 64 bytes
```

### Timing

| Step | Delay | Source |
|---|---|---|
| Before init send | 50ms | `Thread.Sleep(50)` |
| After init send | 200ms | `Thread.Sleep(200)` |
| Before response read | 0ms | Immediate |

### Response (Device → Host)

64 bytes:

```text
Offset  Size  Value         Description
0-3     4     DA DB DC DD   Magic echo
4       1     SUB           Sub-type byte
5       1     PM            Product Model byte (identifies device)
6-11    6     ??            Reserved
12      1     01            Command echo
13-63   52    ??            Device-specific data
```

**Validation:** none is enforced. The Windows `DeviceDataReceived1` does not check the magic or the command byte, and neither does trcc: an unexpected value is logged as a warning and the reply is still parsed (`Led._parse_reply` in `adapters/device/led.py`). A recognised fingerprint header (see below) is not warned about.

**Key bytes:**
- `response[5]` = **PM** (product model) — used to select LED style
- `response[4]` = **SUB** (sub-type) — recorded, but not used to pick a style

The Windows handler's `data[]` array carries a leading report-ID byte, so its indices are one higher than these (its `data[17] == 16` is byte 16 here, `UCDevice.cs`).

**Firmware limitation:** The handshake only works **once per power cycle**. Subsequent init packets receive no response. The Linux implementation caches handshake results to `led_probe_cache.json` in the config directory (`~/.trcc/led_probe_cache.json` on Linux).

## PM → Style Mapping

From `FormLEDInit()` (FormLED.cs line 1598):

| PM Byte | Style | Model | LED Count | Segments | Zones | Background |
|---------|-------|-------|-----------|----------|-------|------------|
| 1 | 1 | FROZEN HORIZON PRO | 30 | 10 | 4 | D0数码屏 |
| 2 | 1 | FROZEN MAGIC PRO | 30 | 10 | 4 | D0数码屏 |
| 3 | 1 | AX120 DIGITAL | 30 | 10 | 4 | D0数码屏 |
| 16-22, 24-31 | 2 | PA120 DIGITAL | 84 | 18 | 4 | D0数码屏4区域 |
| 23 | 2 | RK120 DIGITAL | 84 | 18 | 4 | — |
| 32 | 3 | AK120 DIGITAL | 64 | 10 | 2 | D0数码屏 |
| 48 | 5 | LF8 | 93 | 23 | 2 | D0LF8 |
| 49 | 5 | LF10 (LF8 layout) | 93 | 23 | 2 | — |
| 80 | 6 | LF12 | 124 | 72 | 2 | D0LF12 |
| 96 | 7 | LF10 | 116 | 12 | 3 | D0LF10 |
| 112 | 9 | LC2 | 61 | 31 | 0 | D0LC2 |
| 128 | 4 | LC1 | 31 | 14 | 3 | D0LC1 |
| 129 | 10 | LF11 (wire sub-variant 1) | 38 | 17 | 4 | D0LF11 |
| 144 | 11 | LF15 | 93 | 72 | 2 | D0LF15 |
| 160 | 12 | LF13 | 62 | 62 | 0 | D0rgblf13 |
| 176 | 5 | LF25 (LF8 layout, wire sub-variant 1) | 93 | 23 | 2 | — |
| 208 | 8 | CZ1 | 18 | 13 | 4 | D0CZ1 |

Source of truth: `_PM_REGISTRY` in `src/trcc/core/led_protocol.py` (PM → style and model), `LED_STYLES` in `src/trcc/core/led_models.py` (LED count; Zones is its `zone_count`, where 0 means no zone buttons), and `LEGACY_STYLE_ID` (the Style column). Segments and Background come from the C# and are not mirrored in code.

**LED Count vs Segment Count:** LEDs are the physical addressable units. Segments are logical groups shown in the GUI. For style 1: 30 LEDs, 10 segments = 3 LEDs per segment.

**SUB byte:** trcc does not use it to pick a style — `resolve_pm()` looks up the PM byte alone, so PM=128 is always LC1.

### Handshake-header overrides (Linux additions)

A product can reuse another device's PM byte yet ship a distinct firmware
handshake header, leaving the PM byte alone ambiguous. When the leading four
header bytes match a known fingerprint, that entry wins over the PM registry
(`resolve_handshake()` in `core/led_protocol.py`, checked before `resolve_pm()`).

| Header | Style | Model | LED Count | Segments | Zones |
|--------|-------|-------|-----------|----------|-------|
| `DC DD AA 01` | 13 | Magic Qube | 65 | 14 | 4 |

The **Magic Qube** reports PM=208 (same as CZ1) but answers the HID handshake
with `DC DD AA 01` instead of the standard `DA DB DC DD` — the only wire-level
signal that tells it apart. It is a 65-LED device (two 7-segment digits at 3 LEDs
per segment, four corner metric indicators, and a 15-LED contour border) with a
CZ1-style 2-digit / 4-phase indicator rotation (CPU°C / GPU°C / GPU% / CPU%). No
reference exists in the Windows TRCC app, including the oracle release; the layout was
reverse-engineered on hardware and validated by
[@jphilipb](https://github.com/jphilipb).

## LED Data Packet

### Header (20 bytes)

From `SendHidVal()` (FormLED.cs line 4309):

```text
Offset  Size  Value         Description
0-3     4     DA DB DC DD   Magic header
4-11    8     00 × 8        Reserved
12      1     02            Command: LED data
13-15   3     00 × 3        Reserved
16-17   2     LL LL         Payload length (little-endian uint16)
18-19   2     00 × 2        Reserved
```

### RGB Payload (N × 3 bytes)

One RGB triplet per LED, in device wire order:

```text
Offset      Value    Description
0           R₀       LED 0 red   (scaled)
1           G₀       LED 0 green (scaled)
2           B₀       LED 0 blue  (scaled)
3           R₁       LED 1 red   (scaled)
4           G₁       LED 1 green (scaled)
5           B₁       LED 1 blue  (scaled)
...
(N-1)*3     Rₙ₋₁     LED N-1 red
(N-1)*3+1   Gₙ₋₁     LED N-1 green
(N-1)*3+2   Bₙ₋₁     LED N-1 blue
```

### Color Scaling

All RGB values are scaled by **0.4** before transmission (PWM duty cycle normalization):

```python
scaled_r = int(r * brightness / 100 * 0.4)
scaled_g = int(g * brightness / 100 * 0.4)
scaled_b = int(b * brightness / 100 * 0.4)
```

Source: `(float)(int)color * 0.4f` in FormLED.cs `SendHidVal`.

In trcc the service layer applies brightness before the wire; `Led` applies only the ×0.4 (`adapters/device/led.py`).

Off LEDs send `(0, 0, 0)`.

### Complete Packet

```json
[20-byte header] + [N × 3 bytes RGB payload]
```

Example for style 1 (30 LEDs):
- Header: 20 bytes
- Payload: 30 × 3 = 90 bytes
- Total: 110 bytes → chunked into 2 HID reports (64 + 46 padded to 64)

## HID Report Chunking

From `ThreadSendDeviceData1()` (UCDevice.cs line 983):

The complete packet is split into 64-byte HID reports:

```text
Packet size   Chunks   Example
110 bytes     2        [64] + [46→64 padded]
272 bytes     5        [64] × 4 + [16→64 padded]
```

**Chunking algorithm:**
1. While remaining > 0:
   - Send min(remaining, 64) bytes as one HID report
   - If chunk < 64 bytes, zero-pad to 64
2. After all chunks sent: sleep 30ms

**Timing:** `Thread.Sleep(30)` after each complete packet send. This is the minimum interval between consecutive LED updates.

## LED Effect Modes

Six effect modes, each implemented as a timer function in FormLED.cs. The timer fires every ~30ms. trcc implements these six as `LEDMode` 0-5 (`src/trcc/core/led_models.py`) and adds its own mode 6, OpenRGB.

### Mode 0: Static (DSCL_Timer)

All segments set to the user-selected color `(rgbR1, rgbG1, rgbB1)`.

```text
for each segment i:
    color[i] = (rgbR1, rgbG1, rgbB1)
```

### Mode 1: Breathing (DSHX_Timer)

Fade in/out with a 66-tick cycle (period ≈ 2 seconds at 30ms ticks).

```text
Constants:
    BREATHING_PERIOD = 66 ticks
    BREATHING_HALF = 33 ticks (fade in = fade out)
    BLEND_RATIO = 0.8 breathing + 0.2 static

Algorithm:
    rgbTimer++ (wraps at 66 → 0)

    if rgbTimer < 33:
        factor = rgbTimer / 33          (0.0 → 1.0, fade in)
    elif rgbTimer < 66:
        factor = (66 - rgbTimer) / 33   (1.0 → 0.0, fade out)
    else:
        factor = 0, reset rgbTimer = 0

    for each segment:
        r = int(rgbR1 * factor * 0.8 + rgbR1 * 0.2)
        g = int(rgbG1 * factor * 0.8 + rgbG1 * 0.2)
        b = int(rgbB1 * factor * 0.8 + rgbB1 * 0.2)
```

The 80/20 blend ensures LEDs never go completely dark — they maintain a 20% minimum intensity.

### Mode 2: Gradient / Spectrum (QCJB_Timer)

Smooth 6-phase color transition. All segments show the same color as it cycles through the spectrum.

```text
Constants:
    COLORFUL_STEP = 28 ticks per phase transition
    6 phases × 28 ticks = 168 tick full cycle (≈ 5 seconds)

Phases:
    0: Red→Yellow      (R=255, G=0, B=255→0)
    1: Yellow→Green    (R=255→0, G=0→255, B=0)
    2: Green→Cyan      (R=0, G=255, B=0→255)
    3: Cyan→Blue       (R=0, G=255→0, B=255)
    4: Blue→Magenta    (R=0→255, G=0, B=255)
    5: Magenta→Red     (R=255, G=0, B=255→0)
```

### Mode 3: Rainbow (CHMS_Timer)

Per-LED rainbow using the 768-entry RGB lookup table with phase offset per LED.

```text
Constants:
    TABLE_SIZE = 768 entries
    STEP = 4 entries per tick
    Phase offset per LED = (768 / segment_count / 6)

Algorithm:
    rgbTimer = (rgbTimer + 4) % 768

    for each segment i:
        table_index = (rgbTimer + i * 768 / segment_count / 6) % 768
        color[i] = RGBTable[table_index]
```

The divisor `/6` controls rainbow density — larger divisors spread colors more evenly across LEDs.

### RGB Rainbow Table (768 entries)

The table covers a full HSV hue cycle in 768 steps (128 steps per sextant):

| Index Range | Phase | R | G | B |
|---|---|---|---|---|
| 0-127 | Red→Yellow | 255 | 0→255 | 0 |
| 128-255 | Yellow→Green | 255→0 | 255 | 0 |
| 256-383 | Green→Cyan | 0 | 255 | 0→255 |
| 384-511 | Cyan→Blue | 0 | 255→0 | 255 |
| 512-639 | Blue→Magenta | 0→255 | 0 | 255 |
| 640-767 | Magenta→Red | 255 | 0 | 255→0 |

### Mode 4: Temperature-Linked (WDLD_Timer)

Maps CPU/GPU temperature to a fixed color:

| Temperature | Color | RGB |
|---|---|---|
| < 30°C | Cyan | (0, 255, 255) |
| 30-49°C | Green | (0, 255, 0) |
| 50-69°C | Yellow | (255, 255, 0) |
| 70-89°C | Orange | (255, 110, 0) |
| ≥ 90°C | Red | (255, 0, 0) |

All segments are set to the same color based on the current temperature reading.

### Mode 5: Load-Linked (FZLD_Timer)

Identical color thresholds as temperature-linked, but using CPU/GPU load percentage (0-100%) instead of temperature.

| Load | Color | RGB |
|---|---|---|
| < 30% | Cyan | (0, 255, 255) |
| 30-49% | Green | (0, 255, 0) |
| 50-69% | Yellow | (255, 255, 0) |
| 70-89% | Orange | (255, 110, 0) |
| ≥ 90% | Red | (255, 0, 0) |

## Preset Colors

From FormLED.cs `ucColor1_ChangeColor` handlers:

| Preset | RGB |
|---|---|
| C1 | (255, 0, 42) Red-pink |
| C2 | (255, 110, 0) Orange |
| C3 | (255, 255, 0) Yellow |
| C4 | (0, 255, 0) Green |
| C5 | (0, 255, 255) Cyan |
| C6 | (0, 91, 255) Blue |
| C7 | (214, 0, 255) Purple |
| C8 | (255, 255, 255) White |

**Note:** Windows `ucColor1Delegate` uses swapped B,G parameter order `(R, B, G)`.

## Multi-Zone Devices

Every style except LC2 (9) and LF13 (12) has 2-4 independent zones (`zone_count` in `LED_STYLES`). Each zone has its own:
- Mode (static, breathing, rainbow, etc.)
- Color (RGB)
- Brightness (0-100)
- On/off state

The LED payload maps physical LEDs to zones. For style 2 (PA120 DIGITAL, 84 LEDs, 4 zones):
- Zone 1: CPU LEDs
- Zone 2: Fan LEDs
- Zone 3: VRAM/HDD LEDs
- Zone 4: GPU LEDs

`SendHidVal()` reorders the segment-ordered color array to match the device's physical wire order before transmission.

## State Variables

| Variable | Type | Range | Windows Source |
|---|---|---|---|
| `myDeviceCount` | int | 0+ | Device index in multi-device setup |
| `rgbR1, rgbG1, rgbB1` | int | 0-255 | User-selected base RGB |
| `myBrightness` | byte | 0-100 | Brightness percentage |
| `myOnOff` | byte | 0/1 | Global LED on/off |
| `myLedMode` | int | 1-6 | Active effect mode |
| `nowLedStyle` | byte | 1-13 | Device style (from PM byte) |
| `rgbTimer` | int | 0-767 | Rainbow/cycle animation counter |
| `rgbTimer1` | int | 0-65 | Breathing animation counter |
| `nowJianbian` | int | 0-5 | Gradient phase (QCJB mode) |
| `nowJianbianTimer` | int | 0-27 | Gradient step within phase |
| `ledVal[N, 3]` | byte[,] | 0-255 | Computed LED color array |

## Timing Constants

| Constant | Value | Source |
|---|---|---|
| HID report size | 64 bytes | `UsbHidDevice(..., 64)` |
| Send cooldown | 30ms | `Thread.Sleep(30)` after send |
| Pre-handshake delay | 50ms | `Thread.Sleep(50)` before init |
| Post-handshake delay | 200ms | `Thread.Sleep(200)` after init |
| Breathing period | 66 ticks | `RGB_BREATHING_TIMER = 33` × 2 |
| Gradient step | 28 ticks | `RGB_COLORFUL_TIMER = 28` |
| Rainbow table size | 768 entries | `RGBTableCount = 768` |
| Rainbow step | 4 entries/tick | `rgbTimer += 4` |
| Color scale factor | 0.4 | PWM duty cycle normalization |
| Timer interval | ~30ms | FormLED timer tick rate |

## Probe Cache

The firmware only responds to the handshake **once per power cycle**. To avoid consuming the one-shot handshake during device detection, the Linux implementation caches probe results:

**Cache file:** `led_probe_cache.json` in the config directory (`~/.trcc/led_probe_cache.json` on Linux)

```json
{
    "0416_8001": {
        "pm": 3,
        "sub": 0,
        "model_name": "AX120_DIGITAL"
    },
    "0416_8001_2-1.4": {
        "pm": 128,
        "sub": 0,
        "model_name": "LC1"
    }
}
```

Cache keys include the USB bus path (e.g. `2-1.4`) to disambiguate multiple devices with the same VID:PID; a lookup falls back to the plain `vid_pid` key.

## Linux Implementation Files

| File | Purpose |
|---|---|
| `adapters/device/led.py` | `Led` — handshake, PM/SUB parse, probe cache, packet builder, 64-byte chunking |
| `core/led_protocol.py` | PM → style registry, handshake-header overrides, wire-order remap |
| `core/led_models.py` | `LEDMode`, `LedStyle`, `LED_STYLES`, `LedPayload` |
| `services/led_effects.py` | `LEDEffectEngine` — effect computation |
| `ui/gui/uc_led_control.py` | PySide6 GUI panel |
| `ui/gui/uc_screen_led.py` | LED segment visualization widget |
| `ui/gui/uc_color_wheel.py` | HSV color wheel widget |

## See Also

- [PROTOCOL_USBLCDNEW.md](PROTOCOL_USBLCDNEW.md) — USB bulk protocol from USBLCDNEW.exe
- [REFERENCE_TECHNICAL.md](REFERENCE_TECHNICAL.md) — SCSI protocol and full technical reference
- [REFERENCE_TECHNICAL.md](REFERENCE_TECHNICAL.md) — Full project technical reference

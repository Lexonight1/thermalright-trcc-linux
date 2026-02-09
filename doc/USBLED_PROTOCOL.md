# FormLED.cs — HID LED Protocol Reference

Reverse-engineered from `FormLED.cs`, `UCDevice.cs`, and `UCScreenLED.cs` (TRCC 2.0.3, .NET/ILSpy decompilation). This covers the **HID LED protocol** used by RGB LED devices (VID:PID `0416:8001`).

For SCSI LCD devices, see [USBLCD_PROTOCOL.md](USBLCD_PROTOCOL.md).
For HID LCD devices, see [USBLCDNEW_PROTOCOL.md](USBLCDNEW_PROTOCOL.md).

## Overview

```
Windows:  TRCC.exe → FormLED.cs (effects) → UCDevice.cs (HID sender) → USB HID 64-byte reports
Linux:    trcc → LEDController (effects) → LedHidSender (chunking) → pyusb/hidapi → USB HID
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

```
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

```
Offset  Size  Value         Description
0-3     4     DA DB DC DD   Magic echo (must match)
4       1     ??            Reserved
5       1     SUB           Sub-type byte (distinguishes HR10 from LC1)
6       1     PM            Product Model byte (identifies device)
7-11    5     ??            Reserved
12      1     01            Command echo (must be 1)
13-63   52    ??            Device-specific data
```

**Validation:**
- `response[0:4]` must equal `DA DB DC DD`
- `response[12]` must equal `1` (handshake echo)

**Key bytes:**
- `response[6]` = **PM** (product model) — used to select LED style
- `response[5]` = **SUB** (sub-type) — disambiguates devices sharing a PM byte

**Firmware limitation:** The handshake only works **once per power cycle**. Subsequent init packets receive no response. The Linux implementation caches handshake results to `~/.config/trcc/led_probe_cache.json`.

## PM → Style Mapping

From `FormLEDInit()` (FormLED.cs line 1598):

| PM Byte | Style | Model | LED Count | Segments | Zones | Background |
|---------|-------|-------|-----------|----------|-------|------------|
| 1 | 1 | FROZEN HORIZON PRO | 30 | 10 | 1 | D0数码屏 |
| 2 | 1 | FROZEN MAGIC PRO | 30 | 10 | 1 | D0数码屏 |
| 3 | 1 | AX120 DIGITAL | 30 | 10 | 1 | D0数码屏 |
| 16-31 | 2 | PA120 DIGITAL | 84 | 18 | 4 | D0数码屏4区域 |
| 32 | 3 | AK120 DIGITAL | 64 | 10 | 2 | D0数码屏 |
| 48-49 | 5 | LF8 | 93 | 23 | 2 | D0LF8 |
| 80 | 6 | LF12 | 124 | 72 | 2 | D0LF12 |
| 96 | 7 | LF10 | 116 | 12 | 3 | D0LF10 |
| 112 | 9 | LC2 | 61 | 31 | 1 | D0LC2 |
| 128 | 4 | LC1 | 31 | 14 | 1 | D0LC1 |
| 128 (sub=129) | 13 | HR10 2280 PRO DIGITAL | 31 | 14 | 1 | D0数码屏 |
| 129 | 10 | LF11 | 38 | 17 | 1 | D0LF11 |
| 144 | 11 | LF15 | 93 | 72 | 2 | D0LF15 |
| 160 | 12 | LF13 | 62 | 62 | 1 | D0rgblf13 |
| 208 | 8 | CZ1 | 18 | 13 | 4 | D0CZ1 |

**LED Count vs Segment Count:** LEDs are the physical addressable units. Segments are logical groups shown in the GUI. For style 1: 30 LEDs, 10 segments = 3 LEDs per segment.

**Sub-type disambiguation:** PM=128 is shared by LC1 (sub=0) and HR10 (sub=129). The `SUB_TYPE_OVERRIDES` table resolves this.

## LED Data Packet

### Header (20 bytes)

From `SendHidVal()` (FormLED.cs line 4309):

```
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

```
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

Off LEDs send `(0, 0, 0)`.

### Complete Packet

```
[20-byte header] + [N × 3 bytes RGB payload]
```

Example for style 1 (30 LEDs):
- Header: 20 bytes
- Payload: 30 × 3 = 90 bytes
- Total: 110 bytes → chunked into 2 HID reports (64 + 46 padded to 64)

## HID Report Chunking

From `ThreadSendDeviceData1()` (UCDevice.cs line 983):

The complete packet is split into 64-byte HID reports:

```
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

Six effect modes, each implemented as a timer function in FormLED.cs. The timer fires every ~30ms.

### Mode 0: Static (DSCL_Timer)

All segments set to the user-selected color `(rgbR1, rgbG1, rgbB1)`.

```
for each segment i:
    color[i] = (rgbR1, rgbG1, rgbB1)
```

### Mode 1: Breathing (DSHX_Timer)

Fade in/out with a 66-tick cycle (period ≈ 2 seconds at 30ms ticks).

```
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

```
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

```
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

Styles 2, 3, 5, 6, 7, 8, 11 have 2-4 independent zones. Each zone has its own:
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

## HR10 2280 PRO Digital (Style 13)

The HR10 is a NVMe SSD heatsink with a 31-LED 7-segment display. It shares PM=128 with LC1 but is distinguished by `sub_type=129` in the handshake response.

### Physical LED Layout

```
Wire positions (left → right on the physical heatsink):

[Digit4] [Digit3] [Digit2] [°] [Digit1] [MB/s] [%]

LED Index:  0    1    2    3    4    5    6    7    8    9   10   ...   30
Purpose:  MB/s  %   [----Digit 1----] [°]  [----Digit 2----] ...
```

| LED Index | Purpose |
|---|---|
| 0 | MB/s indicator |
| 1 | % indicator |
| 2-7, 9 | Digit 1 (rightmost): segments c, d, e, g, b, a, f |
| 8 | ° (degree) indicator |
| 10-16 | Digit 2: segments c, d, e, g, b, a, f |
| 17-23 | Digit 3: segments c, d, e, g, b, a, f |
| 24-30 | Digit 4 (leftmost): segments c, d, e, g, b, a, f |

### 7-Segment Wire Order

Each digit's 7 segments are wired in the order: **c, d, e, g, b, a, f**

Standard 7-segment labeling:
```
 aaa
f   b
f   b
 ggg
e   c
e   c
 ddd
```

### Character Encoding

| Char | Segments ON |
|---|---|
| 0 | a, b, c, d, e, f |
| 1 | b, c |
| 2 | a, b, d, e, g |
| 3 | a, b, c, d, g |
| 4 | b, c, f, g |
| 5 | a, c, d, f, g |
| 6 | a, c, d, e, f, g |
| 7 | a, b, c |
| 8 | a, b, c, d, e, f, g |
| 9 | a, b, c, d, f, g |

### Display Modes

The HR10 GUI panel shows drive metrics via the 7-segment display:

| Mode | Indicators | Example |
|---|---|---|
| Temperature | ° | "52C" with ° lit |
| Activity | % | "87" with % lit |
| Read speed | MB/s | "1250" with MB/s lit |
| Write speed | MB/s | "830" with MB/s lit |

### NVMe Temperature Daemon

`trcc hr10-tempd` runs as a standalone daemon:
1. Reads NVMe temperature from `/sys/class/hwmon/hwmon*/temp1_input` (matched by drive model string)
2. Converts to color using a thermal gradient (blue→cyan→green→yellow→orange→red)
3. Applies breathe animation that speeds up as temperature rises
4. Sends to HR10 via HID every animation tick
5. Fast red blink above throttle threshold (~80°C)

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

**Cache file:** `~/.config/trcc/led_probe_cache.json`

```json
{
    "0416_8001": {
        "pm": 3,
        "sub_type": 0,
        "model_name": "AX120_DIGITAL",
        "style_id": 1
    },
    "0416_8001_2-1.4": {
        "pm": 128,
        "sub_type": 129,
        "model_name": "HR10_2280_PRO_DIGITAL",
        "style_id": 13
    }
}
```

Cache keys include the USB bus path (e.g. `2-1.4`) to disambiguate multiple devices with the same VID:PID.

## Linux Implementation Files

| File | Purpose |
|---|---|
| `led_device.py` | Constants, styles, PM mapping, packet builder, HID sender, probe cache |
| `device_factory.py` | `LedProtocol` class (observer pattern, transport management) |
| `controllers.py` | `LEDController` (effect engine), `LEDDeviceController` (orchestrator) |
| `models.py` | `LEDMode`, `LEDState`, `LEDModel` (MVC state + tick computation) |
| `hr10_display.py` | HR10 7-segment renderer (text → 31-LED color array) |
| `hr10_tempd.py` | HR10 NVMe temperature daemon |
| `uc_led_control.py` | PyQt6 GUI panel (all LED styles 1-13) |
| `uc_screen_led.py` | LED segment visualization widget |
| `uc_color_wheel.py` | HSV color wheel widget |
| `uc_seven_segment.py` | 7-segment display preview widget |

## See Also

- [USBLCD_PROTOCOL.md](USBLCD_PROTOCOL.md) — SCSI protocol from USBLCD.exe
- [USBLCDNEW_PROTOCOL.md](USBLCDNEW_PROTOCOL.md) — USB bulk protocol from USBLCDNEW.exe
- [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md) — Full project technical reference

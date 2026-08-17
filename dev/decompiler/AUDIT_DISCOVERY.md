# AUDIT — Device Discovery & Connection

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=none -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> 13 method(s) documented here changed in TRCC 2.1.6 and have NOT been re-read: `AddhidDeviceList`, `CheckDirectoryExist`, `DelegateUCAbout`, `DelegateUCDevice`, `DeviceDataReceived1`, `DeviceDataReceived2`, `DeviceOnConnected1`, `DeviceOnConnected2`, `InitializeComponent`, `KaijiQidong`, `Timer_Form_event`, `Timer_RGB_LCD_event` (+1 more) — read those entries as TRCC 2.0.3 history.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Sources (verbatim, line-cited):
- `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC/Form1.cs` (2,200 lines)
- `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC/UCDevice.cs` (1,747 lines)

Every claim below quotes the exact source line(s). Where a value lives in a Form
not in scope (`FormCZTV`, `FormLED`, `FormSystemInfo`, external `USBLCD*.exe`), it
is flagged **out-of-scope** — not inferred.

---

## 1. Purpose & lifecycle

### 1.1 The two device transports are architecturally split
TRCC discovers devices through **two entirely separate channels**:

1. **HID channel** — owned by `UCDevice` (`UsbHidDevice` objects `device1..4`).
   In-process enumeration, connect, handshake, data receive.
2. **Shared-memory channel** — owned by `Form1`, fed by **external processes**
   `USBLCD.exe` / `USBLCDNEW.exe` that own the SCSI/SPI LCD wire and write frames
   into named memory-mapped files. `Form1` polls that shared memory and
   synthesizes `FormCZTV` windows from magic bytes.

### 1.2 Startup sequence — `Form1()` ctor
The constructor (`Form1.cs:272-317`) runs this order, and the order is load-bearing
— both shared-memory maps exist and are zeroed before the 15 ms timer that reads
them is ever started:

- `InitializeComponent`, then `ClearButtonBouns` — builds the widgets and clears
  the sidebar button bounds.
- `KaijiQidong` — writes/removes the Windows autostart registry entry.
- `CheckDirectoryExist` — creates the app's data directories if missing.
- `InitMemorySize` — creates the SPI/SCSI memory-mapped file `"shareMemory_Image"`
  at 38,400,000 bytes; `WriteShareMemory` then zeroes the first 4 bytes (writes a
  4-byte zero buffer at offset 0), clearing any stale magic left by a previous run.
- `InitMemorySizeRGB` — creates the RGB memory-mapped file
  `"shareMemory_ImageRGB"` at 34,560,000 bytes; `WriteShareMemoryRGB` zeroes its
  first 4 bytes the same way.
- `InitializeForm` — lays out the main window.
- `formDeviceArray` and `formCZTVArray` are allocated empty — the first holds
  HID-backed Forms indexed by ID block (§5), the second holds the shared-memory
  `FormCZTV`s.
- `m_timer` — a WinForms `Timer` wired to `Timer_event`, `Interval` set to **15 ms**,
  `Enabled` set true and then immediately stopped, so it does not tick during the
  rest of construction.
- Any stray already-running `"USBLCD"` processes are killed before the app claims
  the wire.
- `formStart` — the splash/startup Form is constructed.
- `ucDevice1.Scan_UsbHid` — kicks off HID enumeration (§3.1).
- `DelegateUCAbout` is invoked with command `32` and the current `Language`, null payload.
- `m_timer.Start` — only now does the 15 ms pump begin.
- `SystemEvents.PowerModeChanged` is subscribed to `OnPowerModeChanged` (§1.5).

Shared-memory sizes and names (`Form1.cs:68-90`) — the "size0"/"size1" constants are
one frame, the big ones are the whole ring the helper process writes into:

| Constant | Value | Meaning |
|---|---|---|
| `shareMemorySize0` | 153600 | one SPI/SCSI frame — 320 × 240 × 2 bytes |
| `shareMemorySize` | 38400000 | total size of the SPI/SCSI map |
| `shareMemoryName` | `"shareMemory_Image"` | name of the SPI/SCSI map |
| `shareMemorySize1` | 691200 | one RGB frame — 480 × 480 × 3 bytes |
| `shareMemorySizeRGB` | 34560000 | total size of the RGB map |
| `shareMemoryNameRGB` | `"shareMemory_ImageRGB"` | name of the RGB map |

### 1.3 The 15 ms master timer fans out to three pumps
There is exactly one timer in `Form1`; its handler calls three pumps in a fixed
order on every 15 ms tick, with no guards between them:

- `Timer_event` (`Form1.cs:1182-1187`) — the single tick handler, calling the three below.
- `Timer_Form_event` — ticks every open `FormLED` / `FormCZTV` (their own render/send cadence rides this).
- `Timer_One_LCD_event` — (re)launches the `USBLCD*.exe` helper processes, used when resuming from sleep (§1.5).
- `Timer_RGB_LCD_event` — polls the shared memory maps and synthesizes `FormCZTV` windows from the magic bytes it finds (§4).

### 1.4 Hotplug — Windows `WM_DEVICECHANGE`
- `WndProc` (`Form1.cs:677-690`) — the overridden window-message pump reads the
  message's `WParam` as an int and, when it equals **7**, calls
  `ucDevice1.Scan_UsbHid` to restart HID enumeration. The whole test is wrapped in
  a swallow-everything try/catch, and the base `Form.WndProc` is always called
  afterwards regardless of what happened.

`WParam == 7` is `DBT_DEVNODES_CHANGED`; a USB arrival/removal re-triggers the HID
scan. **This is the only OS hotplug hook; the shared-memory LCD channel has no
hotplug — it is discovered by continuous polling (§4).**

### 1.5 Power / sleep handling
`Form1.cs:319-357` — `OnPowerModeChanged`. `switch (val - 1)`: `case 0` (Suspend)
sets `myPowerMode = true`; `case 2` (Resume) kills `USBLCD` processes, sets
`myPowerMode = false`, calls `ResetAllDevice()`. On resume the LCD helper
processes are re-spawned by `Timer_One_LCD_event` (`Form1.cs:955-1019`, gated on
`myPowerMode` + a 128-tick counter).

---

## 2. VID/PID table & wire routing (the core answer)

All four HID devices are constructed inside the scan timer — `Timer_event`
(`UCDevice.cs:229-272`) builds a `UsbHidDevice` per slot, each taking decimal VID,
decimal PID, that slot's name list, and an optional report length (omitting it
takes the 512-byte default):

- `device1` — VID 1046, PID 32769, name list `hidNameList1`, report length **64**.
- `device2` — VID 1046, PID 21250, name list `hidNameList2`, report length defaulted to **512**.
- `device3` — VID 1048, PID 21251, name list `hidNameList3`, report length **64**.
- `device4` — VID 1048, PID 21252, name list `hidNameList4`, report length defaulted to **512**.

Decimal→hex: 1046=`0x0416`, 1048=`0x0418`, 32769=`0x8001`, 21250=`0x5302`,
21251=`0x5303`, 21252=`0x5304`.

| Slot | VID | PID | report len | list | ID | Device kind | Form opened |
|------|-----|-----|-----------|------|----|-----|-----|
| device1 | 0x0416 | 0x8001 | 64 | hidList1 | 1 | **LED** (RGB / segment) | `FormLED` |
| device2 | 0x0416 | 0x5302 | 512 | hidList2 | 2 | **LCD (HID wire)** | `FormCZTV` (mode 3) |
| device3 | 0x0418 | 0x5303 | 64 | hidList3 | 3 | (routed, no Form body) | none — case empty |
| device4 | 0x0418 | 0x5304 | 512 | hidList4 | 4 | (routed, no Form body) | none — case empty |

The `ID` (1/2/3/4) is the routing key everywhere; the fifth kind, **ID `257`**
(`USB_ID1_1 = 257`, `UCDevice.cs:48`), is the **shared-memory RGB LCD** added by
`RGB_ADD_Device` (`UCDevice.cs:1620-1624`), not a HID device.

**Caveat — ID 3 & 4 are enumerated but inert in these two files.** `Form1`
`DelegateUCDevice` `case 1` only builds a Form for `info==1` (LED) and `info==2`
(LCD).

- `DelegateUCDevice` (`Form1.cs:1198-1200`) — the trailing branch of that chain
  tests that `info` is neither 3 nor 4 and then does nothing at all: the body is
  empty, so IDs 3 and 4 fall out of the routing with no Form and no side effect.
- `SendDeviceData3` (`UCDevice.cs:1537-1539`) and `SendDeviceData4`
  (`UCDevice.cs:1277-1279`) — both are empty method bodies.

### 2.1 report-length constants
- `USB_LEN0` (`UCDevice.cs:74-76`) — the short HID report length, **64**; declared
  alongside `USB_LEN`, the long report length, **512**.

device1/device3 use the 64-byte report; device2/device4 default (512-byte path,
see chunking §3.4).

---

## 3. Handshake dispatch per wire

### 3.1 HID scan lifecycle — `UCDevice.Scan_UsbHid` + poll timer
- `Scan_UsbHid` (`UCDevice.cs:173-179`) — takes one bool parameter defaulting to
  true, and does nothing but reset the scan state machine: `scanCount` back to
  **0**, `timerStartCount` back to **0**, `timerStartCountVal` to **20**, and
  `timerStart` set to the passed bool. It never touches USB itself — the timer
  below does the work on its next tick.

100 ms `System.Timers.Timer` (`UCDevice.cs:161-164`). `Timer_event`
(`UCDevice.cs:181-276`) counts up to `timerStartCountVal`, does up to 3 scan
passes (`scanCount` 1→val=2, 2→val=30, `>=3`→stop & dispose all four devices),
otherwise constructs/`Connect()`s each `deviceN`. After a successful add the poll
value is bumped to keep-alive `300` (`DeviceDataReceived1:960`,
`DeviceDataReceived2:1083`).

### 3.2 LED (device1, ID 1) — connect handshake
- `DeviceOnConnected1` (`UCDevice.cs:925-939`) — on connect it builds one 20-byte
  buffer, wraps it in a `CommandMessage`, and sends it with `device1.SendMessage`.
  The buffer is `218, 219, 220, 221` followed by eight zero bytes, a `1` at index
  12, then seven trailing zero bytes.

Magic prefix = `0xDA 0xDB 0xDC 0xDD`, byte[12]=`1`.

### 3.3 LED (device1) — response fingerprint
- `DeviceDataReceived1` (`UCDevice.cs:954-981`) — enqueues event `4096` with the
  received data, then derives a display name. If `data[17] == 16` **and** the
  buffer is at least 37 bytes long, the serial is the hex of every byte from index
  **21** to the end, with the separating dashes stripped, and that string is added
  to the name list. Otherwise it falls back to a sanitized form of the HID
  `DevicePath`.

The **pm/sub fingerprint** is consumed in `AddhidDeviceList` (§3.6): `receive[6]`
= pm, `receive[5]` = sub.

### 3.4 LCD-HID (device2, ID 2) — connect handshake
- `DeviceOnConnected2` (`UCDevice.cs:1045-1064`) — allocates a 512-byte all-zero
  buffer and wraps it in a `CommandMessage`, spins an empty loop of **400**
  iterations as a timing spacer, then builds the same 20-byte probe as the LED
  path (`218, 219, 220, 221`, eight zeros, `1` at index 12, seven trailing zeros)
  and sends it with `device2.SendMessage`.

Same 20-byte `0xDA DB DC DD … [12]=1` probe as LED, preceded by a 512-byte zero
frame + a 400-iteration spin.

### 3.5 LCD-HID (device2) — response validation & fingerprint
- `DeviceDataReceived2` (`UCDevice.cs:1079-1125`) — accepts a buffer as a
  handshake reply only when it is non-empty, the reporting device is `device2`
  itself, the magic reads `218, 219, 220, 221` at indices **1, 2, 3, 4**, and
  `data[13] == 1`; that path enqueues event `4097` with the data. Anything whose
  `data[13]` is neither 1 nor **8** returns without doing anything.

**Validated magic offsets differ from LED — here the prefix sits at data[1..4],
not data[0..3].**

`data[13]==1` = handshake reply → device add; `data[13]==8` = runtime device
data → routed as event `8193` with `data[9]` set to the list index
(`UCDevice.cs:1111-1123`).

### 3.6 The device-add ACK + pm/sub extraction — `AddhidDeviceList`
- `AddhidDeviceList` (`UCDevice.cs:813-853`) — builds a 14-byte acknowledgement
  seeded with `220, 221, 1` then zeros, a `1` at index 11, and two trailing zeros,
  and patches three fields into it: index **4** takes the low byte of `ID`, index
  **5** the high byte (`ID >> 8`), and index **10** the current device-list count.
  It wraps that in a `CommandMessage` and then splits on ID.
  - When `ID == 4` it actually sends the ACK to the device, calls `ADDUserButton`
    with the hardcoded pair pm **50** / sub **0** (no fingerprint is read from the
    device at all), and fires `delegateUcDevice` — null-conditionally — with
    command `1` and the ID only.
  - For every other ID it does **not** send the ACK; it calls `ADDUserButton` with
    pm taken from `receive[6]` and sub from `receive[5]`, and fires
    `delegateUcDevice` with command `1`, the ID, that same `receive[6]` pm byte,
    and the resolved device name.

**Fingerprint bytes located here:** `receive[6]` = **pm** (product-model byte),
`receive[5]` = **sub**. These drive both the sidebar icon (`ADDUserButton`, §5)
and the Form (`FormLEDInit((byte)data,…)` / `FormCZTVInit((byte)data,…)`).

### 3.7 device3 / device4 — no probe on connect
`DeviceOnConnected3` (`UCDevice.cs:1499-1510`) and `DeviceOnConnected4`
(`1239-1250`) send **no** handshake bytes; they only post events `4098`/`4099`.
`DeviceDataReceived3/4` (`1223-1233`, `1265-1275`) forward raw data as `8194`/`8195`.
`AddhidDeviceList` for ID 4 sends the 14-byte ACK (the `ID == 4` branch above).

### 3.8 Where fbl / mode / count / ysl come from
**Only pm (`receive[6]`) and sub (`receive[5]`) are read in these two files.**
`fbl`, `mode`, `count`, `ysl` are **out-of-scope** — they are parsed inside
`FormCZTV.FormCZTVInit(...)` / `FormCZTV.DeviceDataReceived(...)` and
`FormLED.FormLEDInit(...)`, which receive the raw `data[]`. `Form1` routes the raw
byte[] to them without inspecting it:

- `DelegateUCDevice` `case 2` (`Form1.cs:1202-1232`) — for the LCD-HID branch it
  computes the Form index as the LED block size (`hidList1.Count`) plus the
  payload's own byte at index **9**, pulls the `FormCZTV` out of the device array
  at that slot, and hands it the raw byte[] through `FormCZTV.DeviceDataReceived`.
  `Form1` parses none of the frame itself.

---

## 4. Shared-memory LCD discovery (SCSI/SPI via USBLCD*.exe)

`Form1.Timer_RGB_LCD_event` (`Form1.cs:656-811`) polls the two MMFs and pattern-
matches magic bytes to synthesize `FormCZTV`. Four distinct signatures:

All offsets below are into `shareMemoryValRGB` (the RGB map) or `shareMemoryVal`
(the SPI map); "count" is that buffer's running Form counter.

| Buffer | Magic test — all conditions must hold | `FormCZTVInit` arguments, in order | line |
|---|---|---|---|
| RGB | byte 6 is 220, byte 2 is 72, and byte 0 is either 0 while byte 7 is not 85, or exactly 1, or in the inclusive range 4–12 | 72, 2, count, 95, byte 4, name text, byte 1 | 679, 706 |
| RGB | byte 6 is 221, byte 5 is 220, byte 7 is 220 | byte 0, 10, count, 95, 1, name text | 713, 740 |
| RGB | byte 6 is 220, byte 2 is 54 | byte 1, 3, count, 95, 100, name text, byte 0 | 747, 774 |
| SPI | byte 153598 is 220 | byte 153599, 1, SPI count | 787, 805 |

Header metadata read from RGB buffer (`Form1.cs:689`, `706`, `708`):

- name decode (`Form1.cs:689`) — the device name is UTF-8 decoded out of
  `shareMemoryValRGB` starting at offset **9**, for a length given by the buffer's
  own byte at offset **8**.
- Form construction (line `706`) — `FormCZTVInit` is called on the new `FormCZTV`
  with, in order: **72**, the wire mode **2**, the running RGB Form count
  `formCZTVRgbArrayCount`, **95**, pm from buffer byte 4, the decoded name, and
  sub from buffer byte 1.
- sidebar registration (line `708`; `Form1.cs:1073` in 2.1.6) —
  `ucDevice1.RGB_ADD_Device` is called with the same two fingerprint bytes, pm =
  buffer byte 4 and sub = buffer byte 1.

So for the shared-memory path the **second `FormCZTVInit` arg is the wire "mode"**:
`1`=SPI, `2`, `3`=HID, `10`. pm/sub for the sidebar come from buffer offsets that
differ per signature (`[4]/[1]`, `[0]/0`, `[1]/[0]`, `[153599]/0`).

**Caveat — magic bytes are `0xDA/0xDB/0xDC/0xDD` = 218/219/220/221.** Header
constants `USB_PACKED_Head=220 (0xDC)`, `USB_PACKED_Head1=221 (0xDD)`
(`UCDevice.cs:50-52`). The 20-byte HID probe additionally leads with `218 219`
(`0xDA 0xDB`).

- `RGB_ADD_Device` (`UCDevice.cs:1620-1624`) — takes a pm byte defaulting to **50**
  and a sub byte defaulting to **0**, calls `ADDUserButton` with the synthetic ID
  **257** and that pm/sub pair, and appends the new device's 1-based position
  (current `rgbList` count plus 1) to `rgbList`. It performs no I/O — the
  shared-memory poll is the only thing that "discovers" these panels.

---

## 5. Device → Form routing table (`Form1.DelegateUCDevice`)

`Form1.cs:1149-1201` `case 1` (device connected):
- `info == 1` → `new FormLED()`, `formLED.FormLEDInit((byte)data, 1, hidList1.Count, name)` (`1156-1171`); delegate `DelegateFormLED`.
- `info == 2` → `new FormCZTV()`, delegate `DelegateFormCZTVHid`, `FormCZTVInit((byte)data, 3, hidList2.Count, 95, 100, name)` (`1176-1196`).
- `info == 3 || 4` → empty (`1198-1200`).

`case 2` (device data received) `Form1.cs:1202-1232` — routes by the same 1/2/3/4
switch, index = `hidList*.Count + data[9|11]`.

`formDeviceArray` layout is **ordered by ID block**: LED block `[0 .. hidList1.Count)`,
then LCD-HID block, then hidList3, then hidList4 (see index math at
`Form1.cs:1096`, `1102`, `1111`, `1116`, `1183`, `1213`, `1221`, `1227`). RGB
shared-memory `FormCZTV`s live in the separate `formCZTVArray`.

### 5.1 pm → product name (sidebar) — the fullest device table
`UCDevice.ADDUserButton` (`UCDevice.cs:317-749`) switches `ID` then `pm` (and often
`sub`) to pick a bitmap. This is the richest VID/PID→model map in the codebase.
Representative (verbatim mapping, `switch (pm)`):

- **ID 1 (LED, 0x0416/0x8001)** `UCDevice.cs:334-424`: pm 1=FROZEN_HORIZON_PRO,
  2=FROZEN_MAGIC_PRO, 3=AX120_DIGITAL, 16=PA120_DIGITAL, 23=RK120_DIGITAL,
  32=AK120_Digital, 48=LF8, 49=LF10, 80=LF12, 96=LF10, 112=LC2, 128=LC1, 129=LF11,
  144=LF15, 160=LF13, 208=CZ1, default=KVMALEDC6.
- **ID 2 (LCD-HID, 0x0416/0x5302)** `UCDevice.cs:426-493`: pm 36=AS120_VISION,
  50/51=FROZEN_WARFRAME, 52/53=BA120_VISION, 54=LC5, 58(sub0=FROZEN_WARFRAME_SE /
  else LM26), 100=FROZEN_WARFRAME_PRO, 101=ELITE_VISION, 128=LM24, default=CZTV.
- **ID 257 (shared-mem RGB LCD)** `UCDevice.cs:505-720`: large pm×sub matrix
  (pm 1 sub≤1=GRAND_VISION, sub48=LM22, sub49=LF14; pm3=CORE_VISION;
  pm4 sub1=HYPER_VISION/sub2=RP130_VISION/sub3=LM16SE; pm5=Mjolnir_VISION;
  pm6 sub1=FROZEN_WARFRAME_Ultra/sub2=FROZEN_VISION_V2; pm7 sub1=Stream_Vision/
  sub2=Mjolnir_VISION_PRO; pm9=LC2JD; pm10 sub5=LF16/sub6=LF18/def=LC3; pm11=LF19;
  pm12=LF167; pm32/50/51/64/65/100/101/128 = further FROZEN/LM/ELITE/LF variants).
- **ID 3, ID 4** `UCDevice.cs:495-504`: both fall through to generic `A1CZTV`.

---

## 6. Reconnect / multi-device / teardown

- **Multi-device**: every kind is a list (`hidList1..4`, `rgbList`,
  `formCZTVArray`); `formDeviceArray` interleaves them by ID block. Buttons in
  `myButtonList` are `Insert`ed at the block offset (`UCDevice.cs:730-747`).
- **Disconnect**: `DeviceOnDisConnectedN` (`UCDevice.cs:1223`, `1066`, `1210`, `1252`)
  post events 0/1/2/3 → `DelhidDeviceList` (`UCDevice.cs:794-811`) removes any
  `!IsDeviceConnected` entry, deletes its button, and fires `delegate(0, ID, num)`
  → `Form1.DelegateUCDevice case 0` disposes the Form (`Form1.cs:1076-1147`).
- **Reconnect after 3 empty scans**: `Timer_event` disposes device1..4 and nulls
  them (`UCDevice.cs:201-226`); the next `Scan_UsbHid` (from hotplug or add)
  reconstructs them.
- **Concurrency guards**: `workingcon` / `workingDis` spin-locks serialize
  connect/disconnect (`UCDevice.cs:927`, `943`, `1047`, etc.); `isTimerIN`
  re-entrancy guard on the scan timer (`UCDevice.cs:183-187`).
- **Full reset** on resume/manual: `ResetAllDevice` (`Form1.cs:1510-1555`) tears
  down every Form, clears both MMF buffers, calls `ucDevice1.Remove_RGB_Device()`.

---

## 7. Magic-byte reference (verbatim constants)

The packet-vocabulary constants are declared together as public bytes on
`UCDevice` (`UCDevice.cs:50-68`) — the first two are the frame header, the rest
are command/subsystem selectors:

| Constant | Decimal | Hex |
|---|---|---|
| `USB_PACKED_Head` | `:220` | 0xDC |
| `USB_PACKED_Head1` | `:221` | 0xDD |
| `USB_PACKED_ONOFF` | 0 | 0x00 |
| `USB_PACKED_STATE` | 1 | 0x01 |
| `USB_PACKED_GET_STATE` | 2 | 0x02 |
| `USB_PACKED_AUDIO` | 4 | 0x04 |
| `USB_PACKED_MOTOR` | 5 | 0x05 |
| `USB_PACKED_FAN` | 6 | 0x06 |
| `USB_PACKED_LED` | `:16` | 0x10 |
| `USB_PACKED_LCD` | `:48` | 0x30 |

- HID connect probe: `DA DB DC DD 00×7 01 00…` (20 bytes), reply valid when
  `data[13]==1` (LCD) / consumed via `receive[6]=pm,[5]=sub` (LED).
- Device-add ACK: `DC DD 01 00 [ID] [ID>>8] 00×4 [count] 01 00 00` (14 bytes).
- Shutdown/clear sentinel written to RGB MMF: `AA BB CC DD` = `170 187 204 221`
  (`Form1.cs:513`, `1561`).

---

## 8. Confidence & gaps

- **HIGH** confidence on: the 4 VID/PID rows + report lengths, the ID→Form routing,
  the connect handshake byte arrays, the pm=`receive[6]`/sub=`receive[5]`
  fingerprint, the shared-memory magic-byte signatures, hotplug via `WParam==7`.
- **Cannot resolve from these two files (out-of-scope):**
  - `fbl`, `mode`, `count`, `ysl` field parsing — lives in `FormCZTV` / `FormLED`.
  - The `FormCZTVInit` / `FormLEDInit` parameter *semantics* (arg 2 = wire mode is
    inferred from call-site consistency, but the signature is defined elsewhere).
  - The SCSI/SPI wire handshake proper — performed by external `USBLCD.exe` /
    `USBLCDNEW.exe`, not present in this decompile scope.
  - Whether `data[17]==16` (LED serial gate) corresponds to `USB_PACKED_LED=16`
    is likely but not proven within these files.

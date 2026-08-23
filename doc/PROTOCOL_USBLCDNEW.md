# USBLCDNEW.exe — USB Bulk Protocol Reference

Decompiled from `USBLCDNEW.exe` (.NET 8.0 / C#) via ILSpy.
This binary handles **non-SCSI LCD devices** on Windows via LibUsbDotNet raw USB bulk transfers.

## Overview

```text
Windows:  TRCC.exe ──shared memory──> USBLCDNEW.exe ──LibUsbDotNet──> USB Bulk EP
Linux:    trcc ──────────────────────────────────────────────────────> PyUSB/HIDAPI
```

USBLCDNEW.exe uses raw USB bulk transfers via LibUsbDotNet/WinUSB. On Linux, trcc uses PyUSB
directly — the same protocol commands, different transport library.

## Binary Details

| Property | Value |
|---|---|
| Type | .NET 8.0 (C#), clean decompilation |
| Version | 2.3.0 |
| Size | ~1150 lines decompiled |
| USB API | LibUsbDotNet (→ WinUSB/libusb) |
| Source | TRCC **2.1.6** installer payload → `TRCCCAP/USBLCDNEW.dll` (net8.0; `USBLCDNEW.exe` is the apphost stub, the code is in the .dll) |

## Supported Devices

| VID:PID | Decimal | Handler | Protocol | Write EP | Read EP |
|---|---|---|---|---|---|
| `87CD:70DB` | 34733:28891 | `ThreadSendDeviceData` | Magic `0x12345678` | EP01 OUT | EP01 IN |
| `0416:5302` | 1046:21250 | `ThreadSendDeviceDataH` | DA/DB/DC/DD handshake | EP02 OUT | EP01 IN |
| `0416:5406` | 1046:21510 | `ThreadSendDeviceDataALi` | 0xF5 SCSI-like | EP02 OUT | EP01 IN |
| `0416:5408` | 1046:21512 | `ThreadSendDeviceDataLY` | LY chunked bulk | EP09 OUT | EP01 IN |
| `0416:5409` | 1046:21513 | `ThreadSendDeviceDataLY1` | LY1 chunked bulk | EP02 OUT | EP01 IN |

## Command Type Constants

```csharp
SSCRM_CMD_TYPE_DEV_INFO   = 1   // Device information query
SSCRM_CMD_TYPE_PICTURE    = 2   // Picture/frame data
SSCRM_CMD_TYPE_LOGO       = 3   // Logo/boot screen
SSCRM_CMD_TYPE_OTA        = 4   // Over-the-air update
SSCRM_CMD_TYPE_UPG_STATE  = 5   // Upgrade state
SSCRM_CMD_TYPE_ROTATE     = 6   // Rotation control
SSCRM_CMD_TYPE_SCR_SET    = 7   // Screen settings
SSCRM_CMD_TYPE_BKL_SET    = 8   // Backlight/brightness
SSCRM_CMD_TYPE_LOGO_STATE = 9   // Logo state
```

These constants are defined in the class but used by TRCC.exe when preparing command packets in shared memory. USBLCDNEW.exe itself just forwards whatever TRCC.exe puts in the shared memory buffer.

## Shared Memory (IPC with TRCC.exe)

### Memory Layout

```text
Name:       "shareMemory_ImageRGB"
Total size: 34,560,000 bytes (50 slots × 691,200 bytes/slot)
Slot size:  691,200 bytes
Max devices: 10 (arrayDeviceOnline[10])
```

Each device gets **2 slots** (indexed as `n*2` and `n*2+1`):
- Slot `n*2`: Control bytes (4 bytes) and device info (written back after handshake)
- Slot `n*2+1`: Frame data (up to 691,200 bytes)

### Control Bytes (Slot n*2, offset 0)

| Bytes | Meaning |
|---|---|
| `00 01 01 xx` | Send trigger — device ready, USBLCDNEW reads frame from slot n*2+1 |
| `AA BB CC DD` | Shutdown signal — USBLCDNEW stops all threads |
| `xx xx 00 xx` | Idle — USBLCDNEW polls at 1ms intervals |

After processing a send trigger, USBLCDNEW clears byte[2] to 0.

---

## Protocol 1: Thermalright LCD (`87CD:70DB`)

### USB Configuration

```text
Endpoint Write: EP01 OUT
Endpoint Read:  EP01 IN
```

### Handshake

Send 64 bytes:
```text
Offset  Value
0-3     12 34 56 78    (magic: 0x12345678)
4-55    00 00 ...      (zeros)
56      01             (command: device info query)
57-63   00 00 ...      (zeros)
```

Read 1024 bytes response. Check `response[24] != 0` for valid device.

### Device Info (Written Back to Shared Memory)

Two branches based on `response[56]`:

**Branch A — `response[56] == 0x81` (129):** Reads bytes 48-51 as 4-byte hex string for device name.

```text
9-byte header + 8-char hex string:

Byte  Source          Purpose
0     response[32]    PM (product mode) byte
1     response[36]    Unknown
2     0x48 ('H')      Hardcoded type marker
3     response[40]    Unknown
4     response[24]    Device present flag
5     response[28]    Unknown
6     0xDC (220)      Protocol marker
7     response[20]    Unknown
8     text.Length     Hex string length (8)
9+    hex bytes       BitConverter.ToString(response, 48, 4) — 4-byte hex ID
```

**Branch B — all other `response[56]` values:** Uses device path string as device name.

```text
Same 9-byte header, but:
8     path.Length     Device path string length
9+    path bytes      UTF-8 device path from DevicePath.Split('#')[2]
```

### Frame Send

1. Read 691,200 bytes from shared memory slot `n*2+1`
2. Extract data length from bytes[60:63] (LE uint32) + 64 = total transfer size
3. Send via async USB bulk write to EP01 OUT
4. If transfer size is exact multiple of 512, send zero-length packet (USB bulk transfer protocol requirement)
5. Sleep 15ms

The 64-byte offset suggests the frame data has a 64-byte internal header (prepared by TRCC.exe) followed by the actual pixel data.

---

## Protocol 2: Nuvoton HID LCD (`0416:5302`)

### USB Configuration

```text
Endpoint Write: EP02 OUT    ← NOTE: different endpoint than 87CD
Endpoint Read:  EP01 IN
```

### Handshake (DA/DB/DC/DD)

Send 512 bytes:
```text
Offset  Value
0-3     DA DB DC DD    (magic handshake)
4-11    00 00 ...      (zeros)
12      01             (command: device info)
13-19   00 00 ...      (zeros)
20-511  00 00 ...      (zeros, padding to 512)
```

Read 512 bytes response. Validate:
```text
response[0]  == 0xDA
response[1]  == 0xDB
response[2]  == 0xDC
response[3]  == 0xDD    (echo of handshake magic)
response[12] == 0x01    (success)
response[16] == 0x10    (16 = data present flag)
```

**This is the same DA/DB/DC/DD handshake used in our `device_hid.py`.**

### Device Info (Written Back to Shared Memory)

Serial number extracted from response bytes 20-35 as hex string.

9-byte header + serial hex:

| Byte | Source | Purpose |
|---|---|---|
| 0 | response[4] | PM (product mode) byte |
| 1 | response[5] | SUB byte |
| 2 | 0x36 (54) | Hardcoded FBL marker |
| 3 | response[4] | PM (repeated) |
| 4 | response[5] | SUB (repeated) |
| 5 | 0xDC (220) | Protocol marker |
| 6 | 0xDC (220) | Protocol marker |
| 7 | 0xDC (220) | Protocol marker |
| 8 | serial length | Serial hex string length |
| 9+ | serial bytes | UTF-8 hex serial number |

The FBL value 0x36 (54) maps to 360×360 resolution in TRCC.exe's FBL table. But this may not be the actual FBL — it could be a default that gets overridden by the PM/SUB bytes.

### Frame Send

1. Read frame data from shared memory slot `n*2+1`
2. Extract data length from bytes[16:19] (LE uint32) + 20 = total size
3. Round up to next 512-byte boundary: `(size / 512 * 512) + ((size % 512 != 0) ? 512 : 0)`
4. Send via synchronous USB bulk write to EP02 OUT
5. Sleep 1ms

The 20-byte offset suggests the frame data has a 20-byte internal header (the DA/DB/DC/DD protocol header, prepared by TRCC.exe) followed by pixel data. The 512-byte alignment is required for USB bulk transfers.

---

## Protocol 3: ALi Corp LCD (`0416:5406`)

### USB Configuration

```text
Endpoint Write: EP02 OUT    ← Same as 0416:5302
Endpoint Read:  EP01 IN
```

### Handshake (SCSI-like 0xF5)

Send 16 + 1024 = 1040 bytes:
```text
Header (16 bytes):
Offset  Value
0       F5             (SCSI protocol marker)
1       00             (sub-command: poll)
2       01             (mode flag)
3       00
4-7     BC FF B6 C8    (magic/checksum)
8-11    00 00 00 00
12-15   00 04 00 00    (data size: 0x0400 = 1024)

Payload (1024 bytes):
All zeros
```

Read 1024 bytes response. Check:
```text
response[0] == '6' (0x36) → 240×320 display (frame size 153,600 bytes)
response[0] == 'e' (0x65) → 320×320 display (frame size 204,800 bytes)
response[0] == 'f' (0x66) → other resolution (frame size 204,800 bytes)
```

### Device Info (Written Back to Shared Memory)

Device identifier from response bytes 10-13 as hex string.

9-byte header + identifier hex:

| Byte | Source | Purpose |
|---|---|---|
| 0 | response[0] - 1 | Resolution code ('6'→'5', 'e'→'d', 'f'→'e') |
| 1 | response[10] | ID byte 0 |
| 2 | response[11] | ID byte 1 |
| 3 | response[12] | ID byte 2 |
| 4 | response[13] | ID byte 3 |
| 5 | response[1] | Second response byte |
| 6 | 0xDD (221) | Protocol marker (ALi variant) |
| 7 | 0xDC (220) | Protocol marker |
| 8 | ID hex length | Identifier string length |
| 9+ | ID hex bytes | UTF-8 hex identifier |

Note: `response[0] - 1` converts the resolution byte down by one (`'e'`→`'d'`, `'f'`→`'e'`, `'6'`→`'5'`), matching the USBLCD.exe format convention.

### Frame Send

Frame size depends on resolution code:
- `response[0] == '6'` (0x36): 153,600 bytes (240×320×2)
- all others: 204,800 bytes (320×320×2)

Send 16 + frame_size bytes as one USB bulk write:
```text
Header (16 bytes):
Offset  Value
0       F5             (SCSI protocol marker)
1       01             (sub-command: write)
2       01             (mode: frame data)
3       00             (chunk index: 0)
4-7     BC FF B6 C8    (magic/checksum — same as poll)
8-11    00 00 00 00
12-15   frame_size     (LE uint32)

Payload (frame_size bytes):
RGB565 pixel data
```

After write, reads back 16 bytes (acknowledgment from device).

**Key difference from USBLCD.exe:** The entire frame is sent as ONE bulk transfer (header + all pixel data), NOT chunked into 64 KiB pieces.

### Magic Bytes `BC FF B6 C8`

These appear at bytes[4:7] in both poll and frame headers. They may be a constant protocol identifier or firmware version marker — they are NOT the CRC32 from USBLCD.exe.

---

## Protocol 4: LY LCD (`0416:5408`)

### USB Configuration

```text
Endpoint Write: EP09 OUT    ← Unique endpoint, different from all other protocols
Endpoint Read:  EP01 IN
```

### Handshake

Send 16 + 2032 = 2048 bytes:
```text
Header (16 bytes):
Offset  Value
0       02             (command)
1       FF             (0xFF)
2-7     00 00 ...      (zeros)
8       01             (mode flag)
9-15    00 00 ...      (zeros)

Payload (2032 bytes):
All zeros
```

Read 512 bytes response. Validate:
```text
response[0] == 0x03
response[1] == 0xFF
response[8] == 0x01
```

### Device Info (Written Back to Shared Memory)

ID extracted from response bytes 16-19 as hex string.

9-byte header + hex ID:

| Byte | Source | Purpose |
|---|---|---|
| 0 | 0x08 (8) | Hardcoded |
| 1 | 1 + response[22] | SUB-derived byte |
| 2 | 0x48 ('H') | Hardcoded type marker |
| 3 | 0x01 (1) | Hardcoded |
| 4 | 64 + response[20] | Mode/FBL derived (clamped: if response[20] ≤ 3, treated as 1) |
| 5 | 0x44 (68) | Hardcoded |
| 6 | 0xDC (220) | Protocol marker |
| 7 | 0x70 (112) | Hardcoded |
| 8 | ID hex length | Identifier string length |
| 9+ | ID hex bytes | UTF-8 hex of response[16:19] |

Note: `response[20]` is clamped — if `≤ 3`, it is set to 1 before the calculation.

### Frame Send

Frame length read from slot `n*2+1` bytes[60:63] (LE uint32).
Data begins at offset 64 within the slot (same as 87CD:70DB).

Frame is split into 512-byte chunks with 496 bytes of payload each:

```text
Each chunk (512 bytes):
Offset  Value
0       01             (chunk marker)
1       FF             (0xFF)
2-5     total_size     (LE uint32, full frame byte count)
6-7     chunk_payload  (LE uint16: 496 for non-last, remainder for last chunk)
8       01             (flag)
9-10    total_chunks   (LE uint16)
11-12   chunk_index    (LE uint16)
13-15   00 00 00       (padding)
16-511  pixel_data     (496 bytes of frame data, or remainder for last chunk)
```

Total chunk count is rounded up to next multiple of 4.

Chunks are sent in **4096-byte bursts** (8 chunks at a time). If the remaining data is less than 4096 bytes, a 2048-byte write is used instead. After all chunks, reads 512 bytes acknowledgment from EP01 IN.

---

## Protocol 5: LY1 LCD (`0416:5409`)

### USB Configuration

```text
Endpoint Write: EP02 OUT    ← Same as 0416:5302 and 0416:5406
Endpoint Read:  EP01 IN
```

### Handshake

Send 16 + 496 = 512 bytes:
```text
Header (16 bytes):
Same as LY: {02, FF, 00, 00, 00, 00, 00, 00, 01, 00, 00, 00, 00, 00, 00, 00}

Payload (496 bytes):
All zeros
```

Read 511 bytes response. Validate:
```text
response[0] == 0x03
response[1] == 0xFF
response[8] == 0x01
```

### Device Info (Written Back to Shared Memory)

ID extracted from response bytes 16-19 as hex string.

9-byte header + hex ID:

| Byte | Source | Purpose |
|---|---|---|
| 0 | 50 + response[36] | FBL-derived byte |
| 1 | response[22] | SUB byte |
| 2 | 0x48 ('H') | Hardcoded type marker |
| 3 | 0x01 (1) | Hardcoded |
| 4 | 49 + response[20] | Mode/FBL derived |
| 5 | response[32] | Additional mode byte |
| 6 | 0xDC (220) | Protocol marker |
| 7 | 0x70 (112) | Hardcoded |
| 8 | ID hex length | Identifier string length |
| 9+ | ID hex bytes | UTF-8 hex of response[16:19] |

### Frame Send

Frame length read from slot `n*2+1` bytes[16:19] (LE uint32) — **different offset than LY** (which uses bytes[60:63]).

Same chunked 512-byte packet format as LY (Protocol 4). Same 4096-byte burst sending. Same 512-byte acknowledgment read after completion.

---

## Protocol Comparison

### Device Detection

| Binary | 87CD:70DB | 0416:5302 | 0416:5406 | 0402:3922 | 0416:5408 | 0416:5409 |
|---|---|---|---|---|---|---|
| USBLCD.exe | ✗ | ✗ | ✓ (SCSI) | ✓ (SCSI) | ✗ | ✗ |
| USBLCDNEW.exe | ✓ (USB) | ✓ (USB) | ✓ (USB) | ✗ | ✓ (USB) | ✓ (USB) |

Note: `0416:5406` is handled by BOTH binaries — USBLCDNEW.exe via raw USB, USBLCD.exe via SCSI pass-through. In practice, only one claims the device (WinUSB takes priority over SCSI).

### Handshake Comparison

| Device | Magic | Send Size | Read Size | Key Response Bytes |
|---|---|---|---|---|
| 87CD:70DB | `12 34 56 78` | 64 | 1024 | [24]=present, [32]=PM, [56]=name branch |
| 0416:5302 | `DA DB DC DD` | 512 | 512 | [4]=PM, [5]=SUB, [12]=OK, [16]=0x10 |
| 0416:5406 | `F5 00 01 00` | 1040 | 1024 | [0]='6'/'e'/'f' (resolution) |
| 0416:5408 | `02 FF ...` | 2048 | 512 | [0]=0x03, [1]=0xFF, [8]=0x01 |
| 0416:5409 | `02 FF ...` | 512 | 511 | [0]=0x03, [1]=0xFF, [8]=0x01 |

### Frame Send Comparison

| Device | Length Source | Header | Chunked? | Burst Size | ACK? |
|---|---|---|---|---|---|
| 87CD:70DB | bytes[60:63] + 64 | none (inline) | No (single async) | — | No |
| 0416:5302 | bytes[16:19] + 20 | DA/DB/DC/DD (20B) | No (512-aligned) | — | No |
| 0416:5406 | fixed (153600 or 204800) | F5 header (16B) | No (single sync) | — | Yes (16B) |
| 0416:5408 | bytes[60:63] | 16B per chunk | Yes (496B payload/chunk) | 4096B | Yes (512B) |
| 0416:5409 | bytes[16:19] | 16B per chunk | Yes (496B payload/chunk) | 4096B | Yes (512B) |

### trcc-linux Implementation Mapping

| Feature | USBLCDNEW.exe | trcc-linux | Status |
|---|---|---|---|
| 87CD:70DB via SCSI | N/A (uses USB) | ✓ `device_scsi.py` | Working (SCSI transport) |
| 0416:5302 DA/DB/DC/DD | ✓ | ✓ `device_hid.py` | Matches exactly |
| 0416:5406 via SCSI | N/A (uses USB) | ✓ `device_scsi.py` | Working (SCSI transport) |
| 0416:5406 frame (USB) | ✓ (single bulk) | ✓ (chunked SCSI) | Same data, different transport |
| 0416:5408 LY chunked | ✓ | ✓ `device_ly.py` | Matches exactly |
| 0416:5409 LY1 chunked | ✓ | ✓ `device_ly.py` | Matches exactly |
| Device info writeback | ✓ (shared memory) | ✓ (device_detector) | Equivalent |

---

## Architectural Insight

On Windows, there are two separate USB transport paths running simultaneously:
- **USBLCD.exe** → SCSI pass-through → `DeviceIoControl` → commands wrapped in SCSI CDB
- **USBLCDNEW.exe** → LibUsbDotNet/WinUSB → raw USB bulk transfers → direct commands

On Linux, there is only one path per device type:
- **SCSI devices** → `sg_raw` → usb-storage SCSI Generic → SCSI CDB wrapper
- **HID/Bulk/LY devices** → PyUSB → direct USB bulk transfers

The device firmware accepts the same protocol commands regardless of transport. For HID/bulk/LY devices (`0416:5302`, `0416:8001`, `87AD:70DB`, `0416:5408`, etc.), Linux uses PyUSB directly — matching the USBLCDNEW.exe approach.

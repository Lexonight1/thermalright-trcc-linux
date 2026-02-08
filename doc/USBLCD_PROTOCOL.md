# USBLCD.exe — SCSI Protocol Reference

Reverse-engineered from `USBLCD.exe` (native C++/MFC) via Ghidra decompilation.
This binary handles **SCSI LCD devices** (VID:PID `0402:3922`) on Windows.

For HID/USB bulk devices (`87CD:70DB`, `0416:5406`), see `USBLCDNEW_PROTOCOL.md` (TODO).

## Overview

```
Windows:  TRCC.exe ──shared memory──> USBLCD.exe ──DeviceIoControl──> \\.\X: (SCSI)
Linux:    trcc ──────────────────────────────────> sg_raw ──────────> /dev/sgX (SCSI)
```

USBLCD.exe is a background process that:
1. Detects SCSI LCD devices by scanning drive letters
2. Opens shared memory (`shareMemory_Image`) created by TRCC.exe
3. Polls the device for resolution, waits for frames from TRCC.exe
4. Sends RGB565 frame data to the LCD via SCSI pass-through

On Linux, `trcc` combines both roles — no shared memory needed.

## Binary Details

| Property | Value |
|---|---|
| Type | Native C++ (MFC), NOT .NET |
| Size | ~600 KB |
| Imports | `CreateFileA`, `DeviceIoControl`, `WriteFile`, `ReadFile`, `SetupDiGetClassDevs` |
| USB API | None — uses Windows SCSI pass-through via `DeviceIoControl` |
| Ghidra output | 413K lines at `/home/ignorant/Downloads/TRCCCAPEN/USBLCD.exe.c` |

**Not to be confused with:**
- `USBLCDNEW.exe` — .NET binary using LibUsbDotNet/WinUSB for `87CD:70DB` and `0416:5406`
- `TRCC.exe` — Main .NET GUI application

## SCSI Transport

USBLCD.exe uses two Windows IOCTLs:
- `IOCTL_SCSI_PASS_THROUGH` (0x4D004) — for device detection (INQUIRY)
- `IOCTL_SCSI_PASS_THROUGH_DIRECT` (0x4D014) — for frame data transfer

On Linux, both map to `sg_raw` commands to `/dev/sgX`.

## Header Format

All commands use a 20-byte header:

```
Offset  Size  Field
0       4     Command (LE uint32)
4       8     Zeros
12      4     Data size (LE uint32)
16      4     CRC32(bytes[0:15])
```

Only bytes 0-15 are sent as the 16-byte SCSI CDB. The CRC32 is computed but the device firmware does not validate it (tested: zeroed CRC still works).

## CDB Byte Structure

The 4-byte command field encodes a structured protocol:

```
byte[0] = 0xF5    Protocol marker (always)
byte[1] = Sub-command:
    0x00  Poll / read
    0x01  Write / send data
    0x02  Flash erase
    0x04  Flash info query
    0x05  Flash status query
    0x41  'A' (API device identification)
byte[2] = Mode (when byte[1] = 0x01):
    0x00  Init (send 0xE100 zeros to initialize display)
    0x01  Raw frame chunk (byte[3] = chunk index 0-3)
    0x02  Compressed frame (zlib level 3)
    0x03  Multi-frame carousel (compressed, looped)
    0x04  Display clear (black screen)
byte[3] = Index
    Chunk index for raw frames (0x01 mode)
    Frame index for carousel (0x03 mode)
```

## Complete Command Table

### Display Commands (Normal Operation)

| Command (LE) | CDB Bytes | Direction | Size | Purpose |
|---|---|---|---|---|
| `0x000000F5` | `F5 00 00 00` | READ | 0xE100 | Poll device status / resolution |
| `0x000001F5` | `F5 01 00 00` | WRITE | 0xE100 | Initialize display controller |
| `0x000101F5` | `F5 01 01 00` | WRITE | 0x10000 | Frame chunk 0 (64 KiB) |
| `0x010101F5` | `F5 01 01 01` | WRITE | 0x10000 | Frame chunk 1 (64 KiB) |
| `0x020101F5` | `F5 01 01 02` | WRITE | 0x10000 | Frame chunk 2 (64 KiB) |
| `0x030101F5` | `F5 01 01 03` | WRITE | varies | Frame chunk 3 (remainder) |
| `0x000201F5` | `F5 01 02 00` | WRITE | varies | Compressed frame (zlib) |
| `0x000301F5` | `F5 01 03 xx` | WRITE | varies | Multi-frame carousel |
| `0x000401F5` | `F5 01 04 00` | WRITE | 0xE100 | Display clear (black) |

### Firmware / Flash Commands

| Command (LE) | CDB Bytes | Direction | Size | Purpose |
|---|---|---|---|---|
| `0x000002F5` | `F5 02 00 00` | WRITE | 0x10000 | NOR flash erase (sector-aligned) |
| `F5 00` (byte[14]=1) | `F5 00 ... 01 00` | READ | 0x10000 | Flash read (64 KiB) |
| `F5 01` (byte-level) | `F5 01 ...` | WRITE | 0x10000 | Flash write (64 KiB) |
| `0x000004F5` | `F5 04 00 00` | READ | 0x10 | NOR flash info (erase/page/sector/total) |
| `0x000005F5` | `F5 05 00 00` | READ | 4 | Flash status |
| `0x000004F5` | `F5 04 00 00` | READ | 0xC | eMMC info (BOOT0/BOOT1/USER blocks) |

### Device Identification

| Command (LE) | CDB Bytes | Direction | Size | Purpose |
|---|---|---|---|---|
| Standard INQUIRY | `12 00 ... 24` | READ | 0x24 | Standard SCSI INQUIRY (36 bytes) |
| `0x495041F5` | `F5 41 50 49` | READ | 0xC | Device API identification |

## Device Detection Flow

```
1. Scan drive letters \\.\C: through \\.\Z: via CreateFileA
2. Send standard SCSI INQUIRY (CDB opcode 0x12, alloc 0x24)
   → Check response: peripheral device type field
3. Send API identification query:
   CDB: F5 41 50 49 58 B3 0C 00 ...  (cmd + magic 0xB358 + size 12)
   → Read 12-byte device signature
4. FUN_0040c510: Compare 12 bytes against hardcoded DAT_005ee4a8 pattern
   → Store response[2:3] as device variant identifiers
   → (0x01, 0x39) and (0x03, 0x36) are known variants
   → response[5] == 0x10 determines sub-type
5. On match: add to CObList device list with drive path and variant info
```

On Linux: Detection uses `lsusb` + `/sys/class/scsi_generic/` sysfs scan (no drive letters).

## Poll Response

The 0xE100-byte (57,600) poll response contains device state:

| Byte | Value | Meaning |
|---|---|---|
| 0 | `'$'` (0x24) | 240×240 display (mode 1) |
| 0 | `'2'` (0x32) | 320×240 display (mode 2) |
| 0 | `'3'` (0x33) | 320×240 display (mode 2, alternate) |
| 0 | `'d'` (0x64) | 320×320 display (mode 3) |
| 0 | `'e'` (0x65) | 320×320 display (mode 3, alternate) |
| 4-7 | `0xA1A2A3A4` | Device still booting — wait 3 seconds, re-poll |

Note: The alternate codes ('3' vs '2', 'e' vs 'd') may indicate hardware revision or firmware version but map to the same resolution.

## Initialization Sequence

```
1. Poll:  cmd=0xF5,   READ  0xE100 bytes → extract resolution from byte[0]
2. Wait:  if bytes[4:8] == 0xA1A2A3A4, Sleep(3000ms), re-poll
3. Init:  cmd=0x1F5,  WRITE 0xE100 zeros → initialize display controller
4. Ready: device accepts frame data
```

Init is sent once per session. The `DAT_005f4a40` flag tracks init state:
- 0 = not initialized
- 1 = init sent, ready for single frames
- 2 = has received at least one compressed/carousel frame

## Frame Transfer

### Raw Frame Chunks

Each chunk is up to 64 KiB. The number of chunks depends on resolution:

| Resolution | RGB565 Size | Mode | Chunks |
|---|---|---|---|
| 240×240 | 115,200 (0x1C200) | 1 | 2 × 0xE100 |
| 320×240 | 153,600 (0x25800) | 2 | 2 × 0xE100 + 0x9600 |
| 320×320 | 204,800 (0x32000) | 3 | 3 × 0x10000 + 0x2000 |

Note: Mode 1 and 2 use 0xE100 (57,600) chunk size; mode 3 uses 0x10000 (65,536).

### Compressed Frame Transfer

Not implemented in trcc-linux (raw transfer works fine).

1. Compress RGB565 data with zlib level 3 (fast)
2. Send via `0x201F5` with compressed size in header
3. `header[8]` = frame count from shared memory byte[1]
4. Sleep 500ms after send

Compression workspace: 0x4DF40 bytes (~319 KiB), allocated via `FUN_00406080`.

### Multi-Frame Carousel

Not implemented in trcc-linux (carousel uses sequential raw sends instead).

1. For each frame (0 to N-1):
   - Compress frame data for the current resolution mode
   - Command: `0x301F5 | (shared_mem[4+i] << 24)`
   - `header[8]` = frame index
   - Send compressed data
   - Sleep 10ms between frames
2. Frame data offsets in shared memory: stride 0x25800 (mode 1/2) or 0x4B000 (mode 3)

### Display Clear

Send `0x401F5` with 0xE100 bytes of data. Clears LCD to black.

Triggered when shared memory byte[3] == 0x7F.

## Pixel Format

RGB565 big-endian, 2 bytes per pixel:

```python
r5 = (r >> 3) & 0x1F   # 5 bits red
g6 = (g >> 2) & 0x3F   # 6 bits green
b5 = (b >> 3) & 0x1F   # 5 bits blue
pixel = (r5 << 11) | (g6 << 5) | b5  # uint16, stored big-endian
```

Total frame size = width × height × 2 bytes.

## Firmware Update Operations

These commands are used by USBLCD.exe's firmware update dialog. **Not implemented in trcc-linux** (intentionally — firmware updates are risky without official tooling).

### NOR Flash Operations

| Step | Command | Details |
|---|---|---|
| 1. Query info | `F5 04` (READ 0x10) | Returns: erase size, page size, sector size, total size |
| 2. Check status | `F5 05` (READ 4) | Returns: completion status byte |
| 3. Erase sector | `F5 02` (WRITE) | Must be sector-aligned; retries up to 6× on timeout |
| 4. Write chunk | `F5 01` (WRITE 0x10000) | 64 KiB chunks with CRC32 of data in extended header |
| 5. Read back | `F5 00` with byte[14]=1 | Verify written data |

Error messages from the binary: "erase nor flash error, retry timeout!", "write nor flash error, retry timeout!", "image size is larger than nor flash size!"

### Partition Table

`FUN_0040d4d0` builds a 0x4400-byte firmware partition table:
- Offset 0x1FE: MBR signature `0xAA55`
- Partition entries at 0x400+ with name, offset, size, CRC32
- Supports multiple partitions with block-aligned boundaries

### eMMC Info

Command `0x4F5` (READ 0xC × 3 calls) returns:
- BOOT0 area block count
- BOOT1 area block count
- USER area block count

## What USBLCD.exe Does NOT Handle

Searched the entire 413K-line decompilation — confirmed absent:

- **Brightness control** — no SCSI commands for brightness. TRCC.exe adjusts image levels before sending.
- **LCD rotation** — no rotation commands. TRCC.exe rotates the image in software before RGB565 conversion.
- **Display power on/off** — only display clear (0x401F5). No standby/wake commands.
- **Model identification** — firmware reports generic "USBLCD / USB PRC System" for all SE/PRO/Ultra variants. The API query (0x495041F5) returns variant bytes but doesn't map to user-visible model names.

## Shared Memory IPC (Windows Only)

TRCC.exe creates named shared memory `shareMemory_Image`. USBLCD.exe opens it via `OpenFileMappingA` / `MapViewOfFile`.

| Offset | Size | Written By | Purpose |
|---|---|---|---|
| 0x0000 | 1 | USBLCD | Resolution code from poll byte[0], or 0x00 = frame ready |
| 0x0001 | 1 | TRCC | Frame count (1=single, N>1=carousel) |
| 0x0002 | 1 | TRCC→1, USBLCD→0 | Send trigger flag |
| 0x0003 | 1 | TRCC | Display clear flag (0x7F = clear) |
| 0x0004-0x0007 | 4 | USBLCD | Boot signature (0xA1A2A3A4 during boot) |
| 0x0004+N | 1 | TRCC | Per-frame byte for multi-frame carousel |
| 0x257FE | 1 | USBLCD | Resolution echo: 0xDC |
| 0x257FF | 1 | USBLCD | Resolution echo: poll code |
| 0x25800 | varies | TRCC | RGB565 image data |

Not relevant to Linux — trcc sends frames directly to `/dev/sgX`.

## Key Ghidra Functions

| Function | Line | Purpose |
|---|---|---|
| `FUN_004062a0` | 11002 | CRC32 table-driven (identical to Python `binascii.crc32`) |
| `FUN_0040d2b0` | — | CRC32 wrapper for headers |
| `FUN_00406080` | 10883 | zlib/deflate compression (level 3, workspace 0x4DF40) |
| `FUN_0040c510` | 16527 | Device signature validation (12-byte pattern match) |
| `FUN_0040d4d0` | 17361 | Firmware partition table builder (0x4400 bytes, MBR 0xAA55) |
| `FUN_0040d1d0` | — | ASPI SCSI send wrapper |
| Main loop | 20452 | Poll → init → frame send / compressed / carousel |
| Device detect | 16977 | Drive letter scan → INQUIRY → API query → signature check |

## Comparison: Our Implementation vs Windows

| Feature | USBLCD.exe | trcc-linux (`scsi_device.py`) | Match? |
|---|---|---|---|
| Poll (0xF5, READ 0xE100) | ✓ | ✓ | Exact |
| Init (0x1F5, WRITE 0xE100 zeros) | ✓ | ✓ | Exact |
| Frame chunks (0x101F5 + index<<24) | ✓ | ✓ | Exact |
| CRC32 in header | ✓ (computed) | ✓ (computed) | Exact (device ignores it) |
| 320×320 frame send | ✓ (4 chunks) | ✓ (4 chunks) | Exact |
| 240×240 frame send | ✓ (2 chunks @ 0xE100) | ✓ (2 chunks @ 0x10000) | Chunk sizes differ* |
| 320×240 frame send | ✓ (3 chunks @ 0xE100) | ✗ (not supported) | No known devices |
| Compressed frame (0x201F5) | ✓ | ✗ | Not needed |
| Carousel (0x301F5) | ✓ | ✗ | Uses raw sends instead |
| Display clear (0x401F5) | ✓ | ✗ | Could add |
| Device API ID (0x495041F5) | ✓ | ✗ | Can't distinguish models anyway |
| Flash firmware update | ✓ | ✗ | Intentionally not implemented |

*240×240 chunk size: Windows uses 0xE100 (57,600) per chunk; our code uses 0x10000 (65,536). Both work because the device accepts any chunk size ≤ 64 KiB — the total data matters, not individual chunk boundaries.

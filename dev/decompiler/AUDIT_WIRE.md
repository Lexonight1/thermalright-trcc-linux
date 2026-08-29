# AUDIT — the wire: bulk / H / ALi / LY / LY1 senders and the shared-memory bridge

<!-- audit-state: origin=2.1.6.0 addresses=2.1.6.0 -->
> **Audited against the release we port; every citation read from
> `core.csharp.DECOMPILE_ROOT`'s companion binary, not re-anchored onto it.**
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Line-cited audit of `DCReadWriteAsync.cs` (1,366 lines, 11 methods, 112
branches) in the `USBLCDNEW` component. **This is the layer that actually
speaks USB.** The main assembly composes and encodes a frame, writes it to
shared memory, and this process picks it up and puts it on the wire — which is
why `oracle-spec.json` recorded `bulk`, `ly` and `scsi` as absent for so long.
They were never in the main assembly to find.

**Scope, stated up front.** `USBLCD.exe` (2.7 MB) and `USBLCDNEW.exe` (164 KB)
are NATIVE — no CLR directory, so no decompile. **SCSI is not here and is not
answered by this document.** `UsbHid.dll` is a generic third-party HID wrapper
(SetupApi/HidD/Kernel32 P/Invoke) with no vendor identifiers and no protocol; it
has no oracle value.

## Entry point

- `Main` (ReadWriteAsync.cs:5) — the whole process: construct `DCReadWriteAsync`
  and call `StratDCReadWriteAsync`. No argument parsing, no configuration, no
  exit path. The component is a pure shared-memory-to-USB pump, which is why the
  main assembly can neither configure it nor learn anything from it except
  through the mapping.

## The shared-memory bridge

- `InitMemorySizeRGB` (DCReadWriteAsync.cs:68) — opens the existing mapping
  `shareMemory_ImageRGB`. Never creates it: the main assembly owns its lifetime,
  and this process returns immediately if it is absent (`:96`).
- `ReadShareMemoryRGB` (DCReadWriteAsync.cs:73) — page `n` at offset
  `n * 691200`, default count the full page.
- `WriteShareMemoryRGB` (DCReadWriteAsync.cs:81) — same addressing, write side.
- `CloseShareMemoryRGB` (DCReadWriteAsync.cs:89) — disposes the handle.

Geometry, from the literals rather than inferred: page `shareMemorySize1 =
691200`, `shareMemorySizeCountRGB = 50`, total `shareMemorySizeRGB = 34560000`
— and `50 x 691200 = 34560000` exactly. **691,200 is a page size, not a
resolution**; it is the frame-buffer stride the bridge moves, and both
`480x480x3` and `640x360x3` equal it, so no geometry may be inferred from it.

**Each device owns TWO pages**: `n * 2` is the control/handshake page and
`n * 2 + 1` carries the frame (`:428`, `:429`). With 10 device slots
(`arrayDeviceOnline`, `:34`) that is 20 of the 50 pages.

**Control words on page `n*2`**, read 4 bytes at a time:
- `AA BB CC DD` — power off; every thread exits (`:414`).
- `00 01 01` — a frame is ready on page `n*2+1`. The reader clears byte 2 and
  writes the word back before consuming the frame (`:427`), so the flag is an
  ack-on-take, not a queue.

## Device dispatch

- `StratDCReadWriteAsync` (DCReadWriteAsync.cs:94) — polls `UsbDevice.AllDevices`
  every second forever, opens each match to prove it is claimable, closes it, and
  starts ONE `AboveNormal` thread per device keyed on VID/PID. Identity is
  `DevicePath.Split('#')[2]` — the OS instance id, not a USB serial — and it is
  what re-opens the device inside the thread. Slots fill lowest-free-first and
  free on thread exit.

| VID | PID | sender | citation |
|---|---|---|---|
| `0x87AD` | `0x70DB` | bulk | `ThreadSendDeviceData` (DCReadWriteAsync.cs:271) |
| `0x0416` | `0x5302` | H | `ThreadSendDeviceDataH` (DCReadWriteAsync.cs:491) |
| `0x0416` | `0x5406` | ALi | `ThreadSendDeviceDataALi` (DCReadWriteAsync.cs:663) |
| `0x0416` | `0x5408` | LY | `ThreadSendDeviceDataLY` (DCReadWriteAsync.cs:859) |
| `0x0416` | `0x5409` | LY1 | `ThreadSendDeviceDataLY1` (DCReadWriteAsync.cs:1121) |

## Per-wire shape

Every sender: `SetConfiguration(1)`, `ClaimInterface(0)`, IN endpoint `0x81`,
`Sleep(50)` before the handshake and `Sleep(200)` after, then a frame loop.
What differs:

| | bulk | H | ALi | LY | LY1 |
|---|---|---|---|---|---|
| OUT endpoint | `0x01` | `0x02` | `0x02` | **`0x09`** | `0x02` |
| handshake bytes | 64 | 20 | 16 | 16 | 16 |
| reply buffer | 1024 | 512 | 1024 | 512 | **511** |
| frame header | 64 | 20 | 16 | 64 | 20 |
| length field | `[60..63]` LE | `[16..19]` LE | — | `[60..63]` LE | `[16..19]` LE |
| write strategy | one transfer | 512-rounded | — | 4096 chunks | fill loop |

- `ThreadSendDeviceData` (DCReadWriteAsync.cs:271) — the bulk wire. Handshake is
  64 bytes opening `12 34 56 78` with `01` at offset 56 (`:321`). Frame length is
  little-endian at header `[60..63]` **plus 64** for the header itself (`:430`),
  submitted as ONE transfer. **Sends a zero-length packet when the total is a
  multiple of 512** (`:443`) — the standard USB short-packet terminator, and a
  real requirement rather than an optimisation. Paces at `Sleep(15)` (`:447`).
- `ThreadSendDeviceDataH` (DCReadWriteAsync.cs:491) — 20-byte header, length at
  `[16..19]` plus 20, then **rounded UP to a 512 multiple** (`:624`) rather than
  terminated with a ZLP. Different solution to the same problem as the bulk ZLP.
- `ThreadSendDeviceDataALi` (DCReadWriteAsync.cs:663) — 16-byte handshake, OUT
  `0x02`, 1024-byte reply. Publishes a 16-byte record (`:805`).
- `ThreadSendDeviceDataLY` (DCReadWriteAsync.cs:859) — the only sender on OUT
  endpoint **`0x09`**. Splits the frame into `n` packets each carrying a 16-byte
  sub-header — total length at `+2..+5`, payload size at `+6..+7`, constant `1`
  at `+8`, packet count at `+9..+10`, packet index at `+11..+12`, payload from
  `+16` (`:1040`). Payload is read from frame offset `64 + i * size`.
  **Rounds the PACKET COUNT up to a multiple of 4** (`:1053`), then writes 4096
  at a time (2048 for the tail) and reads a 512-byte acknowledgement (`:1085`).
- `ThreadSendDeviceDataLY1` (DCReadWriteAsync.cs:1121) — same packetisation with
  a 20-byte frame header (payload from `20 + i * size`, `:1304`), a fill-style
  write loop with a 1000 ms timeout, and a **511**-byte acknowledgement (`:1330`).

## Findings that bear on our adapters

**The LY1 alignment is dead code.** `num8 = num9 % 1; if (num8 != 0) num9 += 1 -
num8;` (DCReadWriteAsync.cs:1307) — `% 1` is always zero, so the branch can never
be taken. It is LY's `% 4` packet-count alignment (`:1053`) copy-pasted with the
4 changed to a 1, which makes it a no-op. **Any "+1" we carry for this wire is
implementing a vendor typo, not a protocol requirement.**

**Device identity is published to shared memory, and only bulk MEASURES it.**
The bulk sender builds its 9-byte record from the handshake reply — `[array[32],
array[36], 0x48, array[40], array[24], array[28], 0xDC, array[20], namelen]`
followed by the UTF-8 name (`:373`), taking the name from reply bytes `48..51`
when `array[56] == 0x81` and from the OS instance id otherwise (`:390`). It
refuses the device outright when `array[24] == 0` (`:368`). **The other wires
publish CONSTANTS**: H `{0,0,54,0,0,220,220,220,0}` (`:583`), LY
`{8,0,72,1,0,68,220,112,0}` (`:967`), LY1 `{0,0,72,1,0,0,220,112,0}` (`:1220`).
So for those panels the main assembly is reading a hardcoded descriptor, not
something the hardware said.

**This confirms the bulk offsets our conformance test asserts.** `shm[1]` is
`array[36]` and `shm[4]` is `array[24]`, visible directly in the record above.
That claim had been derived from an older release; it holds here.

**`0x0416:0x5302` is the H wire.** That is the VID/PID pair from the quirk work,
and it is served by a distinct sender with a 20-byte header and 512-rounding —
not the bulk path.

## Gaps — what this does NOT establish

- **SCSI**: absent. Native `USBLCD.exe`; needs disassembly, not decompilation.
- **`ThreadSendDeviceDataALi`'s framing**: the handshake, endpoints and record
  are cited above, but its length field and chunking were not traced to the same
  depth as the other four. Treat the blank cells in the table as unread, not as
  "no such field".
- **`Main` cannot be cited, and that is a tooling floor rather than a gap.**
  The citation parser requires a line number of at least two digits
  (`core/citations.py:33`, `\d{2,5}`), and the entry point is at line 5. It is
  described above and was read; it simply cannot be expressed as a citation, so
  this binary reads 10/11 = 90% and cannot reach 100% until that floor moves.
  Widening it touches every doc in the corpus and belongs in its own change,
  measured the way the other citation fixes were.
- **Nothing here is glass-verified.** This is what the vendor's code does, not
  proof that our code matching it produces a correct picture on a panel.

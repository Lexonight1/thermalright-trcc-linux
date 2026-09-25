# AUDIT — the SCSI wire, from `USBLCD.exe`

<!-- audit-state: origin=2.1.6.0 addresses=2.1.6.0 -->
> **Disassembled from the release we port.  This component is NATIVE — the
> citations below are Ghidra function addresses in `USBLCD.exe`, not
> file:line into a decompiled tree, so they are not re-anchorable by
> `audit_release.py`.**
> Re-measured when the oracle moved on 2026-09-25: the newer installer's
> `USBLCD.exe` carries the same PE build date (2024-03-25) and byte-identical
> `.text` / `.rdata` / `.data` sections.  Only the PE checksum and the appended
> Authenticode signature differ, so every finding below still holds.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

`USBLCD.exe` is **native** (PE32 i386, 2.7 MB), so ILSpy cannot touch it and it
sat recorded as "absent — needs disassembly" for months.  Disassembled with
Ghidra 12.0.1 on **2026-09-18**; this is what it does.

## Provenance — and why this one IS independent

**Check the date before citing a component.**  2.1.4 is the release this
project was built FROM (tree dated Dec 2025 - Jan 2026; our first commit is
2026-02-05).  2.1.6's MANAGED components post-date our public release —
`TRCC.exe` 2026-06-01, `USBLCDNEW.dll` 2026-05-21 — so agreement with them is
not automatically independent confirmation.

This binary is the exception, on two independent signals:

| binary | PE TimeDateStamp | |
|---|---|---|
| **`USBLCD.exe`** | **2024-03-25** | ~23 months BEFORE our first commit (2026-02-05) |
| `USBLCDNEW.exe` | 2025-12-06 | native, datable |
| `TRCC.exe` | "2061-12-05" | .NET — a Roslyn content hash, not a date |
| `USBLCDNEW.dll` | "2092-03-15" | .NET — same |

Second signal: the CDB builder computes a 4-byte checksum, stores it at offset
16, and passes a 20-byte buffer — which the transport then truncates to 16.
Vestigial structure from an older protocol revision.  Nobody writes that fresh,
and nobody copying our 16-byte builder would invent it.

**So the SCSI agreement below is genuine independent confirmation.**  Treat
`AUDIT_WIRE.md`'s managed findings with more suspicion than these.

## Transport — `FUN_0040c6a0`

`DeviceIoControl(handle, 0x4D014 /* IOCTL_SCSI_PASS_THROUGH_DIRECT */, buf,
0x50, buf, 0x50, …)` — the Windows equivalent of our `SG_IO` ioctl on
`/dev/sgN`.  Both hand a vendor CDB to the same kernel storage path.

`SCSI_PASS_THROUGH_DIRECT` is filled as:

    Length           = 0x2C (44)
    CdbLength        = 0x10 (16)      <- from packed 0x18100001
    SenseInfoLength  = 0x18 (24)
    SenseInfoOffset  = 0x30 (48)
    Cdb[0..15]       = four DWORD copies from the caller's buffer

**Only bytes 0-15 are copied and `CdbLength` is literally 16** — so the
checksum at offset 16 never reaches the wire.  Our `_build_cdb` docstring
asserted exactly this from reasoning about our own truncation; it is now
confirmed against the vendor's binary.

Retries `ERROR_GEN_FAILURE` (0x1F) up to **3** times before giving up.

## Frame path — `FUN_00410d00`

Pulls composed frames from `OpenFileMappingA(…, "shareMemory_Image")` — the
same shared-memory bridge `AUDIT_WIRE.md` documents for the other wires.  Pixel
data starts at `shm + 0x25800`.

**Handshake.**  Poll `cmd=0xF5`, `size=0xE100`, data-IN.  If
`response[4..7] == A1 A2 A3 A4` the panel is still booting → `Sleep(3000)` and
retry.  Init is `cmd=0x1F5`, `size=0xE100`, data-OUT, then `Sleep(100)`.

**Panel class comes from `response[0]`**, as an ASCII digit — `'$'`, `'2'`,
`'3'` — not from a resolution lookup.  We derive chunking from the resolution
instead and arrive at the same answer; different route, same bytes.

**Chunking.**  Command word is `0x101F5 | (index << 24)`:

| class | chunks (cmd / size) | total | panel |
|---|---|---|---|
| `'$'` | `0x101F5`/0xE100, `0x10101F5`/0xE100 | 115,200 | 240x240 |
| `'2'` | `0x101F5`/0xE100, `0x10101F5`/0xE100, `0x20101F5`/**0x9600** | 153,600 | 320x240 |
| `'3'` | `0x101F5`/0x10000 x3, `0x30101F5`/**0x2000** | 204,800 | 320x320 |

`ScsiLcd._frame_chunks` reproduces all three **byte for byte**, and
`_SMALL_DISPLAY_PIXELS = 76800` lands the 0xE100-vs-0x10000 switch exactly
where the vendor's classes do.

## What matches — verified against `src/trcc/adapters/device/scsi_lcd.py`

| | vendor | ours |
|---|---|---|
| poll / init command | `0xF5` / `0x1F5` | `_POLL_CMD` / `_INIT_CMD` |
| poll size | `0xE100` | `_POLL_SIZE` |
| boot signature | `A1 A2 A3 A4` at `[4..7]` | `_BOOT_SIGNATURE` |
| boot retry wait | `Sleep(3000)` | `_BOOT_WAIT_S = 3.0` |
| post-init delay | 100 ms | `_POST_INIT_DELAY_S = 0.1` |
| CDB length | 16 | 16 |
| frame command base | `0x101F5 \| idx << 24` | `_FRAME_CMD_BASE` |
| chunk sizes | 0xE100 / 0x10000 | `_CHUNK_SIZE_SMALL` / `_LARGE` |

## THE CEILING — what this does NOT cover

**`USBLCD.exe` contains chunk indices 0-3 and nothing higher.**  Its SCSI path
cannot emit more than four chunks, so it tops out at 320x320 (204,800 bytes).

That is consistent with the device split: our only 480x480 device
(`87ad:70db`) is on the **BULK** wire, and bulk/HID/ALi/LY live in
`USBLCDNEW.dll`.  SCSI carries the small panels; everything larger does not use
it.

**The latent hazard.**  A SCSI panel's resolution comes from the FBL byte it
reports, through `get_profile(fbl, fbl)` — not from the registry.  **7 of our
19 FBL profiles exceed the four-chunk ceiling**: 72/129 (480x480, 8 chunks), 64
(640x480, 10), 224 (854x480, 13), 128 (1280x480, 19), 192 (1920x462, 28), 114
(1600x720, 36).  If a SCSI device ever reported one, `_frame_chunks` would emit
command words (`0x40101F5` …) that **appear nowhere in the vendor's binary** —
we would be inventing protocol.

Unknown whether any SCSI panel can report those FBLs; no device in the registry
does.  Recorded so the ceiling is a known fact rather than an assumption.  The
cheap guard is to log loudly (or refuse) when a SCSI handshake resolves to a
profile needing more than four chunks, which turns silent invention into a line
in `trcc report`.

## Reproducing

    unzip -o -j ~/Downloads/trcc216_payload.zip "TRCCCAP/USBLCD.exe" \
        -d ~/Downloads/TRCC_2.1.6_USBLCD_native/
    JAVA_HOME=/usr/lib/jvm/java-25-openjdk \
      ~/bin/ghidra_12.0.1_PUBLIC/support/analyzeHeadless <proj> USBLCD \
      -import USBLCD.exe -scriptPath <dir> -postScript FindScsi.java

Ghidra needs a **JDK** (`application.java.min=21`), not a JRE, and headless
cannot prompt for it — set `JAVA_HOME`.  Its Python scripting needs PyGhidra
enabled; a `.java` script always works.  `FindScsi.java` finds every function
referencing the protocol constants and decompiles it.

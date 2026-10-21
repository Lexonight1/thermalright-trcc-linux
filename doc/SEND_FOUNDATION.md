# Send/Output Foundation — tracker

The per-device send-worker (actor) that owns each device's USB wire. This file
is the **scoreboard** for the work: every row stays here until it's `DONE` on a
verified run. Deferrals are rows with a **trigger**, never a sentence in a chat
that scrolls away. (Why this file exists: "not now" with no home becomes
"never" — that's how the cutover dropped working capabilities.)

## Why
*(The state before Phase 1, 2026-06-04 — kept as the record of why.)*
- Device sends were unserialized across two threads (main: GUI/video;
  `trcc-metrics` bg: `_DeviceRenderObserver` → `RenderAndSend` → `device.send`).
  No lock on `Device.send` / `App.dispatch` / any adapter.
- Bulk/LY firmware reverts to the logo after ~2-3 s without a fresh frame
  (`KeepaliveService` + `KeepAliveLoop` exist but nothing auto-runs them; static
  themes rely on the ~2 s metrics cadence — too slow).
- Both are dropped legacy capabilities (audit: *async send-worker
  `send_*_async`/`is_busy`/`stop_send_worker` MISSING*; *`_sending` concurrency
  guard MISSING*).

## Design (locked)
Separate **policy** from **execution**, inject execution (the codebase's own
idiom: `SlideshowService` = pure cursor, `make_timer`/`MetricsLoop` = driver).

| Layer | Component | Depends on | Responsibility |
|---|---|---|---|
| core / port | `SendTask` ABC (`ports.py`) | — | unit of work the scheduler drives (`run_once(now)`, `wait`, `key`) |
| core / port | `SendScheduler` ABC (`ports.py`) | `SendTask` | execution abstraction (`add`/`remove`/`shutdown`) |
| services | `DeviceSender(SendTask)` (`services/device_sender.py`) | `Device` port | serialize writes · cache last · keepalive policy. Told `volatile=bool` (no Wire knowledge) |
| adapters/infra | `ThreadSendScheduler`, `SyncSendScheduler` (`adapters/infra/send_scheduler.py`) | `SendTask` | thread-per-task / deterministic manual tick |
| composition | `App` | both ports | sender registry, inject scheduler, lifecycle |

Serialization is by construction: **one scheduler thread per task = single
consumer of `device.send`** — no wire lock needed. The inbox lock only guards
the latest-wins slot.

## Phase 1 — the foundation (all of it; nothing here is deferred)

| # | Increment | Status | Verify |
|---|---|---|---|
| 1 | `SendTask`+`SendScheduler` ports; `DeviceSender` (serialize·cache·keepalive, `volatile` flag); `ThreadSendScheduler`+`SyncSendScheduler`; unit tests (Sync, no sleeps) + a thread no-interleave test | **DONE** 2026-06-04 — `ports.py`, `services/device_sender.py`, `adapters/infra/send_scheduler.py`, `tests/test_device_sender.py` (12 tests); ruff/pyright clean, suite 1242 | ✓ |
| 2 | `App.senders` registry + `App.send(key,payload,*,wait)` facade + lifecycle (create/start `ConnectDevice`; stop/drop `detach`/`close`); `ThreadSendScheduler` default-injected; `VOLATILE_FRAME_WIRES`+`Device.needs_keepalive` (historical: `VOLATILE_FRAME_WIRES` was removed 2026-08-19; `Device.needs_keepalive` alone decides now — `core/models.py`); `submit(wait=)` semantics (absorbed old #4) | **DONE** 2026-06-04 — `app.py`, `models.py`, `ports.py`, `commands/device.py`, `tests/test_app_senders.py`; suite 1246 | ✓ |
| 3 | Reroute the 8 wire-write sites → `app.send` (**all `wait=True`** for now — preserves every caller's synchronous result + error handling via exception-propagating `submit`; no worse than today's sync latency). Writes now flow through the workers | **DONE** 2026-06-04 — 8 sites in `device.py`/`theme.py`/`led.py`/`system.py`; `submit` relays wire exceptions; LED test helper starts a sender; suite 1247 | ✓ |
| 4 | ~~Actor submit semantics~~ — **folded into #2** (`submit(wait=)`) | **DONE** (in #2) | — |
| 5 | Intrinsic keepalive verified end-to-end (volatile resend @150ms, reset on new frame; the cadence is per device now — see the 2026-10-18 note below) | **DONE** 2026-06-04 — `dev/smoke_keepalive.py`: Bulk +7 / SCSI +0; mock GUI headless no tracebacks | ✓ |
| 6 | Absorb `KeepaliveService` into the sender (one owner of "last frame"); `KeepAliveLoop` + CLI `display keepalive` + API `/keepalive` become thin wrappers (`count>=1` confirms, `count=0` blocks); `keepalive.py` deleted | **DONE** 2026-06-04 — removed service + 4 `store` calls + `forget`; reworked `KeepAliveLoop`; suite 1244; smoke still Bulk +7 / SCSI +0 | ✓ |
| 7 | Boot-anim (`send_boot_animation`, SCSI-only) holds the wire via `app.exclusive_wire(key)` context manager (no lambda — `DeviceSender.exclusive()` + `_wire_lock`). Screencast needed no change — it already sends per-frame through `app.send` | **DONE** 2026-06-04 — `device_sender.exclusive()`, `app.exclusive_wire`, boot-anim wrapped; `test_exclusive_blocks_worker_writes`; suite 1245 | ✓ |
| 9 | Sender publishes on fire-and-forget failure: injected `on_failure` (named callback, no EventBus dep) → `App._on_sender_failure` publishes `ErrorOccurred` + `DeviceDisconnected` (for `DeviceDisconnectedError`), mirroring the Command `except`. Keepalive guarded on `is_connected` to stop the flood once recovery closes the device. (Per-frame retry + 3-strike `RecoveryTracker` already in the Device adapters.) | **DONE** 2026-06-04 — `device_sender` `on_failure`/`_fail`; `app._on_sender_failure`; 2 tests; suite 1247 | ✓ |
| 8 | Per-tick `RenderAndSend` (metrics observer + video tick) → `app.send(wait=False)`: producers never block on USB; I/O leaves the producer thread. Failures surface via #9 | **DONE** 2026-06-04 — one line; suite 1247; keepalive smoke + mock GUI clean | ✓ |
| 10 | Disconnect-during-send: `detach` calls `stop_sender` (scheduler joins the worker thread) **before** `device.disconnect()`, so an in-flight write completes before the transport closes — no race | **DONE** 2026-06-04 — ordering in `App.detach`; `test_detach_joins_worker_before_transport_close` (no post-detach writes) | ✓ |
| 11 | Daemon + CLI + GUI all create senders via the `ConnectDevice` chokepoint (daemon: `start_hotplug` → `DeviceAttached` → `_on_device_attached` → `ConnectDevice` → `start_sender`); `App.close` shuts the scheduler. Uniform, no per-UI wiring | **DONE** 2026-06-04 — verified by design (single chokepoint) + daemon path read | ✓ |
| 12 | `dev/smoke_keepalive.py` + send-touching tests updated (render_led helper, backend_finish, device_sender, app_senders) | **DONE** 2026-06-04 — suite 1248; no "no sender" warnings in logs | ✓ |

**2026-10-18: SCSI is volatile too.** The "SCSI +0" above was correct for the code
and wrong for the panel: an idle SCSI panel drops to its USB-standby screen inside
~30 s (0402:3922, on glass), and the C# re-sends every ~150 ms.  The base
`Device.needs_keepalive` now returns True for every device (`core/ports.py`); a
device whose firmware holds its frame would override it, and none does.  The
resend cadence is `Device.keepalive_interval`: 1 s by default, 150 ms
(`FAST_REVERT_KEEPALIVE_S`, `adapters/device/_base.py`) for `BulkLcd` and
`LyLcd`, passed to the sender by `App.start_sender`.  `dev/smoke_keepalive.py`
now expects both to resend.

**Phase 1 complete (12/12).** The freeze is fixed; the per-device send-worker actor owns every wire write, serializes by construction, keepalives volatile wires, surfaces failures, and runs uniformly across GUI/CLI/API/daemon.

## Phase 2 — deferred, tracked (each has a trigger; do NOT drop)

| Item | Why later | Re-entry trigger | Status |
|---|---|---|---|
| Unify background loops (metrics + slideshow + keepalive) under one shared scheduler (extract a generic `Pumpable`) | DRY refactor; unsafe while introducing the sender | Phase 1 merged + green on hardware | **PARTLY DONE** — the slideshow, screencast, visual, stream, game-mode and reconnect drivers run as `SendTask`s on the scheduler through `BaseSendTask` (`services/_send_task.py`); `MetricsLoop`, `VideoLoop` and `LedAnimationLoop` still own a thread each |
| asyncio / pool execution model | blocking USB ⇒ thread-per-device is the honest fit now | only if profiling shows the thread model bottlenecks | DEFERRED |
| `wait=False` on the video/metrics **hot path** (so per-frame producers never block on USB) | safe only once the metrics observer no longer needs the synchronous result on its own thread | after #8 (observer submits non-blocking) | **DONE** (in #8) — `RenderAndSend` calls `app.send(key, frame, wait=False)` (`core/commands/device.py`); `App.send` returns without waiting (`app.py`) |

## Adjacent subsystems (separate work — listed so they're not lost)
- Per-device json config persistence (background/mask per device) — **done**: `DeviceSettings.mask_path` / `background_path` (`core/models.py`), saved in `trcc.json`.
- HID/LY mock handshake scripting (`tests/mock_platform.py`) — **done**: `mock_handshake` scripts the HID type-2/type-3 and LY replies.
- Encode-rotation table (Tier-1 widescreen panels) — **wired**: `get_profile` resolves it through `resolve_encode_rotation` (`core/protocol.py`), and `bulk_lcd.py` calls it with the live SUB byte. Wire output on real glass is still unconfirmed — `memory/project_geometry_subsystem_and_mock.md` §1b.
- The rest of the cutover audit backlog — `memory/project_full_cutover_audit.md`.

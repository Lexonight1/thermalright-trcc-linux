# Send/Output Foundation — tracker

The per-device send-worker (actor) that owns each device's USB wire. This file
is the **scoreboard** for the work: every row stays here until it's `DONE` on a
verified run. Deferrals are rows with a **trigger**, never a sentence in a chat
that scrolls away. (Why this file exists: "not now" with no home becomes
"never" — that's how the cutover dropped working capabilities.)

## Why
- Device sends are unserialized across two threads today (main: GUI/video;
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
| 2 | `App.senders` registry + `App.send(key,payload,*,wait)` facade + lifecycle (create/start `ConnectDevice`; stop/drop `detach`/`close`); `ThreadSendScheduler` default-injected; `VOLATILE_FRAME_WIRES`+`Device.needs_keepalive`; `submit(wait=)` semantics (absorbed old #4) | **DONE** 2026-06-04 — `app.py`, `models.py`, `ports.py`, `commands/device.py`, `tests/test_app_senders.py`; suite 1246 | ✓ |
| 3 | Reroute the 8 wire-write sites → `app.send` (**all `wait=True`** for now — preserves every caller's synchronous result + error handling via exception-propagating `submit`; no worse than today's sync latency). Writes now flow through the workers | **DONE** 2026-06-04 — 8 sites in `device.py`/`theme.py`/`led.py`/`system.py`; `submit` relays wire exceptions; LED test helper starts a sender; suite 1247 | ✓ |
| 4 | ~~Actor submit semantics~~ — **folded into #2** (`submit(wait=)`) | **DONE** (in #2) | — |
| 5 | Intrinsic keepalive verified end-to-end (volatile resend @150ms, reset on new frame) | **DONE** 2026-06-04 — `dev/smoke_keepalive.py`: Bulk +7 / SCSI +0; mock GUI headless no tracebacks | ✓ |
| 6 | Absorb `KeepaliveService` into the sender (one owner of "last frame"); `KeepAliveLoop` + CLI `display keepalive` + API `/keepalive` become thin "ensure-running" wrappers (kept working) | TODO | CLI + API respond |
| 7 | Route the **other** wire writers through the sender: `send_boot_animation` + screencast via `sender.run_exclusive` (they write the wire too) | TODO | no interleave w/ keepalive |
| 8 | `_DeviceRenderObserver` submits non-blocking → device I/O leaves the metrics thread | TODO | thread test |
| 9 | Auto-recovery: funnel failures through existing `RecoveryTracker`; port dropped per-frame retry + `is_busy` surface | TODO | recovery unit test |
| 10 | Disconnect-during-send: teardown drains/joins so it can't race an in-flight write | TODO | shutdown test |
| 11 | Daemon + CLI + GUI all create senders via `ConnectDevice` — uniform, no per-UI wiring | TODO | daemon smoke |
| 12 | `dev/smoke_keepalive.py` + update ~7 send-touching tests | TODO | green |

## Phase 2 — deferred, tracked (each has a trigger; do NOT drop)

| Item | Why later | Re-entry trigger | Status |
|---|---|---|---|
| Unify background loops (metrics + slideshow + keepalive) under one shared scheduler (extract a generic `Pumpable`) | DRY refactor; unsafe while introducing the sender | Phase 1 merged + green on hardware | DEFERRED |
| asyncio / pool execution model | blocking USB ⇒ thread-per-device is the honest fit now | only if profiling shows the thread model bottlenecks | DEFERRED |
| `wait=False` on the video/metrics **hot path** (so per-frame producers never block on USB) | safe only once the metrics observer no longer needs the synchronous result on its own thread | after #8 (observer submits non-blocking) | DEFERRED |

## Adjacent subsystems (separate work — listed so they're not lost)
- Per-device json config persistence (background/mask per device) — maps to the cutover audit's per-device config rows.
- HID/LY mock handshake scripting (`tests/mock_platform.py`).
- Encode-rotation table (Tier-1 widescreen panels) — `memory/project_geometry_subsystem_and_mock.md` §1b.
- The rest of the cutover audit backlog — `memory/project_full_cutover_audit.md`.

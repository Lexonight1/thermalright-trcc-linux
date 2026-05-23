# Known acceptable parity diffs + adopted-from-legacy implementations

Two kinds of entries live here:

1. **Acceptable diffs** — byte-level differences between legacy and
   next/ output that have been investigated and deemed acceptable.
   These come with a documented root cause, near-zero user impact,
   and a reason we don't fix them.

2. **Adopted-from-legacy** — places where next/ deliberately matches a
   legacy implementation choice (often a slightly quirky one) so the
   Phase C byte-equality gate passes.  These are coupling-debt to
   clean up after Phase E (when legacy goes away).

---

## Adopted-from-legacy

### `QtRenderer.apply_brightness` — QPainter overlay vs pure multiply

- **Files**: `src/trcc/next/adapters/render/qt.py` (the `FIXME` comment
  on `apply_brightness`).
- **What next/ did originally**: per-pixel `pixel * (percent / 100)`
  multiply into a fresh `Format_ARGB32` surface.
- **What legacy does**: paint a semi-transparent black overlay via
  `QPainter.fillRect(...)` on the source surface in place.
- **Why we adopted legacy's approach**:
  - Visually identical output (sub-perceptible ±1-LSB drift in
    pre-quantization channels; RGB565 quantization erases most of it
    anyway).
  - GPU-accelerated through QPainter source-over composite — much
    faster than a per-pixel Python loop on large surfaces.
  - Phase C byte-equality gate passes when the full pipeline
    (resize + brightness + rotate + encode) matches.
  - Legacy's behavior is the one shipping to users today, so adopting
    it preserves the "legacy users transitively validate next/"
    contract that Phase C exists to establish.
- **What this costs**: next/ inherits one quirk — no support for
  `percent > 100` brightness boost — and a `QPainter` round-trip
  where a literal multiply would do.
- **Post-Phase E**: revisit.  Either switch back to pure-math
  multiply (and update the parity test to use a pixel-tolerance
  assertion instead of byte-equality), or keep the QPainter approach
  because it's faster.  Sub-perceptible, no user pressure.

---

## Acceptable diffs

| # | Subject | Diff shape | Root cause | User impact | Why we don't fix |
|---|---|---|---|---|---|

*(empty — every diff Phase C has surfaced so far has either been a
real bug to fix, or moved to the "adopted-from-legacy" section above
with a documented trade-off.)*

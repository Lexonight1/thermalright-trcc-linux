# Known acceptable parity diffs

This file documents byte-level differences between legacy `src/trcc/`
and `src/trcc/next/` wire output that have been investigated and
deemed acceptable.  **Every entry must include**:

- Affected protocol / operation
- Root cause (linked to a library, OS, or environmental factor)
- User-visible impact (must be near-zero)
- Why we don't fix it

Anything not on this list is a bug. When CI surfaces a parity diff,
investigate first — only add a row here after the investigation
turns up an unavoidable environmental cause.

---

| # | Subject | Diff shape | Root cause | User impact | Why we don't fix |
|---|---|---|---|---|---|

*(empty so far — Phase C is gating on every diff being a real fix.)*

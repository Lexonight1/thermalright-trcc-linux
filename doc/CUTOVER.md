# Cutover plan — `src/trcc/next/` → primary

This doc is the pre-flight checklist for flipping `trcc-next` from
opt-in rebuild to the default `trcc` binary.  When you tag v10.0.0
you should be able to walk this page top-to-bottom and feel done
on every line.

If anything below is unchecked, **don't tag yet**.

## Mental model

The filing cabinet got rebuilt, drawer by drawer, behind a curtain.
The drawers (Commands), index cards (Models), and procedures
(Services) are all in place.  The new clerk (next/ App) reads from
the same drawers and serves every UI that walks up to the front
counter (CLI / GUI / API / REPL / future VR).

Cutover means: pulling the curtain.  Users who walked up to the
old counter yesterday will keep getting served — the cabinet is the
same — only the clerk is new.

## What "done" looks like

Two console-script flips:

1. **v10.0.0** — `trcc` points at `trcc.next.ui.cli.main:main` (the
   new clerk).  `trcc-legacy` is added as an opt-out for one
   release window so anyone who hits a regression has a working
   fallback.

2. **v10.1.0** (or v10.0.1 if no regressions surface) — `git mv
   src/trcc/next/* src/trcc/`, drop `trcc-legacy`, delete the
   parity scaffolding.  Tree is clean; only one cabinet remains.

## Pre-flight checklist — must be green before tagging v10.0.0

### Phase A — feature parity

- [ ] Every legacy Command has a next/ counterpart (`core/commands.py`)
- [ ] `dev/smoke_full_pipeline.py` exits 0 (every Command family round-trips through bus + EventBus)
- [ ] `trcc-next gui` launches without exceptions on Linux dev box
- [ ] `trcc-next shell` REPL opens, accepts a command, returns a result
- [ ] `trcc-next daemon` starts, `trcc-next status` shows it running, `trcc-next kill` stops it

### Phase B — per-OS native paths

- [ ] **Linux**: native end-to-end works on the maintainer's dev box (`dev/mock_gui.py` + real hardware)
- [ ] **Windows**: at least one reporter has confirmed the Windows VM build + connect + display + LED on real hardware
- [ ] **macOS Intel**: at least one reporter has confirmed Intel SMC + LaunchAgent + connect-display on real hardware
- [ ] **macOS Apple Silicon**: separate reporter cycle (or opt-out documented in release notes)
- [ ] **FreeBSD**: VM setup OR one reporter confirmed; `dev/smoke_bsd.py` + parity tests green

### Phase C — byte-equality parity

- [ ] `pytest tests/parity/ -n 8 -x -q` green
- [ ] `dev/parity_smoke.py` exits 0
- [ ] No new entries in `tests/parity/KNOWN_DIFFS.md` since last release
- [ ] If new entries exist, each has a documented root cause + impact analysis

### Phase D — test coverage + integration

- [ ] `pytest tests/next/ tests/parity/ -n 8 -x -q` green
- [ ] Coverage report: every `ui/*` module above 30%, every `core/*` and `services/*` above 70%
- [ ] `dev/smoke_full_pipeline.py` green
- [ ] No `# type: ignore` added that wasn't there in the prior release (or each new one is explained inline)
- [ ] `ruff check .` clean
- [ ] `python3 -m pyright src/trcc/next/ tests/next/ tests/parity/` clean

### Documentation

- [ ] `doc/CHANGELOG.md` entry for v10.0.0 mentioning the cutover
- [ ] `README.md` install instructions still work after `pip install trcc-linux`
- [ ] Release notes draft includes:
  - "what's new" — daemon mode, REPL, LED effects engine, byte-parity gate
  - "opt-out instructions" — how to use `trcc-legacy` to keep the old binary
  - "known macOS reporter-pending" — Apple Silicon SMC keys still gated by env var
  - "report bugs" — link to GitHub issues, label `next-cutover-regression`

## Mechanics — how the v10.0.0 release actually happens

### Step 1: lint + test gate

```bash
ruff check .
python3 -m pyright src/trcc/ src/trcc/next/ tests/
PYTHONPATH=src pytest tests/ -n 8 -x -q
PYTHONPATH=src python3 dev/parity_smoke.py
PYTHONPATH=src python3 dev/smoke_full_pipeline.py
```

All four green.

### Step 2: console-script swap

Edit `pyproject.toml` `[project.scripts]`:

```toml
[project.scripts]
trcc        = "trcc.next.ui.cli.main:main"   # was trcc.ui.cli.main:main
trcc-next   = "trcc.next.ui.cli.main:main"   # alias, same target
trcc-legacy = "trcc.ui.cli.main:main"        # opt-out for one release
```

### Step 3: archive branch

Cut `legacy-archive` from the current `main` *before* the cutover
commits land.  Anyone who needs to debug a legacy-only bug report
years from now can `git checkout legacy-archive` and have the
pre-cutover tree intact.

```bash
git branch legacy-archive HEAD
git push origin legacy-archive
```

### Step 4: version bump + changelog

- `src/trcc/__version__.py`: `10.0.0`
- `pyproject.toml`: same
- `flake.nix`: same
- `doc/CHANGELOG.md`: add the v10.0.0 entry

### Step 5: commit + tag + push

```bash
git add -A
git commit -m "release: v10.0.0 — next/ becomes the default trcc binary"
git tag v10.0.0
git push origin main
git push origin v10.0.0   # triggers PyPI release via CI
```

### Step 6: GitHub release

```bash
gh release create v10.0.0 --target main --title "v10.0.0 — next/ cutover"
```

Body: the release-notes draft from the documentation checklist above.

## Reporter ping plan

Drop a short, humble message on each open issue tagged
`awaiting-reporter` or pinned for the cutover.  Reuse the format from
prior releases (see `feedback_humble_issue_replies.md`):

```
Hey @reporter — I'm doing my best to make this work for every reporter
on every distro.  v10.0.0 is the cutover release: `trcc` now points at
the rebuilt internals; the old binary is still available as
`trcc-legacy` if you hit a regression.  Could you give v10.0.0 a try
and let me know what you see?  Thanks for your patience.

[install command for the reporter's distro]
```

The five reporter groups to ping:
- Windows VM testers (B.1-B.3 verifications)
- macOS Intel reporters (B.6-B.8 verifications)
- macOS Apple Silicon (gated SMC keys)
- FreeBSD users (B.4-B.5)
- AussieMakerGeek (#150) — specifically benefited from daemon mode

## Rollback plan

If a critical regression surfaces within the v10.0.0 release window:

1. **Don't panic** — `trcc-legacy` is on the user's machine; one
   command swaps them back to known-good behavior.
2. **Comment on the regression issue** — confirm reproducible, ask
   for log paste.
3. **Patch in `next/` if possible** — most parity-discovered bugs
   are one-method fixes thanks to the hexagonal seams.
4. **Tag v10.0.1** with the fix.
5. **If the bug is structural** — point users at `trcc-legacy` in
   release notes, fix in `next/` over the next cycle, retag.

The console-script swap is reversible at any time by editing
`pyproject.toml` and bumping a patch release.  No data migration
happens at cutover; the same `config.json` works for both binaries.

## Cleanup release — v10.1.0

Two-to-four weeks after v10.0.0 with no critical regressions:

1. `git mv src/trcc/next/* src/trcc/` (relative imports survive
   automatically; absolute `trcc.next.foo` references in tests +
   `pyproject.toml` need a grep + sed pass)
2. Delete the old legacy files under `src/trcc/` that didn't move
3. Drop the `trcc-legacy` console script
4. Delete `tests/parity/` + `dev/parity_smoke.py` (no second tree to
   compare against; future drift caught by `tests/next/`)
5. Update `CLAUDE.md` — drop the "Two Source Trees" header
6. Tag v10.1.0

If trouble surfaces during the rename, the `legacy-archive` branch
preserves the v9.x state for archaeology.

## What I will NOT do

- Skip the reporter cycle.  Even if the parity gate is green, real
  hardware on each OS catches things synthetic tests miss.
- Force-push to `main` between v10.0.0 and v10.1.0.  Users have
  installed v10.0.0; rewriting history breaks their pip-cache
  reproducibility.
- Delete `src/trcc/` in the v10.0.0 commit itself.  One change per
  release; the cutover and the cleanup are different decisions.
- Bundle the cutover with an unrelated feature.  v10.0.0 is "the
  internals changed"; if a user upgrades and a new feature broke
  something, the bisect is easier when the feature ships in v10.2.

## When to revisit this doc

- Right before tagging v10.0.0: walk every checkbox top-to-bottom.
- After v10.0.0 release: update the "rollback plan" section with
  any real rollbacks that happened, so the next major-version
  cutover (someday) inherits the lesson.
- Before tagging v10.1.0: walk the cleanup steps.
- After v10.1.0: delete this file or move it to
  `doc/HISTORY_CUTOVER.md` as a historical record.

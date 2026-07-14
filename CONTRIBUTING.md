# Contributing to TRCC Linux

Thanks for your interest in contributing! This project is a Linux port of the Thermalright LCD Control Center and welcomes bug fixes, device support, hardware testing, and documentation improvements.

## Development Setup

```bash
git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
pip install -e '.[dev]'
git config core.hooksPath .githooks   # enable repo git hooks (see below)
trcc system setup              # interactive wizard — checks deps, udev, desktop entry
```

### Git hooks

Run `git config core.hooksPath .githooks` once per clone to enable the tracked
hooks in [`.githooks/`](.githooks/). The `pre-commit` hook keeps code-derived
artifacts in sync with the source: when a commit touches the CLI surface
(`src/trcc/ui/cli/`) or the version, it regenerates the man pages
(`dev/gen_manpages.py` → `man/man1/*.1`) and folds them into the same commit, so
the committed pages can never drift (CI's `test_manpages` is the backstop).

Or manually:

```bash
trcc system setup         # install udev rules (auto-prompts for sudo)
# Unplug/replug USB cable after
```

## Running Tests and Linting

```bash
PYTHONPATH=src pytest tests/ -x -q   # run all tests
PYTHONPATH=src pytest tests/core/    # run domain layer tests only
PYTHONPATH=src pytest tests/services/  # run service layer tests only
PYTHONPATH=src pytest tests/adapters/  # run adapter layer tests only
pytest --cov                         # run with coverage
ruff check .                         # lint
python -m pyright                    # type check
```

Tests are organized to mirror `src/trcc/` hexagonal layers:
- `tests/core/` — domain logic (pure unit tests)
- `tests/services/` — application/use case layer
- `tests/adapters/{device,infra,system}/` — infrastructure adapters
- `tests/cli/`, `tests/api/`, `tests/gui/` — presentation adapters

All PRs must pass tests, `ruff check`, and `pyright` with 0 errors.

## Branch Strategy

1. Fork the repo and create a branch off `main`
2. Make your changes and ensure tests pass
3. Open a PR targeting `main`

> `main` is the default branch. All development, releases, and user-facing clones happen here.

## Ways to Contribute

- **Bug fixes** — Reproduce, write a test, fix it
- **Device support** — Add new Thermalright USB VID:PID mappings to `adapters/device/detector.py`
- **Hardware testing** — Own a HID device? See [doc/GUIDE_DEVICE_TESTING.md](doc/GUIDE_DEVICE_TESTING.md) for how to help validate support
- **Documentation** — Install guides, troubleshooting tips, translations

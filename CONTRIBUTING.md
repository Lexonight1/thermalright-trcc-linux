# Contributing to TRCC Linux

Thanks for your interest in contributing! This project is a Linux port of the Thermalright LCD Control Center and welcomes bug fixes, device support, hardware testing, and documentation improvements.

## Development Setup

```bash
git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
pip install -e '.[dev]'
git config core.hooksPath .githooks   # enable repo git hooks (see below)
trcc system setup              # OS setup (udev rules on Linux) — applies; --dry-run previews
```

### Git hooks

Run `git config core.hooksPath .githooks` once per clone to enable the tracked
hooks in [`.githooks/`](.githooks/). The `pre-commit` hook keeps every
code-derived document in sync with the source and folds the result into the same
commit, so a committed page can never drift from the tree it describes:

| Touching | Regenerates |
|---|---|
| `src/trcc/ui/cli/` or the version | man pages (`man/man1/*.1`) |
| `src/trcc/ui/cli/` | `doc/REFERENCE_CLI.md` |
| `src/trcc/` | `doc/REFERENCE_PORTS.md` |
| `src/trcc/` | `doc/REFERENCE_COMMANDS.md` |
| `src/trcc/` | `doc/REFERENCE_API.md` |
| `src/trcc/` | packaged setup files (`packaging/udev/`, `packaging/modprobe/`) |

Each has a test as the backstop, so CI fails if a page is stale.

Or manually:

```bash
trcc system setup         # install udev rules (auto-prompts for sudo)
# Unplug/replug USB cable after
```

## Running Tests and Linting

```bash
pytest                       # the whole suite — pyproject sets testpaths,
                             # pythonpath and -n auto, so no wrapper is needed
pytest tests/test_ipc_wire.py   # one file
pytest -k flock              # one topic
ruff check .                 # lint
pyright                      # type check
```

Most tests sit directly in `tests/`, named after what they cover
(`test_ipc_wire.py`, `test_app_start_session.py`). A few subdirectories
group the ones that benefit from it: `tests/adapters/{infra,rgb,system}/` and
`tests/ui/presentation/`.

All PRs must pass the suite, `ruff check`, and `pyright` with 0 errors.

## Branch Strategy

1. Fork the repo and create a branch off `main`
2. Make your changes and ensure tests pass
3. Open a PR targeting `main`

> `main` is the default branch. All development, releases, and user-facing clones happen here.

## Ways to Contribute

- **Bug fixes** — Reproduce, write a test, fix it
- **Device support** — Add a row to `src/trcc/core/registry.py`. It is pure data: the App picks the right `Device` subclass from the row's `wire` field, so a new cooler needs no code elsewhere
- **Hardware testing** — Own a HID device? See [doc/GUIDE_DEVICE_TESTING.md](doc/GUIDE_DEVICE_TESTING.md) for how to help validate support
- **Documentation** — Install guides, troubleshooting tips, translations

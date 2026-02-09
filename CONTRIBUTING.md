# Contributing to TRCC Linux

Thanks for your interest in contributing! This project is a Linux port of the Thermalright LCD Control Center and welcomes bug fixes, device support, hardware testing, and documentation improvements.

## Development Setup

```bash
git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
pip install -e '.[dev]'
sudo trcc setup-udev   # install udev rules, then unplug/replug your device
```

System packages (Fedora/Ubuntu):

```bash
# Fedora
sudo dnf install sg3_utils python3-pyqt6 ffmpeg

# Ubuntu / Debian
sudo apt install sg3-utils python3-pyqt6 ffmpeg
```

## Running Tests and Linting

```bash
pytest -v --tb=short       # run tests
pytest --cov               # run with coverage
ruff check .               # lint
ruff check --fix .         # auto-fix lint issues
```

All PRs must pass tests and have clean `ruff check` output.

## Branch Strategy

1. Fork the repo and create a branch off `main`
2. Make your changes and ensure tests pass
3. Open a PR targeting `main`

## Ways to Contribute

- **Bug fixes** — Reproduce, write a test, fix it
- **Device support** — Add new Thermalright USB VID:PID mappings to `device_detector.py`
- **Hardware testing** — Own a HID device? See [doc/HID_TESTING.md](doc/HID_TESTING.md) for how to help validate support
- **Documentation** — Install guides, troubleshooting tips, translations

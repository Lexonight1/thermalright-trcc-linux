# TRCC Linux — Test Suite

5243 tests across 91 files, organized to mirror `src/trcc/` hexagonal layers.

## Running Tests

```bash
PYTHONPATH=src pytest tests/ -x -q          # full suite
PYTHONPATH=src pytest tests/core/           # domain layer
PYTHONPATH=src pytest tests/services/       # application layer
PYTHONPATH=src pytest tests/adapters/       # infrastructure adapters
PYTHONPATH=src pytest tests/cli/            # CLI adapter
PYTHONPATH=src pytest tests/api/            # REST API adapter
PYTHONPATH=src pytest tests/gui/  # GUI adapter
```

## Directory Layout

```
tests/
├── core/                        # Domain logic (pure unit tests)
│   ├── test_models.py
│   ├── test_led_segment.py
│   └── test_led_segment_ax120.py
├── services/                    # Application/use-case layer
│   ├── conftest.py              # Shared DI-wired service fixtures
│   ├── test_services.py
│   ├── test_display_integration.py
│   ├── test_image_ansi.py
│   ├── test_led.py
│   ├── test_overlay.py
│   ├── test_theme.py
│   ├── test_theme_persistence.py
│   └── test_video_cache.py
├── adapters/
│   ├── device/                  # USB protocol adapters
│   │   ├── conftest.py          # Shared HID/LED fixtures
│   │   ├── test_bulk.py
│   │   ├── test_detector.py
│   │   ├── test_factory.py
│   │   ├── test_frame.py
│   │   ├── test_hid.py
│   │   ├── test_implementations.py
│   │   ├── test_lcd.py
│   │   ├── test_led.py
│   │   ├── test_led_kvm.py
│   │   ├── test_ly.py
│   │   └── test_scsi.py
│   ├── infra/                   # Infrastructure I/O adapters
│   │   ├── test_data_repository.py
│   │   ├── test_dc_config.py
│   │   ├── test_dc_parser.py
│   │   ├── test_dc_writer.py
│   │   ├── test_debug_report.py
│   │   ├── test_doctor.py
│   │   ├── test_media_player.py
│   │   ├── test_theme_cloud.py
│   │   └── test_theme_downloader.py
│   └── system/                  # System integration adapters
│       ├── test_config.py
│       ├── test_hardware.py
│       └── test_sensors.py
├── cli/                         # CLI presentation adapter
│   ├── test_cli.py
│   ├── test_device.py
│   ├── test_display.py
│   ├── test_led.py
│   ├── test_system.py
│   └── test_theme.py
├── api/                         # REST API adapter
│   ├── test_api.py
│   └── test_api_security.py
├── gui/               # GUI adapter
│   ├── test_base.py
│   ├── test_base_panel.py
│   ├── test_constants.py
│   ├── test_lcd_visual.py
│   ├── test_led_control.py
│   ├── test_led_visual.py
│   ├── test_misc.py
│   ├── test_preview.py
│   ├── test_theme_setting.py
│   ├── test_trcc_app.py
│   └── test_widgets.py
├── test_architecture.py         # Cross-cutting architecture constraints
├── test_conf.py                 # Settings singleton
├── test_integration.py          # Cross-component integration
├── test_memory.py               # Memory/resource tests
└── conftest.py                  # Global fixtures
```

## Principles

- **Isolation**: Each test is independent — no shared mutable state
- **Mocking**: Hardware access (USB, SCSI, HID) is fully mocked
- **Fast**: Full suite runs in ~2 seconds
- **CI-safe**: Tests work as root (CI) and regular user (dev)

"""Tests verifying OOP design patterns from CLAUDE.md.

Validates that the hexagonal architecture patterns are correctly implemented:
- Dependency Injection
- Strategy Pattern
- Data Transfer Objects (DTOs)
- Service isolation (no Qt dependencies)
- Builder pattern
"""
from __future__ import annotations

import ast
import inspect
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from tests.conftest import get_pixel, make_device_service, make_test_surface, surface_size
from trcc.legacy.core.models import (
    DeviceInfo,
    ThemeData,
    ThemeInfo,
    VideoState,
)
from trcc.legacy.services.display import DisplayService
from trcc.legacy.services.image import ImageService
from trcc.legacy.services.media import MediaService
from trcc.legacy.services.overlay import OverlayService
from trcc.legacy.services.theme import ThemeService

SERVICES_DIR = Path(__file__).resolve().parent.parent / 'src' / 'trcc' / 'services'


# =============================================================================
# Dependency Injection — services are injectable, not hardcoded
# =============================================================================


class TestDependencyInjection(unittest.TestCase):
    """Services accept injected sub-services — never create their own."""

    def test_display_service_accepts_injected_sub_services(self):
        """DisplayService receives DeviceService, OverlayService, MediaService."""
        dev = make_device_service()
        ovl = OverlayService(renderer=ImageService.renderer())
        med = MediaService()
        svc = DisplayService(dev, ovl, med)
        self.assertIs(svc.devices, dev)
        self.assertIs(svc.overlay, ovl)
        self.assertIs(svc.media, med)

    @patch('trcc.legacy.adapters.infra.data_repository.DataManager.ensure_all')
    def test_builder_wires_services_into_lcd_device(self, _):
        """ControllerBuilder creates LCDDevice with properly wired services."""
        from unittest.mock import MagicMock

        from trcc.legacy.core.builder import ControllerBuilder
        from trcc.legacy.core.device import Device

        lcd = ControllerBuilder(MagicMock()).with_renderer(ImageService.renderer()).build_device()
        self.assertIsInstance(lcd, Device)


# =============================================================================
# Strategy Pattern — swappable behavior based on context
# =============================================================================


class TestStrategyPattern(unittest.TestCase):
    """Algorithms swap dynamically based on device/resolution context."""

    def test_byte_order_strategy_scsi_320(self):
        """SCSI 320x320 uses big-endian (Windows ImageTo565 compat)."""
        self.assertEqual(ImageService.byte_order_for('scsi', (320, 320)), '>')

    def test_byte_order_strategy_scsi_480(self):
        """SCSI non-320 uses little-endian."""
        self.assertEqual(ImageService.byte_order_for('scsi', (480, 480)), '<')

    def test_byte_order_strategy_hid(self):
        """HID uses big-endian for 320x320, little-endian for others."""
        self.assertEqual(ImageService.byte_order_for('hid', (320, 320)), '>')
        self.assertEqual(ImageService.byte_order_for('hid', (480, 480)), '<')
        self.assertEqual(ImageService.byte_order_for('hid', (320, 240)), '<')

    def test_rotation_strategy_0(self):
        img = make_test_surface(4, 8)
        result = ImageService.apply_rotation(img, 0)
        self.assertEqual(surface_size(result), (4, 8))

    def test_rotation_strategy_90(self):
        img = make_test_surface(4, 8)
        result = ImageService.apply_rotation(img, 90)
        self.assertEqual(surface_size(result), (8, 4))

    def test_rotation_strategy_180(self):
        img = make_test_surface(4, 8)
        result = ImageService.apply_rotation(img, 180)
        self.assertEqual(surface_size(result), (4, 8))

    def test_rotation_strategy_270(self):
        img = make_test_surface(4, 8)
        result = ImageService.apply_rotation(img, 270)
        self.assertEqual(surface_size(result), (8, 4))

    def test_brightness_strategy_100_noop(self):
        """100% brightness returns the same object (no processing)."""
        img = make_test_surface(2, 2, (200, 100, 50))
        result = ImageService.apply_brightness(img, 100)
        self.assertIs(result, img)

    def test_brightness_strategy_50_darkens(self):
        img = make_test_surface(1, 1, (200, 100, 50))
        result = ImageService.apply_brightness(img, 50)
        self.assertLess(get_pixel(result, 0, 0)[0], 200)

    def test_theme_filter_strategy_all(self):
        """'all' filter passes everything."""
        theme = ThemeInfo(name='anything')
        self.assertTrue(ThemeService._passes_filter(theme, 'all'))

    def test_theme_filter_strategy_user(self):
        """'user' filter passes Custom_ prefix."""
        theme = ThemeInfo(name='Custom_foo')
        self.assertTrue(ThemeService._passes_filter(theme, 'user'))

    def test_theme_filter_strategy_default_excludes_custom(self):
        """'default' filter excludes Custom_ prefix."""
        theme = ThemeInfo(name='Custom_foo')
        self.assertFalse(ThemeService._passes_filter(theme, 'default'))


# =============================================================================
# Data Transfer Objects — pure dataclasses, no logic
# =============================================================================


class TestDTOs(unittest.TestCase):
    """DTOs are strictly defined structures for cross-boundary data."""

    def test_theme_data_is_dataclass(self):
        """ThemeData is a dataclass with known fields."""
        field_names = {f.name for f in fields(ThemeData)}
        expected = {'background', 'animation_path', 'is_animated',
                    'mask', 'mask_position', 'mask_source_dir'}
        self.assertEqual(field_names, expected)

    def test_theme_data_defaults(self):
        """ThemeData defaults are all None/False — no surprise state."""
        data = ThemeData()
        self.assertIsNone(data.background)
        self.assertIsNone(data.animation_path)
        self.assertFalse(data.is_animated)
        self.assertIsNone(data.mask)
        self.assertIsNone(data.mask_position)
        self.assertIsNone(data.mask_source_dir)

    def test_theme_info_is_dataclass(self):
        field_names = {f.name for f in fields(ThemeInfo)}
        self.assertIn('name', field_names)
        self.assertIn('path', field_names)

    def test_device_info_is_dataclass(self):
        field_names = {f.name for f in fields(DeviceInfo)}
        self.assertIn('name', field_names)
        self.assertIn('path', field_names)
        self.assertIn('protocol', field_names)

    def test_video_state_is_dataclass(self):
        field_names = {f.name for f in fields(VideoState)}
        self.assertIn('current_frame', field_names)
        self.assertIn('total_frames', field_names)
        self.assertIn('fps', field_names)

    def test_theme_data_lives_in_models(self):
        """ThemeData is in core.models, not in services."""
        from trcc.legacy.core import models
        self.assertTrue(hasattr(models, 'ThemeData'))

    def test_device_info_lives_in_models(self):
        from trcc.legacy.core import models
        self.assertTrue(hasattr(models, 'DeviceInfo'))


# =============================================================================
# Service Isolation — no Qt/GUI dependencies in services
# =============================================================================


class TestServiceIsolation(unittest.TestCase):
    """Services are pure Python — no Qt/PySide6/PyQt6 imports."""

    def _get_imports(self, filepath: Path) -> set[str]:
        """Parse a Python file and return all imported module names."""
        source = filepath.read_text()
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split('.')[0])
        return imports

    def test_no_qt_in_services(self):
        """No service file imports PySide6 or PyQt6."""
        qt_modules = {'PySide6', 'PyQt6', 'PyQt5'}
        for py_file in SERVICES_DIR.glob('*.py'):
            imports = self._get_imports(py_file)
            offenders = imports & qt_modules
            self.assertEqual(
                offenders, set(),
                f"{py_file.name} imports Qt: {offenders}")

    def test_no_qt_in_models(self):
        """Models are pure dataclasses — no Qt."""
        models_dir = SERVICES_DIR.parent / 'core' / 'models'
        qt_modules = {'PySide6', 'PyQt6', 'PyQt5'}
        for py_file in models_dir.glob('*.py'):
            imports = self._get_imports(py_file)
            offenders = imports & qt_modules
            self.assertEqual(offenders, set(),
                             f"models/{py_file.name} imports Qt: {offenders}")

    def test_services_init_exports(self):
        """services/__init__.py exports all 6 services."""
        from trcc import services
        expected = {'DeviceService', 'DisplayService', 'ImageService',
                    'MediaService', 'OverlayService', 'ThemeService'}
        for name in expected:
            self.assertTrue(
                hasattr(services, name),
                f"services missing export: {name}")


# =============================================================================
# Service Statelessness — stateless services have only static methods
# =============================================================================


class TestServiceStatelessness(unittest.TestCase):
    """Stateless services (ImageService) use only static methods."""

    def test_image_service_all_static(self):
        """Every public method on ImageService is @staticmethod."""
        for name, method in inspect.getmembers(ImageService, predicate=inspect.isfunction):
            if not name.startswith('_'):
                self.assertTrue(
                    isinstance(inspect.getattr_static(ImageService, name), staticmethod),
                    f"ImageService.{name} should be @staticmethod")

    def test_image_service_no_init_state(self):
        """ImageService has no __init__ — fully stateless."""
        # ImageService inherits object.__init__, has no custom state
        svc = ImageService()
        self.assertEqual(len(svc.__dict__), 0)


# =============================================================================
# Hexagonal Boundary — core/ never imports from services/ or adapters/
# =============================================================================


CORE_DIR = Path(__file__).resolve().parent.parent / 'src' / 'trcc' / 'core'


class TestHexagonalBoundary(unittest.TestCase):
    """Core layer must not import from services or adapters at module level.

    Deferred imports inside functions/methods are fine — that's how
    builder.py wires dependencies at call time (it's the composition root).
    lcd_device.py and led_device.py are strict DI — no adapter imports.
    """

    # builder.py is the composition root — it's the ONLY core/ file
    # that imports from adapters (to inject concrete implementations).
    _COMPOSITION_ROOTS = {'builder.py'}

    def _get_module_level_imports(self, filepath: Path) -> list[tuple[str, int]]:
        """Return (module_name, lineno) for top-level imports only.

        Skips imports inside functions, methods, or if TYPE_CHECKING blocks.
        """
        source = filepath.read_text()
        tree = ast.parse(source)
        results = []
        for node in ast.iter_child_nodes(tree):
            # Only look at module-level statements
            if isinstance(node, ast.Import):
                for alias in node.names:
                    results.append((alias.name, node.lineno))
            elif isinstance(node, ast.ImportFrom) and node.module:
                results.append((node.module, node.lineno))
            elif isinstance(node, ast.If):
                # Skip TYPE_CHECKING blocks
                test = node.test
                if isinstance(test, ast.Name) and test.id == 'TYPE_CHECKING':
                    continue
                if isinstance(test, ast.Attribute) and test.attr == 'TYPE_CHECKING':
                    continue
                # Check imports inside non-TYPE_CHECKING if blocks
                for child in ast.walk(node):
                    if isinstance(child, ast.Import):
                        for alias in child.names:
                            results.append((alias.name, child.lineno))
                    elif isinstance(child, ast.ImportFrom) and child.module:
                        results.append((child.module, child.lineno))
        return results

    def test_core_no_module_level_service_imports(self):
        """core/ files never import from services/ at module level."""
        violations = []
        for py_file in CORE_DIR.glob('*.py'):
            if py_file.name in self._COMPOSITION_ROOTS:
                continue
            for module, lineno in self._get_module_level_imports(py_file):
                if '.services' in module or module.startswith('trcc.legacy.services'):
                    violations.append(f"{py_file.name}:{lineno} imports {module}")
        self.assertEqual(violations, [], f"core/ → services/ violations: {violations}")

    def test_core_no_module_level_adapter_imports(self):
        """core/ files never import from adapters/ at module level."""
        violations = []
        for py_file in CORE_DIR.glob('*.py'):
            if py_file.name in self._COMPOSITION_ROOTS:
                continue
            for module, lineno in self._get_module_level_imports(py_file):
                if '.adapters' in module or module.startswith('trcc.legacy.adapters'):
                    violations.append(f"{py_file.name}:{lineno} imports {module}")
        self.assertEqual(violations, [], f"core/ → adapters/ violations: {violations}")


class TestPlatformImplementsPlatform(unittest.TestCase):
    """All platform adapters implement the Platform ABC."""

    def test_linux_implements_abc(self):
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        from trcc.legacy.core.ports import Platform
        self.assertTrue(issubclass(LinuxPlatform, Platform))

    def test_windows_implements_abc(self):
        from trcc.legacy.adapters.system.windows_platform import WindowsPlatform
        from trcc.legacy.core.ports import Platform
        self.assertTrue(issubclass(WindowsPlatform, Platform))

    def test_macos_implements_abc(self):
        from trcc.legacy.adapters.system.macos_platform import MacOSPlatform
        from trcc.legacy.core.ports import Platform
        self.assertTrue(issubclass(MacOSPlatform, Platform))

    def test_bsd_implements_abc(self):
        from trcc.legacy.adapters.system.bsd_platform import BSDPlatform
        from trcc.legacy.core.ports import Platform
        self.assertTrue(issubclass(BSDPlatform, Platform))


class TestSensorEnumeratorABC(unittest.TestCase):
    """All platform sensor enumerators implement the SensorEnumerator ABC."""

    def test_linux_implements_abc(self):
        from trcc.legacy.adapters.system.linux_platform import SensorEnumerator
        from trcc.legacy.core.ports import SensorEnumerator as ABC
        self.assertTrue(issubclass(SensorEnumerator, ABC))

    def test_windows_implements_abc(self):
        from trcc.legacy.adapters.system.windows.enumerator import (
            WindowsSensorEnumerator,
        )
        from trcc.legacy.core.ports import SensorEnumerator as ABC
        self.assertTrue(issubclass(WindowsSensorEnumerator, ABC))

    def test_macos_implements_abc(self):
        # Native sensor enumeration was extracted from macos_platform.py
        # into the multi-module macos/ package (PR #117 — jaminmc).
        from trcc.legacy.adapters.system.macos.sensors import MacOSSensorEnumerator
        from trcc.legacy.core.ports import SensorEnumerator as ABC
        self.assertTrue(issubclass(MacOSSensorEnumerator, ABC))

    def test_bsd_implements_abc(self):
        from trcc.legacy.adapters.system.bsd_platform import SensorEnumerator
        from trcc.legacy.core.ports import SensorEnumerator as ABC
        self.assertTrue(issubclass(SensorEnumerator, ABC))


class TestCLISuggestedCommandsExist(unittest.TestCase):
    """Every 'run trcc xxx' message must reference a registered Typer command.

    Closes the gap that produced #139 — doctor / factory / error
    messages suggested ``trcc setup-udev`` (and 3 other setup-*
    commands) but only the sudo-reexec dispatch knew about them, so
    users who tried them as documented hit ``No such command``.
    """

    def test_every_suggested_command_is_registered(self):
        import ast
        import re
        from pathlib import Path

        src = Path(__file__).resolve().parent.parent / "src" / "trcc"
        # Hyphenated subcommand only — filters prose like "trcc imports
        # numpy" while catching every setup-/lcd-/led-/lang- style name
        # we genuinely tell users to type.
        pattern = re.compile(r"\b(?:sudo )?trcc ([a-z]+(?:-[a-z]+)+)\b")

        suggested: set[str] = set()
        for py in src.rglob("*.py"):
            if "next" in py.relative_to(src).parts:
                continue  # next/ has its own Typer app, separate surface
            tree = ast.parse(py.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    suggested.update(pattern.findall(node.value))

        from trcc.legacy.ui.cli import app
        registered = {cmd.name for cmd in app.registered_commands}
        missing = suggested - registered
        self.assertFalse(
            missing,
            "User-facing messages reference these commands which aren't "
            f"registered as Typer commands: {sorted(missing)}",
        )


if __name__ == '__main__':
    unittest.main()

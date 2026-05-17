"""Security tests for API endpoints — path traversal, info leakage, input validation.

These tests attack our own API with adversarial inputs to verify the security
boundaries documented in CLAUDE.md § Security hold up.
"""
from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import trcc.conf as _conf
import trcc.ui.api as api_module
from trcc.ui.api import app, configure_auth


class _ApiSecurityBase(unittest.TestCase):
    """Shared setup for security tests.

    Patches both legacy ``_system_svc`` (still used by some endpoints) AND
    the post-metrics-unification ``trcc().os.metrics`` chokepoint so the
    test client renders deterministic system metrics regardless of what
    sensors the test host actually exposes.
    """

    def setUp(self):
        from trcc.core.models import HardwareMetrics
        configure_auth(None)
        self.client = TestClient(app)
        self._saved_system_svc = api_module._system_svc
        mock_svc = MagicMock()
        record = HardwareMetrics()
        record.cpu_temp = 65.0
        record.cpu_percent = 12.0
        mock_svc.all_metrics = record
        api_module._system_svc = mock_svc

        proxy = MagicMock()
        proxy.os.metrics = record
        self._trcc_patch = patch('trcc.ui.api.system.trcc', return_value=proxy)
        self._trcc_patch.start()

    def tearDown(self):
        self._trcc_patch.stop()
        api_module._system_svc = self._saved_system_svc


# ===========================================================================
# Path Traversal — /display/overlay
# ===========================================================================

class TestOverlayPathTraversal(_ApiSecurityBase):
    """POST /display/overlay — dc_path must be within str(_conf.settings.user_data_dir)."""

    def test_absolute_path_outside_data_dir(self):
        resp = self.client.post("/display/overlay?dc_path=/etc/passwd")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid", resp.json()["detail"])

    def test_relative_traversal(self):
        traversal = f"{_conf.settings.user_data_dir!s}/../../etc/shadow"
        resp = self.client.post(f"/display/overlay?dc_path={traversal}")
        self.assertEqual(resp.status_code, 400)

    def test_dot_dot_in_path(self):
        resp = self.client.post("/display/overlay?dc_path=../../../etc/passwd")
        self.assertEqual(resp.status_code, 400)

    def test_null_byte_injection(self):
        resp = self.client.post(f"/display/overlay?dc_path={_conf.settings.user_data_dir!s}/theme%00.dc")
        self.assertEqual(resp.status_code, 400)

    def test_valid_data_dir_path_passes_validation(self):
        """A path within str(_conf.settings.user_data_dir) should pass validation (may fail later
        on missing file or no device, but NOT with 'Invalid overlay path')."""
        safe_path = f"{_conf.settings.user_data_dir!s}/test_theme/config1.dc"
        resp = self.client.post(f"/display/overlay?dc_path={safe_path}")
        # Should be 409 (no device) or 404 (no file), NOT 400 (invalid path)
        self.assertNotEqual(resp.status_code, 400)


# ===========================================================================
# Path Traversal — /themes/web/{theme_id}/download
# ===========================================================================

class TestThemeIdTraversal(_ApiSecurityBase):
    """POST /themes/web/{theme_id}/download — theme_id must not escape cache."""

    def test_traversal_in_theme_id(self):
        resp = self.client.post("/themes/web/..%2F..%2Fetc%2Fpasswd/download")
        # Should fail safely — 404 or 400, never 200 with file contents
        self.assertIn(resp.status_code, (400, 404, 422))

    def test_absolute_path_as_theme_id(self):
        resp = self.client.post("/themes/web/%2Fetc%2Fpasswd/download")
        self.assertIn(resp.status_code, (400, 404, 422))

    def test_normal_theme_id_accepted(self):
        """Valid theme IDs like 'a001' should not be rejected by security checks."""
        with patch("trcc.adapters.infra.theme_cloud.CloudThemeDownloader.is_cached",
                   return_value=False), \
             patch("trcc.adapters.infra.theme_cloud.CloudThemeDownloader.download_theme",
                   return_value=None):
            resp = self.client.post("/themes/web/a001/download?resolution=320x320")
        # 404 because theme doesn't exist on server — but not a security rejection
        self.assertEqual(resp.status_code, 404)


# ===========================================================================
# Information Leakage — /themes/import
# ===========================================================================

class TestThemeImportInfoLeakage(_ApiSecurityBase):
    """POST /themes/import — must not leak internal paths or stack traces."""

    def setUp(self):
        super().setUp()
        mock_dispatcher = MagicMock()
        mock_dispatcher.connected = True
        mock_dispatcher.resolution = (320, 320)
        api_module._device_dispatcher = mock_dispatcher

    def tearDown(self):
        api_module._device_dispatcher = None
        super().tearDown()

    def test_service_error_no_internal_details(self):
        with patch("trcc.ui.api.themes.ThemeService.import_tr",
                   return_value=(False, "corrupt archive at /home/user/.trcc/data/foo")), \
             patch("trcc.core.paths.resolve_theme_dir") as mock_td:
            mock_td.return_value = "/tmp"
            resp = self.client.post(
                "/themes/import",
                files={"file": ("evil.tr", io.BytesIO(b"junk"), "application/octet-stream")},
            )
        self.assertEqual(resp.status_code, 400)
        # Must NOT leak the internal path from the service error
        self.assertNotIn("/home/user", resp.json()["detail"])
        self.assertEqual(resp.json()["detail"], "Theme import failed")

    def test_exception_no_stack_trace(self):
        with patch("trcc.ui.api.themes.ThemeService.import_tr",
                   side_effect=FileNotFoundError("/home/user/.trcc/data/secret/config.json")), \
             patch("trcc.core.paths.resolve_theme_dir") as mock_td:
            mock_td.return_value = "/tmp"
            resp = self.client.post(
                "/themes/import",
                files={"file": ("bad.tr", io.BytesIO(b"data"), "application/octet-stream")},
            )
        self.assertEqual(resp.status_code, 500)
        detail = resp.json()["detail"]
        self.assertEqual(detail, "Internal server error")
        self.assertNotIn("Traceback", detail)
        self.assertNotIn("/home/", detail)

    def test_uploaded_filename_not_echoed(self):
        """Uploaded filenames must not be reflected back to clients."""
        with patch("trcc.ui.api.themes.ThemeService.import_tr",
                   return_value=(True, "ok")), \
             patch("trcc.core.paths.resolve_theme_dir") as mock_td:
            mock_td.return_value = "/tmp"
            resp = self.client.post(
                "/themes/import",
                files={"file": ("../../etc/passwd.tr", io.BytesIO(b"data"),
                        "application/octet-stream")},
            )
        if resp.status_code == 200:
            # Success message must not contain the malicious filename
            self.assertNotIn("../../", resp.json().get("message", ""))


# ===========================================================================
# Information Leakage — /system/metrics/{category}
# ===========================================================================

class TestMetricsCategoryValidation(_ApiSecurityBase):
    """GET /system/metrics/{category} — unknown categories rejected cleanly.

    Uses real system metrics — every computer running tests has a CPU.
    """

    def test_unknown_category_returns_400(self):
        resp = self.client.get("/system/metrics/malicious_input")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Unknown category", resp.json()["detail"])

    def test_valid_category_accepted(self):
        resp = self.client.get("/system/metrics/cpu")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(all(k.startswith("cpu_") for k in data))


# ===========================================================================
# File Upload Validation — /display/mask
# ===========================================================================

class TestMaskUploadSecurity(_ApiSecurityBase):
    """POST /display/mask — file upload boundary checks."""

    def test_oversized_mask_rejected(self):
        mock_lcd = MagicMock()
        mock_lcd.connected = True
        api_module._device_dispatcher = mock_lcd
        big = io.BytesIO(b"\x00" * (11 * 1024 * 1024))
        resp = self.client.post(
            "/display/mask",
            files={"image": ("big.png", big, "image/png")},
        )
        self.assertEqual(resp.status_code, 413)
        api_module._device_dispatcher = None


# ===========================================================================
# Resolution Parameter — format validation
# ===========================================================================

class TestResolutionParamValidation(_ApiSecurityBase):
    """Resolution query params must be validated — no injection."""

    def test_invalid_resolution_format(self):
        resp = self.client.get("/themes?resolution=abc")
        self.assertEqual(resp.status_code, 400)

    def test_negative_resolution(self):
        resp = self.client.get("/themes?resolution=-1x-1")
        self.assertEqual(resp.status_code, 400)

    def test_zero_resolution(self):
        resp = self.client.get("/themes?resolution=0x0")
        self.assertEqual(resp.status_code, 400)

    def test_resolution_with_injection(self):
        resp = self.client.get("/themes?resolution=320x320;rm+-rf+/")
        self.assertEqual(resp.status_code, 400)

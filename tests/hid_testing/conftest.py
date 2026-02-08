"""Enable HID device detection for all tests in this directory."""
import pytest

from trcc.device_detector import enable_hid_testing


@pytest.fixture(autouse=True)
def _enable_hid_for_tests():
    """Auto-enable HID testing for every test in hid_testing/."""
    enable_hid_testing()

"""macOS setup diagnostic + Gatekeeper / privileges instructions.

Same shape as the Windows ``_winusb.py`` wizard: read-only diagnostic
that names the issue and gives the user copy-paste-ready commands.
No side effects — macOS USB access requires either a signed bundle
with provisioning entitlements (Apple Developer Program) or
``sudo``, neither of which a setup script can fake.

Three checks
------------

1. **Codesign**: is ``sys.executable`` (or its app bundle) signed?  An
   unsigned binary triggers Gatekeeper on first launch and gets killed
   silently from a double-click.

2. **Quarantine**: is the ``com.apple.quarantine`` xattr present?
   Anything downloaded from a browser carries it; macOS won't run it
   without the user explicitly approving the binary in System
   Settings → Privacy & Security.

3. **Privileges**: running as the logged-in user is the normal case.
   Sensor readings (SMC, the HID sensor hub, IOReport) and HID-report
   panels do not need root and do not need a Developer ID certificate.
   A mass-storage SCSI panel is the exception: capturing it from
   libusb needs root, because ``com.apple.vm.device-access`` is a
   managed entitlement Apple grants to VM apps only.

Exit codes::

    0  every check passed (or running as root and signed)
    1  one or more user-actionable issues — instructions printed
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def install(dry_run: bool = False) -> int:
    """Diagnose Gatekeeper / codesign / privilege state for the current binary.

    ``dry_run`` is accepted for parity with the Linux/BSD installers
    but macOS setup is read-only — fixing requires user action in
    System Settings or a re-launch under ``sudo``.
    """
    log.info("install: dry_run=%s", dry_run)
    if not _is_macos():
        log.warning("macOS setup wizard is macOS-only — current platform: %s", sys.platform)
        return 0

    issues: list[str] = []
    binary = Path(sys.executable).resolve()
    bundle = _enclosing_app_bundle(binary)

    print(f"  Binary:  {binary}")
    if bundle:
        print(f"  Bundle:  {bundle}")
    print()

    # ── Check 1: codesign ──────────────────────────────────────────
    target = str(bundle) if bundle else str(binary)
    signed_status = _check_codesign(target)
    if signed_status is True:
        print(f"  [OK]  codesigned: {target}")
    elif signed_status is False:
        print(f"  [!]   not codesigned: {target}")
        issues.append("codesign")
    else:
        print("  [--]  codesign tool not available — skipping signature check")

    # ── Check 2: quarantine xattr ──────────────────────────────────
    quarantined_target = bundle or binary
    if _has_quarantine_attr(quarantined_target):
        print(f"  [!]   quarantined (com.apple.quarantine present): {quarantined_target}")
        issues.append("quarantine")
    else:
        print(f"  [OK]  no quarantine attribute on {quarantined_target}")

    # ── Check 3: root is not required for sensors or HID panels ──
    if os.geteuid() == 0:
        print("  [OK]  running as root")
    else:
        print("  [OK]  running as the logged-in user")
        print("        Sensors (SMC, HID hub, IOReport) and HID-report panels")
        print("        do not need root or a Developer ID certificate.")
        print("        A mass-storage SCSI panel still needs administrator")
        print("        rights to capture the USB device.")

    if not issues:
        print("\n  Every macOS check passed. 🍻")
        return 0

    print()
    print("  How to fix:")
    if "quarantine" in issues:
        print()
        print("    Quarantine — macOS blocks the binary by default.")
        print(f"      System Settings → Privacy & Security → click 'Open Anyway' for {quarantined_target.name}")
        print("      (Or, from Terminal: ")
        print(f"         xattr -d com.apple.quarantine '{quarantined_target}')")

    if "codesign" in issues:
        print()
        print("    Codesigning — TRCC's macOS DMG is currently unsigned.")
        print("      Right-click the app in Finder → Open  (one time only,")
        print("      Gatekeeper marks it trusted after the first launch).")
        print("      For a permanent fix, the project owner needs an Apple")
        print("      Developer ID + notarization (~$99/year).")

    return 1


def _is_macos() -> bool:
    log.debug("_is_macos")
    return sys.platform == "darwin"


def _enclosing_app_bundle(binary: Path) -> Path | None:
    """Return the ``.app`` bundle containing this binary, if any.

    macOS PyInstaller bundles produce ``Foo.app/Contents/MacOS/foo``,
    so we walk parents looking for ``Contents/MacOS`` and return the
    enclosing ``.app``.
    """
    log.debug("_enclosing_app_bundle: binary=%s", binary)
    for parent in binary.parents:
        if parent.suffix == ".app" and (parent / "Contents" / "MacOS").is_dir():
            return parent
    return None


def _check_codesign(target: str) -> bool | None:
    """``codesign --verify`` — True if signed, False if not, None if tool absent."""
    try:
        result = subprocess.run(
            ["codesign", "--verify", "--strict", target],
            check=False, capture_output=True, timeout=10,
        )
    except FileNotFoundError:
        return None
    except (OSError, subprocess.SubprocessError):
        log.exception("codesign verify failed for %s", target)
        return None
    return result.returncode == 0


def _has_quarantine_attr(path: Path) -> bool:
    """Check the ``com.apple.quarantine`` extended attribute via ``xattr``."""
    log.debug("_has_quarantine_attr: path=%s", path)
    try:
        result = subprocess.run(
            ["xattr", str(path)],
            check=False, capture_output=True, timeout=5, text=True,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False
    return "com.apple.quarantine" in result.stdout

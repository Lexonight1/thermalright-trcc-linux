"""Regression: RunSetup must check permissions AFTER running setup.

Snapshotting warnings before setup made a first run report "udev rules not
installed" right next to "exit code 0" — the install had just written them.
Legacy's run_setup checks-then-installs and never reports a pre-install warning
as a result; this locks that ordering for the Command-bus rewrite.
"""
from __future__ import annotations

from pathlib import Path

from trcc.app import App
from trcc.core.commands.system import RunSetup

from .conftest import FakePlatform


class _OrderTrackingPlatform(FakePlatform):
    """check_permissions warns ONLY before setup ran — so a result with no
    warnings proves RunSetup checked permissions after the install."""

    def __init__(self, tmp_home: Path) -> None:
        super().__init__(tmp_home)
        self.calls: list[str] = []

    def setup(self, dry_run: bool = False) -> int:
        self.calls.append("setup")
        return 0

    def check_permissions(self) -> list[str]:
        warns = [] if "setup" in self.calls else ["udev rules not installed"]
        self.calls.append("check")
        return warns


def test_run_setup_checks_permissions_after_setup(tmp_home: Path) -> None:
    platform = _OrderTrackingPlatform(tmp_home)
    result = RunSetup().execute(App(platform=platform))

    assert platform.calls == ["setup", "check"]
    # the stale pre-install warning is gone — the post-install state is clean
    assert result.warnings == []
    assert result.ok
    assert result.exit_code == 0


def test_setup_applies_by_default_and_only_dry_run_previews(
    tmp_home: Path,
) -> None:
    """#285: `--yes` previewed instead of writing the udev rules.

    The platform flag was `interactive` and inverted -- `interactive=False`
    reads as "no prompts" and MEANT "change nothing" -- so the CLI mapped
    `--yes` (a confirmation flag) onto it and a first-run user got permission
    errors from a setup that had just reported success.

    Asserted at the seam that decides it, per platform-facing value: what the
    Command hands `Platform.setup`.
    """
    seen: list[bool] = []

    class _Platform(FakePlatform):
        def setup(self, dry_run: bool = False) -> int:
            seen.append(dry_run)
            return 0

        def check_permissions(self) -> list[str]:
            return []

    app = App(platform=_Platform(tmp_home))
    RunSetup().execute(app)
    RunSetup(dry_run=True).execute(app)

    assert seen == [False, True], (
        "default must APPLY and only dry_run=True may preview; got "
        f"dry_run values {seen}")

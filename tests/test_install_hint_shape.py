"""An install hint is ONE LINE, on every OS, for every tool.

Every consumer appends ``fix_hint`` to a list that is later ``"\\n".join``-ed
under a ``hint: `` label — ``doctor.py:39``, ``debug_report.py:522``,
``qtgui/panels/system_panel.py:182`` — and ``quickstart`` passes it through as a
single ``next_step_hint``. Four consumers, one assumption, and until this file
nothing enforced it.

``BaseOS.software_install_hint`` returned ``f"{tool} not found — install
it:\\n  {hint}"``, so on macOS, Windows and all three BSDs the second half broke
out of both the indent and the label:

    [WARN] 7z                      7z not on PATH
            hint: 7z not found — install it:
      pkg install p7zip

That is the output ``trcc report`` pastes, which is the whole diagnosis for
hardware we do not own. Linux never showed it because ``LinuxOS`` overrides the
method — so the one platform the maintainer runs was the one platform that
looked fine.

The existing tests could not catch it: ``test_diagnostics`` asserts
``"pkg install" in hint`` and ``test_os_families`` asserts a family names its
own manager. Both are true of a broken two-line string. They check the CONTENT;
this checks the SHAPE.

MUTATION CHECK — restore the wrapper in ``BaseOS.software_install_hint``.
MEASURED 2026-08-21: **20 failed**, 110 passed.  A first draft of this
docstring predicted 5 — one per affected OS — and was wrong, because the
parametrisation is per (OS, tool): 5 OSes x 4 mapped tools.  The fifth tool is
deliberately unmapped, so it takes the ``hint is None`` fallback, which never
carried the wrapper.  Run the mutation, then write the number; a predicted one
is a claim, not a measurement.
"""
from __future__ import annotations

import pytest

from trcc.adapters.system.bsd import FreeBsdOS, NetBsdOS, OpenBsdOS
from trcc.adapters.system.linux import _LINUX_FAMILIES, GenericLinux
from trcc.adapters.system.macos import MacOSPlatform
from trcc.adapters.system.windows import WindowsPlatform
from trcc.core.ports import Platform

#: Every concrete OS, not just the one this suite happens to run on.  The
#: defect lived for months precisely because it could not reproduce on Linux.
ALL_OS: tuple[type[Platform], ...] = (
    FreeBsdOS, OpenBsdOS, NetBsdOS,
    MacOSPlatform, WindowsPlatform,
    *_LINUX_FAMILIES, GenericLinux,
)

#: Every key any caller passes, plus one that is deliberately unmapped so the
#: fallback branch is covered too — it is a hint like any other and renders in
#: the same field.
TOOLS = ("ffmpeg", "7z", "python", "pynvml", "definitely-not-a-tool")


@pytest.mark.parametrize("os_cls", ALL_OS, ids=lambda c: c.__name__)
@pytest.mark.parametrize("tool", TOOLS)
def test_install_hint_is_a_single_line(os_cls: type[Platform], tool: str) -> None:
    """No newline, no carriage return — the renderer supplies the line."""
    hint = os_cls().software_install_hint(tool)
    assert "\n" not in hint and "\r" not in hint, (
        f"{os_cls.__name__}.software_install_hint({tool!r}) is multi-line: "
        f"{hint!r}\nIt is rendered as `hint: {{fix_hint}}` on one line; a "
        f"newline breaks the remainder out of the label and the indent."
    )


@pytest.mark.parametrize("os_cls", ALL_OS, ids=lambda c: c.__name__)
@pytest.mark.parametrize("tool", TOOLS)
def test_install_hint_is_not_empty_or_padded(os_cls: type[Platform],
                                             tool: str) -> None:
    """A hint that renders as blank or ragged is worse than none.

    Guards the other two ways a one-line rule gets broken: an empty string
    (the label prints with nothing after it) and leading/trailing whitespace
    (the indent the renderer already applied gets doubled).
    """
    hint = os_cls().software_install_hint(tool)
    assert hint, f"{os_cls.__name__}.software_install_hint({tool!r}) is empty"
    assert hint == hint.strip(), (
        f"{os_cls.__name__}.software_install_hint({tool!r}) has surrounding "
        f"whitespace: {hint!r} — the renderer supplies the indent."
    )

"""The decompile we read must BE the release we port.

Every C#-parity test in this suite gates on ``DECOMPILE_ROOT.exists()`` — a path
check, which answers "is there a decompile?" and never "is it the right one?".
That gap is not hypothetical: it ran for ten weeks.  The 2.1.6 installer had been
on disk since 2026-06-05, yet every miner, test and audit doc read a **2.0.3**
tree and called it 2.1.6, because the directory was labelled 2.1.6 in the docs
and nothing ever opened it to check.  Audit coverage read 100% against the wrong
program; a reporter (#224) was told his cooler was equally broken on Windows on
the strength of that reading, and it was not — 2.1.6 fixed his SKU specifically.

MEASURED 2026-08-18, before this gate existed: pointing ``TRCC_DECOMPILE`` at the
2.0.3 tree and running the whole oracle suite gave **163 passed, 21 skipped** —
identical to a healthy run.  Every parity assertion happily proved our models
agree with a program we do not ship.  A version label is prose: free to write and
checked by nobody.  This file is the check.

Three states, and the distinction between the last two is the entire point:

* **absent**   → skip.  A gate that cannot see its subject must say so.
* **wrong**    → FAIL, naming found-vs-expected.  Never a silent pass.
* **right**    → run.

See CLAUDE.md "The C# oracle" and
``memory/project_csharp_oracle_was_the_wrong_version.md``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev" / "decompiler"))

from core.csharp import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    DECOMPILE_ROOT,
    ORACLE_RELEASE,
    ORACLE_VERSION,
    assembly_version,
)

_ABSENT = pytest.mark.skipif(
    not DECOMPILE_ROOT.exists(),
    reason=f"no decompile at {DECOMPILE_ROOT} — extract with `ilspycmd -p`, "
           f"or set TRCC_DECOMPILE",
)


def test_oracle_release_is_one_constant() -> None:
    """The release is stated once and everything else derives from it.

    ``ORACLE_VERSION`` is built from ``ORACLE_RELEASE`` and the default
    ``DECOMPILE_ROOT`` embeds it, so switching releases is a one-line edit and
    cannot leave a second spelling behind.  This asserts the derivation rather
    than the literals: hardcoding ``"2.1.6.0"`` here would be a second spelling.
    """
    assert f"{ORACLE_RELEASE}.0" == ORACLE_VERSION
    if os.environ.get("TRCC_DECOMPILE"):
        pytest.skip("TRCC_DECOMPILE overrides the derived path by design")
    assert ORACLE_RELEASE in DECOMPILE_ROOT.name, (
        f"default DECOMPILE_ROOT should derive from ORACLE_RELEASE; "
        f"got {DECOMPILE_ROOT}")


@_ABSENT
def test_decompile_on_disk_is_the_release_we_port() -> None:
    """The tree at ``DECOMPILE_ROOT`` declares the release we port.

    The one assertion that ten weeks of wrong-oracle work needed and did not
    have.  Reads ``AssemblyVersion`` out of the tree — its own statement of what
    it is — never its directory name, which is the inference that failed.

    MUTATION CHECK -- ``TRCC_DECOMPILE=~/Downloads/<any 2.0.3 tree> pytest
    tests/test_oracle_version.py``.  MEASURED 2026-08-18 against the real 2.0.3
    tree, before it was deleted: **1 failed**, message
    ``decompile at ... is TRCC 2.0.3.0, but this project ports 2.1.6.0``.
    """
    found = assembly_version(DECOMPILE_ROOT)
    assert found is not None, (
        f"decompile at {DECOMPILE_ROOT} declares no AssemblyVersion — it is not "
        f"an ilspycmd tree (expected Properties/*.cs), so no parity result from "
        f"it means anything")
    assert found == ORACLE_VERSION, (
        f"decompile at {DECOMPILE_ROOT} is TRCC {found}, but this project ports "
        f"{ORACLE_VERSION}.  Every C#-parity test would pass against it and "
        f"prove nothing.  Extract the right release (`ilspycmd -p`) or point "
        f"TRCC_DECOMPILE at it.  See CLAUDE.md 'The C# oracle'.")

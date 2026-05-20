"""Byte-equality parity tests — legacy ``src/trcc/`` vs ``src/trcc/next/``.

Phase C of the next/ rebuild: prove the two trees produce byte-identical
wire output for every protocol family.  When green, the installed-user
base of legacy is transitively the verification of next/.

Run interactively via ``dev/parity_smoke.py`` (developer command, prints
a clean pass/fail table per protocol) or as part of the main test suite
via ``pytest tests/parity/`` (automated cutover gate).

Per ``tests/parity/KNOWN_DIFFS.md`` — only OS-environmental diffs (font
hinting AA across libraries) are acceptable; everything else gets
investigated + fixed in next/.
"""

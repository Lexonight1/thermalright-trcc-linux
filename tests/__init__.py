"""Tests for ``src/trcc/``.

Most are flat files here; ``adapters/`` and ``ui/`` hold some of the
adapter and presentation tests, and each module's own tests are
``module_<key>/`` -- the folder the module review runs.  Fakes stand at
the transport boundary (no real USB), and ``conftest.py``'s autouse
fixtures keep every test off the user's real home and runtime dir, the
network, root commands and the systemd user manager.
"""

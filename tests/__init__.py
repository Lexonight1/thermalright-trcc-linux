"""Tests for src/trcc/next/ — clean-slate architecture.

Mirrors the next/ layout (core / adapters / services / ui) so each
test maps to the file it exercises.  Uses fakes at the transport
boundary (no real USB) and a tmp-home per-test fixture to keep
`LinuxAutostart` and any path-resolution code from touching the user's
real ~.
"""

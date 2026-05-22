"""Tests for atomic_write_json — concurrency, crash, unicode."""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest

from trcc.legacy.core.io import atomic_write_json


class TestAtomicWriteJson:

    def test_writes_and_reads_back(self, tmp_path):
        path = tmp_path / "config.json"
        data = {"hello": "world", "n": 42}
        atomic_write_json(path, data)
        assert json.loads(path.read_text()) == data

    def test_concurrent_writers_no_corruption(self, tmp_path):
        # Two threads each write 100 distinct payloads. The file at the
        # end must always parse as JSON and equal one of the writers'
        # last writes — never interleaved bytes.
        path = tmp_path / "config.json"

        def worker(tag: str, count: int) -> None:
            for i in range(count):
                atomic_write_json(path, {"tag": tag, "i": i})

        t1 = threading.Thread(target=worker, args=("a", 100))
        t2 = threading.Thread(target=worker, args=("b", 100))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # Repeated parse to confirm no partial-write artefacts.
        for _ in range(10):
            parsed = json.loads(path.read_text())
            assert parsed["tag"] in ("a", "b")
            assert 0 <= parsed["i"] < 100

    def test_crash_mid_write_leaves_original_intact(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"old": True}))

        with patch("trcc.legacy.core.io.json.dump",
                   side_effect=RuntimeError("simulated crash")):
            with pytest.raises(RuntimeError):
                atomic_write_json(path, {"new": True})

        assert json.loads(path.read_text()) == {"old": True}
        # No leftover temp files in the directory.
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith('.')]
        assert leftovers == []

    def test_ensure_ascii_false_preserves_unicode(self, tmp_path):
        path = tmp_path / "theme.json"
        data = {"name": "霜风战甲"}
        atomic_write_json(path, data, ensure_ascii=False)
        # Raw bytes contain UTF-8, not \\uXXXX escapes.
        raw = path.read_bytes()
        assert "霜风战甲".encode("utf-8") in raw
        assert b"\\u" not in raw

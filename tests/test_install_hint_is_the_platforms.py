"""A missing tool is reported with THIS platform's install hint.

Four messages in ``services/`` told every OS -- Windows, macOS, BSD -- to
run ``dnf install ffmpeg``, a package stock Fedora does not ship.  The
platform already knows the right command (``software_install_hint``).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from trcc.core import toolchain
from trcc.core.errors import ThemeError
from trcc.services.media import MediaService
from trcc.services.video_export import VideoExporter, VideoExportError

_SRC = Path(__file__).resolve().parent.parent / "src" / "trcc"
_COMMAND = re.compile(r"\b(dnf|apt|apt-get|brew|winget|pacman|zypper|pkg) install\b")


def _messages(path: Path) -> list[ast.Constant]:
    """Every string literal in *path* except docstrings -- what a user can see."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)}
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docs]


def test_no_package_command_is_written_into_core_or_services() -> None:
    """The platform names the package; core and services never do.

    MUTATION CHECK: put a literal 'dnf install ffmpeg' message back and this
    names it.
    """
    hits = [f"{path.relative_to(_SRC)}:{node.lineno}"
            for layer in ("core", "services")
            for path in (_SRC / layer).rglob("*.py")
            for node in _messages(path)
            if _COMMAND.search(node.value)]

    assert hits == []


def _no_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(toolchain, "present", lambda tool: tool != "ffmpeg")


def test_a_video_without_ffmpeg_names_the_platforms_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """MUTATION CHECK: ignore the injected hint and the generic one shows."""
    _no_ffmpeg(monkeypatch)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\0")
    media = MediaService(install_hint=lambda tool: f"this OS: get {tool}")

    with pytest.raises(ThemeError, match="this OS: get ffmpeg"):
        media.load_video("k", clip, size=(320, 320))


def test_an_export_without_ffmpeg_names_the_platforms_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from trcc.core.models import VideoExportRequest
    _no_ffmpeg(monkeypatch)
    exporter = VideoExporter(lambda tool: f"this OS: get {tool}")

    with pytest.raises(VideoExportError, match="this OS: get ffmpeg"):
        exporter.export_zt(VideoExportRequest(
            source=tmp_path / "x.mp4", start_ms=0, end_ms=1000,
            target_w=320, target_h=320, rotation=0))


def test_the_app_hands_its_platforms_hint_to_media(fake_platform) -> None:
    """The composition root is what makes the hint the platform's."""
    from trcc.app import App
    app = App(fake_platform)

    assert app.media._install_hint("ffmpeg") == "fake platform: install ffmpeg"

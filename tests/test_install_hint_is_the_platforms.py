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

from trcc.adapters.media.ffmpeg import FfmpegTool
from trcc.core import toolchain
from trcc.core.errors import ThemeError, VideoExportError
from trcc.services.media import MediaService
from trcc.services.video_export import VideoExporter

_SRC = Path(__file__).resolve().parent.parent / "src" / "trcc"
_COMMAND = re.compile(r"\b(dnf|apt|apt-get|brew|winget|pacman|zypper|pkg) install\b")


def _messages(path: Path) -> list[tuple[int, str]]:
    """Every string literal in *path* except docstrings -- what a user can see
    -- as ``(lineno, text)``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)}
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docs]


def test_no_package_command_is_written_into_core_or_services() -> None:
    """The platform names the package; core and services never do.

    MUTATION CHECK: put a literal 'dnf install ffmpeg' message back and this
    names it.
    """
    hits = [f"{path.relative_to(_SRC)}:{lineno}"
            for layer in ("core", "services")
            for path in (_SRC / layer).rglob("*.py")
            for lineno, text in _messages(path)
            if _COMMAND.search(text)]

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
    media = MediaService(video_tool=FfmpegTool(lambda tool: f"this OS: get {tool}"))

    with pytest.raises(ThemeError, match="this OS: get ffmpeg"):
        media.load_video("k", clip, size=(320, 320))


def test_an_export_without_ffmpeg_names_the_platforms_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from trcc.core.models import VideoExportRequest
    _no_ffmpeg(monkeypatch)
    exporter = VideoExporter(lambda tool: f"this OS: get {tool}", video_tool=FfmpegTool())

    with pytest.raises(VideoExportError, match="this OS: get ffmpeg"):
        exporter.export_zt(VideoExportRequest(
            source=tmp_path / "x.mp4", start_ms=0, end_ms=1000,
            target_w=320, target_h=320, rotation=0))


def test_the_app_hands_its_platforms_hint_to_the_video_tool(
    fake_platform, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The composition root is what makes the hint the platform's.

    MUTATION CHECK: build the App's ``FfmpegTool()`` without the hint -- the
    generic one shows."""
    from trcc.app import App
    app = App(fake_platform)
    _no_ffmpeg(monkeypatch)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\0")

    with pytest.raises(ThemeError, match="fake platform: install ffmpeg"):
        app.media.load_video("k", clip, size=(320, 320))


def test_a_zt_plays_without_ffmpeg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A ``.zt`` is JPEGs on disk: reading it needs no ffmpeg, so none is asked for.

    ``ZtDecoder`` used to refuse when ffmpeg was absent although it runs none,
    so a saved video theme went black on a box without ffmpeg.
    MUTATION CHECK: put the ``toolchain.present("ffmpeg")`` refusal back in
    ``ZtDecoder.decode`` and this raises.
    """
    import struct

    from trcc.core.models import ZT_MAGIC
    _no_ffmpeg(monkeypatch)
    frames = [b"\xff\xd8 one \xff\xd9", b"\xff\xd8 two \xff\xd9"]
    zt = tmp_path / "Theme.zt"
    zt.write_bytes(
        struct.pack("<Bi", ZT_MAGIC, len(frames))
        + b"".join(struct.pack("<i", 42 * (i + 1)) for i in range(len(frames)))
        + b"".join(struct.pack("<i", len(f)) + f for f in frames))

    playback = MediaService(video_tool=FfmpegTool()).load_video("k", zt, size=(320, 320))

    assert playback.frames == frames


#: What Fedora's ffmpeg-free 7.1 and 8.1 print for an HEVC clip -- captured
#: from TRCC's own decode command in Fedora 42 / 44 containers, 2026-11-05.
_NO_HEVC = (b"[vist#0:0/hevc @ 0x5591284e2e80] Decoding requested, but no decoder "
            b"found for: hevc\nError opening output file pipe:1.\n"
            b"Error opening output files: Invalid argument\n")
_RPM_FUSION = "sudo dnf swap ffmpeg-free ffmpeg --allowerasing"


def _rpm_fusion() -> str:
    return _RPM_FUSION


def test_a_video_this_ffmpeg_cannot_decode_says_how_to_get_one_that_can(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """An iPhone's HEVC on Fedora's ffmpeg-free: the user is told which codec
    and the platform's way to a full ffmpeg -- not "ffmpeg exit 234".

    MUTATION CHECK: drop the explanation from FfmpegDecode._finish -- the raw
    exit code comes back."""
    from .test_background_decode import _GatedFfmpeg, _TwoSeconds

    monkeypatch.setattr(toolchain, "present", lambda tool: True)
    ffmpeg = _GatedFfmpeg(first=[], rest=[], code=234, stderr=_NO_HEVC)
    ffmpeg.release()
    monkeypatch.setattr("trcc.adapters.media.ffmpeg.subprocess.Popen", ffmpeg)
    clip = tmp_path / "IMG_0001.MOV"
    clip.write_bytes(b"\0")

    with pytest.raises(ThemeError) as caught:
        MediaService(video_tool=_TwoSeconds(full_ffmpeg_hint=_rpm_fusion)).load_video(
            "k", clip, size=(320, 320))
    assert str(caught.value) == ("ffmpeg decode failed: this ffmpeg cannot play HEVC "
                                 f"video -- to get one that can: {_RPM_FUSION}")


def test_an_export_this_ffmpeg_cannot_decode_says_so_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The trimmer's Export, same clip, same words.

    MUTATION CHECK: raise the raw "ffmpeg exited" text -- this fails."""
    import subprocess
    from types import SimpleNamespace

    from trcc.core.models import VideoExportRequest

    def ran(*_a: object, **_k: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=234, stderr=_NO_HEVC)

    monkeypatch.setattr(subprocess, "run", ran)
    with pytest.raises(VideoExportError) as caught:
        FfmpegTool(full_ffmpeg_hint=_rpm_fusion).extract_frames(VideoExportRequest(
            source=tmp_path / "IMG_0001.MOV", start_ms=0, end_ms=1000,
            target_w=320, target_h=320), tmp_path)
    assert str(caught.value) == f"this ffmpeg cannot play HEVC video -- to get one that can: {_RPM_FUSION}"


def test_the_app_hands_its_platforms_full_ffmpeg_hint_to_the_tool(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION CHECK: build the App's FfmpegTool without it -- the generic
    "install an ffmpeg build that includes it" shows instead."""
    from trcc.app import App

    monkeypatch.setattr(type(fake_platform), "full_ffmpeg_hint", lambda self: _RPM_FUSION)
    tool = App(fake_platform).video_tool
    assert isinstance(tool, FfmpegTool)
    assert tool._explain(_NO_HEVC.decode()).endswith(_RPM_FUSION)


def test_any_other_ffmpeg_failure_keeps_its_own_words() -> None:
    assert FfmpegTool(full_ffmpeg_hint=_rpm_fusion)._explain(
        "clip.mp4: Invalid data found when processing input") == ""


class _Decoders(FfmpegTool):
    """The ffmpeg tool, its build lacking *missing* decoders."""

    def __init__(self, *missing: str) -> None:
        super().__init__()
        self.missing = missing

    def lacks_decoders(self, codecs: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(c for c in codecs if c in self.missing)


@pytest.mark.parametrize(("advice", "missing", "told"), [
    (_RPM_FUSION, ("hevc",), [f"Apple videos (HEVC) will not play with this ffmpeg -- "
                              f"to get the full one: {_RPM_FUSION}"]),
    (_RPM_FUSION, (), []),             # RPM Fusion's ffmpeg already in: quiet
    ("", ("hevc",), []),               # a platform with nothing better to offer
], ids=["fedora-ffmpeg-free", "fedora-rpm-fusion", "no-advice"])
def test_setup_tells_the_user_how_to_play_apple_videos(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
    advice: str, missing: tuple[str, ...], told: list[str],
) -> None:
    """His ask (2026-11-05): during setup, tell a Fedora user to get RPM
    Fusion's ffmpeg, or Apple (HEVC) videos will not play.

    MUTATION CHECK: drop the decoder check -- the RPM Fusion user is told too."""
    from trcc.app import App
    from trcc.core.commands import RunSetup

    monkeypatch.setattr(fake_platform, "_full_ffmpeg_steps",
                        ((tuple(advice.split()),) if advice else ()))
    app = App(fake_platform, video_tool=_Decoders(*missing))
    try:
        result = app.dispatch(RunSetup(dry_run=True))
        assert (result.warnings, result.full_ffmpeg) == (told, advice if told else "")
    finally:
        app.close()


def test_the_decoder_list_is_read_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ffmpeg -decoders`` as Fedora's ffmpeg-free prints it: h264 there,
    hevc absent.  A decoder named after another (hevc_qsv) is not hevc's."""
    import subprocess
    from types import SimpleNamespace

    listing = (b"Decoders:\n V..... = Video\n ------\n"
               b" V....D h264                 H.264 / AVC / MPEG-4 AVC\n"
               b" V....D hevc_qsv             HEVC (Intel Quick Sync Video)\n")

    def ran(*_a: object, **_k: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=listing)

    monkeypatch.setattr(toolchain, "present", lambda tool: True)
    monkeypatch.setattr(subprocess, "run", ran)
    assert FfmpegTool().lacks_decoders(("h264", "hevc")) == ("hevc",)


@pytest.mark.skipif(not toolchain.present("ffmpeg"), reason="ffmpeg not on PATH")
def test_this_machines_ffmpeg_answers_for_itself() -> None:
    """The real program: a build with every decoder lacks none of these."""
    assert FfmpegTool().lacks_decoders(("h264",)) == ()


@pytest.mark.parametrize("said", [
    _NO_HEVC.decode(),                                    # upstream: Fedora ffmpeg-free 7.1 / 8.1
    '[vist#0:0/hevc @ 0x559c965428c0] Decoding was requested, but this build of '
    'ffmpeg does not include a "hevc" decoder',          # openSUSE TW 9.0.2, Leap 16 7.1.5
], ids=["upstream", "opensuse"])
def test_both_measured_wordings_of_a_missing_codec_are_read(said: str) -> None:
    """openSUSE patches the words; measured in containers 2026-11-05.

    MUTATION CHECK: drop the openSUSE arm -- its users get the raw error."""
    assert FfmpegTool(full_ffmpeg_hint=_rpm_fusion)._explain(said) == (
        f"this ffmpeg cannot play HEVC video -- to get one that can: {_RPM_FUSION}")



def test_the_steps_run_in_order_without_a_shell_and_stop_at_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Argument lists, never a shell line; RPM Fusion's ``$(rpm -E %fedora)``
    is asked of rpm; root drops ``sudo``; a failed step stops the rest.

    MUTATION CHECK: keep going after a failure -- the swap runs after the
    repository did not install."""
    import subprocess
    from types import SimpleNamespace

    from trcc.adapters.system import _elevate

    ran: list[list[str]] = []

    def run(argv: list[str], **_k: object) -> SimpleNamespace:
        ran.append(argv)
        if argv[:2] == ["rpm", "-E"]:
            return SimpleNamespace(returncode=0, stdout="44\n")
        return SimpleNamespace(returncode=1 if argv[-1] == "fails" else 0, stdout="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(_elevate.os, "geteuid", lambda: 1000)
    assert _elevate.run_steps((
        ("sudo", "dnf", "install", "https://x/release-$(rpm -E %fedora).rpm"),
        ("sudo", "dnf", "swap", "a", "b"))) == 0
    assert ran == [["rpm", "-E", "%fedora"],
                   ["sudo", "dnf", "install", "https://x/release-44.rpm"],
                   ["sudo", "dnf", "swap", "a", "b"]]

    ran.clear()
    monkeypatch.setattr(_elevate.os, "geteuid", lambda: 0)
    assert _elevate.run_steps((("sudo", "zypper", "fails"), ("opi", "codecs"))) == 1
    assert ran == [["zypper", "fails"]]


class _Fixable(FfmpegTool):
    """HEVC missing until the install runs."""

    def __init__(self) -> None:
        super().__init__()
        self.fixed = False

    def lacks_decoders(self, codecs: tuple[str, ...]) -> tuple[str, ...]:
        return () if self.fixed else codecs


@pytest.mark.parametrize(("fixes", "ok"), [(True, True), (False, False)])
def test_installing_the_full_ffmpeg_reports_what_really_happened(
    fake_platform, monkeypatch: pytest.MonkeyPatch, fixes: bool, ok: bool,
) -> None:
    """Judged by asking ffmpeg again, not by exit codes.

    MUTATION CHECK: report ok from the exit code -- a run that exits 0 but
    leaves HEVC missing says "installed"."""
    from trcc.app import App
    from trcc.core.commands import InstallFullFfmpeg

    tool = _Fixable()

    def install(self: object) -> int:
        tool.fixed = fixes
        return 0

    monkeypatch.setattr(fake_platform, "_full_ffmpeg_steps", (("sudo", "dnf", "swap", "a", "b"),))
    monkeypatch.setattr(type(fake_platform), "install_full_ffmpeg", install)
    app = App(fake_platform, video_tool=tool)
    try:
        result = app.dispatch(InstallFullFfmpeg())
    finally:
        app.close()
    assert result.ok is ok
    assert result.message == ("The full ffmpeg is installed -- Apple videos will play." if ok else
                              "The full ffmpeg was not installed (exit 0) -- Apple videos still "
                              "will not play.  To try by hand: sudo dnf swap a b")


@pytest.mark.parametrize(("terminal", "answer", "installs"), [
    (True, "y\n", True), (True, "\n", False), (False, "y\n", False)],
    ids=["yes", "default-no", "no-terminal"])
def test_setup_offers_the_full_ffmpeg_to_a_person_at_the_terminal(
    tmp_path: Path, cli_runner, monkeypatch: pytest.MonkeyPatch,
    terminal: bool, answer: str, installs: bool,
) -> None:
    """His ask (2026-11-05): warn, and offer to install it.  Default No (a
    third-party repository); never without a terminal.

    MUTATION CHECK: default the question to yes -- the empty answer installs."""
    from tests.mock_platform import MockPlatform
    from trcc.ui.cli import _ctx
    from trcc.ui.cli import system as cli_system
    from trcc.ui.cli.main import app as cli

    from .conftest import _CliRenderer

    ran: list[str] = []
    platform = MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}], tmp_path)
    platform._full_ffmpeg_steps = (("sudo", "dnf", "swap", "ffmpeg-free", "ffmpeg"),)
    monkeypatch.setattr(type(platform), "setup", lambda self, dry_run=False: 0)
    monkeypatch.setattr(type(platform), "install_full_ffmpeg",
                        lambda self: ran.append("install") or 0)
    monkeypatch.setattr(FfmpegTool, "lacks_decoders", lambda self, codecs: codecs)
    monkeypatch.setattr(cli_system, "_at_a_terminal", lambda: terminal)
    _ctx.set_platform(platform)
    _ctx.set_renderer(_CliRenderer())
    out = cli_runner.invoke(cli, ["system", "setup"], input=answer)

    assert ran == (["install"] if installs else []), out.output
    assert ("Install the full ffmpeg now" in out.output) is terminal

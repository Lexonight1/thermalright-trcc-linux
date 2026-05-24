"""Theme persistence Commands: SaveTheme / ExportTheme / ImportTheme.

Also exercises ``ThemeService.export`` / ``ThemeService.import_`` directly
since those fill the two ``not yet implemented`` stubs the parent memo
called out.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.commands import ExportTheme, ImportTheme, SaveTheme
from trcc.core.errors import ThemeError
from trcc.core.events import ThemeExported, ThemeImported, ThemeSaved
from trcc.core.models import Theme
from trcc.services.theme import ThemeService

from .conftest import FakePlatform

# ── Helpers ───────────────────────────────────────────────────────────


def _write_theme(directory: Path, name: str = "demo",
                  width: int = 320, height: int = 320) -> Path:
    """Create a minimal theme directory under ``directory``."""
    theme_dir = directory / name
    theme_dir.mkdir(parents=True)
    config = {"name": name, "width": width, "height": height, "elements": []}
    (theme_dir / "trcc.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8",
    )
    (theme_dir / "background.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return theme_dir


@pytest.fixture
def app(tmp_home: Path) -> App:
    return App(platform=FakePlatform(tmp_home))


# Tests pretend the device under key ``0402:3922`` is connected — that
# product is in the registry with native_resolution (320, 320), so
# ``_resolve_resolution`` resolves through the registry fallback without
# the test having to construct a fake device.
_TEST_DEVICE_KEY = "0402:3922"
_TEST_RES = (320, 320)


@pytest.fixture
def user_theme_dir(app: App) -> Path:
    """Per-resolution dir where saved/imported themes land.

    Matches the layout :class:`SaveTheme` / :class:`ImportTheme` write
    to — ``user_content_dir / data / theme{w}{h} / <name>`` for the
    test device's 320×320 resolution.
    """
    w, h = _TEST_RES
    return app.platform.paths().user_theme_dir(w, h)


# ─────────────────────────────────────────────────────────────────────
# ThemeService.export
# ─────────────────────────────────────────────────────────────────────


def test_export_writes_zip_with_expected_members(tmp_home: Path) -> None:
    theme_dir = _write_theme(tmp_home, "demo")
    archive = tmp_home / "demo.tr"

    ThemeService().export(theme_dir, archive)

    assert archive.exists()
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    assert "trcc.json" in names
    assert "background.png" in names


def test_export_rejects_missing_source(tmp_home: Path) -> None:
    missing = tmp_home / "nope"
    archive = tmp_home / "out.tr"

    with pytest.raises(ThemeError, match="does not exist"):
        ThemeService().export(missing, archive)


def test_export_rejects_non_directory_source(tmp_home: Path) -> None:
    file_path = tmp_home / "not_a_dir.txt"
    file_path.write_text("x")

    with pytest.raises(ThemeError, match="not a directory"):
        ThemeService().export(file_path, tmp_home / "out.tr")


def test_export_preserves_subdirectory_structure(tmp_home: Path) -> None:
    """Nested files retain their relative path inside the archive."""
    theme_dir = _write_theme(tmp_home, "nested")
    (theme_dir / "assets").mkdir()
    (theme_dir / "assets" / "logo.png").write_bytes(b"\x89PNG")

    archive = tmp_home / "nested.tr"
    ThemeService().export(theme_dir, archive)

    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    assert "assets/logo.png" in names


# ─────────────────────────────────────────────────────────────────────
# ThemeService.import_
# ─────────────────────────────────────────────────────────────────────


def test_import_unpacks_a_round_tripped_archive(tmp_home: Path) -> None:
    source = _write_theme(tmp_home / "src", "demo")
    archive = tmp_home / "demo.tr"
    ThemeService().export(source, archive)

    target = tmp_home / "imported"
    theme: Theme = ThemeService().import_(archive, target)

    assert theme.name == "demo"
    assert theme.resolution == (320, 320)
    assert (target / "trcc.json").is_file()
    assert (target / "background.png").is_file()


def test_import_rejects_missing_archive(tmp_home: Path) -> None:
    with pytest.raises(ThemeError, match="does not exist"):
        ThemeService().import_(tmp_home / "nope.tr", tmp_home / "out")


def test_import_rejects_existing_target(tmp_home: Path) -> None:
    source = _write_theme(tmp_home / "src", "demo")
    archive = tmp_home / "demo.tr"
    ThemeService().export(source, archive)

    target = tmp_home / "already_there"
    target.mkdir()

    with pytest.raises(ThemeError, match="already exists"):
        ThemeService().import_(archive, target)


def test_import_rejects_invalid_zip(tmp_home: Path) -> None:
    bogus = tmp_home / "garbage.tr"
    bogus.write_bytes(b"not a zip file")
    target = tmp_home / "out"

    with pytest.raises(ThemeError, match="Not a valid zip"):
        ThemeService().import_(bogus, target)

    # Failed extraction must clean up the half-created target.
    assert not target.exists()


def test_import_skips_zip_slip_members(tmp_home: Path) -> None:
    """Members with ``..`` or absolute paths are filtered out silently."""
    archive = tmp_home / "malicious.tr"

    # Build a zip with a valid file + a zip-slip attempt + an absolute path.
    # Also include a valid theme config so .load() succeeds.
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "trcc.json",
            json.dumps({"name": "ok", "width": 320, "height": 320}),
        )
        zf.writestr("../escape.txt", b"would escape")
        zf.writestr("/abs/path.txt", b"absolute")
        zf.writestr("subdir/../../also_escapes.txt", b"sneaky")

    target = tmp_home / "imported"
    theme = ThemeService().import_(archive, target)

    assert theme.name == "ok"
    # Only the safe member made it in
    extracted = {p.relative_to(target).as_posix()
                 for p in target.rglob("*") if p.is_file()}
    assert "trcc.json" in extracted
    # No file with "escape" or "abs" anywhere in the path
    assert all("escape" not in p and "abs" not in p for p in extracted)


def test_import_cleans_up_when_archive_has_no_theme_config(
    tmp_home: Path,
) -> None:
    """Archive that extracts but isn't a valid theme deletes the target."""
    archive = tmp_home / "empty.tr"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("random.txt", b"not a theme")

    target = tmp_home / "imported"
    with pytest.raises(ThemeError):
        ThemeService().import_(archive, target)
    # Cleaned up
    assert not target.exists()


# ─────────────────────────────────────────────────────────────────────
# SaveTheme Command
# ─────────────────────────────────────────────────────────────────────


def test_save_theme_duplicates_active_theme(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    source = _write_theme(tmp_home, "source")
    theme = ThemeService().load(source)
    app.active_themes[_TEST_DEVICE_KEY] = theme

    result = app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="my-copy"))

    assert result.ok is True
    saved = user_theme_dir / "my-copy"
    assert saved.is_dir()
    assert (saved / "trcc.json").is_file()


def test_save_theme_no_active_theme_returns_failure(app: App) -> None:
    result = app.dispatch(SaveTheme(key="0402:3922", name="x"))

    assert result.ok is False
    assert "no active theme" in result.message


@pytest.mark.parametrize("bad_name", [
    "", ".hidden", "../escape", "with/slash", "back\\slash",
    "null\x00byte", "a" * 256,
])
def test_save_theme_rejects_unsafe_name(
    app: App, tmp_home: Path, bad_name: str,
) -> None:
    source = _write_theme(tmp_home, "source")
    theme = ThemeService().load(source)
    app.active_themes["0402:3922"] = theme

    result = app.dispatch(SaveTheme(key="0402:3922", name=bad_name))

    assert result.ok is False
    assert "invalid theme name" in result.message


def test_save_theme_refuses_to_overwrite(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    source = _write_theme(tmp_home, "source")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    # First save succeeds
    app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="dupe"))
    # Second save with same name fails
    result = app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="dupe"))

    assert result.ok is False
    assert "already exists" in result.message


def test_save_theme_publishes_event(
    app: App, tmp_home: Path,
) -> None:
    source = _write_theme(tmp_home, "source")
    app.active_themes["0402:3922"] = ThemeService().load(source)
    events: list[ThemeSaved] = []
    app.events.subscribe(ThemeSaved, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(SaveTheme(key="0402:3922", name="ok-name"))

    assert len(events) == 1
    assert events[0].theme_name == "ok-name"


# ─────────────────────────────────────────────────────────────────────
# ExportTheme Command
# ─────────────────────────────────────────────────────────────────────


def test_export_theme_command_writes_archive(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    user_theme_dir.mkdir(parents=True, exist_ok=True)
    _write_theme(user_theme_dir, "demo")
    archive = tmp_home / "out.tr"

    result = app.dispatch(
        ExportTheme(
            key=_TEST_DEVICE_KEY, theme_name="demo", archive_path=archive,
        ),
    )

    assert result.ok is True
    assert archive.is_file()
    assert result.archive_path == str(archive)


def test_export_theme_unknown_name_returns_failure(
    app: App, tmp_home: Path,
) -> None:
    result = app.dispatch(
        ExportTheme(
            key=_TEST_DEVICE_KEY, theme_name="missing",
            archive_path=tmp_home / "out.tr",
        ),
    )

    assert result.ok is False
    assert "not found" in result.message


@pytest.mark.parametrize("bad_name", ["", "../escape", "with/slash"])
def test_export_theme_rejects_unsafe_name(
    app: App, tmp_home: Path, bad_name: str,
) -> None:
    result = app.dispatch(
        ExportTheme(
            key=_TEST_DEVICE_KEY, theme_name=bad_name,
            archive_path=tmp_home / "out.tr",
        ),
    )

    assert result.ok is False
    assert "invalid theme name" in result.message


def test_export_theme_publishes_event(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    user_theme_dir.mkdir(parents=True, exist_ok=True)
    _write_theme(user_theme_dir, "demo")
    events: list[ThemeExported] = []
    app.events.subscribe(ThemeExported, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(ExportTheme(
        key=_TEST_DEVICE_KEY, theme_name="demo",
        archive_path=tmp_home / "out.tr",
    ))

    assert len(events) == 1
    assert events[0].theme_name == "demo"


# ─────────────────────────────────────────────────────────────────────
# ImportTheme Command
# ─────────────────────────────────────────────────────────────────────


def test_import_theme_command_unpacks_archive(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    source = _write_theme(tmp_home, "src_theme")
    archive = tmp_home / "imported.tr"
    ThemeService().export(source, archive)

    result = app.dispatch(
        ImportTheme(
            key=_TEST_DEVICE_KEY, archive_path=archive, name="my-import",
        ),
    )

    assert result.ok is True
    target = user_theme_dir / "my-import"
    assert target.is_dir()
    assert result.path == str(target)


def test_import_theme_command_defaults_name_to_archive_stem(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    source = _write_theme(tmp_home, "src_theme")
    archive = tmp_home / "snowflake.tr"
    ThemeService().export(source, archive)

    result = app.dispatch(ImportTheme(
        key=_TEST_DEVICE_KEY, archive_path=archive, name="",
    ))

    assert result.ok is True
    assert (user_theme_dir / "snowflake").is_dir()


def test_import_theme_unknown_archive_returns_failure(
    app: App, tmp_home: Path,
) -> None:
    result = app.dispatch(
        ImportTheme(
            key=_TEST_DEVICE_KEY, archive_path=tmp_home / "nope.tr", name="x",
        ),
    )

    assert result.ok is False
    assert "does not exist" in result.message


def test_import_theme_publishes_event(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    source = _write_theme(tmp_home, "src")
    archive = tmp_home / "src.tr"
    ThemeService().export(source, archive)
    events: list[ThemeImported] = []
    app.events.subscribe(ThemeImported, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(ImportTheme(
        key=_TEST_DEVICE_KEY, archive_path=archive, name="evt",
    ))

    assert len(events) == 1
    assert events[0].theme_name == "evt"

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
from trcc.core.commands import ExportTheme, ImportTheme, LoadTheme, SaveTheme
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


def _write_self_contained_theme(directory: Path, name: str = "demo",
                                width: int = 320, height: int = 320) -> Path:
    """A self-contained theme dir: canonical 00.png + 01.png + trcc.json.

    Bytes are placeholders — the export/import path copies them verbatim
    (never opens them), so a real image isn't needed here.
    """
    theme_dir = directory / name
    theme_dir.mkdir(parents=True)
    config = {"name": name, "width": width, "height": height, "elements": []}
    (theme_dir / "trcc.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8",
    )
    (theme_dir / "00.png").write_bytes(b"\x89PNG\r\n\x1a\nBG")
    (theme_dir / "01.png").write_bytes(b"\x89PNG\r\n\x1a\nMASK")
    return theme_dir


@pytest.fixture
def app(tmp_home: Path) -> App:
    """App with a real QtRenderer attached.

    SaveTheme's new write path re-encodes overrides through the
    renderer (guarantees PNG bytes regardless of source extension),
    so tests need an actual renderer.  QtRenderer bootstraps an
    offscreen QGuiApplication on construction — headless-safe.
    """
    from trcc.adapters.render.qt import QtRenderer

    a = App(platform=FakePlatform(tmp_home))
    a.set_renderer(QtRenderer())
    return a


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
    theme_dir = _write_self_contained_theme(tmp_home, "demo")
    archive = tmp_home / "demo.tr"

    ThemeService().export(theme_dir, archive)

    assert archive.exists()
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    # Export produces a canonical self-contained theme.
    assert "trcc.json" in names
    assert "00.png" in names
    assert "01.png" in names


def test_export_dereferences_reference_theme_to_self_contained(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """Export resolves a reference theme's library bg + mask into the
    archive as self-contained 00.png/01.png, so a recipient with NO
    matching library can import + load it (the Phase-E proof)."""
    import json as _json

    # Save a reference theme — its bg + mask land in the user library.
    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)
    assert app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="ref")).ok
    saved = user_theme_dir / "ref"
    saved_manifest = _json.loads(
        (saved / "trcc.json").read_text(encoding="utf-8"),
    )
    assert "background" in saved_manifest and "mask" in saved_manifest
    assert not (saved / "00.png").exists()

    # Export → archive must be self-contained, with refs stripped.
    archive = tmp_home / "ref.tr"
    app.themes.export(saved, archive)
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        exported = _json.loads(zf.read("trcc.json").decode("utf-8"))
    assert "00.png" in names
    assert "01.png" in names
    assert "background" not in exported
    assert "mask" not in exported

    # Import into a FRESH service with NO library — must resolve in-dir.
    fresh = ThemeService()
    recipient = tmp_home / "recipient"
    imported = fresh.import_(archive, recipient)
    assert fresh.background_path(imported) == recipient / "00.png"
    assert fresh.mask_path(imported) == recipient / "01.png"


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


# NB: export no longer preserves arbitrary subdirectory files — Phase E
# made it produce a CANONICAL self-contained theme (00.png / 01.png /
# Theme.png / trcc.json), dereferencing library refs.  The old
# "preserves arbitrary nested files" test was removed with that change.


# ─────────────────────────────────────────────────────────────────────
# ThemeService.import_
# ─────────────────────────────────────────────────────────────────────


def test_import_unpacks_a_round_tripped_archive(tmp_home: Path) -> None:
    source = _write_self_contained_theme(tmp_home / "src", "demo")
    archive = tmp_home / "demo.tr"
    ThemeService().export(source, archive)

    target = tmp_home / "imported"
    theme: Theme = ThemeService().import_(archive, target)

    assert theme.name == "demo"
    assert theme.resolution == (320, 320)
    assert (target / "trcc.json").is_file()
    assert (target / "00.png").is_file()


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


def test_save_theme_flags_existing_then_overwrites(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    source = _write_theme(tmp_home, "source")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    # First save succeeds
    assert app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="dupe")).ok

    # Second save with the same name is refused — but flagged as a name
    # collision (target_exists) so the UI can offer a one-click overwrite
    # instead of forcing the user to invent a new name.
    result = app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="dupe"))
    assert result.ok is False
    assert result.target_exists is True
    assert "already exists" in result.message

    # Explicit overwrite replaces the existing theme and succeeds.
    overwritten = app.dispatch(
        SaveTheme(key=_TEST_DEVICE_KEY, name="dupe", overwrite=True))
    assert overwritten.ok is True
    assert overwritten.target_exists is False


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


def test_reference_theme_resolves_assets_from_user_library(
    app: App, tmp_home: Path,
) -> None:
    """A reference theme resolves its background + mask from the user
    library (web/{res}, web/zt{res}), not from inside the theme dir."""
    paths = app.platform.paths()
    w, h = _TEST_RES
    bg_dir = paths.user_background_dir(w, h)
    bg_dir.mkdir(parents=True)
    (bg_dir / "a042.mp4").write_bytes(b"VIDEO")
    mask_dir = paths.user_mask_dir(w, h) / "m007"
    mask_dir.mkdir(parents=True)
    (mask_dir / "01.png").write_bytes(b"\x89PNG\r\n\x1a\nMASK")

    theme = Theme(
        path=tmp_home / "ref-theme",
        name="ref-theme",
        resolution=_TEST_RES,
        config={
            "name": "ref-theme",
            "background": f"web/{w}{h}/a042.mp4",
            "mask": f"web/zt{w}{h}/m007",
        },
    )
    svc = ThemeService(paths)
    assert svc.background_path(theme) == (bg_dir / "a042.mp4").resolve()
    assert svc.mask_path(theme) == (mask_dir / "01.png").resolve()


# ─────────────────────────────────────────────────────────────────────
# Phase C — content-hash library writers
# ─────────────────────────────────────────────────────────────────────


def test_store_background_round_trips_through_resolver(
    app: App, tmp_home: Path,
) -> None:
    """A stored background lands under user_background_dir and a
    reference theme naming the returned ref resolves back to it."""
    paths = app.platform.paths()
    w, h = _TEST_RES
    svc = ThemeService(paths)
    data = b"\x89PNG\r\n\x1a\nBACKGROUND"

    ref = svc.store_background(data, ".png", w, h)

    files = list(paths.user_background_dir(w, h).iterdir())
    assert len(files) == 1
    assert files[0].suffix == ".png"
    assert files[0].read_bytes() == data
    assert ref == f"web/{w}{h}/{files[0].name}"
    theme = Theme(
        path=tmp_home / "ref", name="ref", resolution=_TEST_RES,
        config={"name": "ref", "background": ref},
    )
    assert svc.background_path(theme) == files[0].resolve()


def test_store_background_dedups_identical_bytes(app: App) -> None:
    """Storing identical bytes twice yields one file and the same ref."""
    paths = app.platform.paths()
    w, h = _TEST_RES
    svc = ThemeService(paths)
    data = b"\x89PNG\r\n\x1a\nSAME"

    ref1 = svc.store_background(data, ".png", w, h)
    ref2 = svc.store_background(data, ".png", w, h)

    assert ref1 == ref2
    assert len(list(paths.user_background_dir(w, h).iterdir())) == 1


def test_store_background_rejects_unknown_ext(app: App) -> None:
    """A background ext outside the shippable allowlist is refused."""
    svc = ThemeService(app.platform.paths())
    with pytest.raises(ThemeError):
        svc.store_background(b"x", ".exe", *_TEST_RES)


def test_store_mask_round_trips_through_resolver(
    app: App, tmp_home: Path,
) -> None:
    """A stored mask lands at user_mask_dir/<id>/{01.png,config1.dc} and a
    reference theme naming the returned ref resolves back to its 01.png."""
    paths = app.platform.paths()
    w, h = _TEST_RES
    svc = ThemeService(paths)
    image = b"\x89PNG\r\n\x1a\nMASKBYTES"
    dc = b"\xddDCBYTES"

    ref = svc.store_mask(image, w, h, dc=dc)

    dirs = list(paths.user_mask_dir(w, h).iterdir())
    assert len(dirs) == 1
    asset_dir = dirs[0]
    assert (asset_dir / "01.png").read_bytes() == image
    assert (asset_dir / "config1.dc").read_bytes() == dc
    assert ref == f"web/zt{w}{h}/{asset_dir.name}"
    theme = Theme(
        path=tmp_home / "ref", name="ref", resolution=_TEST_RES,
        config={"name": "ref", "mask": ref},
    )
    assert svc.mask_path(theme) == (asset_dir / "01.png").resolve()


def test_store_mask_dedups_identical_image(app: App) -> None:
    """Identical mask images dedup to one <id> directory."""
    paths = app.platform.paths()
    w, h = _TEST_RES
    svc = ThemeService(paths)
    image = b"\x89PNG\r\n\x1a\nDUP"

    ref1 = svc.store_mask(image, w, h)
    ref2 = svc.store_mask(image, w, h)

    assert ref1 == ref2
    assert len(list(paths.user_mask_dir(w, h).iterdir())) == 1


def test_library_writers_require_paths() -> None:
    """Writers raise without a Paths port — a library write with no root
    is a wiring bug, not a user error."""
    svc = ThemeService()
    with pytest.raises(RuntimeError):
        svc.store_background(b"x", ".png", *_TEST_RES)
    with pytest.raises(RuntimeError):
        svc.store_mask(b"x", *_TEST_RES)


def _write_theme_with_mask(directory: Path, name: str = "masked",
                           width: int = 320, height: int = 320) -> Path:
    """Source theme dir carrying its own background (00.png) + mask (01.png)."""
    theme_dir = directory / name
    theme_dir.mkdir(parents=True)
    config = {
        "name": name, "width": width, "height": height,
        "elements": [], "mask_visible": True,
    }
    (theme_dir / "trcc.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8",
    )
    # Real PNGs (distinct colors) — SaveTheme re-encodes the resolved
    # background/mask through the renderer into the library, so the source
    # assets must be openable images, not placeholder bytes.
    (theme_dir / "00.png").write_bytes(_png_bytes(red=0x10))
    (theme_dir / "01.png").write_bytes(_png_bytes(red=0x20))
    return theme_dir


def test_save_theme_repoints_active_theme_to_saved_dir(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """After save, the live active theme must point at the SAVED dir.

    Regression: SaveTheme re-pointed only the persisted current_theme
    string, leaving app.active_themes[key] on the SOURCE theme.  The
    next render then resolved the mask from the source instead of the
    saved theme's, so the displayed mask "reverted" to the source
    immediately after saving.
    """
    source = _write_theme_with_mask(tmp_home, "source")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    result = app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="saved-copy"))

    assert result.ok is True
    saved = user_theme_dir / "saved-copy"
    active = app.active_themes[_TEST_DEVICE_KEY]
    # The live object the renderer reads is the SAVED theme, not the source.
    assert active.path == saved
    assert active.path != source
    # The saved theme references its mask in the user library (not an
    # in-dir 01.png), and that ref resolves through the paths-injected
    # service — never back to the source theme's mask.
    assert not (saved / "01.png").exists()
    w, h = _TEST_RES
    mask = app.themes.mask_path(active)
    assert mask is not None
    assert mask.parent.parent == app.platform.paths().user_mask_dir(w, h)
    assert mask != source / "01.png"


def _write_theme_with_dc(directory: Path, name: str = "withdc",
                          width: int = 320, height: int = 320) -> Path:
    """Source theme carrying one bundled clock element in its layout.

    The layout lives in ``trcc.json`` (next/'s rendered source of truth —
    ``load()`` prefers it over any ``config1.dc``), so SaveTheme inlines
    that clock into the saved manifest's ``elements``.
    """
    theme_dir = directory / name
    theme_dir.mkdir(parents=True)
    config = {
        "name": name, "width": width, "height": height,
        "overlay_enabled": True,
        "elements": [{
            "type": "clock", "x": 100, "y": 100, "color": "#ffffff",
            "size": 24, "bold": False, "italic": False, "source": "time",
        }],
    }
    (theme_dir / "trcc.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8",
    )
    (theme_dir / "background.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return theme_dir


def test_save_theme_bakes_user_overlay_elements_into_manifest(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """SaveTheme must persist user_overlay_elements into the saved manifest.

    Without this the saved theme loses the user's customisations — they
    live in DeviceSettings, never in the theme's layout, so they don't
    round-trip when the saved theme is re-loaded.  Reported by the user
    2026-05-26 ("when i save a custom theme it always saves as local
    theme1").
    """
    import json as _json

    from trcc.core.models import OverlayElement

    # Source theme has one clock element in its layout.
    source = _write_theme_with_dc(tmp_home, "source")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    # User adds a custom text element to the device's overlay layer.
    app.settings.add_user_overlay_element(
        _TEST_DEVICE_KEY,
        OverlayElement(
            id="user_text_1", type="text",
            x=50, y=50, color="#ff8800", size=18,
            bold=True, italic=False, text="CUSTOM",
        ),
    )

    result = app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="my-edits"))
    assert result.ok is True

    saved = user_theme_dir / "my-edits"
    manifest = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
    elements = manifest["elements"]
    # Source theme clock + user text element should both be present.
    types = [e["type"] for e in elements]
    assert "clock" in types
    assert "text" in types
    user_text = next(e for e in elements if e["type"] == "text")
    assert user_text["text"] == "CUSTOM"
    assert user_text["color"] == "#ff8800"
    # Reference format writes no DC file — load() reads trcc.json.
    assert not (saved / "config1.dc").exists()
    assert user_text["x"] == 50 and user_text["y"] == 50


def test_save_theme_without_user_edits_inlines_source_layout(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """No user_overlay_elements → saved manifest inlines just the source
    layout, with nothing appended."""
    import json as _json

    source = _write_theme_with_dc(tmp_home, "source")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="no-edits"))

    manifest = _json.loads(
        (user_theme_dir / "no-edits" / "trcc.json").read_text(encoding="utf-8"),
    )
    # Exactly the one original element, nothing appended.
    assert len(manifest["elements"]) == 1
    assert manifest["elements"][0]["type"] == "clock"


def test_save_theme_clears_user_overlay_after_bake(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """Successful bake → user_overlay_elements in DeviceSettings cleared.

    Otherwise the baked elements would render TWICE on next theme load
    (once from the DC, once from live DeviceSettings).
    """
    from trcc.core.models import OverlayElement

    source = _write_theme_with_dc(tmp_home, "source")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)
    app.settings.add_user_overlay_element(
        _TEST_DEVICE_KEY,
        OverlayElement(
            id="ed1", type="text", x=10, y=10, color="#fff",
            size=12, bold=False, italic=False, text="hi",
        ),
    )
    assert len(app.settings.for_device(_TEST_DEVICE_KEY).user_overlay_elements) == 1

    app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="clear-test"))

    assert app.settings.for_device(_TEST_DEVICE_KEY).user_overlay_elements == []


def test_save_theme_repoints_current_theme(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """Successful save → DeviceSettings.current_theme points at the saved dir."""
    from trcc.core.models import OverlayElement

    source = _write_theme_with_dc(tmp_home, "source")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)
    app.settings.add_user_overlay_element(
        _TEST_DEVICE_KEY,
        OverlayElement(
            id="ed1", type="text", x=5, y=5, color="#fff",
            size=10, bold=False, italic=False, text="x",
        ),
    )

    app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="repoint-test"))

    expected = str((user_theme_dir / "repoint-test").resolve())
    assert app.settings.for_device(_TEST_DEVICE_KEY).current_theme == expected


# ─────────────────────────────────────────────────────────────────────
# SaveTheme — current-state capture (cloud bg + cloud mask overrides)
# ─────────────────────────────────────────────────────────────────────


def _png_bytes(red: int = 0) -> bytes:
    """Tiny valid 1×1 PNG with R=red.  Distinct red bytes prove
    file-content provenance in cloud-override tests."""
    # Pre-computed 1x1 RGBA PNGs; first byte after IDAT distinguishes them
    # well enough for test identity checks via length + leading bytes.
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage
    img = QImage(1, 1, QImage.Format.Format_ARGB32)
    img.fill((red << 16) | 0xFF000000)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data().data())


def _write_theme_with_real_pngs(directory: Path, name: str = "src",
                                  width: int = 320, height: int = 320) -> Path:
    """Theme dir with real PNGs at the canonical names (00.png / 01.png)
    and a one-clock layout in ``trcc.json``.

    SaveTheme re-encodes the resolved background/mask into the library
    and inlines this layout into the saved manifest.
    """
    theme_dir = directory / name
    theme_dir.mkdir(parents=True)
    config = {
        "name": name, "width": width, "height": height,
        "overlay_enabled": True,
        "elements": [{
            "type": "clock", "x": 100, "y": 100, "color": "#ffffff",
            "size": 24, "bold": False, "italic": False, "source": "time",
        }],
    }
    (theme_dir / "trcc.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8",
    )
    (theme_dir / "00.png").write_bytes(_png_bytes(red=0x10))
    (theme_dir / "01.png").write_bytes(_png_bytes(red=0x20))
    return theme_dir


def test_save_theme_bakes_cloud_background_override(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """``DeviceSettings.background_path`` set → saved theme REFERENCES the
    override's content in the library, NOT the source theme's bg.

    Reproduces the user-reported bug: select cloud background → save →
    new theme should resolve to the cloud bg, not the local Theme1's bg.
    """
    import json as _json

    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    cloud_bg = tmp_home / "cloud_pool" / "fancy_bg.png"
    cloud_bg.parent.mkdir(parents=True)
    cloud_bg.write_bytes(_png_bytes(red=0x99))
    app.settings.set_background_path(_TEST_DEVICE_KEY, str(cloud_bg))

    result = app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="with-cloud-bg"))
    assert result.ok is True

    saved = user_theme_dir / "with-cloud-bg"
    manifest = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
    # Background is a library ref, not a bundled 00.png.
    assert manifest["background"].startswith(f"web/{_TEST_RES[0]}{_TEST_RES[1]}/")
    assert not (saved / "00.png").exists()
    # The referenced asset resolves to the cloud override, not the source bg.
    resolved = app.themes.background_path(app.themes.load(saved))
    assert resolved is not None
    assert resolved.read_bytes() != (source / "00.png").read_bytes()


def test_save_theme_keeps_cloud_video_at_theme_ext(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """Cloud video bg → ``Theme.mp4`` copied verbatim (no re-encode)."""
    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    cloud_video = tmp_home / "cloud_pool" / "loop.mp4"
    cloud_video.parent.mkdir(parents=True)
    fake_mp4_bytes = b"\x00\x00\x00\x20ftypisom..." * 100
    cloud_video.write_bytes(fake_mp4_bytes)
    app.settings.set_background_path(_TEST_DEVICE_KEY, str(cloud_video))

    app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="with-cloud-video"))

    saved_video = user_theme_dir / "with-cloud-video" / "Theme.mp4"
    assert saved_video.is_file()
    assert saved_video.read_bytes() == fake_mp4_bytes


def test_save_theme_bakes_cloud_mask_override(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """``DeviceSettings.mask_path`` set → saved theme REFERENCES the override
    mask in the library, NOT the source theme's mask."""
    import json as _json

    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    cloud_mask = tmp_home / "cloud_masks" / "circle.png"
    cloud_mask.parent.mkdir(parents=True)
    cloud_mask.write_bytes(_png_bytes(red=0xAA))
    app.settings.set_mask_path(_TEST_DEVICE_KEY, str(cloud_mask))

    app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="with-cloud-mask"))

    saved = user_theme_dir / "with-cloud-mask"
    manifest = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
    assert manifest["mask"].startswith(f"web/zt{_TEST_RES[0]}{_TEST_RES[1]}/")
    assert not (saved / "01.png").exists()
    resolved = app.themes.mask_path(app.themes.load(saved))
    assert resolved is not None
    assert resolved.read_bytes() != (source / "01.png").read_bytes()


def test_save_theme_inlines_mask_overlay_elements_into_manifest(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """``DeviceSettings.mask_overlay_elements`` set → manifest elements come
    from the mask layout (REPLACE source theme's elements), matching the
    runtime ``_build_overlay`` precedence.
    """
    import json as _json

    from trcc.core.models import OverlayElement

    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    # Mask brings its own layout — text element with distinctive value.
    app.settings.set_mask_overlay_elements(
        _TEST_DEVICE_KEY,
        [OverlayElement(
            id="mask_e1", type="text", x=200, y=200, color="#ff00aa",
            size=20, bold=False, italic=False, text="MASK_LAYOUT",
        )],
    )

    app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="with-mask-dc"))

    saved = user_theme_dir / "with-mask-dc"
    manifest = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
    types = [e["type"] for e in manifest["elements"]]
    texts = [e.get("text") for e in manifest["elements"]]
    # Source theme's clock element MUST be absent — mask layout replaces it.
    assert "clock" not in types
    assert "MASK_LAYOUT" in texts


def test_save_theme_clears_all_overrides_after_save(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """All four overrides cleared after a successful save with all set."""
    from trcc.core.models import OverlayElement

    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    cloud_bg = tmp_home / "pool" / "bg.png"
    cloud_mask = tmp_home / "pool" / "mask.png"
    cloud_bg.parent.mkdir(parents=True)
    cloud_bg.write_bytes(_png_bytes(red=0x33))
    cloud_mask.write_bytes(_png_bytes(red=0x44))
    app.settings.set_background_path(_TEST_DEVICE_KEY, str(cloud_bg))
    app.settings.set_mask_path(_TEST_DEVICE_KEY, str(cloud_mask))
    app.settings.set_mask_overlay_elements(
        _TEST_DEVICE_KEY,
        [OverlayElement(
            id="m1", type="text", x=1, y=1, color="#fff",
            size=12, bold=False, italic=False, text="m",
        )],
    )
    app.settings.add_user_overlay_element(
        _TEST_DEVICE_KEY,
        OverlayElement(
            id="u1", type="text", x=2, y=2, color="#fff",
            size=12, bold=False, italic=False, text="u",
        ),
    )

    app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="full-state"))

    s = app.settings.for_device(_TEST_DEVICE_KEY)
    assert s.background_path is None
    assert s.mask_path is None
    assert s.mask_overlay_elements is None
    assert s.user_overlay_elements == []
    assert s.current_theme == str((user_theme_dir / "full-state").resolve())


def test_save_theme_snapshots_preview_as_thumbnail(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """With a device present, SaveTheme writes Theme.png — a real PNG
    snapshot of the preview composite — so the saved theme shows a grid
    tile in the chooser, just like shipped local themes."""
    from trcc.core.models import Kind, ProductInfo, Wire

    class _StubDevice:
        info = ProductInfo(
            vid=0x0402, pid=0x3922, vendor="Test", product="Stub",
            wire=Wire.SCSI, kind=Kind.LCD, native_resolution=_TEST_RES,
            orientations=(0, 90, 180, 270), native_orientation="landscape",
        )
        profile = None
        is_connected = True
        key = _TEST_DEVICE_KEY

    app.devices[_TEST_DEVICE_KEY] = _StubDevice()  # type: ignore[assignment]
    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    assert app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="snap")).ok

    thumb = user_theme_dir / "snap" / "Theme.png"
    assert thumb.is_file()
    # Real PNG snapshot, not a copied source thumbnail.
    assert thumb.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_save_then_reload_resolves_library_assets_not_source(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """The original-bug regression, end to end.

    Save a theme whose background + mask are cloud overrides, then RELOAD
    it from disk.  The reloaded theme must resolve its background and mask
    from the user library (the overrides) — never reverting to the source
    theme's bundled assets.  Also exercises the Phase-D gotcha: LoadTheme
    must resolve the *relative* mask ref through ``ApplyMask`` (a plain
    ``Path("web/...")`` would never load).
    """
    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    cloud_bg = tmp_home / "pool" / "bg.png"
    cloud_mask = tmp_home / "pool" / "mask.png"
    cloud_bg.parent.mkdir(parents=True)
    cloud_bg.write_bytes(_png_bytes(red=0x99))
    cloud_mask.write_bytes(_png_bytes(red=0xAA))
    app.settings.set_background_path(_TEST_DEVICE_KEY, str(cloud_bg))
    app.settings.set_mask_path(_TEST_DEVICE_KEY, str(cloud_mask))

    assert app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="roundtrip")).ok
    saved = user_theme_dir / "roundtrip"

    # Simulate a fresh reload: forget the live theme, then load from disk.
    del app.active_themes[_TEST_DEVICE_KEY]
    result = app.dispatch(LoadTheme(key=_TEST_DEVICE_KEY, path=saved))
    assert result.ok is True

    active = app.active_themes[_TEST_DEVICE_KEY]
    assert active.path == saved
    paths = app.platform.paths()

    # Background resolves to the library override, not the source bg.
    bg = app.themes.background_path(active)
    assert bg is not None
    assert bg.parent == paths.user_background_dir(*_TEST_RES).resolve()
    assert bg.read_bytes() != (source / "00.png").read_bytes()

    # LoadTheme resolved the relative mask ref + applied it (the gotcha):
    # DeviceSettings.mask_path now points at the library mask, not source.
    applied_mask = app.settings.for_device(_TEST_DEVICE_KEY).mask_path
    assert applied_mask is not None
    assert Path(applied_mask).parent.parent == paths.user_mask_dir(*_TEST_RES).resolve()
    assert Path(applied_mask).read_bytes() != (source / "01.png").read_bytes()


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

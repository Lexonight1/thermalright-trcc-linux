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
from trcc.core.commands import (
    ExportTheme,
    ImportTheme,
    LoadTheme,
    RestoreLastTheme,
    SaveTheme,
)
from trcc.core.errors import ThemeError
from trcc.core.events import ThemeExported, ThemeImported, ThemeSaved
from trcc.core.models import Theme
from trcc.services.settings import Settings
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


def test_export_self_contained_theme_produces_importable_archive(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """A saved theme REFERENCES its bg/mask in the user library (no in-dir
    copies), yet export must DEREFERENCE them into a self-contained archive a
    recipient with NO matching library can import + load (the Phase-E proof)."""
    import json as _json

    # Save a theme — its bg AND mask are stored in the USER library and the
    # saved config just POINTS at them by URI (web/{w}{h}/… refs); the saved
    # dir carries no in-dir 00.png/01.png.
    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)
    assert app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="ref")).ok
    saved = user_theme_dir / "ref"
    saved_manifest = _json.loads(
        (saved / "trcc.json").read_text(encoding="utf-8"),
    )
    w, h = _TEST_RES
    assert saved_manifest["background"].startswith(f"web/{w}{h}/")   # library ref
    assert saved_manifest["background"].endswith(".png")
    assert saved_manifest["mask"].startswith(f"web/zt{w}{h}/")       # library ref
    assert not (saved / "00.png").exists()        # referenced, never bundled
    assert not (saved / "01.png").exists()

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
    # The source's own bundled mask is stored in the USER library and the saved
    # config REFERENCES it — no in-dir 01.png copy.
    assert not (saved / "01.png").exists()
    mask = app.themes.mask_path(active)
    assert mask is not None
    assert mask.is_relative_to(app.platform.paths().user_mask_dir(*_TEST_RES))
    assert mask.read_bytes() == (source / "01.png").read_bytes()


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


def test_save_theme_bakes_user_overlay_layout_into_manifest(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """SaveTheme bakes the user's overlay layout, matching what's rendered.

    Single-layout model (see ``resolve_overlay_elements``): when the user
    has edits, the user layer IS the full overlay layout — the GUI grid is
    seeded from the theme's elements, so an edit produces a complete layout,
    not an "extra" on top.  SaveTheme must inline EXACTLY that layout (what
    was on screen), with no element duplicated by stacking theme + user.
    Originally reported 2026-05-26 ("custom theme always saves as theme1");
    this lock updated 2026-06-05 when render switched theme+user stacking →
    single-layout replace.
    """
    import json as _json

    from trcc.core.models import OverlayElement

    source = _write_theme_with_dc(tmp_home, "source")  # bundled clock
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    # The GUI grid seeds from the theme then dispatches the full grid, so
    # the user layer carries the theme's clock (kept) PLUS the new text.
    app.settings.set_user_overlay_elements(_TEST_DEVICE_KEY, [
        OverlayElement(id="clk", type="clock", x=100, y=100, source="time"),
        OverlayElement(
            id="user_text_1", type="text", x=50, y=50, color="#ff8800",
            size=18, bold=True, italic=False, text="CUSTOM",
        ),
    ])

    result = app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="my-edits"))
    assert result.ok is True

    saved = user_theme_dir / "my-edits"
    manifest = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
    elements = manifest["elements"]
    # Exactly the user's two-element layout — clock kept, text added, NO
    # duplicate clock from stacking the theme's own elements underneath.
    types = sorted(e["type"] for e in elements)
    assert types == ["clock", "text"], f"expected one clock + one text, got {types}"
    user_text = next(e for e in elements if e["type"] == "text")
    assert user_text["text"] == "CUSTOM"
    assert user_text["color"] == "#ff8800"
    assert user_text["x"] == 50 and user_text["y"] == 50
    # Reference format writes no DC file — load() reads trcc.json.
    assert not (saved / "config1.dc").exists()


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


def test_explicit_load_clears_user_edits_restore_preserves(
    app: App, tmp_home: Path,
) -> None:
    """Source-change semantics for the single overlay-layout model.

    An explicit ``LoadTheme`` (the user picking a theme) drops live edits so
    the new theme shows its own layout.  ``RestoreLastTheme`` (reconnect /
    restart) re-runs LoadTheme with ``reset_overrides=False`` and must PRESERVE
    the persisted edits — legacy restored the saved overlay config on connect.
    """
    from trcc.core.models import OverlayElement

    source = _write_theme_with_dc(tmp_home, "source")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)
    app.settings.add_user_overlay_element(
        _TEST_DEVICE_KEY,
        OverlayElement(id="edit1", type="text", x=5, y=5, text="X"),
    )

    # Explicit switch → edits cleared.
    app.dispatch(LoadTheme(key=_TEST_DEVICE_KEY, path=source))
    assert app.settings.for_device(_TEST_DEVICE_KEY).user_overlay_elements == []

    # New edit, then a reconnect-style restore → edit survives.
    app.settings.set_current_theme(_TEST_DEVICE_KEY, str(source.resolve()))
    app.settings.add_user_overlay_element(
        _TEST_DEVICE_KEY,
        OverlayElement(id="edit2", type="text", x=6, y=6, text="Y"),
    )
    app.dispatch(RestoreLastTheme(key=_TEST_DEVICE_KEY))
    preserved = app.settings.for_device(_TEST_DEVICE_KEY).user_overlay_elements
    assert [e.id for e in preserved] == ["edit2"], (
        "RestoreLastTheme (reconnect) must keep the user's persisted edits"
    )


def test_restore_keeps_cloud_background_explicit_load_clears_it(
    app: App, tmp_home: Path,
) -> None:
    """A restore (reconnect / view-switch keep-alive) keeps the user's cloud
    background; an explicit LoadTheme reverts to the theme's bundled one.

    Reported 2026-06-05: switching to the System-Info tab reverted the chosen
    background.  The view-switch ran RestoreLastTheme → LoadTheme → StopVideo,
    which cleared background_path.  StopVideo now runs only on an explicit load
    (reset_overrides=True).
    """
    source = _write_theme_with_dc(tmp_home, "source")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)
    app.settings.set_current_theme(_TEST_DEVICE_KEY, str(source.resolve()))
    app.settings.set_background_path(_TEST_DEVICE_KEY, "/some/cloud/a078.mp4")

    # Restore (reset_overrides=False) — the cloud background survives.
    app.dispatch(RestoreLastTheme(key=_TEST_DEVICE_KEY))
    assert (app.settings.for_device(_TEST_DEVICE_KEY).background_path
            == "/some/cloud/a078.mp4"), (
        "RestoreLastTheme must NOT clear the user's cloud background"
    )

    # Explicit load (default reset_overrides=True) reverts to the theme's bg.
    app.dispatch(LoadTheme(key=_TEST_DEVICE_KEY, path=source))
    assert app.settings.for_device(_TEST_DEVICE_KEY).background_path is None


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
    w, h = _TEST_RES
    # Background is stored in the USER library; the config REFERENCES it — the
    # override's content is copied into the library, not bundled in-dir.
    assert manifest["background"].startswith(f"web/{w}{h}/")
    assert not (saved / "00.png").exists()
    # The referenced asset resolves under the user library, to the cloud
    # override's content — not the source bg.
    resolved = app.themes.background_path(app.themes.load(saved))
    assert resolved is not None
    assert resolved.is_relative_to(app.platform.paths().user_background_dir(w, h))
    assert resolved.read_bytes() != (source / "00.png").read_bytes()


def test_save_theme_references_cloud_video_from_library(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """Cloud video bg → stored verbatim in the USER library (no re-encode) and
    the saved config REFERENCES it (web/{w}{h}/<id>.mp4) — no in-dir Theme.mp4."""
    import json as _json

    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    cloud_video = tmp_home / "cloud_pool" / "loop.mp4"
    cloud_video.parent.mkdir(parents=True)
    fake_mp4_bytes = b"\x00\x00\x00\x20ftypisom..." * 100
    cloud_video.write_bytes(fake_mp4_bytes)
    app.settings.set_background_path(_TEST_DEVICE_KEY, str(cloud_video))

    app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="with-cloud-video"))

    w, h = _TEST_RES
    saved = user_theme_dir / "with-cloud-video"
    manifest = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
    assert manifest["background"].startswith(f"web/{w}{h}/")
    assert manifest["background"].endswith(".mp4")
    assert not (saved / "Theme.mp4").exists()      # referenced, never bundled
    # The library asset carries the video verbatim (no re-encode).
    resolved = app.themes.video_path(app.themes.load(saved))
    assert resolved is not None
    assert resolved.is_relative_to(app.platform.paths().user_background_dir(w, h))
    assert resolved.read_bytes() == fake_mp4_bytes


def test_resave_onto_self_keeps_referenced_background(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """Re-saving a saved theme onto its own name must NOT lose its background.

    Reproduces the data-loss bug: the first save stores the cloud video in the
    USER library and points the config at it (web/{w}{h}/<id>.mp4), clears the
    bg override, and re-points the active theme at the saved dir.  The SECOND
    save then has source == target with no override.  Because the asset lives in
    the library (not in-dir), overwriting the theme dir cannot destroy it — the
    resave keeps ``manifest["background"]`` pointing at the resolvable library
    asset (no black canvas on reload).
    """
    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    cloud_video = tmp_home / "cloud_pool" / "loop.mp4"
    cloud_video.parent.mkdir(parents=True)
    video_bytes = b"\x00\x00\x00\x20ftypisom..." * 100
    cloud_video.write_bytes(video_bytes)
    app.settings.set_background_path(_TEST_DEVICE_KEY, str(cloud_video))

    # First save stores the video in the library + references it, clears the
    # override, and re-points the active theme at the saved dir → next save has
    # source == target.
    assert app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="loop")).ok
    saved = user_theme_dir / "loop"

    def _bg_ref(theme_dir: Path) -> str:
        manifest = json.loads((theme_dir / "trcc.json").read_text("utf-8"))
        return manifest["background"]

    w, h = _TEST_RES
    first_ref = _bg_ref(saved)
    assert first_ref.startswith(f"web/{w}{h}/") and first_ref.endswith(".mp4")
    assert not (saved / "Theme.mp4").exists()      # referenced, never bundled
    assert app.themes.background_path(app.themes.load(saved)) is not None
    assert app.settings.for_device(_TEST_DEVICE_KEY).background_path is None
    assert app.active_themes[_TEST_DEVICE_KEY].path == saved

    # Re-save onto the same name (overwrite) — the background ref must survive.
    assert app.dispatch(
        SaveTheme(key=_TEST_DEVICE_KEY, name="loop", overwrite=True)
    ).ok
    assert _bg_ref(saved) == first_ref             # same library asset, no loss
    resolved = app.themes.background_path(app.themes.load(saved))
    assert resolved is not None and resolved.exists()


def test_save_theme_references_catalog_image_background(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """A STATIC IMAGE background that is ALREADY a catalog/library asset keeps
    its existing library ref (no re-copy) — the saved theme just POINTS at it by
    URI.  A re-save keeps the same ref; the background survives overwrite.
    """
    import json as _json

    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    # A user-library image lives under user_background_dir (user_data/web/{w}{h}).
    w, h = _TEST_RES
    lib_dir = app.platform.paths().user_background_dir(w, h)
    lib_dir.mkdir(parents=True, exist_ok=True)
    lib_image = lib_dir / "a023.png"
    lib_image.write_bytes(_png_bytes(red=0x30))
    app.settings.set_background_path(_TEST_DEVICE_KEY, str(lib_image))

    assert app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="catref")).ok
    saved = user_theme_dir / "catref"
    manifest = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
    assert manifest["background"] == f"web/{w}{h}/a023.png"   # existing lib ref
    assert not (saved / "00.png").exists()          # referenced, never bundled
    assert (app.themes.background_path(app.themes.load(saved)) == lib_image)
    assert lib_image.read_bytes() == _png_bytes(red=0x30)

    # Re-save onto the same name → ref persists, no loss.
    assert app.dispatch(
        SaveTheme(key=_TEST_DEVICE_KEY, name="catref", overwrite=True)
    ).ok
    manifest2 = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
    assert manifest2["background"] == f"web/{w}{h}/a023.png"
    assert lib_image.read_bytes() == _png_bytes(red=0x30)


def test_save_theme_references_non_catalog_mask_override(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """A mask override NOT in a catalog (an arbitrary file the user picked) is
    COPIED into the USER mask library and the saved config REFERENCES it
    (web/zt{w}{h}/<id>); it resolves to the override content."""
    import json as _json

    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)

    override = tmp_home / "picked" / "circle.png"
    override.parent.mkdir(parents=True)
    override.write_bytes(_png_bytes(red=0xAA))
    app.settings.set_mask_path(_TEST_DEVICE_KEY, str(override))

    app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="with-override"))

    w, h = _TEST_RES
    saved = user_theme_dir / "with-override"
    manifest = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
    assert manifest["mask"].startswith(f"web/zt{w}{h}/")   # library ref
    assert not (saved / "01.png").exists()          # referenced, never bundled
    # Resolves under the user mask library, to the override content, never the
    # source theme's mask.
    resolved = app.themes.mask_path(app.themes.load(saved))
    assert resolved is not None
    assert resolved.is_relative_to(app.platform.paths().user_mask_dir(w, h))
    assert resolved.read_bytes() == override.read_bytes()
    assert resolved.read_bytes() != (source / "01.png").read_bytes()


def test_saved_pure_pointer_theme_renders_its_referenced_mask(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """A saved theme's REFERENCED mask (a library asset, no in-dir 01.png) must
    still COMPOSITE through the real render pipeline — not just resolve as a path.

    Rendered through the real QtRenderer, the frame WITH the mask must differ
    from the frame with mask_visible=False (the mask contributes real pixels).
    build_frame's cache keys on mask state, so the second call rebuilds.
    """
    from trcc.core.models import Kind, ProductInfo, Wire

    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)
    assert app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="ptr")).ok
    saved = user_theme_dir / "ptr"

    # Referenced: the mask lives in the user library, not in-dir.
    assert not (saved / "01.png").exists()
    reloaded = app.themes.load(saved)
    mask = app.themes.mask_path(reloaded)
    assert mask is not None and mask.exists()
    assert mask.is_relative_to(app.platform.paths().user_mask_dir(*_TEST_RES))

    info = ProductInfo(
        vid=0x0402, pid=0x3922, vendor="ALi Corp", product="320×320 LCD",
        wire=Wire.SCSI, kind=Kind.LCD, device_type=1, fbl=100,
        native_resolution=_TEST_RES, orientations=(0, 90, 180, 270),
    )

    app.settings.set_mask_visible(_TEST_DEVICE_KEY, True)
    frame_with = app.display.build_frame(info=info, theme=reloaded, sensors={})
    app.settings.set_mask_visible(_TEST_DEVICE_KEY, False)
    frame_without = app.display.build_frame(info=info, theme=reloaded, sensors={})

    assert frame_with and frame_without
    assert frame_with != frame_without, (
        "a saved theme's REFERENCED mask must composite real pixels into the "
        "frame — not silently drop out"
    )


def test_video_path_resolves_referenced_video_background(app: App) -> None:
    """A saved theme records a VIDEO background as a ref (web/{res}/<id>.mp4),
    not a local Theme.mp4 — so ``video_path`` must resolve it, or ``LoadTheme``
    hands an .mp4 to the static-image path and the background never plays
    (reported: 'saved themes have no background remembered')."""
    from trcc.core.models import Theme

    w, h = _TEST_RES
    vid = app.platform.paths().data_dir() / "web" / f"{w}{h}" / "a023.mp4"
    vid.parent.mkdir(parents=True, exist_ok=True)
    vid.write_bytes(b"\x00\x00\x00\x20ftypisom")
    theme = Theme(
        path=app.platform.paths().user_theme_dir(w, h) / "vidref",
        name="vidref", resolution=(w, h),
        config={"background": f"web/{w}{h}/a023.mp4", "elements": []},
    )
    assert app.themes.video_path(theme) == vid


def test_saved_theme_video_background_plays_via_reference(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """End-to-end for 'saved themes have no background remembered': a saved
    theme with a loose VIDEO background is stored in the USER library and the
    config REFERENCES it (web/{w}{h}/<id>.mp4), so video_path finds it on reload
    and LoadTheme plays it — instead of the static path handing an .mp4 to the
    image renderer (no background)."""
    import json as _json

    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)
    # A loose video the user picked (not already a catalog asset) is copied into
    # the user library on save.
    picked_video = tmp_home / "picked" / "a023.mp4"
    picked_video.parent.mkdir(parents=True, exist_ok=True)
    video_bytes = b"\x00\x00\x00\x20ftypisom" * 50
    picked_video.write_bytes(video_bytes)
    app.settings.set_background_path(_TEST_DEVICE_KEY, str(picked_video))

    assert app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="vid")).ok
    w, h = _TEST_RES
    saved = user_theme_dir / "vid"
    manifest = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
    assert manifest["background"].startswith(f"web/{w}{h}/")
    assert manifest["background"].endswith(".mp4")
    assert not (saved / "Theme.mp4").exists()      # referenced, never bundled
    # The saved theme survives the picked source being removed — proof the video
    # was copied into the library, not linked to a path that would go dark.
    picked_video.unlink()
    # Reload from disk → video_path resolves the referenced library video →
    # PlayVideo fires.
    vp = app.themes.video_path(app.themes.load(saved))
    assert vp is not None
    assert vp.exists()
    assert vp.is_relative_to(app.platform.paths().user_background_dir(w, h))
    assert vp.read_bytes() == video_bytes


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
            orientations=(0, 90, 180, 270),
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

    w, h = _TEST_RES
    # Background is stored in the user library and REFERENCED (no in-dir 00.png);
    # it carries the override content, never the source bg.
    bg = app.themes.background_path(active)
    assert bg is not None
    assert not (saved / "00.png").exists()
    assert bg.is_relative_to(app.platform.paths().user_background_dir(w, h))
    assert bg.read_bytes() != (source / "00.png").read_bytes()

    # The mask override is stored in the user library and REFERENCED (no in-dir
    # 01.png); it carries the override content, never the source's mask.
    mask = app.themes.mask_path(active)
    assert mask is not None
    assert not (saved / "01.png").exists()
    assert mask.is_relative_to(app.platform.paths().user_mask_dir(w, h))
    assert mask.read_bytes() != (source / "01.png").read_bytes()


def test_save_theme_references_screencast_region(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """An active screencast region is stored in the user screencast library and
    referenced by URI in the saved theme's config; the ref resolves back to the
    region (the screencast toggle as a saveable theme asset)."""
    import json as _json

    source = _write_theme_with_real_pngs(tmp_home, "src")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)
    app.settings.set_screencast_region(_TEST_DEVICE_KEY, (100, 50, 640, 480, True))

    assert app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="cast")).ok
    saved = user_theme_dir / "cast"
    manifest = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
    ref = manifest["screencast"]
    assert ref.startswith("screencast/")
    # Config lives in the user screencast library, referenced — NOT in the theme.
    scdir = app.platform.paths().user_screencast_dir() / Path(ref).name
    assert (scdir / "config.json").exists()
    assert not (saved / "config.json").exists()
    # Ref resolves back to the exact region.
    loaded = app.themes.load(saved)
    assert app.themes.screencast_region(loaded) == (100, 50, 640, 480, True)


def test_screencast_region_persists_and_is_mutually_exclusive(
    app: App, tmp_home: Path,
) -> None:
    """set_screencast_region round-trips through config.json, and setting a
    background clears it (the four toggles are mutually exclusive)."""
    del tmp_home
    app.settings.set_screencast_region(_TEST_DEVICE_KEY, (1, 2, 3, 4, False))
    reloaded = Settings(app.platform.paths())
    assert reloaded.for_device(_TEST_DEVICE_KEY).screencast_region == (1, 2, 3, 4, False)
    # A background override clears the screencast (mutually exclusive).
    app.settings.set_background_path(_TEST_DEVICE_KEY, "/some/video.mp4")
    assert app.settings.for_device(_TEST_DEVICE_KEY).screencast_region is None


def test_save_theme_references_media_player_uri_and_url(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """The media-player source (a local URI or a web URL) is stored in the user
    library and referenced by URI in the theme; the URL survives verbatim
    (slashes intact — it is kept a string, never a Path)."""
    import json as _json

    for name, uri in (("local", "/home/me/clip.mp4"),
                      ("web", "https://test-streams.mux.dev/x/y.m3u8")):
        source = _write_theme_with_real_pngs(tmp_home, f"src_{name}")
        app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)
        app.settings.set_media_player_uri(_TEST_DEVICE_KEY, uri)

        assert app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name=name)).ok
        saved = user_theme_dir / name
        manifest = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
        ref = manifest["media_player"]
        assert ref.startswith("media_player/")
        mpdir = app.platform.paths().user_media_player_dir() / Path(ref).name
        assert (mpdir / "config.json").exists()
        # Ref resolves back to the exact URI/URL (no slash mangling).
        assert app.themes.media_player_uri(app.themes.load(saved)) == uri


def test_media_player_uri_persists_and_is_mutually_exclusive(
    app: App, tmp_home: Path,
) -> None:
    """set_media_player_uri round-trips and clears the other display sources."""
    del tmp_home
    app.settings.set_screencast_region(_TEST_DEVICE_KEY, (1, 2, 3, 4, False))
    app.settings.set_media_player_uri(_TEST_DEVICE_KEY, "rtsp://cam/live")
    dev = app.settings.for_device(_TEST_DEVICE_KEY)
    assert dev.media_player_uri == "rtsp://cam/live"
    assert dev.screencast_region is None          # cleared — mutually exclusive
    reloaded = Settings(app.platform.paths())
    assert reloaded.for_device(_TEST_DEVICE_KEY).media_player_uri == "rtsp://cam/live"


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


# ── Masks are REFERENCED on save, not duplicated; user masks are editable ──
#
# Saving a theme that uses a CLOUD mask must reference it (never copy it
# into the user catalog / masks browser).  A theme's own bundled mask is
# copied theme-local.  Only UploadCustomMask adds to the user catalog, and
# its config1.dc is editable as the user changes the mask's metrics.


def test_save_theme_references_cloud_mask_without_polluting_library(
    app: App, tmp_home: Path, user_theme_dir: Path,
) -> None:
    """A CLOUD mask is already a catalog asset → the saved theme REFERENCES it
    by its existing library URI (web/zt{w}{h}/<id>) — NOT copied in-dir AND NOT
    duplicated into the user mask library (no extra entry in the masks browser).
    """
    w, h = _TEST_RES
    cloud_id = "004b"
    cloud_dir = app.platform.paths().cloud_mask_dir(w, h) / cloud_id
    cloud_dir.mkdir(parents=True)
    (cloud_dir / "01.png").write_bytes(_png_bytes(red=0x40))
    (cloud_dir / "config1.dc").write_bytes(b"\xddCLOUDDC")

    source = _write_theme_with_dc(tmp_home, "source")    # bg + clock, no mask
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(source)
    app.settings.set_mask_path(_TEST_DEVICE_KEY, str(cloud_dir / "01.png"))

    result = app.dispatch(SaveTheme(key=_TEST_DEVICE_KEY, name="cloud-themed"))
    assert result.ok is True

    saved = user_theme_dir / "cloud-themed"
    manifest = json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
    assert manifest["mask"] == f"web/zt{w}{h}/{cloud_id}", \
        "saved theme references the cloud mask by its existing library URI"
    assert not (saved / "01.png").exists()          # referenced, never bundled
    # No duplicate created in the user mask library (the masks browser source).
    umd = app.platform.paths().user_mask_dir(w, h)
    assert not umd.exists() or not list(umd.iterdir()), \
        "cloud mask must not be duplicated into the user library"
    # The referenced mask resolves to the cloud mask's content.
    mask = app.themes.mask_path(app.active_themes[_TEST_DEVICE_KEY])
    assert mask is not None
    assert mask == cloud_dir / "01.png"
    assert mask.read_bytes() == (cloud_dir / "01.png").read_bytes()


def test_upload_custom_mask_captures_current_overlay_as_dc(
    app: App, tmp_home: Path,
) -> None:
    """Uploading a mask while an overlay is active writes config1.dc — the
    user mask carries metrics in unison with SaveTheme."""
    from trcc.core.commands import UploadCustomMask
    from trcc.core.models import OverlayElement

    app.settings.add_user_overlay_element(
        _TEST_DEVICE_KEY,
        OverlayElement(
            id="c1", type="clock", x=20, y=20, color="#ffffff",
            size=20, bold=False, italic=False, source="time",
        ),
    )
    src = tmp_home / "mymask.png"
    src.write_bytes(_png_bytes(red=0x33))

    result = app.dispatch(UploadCustomMask(key=_TEST_DEVICE_KEY, source=src))
    assert result.ok is True

    w, h = _TEST_RES
    mask_dir = app.platform.paths().user_mask_dir(w, h) / "custom_mymask"
    assert (mask_dir / "01.png").is_file()
    assert (mask_dir / "config1.dc").is_file(), \
        "uploaded mask must carry config1.dc from the active overlay"


def test_upload_custom_mask_always_writes_dc_even_without_metrics(
    app: App, tmp_home: Path,
) -> None:
    """Every uploaded mask carries a config1.dc from the moment of upload —
    even with NO metrics on screen — so it's an editable {01.png, config1.dc}
    unit from the start."""
    from trcc.core.commands import UploadCustomMask

    src = tmp_home / "blank.png"
    src.write_bytes(_png_bytes(red=0x22))
    # No overlay set on the device.
    assert app.dispatch(UploadCustomMask(key=_TEST_DEVICE_KEY, source=src)).ok

    w, h = _TEST_RES
    mask_dir = app.platform.paths().user_mask_dir(w, h) / "custom_blank"
    assert (mask_dir / "01.png").is_file()
    assert (mask_dir / "config1.dc").is_file(), \
        "an uploaded mask must carry a config1.dc even with no metrics yet"


def test_store_mask_distinct_dc_yields_distinct_dirs(app: App) -> None:
    """Same mask image with DIFFERENT DC metrics → distinct catalog dirs,
    each with its own config1.dc; identical image+DC dedups."""
    paths = app.platform.paths()
    w, h = _TEST_RES
    svc = ThemeService(paths)
    image = b"\x89PNG\r\n\x1a\nSHARED"

    ref1 = svc.store_mask(image, w, h, dc=b"\xddAAA")
    ref2 = svc.store_mask(image, w, h, dc=b"\xddBBB")
    ref_same = svc.store_mask(image, w, h, dc=b"\xddAAA")

    assert ref1 != ref2, "different metrics must not collapse to one dir"
    assert ref1 == ref_same, "identical image+DC must dedup"
    assert len(list(paths.user_mask_dir(w, h).iterdir())) == 2


def test_editing_metrics_persists_user_mask_dc(
    app: App, tmp_home: Path,
) -> None:
    """Editing a metric while a USER mask is active rewrites that mask's
    config1.dc (OverlayChanged → persist_user_mask_dc) — the user mask is an
    editable {01.png, config1.dc} unit."""
    from trcc.core.commands import AddOverlayElement, UploadCustomMask
    from trcc.services import _dc as Dc

    src = tmp_home / "mine.png"
    src.write_bytes(_png_bytes(red=0x55))
    assert app.dispatch(UploadCustomMask(key=_TEST_DEVICE_KEY, source=src)).ok

    w, h = _TEST_RES
    mask_dc = (app.platform.paths().user_mask_dir(w, h)
               / "custom_mine" / "config1.dc")
    before = mask_dc.read_bytes() if mask_dc.is_file() else b""

    result = app.dispatch(AddOverlayElement(
        key=_TEST_DEVICE_KEY, type="metric", x=88, y=44,
        metric="cpu:temp", source="metric",
    ))
    assert result.ok is True

    assert mask_dc.is_file(), "user mask DC must exist after a metric edit"
    assert mask_dc.read_bytes() != before, \
        "editing metrics must rewrite the user mask's DC"
    parsed = Dc.File(mask_dc).read()
    metrics = [e for e in parsed.get("elements", []) if e.get("type") == "metric"]
    assert metrics, "the edited metric must be persisted in the mask's DC"


def test_editing_metrics_does_not_touch_cloud_mask_dc(
    app: App, tmp_home: Path,
) -> None:
    """Editing metrics while a CLOUD mask is active never rewrites the
    read-only cloud mask's config1.dc."""
    from trcc.core.commands import AddOverlayElement, ApplyMask

    w, h = _TEST_RES
    cloud_dir = app.platform.paths().cloud_mask_dir(w, h) / "00cc"
    cloud_dir.mkdir(parents=True)
    (cloud_dir / "01.png").write_bytes(_png_bytes(red=0x60))
    (cloud_dir / "config1.dc").write_bytes(b"\xddORIGINAL")

    assert app.dispatch(
        ApplyMask(key=_TEST_DEVICE_KEY, path=cloud_dir / "01.png")).ok
    app.dispatch(AddOverlayElement(
        key=_TEST_DEVICE_KEY, type="text", x=1, y=1, text="X"))

    assert (cloud_dir / "config1.dc").read_bytes() == b"\xddORIGINAL", \
        "a cloud mask's DC must never be rewritten by a metric edit"


def test_list_themes_user_and_shipped_same_name_coexist(
    app: App, user_theme_dir: Path,
) -> None:
    """A user-saved theme and a shipped theme of the same name COEXIST — the
    user save never hides or overwrites the shipped one.  Both are listed,
    distinguished by ``origin``, user first (so the user's surfaces ahead of the
    shipped placeholder). (#theme-collision)
    """
    from trcc.core.commands import ListThemes

    paths = app.platform.paths()
    shipped = _write_theme_with_real_pngs(paths.theme_dir(*_TEST_RES), "Theme1")
    user = _write_theme_with_real_pngs(user_theme_dir, "Theme1")

    result = app.dispatch(ListThemes(resolution=_TEST_RES))
    assert result.ok
    matches = [e for e in result.themes if e.name == "Theme1"]
    assert len(matches) == 2                         # BOTH coexist (no name dedupe)
    by_origin = {e.origin: e for e in matches}
    assert Path(by_origin["user"].path) == user      # the user's
    assert Path(by_origin["shipped"].path) == shipped  # the shipped, NOT hidden
    assert matches[0].origin == "shipped"            # shipped (cs/program) first, user after
    assert by_origin["user"].preview                 # tile image resolved


def test_list_themes_classifies_origin_by_location(
    app: App, user_theme_dir: Path,
) -> None:
    """Origin is location-derived (under user_data_dir → "user"), not name-based —
    a user theme NOT named User*/Custom* is still origin="user"."""
    from trcc.core.commands import ListThemes

    paths = app.platform.paths()
    _write_theme_with_real_pngs(paths.theme_dir(*_TEST_RES), "Aurora")   # shipped
    _write_theme_with_real_pngs(user_theme_dir, "MyMix")                 # user

    by_name = {e.name: e for e in app.dispatch(ListThemes(resolution=_TEST_RES)).themes}
    assert by_name["Aurora"].origin == "shipped"
    assert by_name["MyMix"].origin == "user"


def test_restore_loads_exact_stored_path_no_user_override(
    app: App, user_theme_dir: Path,
) -> None:
    """Restore loads EXACTLY the persisted path.  A same-named USER theme does
    NOT override a stored SHIPPED pointer — a user save never overwrites the
    shipped theme; restore honours whatever the user last selected. (#theme-collision)
    """
    paths = app.platform.paths()
    shipped = _write_theme_with_real_pngs(paths.theme_dir(*_TEST_RES), "Theme1")
    _write_theme_with_real_pngs(user_theme_dir, "Theme1")   # same-name user theme also exists
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(shipped)
    app.settings.set_current_theme(_TEST_DEVICE_KEY, str(shipped.resolve()))

    assert app.dispatch(RestoreLastTheme(key=_TEST_DEVICE_KEY)).ok
    restored = Path(app.settings.for_device(_TEST_DEVICE_KEY).current_theme)
    assert restored.resolve() == shipped.resolve()    # shipped stays, NOT overridden by the user theme


def test_restore_keeps_shipped_when_no_user_shadow(app: App) -> None:
    """No same-named user theme → RestoreLastTheme loads the stored shipped
    theme unchanged (no regression)."""
    paths = app.platform.paths()
    shipped = _write_theme_with_real_pngs(paths.theme_dir(*_TEST_RES), "Aurora")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(shipped)
    app.settings.set_current_theme(_TEST_DEVICE_KEY, str(shipped.resolve()))

    assert app.dispatch(RestoreLastTheme(key=_TEST_DEVICE_KEY)).ok
    restored = Path(app.settings.for_device(_TEST_DEVICE_KEY).current_theme)
    assert restored.resolve() == shipped.resolve()


def test_restore_keeps_user_theme_unchanged(app: App, user_theme_dir: Path) -> None:
    """current_theme already at a user theme → unchanged (prefer-user is a no-op
    for non-shipped paths)."""
    user = _write_theme_with_real_pngs(user_theme_dir, "MyMix")
    app.active_themes[_TEST_DEVICE_KEY] = ThemeService().load(user)
    app.settings.set_current_theme(_TEST_DEVICE_KEY, str(user.resolve()))

    assert app.dispatch(RestoreLastTheme(key=_TEST_DEVICE_KEY)).ok
    restored = Path(app.settings.for_device(_TEST_DEVICE_KEY).current_theme)
    assert restored.resolve() == user.resolve()


# ─────────────────────────────────────────────────────────────────────
# SaveTheme — folder matches the COMPOSED orientation, not the raw angle
# (Phase D of the folder-switch geometry restore).  A connected rotate
# panel exposes its profile, so the save resolution is keyed on the same
# content_is_portrait / plan_orientation decision the renderer uses.
# ─────────────────────────────────────────────────────────────────────

_ROTATE_KEY = "87ad:70db"


def _rotate_device():
    """A connected non-square rotate panel (native 320×240) with a live
    profile — portrait catalogs theme240320 / zt240320."""
    from trcc.core.models import Kind, ProductInfo, Wire
    from trcc.core.protocol import DeviceProfile

    class _RotateDevice:
        info = ProductInfo(
            vid=0x87AD, pid=0x70DB, vendor="Test", product="Rotate",
            wire=Wire.SCSI, kind=Kind.LCD, native_resolution=(320, 240),
            orientations=(0, 90, 180, 270),
        )
        profile = DeviceProfile(320, 240, rotate=True)
        is_connected = True
        key = _ROTATE_KEY

    return _RotateDevice()


def test_save_at_90_portrait_mask_saves_to_portrait_folder(
    app: App, tmp_home: Path,
) -> None:
    """A portrait mask applied at 90° saves into the PORTRAIT folder
    (theme240320) — its composed orientation — so it reloads composed
    portrait, not spun.  And the reloaded theme composes 240×320."""
    device = _rotate_device()
    app.devices[_ROTATE_KEY] = device  # type: ignore[assignment]
    source = _write_theme_with_real_pngs(tmp_home, "land", 320, 240)
    app.active_themes[_ROTATE_KEY] = ThemeService().load(source)

    # A real portrait mask under web/zt240320 (the portrait catalog).
    mask = app.platform.paths().cloud_mask_dir(240, 320) / "000d" / "01.png"
    mask.parent.mkdir(parents=True)
    mask.write_bytes(_png_bytes(red=0x55))
    app.settings.set_orientation(_ROTATE_KEY, 90)
    app.settings.set_mask_path(_ROTATE_KEY, str(mask))
    app.settings.set_mask_visible(_ROTATE_KEY, True)

    assert app.dispatch(SaveTheme(key=_ROTATE_KEY, name="p")).ok

    portrait_dir = app.platform.paths().user_theme_dir(240, 320) / "p"
    landscape_dir = app.platform.paths().user_theme_dir(320, 240) / "p"
    assert portrait_dir.is_dir(), "portrait mask @90 must save into theme240320"
    assert not landscape_dir.exists()

    # Round-trip: the reloaded theme (now under theme240320) composes PORTRAIT.
    reloaded = app.themes.load(portrait_dir)
    canvas = app.display.composed_canvas_size(
        device.info, reloaded, device.profile, 90,
    )
    assert canvas == (240, 320), (
        "a saved portrait theme must reload composed portrait, not spun"
    )


def test_save_at_90_landscape_content_saves_to_landscape_folder(
    app: App, tmp_home: Path,
) -> None:
    """Landscape content (no portrait mask, landscape base theme) saved at
    90° lands in the LANDSCAPE folder (theme320240) — not the angle's
    portrait folder — so save + reload agree.  Before the fix this filed
    landscape coords under theme240320, where reload misread them."""
    device = _rotate_device()
    app.devices[_ROTATE_KEY] = device  # type: ignore[assignment]
    source = _write_theme_with_real_pngs(tmp_home, "land", 320, 240)
    app.active_themes[_ROTATE_KEY] = ThemeService().load(source)
    app.settings.set_orientation(_ROTATE_KEY, 90)

    assert app.dispatch(SaveTheme(key=_ROTATE_KEY, name="l")).ok

    landscape_dir = app.platform.paths().user_theme_dir(320, 240) / "l"
    portrait_dir = app.platform.paths().user_theme_dir(240, 320) / "l"
    assert landscape_dir.is_dir(), (
        "landscape content @90 must save into theme320240, not the portrait folder"
    )
    assert not portrait_dir.exists()


# ─────────────────────────────────────────────────────────────────────
# LoadImage staging (#245)
# ─────────────────────────────────────────────────────────────────────
#
# LoadImage kept the source basename, but the background resolver only
# accepts the convention name ``00.png`` (its sibling LoadVideo already
# stages ``Theme.zt``).  The panel therefore showed a solid black canvas
# while the CLI still reported success — a silent wrong result.


def _png(path: Path, rgb: tuple[int, int, int]) -> Path:
    from PySide6.QtGui import QColor, QImage

    img = QImage(320, 320, QImage.Format.Format_RGB888)
    img.fill(QColor(*rgb))
    img.save(str(path))
    return path


def _staged_dir(app: App, stem: str) -> Path:
    return app.platform.paths().user_content_dir() / "single-image" / stem


def test_load_image_stages_as_00png_whatever_the_source_name(
    app: App, tmp_path: Path,
) -> None:
    """Any source filename must land as 00.png (#245)."""
    from trcc.core.commands import LoadImage

    src = _png(tmp_path / "my-test-card.png", (237, 28, 36))
    assert app.dispatch(LoadImage(key=_TEST_DEVICE_KEY, path=src)).ok

    staged = _staged_dir(app, "my-test-card")
    assert (staged / "00.png").is_file(), (
        f"expected 00.png, staged: {sorted(p.name for p in staged.iterdir())}"
    )


def test_load_image_stages_a_jpeg_source_as_00png(
    app: App, tmp_path: Path,
) -> None:
    """Non-PNG sources stage under the .png convention name too — the
    renderer sniffs content, so the extension is cosmetic."""
    from PySide6.QtGui import QColor, QImage

    from trcc.core.commands import LoadImage

    src = tmp_path / "photo.jpg"
    img = QImage(320, 320, QImage.Format.Format_RGB888)
    img.fill(QColor(10, 200, 90))
    img.save(str(src), "JPEG")

    assert app.dispatch(LoadImage(key=_TEST_DEVICE_KEY, path=src)).ok

    staged_png = _staged_dir(app, "photo") / "00.png"
    assert staged_png.is_file()
    assert not QImage(str(staged_png)).isNull(), "staged image must load"


def test_load_image_background_actually_reaches_the_frame(
    app: App, tmp_path: Path,
) -> None:
    """The regression that byte-count checks miss.

    The wire frame is a fixed size either way, so 'sent N bytes' proves
    nothing.  Two different source images must produce DIFFERENT frames;
    identical frames mean the resolver ignored the image and painted the
    solid black canvas of #245.
    """
    from trcc.core.commands import LoadImage
    from trcc.core.registry import find_product

    # Same registry fallback the other tests in this file rely on — the
    # test device is not attached, only known to the registry.
    info = find_product(0x0402, 0x3922)

    red = _png(tmp_path / "red.png", (237, 28, 36))
    assert app.dispatch(LoadImage(key=_TEST_DEVICE_KEY, path=red)).ok
    red_frame = app.display.build_frame(
        info, app.active_themes[_TEST_DEVICE_KEY], {})

    black = _png(tmp_path / "black.png", (0, 0, 0))
    assert app.dispatch(LoadImage(key=_TEST_DEVICE_KEY, path=black)).ok
    black_frame = app.display.build_frame(
        info, app.active_themes[_TEST_DEVICE_KEY], {})

    assert bytes(red_frame) != bytes(black_frame), (
        "red and black sources produced identical frames — the background "
        "is not reaching the wire (#245)"
    )


def test_save_theme_references_the_devices_own_library(
    app: App, tmp_home: Path,
) -> None:
    """A background from the device's PER-SKU library is referenced, not copied.

    1600x720 ships six artwork libraries, picked by the SUB byte crossed with
    orientation (FormCZTV.cs:1290): SUB 3 browses ``web/1600720l``.  The
    catalog check used to try only ``user_background_dir`` and the GENERIC
    ``cloud_theme_dir``, so an asset the user picked out of their own cooler's
    library matched neither, fell through to the copy branch, and was
    duplicated into the user library — a second copy of a file already sitting
    in a shipped one.

    The ref is also derived from the matched root rather than re-spelled from
    width/height, which is what lets it name ``1600720l`` at all.

    MUTATION CHECK -- drop the ``app.libraries(...)`` root from the tuple in
    ``_store_background``, or put back the ``f"web/{width}{height}/..."``
    literal, and this fails.
    """
    import json as _json
    from types import SimpleNamespace

    key = "0416:5408"
    resolution = (1600, 720)
    # A REAL profile — the save path reads rotate/jpeg off it, and a stub
    # that carries only a resolution hides which fields are load-bearing.
    from trcc.core.protocol import get_profile

    app.devices[key] = SimpleNamespace(          # type: ignore[assignment]
        profile=get_profile(114, 64),            # FBL 114 PM 64 → 1600x720
        handshake=SimpleNamespace(sub_byte=3, pm_byte=64),
        info=SimpleNamespace(key=key, native_resolution=resolution),
        is_connected=True,
    )

    source = _write_theme_with_real_pngs(tmp_home, "srcly")
    app.active_themes[key] = ThemeService().load(source)

    # The per-SKU cloud library must EXIST on disk, or DeviceLibraries falls
    # back to the generic one — that fallback is deliberate and tested by its
    # own case below.
    w, h = resolution
    lib_dir = app.platform.paths().cloud_theme_dir(w, h, "l")
    lib_dir.mkdir(parents=True, exist_ok=True)
    assert lib_dir.name == "1600720l"
    lib_image = lib_dir / "a077.png"
    lib_image.write_bytes(_png_bytes(red=0x44))
    app.settings.set_background_path(key, str(lib_image))

    assert app.dispatch(SaveTheme(key=key, name="skuref")).ok
    saved = app.platform.paths().user_theme_dir(w, h) / "skuref"
    manifest = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))

    assert manifest["background"] == "web/1600720l/a077.png", (
        "the saved theme must point at the library the asset is actually in"
    )
    assert not (saved / "00.png").exists(), "referenced, never bundled"


def test_save_theme_falls_back_when_the_sku_library_is_absent(
    app: App, tmp_home: Path,
) -> None:
    """No variant dir on disk → the generic library, and refs say so.

    The suffixed libraries are a separate download, so every consumer has to
    behave when they have not landed.  Same device as above, no ``1600720l``
    directory created.
    """
    import json as _json
    from types import SimpleNamespace

    key = "0416:5409"
    resolution = (1600, 720)
    # A REAL profile — the save path reads rotate/jpeg off it, and a stub
    # that carries only a resolution hides which fields are load-bearing.
    from trcc.core.protocol import get_profile

    app.devices[key] = SimpleNamespace(          # type: ignore[assignment]
        profile=get_profile(114, 64),            # FBL 114 PM 64 → 1600x720
        handshake=SimpleNamespace(sub_byte=3, pm_byte=64),
        info=SimpleNamespace(key=key, native_resolution=resolution),
        is_connected=True,
    )

    source = _write_theme_with_real_pngs(tmp_home, "srcgen")
    app.active_themes[key] = ThemeService().load(source)

    w, h = resolution
    lib_dir = app.platform.paths().cloud_theme_dir(w, h)
    lib_dir.mkdir(parents=True, exist_ok=True)
    lib_image = lib_dir / "a078.png"
    lib_image.write_bytes(_png_bytes(red=0x45))
    app.settings.set_background_path(key, str(lib_image))

    assert app.dispatch(SaveTheme(key=key, name="genref")).ok
    saved = app.platform.paths().user_theme_dir(w, h) / "genref"
    manifest = _json.loads((saved / "trcc.json").read_text(encoding="utf-8"))
    assert manifest["background"] == "web/1600720/a078.png"

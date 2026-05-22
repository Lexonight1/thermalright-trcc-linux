"""ThemeService — theme discovery and metadata parsing.

A theme in TRCC is a directory containing:
    trcc.json         next/'s native element layout / fonts / colors
    background.png    (or .jpg) the base image
    optional extras   mask images, animation frames, fonts

This service provides:
    load(path)          → Theme (metadata, not pixels; pixels rendered later)
    list(directory)     → list[Theme] of themes found under a directory
    export(src, dst)    → zip/archive a theme for sharing
    import_(src, dst)   → unpack a shared theme archive

Config resolution:
    trcc.json     — next/'s native format.  Named distinctly from
                    legacy's `config.json` so migration never clobbers
                    a theme a legacy install also reads.  Themes
                    written by pre-cutover next/ used `trcc-next.json`;
                    that name is still read as a fallback.
    config1.dc    — binary legacy format (read-only fallback);
                    auto-migrated to trcc.json on first load.

Rendering (turning a Theme into frame bytes) is DisplayService's job.
"""
from __future__ import annotations

import builtins
import json
import logging
import shutil
import zipfile
from pathlib import Path

from ..core.errors import ThemeError
from ..core.models import Theme
from ._dc_reader import load_dc_as_theme_config

log = logging.getLogger(__name__)


# Distinct filename from legacy's `config.json` — next/'s JSON layout
# uses a list of elements, legacy's expects a dict keyed by metric name.
# Separating filenames avoids ever reading the other tool's shape.
_CONFIG_FILE = "trcc.json"
# Pre-cutover name — read as a fallback so themes saved during the
# parallel-tree period still load.  Next save under DC migration writes
# the new name; old files are left alone for rollback.
_PRE_CUTOVER_CONFIG_FILE = "trcc-next.json"
# Legacy per-theme JSON shape (different from next/'s).  Wrapper around
# the legacy overlay_config dict under a ``dc`` key, plus explicit
# ``background`` and ``mask`` path fields.  Read-only — we translate to
# next/'s shape on load but don't write this shape back.
_LEGACY_CONFIG_FILE = "config.json"
_DC_CONFIG_FILE = "config1.dc"
_BACKGROUND_CANDIDATES = (
    # next/ native names
    "background.mp4", "background.mov", "background.webm",
    "background.png", "background.jpg", "background.jpeg",
    "background.zt",
    # Legacy theme naming (Windows TRCC) — Theme.zt is the JPEG-sequence
    # archive UCVideoCut writes, opaque to anyone who doesn't speak it.
    "Theme.mp4", "Theme.mov", "Theme.webm",
    "Theme.png", "Theme.jpg", "Theme.jpeg",
    "Theme.zt",
)
_MASK_CANDIDATES = (
    "mask.png", "mask.jpg", "mask.jpeg",
    "Mask.png", "Mask.jpg", "Mask.jpeg",
)


class ThemeService:
    """Theme discovery + parsing.

    Pure file I/O + JSON parsing — no rendering, no device talk.  Builds
    Theme metadata that later services consume.
    """

    def load(self, path: Path) -> Theme:
        """Load a theme directory into a Theme dataclass.

        Raises ThemeError if the directory is missing, unreadable, or
        the config.json is invalid.
        """
        if not path.exists():
            raise ThemeError(f"Theme directory does not exist: {path}")
        if not path.is_dir():
            raise ThemeError(f"Theme path is not a directory: {path}")

        config = self._load_config(path)
        resolution = self._resolution_from_config(config)
        name = config.get("name") or path.name

        return Theme(
            path=path,
            name=name,
            resolution=resolution,
            config=config,
        )

    def list(self, directory: Path) -> builtins.list[Theme]:
        """Return every theme found directly under *directory*.

        A subdirectory is a theme iff it contains config.json OR
        config1.dc.  Invalid themes are skipped with a warning, not
        raised — list() never fails on one bad theme.
        """
        if not directory.exists() or not directory.is_dir():
            return []

        themes: list[Theme] = []
        for entry in sorted(directory.iterdir()):
            if not entry.is_dir():
                continue
            if not _has_theme_marker(entry):
                continue
            try:
                themes.append(self.load(entry))
            except ThemeError as e:
                log.warning("Skipping invalid theme %s: %s", entry, e)
        return themes

    def export_dc(
        self, theme_dir: Path, output_path: Path,
        *,
        user_overlay_elements: list[dict] | None = None,
    ) -> Path:
        """Write *theme_dir*'s config out as legacy ``config1.dc`` to
        *output_path* — for sharing themes with Windows TRCC users.

        Reads next/'s JSON config (or falls back to the existing
        ``config1.dc`` if no JSON), composes with the device's user
        overlay elements (if any), and writes a 0xDD-format DC file.
        Returns the output path.
        """
        from ._dc_reader import write_dc_from_theme_config

        try:
            config = self._load_config(theme_dir)
        except ThemeError:
            raise
        write_dc_from_theme_config(
            output_path, config,
            user_overlay_elements=user_overlay_elements,
        )
        return output_path

    def delete(self, directory: Path, name: str) -> Path:
        """Delete the theme ``directory / name``.

        Confines deletion to *directory* — callers pass the trusted root
        (``user_content_dir``), and we resolve+verify the target stays
        inside it.  Raises ``ThemeError`` on traversal, missing dir, or
        IO failure.
        """
        import shutil

        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise ThemeError(f"Invalid theme name: {name!r}")
        try:
            root = directory.resolve(strict=True)
        except OSError as e:
            raise ThemeError(f"Theme root unreachable: {directory}: {e}") from e
        target = (root / name).resolve()
        try:
            target.relative_to(root)
        except ValueError as e:
            raise ThemeError(
                f"Theme path escapes root: {target} not under {root}",
            ) from e
        if not target.is_dir():
            raise ThemeError(f"Theme {name!r} not found at {target}")
        try:
            shutil.rmtree(target)
        except OSError as e:
            raise ThemeError(f"Failed to delete {target}: {e}") from e
        return target

    def background_path(self, theme: Theme) -> Path | None:
        """Return the theme's background path (video or image), or None."""
        for candidate in _BACKGROUND_CANDIDATES:
            path = theme.path / candidate
            if path.exists():
                return path
        return None

    def mask_path(self, theme: Theme) -> Path | None:
        """Return the theme's mask image path, or None if absent."""
        for candidate in _MASK_CANDIDATES:
            path = theme.path / candidate
            if path.exists():
                return path
        return None

    def export(self, theme_path: Path, archive_path: Path) -> None:
        """Archive a theme directory into a deflate-compressed zip.

        Layout: every file under ``theme_path`` becomes an archive entry
        keyed by its relative path. Empty subdirectories are dropped
        (zip stores files, not directories).
        """
        if not theme_path.exists():
            raise ThemeError(f"Theme directory does not exist: {theme_path}")
        if not theme_path.is_dir():
            raise ThemeError(f"Theme path is not a directory: {theme_path}")

        try:
            with zipfile.ZipFile(archive_path, "w",
                                 compression=zipfile.ZIP_DEFLATED) as zf:
                for file_path in sorted(theme_path.rglob("*")):
                    if file_path.is_file():
                        arcname = file_path.relative_to(theme_path)
                        zf.write(file_path, arcname)
        except OSError as e:
            raise ThemeError(
                f"Failed to write archive {archive_path}: {e}",
            ) from e
        log.info("Exported %s → %s", theme_path, archive_path)

    def import_(self, archive_path: Path, into_dir: Path) -> Theme:
        """Unpack a theme archive into ``into_dir``.

        Rejects zip-slip (absolute paths, parent-traversal) per the
        same sanitizer used by the legacy mask downloader. On any
        extraction failure, the partially-written destination is
        cleaned up so users don't end up with half-extracted themes.
        """
        if not archive_path.exists():
            raise ThemeError(f"Archive does not exist: {archive_path}")
        if not archive_path.is_file():
            raise ThemeError(f"Archive path is not a regular file: {archive_path}")
        if into_dir.exists():
            raise ThemeError(
                f"Target already exists: {into_dir} "
                "(refusing to overwrite — choose a different name)",
            )

        into_dir.mkdir(parents=True)
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                skipped: list[str] = []
                for info in zf.infolist():
                    if not _is_safe_archive_member(info.filename):
                        skipped.append(info.filename)
                        continue
                    zf.extract(info, into_dir)
                if skipped:
                    log.warning(
                        "ThemeService.import_: skipped %d unsafe member(s) in %s: %s",
                        len(skipped), archive_path, skipped,
                    )
        except zipfile.BadZipFile as e:
            shutil.rmtree(into_dir, ignore_errors=True)
            raise ThemeError(
                f"Not a valid zip archive: {archive_path}: {e}",
            ) from e
        except OSError as e:
            shutil.rmtree(into_dir, ignore_errors=True)
            raise ThemeError(f"Failed to extract {archive_path}: {e}") from e

        try:
            return self.load(into_dir)
        except ThemeError:
            # Archive extracted but isn't a valid theme — clean up + re-raise.
            shutil.rmtree(into_dir, ignore_errors=True)
            raise

    # ── internals ─────────────────────────────────────────────────────

    def _load_config(self, path: Path) -> dict:
        """Load theme config, preferring JSON and falling back to DC.

        On first successful DC load, writes a ``trcc.json`` alongside
        so subsequent loads skip the binary path.  Legacy's ``config.json``
        uses a different shape, so we keep filenames separate — the two
        tools can share theme directories without stepping on each other.
        Pre-cutover next/ wrote ``trcc-next.json``; that name is still
        read as a fallback.  Migration failure (read-only dir,
        permission, etc.) is logged but doesn't prevent the theme from
        loading.
        """
        json_path = path / _CONFIG_FILE
        if json_path.exists():
            try:
                return json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                raise ThemeError(f"Invalid theme config {json_path}: {e}") from e

        legacy_next_path = path / _PRE_CUTOVER_CONFIG_FILE
        if legacy_next_path.exists():
            try:
                return json.loads(legacy_next_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                raise ThemeError(
                    f"Invalid theme config {legacy_next_path}: {e}",
                ) from e

        legacy_path = path / _LEGACY_CONFIG_FILE
        if legacy_path.exists():
            try:
                raw = json.loads(legacy_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                raise ThemeError(
                    f"Invalid theme config {legacy_path}: {e}",
                ) from e
            if not _looks_like_legacy_theme_config(raw):
                # Bare config.json without theme markers (``dc`` /
                # ``background`` / ``elements``) belongs to some other
                # tool (e.g. legacy app's global ``~/.trcc/config.json``).
                # Skip — let DC fallback try next.
                pass
            else:
                return _legacy_json_to_next_config(raw, path.name)

        dc_path = path / _DC_CONFIG_FILE
        if dc_path.exists():
            config = load_dc_as_theme_config(dc_path)
            self._try_migrate(json_path, config)
            return config

        raise ThemeError(
            f"No {_CONFIG_FILE} or {_DC_CONFIG_FILE} in {path}"
        )

    @staticmethod
    def _try_migrate(json_path: Path, config: dict) -> None:
        """Write the JSON form alongside the DC file; skip quietly on error."""
        try:
            json_path.write_text(
                json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            log.info("Migrated %s → %s", _DC_CONFIG_FILE, json_path)
        except OSError as e:
            log.warning("Could not migrate DC→JSON at %s: %s", json_path, e)

    def _resolution_from_config(self, config: dict) -> tuple[int, int]:
        """Extract (width, height) from config; fall back to (0, 0) if absent."""
        width = int(config.get("width", 0))
        height = int(config.get("height", 0))
        return (width, height)


def _has_theme_marker(entry: Path) -> bool:
    """Cheap-then-deep check: is *entry* a theme directory?

    First pass: existence of a known marker file (`trcc.json`,
    `trcc-next.json`, `config1.dc`).  Legacy `config.json` is a
    deeper check because the filename collides with unrelated config
    files — we read it and require theme-shape content.
    """
    if (entry / _CONFIG_FILE).exists():
        return True
    if (entry / _PRE_CUTOVER_CONFIG_FILE).exists():
        return True
    if (entry / _DC_CONFIG_FILE).exists():
        return True
    legacy = entry / _LEGACY_CONFIG_FILE
    if not legacy.is_file():
        return False
    try:
        raw = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return _looks_like_legacy_theme_config(raw)


def _looks_like_legacy_theme_config(raw: dict) -> bool:
    """True iff ``raw`` looks like a legacy theme config.json (not just
    any JSON file that happens to be named config.json).

    Legacy theme configs always carry a ``dc`` overlay dict; non-theme
    configs (e.g. legacy app global ``config.json``) don't.  This guard
    keeps unrelated config.json files from being mistaken for themes
    when ``list()`` walks a directory.
    """
    if not isinstance(raw, dict):
        return False
    if isinstance(raw.get("dc"), dict):
        return True
    return isinstance(raw.get("elements"), list)


def _legacy_json_to_next_config(raw: dict, theme_name: str) -> dict:
    """Translate a legacy ``config.json`` into next/'s theme config shape.

    Legacy shape (per the Windows TRCC + the legacy Linux tree):
        {
          "background": "<path>",         # auto-discovered, also kept for ref
          "mask": "<path-to-mask-subdir>",  # PRESERVED — applied on LoadTheme
          "dc": {                         # legacy overlay_config
              "time": {x, y, color, font:{size, name, style}, ...},
              "date": {...},
              "<sensor>": {...},
              ...
          }
        }

    Output is next/'s theme config (``elements`` list + flag fields)
    plus a ``mask`` passthrough so ``LoadTheme`` can dispatch
    ``ApplyMask`` for themes that carry an attached mask.  Background
    discovery still walks ``_BACKGROUND_CANDIDATES`` so the explicit
    ``background`` path is informational only.
    """
    elements: list[dict] = []
    dc = raw.get("dc")
    if isinstance(dc, dict):
        for _key, entry in dc.items():
            if not isinstance(entry, dict):
                continue
            if not entry.get("enabled", True):
                continue
            translated = _legacy_entry_to_next_element(entry)
            if translated is not None:
                elements.append(translated)
    out: dict = {
        "name": theme_name,
        "overlay_enabled": True,
        "elements": elements,
    }
    mask = raw.get("mask")
    if isinstance(mask, str) and mask:
        out["mask"] = mask
    return out


_LEGACY_FONT_DEFAULTS = {"name": "Microsoft YaHei", "size": 24, "style": "regular"}


def _legacy_entry_to_next_element(entry: dict) -> dict | None:
    """One legacy overlay-config entry → one next/-shape element dict."""
    font_in = entry.get("font")
    font = (
        font_in if isinstance(font_in, dict) else _LEGACY_FONT_DEFAULTS
    )
    base = {
        "x": int(entry.get("x", 0)),
        "y": int(entry.get("y", 0)),
        "color": entry.get("color", "#ffffff"),
        "name": str(font.get("name", _LEGACY_FONT_DEFAULTS["name"])),
        "size": int(font.get("size", _LEGACY_FONT_DEFAULTS["size"])),
        "bold": font.get("style") == "bold",
        "italic": font.get("style") == "italic",
    }
    metric = entry.get("metric", "")
    if metric in ("time", "date", "weekday"):
        return {**base, "type": "clock", "source": metric}
    if "text" in entry:
        return {**base, "type": "text", "text": str(entry["text"])}
    if metric:
        return {**base, "type": "metric", "metric": metric}
    return None


def _is_safe_archive_member(name: str) -> bool:
    """Reject absolute paths + parent-traversal in zip member names."""
    if not name:
        return False
    return not (Path(name).is_absolute() or ".." in name.split("/"))

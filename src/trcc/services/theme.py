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
import hashlib
import json
import logging
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..core._safe import is_safe_zip_member
from ..core.errors import ThemeError
from ..core.models import Theme, ThemeDir
from . import _dc as Dc

if TYPE_CHECKING:
    from ..core.ports import Paths


@dataclass(frozen=True, slots=True)
class DiscoveredMask:
    """One mask found by ``ThemeService.discover_masks``.

    Matches legacy ``MaskInfo`` — pure value object, GUI maps it to
    its own MaskItem for display.
    """
    name: str
    path: Path
    preview_path: Path
    is_custom: bool

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
# Video-background filenames TRCC actually ships.  ``td.bg`` (00.png)
# is the static fallback rendered when no video is present; videos
# live alongside it as ``Theme.{mp4,mov,webm}`` or ``Theme.zt`` (the
# JPEG-sequence archive UCVideoCut writes).  No ``background.*`` —
# that name never existed in legacy or Windows TRCC.
_VIDEO_CANDIDATES = (
    "Theme.mp4", "Theme.mov", "Theme.webm", "Theme.zt",
)
# Video container extensions we ship (derived from _VIDEO_CANDIDATES so the
# two never drift); the background allowlist is those plus the static PNG.
_VIDEO_EXTS = frozenset(Path(c).suffix.lower() for c in _VIDEO_CANDIDATES)
_BG_EXTS = _VIDEO_EXTS | {".png"}


class ThemeService:
    """Theme discovery + parsing.

    Pure file I/O + JSON parsing — no rendering, no device talk.  Builds
    Theme metadata that later services consume.
    """

    def __init__(self, paths: Paths | None = None) -> None:
        """*paths* enables manifest asset-reference resolution.

        A reference theme names its background/mask by a path relative
        to a data root (``web/{w}{h}/<id>``) instead of bundling the
        bytes.  With *paths* injected, ``background_path`` / ``mask_path``
        resolve those refs against the user library first, then the
        default/cloud library.  When *paths* is None (unit tests that
        only exercise self-contained themes), resolution falls back to
        the in-directory convention.
        """
        self._paths = paths

    def _resolve_asset_ref(self, ref: str) -> Path | None:
        """Resolve a manifest asset reference to an absolute path.

        A relative ref (``web/{w}{h}/a042.mp4``, ``web/zt{w}{h}/m007``)
        is tried under the user data root first, then the default data
        root — "only the parent differs".  Path traversal is rejected:
        the resolved target must stay under the root it matched.  An
        absolute ref is honoured as-is (legacy themes stored absolute
        mask paths) but only when it exists.
        """
        if not ref:
            return None
        rel = Path(ref)
        if rel.is_absolute():
            return rel if rel.exists() else None
        if self._paths is None:
            return None
        for root in (self._paths.user_data_dir(), self._paths.data_dir()):
            candidate = root / rel
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root.resolve())
            except (OSError, ValueError):
                log.warning("_resolve_asset_ref: %r escapes %s — rejected",
                            ref, root)
                continue
            if resolved.exists():
                log.debug("_resolve_asset_ref: %r → %s", ref, resolved)
                return resolved
        log.debug("_resolve_asset_ref: %r unresolved under user/default roots",
                  ref)
        return None

    # ── Library writers — the write-side mirror of _resolve_asset_ref ──

    @staticmethod
    def _content_id(data: bytes) -> str:
        """Content-hash id for a library asset — sha256, 16 hex chars.

        64 bits won't collide across a personal library, and the short
        id keeps on-disk paths readable.  Identical bytes always hash to
        the same id — that is what gives the writers their auto-dedup.
        """
        return hashlib.sha256(data).hexdigest()[:16]

    def store_background(
        self, data: bytes, ext: str, width: int, height: int,
    ) -> str:
        """Store a background in the user library; return its manifest ref.

        Writes *data* to ``user_background_dir(w,h)/<id><ext>`` (``<id>``
        = content hash) and returns the relative ref
        ``web/{w}{h}/<id><ext>`` — the exact shape
        :meth:`_resolve_asset_ref` consumes, so a stored asset round-trips
        back through :meth:`background_path`.  Identical bytes dedup to
        one file (write skipped when the target exists).  *ext* must name
        a shippable background container (``.png`` or a video ext);
        anything else raises :class:`ThemeError`.
        """
        if self._paths is None:
            raise RuntimeError("store_background requires paths injection")
        ext = ext.lower()
        if not ext.startswith("."):
            ext = f".{ext}"
        if ext not in _BG_EXTS:
            log.warning("store_background: rejected ext %r (allowed: %s)",
                        ext, sorted(_BG_EXTS))
            raise ThemeError(f"unsupported background extension: {ext!r}")
        filename = f"{self._content_id(data)}{ext}"
        dest = self._paths.user_background_dir(width, height) / filename
        ref = f"web/{width}{height}/{filename}"
        if dest.exists():
            log.info("store_background: dedup hit %s → %s", filename, dest)
            return ref
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        log.info("store_background: wrote %d byte(s) → %s (ref=%s)",
                 len(data), dest, ref)
        return ref

    def store_mask(
        self, image: bytes, width: int, height: int,
        *, dc: bytes | None = None,
    ) -> str:
        """Store a mask (+ its DC) in the user library; return its ref.

        Writes *image* to ``user_mask_dir(w,h)/<id>/01.png`` and, when
        *dc* is given, its layout to ``.../<id>/config1.dc`` — the
        ``{01.png, config1.dc}`` unit a mask carries.  ``<id>`` is the
        content hash of the mask *image*: its visual identity is the
        dedup key, while the DC is the mask's intrinsic catalog layout.
        Returns the directory ref ``web/zt{w}{h}/<id>`` that
        :meth:`mask_path` resolves.  Identical images dedup to one dir
        (write skipped when its ``01.png`` exists).
        """
        if self._paths is None:
            raise RuntimeError("store_mask requires paths injection")
        asset_id = self._content_id(image)
        dest_dir = self._paths.user_mask_dir(width, height) / asset_id
        td = ThemeDir(dest_dir)
        ref = f"web/zt{width}{height}/{asset_id}"
        if td.mask.exists():
            log.info("store_mask: dedup hit %s → %s", asset_id, dest_dir)
            return ref
        dest_dir.mkdir(parents=True, exist_ok=True)
        td.mask.write_bytes(image)
        if dc is not None:
            td.dc.write_bytes(dc)
        log.info("store_mask: wrote mask=%d byte(s) dc=%s → %s (ref=%s)",
                 len(image), "yes" if dc is not None else "no", dest_dir, ref)
        return ref

    def load(self, path: Path) -> Theme:
        """Load a theme directory into a Theme dataclass.

        Raises ThemeError if the directory is missing, unreadable, or
        the config.json is invalid.
        """
        log.info("load: %s", path)
        if not path.exists():
            raise ThemeError(f"Theme directory does not exist: {path}")
        if not path.is_dir():
            raise ThemeError(f"Theme path is not a directory: {path}")

        config = self._load_config(path)
        resolution = self._resolution_from_config(config)
        name = config.get("name") or path.name
        n_elements = len(config.get("elements") or [])
        log.info(
            "load: %s → name=%r resolution=%s elements=%d "
            "overlay_enabled=%s mask_visible=%s mask_position=%s",
            path.name, name, resolution, n_elements,
            config.get("overlay_enabled"), config.get("mask_visible"),
            config.get("mask_position"),
        )

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
            log.debug("list: directory missing or not a dir → %s", directory)
            return []

        themes: list[Theme] = []
        skipped = 0
        for entry in sorted(directory.iterdir()):
            if not entry.is_dir():
                continue
            if not _has_theme_marker(entry):
                log.debug("list: %s has no theme marker — skipping", entry.name)
                continue
            try:
                themes.append(self.load(entry))
            except ThemeError as e:
                log.warning("list: skipping invalid theme %s: %s", entry, e)
                skipped += 1
        log.info("list: %s → %d theme(s) (skipped=%d)",
                 directory, len(themes), skipped)
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
        log.info("export_dc: theme_dir=%s output_path=%s user_elements=%s",
                 theme_dir, output_path,
                 None if user_overlay_elements is None
                 else len(user_overlay_elements))
        try:
            config = self._load_config(theme_dir)
        except ThemeError:
            raise
        Dc.File(output_path).write(
            config, user_overlay_elements=user_overlay_elements,
        )
        return output_path

    def delete(self, directory: Path, name: str) -> Path:
        """Delete the theme ``directory / name``.

        Confines deletion to *directory* — callers pass the trusted root
        (``user_content_dir``), and we resolve+verify the target stays
        inside it.  Raises ``ThemeError`` on traversal, missing dir, or
        IO failure.
        """
        log.info("delete: directory=%s name=%s", directory, name)
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
        """Return the theme's background — a referenced library asset or
        the in-dir ``00.png`` / video file.

        Reference themes carry a ``background`` key naming a library
        asset (resolved user-root → default-root); self-contained themes
        keep the strict legacy convention — static background ``00.png``,
        videos alongside as ``Theme.{mp4,mov,webm,zt}``.  ``Theme.png`` is
        the panel thumbnail and MUST NOT be returned here (renderer would
        ship the thumbnail to the device).
        """
        ref = theme.config.get("background")
        if isinstance(ref, str) and ref:
            resolved = self._resolve_asset_ref(ref)
            if resolved is not None:
                log.info("background_path: %s → referenced asset %s",
                         theme.name, resolved)
                return resolved
            log.warning("background_path: %s references %r but it did not "
                        "resolve — falling back to in-dir convention",
                        theme.name, ref)
        video = self.video_path(theme)
        if video is not None:
            return video
        td = ThemeDir(theme.path)
        if td.bg.exists():
            return td.bg
        return None

    def video_path(self, theme: Theme) -> Path | None:
        """Return the theme's bundled video file, or None.

        Used by ``LoadTheme`` to decide whether to dispatch ``PlayVideo``
        (animated theme) or render a single static frame (image-only
        theme).  SRP — caller doesn't have to inspect the suffix of
        whatever ``background_path`` returned.
        """
        log.debug("video_path: theme=%s", theme.name)
        for candidate in _VIDEO_CANDIDATES:
            video = theme.path / candidate
            if video.exists():
                return video
        return None

    def mask_path(self, theme: Theme) -> Path | None:
        """Return the theme's mask overlay (``01.png``) or None.

        Reference themes carry a ``mask`` key naming a library mask
        directory (resolved user-root → default-root); its ``01.png`` is
        returned.  Self-contained themes use the in-dir ``01.png``.
        """
        ref = theme.config.get("mask")
        if isinstance(ref, str) and ref:
            resolved = self._resolve_asset_ref(ref)
            if resolved is not None:
                td = ThemeDir(resolved if resolved.is_dir() else resolved.parent)
                if td.mask.exists():
                    log.info("mask_path: %s → referenced mask %s",
                             theme.name, td.mask)
                    return td.mask
            log.warning("mask_path: %s references %r but it did not "
                        "resolve — falling back to in-dir convention",
                        theme.name, ref)
        td = ThemeDir(theme.path)
        return td.mask if td.mask.exists() else None

    def preview_path(self, theme: Theme) -> Path | None:
        """Return the theme's panel thumbnail (``Theme.png``) or None.

        The GUI's theme browser uses this for grid tiles — distinct from
        ``background_path`` which is what the renderer ships to the LCD.
        """
        log.debug("preview_path: theme=%s", theme.name)
        td = ThemeDir(theme.path)
        return td.preview if td.preview.exists() else None

    @staticmethod
    def discover_masks(
        cloud_masks_dir: Path | None = None,
        user_masks_dir: Path | None = None,
    ) -> builtins.list[DiscoveredMask]:
        """Walk user + cloud mask dirs and return their mask metadata.

        Order: user masks first (custom content), then cloud-cached
        masks.  Dedupe by name (first seen wins).  Each mask must have
        ``Theme.png`` (preview thumbnail) OR ``01.png`` (canonical mask
        overlay) — matches legacy's acceptance.  Port of legacy
        ``ThemeService.discover_masks`` so the GUI inlining at
        ``uc_theme_mask.refresh_masks`` can be replaced by a one-liner.
        """
        log.info("discover_masks: cloud=%s user=%s",
                 cloud_masks_dir, user_masks_dir)
        masks: builtins.list[DiscoveredMask] = []
        seen: set[str] = set()

        def _scan(directory: Path | None, is_custom: bool) -> None:
            if directory is None or not directory.exists():
                return
            for item in sorted(directory.iterdir()):
                if not item.is_dir() or item.name in seen:
                    continue
                td = ThemeDir(item)
                if td.preview.exists() or td.mask.exists():
                    seen.add(item.name)
                    masks.append(DiscoveredMask(
                        name=item.name,
                        path=item,
                        preview_path=(
                            td.preview if td.preview.exists() else td.mask
                        ),
                        is_custom=is_custom,
                    ))

        _scan(user_masks_dir, is_custom=True)
        _scan(cloud_masks_dir, is_custom=False)
        return masks

    def export(self, theme_path: Path, archive_path: Path) -> None:
        """Archive a theme as a self-contained, shareable zip.

        A saved theme references its background/mask in the user library
        (Phase D), so a raw dir-zip would omit them.  Export DEREFERENCES:
        the resolved background lands as ``00.png`` (or a bundled video as
        ``Theme.<ext>``), the mask as ``01.png``, the thumbnail as
        ``Theme.png``, and a ``trcc.json`` with the library ``background``/
        ``mask`` ref keys STRIPPED — so the recipient loads via the in-dir
        convention without needing the sender's library.
        """
        if not theme_path.exists():
            raise ThemeError(f"Theme directory does not exist: {theme_path}")
        if not theme_path.is_dir():
            raise ThemeError(f"Theme path is not a directory: {theme_path}")

        members = self._export_members(self.load(theme_path), theme_path)
        try:
            with zipfile.ZipFile(archive_path, "w",
                                 compression=zipfile.ZIP_DEFLATED) as zf:
                for arcname, source in sorted(members.items()):
                    if isinstance(source, Path):
                        zf.write(source, arcname)
                    else:
                        zf.writestr(arcname, source)
        except OSError as e:
            raise ThemeError(
                f"Failed to write archive {archive_path}: {e}",
            ) from e
        log.info("Exported %s → %s (%d member(s): %s)",
                 theme_path, archive_path, len(members), sorted(members))

    def _export_members(
        self, theme: Theme, theme_path: Path,
    ) -> dict[str, Path | bytes]:
        """Resolve a theme into its self-contained archive members.

        Maps each archive entry name to a source ``Path`` (copied
        verbatim) or ``bytes`` (the rebuilt manifest).  Dereferences the
        background/mask refs to library files via Phase-B resolution, so
        the archive carries the bytes, not the refs.
        """
        members: dict[str, Path | bytes] = {}

        bg = self.background_path(theme)
        if bg is not None and bg.suffix.lower() in _VIDEO_EXTS:
            members[f"Theme{bg.suffix.lower()}"] = bg
            log.info("export: bundling video bg %s", bg.name)
        elif bg is not None:
            members["00.png"] = bg
            log.info("export: dereferenced bg → 00.png (%s)", bg)
        else:
            log.info("export: theme %r has no background", theme.name)

        mask = self.mask_path(theme)
        if mask is not None:
            members["01.png"] = mask
            log.info("export: dereferenced mask → 01.png (%s)", mask)

        td = ThemeDir(theme_path)
        if td.preview.exists():
            members["Theme.png"] = td.preview

        manifest = {
            k: v for k, v in theme.config.items()
            if k not in ("background", "mask")
        }
        members["trcc.json"] = (
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        log.info("export: manifest stripped of bg/mask refs (%d key(s))",
                 len(manifest))
        return members

    def import_(self, archive_path: Path, into_dir: Path) -> Theme:
        """Unpack a theme archive into ``into_dir``.

        Rejects zip-slip (absolute paths, parent-traversal) per the
        same sanitizer used by the legacy mask downloader. On any
        extraction failure, the partially-written destination is
        cleaned up so users don't end up with half-extracted themes.
        """
        log.info("import_: archive=%s into=%s", archive_path, into_dir)
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
                    if not is_safe_zip_member(info.filename):
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
            log.info("_load_config: %s → reading %s", path.name, _CONFIG_FILE)
            try:
                return json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                raise ThemeError(f"Invalid theme config {json_path}: {e}") from e

        legacy_next_path = path / _PRE_CUTOVER_CONFIG_FILE
        if legacy_next_path.exists():
            log.info("_load_config: %s → reading pre-cutover %s",
                     path.name, _PRE_CUTOVER_CONFIG_FILE)
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
                log.debug(
                    "_load_config: %s → %s lacks theme markers, falling "
                    "through to DC", path.name, _LEGACY_CONFIG_FILE,
                )
            else:
                log.info(
                    "_load_config: %s → translating legacy %s",
                    path.name, _LEGACY_CONFIG_FILE,
                )
                return _legacy_json_to_next_config(raw, path.name)

        dc_path = path / _DC_CONFIG_FILE
        if dc_path.exists():
            log.info("_load_config: %s → reading %s (binary DC)",
                     path.name, _DC_CONFIG_FILE)
            config = Dc.File(dc_path).read()
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
    `trcc.json`, `config1.dc`).  Legacy `config.json` is a
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
    discovery uses ``ThemeDir`` (``00.png`` only) so the explicit
    ``background`` path here is informational only.
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
    pos = raw.get("mask_position")
    if isinstance(pos, (list, tuple)) and len(pos) == 2:
        try:
            out["mask_position"] = [int(pos[0]), int(pos[1])]
        except (TypeError, ValueError):
            pass
    if "mask_visible" in raw:
        out["mask_visible"] = bool(raw["mask_visible"])
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



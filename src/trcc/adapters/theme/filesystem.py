"""``FileContentStore`` — the filesystem :class:`~trcc.core.ports.ContentStore`.

Where themes, masks, backgrounds and capture configs are kept, under
``data_dir()`` (program + cloud content) and ``user_data_dir()`` (what the
user authored).  Sibling of ``cloud.py``, which is the remote half of the
same job.

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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ...core._safe import is_safe_zip_member
from ...core.errors import ThemeError
from ...core.logs import per_frame
from ...core.models import DiscoveredMask, Theme, ThemeDir, WebPreviewInfo
from ...core.ports import ContentStore, SingleFileTheme
from ...services import _dc as Dc

if TYPE_CHECKING:
    from ...core.ports import Paths


log = logging.getLogger(__name__)
#: ``video_path`` is asked once per frame by the background resolver.
frame_log = per_frame(__name__)


# Distinct filename from legacy's `config.json` — next/'s JSON layout
# uses a list of elements, legacy's expects a dict keyed by metric name.
# Separating filenames avoids ever reading the other tool's shape.
# Pre-cutover name — read as a fallback so themes saved during the
# parallel-tree period still load.  Next save under DC migration writes
# the new name; old files are left alone for rollback.
_PRE_CUTOVER_CONFIG_FILE = "trcc-next.json"
# Legacy per-theme JSON shape (different from next/'s).  Wrapper around
# the legacy overlay_config dict under a ``dc`` key, plus explicit
# ``background`` and ``mask`` path fields.  Read-only — we translate to
# next/'s shape on load but don't write this shape back.
# Video-background filenames TRCC actually ships.  ``td.bg`` (00.png)
# is the static fallback rendered when no video is present; videos
# live alongside it as ``Theme.{mp4,mov,webm}`` or ``Theme.zt`` (the
# JPEG-sequence archive UCVideoCut writes).  No ``background.*`` —
# that name never existed in legacy or Windows TRCC.
_VIDEO_CANDIDATES = (
    "Theme.mp4", "Theme.mov", "Theme.webm", ThemeDir.ZT,
)
# Video container extensions we ship (derived from _VIDEO_CANDIDATES so the
# two never drift); the background allowlist is those plus the static PNG.
_VIDEO_EXTS = frozenset(Path(c).suffix.lower() for c in _VIDEO_CANDIDATES)
_BG_EXTS = _VIDEO_EXTS | {".png"}


@dataclass(frozen=True, slots=True)
class FileSingleFileTheme(SingleFileTheme):
    """The on-disk :class:`SingleFileTheme` — yielded by
    :meth:`FileContentStore.single_file_theme`."""

    path: Path
    name: str

    def install(self, source: Path, filename: str) -> Path:
        """Copy *source* in as *filename*, skipping an unchanged re-run.

        The size guard is what makes re-loading the same file cheap; both
        callers had their own copy of it.
        """
        dest = self.path / filename
        if dest.is_file() and dest.stat().st_size == source.stat().st_size:
            log.info("SingleFileTheme.install: %s unchanged — skipped", dest)
            return dest
        shutil.copy2(source, dest)
        log.info("SingleFileTheme.install: %s → %s", source, dest)
        return dest

    def adopt(self, produced: Path, filename: str) -> Path:
        """Move an already-produced file (a transcoder output) in as
        *filename*, and clear the temp directory it came from."""
        dest = self.path / filename
        shutil.move(str(produced), str(dest))
        shutil.rmtree(produced.parent, ignore_errors=True)
        log.info("SingleFileTheme.adopt: %s → %s (temp dir cleared)",
                 produced, dest)
        return dest


class FileContentStore(ContentStore):
    """The filesystem :class:`ContentStore` — theme discovery + parsing.

    File I/O + JSON parsing, no rendering and no device talk.  Builds the
    Theme metadata later services consume.
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

    @contextmanager
    def stage(self, target: Path) -> Iterator[Path]:
        """Build a content unit in a sibling dir, then swap it over *target*.

        Yields the staging directory.  A clean exit swaps it atomically into
        place; ANY exception discards it and leaves *target* exactly as it
        was.  The caller writes its files and does not think about rollback.

        **Why staging rather than writing into the target.**  The unit being
        saved is usually the target ITSELF — a prior save re-points the active
        theme at the saved dir — so clearing the target first would destroy
        the source's own background and mask before they had been read.  That
        was a real data-loss bug ("source theme has no background").  Staging
        keeps the source intact until every asset is captured, and makes an
        overwrite crash-safe.

        **What it does NOT cover.**  A theme is a config that REFERENCES its
        assets; ``store_background`` / ``store_mask`` put those in the shared
        user library, outside this directory.  A failure here discards the
        unit but leaves an already-ingested library asset behind, orphaned
        until the content hash matches another save.  Wasted bytes, never a
        wrong render — but this is not a transaction over the assets, and it
        should not be described as one.

        The commit window is one ``rmtree`` + one ``replace``, inherited from
        the hand-rolled version this replaces: if the process dies between
        them an overwritten unit is gone.  Closing that needs a move-aside
        restore, which is a behaviour change and belongs in its own commit.
        """
        staging = target.parent / f".{target.name}.saving"
        log.info("stage: %s → staging %s", target, staging)
        target.parent.mkdir(parents=True, exist_ok=True)
        if staging.exists():
            log.warning("stage: clearing abandoned staging dir %s", staging)
            shutil.rmtree(staging)
        staging.mkdir(exist_ok=False)
        try:
            yield staging
            if target.exists():
                log.warning("stage: overwriting existing unit %s", target)
                shutil.rmtree(target)
            staging.replace(target)
        except BaseException:
            log.exception("stage: discarding %s — %s left untouched",
                          staging, target)
            shutil.rmtree(staging, ignore_errors=True)
            raise
        log.info("stage: committed %s", target)

    @contextmanager
    def single_file_theme(
        self, source: Path, kind: str,
    ) -> Iterator[FileSingleFileTheme]:
        """A theme directory wrapping ONE file — an image or a video.

        ``LoadImage`` and ``LoadVideo`` turn an arbitrary file the user picked
        into a minimal theme so it appears in ``theme list`` like any other and
        the ordinary ``LoadTheme`` path can render it.  ``LoadVideo``'s own
        docstring called itself "conceptually parallel to LoadImage"; they had
        the same twenty lines each, and this is those lines.

        Yields the unit; the caller installs its one payload file.  The
        ``trcc.json`` marker is written LAST, on a clean exit — deliberately,
        because a payload that fails half-way then leaves a directory with no
        marker, which ``_has_theme_marker`` skips, so a broken load never
        shows up in the theme list.  An exception leaves that markerless
        directory behind exactly as the hand-written versions did.
        """
        if self._paths is None:
            raise RuntimeError("single_file_theme requires paths injection")
        root = self._paths.user_content_dir() / f"single-{kind}"
        root.mkdir(parents=True, exist_ok=True)
        name = source.stem
        path = root / name
        if path.exists() and not path.is_dir():
            # A file already owns that name — disambiguate by the source's
            # parent rather than clobbering it.
            path = root / f"{name}-from-{source.parent.name}"
        path.mkdir(parents=True, exist_ok=True)
        log.info("single_file_theme: kind=%s source=%s → %s",
                 kind, source, path)
        unit = FileSingleFileTheme(path, name)
        yield unit
        marker = ThemeDir(path).json
        if not marker.is_file():
            marker.write_text(
                json.dumps({"name": f"{kind}:{name}", "elements": []},
                           indent=2) + "\n",
                encoding="utf-8",
            )
            log.info("single_file_theme: wrote marker %s", marker)

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
        *, dc: bytes | None = None, name: str | None = None,
    ) -> str:
        """Store a mask unit in the user library; return its ref.

        Writes the whole unit a mask is: ``01.png`` (the overlay), ``Theme.png``
        (its browser tile — the same image, because a user upload only HAS the
        one) and, when *dc* is given, ``config1.dc``.  Writing the preview here
        is why this can be the single mask writer: ``UploadCustomMask`` used to
        hand-roll the unit precisely because it needed a tile and this did not
        produce one, and legacy-port browsers gate visibility on it.

        Keying follows *name*, and the difference is not cosmetic:

        * *name* — the user's own label for a mask they uploaded.  A name says
          nothing about the bytes, so the unit is REPLACED; skipping the write
          would leave the old image under the new upload's name.
        * no *name* — content-addressed (image + DC hashed together, so two
          themes sharing an image but not its metrics stay distinct).  Identical
          bytes dedup, because here the id IS the content.

        Returns ``web/zt{w}{h}/<id>``, which :meth:`resolve_ref` and
        :meth:`mask_path` both resolve.
        """
        if self._paths is None:
            raise RuntimeError("store_mask requires paths injection")
        asset_id = _safe_asset_name(name) if name is not None else (
            self._content_id(image if dc is None else image + dc)
        )
        dest_dir = self._paths.user_mask_dir(width, height) / asset_id
        td = ThemeDir(dest_dir)
        ref = f"web/zt{width}{height}/{asset_id}"
        if name is None and td.mask.exists():
            log.info("store_mask: dedup hit %s → %s", asset_id, dest_dir)
            return ref
        dest_dir.mkdir(parents=True, exist_ok=True)
        replacing = td.mask.exists()
        td.mask.write_bytes(image)
        td.preview.write_bytes(image)
        if dc is not None:
            td.dc.write_bytes(dc)
        log.info("store_mask: %s mask=%d byte(s) dc=%s key=%s → %s (ref=%s)",
                 "replaced" if replacing else "wrote", len(image),
                 "yes" if dc is not None else "no",
                 "name" if name is not None else "content", dest_dir, ref)
        return ref

    def resolve_ref(self, ref: str) -> Path | None:
        """Resolve a ref minted by a ``store_*`` method back to a path."""
        log.debug("resolve_ref: %r", ref)
        return self._resolve_asset_ref(ref)

    def store_screencast(
        self, region: tuple[int, int, int, int, bool],
    ) -> str:
        """Store a screencast region config in the user library; return its ref.

        Writes ``{x, y, w, h, audio}`` as JSON to
        ``user_screencast_dir()/<id>/config.json`` (``<id>`` = content hash, so
        identical regions dedup) and returns the ref ``screencast/<id>`` that
        :meth:`screencast_region` resolves back through ``_resolve_asset_ref``.
        A screencast is a live capture, so it is not resolution-keyed.
        """
        if self._paths is None:
            raise RuntimeError("store_screencast requires paths injection")
        x, y, w, h, audio = region
        blob = json.dumps(
            {"x": x, "y": y, "w": w, "h": h, "audio": bool(audio)},
            sort_keys=True,
        ).encode("utf-8")
        asset_id = self._content_id(blob)
        dest_dir = self._paths.user_screencast_dir() / asset_id
        ref = f"screencast/{asset_id}"
        cfg = ThemeDir(dest_dir).legacy_json
        if cfg.exists():
            log.info("store_screencast: dedup hit %s → %s", asset_id, dest_dir)
            return ref
        dest_dir.mkdir(parents=True, exist_ok=True)
        cfg.write_bytes(blob)
        log.info("store_screencast: wrote %s → %s (ref=%s)",
                 region, dest_dir, ref)
        return ref

    def screencast_region(
        self, theme: Theme,
    ) -> tuple[int, int, int, int, bool] | None:
        """Resolve a theme's ``screencast`` ref → (x, y, w, h, audio), or None.

        Reads the ``config.json`` written by :meth:`store_screencast`.  Used by
        LoadTheme to resume a saved screencast.
        """
        ref = theme.config.get("screencast")
        if not (isinstance(ref, str) and ref):
            return None
        resolved = self._resolve_asset_ref(ref)
        if resolved is None:
            log.warning("screencast_region: %s ref %r did not resolve",
                        theme.name, ref)
            return None
        cfg = ThemeDir(resolved).legacy_json if resolved.is_dir() else resolved
        try:
            d = json.loads(cfg.read_text(encoding="utf-8"))
            return (int(d["x"]), int(d["y"]), int(d["w"]),
                    int(d["h"]), bool(d["audio"]))
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("screencast_region: bad config %s (%s)", cfg, e)
            return None

    def store_media_player(self, uri: str) -> str:
        """Store a media-player source URI in the user library; return its ref.

        Writes ``{uri}`` as JSON to ``user_media_player_dir()/<id>/config.json``
        (``<id>`` = content hash, so identical sources dedup) and returns the
        ref ``media_player/<id>`` that :meth:`media_player_uri` resolves back.
        The URI may be a local path or a URL/stream — it is stored verbatim.
        """
        if self._paths is None:
            raise RuntimeError("store_media_player requires paths injection")
        blob = json.dumps({"uri": uri}, sort_keys=True).encode("utf-8")
        asset_id = self._content_id(blob)
        dest_dir = self._paths.user_media_player_dir() / asset_id
        ref = f"media_player/{asset_id}"
        cfg = ThemeDir(dest_dir).legacy_json
        if cfg.exists():
            log.info("store_media_player: dedup hit %s → %s", asset_id, dest_dir)
            return ref
        dest_dir.mkdir(parents=True, exist_ok=True)
        cfg.write_bytes(blob)
        log.info("store_media_player: wrote %r → %s (ref=%s)", uri, dest_dir, ref)
        return ref

    def media_player_uri(self, theme: Theme) -> str | None:
        """Resolve a theme's ``media_player`` ref → its source URI, or None.

        Reads the ``config.json`` written by :meth:`store_media_player`.  Used
        by LoadTheme to resume a saved media-player source.
        """
        ref = theme.config.get("media_player")
        if not (isinstance(ref, str) and ref):
            return None
        resolved = self._resolve_asset_ref(ref)
        if resolved is None:
            log.warning("media_player_uri: %s ref %r did not resolve",
                        theme.name, ref)
            return None
        cfg = ThemeDir(resolved).legacy_json if resolved.is_dir() else resolved
        try:
            uri = json.loads(cfg.read_text(encoding="utf-8"))["uri"]
            return str(uri) if uri else None
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("media_player_uri: bad config %s (%s)", cfg, e)
            return None

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

    def list_web_previews(
        self, web_dir: Path,
    ) -> builtins.list[WebPreviewInfo]:
        """Enumerate the downloaded cloud-theme previews under *web_dir*.

        One entry per ``<id>.png`` (sorted): ``category`` is the id's first
        letter, ``has_video`` whether a sibling ``<id>.mp4`` exists.  Empty
        list when nothing's downloaded yet.  Pure disk enumeration — no
        URLs (that's the caller's concern).
        """
        if not web_dir.is_dir():
            log.debug("list_web_previews: %s missing → []", web_dir)
            return []
        previews = [
            WebPreviewInfo(
                id=png.stem,
                category=png.stem[0] if png.stem else "",
                has_video=(web_dir / f"{png.stem}.mp4").is_file(),
            )
            for png in sorted(web_dir.glob("*.png"))
        ]
        log.info("list_web_previews: %s → %d preview(s)",
                 web_dir, len(previews))
        return previews

    def export_dc(
        self, theme_dir: Path, output_path: Path,
        *, elements: list[dict] | None = None,
    ) -> Path:
        """Write *theme_dir*'s config out as legacy ``config1.dc`` to
        *output_path* — for sharing themes with Windows TRCC users.

        Reads next/'s JSON config (or falls back to the existing
        ``config1.dc`` if no JSON) and writes a 0xDD-format DC file.

        *elements* REPLACES the theme's own layout when given — the caller
        passes what the device is actually showing.  It used to be
        ``user_overlay_elements=``, which the codec APPENDED to the theme's
        elements, so an export could contain each element twice and could
        contain elements the renderer never drew.
        """
        log.info("export_dc: theme_dir=%s output_path=%s elements=%s",
                 theme_dir, output_path,
                 None if elements is None else len(elements))
        config = self._load_config(theme_dir)
        if elements is not None:
            config = {**config, "elements": elements}
        Dc.File(output_path).write(config)
        return output_path

    def write_manifest(self, theme_dir: Path, manifest: dict) -> Path:
        """Persist *manifest* as ``trcc.json`` — see ContentStore.write_manifest."""
        out = ThemeDir(theme_dir).json
        log.info("write_manifest: %s (bg=%s mask=%s elements=%d)",
                 out, manifest.get("background"), manifest.get("mask"),
                 len(manifest.get("elements") or []))
        out.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return out

    def write_preview(self, theme_dir: Path, png: bytes) -> Path:
        """Write the chooser tile — see ContentStore.write_preview."""
        out = ThemeDir(theme_dir).preview
        log.info("write_preview: %d byte(s) → %s", len(png), out)
        out.write_bytes(png)
        return out

    def copy_preview(self, src_theme_dir: Path, dst_theme_dir: Path) -> bool:
        """Copy one theme's tile to another — see ContentStore.copy_preview."""
        src = ThemeDir(src_theme_dir).preview
        if not src.is_file():
            log.info("copy_preview: %s has no %s — nothing to copy",
                     src_theme_dir, ThemeDir.PREVIEW)
            return False
        dst = ThemeDir(dst_theme_dir).preview
        shutil.copy2(src, dst)
        log.info("copy_preview: %s → %s", src, dst)
        return True

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
        frame_log.debug("video_path: theme=%s", theme.name)
        # A theme-local bundled video (Theme.mp4 / .mov / …) — including a
        # symlink SaveTheme drops in for a video background.
        for candidate in _VIDEO_CANDIDATES:
            video = theme.path / candidate
            if video.exists():
                return video
        # A REFERENCED video background: a saved pure-pointer theme records its
        # background as a manifest ref (e.g. ``web/{w}{h}/<id>.mp4``).  Resolve
        # it directly (NOT via ``background_path`` — that calls us back and would
        # recurse) so ``LoadTheme`` plays it through ``PlayVideo`` instead of
        # handing an ``.mp4`` to the static-image path (which renders no
        # background).  This is the symlink-fallback / legacy-ref case.
        ref = theme.config.get("background")
        if (isinstance(ref, str) and ref
                and Path(ref).suffix.lower() in _VIDEO_EXTS):
            resolved = self._resolve_asset_ref(ref)
            if resolved is not None:
                log.info("video_path: %s → referenced video background %s",
                         theme.name, resolved)
                return resolved
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
                    # DEBUG, not INFO: ``_resolve_mask_source`` calls this on
                    # every rendered frame, so an INFO line here floods the
                    # log we ask reporters to send us — one report showed ten
                    # identical lines inside a single second, and the capped
                    # tail is then all frames and no user actions.  The
                    # one-shot callers (LoadTheme, SaveTheme) log their own
                    # INFO entry, so no signal is lost. (#264)
                    log.debug("mask_path: %s → referenced mask %s",
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

    def tile_path(self, theme_dir: Path) -> Path | None:
        """Theme.png → 00.png → any *.png — see ContentStore.tile_path."""
        for name in (ThemeDir.PREVIEW, ThemeDir.BG):
            candidate = theme_dir / name
            if candidate.is_file():
                log.debug("tile_path: %s → %s", theme_dir, candidate)
                return candidate
        any_png = next(theme_dir.glob("*.png"), None)
        log.debug("tile_path: %s → %s (fallback)", theme_dir, any_png)
        return any_png

    def is_theme_dir(self, path: Path) -> bool:
        """True iff *path* is a directory carrying a theme config.

        The store owns which marker files count, so the caller stops
        needing both halves of the question — ``_search_theme_by_name``
        paired its own ``is_dir()`` with a reach into this module's
        private ``_has_theme_marker``.
        """
        verdict = path.is_dir() and _has_theme_marker(path)
        log.debug("is_theme_dir: %s → %s", path, verdict)
        return verdict

    def discover_masks(
        self,
        cloud_masks_dir: Path | None = None,
        user_masks_dir: Path | None = None,
    ) -> builtins.list[DiscoveredMask]:
        """Walk user + cloud mask dirs and return their mask metadata.

        Order: cloud (shipped) masks first, then user masks — consistent with
        themes; user data is never hidden by shipped, nor shipped by user.
        Dedupe by PATH (a dir can't list twice; a same-id user + cloud pair
        both belong).  Each mask must have
        ``Theme.png`` (preview thumbnail) OR ``01.png`` (canonical mask
        overlay) — matches legacy's acceptance.  Port of legacy
        ``FileContentStore.discover_masks`` so the GUI inlining at
        ``uc_theme_mask.refresh_masks`` is a one-liner.
        """
        log.info("discover_masks: cloud=%s user=%s",
                 cloud_masks_dir, user_masks_dir)
        masks: builtins.list[DiscoveredMask] = []
        seen: set[Path] = set()

        def _scan(directory: Path | None, is_custom: bool) -> None:
            if directory is None or not directory.exists():
                return
            for item in sorted(directory.iterdir()):
                if not item.is_dir():
                    continue
                resolved = item.resolve()
                if resolved in seen:
                    continue
                td = ThemeDir(item)
                if td.preview.exists() or td.mask.exists():
                    seen.add(resolved)
                    masks.append(DiscoveredMask(
                        name=item.name,
                        path=item,
                        preview_path=(
                            td.preview if td.preview.exists() else td.mask
                        ),
                        is_custom=is_custom,
                    ))

        # Cloud (shipped) masks first, user masks after — consistent with
        # themes; neither hides the other.
        _scan(cloud_masks_dir, is_custom=False)
        _scan(user_masks_dir, is_custom=True)
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
            members[ThemeDir.BG] = bg
            log.info("export: dereferenced bg → 00.png (%s)", bg)
        else:
            log.info("export: theme %r has no background", theme.name)

        mask = self.mask_path(theme)
        if mask is not None:
            members[ThemeDir.MASK] = mask
            log.info("export: dereferenced mask → 01.png (%s)", mask)

        td = ThemeDir(theme_path)
        if td.preview.exists():
            members[ThemeDir.PREVIEW] = td.preview

        manifest = {
            k: v for k, v in theme.config.items()
            if k not in ("background", "mask")
        }
        members[ThemeDir.JSON] = (
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
                        "FileContentStore.import_: skipped %d unsafe member(s) in %s: %s",
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
        json_path = ThemeDir(path).json
        if json_path.exists():
            log.info("_load_config: %s → reading %s", path.name, ThemeDir.JSON)
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

        legacy_path = ThemeDir(path).legacy_json
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
                    "through to DC", path.name, ThemeDir.LEGACY_JSON,
                )
            else:
                log.info(
                    "_load_config: %s → translating legacy %s",
                    path.name, ThemeDir.LEGACY_JSON,
                )
                return _legacy_json_to_next_config(raw, path.name)

        dc_path = ThemeDir(path).dc
        if dc_path.exists():
            log.info("_load_config: %s → reading %s (binary DC)",
                     path.name, ThemeDir.DC)
            config = Dc.File(dc_path).read()
            self._try_migrate(json_path, config)
            return config

        raise ThemeError(
            f"No {ThemeDir.JSON} or {ThemeDir.DC} in {path}"
        )

    @staticmethod
    def _try_migrate(json_path: Path, config: dict) -> None:
        """Write the JSON form alongside the DC file; skip quietly on error."""
        try:
            json_path.write_text(
                json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            log.info("Migrated %s → %s", ThemeDir.DC, json_path)
        except OSError as e:
            log.warning("Could not migrate DC→JSON at %s: %s", json_path, e)

    def _resolution_from_config(self, config: dict) -> tuple[int, int]:
        """Extract (width, height) from config; fall back to (0, 0) if absent."""
        width = int(config.get("width", 0))
        height = int(config.get("height", 0))
        return (width, height)


def _safe_asset_name(name: str) -> str:
    """Validate a user-supplied library id — it becomes a path segment.

    Same guard ``delete`` applies to a theme name, for the same reason: the
    moment a name reaches the filesystem it is an injection surface.
    """
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        log.warning("_safe_asset_name: rejected %r", name)
        raise ThemeError(f"Invalid asset name: {name!r}")
    return name


def _has_theme_marker(entry: Path) -> bool:
    """Cheap-then-deep check: is *entry* a theme directory?

    First pass: existence of a known marker file (`trcc.json`,
    `trcc.json`, `config1.dc`).  Legacy `config.json` is a
    deeper check because the filename collides with unrelated config
    files — we read it and require theme-shape content.
    """
    if ThemeDir(entry).json.exists():
        return True
    if (entry / _PRE_CUTOVER_CONFIG_FILE).exists():
        return True
    if ThemeDir(entry).dc.exists():
        return True
    legacy = ThemeDir(entry).legacy_json
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



"""Developer variant panel for the mock GUI — every device variant as an asset
button, in a scrollable sub-panel slotted into the left sidebar.

The 119 catalog variants share 3 USB vid:pids, so the real device sidebar (keyed
on vid:pid) shows 3.  This dev-only sub-panel sits in the sidebar gap — below the
System-sensors button, above the control-centre button, with its own inner
scrollbar — and lists EVERY variant as its real ``button_image`` asset.  Clicking
one injects its handshake reply (``set_active_reply`` → PM / sub_byte / FBL) and
re-runs ``ConnectDevice`` so the FULL GUI re-handshakes and presents that device:
you see exactly what that device's user sees, in the real chrome.

Dev-only.  Nothing in ``src/trcc/`` changes — this is a child widget the mock GUI
mounts at runtime, guarded to a mock fleet (``set_active_reply`` present).  The
variant list comes from the model registry (``device_catalog()``), so new devices
appear as soon as they land in ``core/variants.py``.
"""
from __future__ import annotations

import functools
import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)

_BTN_W, _BTN_H = 140, 46          # matches the device buttons above (140 wide)


def _variant_dicts() -> list[dict]:
    """Every variant from the models, in the sidebar dict shape + the
    ``(pm, sub, fbl)`` reply to inject.  ``button_image`` = the model's asset."""
    from _mock_bootstrap import device_catalog

    from trcc.core.protocol import pm_to_fbl
    from trcc.core.registry import find_product

    out: list[dict] = []
    for idx, (vids, pm, sub, model, (w, h)) in enumerate(device_catalog()):
        vid, pid = vids[0]
        rsub = sub if sub is not None else 0
        product = find_product(vid, pid)
        out.append({
            "name": model,
            "path": f"{vid:04x}:{pid:04x}#{pm}:{rsub}",
            "button_image": model,
            "protocol": product.wire.value if product is not None else "scsi",
            "model": model,
            "vid": vid, "pid": pid,
            "pm": pm, "sub": rsub, "fbl": pm_to_fbl(pm, rsub),
            "res": f"{w}×{h}",
            "device_index": idx,
        })
    return out


class VariantPanel(QWidget):
    """Scrollable sub-panel of variant asset buttons; click → morph + present."""

    def __init__(self, window) -> None:
        parent = window.uc_device.parent()
        super().__init__(parent)
        self._window = window
        self._dicts = _variant_dicts()
        # The variant list REPLACES the real device-button area: hide the
        # sidebar's own device scroll so its buttons (current and any summoned
        # later — they're its children) can't show behind the scrollable list.
        window.uc_device.device_scroll.hide()
        # Paint our own opaque background (a plain QWidget ignores a stylesheet
        # background without this), so nothing shows through between buttons.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._geometry_from_sidebar()
        self._setup_ui()
        self.show()
        self.raise_()
        log.info("VariantPanel: %d variants, geometry=%s",
                 len(self._dicts), self.geometry().getRect())

    def _geometry_from_sidebar(self) -> None:
        """Occupy the device-button area exactly — we hid the real device
        buttons, so the scrollable variant list sits where they used to be."""
        from trcc.ui.gui.constants import Layout
        self.setGeometry(*Layout.DEVICE_AREA)        # (0, 160, 180, 560)

    def _setup_ui(self) -> None:
        from trcc.ui.gui.assets import Assets
        from trcc.ui.gui.constants import Colors
        from trcc.ui.gui.uc_device import _DEVICE_SCROLL_QSS, _get_device_images

        # Object-scoped so it neither cascades onto children nor (being a leaf
        # overlay, not an ancestor) blocks any QPalette image background.
        self.setObjectName("variantPanel")
        self.setStyleSheet(f"#variantPanel {{ background: {Colors.WINDOW_BG}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(_DEVICE_SCROLL_QSS)     # the sidebar's own scrollbar

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        col = QVBoxLayout(inner)
        # Left inset aligns the buttons with the x≈25 device buttons above; the
        # right gutter leaves the inner-side scrollbar its own uniform lane so it
        # never crowds the buttons.
        col.setContentsMargins(20, 4, 12, 4)
        col.setSpacing(3)
        col.setAlignment(Qt.AlignmentFlag.AlignTop)

        last_key: tuple[int, int] | None = None
        for d in self._dicts:
            key = (d["vid"], d["pid"])
            if key != last_key:
                hdr = QLabel(f"{d['vid']:04x}:{d['pid']:04x}")
                hdr.setStyleSheet(
                    f"color:{Colors.STATUS_TEXT};font-size:10px;font-weight:bold;"
                    "padding:5px 2px 1px 4px;background:transparent;")
                col.addWidget(hdr)
                last_key = key
            col.addWidget(self._variant_button(d, _get_device_images, Assets,
                                                Colors))

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def _variant_button(self, d: dict, get_images, assets, colors) -> QPushButton:
        normal, _active = get_images(d)
        btn = QPushButton(inner_text(d))
        btn.setFixedHeight(_BTN_H)
        # Mouse-only: deny keyboard focus + default-button status, so focus
        # traversal / Enter can never auto-activate a variant (it must be a click).
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setAutoDefault(False)
        btn.setDefault(False)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(
            f"{d['model']}  ·  pm={d['pm']} sub={d['sub']} fbl={d['fbl']}  "
            f"·  {d['res']}")
        pixmap = assets.load_pixmap(normal, _BTN_W, _BTN_H) if normal else None
        if pixmap is not None and not pixmap.isNull():
            btn.setText("")
            btn.setIcon(QIcon(pixmap))
            btn.setIconSize(QSize(_BTN_W, _BTN_H))
            btn.setFlat(True)
            btn.setStyleSheet(
                "QPushButton{border:none;background:transparent;}"
                f"QPushButton:hover{{background:{colors.HOVER_BG};border-radius:4px;}}")
        else:
            btn.setStyleSheet(
                f"QPushButton{{color:{colors.TEXT};font-size:10px;text-align:left;"
                f"padding:2px 6px;background:{colors.PANEL_FALLBACK};border:none;}}"
                f"QPushButton:hover{{background:{colors.HOVER_BG};}}")
        btn.clicked.connect(functools.partial(self._on_click, d))
        return btn

    # ── click → inject reply + reconnect + present ──────────────────────

    def _on_click(self, d: dict, _checked: bool = False) -> None:
        from trcc.core.commands import ConnectDevice

        w = self._window
        app = w._app
        vid, pid, pm, sub, fbl = d["vid"], d["pid"], d["pm"], d["sub"], d["fbl"]
        key = f"{vid:04x}:{pid:04x}"
        log.info("VariantPanel: select %s → %s pm=%d sub=%d fbl=%d",
                 d["model"], key, pm, sub, fbl)

        app.platform.set_active_reply(vid, pid, pm=pm, sub=sub, fbl=fbl)

        # Fresh handler so apply_device_config / show re-runs (both gate on
        # is_configured); ConnectDevice re-attaches + re-handshakes the reply.
        w._remove_handler(key)
        result = app.dispatch(ConnectDevice(key=key))
        if not getattr(result, "ok", False):
            w.uc_preview.set_status(f"variant {d['model']}: handshake failed")
            log.warning("VariantPanel: ConnectDevice failed for %s", key)
            return
        device = app.devices.get(key)
        if device is None:
            return
        w._add_handler(device)
        w._active_key = ""
        w._activate_device(key)
        self.raise_()                    # stay above the (refreshed) sidebar
        w.uc_preview.set_status(
            f"{d['model']}   ({key}  pm={pm} sub={sub} fbl={fbl})")


def inner_text(d: dict) -> str:
    """Text fallback when a variant has no button-image asset."""
    return f"{d['model']}\n{d['res']}"


def ensure_all_data(app) -> None:
    """Dev: ensure EVERY catalog resolution's data is downloaded (background).

    Checks each distinct resolution and downloads only what's missing
    (``data_install.ensure_all`` is idempotent).  This is the whole
    theme/mask/web data set for every panel — larger than the app itself — so it
    runs once into ``dev/.trcc/data/`` and is a no-op on later launches.  Runs
    off the main thread so the GUI stays responsive while it fetches.
    """
    import threading

    from _mock_bootstrap import NO_PANEL, device_catalog

    # LED coolers drive a segment display, not a canvas — they have no theme
    # data to fetch, and NO_PANEL is not a resolution ensure_all can serve.
    resolutions = sorted({
        res for *_rest, res in device_catalog() if res != NO_PANEL
    })

    def _worker() -> None:
        log.info("dev data: ensuring %d resolution(s) downloaded…", len(resolutions))
        ready = 0
        for w, h in resolutions:
            try:
                app.data_install.ensure_all((w, h))
                ready += 1
            except Exception:
                log.exception("dev data: ensure_all(%dx%d) failed", w, h)
        log.info("dev data: %d/%d resolution(s) ready in dev/.trcc/data",
                 ready, len(resolutions))

    threading.Thread(target=_worker, daemon=True, name="dev-data-prefetch").start()
    log.info("dev data: prefetching %d resolution(s) in the background",
             len(resolutions))


def mount(window):
    """Mount the variant sub-panel for a mock-fleet window; return it (or None).

    Guarded to a ``DevMockPlatform`` (``set_active_reply`` present).  The panel
    is a child widget, so the window keeps it alive.
    """
    platform = window._app.platform
    if not hasattr(platform, "set_active_reply"):
        log.info("VariantPanel.mount: not a mock fleet — skipped")
        return None
    panel = VariantPanel(window)
    log.info("VariantPanel.mount: mounted (%d variants)", len(panel._dicts))
    return panel

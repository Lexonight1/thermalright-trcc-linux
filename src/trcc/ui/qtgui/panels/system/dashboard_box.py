"""DashboardBox — the 4-row-per-panel sensor grid the LCD overlay reads.

Persisted as ``<config_dir>/system_config.json``.  Until
``Get/SetSensorDashboard`` existed the GUI imported the persistence adapter
directly, so cli / api / qtgui could not read the file at all.

The working layout is held here and nothing persists until the user saves:
``SetSensorDashboard`` takes the WHOLE layout in one bulk verb, matching
``SetOverlayConfig``, rather than a rebind per row.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .....core.commands import GetSensorDashboard, SetSensorDashboard
from .....core.models import PanelConfig
from ...sensor_picker import SensorPickerWidget
from ._base import SystemBox

log = logging.getLogger(__name__)

#: Shown in the Sensor column when a row is bound to nothing.  An empty cell
#: is indistinguishable from a column that failed to populate.
_UNBOUND = "— unbound —"


class DashboardBox(SystemBox):
    """Bind each dashboard row to a sensor, then save the layout."""

    TITLE = "Dashboard layout"
    STRETCH = 1

    def _build_ui(self) -> None:
        log.debug("_build_ui")
        layout = QVBoxLayout(self)

        self._tree = QTreeWidget(self)
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Panel / row", "Sensor", "Unit"])
        self._tree.itemSelectionChanged.connect(self._on_row_picked)
        layout.addWidget(self._tree, 1)

        self._picker = SensorPickerWidget(self._app, self._bus, self)
        layout.addWidget(self._picker, 1)

        # Two rows, grouped by WHAT THEY ACT ON: the panel, then the row.
        panel_row = QHBoxLayout()
        for label, slot in (
            ("Add panel", self._on_add_panel),
            ("Delete panel", self._on_delete_panel),
            ("Rename panel…", self._on_rename_panel),
        ):
            btn = QPushButton(label, self)
            btn.clicked.connect(slot)
            panel_row.addWidget(btn)
        panel_row.addStretch(1)
        layout.addLayout(panel_row)

        row = QHBoxLayout()
        bind = QPushButton("Bind selected row", self)
        bind.clicked.connect(self._on_bind)
        row.addWidget(bind)
        save = QPushButton("Save layout", self)
        save.clicked.connect(self._on_save)
        row.addWidget(save)
        row.addStretch(1)
        self._status = QLabel("", self)
        row.addWidget(self._status)
        layout.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        """Read the layout and show it.

        The Result hands out COPIES, so the working layout is held here and
        nothing persists until ``SetSensorDashboard`` -- which is also what
        happens over the daemon socket, where JSON hands back fresh objects.
        """
        log.debug("refresh")
        r = self.dispatch(GetSensorDashboard())
        self._panels = list(r.panels)
        log.info("refresh: %d panel(s), %d row(s) auto-mapped",
                 len(self._panels), getattr(r, "auto_mapped", 0))
        self._redraw()

    def _redraw(self) -> None:
        """Draw the tree from the WORKING layout, without touching the bus.

        Split out of :meth:`refresh` when add / delete / rename arrived: those
        edit ``self._panels`` and must be visible before they are saved, and
        calling ``refresh`` to show them would re-read the bus and discard the
        very edit being drawn.
        """
        log.debug("_redraw: %d panel(s)", len(self._panels))
        self._tree.clear()
        for p_i, panel in enumerate(self._panels):
            top = QTreeWidgetItem([panel.name, "", ""])
            top.setData(0, Qt.ItemDataRole.UserRole, (p_i, -1))
            for s_i, binding in enumerate(panel.sensors):
                child = QTreeWidgetItem(
                    [binding.label, binding.sensor_id or _UNBOUND,
                     binding.unit],
                )
                child.setData(0, Qt.ItemDataRole.UserRole, (p_i, s_i))
                top.addChild(child)
            self._tree.addTopLevelItem(top)
        self._tree.expandAll()

    def _selected_address(self) -> tuple[int, int] | None:
        """The ``(panel, row)`` the tree is on; ``row`` is ``-1`` on a header.

        The one decoder both questions below ask — the header/row distinction
        is already carried in the item's data (``_redraw`` writes it), so it
        is read once here rather than re-derived at each call site.
        """
        items = self._tree.selectedItems()
        log.debug("_selected_address: %d item(s) selected", len(items))
        if not items:
            return None
        return items[0].data(0, Qt.ItemDataRole.UserRole) or None

    def _selected_binding(self) -> tuple[int, int] | None:
        """The (panel, row) a ROW is selected on — never a panel header."""
        addr = self._selected_address()
        log.debug("_selected_binding: addr=%s", addr)
        return None if addr is None or addr[1] < 0 else addr

    def _selected_panel(self) -> int | None:
        """The panel index the tree is on, whether a header or one of its rows.

        Selecting a row and pressing "Delete panel" means the panel that row
        belongs to.  Refusing unless the header itself is selected would be a
        second rule for the user to learn for no gain.
        """
        addr = self._selected_address()
        log.debug("_selected_panel: addr=%s", addr)
        return None if addr is None else addr[0]

    def _on_row_picked(self) -> None:
        """Point the picker at whatever the selected row is already bound to."""
        addr = self._selected_binding()
        if addr is None:
            log.debug("_on_row_picked: no row selected")
            return
        p_i, s_i = addr
        current = self._panels[p_i].sensors[s_i].sensor_id
        log.debug("_on_row_picked: panel=%d row=%d current=%s",
                  p_i, s_i, current)
        if current:
            self._picker.select_sensor_id(current)

    def _on_bind(self) -> None:
        log.info("_on_bind")
        addr = self._selected_binding()
        if addr is None:
            log.warning("_on_bind: a panel heading has no row address")
            self._status.setText("Pick a row, not a panel heading.")
            return
        picked = self._picker.selected_sensor()
        if picked is None:
            log.warning("_on_bind: no sensor selected")
            self._status.setText("Pick a sensor first.")
            return
        sensor_id, label = picked
        p_i, s_i = addr
        binding = self._panels[p_i].sensors[s_i]
        binding.sensor_id = sensor_id
        if not binding.label:
            binding.label = label
        log.info("_on_bind: panel=%d row=%d -> %s", p_i, s_i, sensor_id)
        self._status.setText(f"{label} — unsaved")
        self._redraw_selected_row(sensor_id)

    def _redraw_selected_row(self, sensor_id: str) -> None:
        log.debug("_redraw_selected_row: sensor_id=%s", sensor_id)
        items = self._tree.selectedItems()
        if items:
            items[0].setText(1, sensor_id or _UNBOUND)

    def _on_add_panel(self) -> None:
        """Append an empty custom panel — unsaved until "Save layout"."""
        log.info("_on_add_panel: existing=%d", len(self._panels))
        self._panels.append(PanelConfig.custom())
        self._redraw()
        self._status.setText(f"Added {self._panels[-1].name} — unsaved")

    def _on_delete_panel(self) -> None:
        """Remove the selected panel — unsaved until "Save layout"."""
        index = self._selected_panel()
        log.info("_on_delete_panel: index=%s of %d", index, len(self._panels))
        if index is None:
            log.warning("_on_delete_panel: nothing selected")
            self._status.setText("Select a panel to delete.")
            return
        # SetSensorDashboard REFUSES an empty layout (it would make the next
        # read fall back to defaults — a wipe dressed up as a write), so the
        # refusal is surfaced here rather than at save time.
        if len(self._panels) == 1:
            log.warning("_on_delete_panel: refusing to delete the last panel")
            self._status.setText(
                "That is the last panel — an empty layout is refused.",
            )
            return
        removed = self._panels.pop(index)
        self._redraw()
        self._status.setText(f"Deleted {removed.name} — unsaved")

    def _on_rename_panel(self) -> None:
        """Rename the selected panel — unsaved until "Save layout"."""
        index = self._selected_panel()
        log.info("_on_rename_panel: index=%s", index)
        if index is None:
            log.warning("_on_rename_panel: nothing selected")
            self._status.setText("Select a panel to rename.")
            return
        panel = self._panels[index]
        # A dialog rather than an editable tree item: ``itemChanged`` fires on
        # every programmatic write in ``_redraw`` too, which would need a
        # guard flag to tell a user edit from a repaint.
        name, ok = QInputDialog.getText(
            self, "Rename panel", "Panel name:", text=panel.name,
        )
        if not ok or not (name := name.strip()):
            log.info("_on_rename_panel: cancelled or blank")
            return
        log.info("_on_rename_panel: %r -> %r", panel.name, name)
        panel.name = name
        self._redraw()
        self._status.setText(f"Renamed to {name} — unsaved")

    def _on_save(self) -> None:
        """Persist the WHOLE layout — one bulk verb, not a per-row rebind."""
        log.info("_on_save: %d panel(s)", len(self._panels))
        r = self.dispatch(SetSensorDashboard(panels=tuple(self._panels)))
        self._status.setText(r.message)
        if r.ok:
            self.refresh()

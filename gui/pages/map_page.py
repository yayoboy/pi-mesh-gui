"""Map page: toolbar (zoom / layer / neighbor toggle / recenter / markers) +
``MapView`` canvas + marker / waypoint dialogs.

The view-side widget lives in :mod:`gui.pages.map_view` so each module stays
small enough to navigate. This file owns Page wiring and the dialogs only.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt, QTimer, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.core.tasks import schedule as _module_schedule
from gui.pages.map_view import LAYER_NAMES, MapView
from gui.theme.colors import get_widget_colors
from gui.widgets.status_icons import (
    FlagIcon,
    HexIcon,
    MinusIcon,
    PlusIcon,
    TargetIcon,
    icon_pixmap,
)

log = logging.getLogger(__name__)


class Page(QWidget):
    def __init__(self, eventbus, settings):
        super().__init__()
        self._eventbus = eventbus
        self._settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar (top)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        bar.setSpacing(4)
        self._zoom_in = QPushButton(self)
        self._zoom_in.setIcon(QIcon(icon_pixmap(PlusIcon, 18, "#cdd")))
        self._zoom_in.setIconSize(QSize(18, 18))
        self._zoom_in.setAccessibleName("Aumenta zoom mappa")
        self._zoom_in.setToolTip("Zoom +")
        self._zoom_out = QPushButton(self)
        self._zoom_out.setIcon(QIcon(icon_pixmap(MinusIcon, 18, "#cdd")))
        self._zoom_out.setIconSize(QSize(18, 18))
        self._zoom_out.setAccessibleName("Riduci zoom mappa")
        self._zoom_out.setToolTip("Zoom −")
        # Bumped 34→40 for finger touch on the 3.5" kiosk display.
        self._zoom_in.setFixedWidth(40)
        self._zoom_out.setFixedWidth(40)
        self._zoom_label = QLabel(f"z{MapView.DEFAULT_ZOOM}")
        self._zoom_label.setProperty("role", "muted")
        self._zoom_label.setFixedWidth(20)

        # Layer switcher (osm / topo / satellite)
        self._layer_combo = QComboBox(self)
        for name in LAYER_NAMES:
            self._layer_combo.addItem(name)
        self._layer_combo.currentTextChanged.connect(self._on_layer)
        self._layer_combo.setFixedWidth(80)
        self._layer_combo.setAccessibleName("Seleziona livello mappa")

        # Neighbor links toggle
        self._neighbor_toggle = QToolButton(self)
        self._neighbor_toggle.setIcon(QIcon(icon_pixmap(HexIcon, 18, "#cdd")))
        self._neighbor_toggle.setIconSize(QSize(18, 18))
        self._neighbor_toggle.setToolTip("Mostra link tra vicini")
        self._neighbor_toggle.setAccessibleName("Mostra link tra nodi vicini")
        self._neighbor_toggle.setCheckable(True)
        self._neighbor_toggle.toggled.connect(self._on_toggle_neighbors)

        # Recenter
        recenter = QPushButton(self)
        recenter.setIcon(QIcon(icon_pixmap(TargetIcon, 18, "#cdd")))
        recenter.setIconSize(QSize(18, 18))
        recenter.setToolTip("Centra sul nodo locale")
        recenter.setAccessibleName("Centra mappa sul nodo locale")
        recenter.setFixedWidth(40)

        # Markers / waypoints list
        markers_btn = QToolButton(self)
        markers_btn.setIcon(QIcon(icon_pixmap(FlagIcon, 18, "#cdd")))
        markers_btn.setIconSize(QSize(18, 18))
        markers_btn.setToolTip("Marker / waypoint personalizzati")
        markers_btn.setAccessibleName("Apri elenco marker e waypoint")
        markers_btn.clicked.connect(self._show_markers_dialog)

        # Zoom cluster ( + − z5 ) stays grouped, then a small separator gap,
        # then layer/neighbor controls, stretch, action buttons on the right.
        bar.addWidget(self._zoom_in)
        bar.addWidget(self._zoom_out)
        bar.addWidget(self._zoom_label)
        bar.addSpacing(8)
        bar.addWidget(self._layer_combo)
        bar.addWidget(self._neighbor_toggle)
        bar.addStretch(1)
        bar.addWidget(markers_btn)
        bar.addWidget(recenter)
        layout.addLayout(bar)

        # Map view — resolve widget colors from the active palette.
        _theme = settings.get("display.theme") or "dark"
        self._view = MapView(self, colors=get_widget_colors(_theme))
        layout.addWidget(self._view, 1)

        self._zoom_in.clicked.connect(lambda: self._zoom(+1))
        self._zoom_out.clicked.connect(lambda: self._zoom(-1))
        recenter.clicked.connect(self._recenter_local)

        # Initial markers + waypoints + custom markers
        self._refresh_all()
        self._refresh_waypoints()
        self._refresh_custom_markers()

        # Periodic refresh — cheap, catches deletes too.
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._refresh_all)
        self._timer.start()

        # Slower waypoints poll so we don't hammer the API.
        self._wp_timer = QTimer(self)
        self._wp_timer.setInterval(15000)
        self._wp_timer.timeout.connect(self._refresh_waypoints)
        self._wp_timer.start()

        # Double-click on map → open new-location dialog.
        self._view.location_double_clicked.connect(self._on_double_click_location)

        if eventbus is not None:
            eventbus.position_updated.connect(self._on_position)
            eventbus.traceroute_result.connect(self._on_traceroute)
            eventbus.waypoint.connect(lambda _e: self._refresh_waypoints())
            eventbus.neighbor_info.connect(lambda _e: self._refresh_neighbors_if_visible())

        settings.subscribe("display.theme", self._on_theme_changed)

    def _on_theme_changed(self, theme: str | None) -> None:
        self._view.apply_colors(get_widget_colors(theme or "dark"))
        # Neighbor links carry SNR — rebuild from DB so the SNR ramp picks
        # up the new palette.
        self._refresh_neighbors_if_visible()

    def _zoom(self, delta: int) -> None:
        self._view.set_zoom(self._view.zoom() + delta, recenter=True)
        self._zoom_label.setText(f"z={self._view.zoom()}")

    def _recenter_local(self) -> None:
        try:
            import meshtasticd_client
            local = meshtasticd_client.get_local_node()
        except Exception:
            local = None
        if local and local.get("latitude") is not None:
            self._view.set_center(local["longitude"], local["latitude"])

    def _refresh_all(self) -> None:
        try:
            import meshtasticd_client
            nodes = meshtasticd_client.get_nodes()
        except Exception:
            nodes = []
        for n in nodes:
            lat = n.get("latitude")
            lon = n.get("longitude")
            if lat is None or lon is None:
                continue
            self._view.update_marker(
                n.get("id") or "?",
                float(lon),
                float(lat),
                label=n.get("short_name") or n.get("id"),
                is_local=bool(n.get("is_local")),
            )

    @Slot(dict)
    def _on_position(self, event: dict) -> None:
        node_id = event.get("id")
        lat = event.get("latitude")
        lon = event.get("longitude")
        if not node_id or lat is None or lon is None:
            return
        self._view.update_marker(node_id, float(lon), float(lat))

    @property
    def view(self) -> "MapView":
        return self._view

    def _on_layer(self, name: str) -> None:
        self._view.set_layer(name)

    def _on_toggle_neighbors(self, checked: bool) -> None:
        if checked:
            self._refresh_neighbors_if_visible()
        else:
            self._view.set_neighbor_links([])

    def _refresh_neighbors_if_visible(self) -> None:
        if not self._neighbor_toggle.isChecked():
            return
        _module_schedule(self._fetch_neighbors())

    async def _fetch_neighbors(self) -> None:
        try:
            import database
            links_raw = await database.get_neighbor_info()
        except Exception:
            return
        try:
            import meshtasticd_client
            nodes_by_id = {n.get("id"): n for n in meshtasticd_client.get_nodes()}
        except Exception:
            return
        prepared: list[tuple[float, float, float, float, float]] = []
        for l in links_raw:
            a = nodes_by_id.get(l.get("from_id"))
            b = nodes_by_id.get(l.get("neighbor_id"))
            if not a or not b:
                continue
            if a.get("latitude") is None or b.get("latitude") is None:
                continue
            prepared.append((
                float(a["longitude"]), float(a["latitude"]),
                float(b["longitude"]), float(b["latitude"]),
                float(l.get("snr") or 0.0),
            ))
        self._view.set_neighbor_links(prepared)

    def _refresh_waypoints(self) -> None:
        _module_schedule(self._fetch_waypoints())

    async def _fetch_waypoints(self) -> None:
        try:
            import database
            wps = await database.get_waypoints(active_only=True)
        except Exception:
            return
        seen = set()
        for wp in wps:
            wid = wp.get("id")
            lat = wp.get("lat") or wp.get("latitude")
            lon = wp.get("lon") or wp.get("longitude")
            if wid is None or lat is None or lon is None:
                continue
            seen.add(wid)
            self._view.update_waypoint(int(wid), float(lon), float(lat),
                                       name=wp.get("name") or "")
        # Drop waypoints that are no longer in the list.
        for wid in list(self._view._waypoint_items.keys()):
            if wid not in seen:
                self._view.remove_waypoint(wid)

    # ------------------------------------------------------------------
    # Custom markers + waypoints (CRUD)

    def _refresh_custom_markers(self) -> None:
        _module_schedule(self._fetch_custom_markers())

    async def _fetch_custom_markers(self) -> None:
        try:
            import config as cfg
            import database
            markers = await database.get_markers(cfg.DB_PATH)
        except Exception:
            return
        seen = set()
        for m in markers:
            mid = m.get("id")
            lat = m.get("latitude")
            lon = m.get("longitude")
            if mid is None or lat is None or lon is None:
                continue
            seen.add(int(mid))
            self._view.update_custom_marker(
                int(mid), float(lon), float(lat),
                label=m.get("label") or "", icon_type=m.get("icon_type") or "poi",
            )
        for mid in list(self._view._custom_marker_items.keys()):
            if mid not in seen:
                self._view.remove_custom_marker(mid)

    def _on_double_click_location(self, lon: float, lat: float) -> None:
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QLineEdit,
            QSpinBox,
            QToolButton,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Posizione  {lat:.5f}, {lon:.5f}")
        dlg.setModal(True)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # Match the kiosk panel so the trailing buttons can't fall off.
        win = self.window()
        dlg.setFixedSize(win.width(), win.height())

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        # Header with always-visible ✕ close target — finger-sized for kiosk.
        header = QHBoxLayout()
        header.addWidget(QLabel(f"Posizione  {lat:.5f}, {lon:.5f}"), 1)
        close_top = QToolButton(dlg)
        close_top.setText("✕")
        close_top.setToolTip("Chiudi")
        close_top.setAccessibleName("Chiudi dialogo")
        close_top.setFixedSize(44, 44)
        close_top.clicked.connect(dlg.reject)
        header.addWidget(close_top, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header)

        form = QFormLayout()
        outer.addLayout(form, 1)
        form.addRow(QLabel("Aggiungi un marker locale o invia un waypoint alla mesh."))

        label_edit = QLineEdit()
        label_edit.setPlaceholderText("Etichetta / nome")
        wp_desc = QLineEdit()
        wp_desc.setPlaceholderText("Descrizione waypoint (opzionale)")
        wp_expire = QSpinBox()
        wp_expire.setRange(1, 720)
        wp_expire.setSuffix(" h")
        wp_expire.setValue(24)

        form.addRow("Nome", label_edit)
        form.addRow("Descrizione", wp_desc)
        form.addRow("Durata waypoint", wp_expire)

        row = QHBoxLayout()
        add_marker_btn = QPushButton("Aggiungi marker")
        send_wp_btn = QPushButton("Invia waypoint")
        cancel_btn = QPushButton("Annulla")
        row.addWidget(add_marker_btn)
        row.addWidget(send_wp_btn)
        row.addStretch(1)
        row.addWidget(cancel_btn)
        form.addRow(row)

        choice = {"action": None}

        def do_marker():
            choice["action"] = "marker"
            dlg.accept()

        def do_wp():
            choice["action"] = "waypoint"
            dlg.accept()

        add_marker_btn.clicked.connect(do_marker)
        send_wp_btn.clicked.connect(do_wp)
        cancel_btn.clicked.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        name = label_edit.text().strip() or "marker"
        if choice["action"] == "marker":
            self._schedule(self._add_custom_marker_async(name, lat, lon))
        elif choice["action"] == "waypoint":
            self._schedule(self._send_waypoint_async(
                name, lat, lon, wp_desc.text().strip(), wp_expire.value()
            ))

    @staticmethod
    def _schedule(coro) -> None:
        _module_schedule(coro)

    async def _add_custom_marker_async(self, label: str, lat: float, lon: float) -> None:
        try:
            import config as cfg
            import database
            await database.create_marker(cfg.DB_PATH, label, "poi", lat, lon)
        except Exception:
            log.exception("add marker failed")
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Mappa", "Impossibile aggiungere il marker.")
        self._refresh_custom_markers()

    async def _send_waypoint_async(self, name: str, lat: float, lon: float,
                                   description: str, expire_hours: int) -> None:
        import time
        expire_ts = int(time.time()) + max(0, int(expire_hours)) * 3600
        try:
            import meshtasticd_client
            await meshtasticd_client.send_waypoint(
                name, lat, lon, "default", description or "", expire_ts,
            )
        except Exception:
            log.exception("send waypoint failed")
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Mappa", "Impossibile inviare il waypoint.")
        self._refresh_waypoints()

    def _show_markers_dialog(self) -> None:
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QListWidget,
            QListWidgetItem,
            QMessageBox,
            QToolButton,
            QVBoxLayout,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("Marker e waypoint")
        dlg.setModal(True)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        win = self.window()
        dlg.setFixedSize(win.width(), win.height())
        v = QVBoxLayout(dlg)
        v.setContentsMargins(8, 6, 8, 6)
        header = QHBoxLayout()
        header.addWidget(QLabel("Marker e waypoint"), 1)
        close_top = QToolButton(dlg)
        close_top.setText("✕")
        close_top.setToolTip("Chiudi")
        close_top.setAccessibleName("Chiudi dialogo")
        close_top.setFixedSize(44, 44)
        close_top.clicked.connect(dlg.reject)
        header.addWidget(close_top, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(header)
        v.addWidget(QLabel("Marker personali (viola) — doppio tap per rimuovere."))
        marker_list = QListWidget()
        v.addWidget(marker_list, 1)
        v.addWidget(QLabel("Waypoint (giallo) — inviati alla mesh, doppio tap per rimuovere."))
        wp_list = QListWidget()
        v.addWidget(wp_list, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        v.addWidget(bb)

        async def populate():
            try:
                import config as cfg
                import database
                markers = await database.get_markers(cfg.DB_PATH)
                waypoints = await database.get_waypoints(active_only=True)
            except Exception:
                return
            for m in markers:
                item = QListWidgetItem(
                    f"{m.get('label') or '?'}  ({m.get('latitude'):.4f}, {m.get('longitude'):.4f})"
                )
                item.setData(Qt.ItemDataRole.UserRole, ("marker", int(m["id"])))
                marker_list.addItem(item)
            for w in waypoints:
                item = QListWidgetItem(
                    f"{w.get('name') or '?'}  ({w.get('lat'):.4f}, {w.get('lon'):.4f})"
                )
                item.setData(Qt.ItemDataRole.UserRole, ("waypoint", int(w["id"])))
                wp_list.addItem(item)

        async def remove(kind: str, oid: int):
            try:
                import config as cfg
                import database
                if kind == "marker":
                    await database.delete_marker(cfg.DB_PATH, oid)
                else:
                    await database.delete_waypoint(oid)
            except Exception:
                pass
            self._refresh_custom_markers()
            self._refresh_waypoints()

        marker_list.itemDoubleClicked.connect(
            lambda it: self._schedule(remove(*it.data(Qt.ItemDataRole.UserRole)))
        )
        wp_list.itemDoubleClicked.connect(
            lambda it: self._schedule(remove(*it.data(Qt.ItemDataRole.UserRole)))
        )

        self._schedule(populate())
        dlg.exec()

    @Slot(dict)
    def _on_traceroute(self, event: dict) -> None:
        """Render the traceroute path from local node through the hop list.

        Uses the position cached in get_nodes() for each hop. Hops without
        a known position are skipped — partial paths still render the
        segments we can place.
        """
        try:
            import meshtasticd_client
            nodes_by_id = {n.get("id"): n for n in meshtasticd_client.get_nodes()}
            local_id = meshtasticd_client.get_local_id()
        except Exception:
            return
        dest = event.get("node_id") or event.get("id")
        hops = event.get("hops") or event.get("route") or []
        path: list[tuple[float, float]] = []

        chain = [local_id, *hops, dest] if dest else [local_id, *hops]
        for nid in chain:
            n = nodes_by_id.get(nid)
            if not n:
                continue
            lat = n.get("latitude")
            lon = n.get("longitude")
            if lat is None or lon is None:
                continue
            path.append((float(lon), float(lat)))

        if dest:
            self._view.show_traceroute(dest, path)

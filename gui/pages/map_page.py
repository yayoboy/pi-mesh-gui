"""Map page: pan/zoom QGraphicsView built on the pure helpers in map_math.

The widget renders an offline tile grid (``data/tiles/{z}/{x}/{y}.png``) and
overlays node markers. Live position updates come via ``EventBus.position_updated``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QComboBox,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.widgets.status_icons import FlagIcon, HexIcon, MinusIcon, PlusIcon, TargetIcon, icon_pixmap

from gui.pages.map_math import (
    MAX_ZOOM,
    MIN_ZOOM,
    TILE_SIZE,
    TileLoader,
    lonlat_to_pixel,
    pixel_to_lonlat,
    visible_tiles,
)

log = logging.getLogger(__name__)


# Where offline tiles live. scripts/download_tiles.py writes to
# static/tiles/{layer}/{z}/{x}/{y}.png — match that path so the map
# actually finds them. USB-mounted tile sets are spliced in at runtime
# by usb_storage.{mount,sync} so they end up under the same root.
TILES_BASE = Path("static/tiles")
LAYER_NAMES = ("osm", "topo", "satellite")


def _layer_root(layer: str) -> Path:
    return TILES_BASE / layer


def _load_pixmap(path: Path) -> QPixmap:
    pm = QPixmap(str(path))
    if pm.isNull():
        log.warning("could not load tile %s", path)
    return pm


class MapView(QGraphicsView):
    """Pan/zoom view that renders tiles + node markers in scene coordinates.

    Scene coords use the Web Mercator pixel space at the current zoom. On
    zoom change the scene is rebuilt; markers are kept in a dict keyed by
    node id so we can update without re-creating.
    """

    location_double_clicked = Signal(float, float)  # (lon, lat)

    DEFAULT_LAT = 41.9
    DEFAULT_LON = 12.5
    # Default zoom picked to land inside the range scripts/download_tiles.py
    # actually caches (7-12 for Italy). z=5 showed an empty map for new users
    # because no offline tile is generated for continent-scale zoom levels.
    DEFAULT_ZOOM = 9

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        self._zoom = self.DEFAULT_ZOOM
        self._layer = "osm"
        self._tiles = TileLoader(_layer_root(self._layer), reader=_load_pixmap)
        self._tile_items: dict[tuple[int, int, int], QGraphicsPixmapItem] = {}
        self._marker_items: dict[str, QGraphicsEllipseItem] = {}
        self._label_items: dict[str, QGraphicsTextItem] = {}
        self._traceroute_items: dict[str, QGraphicsPathItem] = {}
        self._waypoint_items: dict[int, tuple[QGraphicsEllipseItem, QGraphicsTextItem]] = {}
        self._custom_marker_items: dict[int, tuple[QGraphicsEllipseItem, QGraphicsTextItem]] = {}
        self._neighbor_items: list[QGraphicsLineItem] = []
        self._center_lon = self.DEFAULT_LON

        # Empty-state overlay (no tiles cached → otherwise users see a
        # black map and assume the GUI is broken). Parented to the view
        # itself (not the viewport) so it floats above the scene.
        self._empty_label = QLabel(self)
        self._empty_label.setText(
            "Nessuna tile offline disponibile.\n"
            "Esegui scripts/download_tiles.py."
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            "color: #8a92a4; font-size: 11px; background: transparent;"
        )
        self._empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._empty_label.hide()
        self._center_lat = self.DEFAULT_LAT

        self.set_zoom(self._zoom, recenter=True)

    # ------------------------------------------------------------------

    def zoom(self) -> int:
        return self._zoom

    def set_zoom(self, zoom: int, recenter: bool = False) -> None:
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if zoom == self._zoom and not recenter:
            return
        self._zoom = zoom
        # Wipe scene and rebuild at the new zoom.
        for item in list(self._tile_items.values()):
            self._scene.removeItem(item)
        self._tile_items.clear()

        if recenter:
            cx, cy = lonlat_to_pixel(self._center_lon, self._center_lat, zoom)
            self.centerOn(cx, cy)

        self._refresh_tiles()
        self._reposition_markers()

    def set_center(self, lon: float, lat: float) -> None:
        self._center_lon = lon
        self._center_lat = lat
        cx, cy = lonlat_to_pixel(lon, lat, self._zoom)
        self.centerOn(cx, cy)
        self._refresh_tiles()

    def set_layer(self, layer: str) -> None:
        if layer == self._layer:
            return
        self._layer = layer
        # Drop the old layer's tiles and switch the loader's tile root.
        for item in list(self._tile_items.values()):
            self._scene.removeItem(item)
        self._tile_items.clear()
        self._tiles = TileLoader(_layer_root(layer), reader=_load_pixmap)
        self._refresh_tiles()

    # ------------------------------------------------------------------
    # Tiles

    def _refresh_tiles(self) -> None:
        vp = self.viewport().size()
        # Center in scene coords:
        scene_center = self.mapToScene(self.viewport().rect().center())
        # Convert back to lon/lat to feed visible_tiles().
        # (We don't strictly need lon/lat: we could compute tile bounds from
        # scene_center directly — but visible_tiles is the API we have.)
        from gui.pages.map_math import pixel_to_lonlat
        lon, lat = pixel_to_lonlat(scene_center.x(), scene_center.y(), self._zoom)

        wanted: set[tuple[int, int, int]] = set()
        for tx, ty in visible_tiles(lon, lat, self._zoom, vp.width(), vp.height()):
            wanted.add((self._zoom, tx, ty))

        # Remove tiles no longer visible.
        for key in list(self._tile_items.keys()):
            if key not in wanted:
                self._scene.removeItem(self._tile_items.pop(key))

        # Add missing tiles.
        for z, tx, ty in wanted:
            key = (z, tx, ty)
            if key in self._tile_items:
                continue
            pm = self._tiles.get(z, tx, ty)
            if pm is None or (hasattr(pm, "isNull") and pm.isNull()):
                continue
            item = QGraphicsPixmapItem(pm)
            item.setPos(tx * TILE_SIZE, ty * TILE_SIZE)
            item.setZValue(-1)
            self._scene.addItem(item)
            self._tile_items[key] = item

        # Show / hide the offline-tiles empty state.
        if self._tile_items:
            self._empty_label.hide()
        else:
            self._empty_label.setGeometry(0, 0, self.width(), self.height())
            self._empty_label.show()
            self._empty_label.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._empty_label.setGeometry(0, 0, self.width(), self.height())

    # ------------------------------------------------------------------
    # Markers

    def update_marker(self, node_id: str, lon: float, lat: float, *, label: str | None = None,
                      is_local: bool = False) -> None:
        x, y = lonlat_to_pixel(lon, lat, self._zoom)
        radius = 6 if not is_local else 9
        color = QColor("#4a9eff") if not is_local else QColor("#ff5722")

        if node_id in self._marker_items:
            item = self._marker_items[node_id]
            item.setRect(x - radius, y - radius, radius * 2, radius * 2)
            item.setBrush(QBrush(color))
        else:
            item = QGraphicsEllipseItem(x - radius, y - radius, radius * 2, radius * 2)
            item.setBrush(QBrush(color))
            item.setPen(QPen(QColor("#000000"), 1))
            item.setZValue(1)
            self._scene.addItem(item)
            self._marker_items[node_id] = item

        text = label or node_id
        if node_id in self._label_items:
            tlbl = self._label_items[node_id]
            tlbl.setPos(x + radius + 2, y - radius - 4)
            tlbl.setPlainText(text)
        else:
            tlbl = QGraphicsTextItem(text)
            tlbl.setDefaultTextColor(QColor("#ffffff"))
            tlbl.setPos(x + radius + 2, y - radius - 4)
            tlbl.setZValue(2)
            self._scene.addItem(tlbl)
            self._label_items[node_id] = tlbl

    def clear_markers(self) -> None:
        for item in self._marker_items.values():
            self._scene.removeItem(item)
        for item in self._label_items.values():
            self._scene.removeItem(item)
        self._marker_items.clear()
        self._label_items.clear()

    def show_traceroute(self, key: str, points: list[tuple[float, float]]) -> None:
        """Draw a polyline through the given (lon, lat) points.

        ``key`` is an identifier (typically the destination node id) so the
        same path can be replaced when an updated traceroute arrives.
        Existing path with the same key is removed first.
        """
        self.clear_traceroute(key)
        if len(points) < 2:
            return
        path = QPainterPath()
        x, y = lonlat_to_pixel(points[0][0], points[0][1], self._zoom)
        path.moveTo(x, y)
        for lon, lat in points[1:]:
            x, y = lonlat_to_pixel(lon, lat, self._zoom)
            path.lineTo(x, y)
        item = QGraphicsPathItem(path)
        pen = QPen(QColor("#ffeb3b"))
        pen.setWidthF(2.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        item.setPen(pen)
        item.setZValue(0.5)  # above tiles, below markers
        self._scene.addItem(item)
        self._traceroute_items[key] = item

    def clear_traceroute(self, key: str | None = None) -> None:
        if key is None:
            for item in self._traceroute_items.values():
                self._scene.removeItem(item)
            self._traceroute_items.clear()
            return
        item = self._traceroute_items.pop(key, None)
        if item is not None:
            self._scene.removeItem(item)

    # -- waypoints (mesh-shared) -----------------------------------------

    def update_waypoint(self, wp_id: int, lon: float, lat: float, *, name: str = "") -> None:
        x, y = lonlat_to_pixel(lon, lat, self._zoom)
        marker, label = self._waypoint_items.get(wp_id, (None, None))
        radius = 5
        color = QColor("#ffeb3b")
        if marker is None:
            marker = QGraphicsEllipseItem(x - radius, y - radius, radius * 2, radius * 2)
            pen = QPen(QColor("#000000"), 1.0)
            marker.setPen(pen)
            marker.setBrush(QBrush(color))
            marker.setZValue(0.7)
            self._scene.addItem(marker)
            label = QGraphicsTextItem(name)
            label.setDefaultTextColor(QColor("#ffeb3b"))
            label.setZValue(0.8)
            self._scene.addItem(label)
        else:
            marker.setRect(x - radius, y - radius, radius * 2, radius * 2)
            label.setPlainText(name)
        label.setPos(x + radius + 2, y - radius - 4)
        self._waypoint_items[wp_id] = (marker, label)

    def remove_waypoint(self, wp_id: int) -> None:
        items = self._waypoint_items.pop(wp_id, None)
        if items is None:
            return
        for it in items:
            self._scene.removeItem(it)

    def clear_waypoints(self) -> None:
        for items in self._waypoint_items.values():
            for it in items:
                self._scene.removeItem(it)
        self._waypoint_items.clear()

    # -- custom markers (local DB only) ----------------------------------

    def update_custom_marker(self, marker_id: int, lon: float, lat: float, *,
                             label: str = "", icon_type: str = "poi") -> None:
        x, y = lonlat_to_pixel(lon, lat, self._zoom)
        marker, text = self._custom_marker_items.get(marker_id, (None, None))
        radius = 5
        # Distinguish from waypoints (yellow) and node markers (blue/orange).
        color = QColor("#9c27b0")  # purple
        if marker is None:
            marker = QGraphicsEllipseItem(x - radius, y - radius, radius * 2, radius * 2)
            marker.setPen(QPen(QColor("#ffffff"), 1.0))
            marker.setBrush(QBrush(color))
            marker.setZValue(0.7)
            self._scene.addItem(marker)
            text = QGraphicsTextItem(label)
            text.setDefaultTextColor(color)
            text.setZValue(0.8)
            self._scene.addItem(text)
        else:
            marker.setRect(x - radius, y - radius, radius * 2, radius * 2)
            text.setPlainText(label)
        text.setPos(x + radius + 2, y - radius - 4)
        self._custom_marker_items[marker_id] = (marker, text)

    def remove_custom_marker(self, marker_id: int) -> None:
        items = self._custom_marker_items.pop(marker_id, None)
        if items is None:
            return
        for it in items:
            self._scene.removeItem(it)

    def clear_custom_markers(self) -> None:
        for items in self._custom_marker_items.values():
            for it in items:
                self._scene.removeItem(it)
        self._custom_marker_items.clear()

    # -- neighbor links (SNR-coloured straight lines) --------------------

    def set_neighbor_links(self, links: list[tuple[float, float, float, float, float]]) -> None:
        """``links`` is a list of (a_lon, a_lat, b_lon, b_lat, snr)."""
        # Wipe old.
        for item in self._neighbor_items:
            self._scene.removeItem(item)
        self._neighbor_items.clear()
        for a_lon, a_lat, b_lon, b_lat, snr in links:
            x1, y1 = lonlat_to_pixel(a_lon, a_lat, self._zoom)
            x2, y2 = lonlat_to_pixel(b_lon, b_lat, self._zoom)
            line = QGraphicsLineItem(x1, y1, x2, y2)
            color = (
                QColor("#4caf50") if snr > 0
                else QColor("#ff9800") if snr > -10
                else QColor("#f44336")
            )
            pen = QPen(color)
            pen.setWidthF(1.2)
            line.setPen(pen)
            line.setZValue(0.4)
            self._scene.addItem(line)
            self._neighbor_items.append(line)

    def _reposition_markers(self) -> None:
        # When zoom changes, redraw markers at their new pixel coords.
        # Marker state is kept on instance so we can rebuild from cache:
        # for now, callers (the page) re-issue update_marker for every node.
        pass

    # ------------------------------------------------------------------
    # Wheel zoom

    def wheelEvent(self, ev: QWheelEvent) -> None:
        delta = ev.angleDelta().y()
        if delta == 0:
            return
        new_zoom = self._zoom + (1 if delta > 0 else -1)
        self.set_zoom(new_zoom, recenter=True)
        ev.accept()

    # Double-click → emit a (lon, lat) signal for the page to handle.
    def mouseDoubleClickEvent(self, ev) -> None:
        scene_p = self.mapToScene(ev.pos())
        lon, lat = pixel_to_lonlat(scene_p.x(), scene_p.y(), self._zoom)
        self.location_double_clicked.emit(float(lon), float(lat))
        ev.accept()


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
        self._zoom_out = QPushButton(self)
        self._zoom_out.setIcon(QIcon(icon_pixmap(MinusIcon, 18, "#cdd")))
        self._zoom_out.setIconSize(QSize(18, 18))
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

        # Neighbor links toggle
        self._neighbor_toggle = QToolButton(self)
        self._neighbor_toggle.setIcon(QIcon(icon_pixmap(HexIcon, 18, "#cdd")))
        self._neighbor_toggle.setIconSize(QSize(18, 18))
        self._neighbor_toggle.setToolTip("Mostra link tra vicini")
        self._neighbor_toggle.setCheckable(True)
        self._neighbor_toggle.toggled.connect(self._on_toggle_neighbors)

        # Recenter
        recenter = QPushButton(self)
        recenter.setIcon(QIcon(icon_pixmap(TargetIcon, 18, "#cdd")))
        recenter.setIconSize(QSize(18, 18))
        recenter.setToolTip("Centra sul nodo locale")
        recenter.setFixedWidth(40)

        # Markers / waypoints list
        markers_btn = QToolButton(self)
        markers_btn.setIcon(QIcon(icon_pixmap(FlagIcon, 18, "#cdd")))
        markers_btn.setIconSize(QSize(18, 18))
        markers_btn.setToolTip("Marker / waypoint personalizzati")
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

        # Map view
        self._view = MapView(self)
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
        loop = __import__("asyncio").get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(self._fetch_neighbors())

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
        loop = __import__("asyncio").get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(self._fetch_waypoints())

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
        loop = __import__("asyncio").get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(self._fetch_custom_markers())

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
        loop = __import__("asyncio").get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(coro)

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

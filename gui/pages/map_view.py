"""``MapView`` — pan/zoom ``QGraphicsView`` built on the pure helpers in
:mod:`gui.pages.map_math`.

The widget renders an offline tile grid (``data/tiles/{z}/{x}/{y}.png``) and
overlays node markers, waypoints, custom markers, neighbor links and
traceroute polylines. Page-level wiring and dialogs live in
:mod:`gui.pages.map_page` so each module stays small enough to navigate.
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

from gui.core.tasks import schedule as _module_schedule
from gui.theme.colors import WidgetColors, get_widget_colors
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

    def __init__(self, parent=None, *, colors: WidgetColors | None = None):
        super().__init__(parent)
        # Widget-color tokens (markers, waypoints, traceroute, neighbor SNR).
        # Resolved from the active palette by the Page; falls back to dark
        # variant when constructed standalone (tests, ad-hoc previews).
        self._colors: WidgetColors = colors or get_widget_colors(None)
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
        self._marker_is_local: dict[str, bool] = {}     # for live restyle on theme change
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
            f"color: {self._colors['subtitle_text']}; font-size: 11px; background: transparent;"
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

        # Capture the current viewport center in lon/lat before changing
        # zoom — otherwise zooming snaps the view back to _center_lon/lat
        # (the configured default) and loses whatever the user had panned
        # to. Skip the capture on the very first call when the viewport
        # isn't sized yet.
        old_zoom = self._zoom
        if self.viewport().width() > 0 and self.viewport().height() > 0:
            sc = self.mapToScene(self.viewport().rect().center())
            try:
                lon, lat = pixel_to_lonlat(sc.x(), sc.y(), old_zoom)
                # Only update if the projection landed somewhere sensible
                # (not the mercator world corner = uninitialised view).
                if -180 < lon < 180 and -85 < lat < 85:
                    self._center_lon, self._center_lat = lon, lat
                    recenter = True
            except Exception:
                pass

        self._zoom = zoom
        # Wipe scene and rebuild at the new zoom.
        for item in list(self._tile_items.values()):
            self._scene.removeItem(item)
        self._tile_items.clear()

        # Set sceneRect covering the mercator world at this zoom BEFORE
        # centering — centerOn is a no-op without a sceneRect.
        side = (2 ** zoom) * TILE_SIZE
        self._scene.setSceneRect(0, 0, side, side)

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
        # When the page is first shown the viewport size is 0 in __init__
        # so the initial _refresh_tiles call asks for tiles inside a 0x0
        # window and adds nothing to the scene. Re-run once a real size
        # is known so the map actually paints.
        if self.width() > 0 and self.height() > 0:
            self._refresh_tiles()

    def showEvent(self, e):
        super().showEvent(e)
        # Also re-center on the configured default whenever the user
        # switches back to the Mappa tab — covers the case where the
        # widget was constructed at 0x0 and never resized afterwards.
        if not self._tile_items and self.viewport().width() > 0:
            self.set_zoom(self._zoom, recenter=True)

    # ------------------------------------------------------------------
    # Markers

    def update_marker(self, node_id: str, lon: float, lat: float, *, label: str | None = None,
                      is_local: bool = False) -> None:
        self._marker_is_local[node_id] = is_local
        x, y = lonlat_to_pixel(lon, lat, self._zoom)
        radius = 6 if not is_local else 9
        color = QColor(self._colors["marker_local" if is_local else "marker_remote"])

        if node_id in self._marker_items:
            item = self._marker_items[node_id]
            item.setRect(x - radius, y - radius, radius * 2, radius * 2)
            item.setBrush(QBrush(color))
        else:
            item = QGraphicsEllipseItem(x - radius, y - radius, radius * 2, radius * 2)
            item.setBrush(QBrush(color))
            item.setPen(QPen(QColor(self._colors["marker_label_outline"]), 1))
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
            tlbl.setDefaultTextColor(QColor(self._colors["marker_label_text"]))
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
        self._marker_is_local.clear()

    def apply_colors(self, colors: WidgetColors) -> None:
        """Update the active widget-color tokens and re-tint existing items.

        Neighbor links are *not* re-tinted in place because the SNR value
        that drives their color is not stored on the QGraphicsLineItem; the
        Page is expected to call ``_refresh_neighbors_if_visible`` after
        invoking us so links are rebuilt from the DB.
        """
        self._colors = colors
        self._empty_label.setStyleSheet(
            f"color: {colors['subtitle_text']}; font-size: 11px; background: transparent;"
        )
        outline = QPen(QColor(colors["marker_label_outline"]), 1)
        for node_id, item in self._marker_items.items():
            is_local = self._marker_is_local.get(node_id, False)
            item.setBrush(QBrush(QColor(colors["marker_local" if is_local else "marker_remote"])))
            item.setPen(outline)
        label_text_color = QColor(colors["marker_label_text"])
        for tlbl in self._label_items.values():
            tlbl.setDefaultTextColor(label_text_color)
        wp_color = QColor(colors["waypoint"])
        wp_outline = QPen(QColor(colors["waypoint_outline"]), 1.0)
        for marker, label in self._waypoint_items.values():
            marker.setBrush(QBrush(wp_color))
            marker.setPen(wp_outline)
            label.setDefaultTextColor(wp_color)
        cm_color = QColor(colors["custom_marker"])
        cm_outline = QPen(QColor(colors["custom_marker_outline"]), 1.0)
        for marker, text in self._custom_marker_items.values():
            marker.setBrush(QBrush(cm_color))
            marker.setPen(cm_outline)
            text.setDefaultTextColor(cm_color)
        tr_color = QColor(colors["traceroute"])
        for path_item in self._traceroute_items.values():
            pen = path_item.pen()
            pen.setColor(tr_color)
            path_item.setPen(pen)

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
        pen = QPen(QColor(self._colors["traceroute"]))
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
        color = QColor(self._colors["waypoint"])
        if marker is None:
            marker = QGraphicsEllipseItem(x - radius, y - radius, radius * 2, radius * 2)
            pen = QPen(QColor(self._colors["waypoint_outline"]), 1.0)
            marker.setPen(pen)
            marker.setBrush(QBrush(color))
            marker.setZValue(0.7)
            self._scene.addItem(marker)
            label = QGraphicsTextItem(name)
            label.setDefaultTextColor(QColor(self._colors["waypoint"]))
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
        # Distinguish from waypoints and node markers via dedicated token.
        color = QColor(self._colors["custom_marker"])
        if marker is None:
            marker = QGraphicsEllipseItem(x - radius, y - radius, radius * 2, radius * 2)
            marker.setPen(QPen(QColor(self._colors["custom_marker_outline"]), 1.0))
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
            color = QColor(
                self._colors["snr_good"] if snr > 0
                else self._colors["snr_mid"] if snr > -10
                else self._colors["snr_bad"]
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

    # Long-press → emit a (lon, lat) signal for the page to handle.
    # We deliberately avoid mouseDoubleClickEvent here: on the
    # touchscreen libinput dispatches a single finger tap as a fast
    # press+release sequence that Qt's gesture filter would happily
    # promote to a double-click, so panning the map by drag landed on
    # the marker dialog. A hold timer started in mousePressEvent and
    # cancelled by mouseMoveEvent / mouseReleaseEvent matches the
    # universal "long-press = context action" gesture.
    _LONG_PRESS_MS = 600
    _LONG_PRESS_MOVE_PX = 8

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press_pos = ev.position().toPoint() if hasattr(ev, 'position') else ev.pos()
            self._long_press_timer = QTimer(self)
            self._long_press_timer.setSingleShot(True)
            self._long_press_timer.timeout.connect(self._on_long_press_timeout)
            self._long_press_timer.start(self._LONG_PRESS_MS)
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        # Cancel pending long-press if the finger drifts (= user is panning).
        t = getattr(self, '_long_press_timer', None)
        if t is not None and t.isActive():
            start = getattr(self, '_press_pos', None)
            cur = ev.position().toPoint() if hasattr(ev, 'position') else ev.pos()
            if start is None or (abs(cur.x() - start.x()) + abs(cur.y() - start.y())) > self._LONG_PRESS_MOVE_PX:
                t.stop()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        t = getattr(self, '_long_press_timer', None)
        if t is not None and t.isActive():
            t.stop()
        super().mouseReleaseEvent(ev)

    def _on_long_press_timeout(self) -> None:
        pos = getattr(self, '_press_pos', None)
        if pos is None:
            return
        scene_p = self.mapToScene(pos)
        lon, lat = pixel_to_lonlat(scene_p.x(), scene_p.y(), self._zoom)
        self.location_double_clicked.emit(float(lon), float(lat))


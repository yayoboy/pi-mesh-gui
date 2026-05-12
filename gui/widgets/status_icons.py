"""Vector icons for the status bar — drawn with QPainter so they survive
across distros that lack the right Unicode font glyphs.

Each icon is a small QWidget that paints a 14×14 area. The visual style is
deliberately monochromatic and matches the SVG paths in templates/base.html.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget


class _IconBase(QWidget):
    """Monochrome icon. Subclasses draw in a 14x14 coordinate space;
    the base paintEvent scales up to DISPLAY_SIZE automatically.

    Emits ``clicked`` on left-mouse release inside the widget so
    consumers can use an icon as a tap target without wrapping it
    in a QToolButton (which would force font-glyph rendering)."""

    _DESIGN_SIZE = 14
    DISPLAY_SIZE = 22

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(QSize(self.DISPLAY_SIZE, self.DISPLAY_SIZE))
        self._color = QColor("#9aa")
        self._tooltip = ""
        self._clickable = False

    def set_clickable(self, clickable: bool) -> None:
        self._clickable = bool(clickable)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._clickable else Qt.CursorShape.ArrowCursor)

    def set_color(self, color: str) -> None:
        if QColor(color) == self._color:
            return
        self._color = QColor(color)
        self.update()

    def set_tooltip(self, tooltip: str) -> None:
        self._tooltip = tooltip
        self.setToolTip(tooltip)

    def mouseReleaseEvent(self, ev):
        if self._clickable and ev.button() == Qt.MouseButton.LeftButton and self.rect().contains(ev.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(ev)

    def paintEvent(self, _event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            scale = self.DISPLAY_SIZE / self._DESIGN_SIZE
            p.scale(scale, scale)
            self._draw(p)
        finally:
            p.end()

    def _draw(self, _p: QPainter) -> None:
        raise NotImplementedError


class BatteryIcon(_IconBase):
    """Outline + variable fill from 0..1."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = 1.0  # 0..1

    def set_level(self, level: float | None) -> None:
        self._level = max(0.0, min(1.0, level)) if level is not None else 0.0
        self._color = (
            QColor("#9aa") if level is None
            else QColor("#f44336") if level < 0.2
            else QColor("#ff9800") if level < 0.5
            else QColor("#4caf50")
        )
        self.update()

    def _draw(self, p: QPainter) -> None:
        pen = QPen(self._color, 1.0)
        p.setPen(pen)
        # Battery body 1..11, height 4..10 (centered).
        p.drawRect(1, 4, 10, 6)
        p.drawRect(11, 6, 1, 2)  # nub
        if self._level > 0:
            inner_w = max(1, int(8 * self._level))
            p.fillRect(2, 5, inner_w, 4, QBrush(self._color))


class SignalIcon(_IconBase):
    """Four ascending bars; ``set_strength(snr)`` fills 0..4 from SNR."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bars = 0  # 0..4

    def set_strength(self, snr: float | None) -> None:
        if snr is None:
            self._bars = 0
            self._color = QColor("#9aa")
        else:
            self._bars = (
                4 if snr > 5
                else 3 if snr > 0
                else 2 if snr > -5
                else 1 if snr > -10
                else 0
            )
            self._color = (
                QColor("#4caf50") if self._bars >= 3
                else QColor("#ff9800") if self._bars == 2
                else QColor("#f44336") if self._bars == 1
                else QColor("#9aa")
            )
        self.update()

    def _draw(self, p: QPainter) -> None:
        for i in range(4):
            x = 1 + i * 3
            h = 2 + i * 3  # 2,5,8,11
            y = 12 - h
            color = self._color if i < self._bars else QColor(self._color.red(), self._color.green(), self._color.blue(), 60)
            p.fillRect(x, y, 2, h, color)


class GpsIcon(_IconBase):
    """Map-pin teardrop, dimmed when no fix.

    Path mirrors the SVG in templates/base.html (viewBox 0 0 24 24, scaled
    to 14×14): pin outline + 2.5 px center circle.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._has_fix = False

    def set_fix(self, has_fix: bool) -> None:
        self._has_fix = bool(has_fix)
        self._color = QColor("#4caf50") if has_fix else QColor("#9aa")
        self.update()

    def _draw(self, p: QPainter) -> None:
        pen = QPen(self._color, 1.2)
        p.setPen(pen)
        # Map 24x24 viewBox to 14x14 with a 1 px margin.
        s = 12.0 / 24.0
        ox, oy = 1.0, 1.0

        def x(v: float) -> float: return ox + v * s
        def y(v: float) -> float: return oy + v * s

        # Pin outline: M12 2 C 8.13 2  5 5.13  5 9 C 5 14.25  12 22  12 22 C 12 22  19 14.25  19 9 C 19 5.13  15.87 2  12 2 z
        path = QPainterPath()
        path.moveTo(x(12), y(2))
        path.cubicTo(x(8.13), y(2),    x(5),  y(5.13), x(5),  y(9))
        path.cubicTo(x(5),    y(14.25), x(12), y(22),    x(12), y(22))
        path.cubicTo(x(12),   y(22),    x(19), y(14.25), x(19), y(9))
        path.cubicTo(x(19),   y(5.13),  x(15.87), y(2),  x(12), y(2))
        p.drawPath(path)

        # Center dot — filled when there's a fix, hollow otherwise.
        cx, cy = x(12), y(9)
        r = 2.5 * s
        if self._has_fix:
            p.setBrush(QBrush(self._color))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)


class ConnIcon(_IconBase):
    """Filled dot when connected, ring when offline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False

    def set_connected(self, connected: bool) -> None:
        self._connected = bool(connected)
        self._color = QColor("#4caf50") if connected else QColor("#f44336")
        self.update()

    def _draw(self, p: QPainter) -> None:
        pen = QPen(self._color, 1.5)
        p.setPen(pen)
        if self._connected:
            p.setBrush(QBrush(self._color))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(2, 2, 10, 10)


# ---------------------------------------------------------------------------
# Status-bar action icons (clickable; replace the old text-glyph QToolButtons
# that rendered as tofu on the Pi because the SPI linuxfb has no emoji font).
# ---------------------------------------------------------------------------


class RotationIcon(_IconBase):
    """Curved arrow indicating screen rotation."""

    def _draw(self, p: QPainter) -> None:
        pen = QPen(self._color, 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(2.5, 2.5, 9, 9), 30 * 16, 270 * 16)
        head = QPolygonF([QPointF(10.5, 1.2), QPointF(12.5, 3.8), QPointF(8.6, 3.6)])
        p.setBrush(QBrush(self._color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(head)


class ScreenshotIcon(_IconBase):
    """Camera silhouette."""

    def _draw(self, p: QPainter) -> None:
        pen = QPen(self._color, 1.2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Top notch (lens hump).
        p.fillRect(QRectF(5, 2, 4, 1.5), QBrush(self._color))
        # Body.
        p.drawRoundedRect(QRectF(1.5, 3.5, 11, 8), 1.2, 1.2)
        # Lens.
        p.drawEllipse(QPointF(7, 7.5), 2.2, 2.2)


class RebootIcon(_IconBase):
    """Reset / circular arrow (counter-clockwise opening at top)."""

    def _draw(self, p: QPainter) -> None:
        pen = QPen(self._color, 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(2.5, 2.5, 9, 9), 120 * 16, 290 * 16)
        head = QPolygonF([QPointF(3.6, 1.4), QPointF(6.2, 3.0), QPointF(3.4, 4.6)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._color))
        p.drawPolygon(head)


class PowerIcon(_IconBase):
    """Classic power symbol: open-top arc + vertical stem."""

    def _draw(self, p: QPainter) -> None:
        pen = QPen(self._color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Arc with a gap at top so the stem reads as separate.
        p.drawArc(QRectF(2.5, 3, 9, 9), -60 * 16, -240 * 16)
        # Stem.
        p.drawLine(QPointF(7, 1.5), QPointF(7, 6))


# ---------------------------------------------------------------------------
# Tab-bar icons. Designed at 14×14 like the status icons; rendered into
# QPixmaps by ``icon_pixmap`` for use with QToolButton.setIcon().
# ---------------------------------------------------------------------------


class NodesIcon(_IconBase):
    """Three nodes connected (mesh topology)."""

    def _draw(self, p: QPainter) -> None:
        nodes = [QPointF(3, 4), QPointF(11, 4), QPointF(7, 11)]
        pen = QPen(self._color, 0.9)
        p.setPen(pen)
        # Connections first so dots cover endpoints.
        p.drawLine(nodes[0], nodes[1])
        p.drawLine(nodes[0], nodes[2])
        p.drawLine(nodes[1], nodes[2])
        p.setBrush(QBrush(self._color))
        p.setPen(Qt.PenStyle.NoPen)
        for n in nodes:
            p.drawEllipse(n, 1.6, 1.6)


class MapIcon(_IconBase):
    """Map pin (teardrop with center dot)."""

    def _draw(self, p: QPainter) -> None:
        pen = QPen(self._color, 1.2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(7, 1.5)
        path.cubicTo(3, 1.5, 2.5, 6.5, 7, 12.5)
        path.cubicTo(11.5, 6.5, 11, 1.5, 7, 1.5)
        p.drawPath(path)
        p.setBrush(QBrush(self._color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(7, 5.5), 1.4, 1.4)


class MessagesIcon(_IconBase):
    """Speech bubble with a tail."""

    def _draw(self, p: QPainter) -> None:
        pen = QPen(self._color, 1.2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(1.5, 2, 11, 7.5), 1.8, 1.8)
        # Tail pointing down-left.
        tail = QPolygonF([QPointF(4.5, 9.4), QPointF(4.5, 12.3), QPointF(6.8, 9.4)])
        p.setBrush(QBrush(self._color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(tail)


class ConfigIcon(_IconBase):
    """Gear (8 teeth) with a central hole."""

    def _draw(self, p: QPainter) -> None:
        import math
        cx, cy = 7.0, 7.0
        outer_r, inner_r = 5.8, 4.2
        teeth = 8
        path = QPainterPath()
        for i in range(teeth * 2):
            ang = math.pi * i / teeth - math.pi / 2
            r = outer_r if i % 2 == 0 else inner_r
            x = cx + r * math.cos(ang)
            y = cy + r * math.sin(ang)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        pen = QPen(self._color, 0.9)
        p.setPen(pen)
        p.setBrush(QBrush(self._color))
        p.drawPath(path)
        # Punch the center as a true transparent hole so the icon works on
        # any background (tab bar pixmap, status bar widget).
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.setBrush(QBrush(QColor(0, 0, 0, 255)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 1.7, 1.7)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


class MetricsIcon(_IconBase):
    """Ascending bar chart (4 bars)."""

    def _draw(self, p: QPainter) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._color))
        heights = (3, 5, 7, 9)
        for i, h in enumerate(heights):
            x = 1.5 + i * 3
            y = 12 - h
            p.fillRect(QRectF(x, y, 2, h), QBrush(self._color))


class LogIcon(_IconBase):
    """Three horizontal lines (list)."""

    def _draw(self, p: QPainter) -> None:
        pen = QPen(self._color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for y in (4, 7, 10):
            p.drawLine(QPointF(2.5, y), QPointF(11.5, y))


# ---------------------------------------------------------------------------
# Page-level action icons (map toolbar, messages canned menu — replaces
# Unicode glyphs that tofu'd on the SPI linuxfb).
# ---------------------------------------------------------------------------


class PlusIcon(_IconBase):
    """Plain plus sign for zoom-in."""

    def _draw(self, p: QPainter) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._color))
        p.drawRoundedRect(QRectF(2, 6.3, 10, 1.4), 0.7, 0.7)
        p.drawRoundedRect(QRectF(6.3, 2, 1.4, 10), 0.7, 0.7)


class MinusIcon(_IconBase):
    """Plain minus sign for zoom-out."""

    def _draw(self, p: QPainter) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._color))
        p.drawRoundedRect(QRectF(2, 6.3, 10, 1.4), 0.7, 0.7)


class TrashIcon(_IconBase):
    """Trash / delete (lid + body + 2 vertical strokes)."""

    def _draw(self, p: QPainter) -> None:
        pen = QPen(self._color, 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        # Lid bar.
        p.drawLine(QPointF(2, 4), QPointF(12, 4))
        # Handle on top of lid.
        p.drawLine(QPointF(5.5, 2.2), QPointF(8.5, 2.2))
        p.drawLine(QPointF(5.5, 2.2), QPointF(5.5, 4))
        p.drawLine(QPointF(8.5, 2.2), QPointF(8.5, 4))
        # Bin body (rounded rect outline).
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(3.2, 4.6, 7.6, 7.5), 0.8, 0.8)
        # Two inner vertical strokes.
        p.drawLine(QPointF(5.8, 6.2), QPointF(5.8, 10.6))
        p.drawLine(QPointF(8.2, 6.2), QPointF(8.2, 10.6))


class MenuIcon(_IconBase):
    """Hamburger menu (3 horizontal bars)."""

    def _draw(self, p: QPainter) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._color))
        for y in (3.5, 6.5, 9.5):
            p.drawRoundedRect(QRectF(2.5, y, 9, 1.4), 0.7, 0.7)


class TargetIcon(_IconBase):
    """Crosshair / recenter (concentric circle + cross hairs)."""

    def _draw(self, p: QPainter) -> None:
        pen = QPen(self._color, 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(7, 7), 4.0, 4.0)
        # Cross hairs sticking out of the circle.
        p.drawLine(QPointF(7, 1.5), QPointF(7, 3.0))
        p.drawLine(QPointF(7, 11.0), QPointF(7, 12.5))
        p.drawLine(QPointF(1.5, 7), QPointF(3.0, 7))
        p.drawLine(QPointF(11.0, 7), QPointF(12.5, 7))
        # Center dot.
        p.setBrush(QBrush(self._color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(7, 7), 1.0, 1.0)


class FlagIcon(_IconBase):
    """Map waypoint flag."""

    def _draw(self, p: QPainter) -> None:
        pen = QPen(self._color, 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        # Pole.
        p.drawLine(QPointF(3.5, 1.5), QPointF(3.5, 12.5))
        # Flag (filled triangle/trapezoid).
        p.setBrush(QBrush(self._color))
        p.setPen(Qt.PenStyle.NoPen)
        flag = QPolygonF([
            QPointF(3.5, 2.2),
            QPointF(11.5, 4.2),
            QPointF(11.5, 7.0),
            QPointF(3.5, 5.0),
        ])
        p.drawPolygon(flag)


class HexIcon(_IconBase):
    """Hexagonal node / neighbor-links indicator."""

    def _draw(self, p: QPainter) -> None:
        import math
        cx, cy, r = 7.0, 7.0, 5.5
        hexagon = QPolygonF()
        for i in range(6):
            ang = math.pi / 3 * i - math.pi / 6
            hexagon.append(QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang)))
        pen = QPen(self._color, 1.2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(hexagon)
        # Inner dot.
        p.setBrush(QBrush(self._color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 1.4, 1.4)


# ---------------------------------------------------------------------------
# Pixmap helper — render any _IconBase subclass into a QPixmap so we can
# stuff it into a QIcon for QToolButton.setIcon() on the tab bar.
# ---------------------------------------------------------------------------


def icon_pixmap(icon_cls: type["_IconBase"], size: int = 20, color: str = "#9aa") -> QPixmap:
    """Render ``icon_cls`` to a transparent QPixmap of side ``size`` (px)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    icon = icon_cls()
    icon._color = QColor(color)
    p = QPainter(pm)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        scale = size / icon_cls._DESIGN_SIZE
        p.scale(scale, scale)
        icon._draw(p)
    finally:
        p.end()
    return pm

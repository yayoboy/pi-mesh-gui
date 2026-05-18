"""Multi-series sparkline widget.

Wraps N :class:`SparklineBuffer` instances on the same canvas so several
metrics with a comparable y-range (typically 0..100 percentages) can be
read at a glance without splitting the eye across stacked charts.

Each series carries a stable key (used by ``push``) plus a label / color
for the inline legend. ``y_range`` is fixed at construction; pass
``None`` to fall back to per-paint autoscale across all buffers.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from gui.widgets.sparkline_buffer import SparklineBuffer


class MultiSparkline(QWidget):
    """Stack N series onto one canvas with a small inline legend."""

    _LEGEND_BAND_H = 14  # px reserved at the top for the colored swatches+labels

    def __init__(
        self,
        series: list[tuple[str, str, str]],
        *,
        capacity: int = 120,
        y_range: tuple[float, float] | None = (0.0, 100.0),
        parent: QWidget | None = None,
    ) -> None:
        """
        Parameters
        ----------
        series:
            Ordered list of ``(key, label, color_hex)``. ``key`` is what
            :meth:`push` consumes; ``label`` is what the legend shows.
        capacity:
            How many samples each buffer keeps. Same value for every
            series so they stay aligned in the x axis.
        y_range:
            Forced y-axis range. ``None`` autoscales per paint from the
            union of finite samples across all buffers.
        """
        super().__init__(parent)
        self._series: list[tuple[str, str, QColor]] = [
            (k, label, QColor(color)) for k, label, color in series
        ]
        self._buffers: dict[str, SparklineBuffer] = {
            k: SparklineBuffer(capacity=capacity) for k, _, _ in series
        }
        self._latest: dict[str, float | None] = {k: None for k, _, _ in series}
        self._y_range = y_range
        self.setMinimumHeight(48)

    # ------------------------------------------------------------------

    def push(self, values: dict[str, float | None]) -> None:
        """Append a sample for each known series key.

        Missing keys append ``None`` so the line shows a gap rather than
        a misleading interpolation across an unknown sample.
        """
        for key, buf in self._buffers.items():
            v = values.get(key)
            buf.push(v)
            if v is not None:
                self._latest[key] = v
        self.update()

    def set_series_color(self, key: str, color: str) -> None:
        """Re-tint one series. Called on live theme change."""
        for i, (k, label, _c) in enumerate(self._series):
            if k == key:
                self._series[i] = (k, label, QColor(color))
                self.update()
                return

    def latest(self, key: str) -> float | None:
        return self._latest.get(key)

    def clear(self) -> None:
        for buf in self._buffers.values():
            buf.clear()
        for k in self._latest:
            self._latest[k] = None
        self.update()

    # ------------------------------------------------------------------

    def _resolve_range(self) -> tuple[float, float]:
        if self._y_range is not None:
            return self._y_range
        lo = hi = None
        for buf in self._buffers.values():
            r_lo, r_hi = buf.auto_range()
            lo = r_lo if lo is None else min(lo, r_lo)
            hi = r_hi if hi is None else max(hi, r_hi)
        if lo is None or hi is None or lo == hi:
            return (0.0, 1.0) if lo is None else (lo - 1.0, lo + 1.0)
        return (lo, hi)

    def paintEvent(self, _event) -> None:
        w = max(2, self.width())
        total_h = max(2, self.height())
        chart_h = max(2, total_h - self._LEGEND_BAND_H)
        y_range = self._resolve_range()

        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            # Legend row.
            self._paint_legend(p, w)
            # Translate to leave the legend band on top.
            p.translate(0, self._LEGEND_BAND_H)
            for key, _label, color in self._series:
                buf = self._buffers[key]
                runs = buf.polylines(w, chart_h, y_range=y_range)
                if not runs:
                    continue
                pen = QPen(color)
                pen.setWidthF(1.5)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                for run in runs:
                    if len(run) < 2:
                        continue
                    for i in range(len(run) - 1):
                        x1, y1 = run[i]
                        x2, y2 = run[i + 1]
                        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        finally:
            p.end()

    def _paint_legend(self, p: QPainter, w: int) -> None:
        """Draw colored swatches + label · latest-value chips on the top band."""
        fm = QFontMetrics(self.font())
        x = 0.0
        gap = 10.0
        sw_w = 8.0
        sw_h = 2.5
        baseline = self._LEGEND_BAND_H - 3  # text baseline inside the band
        for key, label, color in self._series:
            latest = self._latest.get(key)
            val_str = f"{latest:.0f}" if isinstance(latest, (int, float)) else "—"
            text = f"{label} {val_str}"
            text_w = fm.horizontalAdvance(text)
            entry_w = sw_w + 4 + text_w + gap
            if x + entry_w > w:
                break  # don't overflow; drop the rest
            # Swatch
            p.fillRect(
                int(x),
                int((self._LEGEND_BAND_H - sw_h) / 2),
                int(sw_w),
                int(sw_h),
                color,
            )
            # Label + value
            p.setPen(QPen(self.palette().windowText().color()))
            p.drawText(int(x + sw_w + 4), int(baseline), text)
            x += entry_w

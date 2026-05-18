"""Metrics page: RPi telemetry cards + per-node board telemetry + exports.

Two stacked sections:
- Raspberry Pi: CPU%, RAM%, Temp, Uptime (sparklines), plus disk usage bar.
- Board Meshtastic: per-node summary cards (battery, voltage, temp,
  humidity, pressure …) built from the local telemetry table.

CSV / JSON export buttons write the latest rows from the telemetry
table to ``data/exports/`` directly — no HTTP API involved.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.core.tasks import schedule as _schedule
from gui.pages._telemetry_format import serialize_telemetry_rows as _serialize_telemetry_rows
from gui.theme.colors import get_widget_colors
from gui.widgets import status_icons as _status_icons
from gui.widgets.multi_sparkline import MultiSparkline
from gui.widgets.sparkline import Sparkline

log = logging.getLogger(__name__)

# Refresh cadence for both the local RPi sample loop and the Board Meshtastic
# pull. Telemetry packets typically arrive every few minutes per node, so a
# 15 s timer is more than enough; live updates also flow in via the event bus.
_REFRESH_MS = 15000


def _fmt_bytes_mb(mb: float | int | None) -> str:
    """Format a megabyte count as either '123 MB' or '4.2 GB'."""
    if mb is None:
        return "—"
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{int(mb)} MB"


def _level_for(value: float | None, warn: float, danger: float) -> str | None:
    """Return 'danger' / 'warn' / 'ok' / None based on threshold crossings."""
    if value is None:
        return None
    if value >= danger:
        return "danger"
    if value >= warn:
        return "warn"
    return "ok"


def _fmt_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    days = s // 86400
    hours = (s % 86400) // 3600
    minutes = (s % 3600) // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


class MetricCard(QFrame):
    """Single metric: label, big value, sparkline of recent samples."""

    def __init__(self, title: str, *, suffix: str = "", color: str | None = None, parent=None):
        # ``color`` defaults to the active palette's series-default token so
        # call sites that don't override it inherit the theme.
        if color is None:
            color = get_widget_colors(None)["series_default"]
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._suffix = suffix

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        title_lbl = QLabel(title)
        title_lbl.setProperty("role", "muted")
        layout.addWidget(title_lbl)

        self._value = QLabel("—")
        f = self._value.font()
        f.setPointSize(20)
        f.setBold(True)
        self._value.setFont(f)
        layout.addWidget(self._value)

        self._spark = Sparkline(capacity=60, color=color, parent=self)
        layout.addWidget(self._spark)

    def set_color(self, color: str) -> None:
        """Re-tint the embedded sparkline. Called on live theme change."""
        self._spark.set_color(color)

    def update_value(self, value, *, formatter=None) -> None:
        if value is None:
            self._value.setText("—")
            return
        if formatter is not None:
            self._value.setText(formatter(value))
        else:
            self._value.setText(f"{value:.0f}{self._suffix}")
        try:
            self._spark.push(float(value))
        except (TypeError, ValueError):
            self._spark.push(None)


class _LocalBoardChart(QFrame):
    """Aggregate chart for the local Meshtastic board.

    Three percentage series share one canvas (battery level, airtime TX,
    channel utilization). A compact line of chips above the chart prints
    the two non-percentage metrics that can't share the y-axis: average
    SNR of remote neighbors (dB) and last-seen RSSI (dBm).
    """

    def __init__(self, colors: dict, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(2)

        title = QLabel("Locale: batteria · airtime · canale")
        f = title.font()
        f.setPointSize(9)
        title.setFont(f)
        title.setProperty("role", "muted")
        outer.addWidget(title)

        # Chip row: SNR vicini · RSSI ultimo packet.
        chips = QHBoxLayout()
        chips.setSpacing(10)
        self._snr_chip = QLabel("SNR vicini —")
        self._rssi_chip = QLabel("RSSI ultimo —")
        for chip in (self._snr_chip, self._rssi_chip):
            chip.setProperty("role", "muted")
            cf = chip.font()
            cf.setPointSize(8)
            chip.setFont(cf)
            chips.addWidget(chip)
        chips.addStretch(1)
        outer.addLayout(chips)

        self._chart = MultiSparkline(
            series=[
                ("battery",  "Bat",  colors["battery_full"]),
                ("airtime",  "Air",  colors["series_ram"]),
                ("channel",  "Ch",   colors["series_default"]),
            ],
            capacity=120,
            y_range=(0.0, 100.0),
        )
        self._chart.setMinimumHeight(64)
        outer.addWidget(self._chart, 1)

    def apply_colors(self, colors: dict) -> None:
        self._chart.set_series_color("battery", colors["battery_full"])
        self._chart.set_series_color("airtime", colors["series_ram"])
        self._chart.set_series_color("channel", colors["series_default"])

    def update_history(self, samples: list[dict]) -> None:
        """Replace the chart contents with the given chronological samples.

        Each sample is the ``data`` dict from a ``telemetry`` row of
        ``ttype='device'`` for the local node, oldest first.
        """
        self._chart.clear()
        for s in samples:
            bl = s.get("battery_level")
            # >100 means "plugged in / external power": clamp to 100 for the
            # chart so the line doesn't shoot above the y-axis.
            if isinstance(bl, (int, float)) and bl > 100:
                bl = 100
            self._chart.push({
                "battery": bl,
                "airtime": s.get("air_util_tx"),
                "channel": s.get("channel_utilization"),
            })

    def update_neighbor_chips(self, *, avg_snr: float | None,
                              last_rssi: float | None) -> None:
        self._snr_chip.setText(
            f"SNR vicini {avg_snr:+.1f} dB" if avg_snr is not None
            else "SNR vicini —"
        )
        self._rssi_chip.setText(
            f"RSSI ultimo {last_rssi:.0f} dBm" if last_rssi is not None
            else "RSSI ultimo —"
        )


class _MetricCell(QWidget):
    """Compact icon + label pair, used as a single cell in the metrics row.

    Vector icon (QPainter) avoids the .notdef tofu we used to get on the
    SPI kiosk for emoji glyphs the fontconfig stack couldn't find.
    """

    _ICON_PX = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        self._icon = QLabel(self)
        self._icon.setFixedSize(self._ICON_PX, self._ICON_PX)
        self._text = QLabel("", self)
        self._text.setProperty("role", "muted")
        f = self._text.font()
        f.setPointSize(8)
        self._text.setFont(f)
        row.addWidget(self._icon)
        row.addWidget(self._text)
        self._icon_cls: type | None = None
        self._icon_color = "#9aa"

    def set_cell(self, icon_cls: type, text: str, color: str = "#9aa") -> None:
        if icon_cls is not self._icon_cls or color != self._icon_color:
            self._icon.setPixmap(_status_icons.icon_pixmap(icon_cls, self._ICON_PX, color))
            self._icon_cls = icon_cls
            self._icon_color = color
        self._text.setText(text)


class _NodeTelemetryCard(QFrame):
    """One row per node: short name + battery / voltage / env metrics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 4, 6, 4)
        self._layout.setSpacing(2)

        self._title = QLabel("?")
        f = self._title.font()
        f.setBold(True)
        self._title.setFont(f)
        self._layout.addWidget(self._title)

        self._metrics_row = QHBoxLayout()
        self._metrics_row.setSpacing(10)
        self._layout.addLayout(self._metrics_row)
        self._cells: list[_MetricCell] = []

    def _ensure_cells(self, count: int) -> None:
        while len(self._cells) < count:
            cell = _MetricCell(self)
            self._cells.append(cell)
            # Insert before the trailing stretch (if any). The row was set up
            # without one in __init__, so just append.
            self._metrics_row.addWidget(cell)
        for extra in self._cells[count:]:
            extra.hide()
        for visible in self._cells[:count]:
            visible.show()

    def fill(self, info: dict) -> None:
        self._title.setText(info.get("short_name") or "?")
        device = (info.get("device") or {}).get("data") or {}
        env = (info.get("environment") or {}).get("data") or {}
        power = (info.get("power") or {}).get("data") or {}
        air = (info.get("air_quality") or {}).get("data") or {}

        si = _status_icons
        specs: list[tuple[type, str]] = []

        # --- Device metrics ---
        if device.get("battery_level") is not None:
            bl = device["battery_level"]
            specs.append((si.BatteryIcon, "ext" if bl > 100 else f"{bl}%"))
        if device.get("voltage") is not None:
            specs.append((si.BoltIcon, f"{device['voltage']:.2f}V"))
        if device.get("channel_utilization") is not None:
            specs.append((si.ChannelIcon, f"{device['channel_utilization']:.0f}%"))
        if device.get("air_util_tx") is not None:
            specs.append((si.ChannelIcon, f"tx {device['air_util_tx']:.0f}%"))
        if device.get("uptime_seconds") is not None:
            specs.append((si.ClockIcon, _fmt_uptime(device["uptime_seconds"])))

        # --- Environment metrics ---
        if env.get("temperature") is not None:
            specs.append((si.ThermoIcon, f"{env['temperature']:.1f}°C"))
        if env.get("relative_humidity") is not None:
            specs.append((si.DropIcon, f"{env['relative_humidity']:.0f}%"))
        if env.get("barometric_pressure") is not None:
            specs.append((si.GaugeIcon, f"{env['barometric_pressure']:.0f}hPa"))
        if env.get("gas_resistance") is not None:
            # gas_resistance can land in kΩ or Ω depending on the firmware;
            # show a single significant figure to fit the cell width.
            gr = env["gas_resistance"]
            specs.append((si.GasIcon,
                          f"{gr / 1000:.1f}kΩ" if gr >= 1000 else f"{gr:.0f}Ω"))
        if env.get("iaq") is not None:
            specs.append((si.IaqIcon, f"IAQ {env['iaq']:.0f}"))
        # Future-proof: extra environment fields some firmwares may emit.
        if env.get("lux") is not None:
            specs.append((si.SunIcon, f"{env['lux']:.0f}lx"))
        if env.get("uv_lux") is not None or env.get("uv_index") is not None:
            v = env.get("uv_index") if env.get("uv_index") is not None else env.get("uv_lux")
            specs.append((si.UvIcon, f"UV {v:.1f}"))
        if env.get("wind_speed") is not None:
            specs.append((si.WindIcon, f"{env['wind_speed']:.1f}m/s"))
        if env.get("wind_direction") is not None:
            specs.append((si.CompassIcon, f"{env['wind_direction']:.0f}°"))
        if env.get("rainfall_1h") is not None or env.get("rainfall_24h") is not None:
            v = (env.get("rainfall_1h") if env.get("rainfall_1h") is not None
                 else env.get("rainfall_24h"))
            specs.append((si.RainIcon, f"{v:.1f}mm"))
        if env.get("weight") is not None:
            specs.append((si.WeightIcon, f"{env['weight']:.1f}kg"))
        if env.get("distance") is not None:
            specs.append((si.RulerIcon, f"{env['distance']:.0f}mm"))

        # --- Power metrics (3 channels) ---
        for ch in (1, 2, 3):
            v = power.get(f"ch{ch}_voltage")
            i = power.get(f"ch{ch}_current")
            if v is not None:
                specs.append((si.BoltIcon, f"ch{ch} {v:.2f}V"))
            if i is not None:
                specs.append((si.CurrentIcon, f"ch{ch} {i:.0f}mA"))

        # --- Air quality (PMSA0031) ---
        # Prefer the "environmental" particulate readings if present, fall
        # back to the "standard" ones; only show the two most-watched sizes
        # so the row doesn't overflow.
        for size_key, label in (("pm25", "PM2.5"), ("pm10", "PM10")):
            v = (air.get(f"{size_key}_environmental")
                 or air.get(f"{size_key}_standard"))
            if v is not None:
                specs.append((si.DustIcon, f"{label} {v:.0f}"))

        self._ensure_cells(len(specs))
        for cell, (icon_cls, text) in zip(self._cells, specs):
            cell.set_cell(icon_cls, text)


class Page(QWidget):
    def __init__(self, eventbus, settings):
        super().__init__()
        self._eventbus = eventbus
        self._settings = settings
        self._node_cards: dict[str, _NodeTelemetryCard] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        # ---- Raspberry Pi section
        rpi_head = QHBoxLayout()
        rpi_title = QLabel("Raspberry Pi")
        f = rpi_title.font()
        f.setPointSize(11); f.setBold(True)
        rpi_title.setFont(f)
        rpi_title.setProperty("role", "muted")
        rpi_head.addWidget(rpi_title)
        rpi_head.addStretch(1)
        self._updated_lbl = QLabel("—")
        self._updated_lbl.setProperty("role", "muted")
        f2 = self._updated_lbl.font()
        f2.setPointSize(9)
        self._updated_lbl.setFont(f2)
        rpi_head.addWidget(self._updated_lbl)
        layout.addLayout(rpi_head)

        grid = QGridLayout()
        grid.setSpacing(6)
        _theme = settings.get("display.theme") or "dark"
        _c = get_widget_colors(_theme)
        self._cpu = MetricCard("CPU",  suffix=" %", color=_c["series_cpu"])
        self._ram = MetricCard("RAM",  suffix=" %", color=_c["series_ram"])
        self._tmp = MetricCard("Temp", suffix=" °C", color=_c["series_temp"])
        self._upt = MetricCard("Uptime")
        grid.addWidget(self._cpu, 0, 0)
        grid.addWidget(self._ram, 0, 1)
        grid.addWidget(self._tmp, 1, 0)
        grid.addWidget(self._upt, 1, 1)
        layout.addLayout(grid)

        settings.subscribe("display.theme", self._on_theme_changed)

        # Disk bar
        disk_row = QFrame()
        disk_row.setFrameShape(QFrame.Shape.StyledPanel)
        dr = QVBoxLayout(disk_row)
        dr.setContentsMargins(8, 4, 8, 4)
        dr.setSpacing(2)
        head = QHBoxLayout()
        head.addWidget(QLabel("Disk"))
        self._disk_value = QLabel("—")
        self._disk_value.setProperty("role", "muted")
        head.addStretch(1)
        head.addWidget(self._disk_value)
        dr.addLayout(head)
        self._disk_bar = QProgressBar()
        self._disk_bar.setRange(0, 100)
        self._disk_bar.setValue(0)
        self._disk_bar.setTextVisible(False)
        self._disk_bar.setFixedHeight(6)
        dr.addWidget(self._disk_bar)
        layout.addWidget(disk_row)

        # ---- Board Meshtastic section
        board_head = QHBoxLayout()
        board_title = QLabel("Board Meshtastic")
        f = board_title.font()
        f.setPointSize(11); f.setBold(True)
        board_title.setFont(f)
        board_title.setProperty("role", "muted")
        board_head.addWidget(board_title)
        board_head.addStretch(1)
        csv_btn = QPushButton("CSV")
        json_btn = QPushButton("JSON")
        csv_btn.setFixedWidth(48)
        json_btn.setFixedWidth(48)
        csv_btn.setAccessibleName("Esporta metriche board in CSV")
        json_btn.setAccessibleName("Esporta metriche board in JSON")
        csv_btn.clicked.connect(lambda: self._export("csv"))
        json_btn.clicked.connect(lambda: self._export("json"))
        board_head.addWidget(csv_btn)
        board_head.addWidget(json_btn)
        layout.addLayout(board_head)

        # Aggregate chart for the local board (battery / airtime / channel).
        # Placed above the per-node cards so it's the first thing visible
        # when scrolling into the Board section.
        self._local_chart = _LocalBoardChart(_c, parent=body)
        layout.addWidget(self._local_chart)

        self._node_cards_host = QWidget()
        self._node_cards_layout = QVBoxLayout(self._node_cards_host)
        self._node_cards_layout.setContentsMargins(0, 0, 0, 0)
        self._node_cards_layout.setSpacing(4)
        layout.addWidget(self._node_cards_host)

        self._empty_label = QLabel("No telemetry yet")
        self._empty_label.setProperty("role", "muted")
        layout.addWidget(self._empty_label)

        layout.addStretch(1)

        # Periodic refresh for RPi metrics and board telemetry. Both run at
        # _REFRESH_MS; live telemetry events still trigger an immediate
        # board refresh via the event bus below.
        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._poll()

        self._board_timer = QTimer(self)
        self._board_timer.setInterval(_REFRESH_MS)
        self._board_timer.timeout.connect(lambda: _schedule(self._refresh_board()))
        self._board_timer.start()
        _schedule(self._refresh_board())

        if eventbus is not None:
            eventbus.rpi_telemetry.connect(self._on_event)
            eventbus.telemetry.connect(lambda _e: _schedule(self._refresh_board()))

    # ------------------------------------------------------------------

    def _poll(self) -> None:
        try:
            import rpi_telemetry
            data = rpi_telemetry.collect()
        except Exception:
            log.exception("rpi_telemetry.collect failed")
            return
        self._apply(data)

    @Slot(dict)
    def _on_event(self, event: dict) -> None:
        data = event.get("data") if "data" in event else event
        self._apply(data)

    def _apply(self, data: dict) -> None:
        self._cpu.update_value(data.get("cpu_percent"))
        self._ram.update_value(data.get("ram_percent"))
        self._tmp.update_value(data.get("cpu_temp"), formatter=lambda v: f"{v:.1f} °C")
        self._upt.update_value(
            data.get("uptime_seconds"),
            formatter=lambda v: _fmt_uptime(v),
        )

        # Thresholded warning levels: same QSS roles ("ok"/"warn"/"danger")
        # the disk bar already uses, applied to the value labels too.
        self._set_level(self._cpu, _level_for(data.get("cpu_percent"), 75, 90))
        self._set_level(self._ram, _level_for(data.get("ram_percent"), 80, 95))
        self._set_level(self._tmp, _level_for(data.get("cpu_temp"), 70, 80))

        disk_pct = data.get("disk_percent")
        if disk_pct is not None:
            self._disk_bar.setValue(int(disk_pct))
            level = "danger" if disk_pct >= 90 else "warn" if disk_pct >= 75 else "ok"
            if self._disk_bar.property("level") != level:
                self._disk_bar.setProperty("level", level)
                self._disk_bar.style().unpolish(self._disk_bar)
                self._disk_bar.style().polish(self._disk_bar)
        if data.get("disk_used_mb") is not None and data.get("disk_total_mb"):
            self._disk_value.setText(
                f"{_fmt_bytes_mb(data['disk_used_mb'])} / {_fmt_bytes_mb(data['disk_total_mb'])}"
            )

        ts = data.get("ts")
        if ts is not None and hasattr(self, "_updated_lbl"):
            import time as _t
            self._updated_lbl.setText(_t.strftime("%H:%M:%S", _t.localtime(ts)))

    @staticmethod
    def _set_level(card, level: str | None) -> None:
        """Apply a 'level' QSS property to the value label inside a MetricCard."""
        lbl = card._value
        if lbl.property("level") == level:
            return
        lbl.setProperty("level", level or "")
        lbl.style().unpolish(lbl)
        lbl.style().polish(lbl)

    async def _refresh_board(self) -> None:
        try:
            import config as cfg
            import database
            import meshtasticd_client
            # 500 recent rows is enough to capture the latest device +
            # environment + power + air_quality sample for every active
            # node in the last few hours.
            rows = await database.get_telemetry(cfg.DB_PATH, limit=500)
        except Exception:
            log.debug("board telemetry refresh failed", exc_info=True)
            return

        all_nodes = list(meshtasticd_client.get_nodes())
        nodes_by_id = {n.get("id"): n for n in all_nodes}
        local_id = meshtasticd_client.get_local_id()

        # ---- Local board chart: chronological history of device-ttype rows.
        if local_id:
            local_device_samples: list[dict] = [
                (r.get("data") or {})
                for r in reversed(rows)  # rows is ts DESC; we want oldest first
                if r.get("node_id") == local_id and r.get("ttype") == "device"
            ]
            self._local_chart.update_history(local_device_samples[-120:])

            # Aggregate neighbor metrics for the chip row.
            remote_snrs = [
                n["snr"] for n in all_nodes
                if not n.get("is_local") and isinstance(n.get("snr"), (int, float))
            ]
            avg_snr = sum(remote_snrs) / len(remote_snrs) if remote_snrs else None
            # Last-heard remote node's RSSI gives the most recent reception.
            last_rssi: float | None = None
            for n in sorted(all_nodes, key=lambda x: -(x.get("last_heard") or 0)):
                if n.get("is_local"):
                    continue
                r = n.get("rssi")
                if isinstance(r, (int, float)):
                    last_rssi = float(r)
                    break
            self._local_chart.update_neighbor_chips(
                avg_snr=avg_snr, last_rssi=last_rssi
            )

        # Keep only the most recent row per (node_id, ttype). rows come
        # back ts DESC, so the first match wins.
        data: dict[str, dict] = {}
        seen_keys: set[tuple[str, str]] = set()
        for r in rows:
            nid = r.get("node_id")
            ttype = r.get("ttype")
            if not nid or not ttype:
                continue
            key = (nid, ttype)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            info = data.setdefault(nid, {})
            info[ttype] = {"ts": r.get("ts"), "data": r.get("data") or {}}
            if "short_name" not in info:
                n = nodes_by_id.get(nid)
                short = (n or {}).get("short_name") if n else None
                info["short_name"] = short or (nid if nid != local_id else "Local")

        # Order: local node first, then by last device/environment ts desc.
        def _row_ts(info: dict) -> int:
            return max(
                int((info.get("device") or {}).get("ts") or 0),
                int((info.get("environment") or {}).get("ts") or 0),
                int((info.get("power") or {}).get("ts") or 0),
                int((info.get("air_quality") or {}).get("ts") or 0),
            )
        ordered = sorted(
            data.items(),
            key=lambda kv: (kv[0] != local_id, -_row_ts(kv[1])),
        )

        seen = set()
        for idx, (nid, info) in enumerate(ordered):
            seen.add(nid)
            card = self._node_cards.get(nid)
            if card is None:
                card = _NodeTelemetryCard(self._node_cards_host)
                self._node_cards_layout.insertWidget(idx, card)
                self._node_cards[nid] = card
            else:
                self._node_cards_layout.removeWidget(card)
                self._node_cards_layout.insertWidget(idx, card)
            card.fill(info)
        for nid in list(self._node_cards.keys()):
            if nid not in seen:
                w = self._node_cards.pop(nid)
                self._node_cards_layout.removeWidget(w)
                w.deleteLater()
        self._empty_label.setVisible(len(self._node_cards) == 0)

    # ------------------------------------------------------------------

    def _export(self, fmt: str) -> None:
        _schedule(self._export_async(fmt))

    async def _export_async(self, fmt: str) -> None:
        from datetime import datetime
        out_dir = Path("data/exports")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"telemetry-{datetime.now():%Y%m%d-%H%M%S}.{fmt}"
        try:
            import config as cfg
            import database
            rows = await database.get_telemetry(cfg.DB_PATH, limit=1000)
            payload = _serialize_telemetry_rows(rows, fmt)
            out_path.write_text(payload, encoding="utf-8")
        except Exception as exc:
            QMessageBox.warning(self, "Export", f"Export error: {exc}")
            return
        from gui.widgets.toast import show_toast
        show_toast(self, f"Saved {out_path.name}", role="ok")

    def _on_theme_changed(self, theme: str | None) -> None:
        c = get_widget_colors(theme or "dark")
        self._cpu.set_color(c["series_cpu"])
        self._ram.set_color(c["series_ram"])
        self._tmp.set_color(c["series_temp"])
        self._local_chart.apply_colors(c)

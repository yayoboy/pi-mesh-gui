"""Log page: scrollable view of incoming radio packets, capped to N lines.

Two sources are merged:
- ``meshtasticd_client._log_queue`` for the historical buffer (loaded on
  page open).
- ``EventBus.log_line`` for live updates after that.

Toolbar provides Pause/Resume, Clear, Auto-scroll, substring filter, a
row of portnum toggle pills (auto-populated as new types are seen) and
a TSV export button.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.theme.colors import get_widget_colors

log = logging.getLogger(__name__)


_MAX_LINES = 2000


def format_log_line(event: dict) -> str:
    """Render a log event dict as a single human-readable line.

    Columns: "HH:MM:SS · from · SNR · portnum · summary".
    summary is the decoded payload preview built by
    meshtasticd_client._build_log_summary; without it every periodic
    TELEMETRY_APP packet looked identical on screen.
    """
    import time as _t
    ts = event.get("ts")
    ts_s = _t.strftime("%H:%M:%S", _t.localtime(ts)) if ts else "--:--:--"
    src = event.get("from") or event.get("id") or "?"
    snr = event.get("snr")
    snr_s = f"SNR {snr:+.1f}" if isinstance(snr, (int, float)) else "SNR —"
    port = event.get("portnum") or event.get("decoded_portnum") or "?"
    # Shorten portnum: TELEMETRY_APP → TELEMETRY, TEXT_MESSAGE_APP → TEXT.
    port_short = port.replace("_APP", "").replace("APP", "").lstrip("_") or port
    parts = [ts_s, src, snr_s, port_short]
    summary = event.get("summary") or event.get("text") or ""
    if summary:
        parts.append(summary)
    return " · ".join(parts)


class Page(QWidget):
    def __init__(self, eventbus, settings):
        super().__init__()
        self._eventbus = eventbus
        self._settings = settings
        self._paused = False
        self._filter = ""
        self._portnum_filters: set[str] = set()    # active filter set; empty = no filter
        self._known_portnums: set[str] = set()
        self._pill_buttons: list[QToolButton] = []  # filter chips, retained for live restyle
        self._lines: list[dict] = []                # raw events kept for TSV export

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Top toolbar — Pause / Clear / Auto-scroll / search / count.
        bar = QHBoxLayout()
        self._auto = QCheckBox("Auto")
        self._auto.setChecked(True)
        self._pause_btn = QPushButton("Pausa")
        self._pause_btn.setCheckable(True)
        self._pause_btn.toggled.connect(self._on_pause)
        clear = QPushButton("Pulisci")
        clear.clicked.connect(self._on_clear)
        export_btn = QPushButton("TSV")
        export_btn.setToolTip("Esporta righe filtrate come TSV")
        export_btn.setAccessibleName("Esporta log filtrato in TSV")
        export_btn.clicked.connect(self._on_export)
        self._search = QLineEdit()
        self._search.setPlaceholderText("filtra log…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_filter)
        self._count = QLabel("0")
        self._count.setProperty("role", "muted")

        bar.addWidget(self._auto)
        bar.addWidget(self._pause_btn)
        bar.addWidget(clear)
        bar.addWidget(export_btn)
        bar.addStretch(1)
        bar.addWidget(self._search)
        bar.addWidget(self._count)
        layout.addLayout(bar)

        # Portnum filter pills row (populates dynamically).
        self._pills_row = QHBoxLayout()
        self._pills_row.setContentsMargins(2, 2, 2, 2)
        self._pills_row.setSpacing(4)
        # Trailing stretch so pills cluster left, signalling "filter chips"
        # rather than a centered section heading.
        self._pills_row.addStretch(1)
        layout.addLayout(self._pills_row)

        # The log view
        self._view = QPlainTextEdit(self)
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(_MAX_LINES)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._view.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        f = self._view.font()
        f.setFamily("monospace")
        self._view.setFont(f)
        layout.addWidget(self._view, 1)

        self._load_history()

        if eventbus is not None:
            eventbus.log_line.connect(self._on_event)
            # Also feed the local Pi telemetry into the same log stream so
            # both halves of the device (board + host) are visible together
            # in one place. Pi rows are tagged so they're easy to filter out.
            eventbus.rpi_telemetry.connect(self._on_rpi_event)

        settings.subscribe("display.theme", self._on_theme_changed)

    # ------------------------------------------------------------------

    def _load_history(self) -> None:
        try:
            import meshtasticd_client
            history = list(meshtasticd_client.get_log_queue())
        except Exception:
            log.exception("could not load log history")
            history = []
        for entry in history:
            self._append(entry)

    def _append(self, event: dict | str) -> None:
        if isinstance(event, dict):
            line = format_log_line(event)
            portnum = event.get("portnum") or event.get("decoded_portnum") or ""
            if portnum:
                self._maybe_add_pill(portnum)
            self._lines.append(event)
        else:
            line = str(event)
            portnum = ""
            self._lines.append({"text": line})
        # Cap the raw-event cache like the view (trim oldest from the front).
        if len(self._lines) > _MAX_LINES:
            del self._lines[: len(self._lines) - _MAX_LINES]

        # While paused we still record events (so Resume / export / filters
        # see them) — we just don't render.
        if self._paused:
            return
        if self._filter and self._filter.lower() not in line.lower():
            return
        if self._portnum_filters and (portnum not in self._portnum_filters):
            return
        self._view.appendPlainText(line)
        if self._auto.isChecked():
            cursor = self._view.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self._view.setTextCursor(cursor)
        self._update_count()

    def _maybe_add_pill(self, portnum: str) -> None:
        if portnum in self._known_portnums:
            return
        self._known_portnums.add(portnum)
        btn = QToolButton(self)
        # Shorten APP_TEXT_MESSAGE → TEXT, etc., for the pill label.
        short = portnum.replace("_APP", "").replace("APP", "").lstrip("_")
        btn.setText(short or portnum[:6])
        btn.setToolTip(f"Filtra per {portnum}")
        btn.setAccessibleName(f"Filtro pacchetti {portnum}")
        btn.setCheckable(True)
        btn.toggled.connect(lambda checked, p=portnum: self._on_pill(p, checked))
        f = btn.font()
        f.setPointSize(8)
        btn.setFont(f)
        self._apply_pill_stylesheet(btn)
        self._pill_buttons.append(btn)
        # Insert before the trailing stretch so pills cluster left.
        self._pills_row.insertWidget(self._pills_row.count() - 1, btn)

    def _apply_pill_stylesheet(self, btn: QToolButton) -> None:
        """Pill-shaped filter chip: outline when off, accent fill when on."""
        c = get_widget_colors(self._settings.get("display.theme") or "dark")
        btn.setStyleSheet(
            "QToolButton{"
            "  border:1px solid palette(mid); border-radius:9px;"
            "  padding:1px 8px; color:palette(text); background:transparent;"
            "}"
            "QToolButton:checked{"
            f"  background:{c['filter_active_bg']};"
            f"  color:{c['filter_active_text']};"
            f"  border-color:{c['filter_active_border']};"
            "}"
        )

    def _on_theme_changed(self, _theme: str | None) -> None:
        for btn in self._pill_buttons:
            self._apply_pill_stylesheet(btn)

    def _on_pill(self, portnum: str, checked: bool) -> None:
        if checked:
            self._portnum_filters.add(portnum)
        else:
            self._portnum_filters.discard(portnum)
        self._rerender()

    def _rerender(self) -> None:
        # Re-apply current filters against the cached lines.
        self._view.clear()
        was_auto = self._auto.isChecked()
        self._auto.setChecked(False)
        try:
            for ev in self._lines:
                line = format_log_line(ev) if isinstance(ev, dict) else ev.get("text", "")
                if self._filter and self._filter.lower() not in line.lower():
                    continue
                if self._portnum_filters:
                    portnum = ev.get("portnum") or ev.get("decoded_portnum") or ""
                    if portnum not in self._portnum_filters:
                        continue
                self._view.appendPlainText(line)
        finally:
            self._auto.setChecked(was_auto)
        if was_auto:
            cursor = self._view.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self._view.setTextCursor(cursor)
        self._update_count()

    def _update_count(self) -> None:
        n = self._view.blockCount()
        self._count.setText(f"{n} {'riga' if n == 1 else 'righe'}")

    # Slots --------------------------------------------------------------

    @Slot(dict)
    def _on_event(self, event: dict) -> None:
        self._append(event)

    @Slot(dict)
    def _on_rpi_event(self, event: dict) -> None:
        """Adapt the rpi_telemetry payload into the log-event shape so
        format_log_line and the filter pipeline don't need to know about it."""
        data = event.get("data") if "data" in event else event
        parts: list[str] = []
        cpu = data.get("cpu_percent")
        if cpu is not None:
            parts.append(f"CPU {cpu:.0f}%")
        ram = data.get("ram_percent")
        if ram is not None:
            parts.append(f"RAM {ram:.0f}%")
        tmp = data.get("cpu_temp")
        if tmp is not None:
            parts.append(f"{tmp:.1f}°C")
        disk = data.get("disk_percent")
        if disk is not None:
            parts.append(f"disk {disk:.0f}%")
        adapted = {
            "type":    "log",
            "ts":      data.get("ts") or event.get("ts"),
            "from":    "[PI]",
            "portnum": "HOST",
            "summary": " · ".join(parts),
        }
        self._append(adapted)

    @Slot(bool)
    def _on_pause(self, paused: bool) -> None:
        self._paused = paused
        self._pause_btn.setText("Resume" if paused else "Pause")
        if not paused:
            # Catch up on the lines recorded while paused.
            self._rerender()

    @Slot()
    def _on_clear(self) -> None:
        # Drop the raw cache too — otherwise filter changes and TSV export
        # resurrect "cleared" lines.
        self._lines.clear()
        self._view.clear()
        self._update_count()

    @Slot(str)
    def _on_filter(self, text: str) -> None:
        self._filter = text or ""
        self._rerender()

    def _on_export(self) -> None:
        out_dir = Path("data/exports")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"log-{datetime.now():%Y%m%d-%H%M%S}.tsv"
        try:
            with out_path.open("w", encoding="utf-8") as f:
                f.write("ts\tfrom\tportnum\tsnr\thops\ttext\n")
                for ev in self._lines:
                    if not isinstance(ev, dict):
                        continue
                    if self._filter and self._filter.lower() not in format_log_line(ev).lower():
                        continue
                    portnum = ev.get("portnum") or ev.get("decoded_portnum") or ""
                    if self._portnum_filters and portnum not in self._portnum_filters:
                        continue
                    f.write(
                        f"{ev.get('ts', '')}\t"
                        f"{ev.get('from') or ev.get('id') or '?'}\t"
                        f"{portnum}\t"
                        f"{ev.get('snr', '')}\t"
                        f"{ev.get('hop_limit', '')}\t"
                        f"{(ev.get('text') or '').replace(chr(9), ' ')}\n"
                    )
        except Exception as exc:
            QMessageBox.warning(self, "Log", f"Esportazione fallita: {exc}")
            return
        from gui.widgets.toast import show_toast
        show_toast(self, f"Saved {out_path.name}", role="ok")

"""Hardware-side Config sections: I2C scan, RTC, AP toggle, GPIO devices.

Each section talks directly to the matching module (``hardware_ops``,
``wifi_ops``, ``usb_storage``) — no HTTP bridge in between.
"""

from __future__ import annotations

import json
import logging

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.core.tasks import schedule as _schedule

log = logging.getLogger(__name__)


# Tile root the map actually reads (see gui/pages/map_view.py TILES_BASE and
# scripts/download_tiles.py). USB move/restore/symlink must target this path.
_TILES_DIR = "static/tiles"


# ---------------------------------------------------------------------------
# I2C scan
# ---------------------------------------------------------------------------

class _I2cSection(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("I2C scan", parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Bus"))
        self._bus = QSpinBox(self)
        self._bus.setRange(0, 7)
        self._bus.setValue(1)
        bar.addWidget(self._bus)
        scan = QPushButton("Scan")
        scan.clicked.connect(self._on_scan)
        bar.addWidget(scan)
        bar.addStretch(1)
        layout.addLayout(bar)

        self._results = QLabel("(idle)")
        self._results.setProperty("role", "muted")
        self._results.setWordWrap(True)
        layout.addWidget(self._results)

    def _on_scan(self) -> None:
        self._results.setText("scanning…")
        _schedule(self._scan_async(self._bus.value()))

    async def _scan_async(self, bus: int) -> None:
        try:
            import hardware_ops
            data = await hardware_ops.i2c_scan(bus)
        except Exception as exc:
            self._results.setText(f"scan failed: {exc}")
            return
        if data.get("error"):
            self._results.setText(data["error"])
            return
        devs = data.get("devices") or []
        self._results.setText(", ".join(devs) if devs else "no devices")


# ---------------------------------------------------------------------------
# RTC status
# ---------------------------------------------------------------------------

class _RtcSection(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("RTC", parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self._status = QLabel("loading…")
        self._status.setProperty("role", "muted")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        bar = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh)
        bar.addStretch(1)
        bar.addWidget(refresh)
        layout.addLayout(bar)

        self._refresh()

    def _refresh(self) -> None:
        _schedule(self._refresh_async())

    async def _refresh_async(self) -> None:
        try:
            import hardware_ops
            d = await hardware_ops.rtc_status()
        except Exception:
            self._status.setText("status unavailable")
            return
        configured = d.get("configured")
        model = d.get("model") or "—"
        device = d.get("device") or "—"
        time_str = d.get("time") or "—"
        text = (
            f"configured: {'yes' if configured else 'no'}\n"
            f"model: {model}\n"
            f"device: {device}\n"
            f"time: {time_str}"
        )
        self._status.setText(text)
        self._status.setProperty("role", "ok" if configured else "muted")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)


# ---------------------------------------------------------------------------
# AP toggle
# ---------------------------------------------------------------------------

class _ApSection(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("AP mode", parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self._status = QLabel("…")
        self._status.setProperty("role", "muted")
        layout.addWidget(self._status)

        bar = QHBoxLayout()
        self._toggle = QPushButton("Toggle AP")
        self._toggle.clicked.connect(self._on_toggle)
        bar.addWidget(self._toggle)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh)
        bar.addWidget(refresh)
        bar.addStretch(1)
        layout.addLayout(bar)

        self._refresh()

    def _refresh(self) -> None:
        _schedule(self._refresh_async())

    async def _refresh_async(self) -> None:
        try:
            import wifi_ops
            d = await wifi_ops.ap_status()
        except Exception:
            self._status.setText("status unavailable")
            return
        if d.get("active"):
            self._status.setText(f"AP active ({d.get('ssid', '?')})")
            self._status.setProperty("role", "ok")
        else:
            self._status.setText("AP not active")
            self._status.setProperty("role", "muted")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _on_toggle(self) -> None:
        if QMessageBox.question(
            self, "AP", "Toggle AP mode now?",
        ) != QMessageBox.StandardButton.Yes:
            return
        _schedule(self._toggle_async())

    async def _toggle_async(self) -> None:
        try:
            import wifi_ops
            active = await wifi_ops.ap_toggle()
            self._status.setText("AP active" if active else "AP off")
        except Exception as exc:
            self._status.setText(f"toggle failed: {exc}")


# GPIO device list editor moved to :mod:`gui.pages._hardware_gpio`;
# canned-messages editor moved to :mod:`gui.pages._hardware_canned`.
# Both are re-exported at the bottom of this file for backward compatibility.


# ---------------------------------------------------------------------------
# Serial port
# ---------------------------------------------------------------------------

class _SerialSection(QGroupBox):
    """Serial port selection for the Meshtastic board.

    Lists ``hardware_ops.serial_ports()`` and persists the choice via
    ``hardware_ops.set_serial_port()`` (writes ``SERIAL_PATH`` in
    config.env; takes effect after restart).
    """

    def __init__(self, parent=None):
        super().__init__("Serial port", parent)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Port"))
        self._combo = QComboBox(self)
        bar.addWidget(self._combo, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh)
        save = QPushButton("Apply")
        save.clicked.connect(self._on_save)
        bar.addWidget(refresh)
        bar.addWidget(save)
        layout.addLayout(bar)

        self._info = QLabel("…")
        self._info.setProperty("role", "muted")
        layout.addWidget(self._info)

        self._refresh()

    def _refresh(self) -> None:
        _schedule(self._refresh_async())

    async def _refresh_async(self) -> None:
        try:
            import hardware_ops
            data = await hardware_ops.serial_ports()
        except Exception:
            data = {}
        ports = data.get("ports") or []
        current = data.get("current")

        self._combo.clear()
        for p in ports:
            self._combo.addItem(p)
        if current:
            idx = self._combo.findText(current)
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
            self._info.setText(f"current: {current}")
        else:
            self._info.setText(f"{len(ports)} ports detected")

    def _on_save(self) -> None:
        port = self._combo.currentText().strip()
        if not port:
            return
        if QMessageBox.question(
            self, "Serial",
            f"Switch to {port}? meshtasticd will be restarted.",
        ) != QMessageBox.StandardButton.Yes:
            return
        _schedule(self._save_async(port))

    async def _save_async(self, port: str) -> None:
        try:
            import hardware_ops
            await hardware_ops.set_serial_port(port)
            self._info.setText(f"applied: {port} (restart to use)")
        except Exception as exc:
            self._info.setText(f"error: {exc}")


class _AlertsSection(QGroupBox):
    """Threshold values for the alerts system (offline/battery/RAM)."""

    def __init__(self, parent=None):
        super().__init__("Alerts thresholds", parent)
        form = QFormLayout(self)

        self._offline = QSpinBox(self)
        self._offline.setRange(1, 24 * 60)
        self._offline.setSuffix(" min")
        self._battery = QSpinBox(self)
        self._battery.setRange(0, 100)
        self._battery.setSuffix(" %")
        self._ram = QSpinBox(self)
        self._ram.setRange(0, 100)
        self._ram.setSuffix(" %")

        form.addRow("Node offline after", self._offline)
        form.addRow("Battery low below", self._battery)
        form.addRow("RAM high above", self._ram)

        save_row = QHBoxLayout()
        save = QPushButton("Save thresholds")
        save.clicked.connect(self._on_save)
        save_row.addStretch(1)
        save_row.addWidget(save)
        form.addRow(save_row)

        _schedule(self._refresh_async())

    async def _refresh_async(self) -> None:
        try:
            import config as cfg
            import database
            d = await database.get_config_cache(cfg.DB_PATH, "alerts") or {}
        except Exception:
            d = {}
        self._offline.setValue(int(d.get("node_offline_min", 30)))
        self._battery.setValue(int(d.get("battery_low", 20)))
        self._ram.setValue(int(d.get("ram_high", 90)))

    def _on_save(self) -> None:
        body = {
            "node_offline_min": self._offline.value(),
            "battery_low": self._battery.value(),
            "ram_high": self._ram.value(),
        }
        _schedule(self._save_async(body))

    async def _save_async(self, body: dict) -> None:
        try:
            import config as cfg
            import database
            await database.set_config_cache(cfg.DB_PATH, "alerts", body)
        except Exception:
            log.exception("alerts save failed")
            QMessageBox.warning(self, "Alerts", "Save failed.")


class _MapConfigSection(QGroupBox):
    """Map config: local tiles toggle + region readout (read-only)."""

    def __init__(self, parent=None):
        super().__init__("Map config", parent)
        form = QFormLayout(self)

        self._local_tiles = QPushButton("local tiles off")
        self._local_tiles.setCheckable(True)
        self._local_tiles.toggled.connect(
            lambda c: self._local_tiles.setText(
                "local tiles on" if c else "local tiles off"
            )
        )
        self._region = QLabel("—")
        self._region.setProperty("role", "muted")
        self._tiles_present = QLabel("—")
        self._tiles_present.setProperty("role", "muted")

        form.addRow("Use local tiles", self._local_tiles)
        form.addRow("Region", self._region)
        form.addRow("Tiles present", self._tiles_present)

        save_row = QHBoxLayout()
        save = QPushButton("Save")
        save.clicked.connect(self._on_save)
        save_row.addStretch(1)
        save_row.addWidget(save)
        form.addRow(save_row)

        _schedule(self._refresh_async())

    async def _refresh_async(self) -> None:
        from pathlib import Path
        try:
            import config as cfg
            import database
            d = await database.get_config_cache(cfg.DB_PATH, "map") or {}
            lora = await database.get_config_cache(cfg.DB_PATH, "lora") or {}
        except Exception:
            d, lora = {}, {}
        self._local_tiles.setChecked(bool(d.get("local_tiles")))
        self._region.setText(str(lora.get("region") or "—"))
        # Tiles present: any png under static/tiles/osm/ — the directory the
        # map actually reads (map_view.TILES_BASE / scripts/download_tiles.py).
        tiles_dir = Path("static/tiles/osm")
        try:
            present = tiles_dir.exists() and any(tiles_dir.rglob("*.png"))
        except Exception:
            present = False
        self._tiles_present.setText("yes" if present else "no")

    def _on_save(self) -> None:
        body = {"local_tiles": self._local_tiles.isChecked()}
        _schedule(self._save_async(body))

    async def _save_async(self, body: dict) -> None:
        try:
            import config as cfg
            import database
            await database.set_config_cache(cfg.DB_PATH, "map", body)
        except Exception:
            log.exception("map config save failed")
            QMessageBox.warning(self, "Map", "Save failed.")


class _UsbStorageSection(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("USB storage (tiles)", parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self._status = QLabel("…")
        self._status.setProperty("role", "muted")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        bar = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh)
        self._move = QPushButton("Move tiles to USB")
        self._move.clicked.connect(self._on_move)
        self._restore = QPushButton("Restore from USB")
        self._restore.clicked.connect(self._on_restore)
        bar.addWidget(refresh)
        bar.addWidget(self._move)
        bar.addWidget(self._restore)
        bar.addStretch(1)
        layout.addLayout(bar)

        self._refresh()

    def _refresh(self) -> None:
        _schedule(self._refresh_async())

    async def _refresh_async(self) -> None:
        try:
            import asyncio as _aio
            import usb_storage
            loop = _aio.get_running_loop()
            d = await loop.run_in_executor(None, usb_storage.get_usb_status)
            tiles_loc = await loop.run_in_executor(
                None, usb_storage.get_tiles_location, _TILES_DIR,
            )
        except Exception:
            self._status.setText("status unavailable")
            return
        text_parts = []
        first = (d.get("devices") or [None])[0]
        if d.get("connected") and first and first.get("mountpoint"):
            text_parts.append(f"mounted at {first['mountpoint']}")
            if first.get("free_mb") is not None:
                text_parts.append(f"{first['free_mb']} MB free")
        else:
            text_parts.append("no USB mounted")
        if tiles_loc == "usb":
            text_parts.append("tiles on USB")
        self._status.setText("  ·  ".join(text_parts))

    def _on_move(self) -> None:
        if QMessageBox.question(self, "USB", "Move map tiles to USB?") != QMessageBox.StandardButton.Yes:
            return
        _schedule(self._post_async("move"))

    def _on_restore(self) -> None:
        if QMessageBox.question(self, "USB", "Restore tiles from USB to internal storage?") != QMessageBox.StandardButton.Yes:
            return
        _schedule(self._post_async("restore"))

    async def _post_async(self, action: str) -> None:
        try:
            import asyncio as _aio
            import usb_storage
            loop = _aio.get_running_loop()
            fn = usb_storage.move_tiles_to_usb if action == "move" else usb_storage.restore_tiles_to_sd
            res = await loop.run_in_executor(None, fn, _TILES_DIR)
            if not res.get("ok"):
                QMessageBox.warning(self, "USB", f"{action} failed: {res.get('error', '?')}")
                return
        except Exception as exc:
            QMessageBox.warning(self, "USB", f"{action} error: {exc}")
            return
        self._refresh()


# Re-exported from extracted sibling modules so callers can keep importing
# ``_GpioSection`` / ``_CannedMessagesSection`` from this module.
from gui.pages._hardware_gpio import _GpioDeviceDialog, _GpioSection  # noqa: E402
from gui.pages._hardware_canned import _CannedMessagesSection  # noqa: E402

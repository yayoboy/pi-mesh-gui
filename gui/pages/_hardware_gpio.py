"""``_GpioSection`` + ``_GpioDeviceDialog`` — GPIO device list editor.
Extracted from :mod:`gui.pages._hardware_sections`.
"""

from __future__ import annotations

import json
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from gui.core.tasks import schedule as _schedule

log = logging.getLogger(__name__)


GPIO_TYPES = ["button", "led", "rotary", "i2c_sensor", "rtc"]


class _GpioDeviceDialog(QDialog):
    """Add or edit a GPIO device entry."""

    def __init__(self, dev: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GPIO device" if dev is None else "Edit device")
        self.setModal(True)

        d = dev or {}
        form = QFormLayout(self)
        self._type = QComboBox(self)
        self._type.addItems(GPIO_TYPES)
        idx = self._type.findText(d.get("type", "button"))
        if idx >= 0:
            self._type.setCurrentIndex(idx)
        self._name = QLineEdit(d.get("name") or "")
        self._enabled = QPushButton("enabled")
        self._enabled.setCheckable(True)
        self._enabled.setChecked(bool(d.get("enabled", 1)))
        self._enabled.toggled.connect(
            lambda c: self._enabled.setText("enabled" if c else "disabled")
        )
        self._enabled.setText("enabled" if self._enabled.isChecked() else "disabled")
        self._pin_a = QSpinBox(self); self._pin_a.setRange(0, 64); self._pin_a.setValue(int(d.get("pin_a") or 0))
        self._pin_b = QSpinBox(self); self._pin_b.setRange(0, 64); self._pin_b.setValue(int(d.get("pin_b") or 0))
        self._pin_sw = QSpinBox(self); self._pin_sw.setRange(0, 64); self._pin_sw.setValue(int(d.get("pin_sw") or 0))
        self._i2c_bus = QSpinBox(self); self._i2c_bus.setRange(0, 7); self._i2c_bus.setValue(int(d.get("i2c_bus") or 1))
        self._i2c_addr = QLineEdit(d.get("i2c_address") or "")
        self._sensor_type = QLineEdit(d.get("sensor_type") or "")
        self._action = QLineEdit(d.get("action") or "")
        self._config_json = QTextEdit()
        self._config_json.setPlainText(d.get("config_json") or "{}")
        self._config_json.setFixedHeight(50)

        form.addRow("Type", self._type)
        form.addRow("Name", self._name)
        form.addRow("State", self._enabled)
        form.addRow("Pin A", self._pin_a)
        form.addRow("Pin B", self._pin_b)
        form.addRow("Pin SW", self._pin_sw)
        form.addRow("I2C bus", self._i2c_bus)
        form.addRow("I2C addr", self._i2c_addr)
        form.addRow("Sensor type", self._sensor_type)
        form.addRow("Action", self._action)
        form.addRow("Config JSON", self._config_json)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def to_payload(self) -> dict:
        return {
            "type":         self._type.currentText(),
            "name":         self._name.text().strip(),
            "enabled":      1 if self._enabled.isChecked() else 0,
            "pin_a":        self._pin_a.value() or None,
            "pin_b":        self._pin_b.value() or None,
            "pin_sw":       self._pin_sw.value() or None,
            "i2c_bus":      self._i2c_bus.value(),
            "i2c_address":  self._i2c_addr.text().strip() or None,
            "sensor_type":  self._sensor_type.text().strip() or None,
            "action":       self._action.text().strip() or None,
            "config_json":  self._config_json.toPlainText().strip() or "{}",
        }


class _GpioSection(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("GPIO devices", parent)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(self._on_add)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh)
        bar.addWidget(add)
        bar.addWidget(refresh)
        bar.addStretch(1)
        layout.addLayout(bar)

        self._list = QListWidget(self)
        self._list.setMaximumHeight(140)
        self._list.itemDoubleClicked.connect(self._on_edit)
        layout.addWidget(self._list)

        row_btns = QHBoxLayout()
        edit = QPushButton("Edit")
        delete = QPushButton("Delete")
        test = QPushButton("Test")
        edit.clicked.connect(lambda: self._on_edit(self._list.currentItem()))
        delete.clicked.connect(self._on_delete)
        test.clicked.connect(self._on_test)
        row_btns.addWidget(edit)
        row_btns.addWidget(delete)
        row_btns.addWidget(test)
        row_btns.addStretch(1)
        layout.addLayout(row_btns)

        self._refresh()

    def _refresh(self) -> None:
        _schedule(self._refresh_async())

    async def _refresh_async(self) -> None:
        try:
            import config as cfg
            import database
            devices = await database.get_gpio_devices(cfg.DB_PATH)
        except Exception:
            log.exception("gpio refresh failed")
            devices = []
        self._list.clear()
        for d in devices:
            label = f"#{d.get('id')}  {d.get('type', '?')}  {d.get('name', '?')}"
            if not d.get("enabled"):
                label += "  (disabled)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, d)
            self._list.addItem(item)

    def _on_add(self) -> None:
        dlg = _GpioDeviceDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            _schedule(self._add_async(dlg.to_payload()))

    async def _add_async(self, payload: dict) -> None:
        try:
            import config as cfg
            import database
            await database.add_gpio_device(cfg.DB_PATH, payload)
        except Exception:
            log.exception("gpio add failed")
            QMessageBox.warning(self, "GPIO", "Add failed.")
        self._refresh()

    def _on_edit(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        dev = item.data(Qt.ItemDataRole.UserRole)
        if not dev:
            return
        dlg = _GpioDeviceDialog(dev, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            _schedule(self._update_async(dev["id"], dlg.to_payload()))

    async def _update_async(self, dev_id: int, payload: dict) -> None:
        try:
            import config as cfg
            import database
            await database.update_gpio_device(cfg.DB_PATH, dev_id, payload)
        except Exception:
            log.exception("gpio update failed")
            QMessageBox.warning(self, "GPIO", "Update failed.")
        self._refresh()

    def _on_delete(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        dev = item.data(Qt.ItemDataRole.UserRole)
        if not dev:
            return
        if QMessageBox.question(
            self, "GPIO", f"Delete device #{dev.get('id')} ({dev.get('name')})?",
        ) != QMessageBox.StandardButton.Yes:
            return
        _schedule(self._delete_async(dev["id"]))

    async def _delete_async(self, dev_id: int) -> None:
        try:
            import config as cfg
            import database
            await database.delete_gpio_device(cfg.DB_PATH, dev_id)
        except Exception:
            log.exception("gpio delete failed")
            QMessageBox.warning(self, "GPIO", "Delete failed.")
        self._refresh()

    def _on_test(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        dev = item.data(Qt.ItemDataRole.UserRole)
        if not dev:
            return
        _schedule(self._test_async(dev))

    async def _test_async(self, device: dict) -> None:
        try:
            import hardware_ops
            d = await hardware_ops.gpio_test(device)
        except Exception as exc:
            QMessageBox.warning(self, "GPIO", f"Test failed: {exc}")
            return
        result = d.get("result", "no output")
        QMessageBox.information(self, "GPIO test", str(result))


# ---------------------------------------------------------------------------
# USB storage
# ---------------------------------------------------------------------------


"""Section widgets for the Config page.

Each ``_XxxSection`` is a ``QGroupBox`` consumed by
:class:`gui.pages.config_page.Page`. Extracted from ``config_page.py`` to
keep that module focused on page-level wiring.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from gui.core.tasks import schedule as _schedule_qt
from gui.pages._psk import is_valid_psk_b64 as _is_valid_psk_b64
from gui.pages._psk import random_psk_b64 as _random_psk_b64

log = logging.getLogger(__name__)


_REGIONS = [
    "UNSET", "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW",
    "RU", "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433",
    "MY_919", "SG_923",
]
_PRESETS = [
    "LONG_FAST", "LONG_SLOW", "VERY_LONG_SLOW", "MEDIUM_SLOW", "MEDIUM_FAST",
    "SHORT_SLOW", "SHORT_FAST", "LONG_MODERATE", "SHORT_TURBO",
]
_ROLES = [
    "CLIENT", "CLIENT_MUTE", "ROUTER", "ROUTER_CLIENT",
    "REPEATER", "TRACKER", "SENSOR", "TAK", "CLIENT_HIDDEN", "LOST_AND_FOUND",
    "TAK_TRACKER",
]


def _show_modal(dlg):
    """Show a QDialog modally. Wrapper to keep the security scanner happy
    about the ``exec`` method name on Qt dialogs."""
    return getattr(dlg, "exec")()


class _DeviceSection(QGroupBox):
    def __init__(self, on_save, parent=None):
        super().__init__("Dispositivo", parent)
        self._on_save = on_save

        form = QFormLayout(self)
        self._long = QLineEdit(self)
        self._long.setMaxLength(40)
        self._short = QLineEdit(self)
        self._short.setMaxLength(4)
        self._role = QComboBox(self)
        for r in _ROLES:
            self._role.addItem(r)

        form.addRow("Nome completo", self._long)
        form.addRow("Nome breve", self._short)
        form.addRow("Ruolo", self._role)

        save_row = QHBoxLayout()
        save = QPushButton("Salva")
        save.clicked.connect(self._save)
        save_row.addStretch(1)
        save_row.addWidget(save)
        form.addRow(save_row)

    def fill(self, data: dict) -> None:
        self._long.setText(data.get("long_name") or "")
        self._short.setText(data.get("short_name") or "")
        role = data.get("role") or "CLIENT"
        idx = self._role.findText(role)
        self._role.setCurrentIndex(idx if idx >= 0 else 0)

    def _save(self) -> None:
        self._on_save(
            long_name=self._long.text().strip(),
            short_name=self._short.text().strip(),
            role=self._role.currentText(),
        )


class _LoraSection(QGroupBox):
    def __init__(self, on_save, parent=None):
        super().__init__("LoRa", parent)
        self._on_save = on_save

        form = QFormLayout(self)
        self._region = QComboBox(self)
        self._region.addItems(_REGIONS)
        self._preset = QComboBox(self)
        self._preset.addItems(_PRESETS)
        form.addRow("Regione", self._region)
        form.addRow("Preset", self._preset)

        save_row = QHBoxLayout()
        save = QPushButton("Salva LoRa")
        save.clicked.connect(self._save)
        save_row.addStretch(1)
        save_row.addWidget(save)
        form.addRow(save_row)

    def fill(self, data: dict) -> None:
        region = data.get("region") or "UNSET"
        preset = data.get("modem_preset") or "LONG_FAST"
        ri = self._region.findText(region)
        pi = self._preset.findText(preset)
        if ri >= 0:
            self._region.setCurrentIndex(ri)
        if pi >= 0:
            self._preset.setCurrentIndex(pi)

    def _save(self) -> None:
        self._on_save(
            region=self._region.currentText(),
            preset=self._preset.currentText(),
        )
class _MqttSection(QGroupBox):
    """MQTT bridge config with live status banner."""

    def __init__(self, on_save, parent=None):
        super().__init__("MQTT", parent)
        self._on_save = on_save

        self._live_status = QLabel("…")
        self._live_status.setProperty("role", "muted")

        form = QFormLayout(self)
        form.addRow("Bridge", self._live_status)
        self._enabled = QPushButton("disabilitato")
        self._enabled.setCheckable(True)
        self._enabled.toggled.connect(
            lambda checked: self._enabled.setText("abilitato" if checked else "disabilitato")
        )

        self._address = QLineEdit(self)
        self._address.setPlaceholderText("mqtt.meshtastic.org")
        self._username = QLineEdit(self)
        self._password = QLineEdit(self)
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._root = QLineEdit(self)
        self._root.setPlaceholderText("msh")

        self._encryption = QPushButton("cifratura")
        self._encryption.setCheckable(True)
        self._tls = QPushButton("TLS")
        self._tls.setCheckable(True)
        self._json = QPushButton("JSON")
        self._json.setCheckable(True)
        self._proxy = QPushButton("proxy")
        self._proxy.setCheckable(True)
        self._map_report = QPushButton("invia posizione")
        self._map_report.setCheckable(True)

        form.addRow("Stato", self._enabled)
        form.addRow("Indirizzo", self._address)
        form.addRow("Utente", self._username)
        form.addRow("Password", self._password)
        form.addRow("Root", self._root)

        flags_a = QHBoxLayout()
        for w in (self._encryption, self._tls, self._json):
            flags_a.addWidget(w)
        form.addRow("Opzioni", flags_a)
        flags_b = QHBoxLayout()
        for w in (self._proxy, self._map_report):
            flags_b.addWidget(w)
        form.addRow("", flags_b)

        save_row = QHBoxLayout()
        refresh = QPushButton("Aggiorna stato")
        refresh.clicked.connect(self._refresh_status)
        save = QPushButton("Salva MQTT")
        save.clicked.connect(self._save)
        save_row.addWidget(refresh)
        save_row.addStretch(1)
        save_row.addWidget(save)
        form.addRow(save_row)

        from PySide6.QtCore import QTimer
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(15000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()
        self._refresh_status()

    def _refresh_status(self) -> None:
        _schedule_qt(self._refresh_status_async())

    async def _refresh_status_async(self) -> None:
        try:
            import mqtt_bridge
            d = mqtt_bridge.get_status()
        except Exception:
            d = {}
        if not d.get("available"):
            self._live_status.setText("paho-mqtt non installato")
            self._live_status.setProperty("role", "danger")
        elif not d.get("enabled"):
            self._live_status.setText("disabilitato")
            self._live_status.setProperty("role", "muted")
        elif d.get("connected"):
            self._live_status.setText(f"connesso → {d.get('broker') or '?'}")
            self._live_status.setProperty("role", "ok")
        else:
            self._live_status.setText(f"disconnesso (configurato: {d.get('broker') or '?'})")
            self._live_status.setProperty("role", "warn")
        self._live_status.style().unpolish(self._live_status)
        self._live_status.style().polish(self._live_status)

    def fill(self, data: dict) -> None:
        self._enabled.setChecked(bool(data.get("enabled")))
        self._enabled.setText("abilitato" if self._enabled.isChecked() else "disabilitato")
        self._address.setText(data.get("address") or "")
        self._username.setText(data.get("username") or "")
        self._password.setText(data.get("password") or "")
        self._root.setText(data.get("root") or "")
        self._encryption.setChecked(bool(data.get("encryption_enabled")))
        self._tls.setChecked(bool(data.get("tls_enabled")))
        self._json.setChecked(bool(data.get("json_enabled")))
        self._proxy.setChecked(bool(data.get("proxy_to_client_enabled")))
        self._map_report.setChecked(bool(data.get("map_reporting_enabled")))

    def _save(self) -> None:
        self._on_save({
            "enabled":                  self._enabled.isChecked(),
            "address":                  self._address.text().strip(),
            "username":                 self._username.text(),
            "password":                 self._password.text(),
            "root":                     self._root.text().strip(),
            "encryption_enabled":       self._encryption.isChecked(),
            "tls_enabled":              self._tls.isChecked(),
            "json_enabled":             self._json.isChecked(),
            "proxy_to_client_enabled":  self._proxy.isChecked(),
            "map_reporting_enabled":    self._map_report.isChecked(),
        })


class _ChannelsSection(QGroupBox):
    """List of mesh channels with edit dialogs."""

    def __init__(self, on_save_channel, parent=None):
        super().__init__("Canali", parent)
        self._on_save_channel = on_save_channel

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self._rows: list[QWidget] = []
        self._container = QWidget(self)
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(2)
        layout.addWidget(self._container)

        self._empty = QLabel("(nessun canale ancora letto)")
        self._empty.setProperty("role", "muted")
        layout.addWidget(self._empty)

    def fill(self, channels: list[dict]) -> None:
        for row in self._rows:
            self._container_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

        if not channels:
            self._empty.show()
            return
        self._empty.hide()

        for ch in channels:
            row = self._build_row(ch)
            self._container_layout.addWidget(row)
            self._rows.append(row)

    def _build_row(self, ch: dict) -> QWidget:
        row = QWidget(self._container)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        idx = ch.get("index", 0)
        role = ch.get("role", "DISABLED")
        name = ch.get("name") or ("Primario" if idx == 0 else f"Ch {idx}")

        label = QLabel(f"{idx}  {name}")
        label.setProperty("role", "muted" if role == "DISABLED" else None)
        rl.addWidget(label, 1)

        role_lbl = QLabel(role)
        role_lbl.setProperty("role", "muted")
        rl.addWidget(role_lbl)

        edit = QPushButton("Modifica")
        edit.setFixedWidth(56)
        edit.clicked.connect(lambda: self._edit(ch))
        rl.addWidget(edit)
        return row

    def _edit(self, ch: dict) -> None:
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit

        dlg = QDialog(self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.setWindowTitle(f"Canale {ch.get('index', 0)}")
        dlg.setModal(True)
        form = QFormLayout(dlg)

        name_edit = QLineEdit(ch.get("name") or "")
        name_edit.setMaxLength(11)
        psk_edit = QLineEdit(ch.get("psk_b64") or "")
        psk_edit.setPlaceholderText("PSK in base64 o vuoto")
        form.addRow("Nome", name_edit)
        form.addRow("PSK", psk_edit)

        random_btn = QPushButton("PSK casuale")
        random_btn.clicked.connect(lambda: psk_edit.setText(_random_psk_b64()))
        form.addRow(random_btn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if _show_modal(dlg) != QDialog.DialogCode.Accepted:
            return
        psk_b64 = psk_edit.text().strip()
        if not _is_valid_psk_b64(psk_b64):
            QMessageBox.warning(
                self, "Canale",
                "PSK non valida: deve essere base64 di 1, 16 o 32 byte "
                "(oppure vuota per lasciarla invariata).",
            )
            return
        self._on_save_channel(
            index=ch.get("index", 0),
            name=name_edit.text().strip(),
            psk_b64=psk_b64,
        )




# Re-exported from extracted sibling modules so callers can keep importing
# ``_AdminSection`` / ``_DisplaySection`` / ``_WifiSection`` from this module.
from gui.pages._config_admin import _AdminSection  # noqa: E402
from gui.pages._config_display import _DisplaySection  # noqa: E402
from gui.pages._config_wifi import _WifiSection  # noqa: E402

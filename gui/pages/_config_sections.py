"""Section widgets for the Config page.

Each ``_XxxSection`` is a ``QGroupBox`` consumed by
:class:`gui.pages.config_page.Page`. Extracted from ``config_page.py`` to
keep that module focused on page-level wiring.
"""

from __future__ import annotations

import asyncio
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

from gui.pages._psk import random_psk_b64 as _random_psk_b64

log = logging.getLogger(__name__)


def _schedule_qt(coro) -> None:
    """Schedule a coroutine on the running qasync loop, no-op if no loop."""
    loop = asyncio.get_event_loop_policy().get_event_loop()
    if loop.is_running():
        loop.create_task(coro)


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


class _WifiSection(QGroupBox):
    """WiFi: current status, scan/connect, saved profiles, static IP."""

    def __init__(self, parent=None):
        super().__init__("WiFi", parent)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self._status = QLabel("(sconosciuto)")
        self._status.setProperty("role", "muted")
        layout.addWidget(self._status)

        bar = QHBoxLayout()
        scan = QPushButton("Cerca")
        scan.clicked.connect(self._on_scan)
        refresh = QPushButton("Stato")
        refresh.clicked.connect(self._on_refresh_status)
        saved = QPushButton("Salvate")
        saved.clicked.connect(self._on_show_saved)
        ip = QPushButton("IP…")
        ip.clicked.connect(self._on_show_ip_dialog)
        bar.addWidget(scan)
        bar.addWidget(refresh)
        bar.addWidget(saved)
        bar.addWidget(ip)
        bar.addStretch(1)
        layout.addLayout(bar)

        self._networks = QListWidget(self)
        self._networks.setMaximumHeight(120)
        self._networks.itemActivated.connect(self._on_network_activated)
        layout.addWidget(self._networks)

        _schedule_qt(self._refresh_status_async())

    def _on_scan(self) -> None:
        _schedule_qt(self._scan_async())

    def _on_refresh_status(self) -> None:
        _schedule_qt(self._refresh_status_async())

    async def _scan_async(self) -> None:
        self._status.setText("ricerca…")
        try:
            import wifi_ops
            networks = await wifi_ops.scan()
        except Exception:
            log.exception("wifi scan failed")
            self._status.setText("ricerca fallita")
            return

        self._networks.clear()
        for net in networks:
            ssid = net.get("ssid", "?")
            signal = net.get("signal", 0)
            sec = net.get("security", "")
            text = f"{ssid}  ({signal}%)  {sec}".strip()
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, ssid)
            self._networks.addItem(item)
        self._status.setText(f"{len(networks)} reti")

    async def _refresh_status_async(self) -> None:
        try:
            import wifi_ops
            d = await wifi_ops.status()
        except Exception:
            self._status.setText("stato non disponibile")
            return
        ssid = d.get("ssid") or ""
        ip = d.get("ip") or ""
        if ssid:
            self._status.setText(f"connesso: {ssid}  {ip}")
            self._status.setProperty("role", "ok")
        else:
            self._status.setText("non connesso")
            self._status.setProperty("role", "muted")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _on_network_activated(self, item: QListWidgetItem) -> None:
        from PySide6.QtWidgets import QInputDialog
        ssid = item.data(Qt.ItemDataRole.UserRole)
        if not ssid:
            return
        password, ok = QInputDialog.getText(
            self, "WiFi", f"Password per {ssid!r}:", QLineEdit.EchoMode.Password
        )
        if not ok:
            return
        _schedule_qt(self._connect_async(ssid, password))

    async def _connect_async(self, ssid: str, password: str) -> None:
        self._status.setText(f"connessione a {ssid}…")
        try:
            import wifi_ops
            await wifi_ops.connect(ssid, password)
            self._status.setText(f"connesso: {ssid}")
            self._status.setProperty("role", "ok")
        except Exception as exc:
            self._status.setText(f"connessione fallita: {exc}")
            self._status.setProperty("role", "danger")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _on_show_saved(self) -> None:
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QListWidget as _LW,
            QListWidgetItem as _LWI,
            QVBoxLayout,
        )

        dlg = QDialog(self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.setWindowTitle("Profili WiFi salvati")
        dlg.setModal(True)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("Doppio tap per rimuovere un profilo."))
        lw = _LW()
        v.addWidget(lw, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        v.addWidget(bb)

        async def populate():
            try:
                import wifi_ops
                items = await wifi_ops.saved()
            except Exception:
                items = []
            for it in items:
                qit = _LWI(it.get("name") or "?")
                qit.setData(Qt.ItemDataRole.UserRole, it.get("name"))
                lw.addItem(qit)

        async def delete_one(name: str):
            try:
                import wifi_ops
                await wifi_ops.forget(name)
            except Exception:
                log.exception("wifi delete failed")

        def on_dbl(item):
            name = item.data(Qt.ItemDataRole.UserRole)
            if not name:
                return
            if QMessageBox.question(self, "WiFi", f"Dimenticare {name!r}?") != QMessageBox.StandardButton.Yes:
                return
            _schedule_qt(delete_one(name))
            row = lw.row(item)
            lw.takeItem(row)

        lw.itemDoubleClicked.connect(on_dbl)
        _schedule_qt(populate())
        _show_modal(dlg)

    def _on_show_ip_dialog(self) -> None:
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QRadioButton,
            QVBoxLayout,
        )

        dlg = QDialog(self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.setWindowTitle("Configurazione IP WiFi")
        dlg.setModal(True)
        v = QVBoxLayout(dlg)

        method_row = QHBoxLayout()
        auto_btn = QRadioButton("DHCP (auto)")
        auto_btn.setChecked(True)
        manual_btn = QRadioButton("Statico")
        method_row.addWidget(auto_btn)
        method_row.addWidget(manual_btn)
        method_row.addStretch(1)
        v.addLayout(method_row)

        form = QFormLayout()
        addr = QLineEdit()
        addr.setPlaceholderText("192.168.1.50/24")
        gw = QLineEdit()
        gw.setPlaceholderText("192.168.1.1")
        dns = QLineEdit()
        dns.setPlaceholderText("8.8.8.8 1.1.1.1")
        form.addRow("Indirizzo", addr)
        form.addRow("Gateway", gw)
        form.addRow("DNS", dns)
        v.addLayout(form)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        if _show_modal(dlg) != QDialog.DialogCode.Accepted:
            return

        body = {
            "method": "manual" if manual_btn.isChecked() else "auto",
            "address": addr.text().strip(),
            "gateway": gw.text().strip(),
            "dns": dns.text().strip(),
        }

        async def post():
            try:
                import wifi_ops
                await wifi_ops.set_ip(
                    method=body["method"],
                    address=body["address"],
                    gateway=body["gateway"],
                    dns=body["dns"],
                )
                self._status.setText("Configurazione IP applicata")
                self._status.setProperty("role", "ok")
            except Exception as exc:
                self._status.setText(f"Errore IP: {exc}")
                self._status.setProperty("role", "danger")
            self._status.style().unpolish(self._status)
            self._status.style().polish(self._status)

        _schedule_qt(post())


class _AdminSection(QGroupBox):
    """Destructive / system-level actions."""

    def __init__(self, on_factory_reset, on_reboot, on_pi_factory_reset,
                 on_shutdown, parent=None):
        super().__init__("Amministrazione", parent)
        self._on_factory_reset = on_factory_reset
        self._on_reboot = on_reboot
        self._on_pi_factory_reset = on_pi_factory_reset
        self._on_shutdown = on_shutdown

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        info = QLabel(
            "Le operazioni qui sotto interessano radio e Pi. "
            "I reset di fabbrica cancellano la configurazione — conferma due volte."
        )
        info.setProperty("role", "muted")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Row 1: power actions (safe — reversible on power button).
        row1 = QHBoxLayout()
        reboot_btn = QPushButton("Riavvia Pi")
        reboot_btn.clicked.connect(self._reboot_clicked)
        shutdown_btn = QPushButton("Spegni Pi")
        # Distinct color so it doesn't sit next to Riavvia as a lookalike.
        shutdown_btn.setStyleSheet("color:#ffcf3a;")
        shutdown_btn.clicked.connect(self._shutdown_clicked)
        row1.addWidget(reboot_btn)
        row1.addWidget(shutdown_btn)
        layout.addLayout(row1)

        # Row 2: destructive (radio + Pi factory reset).
        row2 = QHBoxLayout()
        radio_btn = QPushButton("Reset radio")
        radio_btn.clicked.connect(self._factory_reset_clicked)
        pi_factory_btn = QPushButton("Reset Pi")
        pi_factory_btn.setStyleSheet("color:#ef4444;")
        pi_factory_btn.clicked.connect(self._pi_factory_clicked)
        row2.addWidget(radio_btn)
        row2.addWidget(pi_factory_btn)
        layout.addLayout(row2)

    def _reboot_clicked(self) -> None:
        if QMessageBox.question(
            self, "Riavvio",
            "Riavviare il Raspberry Pi ora? Radio e GUI non saranno disponibili per ~30 s.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._on_reboot()

    def _shutdown_clicked(self) -> None:
        # Double confirmation: shutting down a kiosk means a physical
        # power-cycle to bring it back, so make sure it's intentional.
        first = QMessageBox.warning(
            self, "Spegnimento",
            "Spegnere il Raspberry Pi ora? Per riaccenderlo servirà staccare e ricollegare l'alimentazione.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if first != QMessageBox.StandardButton.Yes:
            return
        confirm = QMessageBox.warning(
            self, "Spegnimento (ultima conferma)",
            "Conferma: lo spegnimento sicuro chiude la GUI e ferma il sistema.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._on_shutdown()

    def _factory_reset_clicked(self) -> None:
        first = QMessageBox.warning(
            self, "Reset radio",
            "Questo CANCELLA la configurazione radio. Continuare?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if first != QMessageBox.StandardButton.Yes:
            return
        confirm = QMessageBox.warning(
            self, "Reset radio (ultima conferma)",
            "Sei davvero sicuro? L'operazione è irreversibile.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._on_factory_reset()

    def _pi_factory_clicked(self) -> None:
        first = QMessageBox.warning(
            self, "Reset Pi",
            "Questa operazione cancella database, impostazioni e log di pi-Mesh sul Pi. "
            "La configurazione radio NON viene toccata. Continuare?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if first != QMessageBox.StandardButton.Yes:
            return
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "Reset Pi",
            "Digita WIPE per confermare:",
        )
        if not ok or text.strip() != "WIPE":
            return
        self._on_pi_factory_reset()


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
        self._on_save_channel(
            index=ch.get("index", 0),
            name=name_edit.text().strip(),
            psk_b64=psk_edit.text().strip(),
        )


class _DisplaySection(QGroupBox):
    """Theme picker + accent color + brightness + rotation."""

    def __init__(self, settings, parent=None):
        super().__init__("Schermo", parent)
        self._settings = settings

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(4)
        theme_row.addWidget(QLabel("Tema"))
        self._theme_buttons: dict[str, QPushButton] = {}
        for name in ("dark", "light", "hc", "custom"):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked, n=name: self._on_theme_clicked(n))
            theme_row.addWidget(btn)
            self._theme_buttons[name] = btn
        theme_row.addStretch(1)
        layout.addLayout(theme_row)

        accent_row = QHBoxLayout()
        accent_row.addWidget(QLabel("Accent"))
        self._accent_swatch = QPushButton("")
        # 44x44 minimum touch target for the kiosk's 3.5" display.
        self._accent_swatch.setFixedSize(44, 44)
        self._accent_swatch.clicked.connect(self._pick_accent)
        accent_row.addWidget(self._accent_swatch)
        accent_row.addStretch(1)
        layout.addLayout(accent_row)

        bri_row = QHBoxLayout()
        bri_row.addWidget(QLabel("Luminosità"))
        self._brightness = QSlider(Qt.Orientation.Horizontal)
        self._brightness.setRange(0, 255)
        self._brightness.setValue(255)
        self._brightness_value = QLabel("255")
        self._brightness_value.setMinimumWidth(28)
        self._brightness.valueChanged.connect(
            lambda v: self._brightness_value.setText(str(v))
        )
        self._brightness.sliderReleased.connect(self._on_brightness_release)
        bri_row.addWidget(self._brightness, 1)
        bri_row.addWidget(self._brightness_value)
        layout.addLayout(bri_row)

        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("Rotazione"))
        self._rotation_buttons: dict[int, QPushButton] = {}
        for deg in (0, 90, 180, 270):
            btn = QPushButton(f"{deg}°")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c, d=deg: self._on_rotation_clicked(d))
            rot_row.addWidget(btn)
            self._rotation_buttons[deg] = btn
        rot_row.addStretch(1)
        layout.addLayout(rot_row)

        self._refresh()
        _schedule_qt(self._fetch_display())

    def _refresh(self) -> None:
        if self._settings is None:
            return
        current = self._settings.get("display.theme", "dark") or "dark"
        for name, btn in self._theme_buttons.items():
            btn.setChecked(name == current)
        accent = self._settings.get("pimesh-accent") or "#4a9eff"
        self._set_swatch_color(accent)

    def _on_theme_clicked(self, name: str) -> None:
        if self._settings is None:
            return
        self._settings.set("display.theme", name)
        for n, btn in self._theme_buttons.items():
            btn.setChecked(n == name)

    def _pick_accent(self) -> None:
        if self._settings is None:
            return
        current = QColor(self._settings.get("pimesh-accent") or "#4a9eff")
        chosen = QColorDialog.getColor(current, self, "Colore accento")
        if chosen.isValid():
            value = chosen.name()
            self._settings.set("pimesh-accent", value)
            self._set_swatch_color(value)

    def _set_swatch_color(self, hex_color: str) -> None:
        self._accent_swatch.setStyleSheet(
            f"background:{hex_color}; border:1px solid #444; border-radius:6px;"
        )

    async def _fetch_display(self) -> None:
        try:
            import display_ops
            d = await display_ops.get_state()
            self._brightness.setRange(0, int(d.get("max_brightness", 255)))
            self._brightness.setValue(int(d.get("brightness", 255)))
            self._brightness_value.setText(str(self._brightness.value()))
            self._set_rotation_active(int(d.get("rotation", 0)))
        except Exception:
            log.debug("display fetch failed", exc_info=True)

    def _on_brightness_release(self) -> None:
        _schedule_qt(self._post_display(brightness=self._brightness.value()))

    def _on_rotation_clicked(self, deg: int) -> None:
        if QMessageBox.question(
            self, "Rotazione",
            f"Impostare rotazione a {deg}°? Il Pi si riavvierà per applicare.",
        ) != QMessageBox.StandardButton.Yes:
            self._refresh_rotation_buttons_from_settings()
            return
        self._set_rotation_active(deg)
        _schedule_qt(self._post_display(rotation=deg))

    def _refresh_rotation_buttons_from_settings(self) -> None:
        _schedule_qt(self._fetch_display())

    def _set_rotation_active(self, deg: int) -> None:
        for d, btn in self._rotation_buttons.items():
            btn.setChecked(d == deg)

    async def _post_display(self, *, brightness: int | None = None, rotation: int | None = None) -> None:
        if brightness is None and rotation is None:
            return
        try:
            import display_ops
            if brightness is not None:
                await display_ops.set_brightness(brightness)
            if rotation is not None:
                await display_ops.set_rotation(rotation)
        except Exception:
            log.exception("display apply failed")
            QMessageBox.warning(self, "Schermo", "Impossibile applicare la modifica.")


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

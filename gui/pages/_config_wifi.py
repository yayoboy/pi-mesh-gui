"""``_WifiSection`` — current status, scan/connect, saved profiles, static IP.

Extracted from :mod:`gui.pages._config_sections` so neither file grows past
~600 LOC. The shared helpers (``_show_modal``, dialog formatter) still live
in the parent module.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    QRadioButton,
    QVBoxLayout,
)

from gui.core.tasks import schedule as _schedule_qt
from gui.pages._config_sections import _show_modal

log = logging.getLogger(__name__)


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



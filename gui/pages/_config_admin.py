"""``_AdminSection`` — destructive / system-level actions (reboot, shutdown,
factory reset). Extracted from :mod:`gui.pages._config_sections`.
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

log = logging.getLogger(__name__)


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
        # Driven by QSS via role property so it respects the active palette.
        shutdown_btn.setProperty("role", "warn")
        shutdown_btn.clicked.connect(self._shutdown_clicked)
        row1.addWidget(reboot_btn)
        row1.addWidget(shutdown_btn)
        layout.addLayout(row1)

        # Row 2: destructive (radio + Pi factory reset).
        row2 = QHBoxLayout()
        radio_btn = QPushButton("Reset radio")
        radio_btn.clicked.connect(self._factory_reset_clicked)
        pi_factory_btn = QPushButton("Reset Pi")
        pi_factory_btn.setProperty("role", "danger")
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



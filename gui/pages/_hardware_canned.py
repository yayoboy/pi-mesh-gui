"""``_CannedMessagesSection`` — preset messages editor backed by SQLite.
Extracted from :mod:`gui.pages._hardware_sections`.
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


class _CannedMessagesSection(QGroupBox):
    """CRUD list of pre-canned message texts. Persisted directly via
    ``database.{get,add,update,delete}_canned_message``. The Messages
    page reads this list to populate
    its quick-insert menu.
    """

    def __init__(self, parent=None):
        super().__init__("Canned messages", parent)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(self._on_add)
        edit = QPushButton("Edit")
        edit.clicked.connect(self._on_edit)
        delete = QPushButton("Delete")
        delete.clicked.connect(self._on_delete)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh)
        for b in (add, edit, delete, refresh):
            bar.addWidget(b)
        bar.addStretch(1)
        layout.addLayout(bar)

        self._list = QListWidget(self)
        self._list.setMaximumHeight(140)
        self._list.itemDoubleClicked.connect(lambda _it: self._on_edit())
        layout.addWidget(self._list)

        self._refresh()

    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        _schedule(self._refresh_async())

    async def _refresh_async(self) -> None:
        try:
            import database
            items = await database.get_canned_messages()
        except Exception:
            items = []
        self._list.clear()
        for it in items:
            text = it.get("text") or ""
            short = text if len(text) <= 60 else text[:58] + "…"
            label = f"{it.get('sort_order', 0):02d}  {short}"
            qit = QListWidgetItem(label)
            qit.setData(Qt.ItemDataRole.UserRole, it)
            self._list.addItem(qit)

    def _prompt_text(self, current: str = "", current_order: int = 0) -> tuple[str, int] | None:
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QLineEdit,
            QSpinBox,
            QTextEdit,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("Canned message")
        dlg.setModal(True)
        form = QFormLayout(dlg)
        text_edit = QTextEdit(current)
        text_edit.setFixedHeight(80)
        order_edit = QSpinBox()
        order_edit.setRange(0, 999)
        order_edit.setValue(current_order)
        form.addRow("Text", text_edit)
        form.addRow("Order", order_edit)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        text = text_edit.toPlainText().strip()
        if not text:
            return None
        return text, order_edit.value()

    def _on_add(self) -> None:
        result = self._prompt_text()
        if result is None:
            return
        text, order = result
        _schedule(self._post_async("POST", None, text, order))

    def _on_edit(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        result = self._prompt_text(data.get("text") or "", int(data.get("sort_order") or 0))
        if result is None:
            return
        text, order = result
        _schedule(self._post_async("PUT", int(data.get("id")), text, order))

    def _on_delete(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        if QMessageBox.question(
            self, "Canned", f"Delete canned message {data.get('id')}?",
        ) != QMessageBox.StandardButton.Yes:
            return
        _schedule(self._post_async("DELETE", int(data.get("id")), None, None))

    async def _post_async(self, method: str, msg_id: int | None,
                          text: str | None, order: int | None) -> None:
        try:
            import database
            if method == "POST":
                await database.add_canned_message(text or "", int(order or 0))
            elif method == "PUT" and msg_id is not None:
                await database.update_canned_message(msg_id, text or "", int(order or 0))
            elif method == "DELETE" and msg_id is not None:
                await database.delete_canned_message(msg_id)
        except Exception:
            log.exception("canned %s failed", method)
            QMessageBox.warning(self, "Canned", f"{method} failed.")
        self._refresh()



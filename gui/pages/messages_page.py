"""Messages page: broadcast channel + DM threads.

Top-level QTabBar splits the two flows:
- Broadcast: channel selector (0-7) + chronological message list + composer.
- DMs: list of threads (left) + selected thread + composer (right).
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QSize, Qt, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.core.tasks import schedule as _schedule
from gui.pages._message_format import format_message
from gui.widgets.status_icons import MenuIcon, TrashIcon, icon_pixmap

log = logging.getLogger(__name__)


def _tick_oldest_pending(pending: list[QListWidgetItem]) -> bool:
    """Append " ✓" to the oldest pending outgoing item and drop it.

    Acks arrive oldest-first, so the head of the list is the right target.
    Items whose underlying C++ object was deleted (list cleared/reloaded)
    are skipped. Returns True when an item was ticked.
    """
    while pending:
        item = pending.pop(0)
        try:
            item.setText(item.text() + " ✓")
        except RuntimeError:
            continue
        return True
    return False


class _BroadcastView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._oldest_id: int | None = None  # for "load more" pagination
        # Outgoing items awaiting an ack, oldest first. Acks arrive
        # oldest-first so on_ack ticks the head of this list.
        self._pending_acks: list[QListWidgetItem] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.addWidget(QLabel("Canale"))
        self.channel = QSpinBox(self)
        self.channel.setRange(0, 7)
        self.channel.valueChanged.connect(lambda _v: self._reload())
        head.addWidget(self.channel)
        head.addStretch(1)
        self.info = QLabel("")
        self.info.setProperty("role", "muted")
        head.addWidget(self.info)

        # Clear-history trash button
        clear = QToolButton(self)
        clear.setIcon(QIcon(icon_pixmap(TrashIcon, 18, "#cdd")))
        clear.setIconSize(QSize(18, 18))
        clear.setToolTip("Svuota cronologia")
        clear.setAccessibleName("Svuota cronologia canale")
        clear.clicked.connect(self._on_clear)
        head.addWidget(clear)
        layout.addLayout(head)

        self.list = QListWidget(self)
        self.list.setUniformItemSizes(False)
        self.list.setWordWrap(True)
        f = self.list.font()
        f.setFamily("monospace")
        self.list.setFont(f)
        # Top "Load more" item is inserted on demand and removed once consumed.
        self.list.itemActivated.connect(self._maybe_load_more)

        # Empty-state placeholder shown when the channel has no messages.
        self._empty = QLabel(
            "Nessun messaggio nel canale.\nScrivi qui sotto per iniziare."
        )
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setProperty("role", "muted")

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self.list)
        self._stack.addWidget(self._empty)
        layout.addWidget(self._stack, 1)

        comp = QHBoxLayout()
        self.input = QLineEdit(self)
        self.input.setPlaceholderText("Scrivi un messaggio…")
        self.input.returnPressed.connect(self._on_send)
        canned = QToolButton(self)
        canned.setIcon(QIcon(icon_pixmap(MenuIcon, 18, "#cdd")))
        canned.setIconSize(QSize(18, 18))
        canned.setToolTip("Messaggi preimpostati")
        canned.setAccessibleName("Apri menu messaggi preimpostati")
        canned.clicked.connect(self._show_canned_menu)
        send = QPushButton("Invia")
        send.clicked.connect(self._on_send)
        comp.addWidget(self.input, 1)
        comp.addWidget(canned)
        comp.addWidget(send)
        layout.addLayout(comp)

        _schedule(self._reload_async())

    # ------------------------------------------------------------------

    def _reload(self) -> None:
        _schedule(self._reload_async())

    async def _reload_async(self) -> None:
        try:
            import config as cfg
            import database
            msgs = await database.get_messages(cfg.DB_PATH, channel=self.channel.value(), limit=50)
        except Exception:
            log.exception("messages reload failed")
            msgs = []

        self.list.clear()
        self._pending_acks.clear()
        self._oldest_id = msgs[0]["id"] if msgs and "id" in msgs[0] else None
        if self._oldest_id:
            self._add_load_more_item()
        for m in msgs:
            self._append(m)
        n = len(msgs)
        self.info.setText(f"{n} msg")
        self._stack.setCurrentIndex(1 if n == 0 else 0)
        self._scroll_bottom()

    def _add_load_more_item(self) -> None:
        item = QListWidgetItem("↑ Load older messages")
        item.setData(Qt.ItemDataRole.UserRole, "load_more")
        item.setForeground(Qt.GlobalColor.gray)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list.insertItem(0, item)

    def _append(self, msg: dict) -> QListWidgetItem:
        item = QListWidgetItem(format_message(msg))
        if msg.get("is_outgoing"):
            f = item.font()
            f.setItalic(True)
            item.setFont(f)
        self.list.addItem(item)
        return item

    def _prepend_below_loader(self, msg: dict) -> None:
        item = QListWidgetItem(format_message(msg))
        if msg.get("is_outgoing"):
            f = item.font()
            f.setItalic(True)
            item.setFont(f)
        # Insert just after the loader (index 1) so loader stays at top.
        idx = 1 if self.list.count() and self.list.item(0).data(Qt.ItemDataRole.UserRole) == "load_more" else 0
        self.list.insertItem(idx, item)

    def _scroll_bottom(self) -> None:
        if self.list.count() > 0:
            self.list.scrollToItem(self.list.item(self.list.count() - 1))

    def _maybe_load_more(self, item: QListWidgetItem) -> None:
        if item is None or item.data(Qt.ItemDataRole.UserRole) != "load_more":
            return
        if not self._oldest_id:
            return
        _schedule(self._load_older(self._oldest_id))

    async def _load_older(self, before_id: int) -> None:
        try:
            import config as cfg
            import database
            older = await database.get_messages(
                cfg.DB_PATH, channel=self.channel.value(), limit=50, before_id=before_id,
            )
        except Exception:
            log.exception("load older failed")
            return
        if not older:
            # Nothing more — drop the loader.
            top = self.list.item(0)
            if top and top.data(Qt.ItemDataRole.UserRole) == "load_more":
                self.list.takeItem(0)
            self._oldest_id = None
            return
        # Replace loader with new oldest_id, then prepend in chronological
        # order so the visible row order stays correct.
        top = self.list.item(0)
        if top and top.data(Qt.ItemDataRole.UserRole) == "load_more":
            self.list.takeItem(0)
        for m in reversed(older):
            self._prepend_below_loader(m)
        self._oldest_id = older[0].get("id")
        if self._oldest_id:
            self._add_load_more_item()

    def _on_clear(self) -> None:
        if QMessageBox.question(
            self, "Messaggi", "Svuotare tutta la cronologia (broadcast + DM)?",
        ) != QMessageBox.StandardButton.Yes:
            return
        _schedule(self._clear_async())

    async def _clear_async(self) -> None:
        try:
            import config as cfg
            import database
            await database.clear_messages(cfg.DB_PATH)
        except Exception:
            log.exception("clear messages failed")
            return
        self.list.clear()
        self._pending_acks.clear()
        self.info.setText("cleared")

    def _show_canned_menu(self) -> None:
        _schedule(self._populate_and_show_canned())

    async def _populate_and_show_canned(self) -> None:
        try:
            import database
            items = await database.get_canned_messages()
        except Exception:
            items = []
        if not items:
            from gui.widgets.toast import show_toast
            show_toast(self, "Nessun messaggio preimpostato — aggiungili in Config", role="warn")
            return
        menu = QMenu(self)
        for it in items:
            text = it.get("text") or ""
            if not text:
                continue
            short = text if len(text) <= 32 else text[:30] + "…"
            menu.addAction(short, lambda t=text: self._insert_canned(t))
        menu.exec(self.mapToGlobal(self.input.geometry().bottomLeft()))

    def _insert_canned(self, text: str) -> None:
        self.input.setText(text)
        self.input.setFocus()

    # Slots --------------------------------------------------------------

    @Slot(dict)
    def on_incoming(self, event: dict) -> None:
        if event.get("channel", 0) != self.channel.value():
            return
        msg = {
            "ts": event.get("ts") or int(time.time()),
            "node_id": event.get("from") or event.get("id"),
            "text": event.get("text") or "",
            "is_outgoing": False,
            "ack": 0,
        }
        self._append(msg)
        self._scroll_bottom()

    def has_pending_ack(self) -> bool:
        return bool(self._pending_acks)

    @Slot(dict)
    def on_ack(self, event: dict) -> None:
        # Tick the OLDEST pending outgoing item: acks arrive oldest-first.
        # Never substring-match message text — only explicitly tracked
        # outgoing items are eligible.
        _tick_oldest_pending(self._pending_acks)

    @Slot()
    def _on_send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        _schedule(self._send_async(text))

    async def _send_async(self, text: str) -> None:
        try:
            import meshtasticd_client
            await meshtasticd_client.send_text(text, "^all", channel=self.channel.value())
        except Exception:
            log.exception("send_text failed")
            return
        item = self._append({"ts": int(time.time()), "node_id": "me", "text": text, "is_outgoing": True})
        self._pending_acks.append(item)
        self._scroll_bottom()


class _DmView(QWidget):
    """Threads list + thread messages + composer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._peer_id: str | None = None
        # Outgoing items of the open conversation awaiting an ack (oldest first).
        self._pending_acks: list[QListWidgetItem] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        split = QSplitter(Qt.Orientation.Horizontal, self)
        layout.addWidget(split, 1)

        # Left: threads list
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(2)
        ll.addWidget(QLabel("Conversazioni"))
        self.threads = QListWidget(left)
        self.threads.itemSelectionChanged.connect(self._on_thread_selected)
        ll.addWidget(self.threads, 1)
        split.addWidget(left)

        # Right: thread + composer
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(2)
        self.peer_lbl = QLabel("(seleziona una conversazione)")
        self.peer_lbl.setProperty("role", "muted")
        rl.addWidget(self.peer_lbl)
        self.msgs = QListWidget(right)
        self.msgs.setUniformItemSizes(True)
        f = self.msgs.font()
        f.setFamily("monospace")
        self.msgs.setFont(f)
        rl.addWidget(self.msgs, 1)

        comp = QHBoxLayout()
        self.input = QLineEdit(right)
        self.input.setPlaceholderText("Scrivi un DM…")
        self.input.returnPressed.connect(self._on_send)
        send = QPushButton("Invia")
        send.clicked.connect(self._on_send)
        comp.addWidget(self.input, 1)
        comp.addWidget(send)
        rl.addLayout(comp)
        split.addWidget(right)
        # 1/3 threads, 2/3 messages on a 480 px screen.
        split.setSizes([160, 320])

        _schedule(self._reload_threads())

    # ------------------------------------------------------------------

    def reload(self) -> None:
        _schedule(self._reload_threads())

    async def _reload_threads(self) -> None:
        try:
            import config as cfg
            import database
            import meshtasticd_client
            local_id = meshtasticd_client.get_local_id()
            threads = await database.get_dm_threads(cfg.DB_PATH, local_id)
        except Exception:
            log.exception("dm threads reload failed")
            threads = []

        # Rebuild the list without clobbering the user's open conversation:
        # signals are blocked while we restore the selection so the message
        # pane is not reloaded (and unread state not re-marked) as a side
        # effect of a background threads refresh.
        self.threads.blockSignals(True)
        try:
            self.threads.clear()
            for t in threads:
                label = t.get("short_name") or t.get("peer_id") or "?"
                unread = t.get("unread") or 0
                text = f"{label}  ({unread} nuovi)" if unread else label
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, t.get("peer_id"))
                if unread:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                self.threads.addItem(item)
                if self._peer_id and t.get("peer_id") == self._peer_id:
                    self.threads.setCurrentItem(item)
        finally:
            self.threads.blockSignals(False)

    @Slot()
    def _on_thread_selected(self) -> None:
        items = self.threads.selectedItems()
        if not items:
            return
        peer = items[0].data(Qt.ItemDataRole.UserRole)
        self._peer_id = peer
        self.peer_lbl.setText(peer or "")
        _schedule(self._load_messages(peer))

    async def _load_messages(self, peer: str) -> None:
        try:
            import config as cfg
            import database
            import meshtasticd_client
            local_id = meshtasticd_client.get_local_id()
            msgs = await database.get_dm_messages(cfg.DB_PATH, peer, local_id, limit=100)
            await database.mark_dm_read(cfg.DB_PATH, peer)
        except Exception:
            log.exception("dm load failed")
            msgs = []
        # The user may have switched conversation while we awaited the DB —
        # drop stale results instead of letting the last finisher win.
        if peer != self._peer_id:
            return
        self.msgs.clear()
        self._pending_acks.clear()
        for m in msgs:
            item = QListWidgetItem(format_message(m))
            if m.get("is_outgoing"):
                f = item.font()
                f.setItalic(True)
                item.setFont(f)
            self.msgs.addItem(item)
        if self.msgs.count() > 0:
            self.msgs.scrollToItem(self.msgs.item(self.msgs.count() - 1))

    @Slot()
    def _on_send(self) -> None:
        if not self._peer_id:
            return
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        _schedule(self._send_async(self._peer_id, text))

    async def _send_async(self, peer: str, text: str) -> None:
        try:
            import meshtasticd_client
            await meshtasticd_client.send_text(text, peer, channel=0)
        except Exception:
            log.exception("send DM failed")
            return
        # The user may have switched threads mid-await: don't append the echo
        # to a different conversation's pane.
        if peer != self._peer_id:
            return
        msg = {"ts": int(time.time()), "node_id": "me", "text": text, "is_outgoing": True}
        item = QListWidgetItem(format_message(msg))
        f = item.font()
        f.setItalic(True)
        item.setFont(f)
        self.msgs.addItem(item)
        self._pending_acks.append(item)
        self.msgs.scrollToItem(self.msgs.item(self.msgs.count() - 1))

    # Slots --------------------------------------------------------------

    def peer_id(self) -> str | None:
        return self._peer_id

    def has_pending_ack(self) -> bool:
        return bool(self._pending_acks)

    @Slot(dict)
    def on_ack(self, event: dict) -> None:
        _tick_oldest_pending(self._pending_acks)

    def on_incoming(self, peer: str | None, event: dict) -> None:
        """Live incoming DM: refresh the threads list and, when the open
        conversation is with that peer, append to the message pane too."""
        self.reload()
        if not peer or peer != self._peer_id:
            return
        msg = {
            "ts": event.get("ts") or int(time.time()),
            "node_id": peer,
            "text": event.get("text") or "",
            "is_outgoing": False,
        }
        self.msgs.addItem(QListWidgetItem(format_message(msg)))
        self.msgs.scrollToItem(self.msgs.item(self.msgs.count() - 1))


class Page(QWidget):
    def __init__(self, eventbus, settings):
        super().__init__()
        self._eventbus = eventbus
        self._settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Mode tabs at the top: Broadcast / DMs.
        self._tabs = QTabBar(self)
        self._tabs.addTab("Broadcast")
        self._tabs.addTab("DMs")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs)

        self._stack = QStackedWidget(self)
        self._broadcast = _BroadcastView(self._stack)
        self._dm = _DmView(self._stack)
        self._stack.addWidget(self._broadcast)
        self._stack.addWidget(self._dm)
        layout.addWidget(self._stack, 1)

        if eventbus is not None:
            eventbus.message_received.connect(self._on_incoming)
            eventbus.ack_received.connect(self._on_ack)

    @Slot(int)
    def _on_tab_changed(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        if idx == 1:
            self._dm.reload()  # refresh threads each time DM tab is opened

    @Slot(dict)
    def _on_incoming(self, event: dict) -> None:
        # Broadcast vs DM dispatch.
        dest = event.get("destination") or "^all"
        if dest == "^all":
            self._broadcast.on_incoming(event)
        else:
            # Refresh thread list (unread count changes) and, when the open
            # conversation is with this peer, append to the message pane.
            peer = event.get("from") or event.get("id")
            self._dm.on_incoming(peer, event)

    @Slot(dict)
    def _on_ack(self, event: dict) -> None:
        """Route the ack to the right view.

        The ack event only carries the acking node id, so the heuristic is:
        if the open DM conversation is with that node and has un-acked
        outgoing items, tick there; otherwise tick the broadcast view.
        """
        node_id = event.get("node_id")
        if node_id and node_id == self._dm.peer_id() and self._dm.has_pending_ack():
            self._dm.on_ack(event)
        else:
            self._broadcast.on_ack(event)

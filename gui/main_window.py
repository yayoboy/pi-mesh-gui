"""MainWindow: status bar (top, 24 px) + tab content + tab bar (bottom, 32 px).

Geometry targets the 320×480 (or 480×320 rotated) Waveshare SPI display:
``setFixedSize(320, 480)`` so dev runs on the desktop mirror the kiosk.
Rotation is persisted by writing ``rotate=`` inside the ``dtoverlay=`` line
of ``/boot/firmware/config.txt`` (see ``display_ops.set_rotation``); a
reboot is required for the change to take effect.

Each tab is a lazily-imported page module exposing ``Page(QWidget)`` taking
``(eventbus, settings)``. Lazy import keeps heavy pages (map, metrics) out
of the startup hot path.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QEvent, QPoint, QSize, QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.widgets.status_icons import (
    BatteryIcon,
    ConfigIcon,
    ConnIcon,
    GpsIcon,
    LogIcon,
    MapIcon,
    MessagesIcon,
    MetricsIcon,
    NodesIcon,
    RotationIcon,
    ScreenshotIcon,
    SignalIcon,
    icon_pixmap,
)

log = logging.getLogger(__name__)


# Match the web UI: 6 tabs, the 7th (Telemetry) is reachable from the node
# detail view rather than getting its own slot. Italian labels match
# templates/base.html.
_TABS: list[tuple[str, str, type]] = [
    # (label_it, module_path, vector icon class — rendered via QPainter so
    # we don't depend on font glyph availability on the Pi linuxfb)
    ("Nodi",     "gui.pages.nodes_page",    NodesIcon),
    ("Mappa",    "gui.pages.map_page",      MapIcon),
    ("Messaggi", "gui.pages.messages_page", MessagesIcon),
    ("Config",   "gui.pages.config_page",   ConfigIcon),
    ("Metriche", "gui.pages.metrics_page",  MetricsIcon),
    ("Log",      "gui.pages.log_page",      LogIcon),
]

# Hidden tab — accessible programmatically via show_telemetry() but not in
# the bottom bar.
_TELEMETRY_TAB = ("Telemetria", "gui.pages.telemetry_page")


# Geometry constants — default landscape, matching the user's installed
# orientation on the Waveshare 3.5" SPI display. When the OS reports a
# different rotation we adopt that instead at startup (see __init__).
SCREEN_W_LANDSCAPE = 480
SCREEN_H_LANDSCAPE = 320
SCREEN_W_PORTRAIT  = 320
SCREEN_H_PORTRAIT  = 480
STATUS_H = 28
TABBAR_H = 44


# ---------------------------------------------------------------------------
# Status bar
# ---------------------------------------------------------------------------

class _StatusIcon(QLabel):
    """Compact icon label, used for the right-side row in the status bar."""

    def __init__(self, glyph: str = "·", *, tooltip: str = "", parent=None):
        super().__init__(glyph, parent)
        self.setToolTip(tooltip)
        self.setProperty("role", "muted")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(14, 14)
        f = self.font()
        f.setPointSize(9)
        self.setFont(f)


class StatusBar(QFrame):
    """Top status bar mirroring templates/base.html, height = 24 px.

    Left:  node short_name (or "pi-Mesh" when unknown).
    Right: row of compact icons: battery, LoRa signal, GPS, board state,
           rotation menu, screenshot, reboot, shutdown.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusbar")
        self.setFixedHeight(STATUS_H)

        root = QHBoxLayout(self)
        root.setContentsMargins(6, 0, 4, 0)
        root.setSpacing(6)

        self._name = QLabel("pi-Mesh", self)
        self._name.setProperty("role", "muted")
        f = self._name.font()
        f.setPointSize(9)
        self._name.setFont(f)
        root.addWidget(self._name)
        root.addStretch(1)

        # Vector icons drawn with QPainter so we don't depend on font
        # glyph availability (Unicode emojis varied across distros).
        self._batt = BatteryIcon(self)
        self._batt.set_tooltip("Batteria")
        self._lora = SignalIcon(self)
        self._lora.set_tooltip("Segnale LoRa")
        self._gps = GpsIcon(self)
        self._gps.set_tooltip("GPS")
        self._conn = ConnIcon(self)
        self._conn.set_tooltip("Radio")
        _ACTION_COLOR = "#cdd"
        self._rot = RotationIcon(self)
        self._rot.set_color(_ACTION_COLOR)
        self._rot.set_tooltip("Rotazione")
        self._rot.set_clickable(True)
        self._rot.clicked.connect(self._show_rotation_menu)

        self._shot = ScreenshotIcon(self)
        self._shot.set_color(_ACTION_COLOR)
        self._shot.set_tooltip("Screenshot")
        self._shot.set_clickable(True)
        self._shot.clicked.connect(self._take_screenshot)

        # Reboot/shutdown intentionally NOT in the status bar — too easy to
        # tap by accident on the touchscreen. Both actions are available in
        # Config → Amministrazione with double-confirmation.
        for w in (self._batt, self._lora, self._gps, self._conn,
                  self._rot, self._shot):
            root.addWidget(w)

    # ------------------------------------------------------------------

    def update_state(self, *, connected: bool, node_count: int, local_id: str,
                     local_name: str | None = None,
                     battery: int | None = None,
                     snr: float | None = None,
                     gps_fix: bool | None = None) -> None:
        # Clamp the label width so a long local_name doesn't fight the icons
        # for space (and end up rendered with the left side clipped off).
        raw = local_name or local_id or "pi-Mesh"
        fm = self._name.fontMetrics()
        elided = fm.elidedText(raw, Qt.TextElideMode.ElideRight, 110)
        self._name.setText(elided)
        self._name.setToolTip(raw)
        self._conn.set_connected(connected)
        self._batt.set_level(None if battery is None else battery / 100.0)
        self._lora.set_strength(snr)
        self._gps.set_fix(bool(gps_fix))

    # Slots --------------------------------------------------------------

    def _show_rotation_menu(self) -> None:
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        for deg in (0, 90, 180, 270):
            menu.addAction(f"{deg}°", lambda d=deg: self._set_rotation(d))
        menu.exec(self._rot.mapToGlobal(self._rot.rect().bottomLeft()))

    def _set_rotation(self, deg: int) -> None:
        if QMessageBox.question(
            self, "Rotazione",
            f"Ruotare a {deg}° e riavviare?",
        ) != QMessageBox.StandardButton.Yes:
            return
        import asyncio
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(self._post_rotation(deg))

    async def _post_rotation(self, deg: int) -> None:
        try:
            import display_ops
            import system_ops
            await display_ops.set_rotation(deg)
            # Rotation takes effect only after a reboot.
            await system_ops.reboot()
        except Exception:
            log.exception("rotation post failed")
            QMessageBox.warning(self, "pi-Mesh", "Impossibile applicare la rotazione.")

    def _take_screenshot(self) -> None:
        from datetime import datetime
        from pathlib import Path

        from PySide6.QtGui import QPixmap

        win = self.window()
        if win is None:
            return
        pm = win.grab()
        out = Path("data/screenshots") / f"{datetime.now():%Y%m%d-%H%M%S}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        pm.save(str(out), "PNG")
        log.info("screenshot saved to %s", out)

# ---------------------------------------------------------------------------
# Tab bar
# ---------------------------------------------------------------------------

_TAB_ICON_PX = 24
_TAB_ICON_COLOR = "#cdd"
_TAB_ICON_COLOR_ACTIVE = "#ffcf3a"


class _TabButton(QToolButton):
    """Touch-friendly tab button: vector icon on top, label below.

    Optionally renders a small badge by appending ``·N`` to the label
    (used by the Messages tab for unread DM count).
    """

    def __init__(self, label: str, icon_cls: type, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self._label = label
        self._icon_cls = icon_cls
        self._badge = 0
        self._icon_normal = QIcon(icon_pixmap(icon_cls, _TAB_ICON_PX, _TAB_ICON_COLOR))
        self._icon_active = QIcon(icon_pixmap(icon_cls, _TAB_ICON_PX, _TAB_ICON_COLOR_ACTIVE))
        self.setIcon(self._icon_normal)
        self.setIconSize(QSize(_TAB_ICON_PX, _TAB_ICON_PX))
        self._update_text()
        self.setMinimumHeight(TABBAR_H)
        f = self.font()
        f.setPointSize(8)
        self.setFont(f)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toggled.connect(self._on_toggled)

    def set_badge(self, count: int) -> None:
        if count == self._badge:
            return
        self._badge = max(0, int(count))
        self._update_text()

    def _update_text(self) -> None:
        if self._badge:
            badge = "9+" if self._badge > 9 else str(self._badge)
            self.setText(f"{self._label}·{badge}")
        else:
            self.setText(self._label)

    def _on_toggled(self, checked: bool) -> None:
        self.setIcon(self._icon_active if checked else self._icon_normal)


class TabBar(QFrame):
    """Bottom bar: 6 equal-width tabs, vector icons + label."""

    def __init__(self, tabs: list[tuple[str, str, type]], on_select, parent=None):
        super().__init__(parent)
        self.setObjectName("tabbar")
        self.setFixedHeight(TABBAR_H)

        self._buttons: list[_TabButton] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for i, (label, _module, icon_cls) in enumerate(tabs):
            btn = _TabButton(label, icon_cls, self)
            btn.clicked.connect(lambda _checked, idx=i: on_select(idx))
            layout.addWidget(btn, 1)  # stretch=1 → flex-1 equivalent
            self._buttons.append(btn)

    def set_active(self, index: int) -> None:
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == index)

    def set_badge(self, index: int, count: int) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].set_badge(count)


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("pi-Mesh")

        # Lock the window to the SPI display geometry. Rotation is owned by
        # the kernel/X level (dtoverlay tft35a:rotate=N + xrandr), not Qt:
        # we just adopt whichever of the two orientations the running X
        # server reports. On a desktop dev box (no SPI screen) we fall back
        # to landscape 480×320 because that's the user's installed layout.
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geom = screen.size()
            if geom.height() >= geom.width():  # portrait
                w, h = SCREEN_W_PORTRAIT, SCREEN_H_PORTRAIT
            else:
                w, h = SCREEN_W_LANDSCAPE, SCREEN_H_LANDSCAPE
        else:
            w, h = SCREEN_W_LANDSCAPE, SCREEN_H_LANDSCAPE
        self.setFixedSize(QSize(w, h))
        self._screen_w, self._screen_h = w, h
        self._is_landscape = w > h

        self._eventbus = None
        self._settings = None

        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._status = StatusBar(central)
        self._stack = QStackedWidget(central)
        self._tabs = TabBar(_TABS, self._select_tab, central)

        root.addWidget(self._status)
        root.addWidget(self._stack, 1)
        root.addWidget(self._tabs)
        self.setCentralWidget(central)

        # The software cursor is rendered by Qt's linuxfb QPA plugin
        # itself, provided QT_QPA_FB_HIDECURSOR is NOT set (per Qt 6
        # docs: presence of the env var hides the cursor regardless of
        # value). No application-side overlay needed.

        # index -> (label, module_path, instance|None) for the visible tabs.
        self._pages: list[tuple[str, str, QWidget | None]] = [
            (label, module, None) for label, module, _icon in _TABS
        ]
        # The hidden Telemetry page lives outside _pages and is added on
        # demand via show_telemetry().
        self._telemetry_page: QWidget | None = None

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

        # Slower poll for the unread-message badge on the Msg tab.
        self._badge_timer = QTimer(self)
        self._badge_timer.setInterval(5000)
        self._badge_timer.timeout.connect(self._refresh_msg_badge)
        self._badge_timer.start()

    # ------------------------------------------------------------------

    def attach(self, eventbus, settings) -> None:
        self._eventbus = eventbus
        self._settings = settings

        # Software keyboard appears on text-widget focus, hides on blur.
        # Disabled when PIMESH_GUI_NO_VKB=1 (useful for desktop dev).
        import os
        if os.environ.get("PIMESH_GUI_NO_VKB", "0") != "1":
            from gui.widgets.vkb import VkbController
            self._vkb_controller = VkbController(self)

        # Toast host so any descendant can call show_toast(self, …).
        from gui.widgets.toast import ToastHost
        ToastHost.for_window(self)

        self._select_tab(0)

    def _select_tab(self, index: int) -> None:
        label, module_path, instance = self._pages[index]
        if instance is None:
            instance = self._build_page(module_path, label)
            self._pages[index] = (label, module_path, instance)
            self._stack.addWidget(instance)
        self._stack.setCurrentWidget(instance)
        self._tabs.set_active(index)

    def show_telemetry(self) -> None:
        """Open the (hidden) telemetry page on demand from a node detail view."""
        if self._telemetry_page is None:
            self._telemetry_page = self._build_page(_TELEMETRY_TAB[1], _TELEMETRY_TAB[0])
            self._stack.addWidget(self._telemetry_page)
        self._stack.setCurrentWidget(self._telemetry_page)

    def _build_page(self, module_path: str, label: str) -> QWidget:
        try:
            mod = __import__(module_path, fromlist=["Page"])
            return mod.Page(self._eventbus, self._settings)
        except Exception as exc:
            log.exception("failed to build page %s", module_path)
            from gui.pages._stub import StubPage
            return StubPage(label, error=str(exc))

    def _refresh_msg_badge(self) -> None:
        import asyncio
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(self._fetch_unread_count())

    async def _fetch_unread_count(self) -> None:
        try:
            import config as cfg
            import database
            import meshtasticd_client
            local_id = meshtasticd_client.get_local_id() or ""
            count = await database.get_total_unread(cfg.DB_PATH, local_id)
        except Exception:
            count = 0
        # Index 2 in _TABS is the Msg tab.
        self._tabs.set_badge(2, count)

    def _refresh_status(self) -> None:
        try:
            import meshtasticd_client
            local = meshtasticd_client.get_local_node() or {}
            self._status.update_state(
                connected=meshtasticd_client.is_connected(),
                node_count=len(meshtasticd_client.get_nodes()),
                local_id=meshtasticd_client.get_local_id(),
                local_name=local.get("short_name"),
                battery=local.get("battery_level"),
                snr=local.get("snr"),
                gps_fix=local.get("latitude") is not None,
            )
        except Exception:
            log.debug("status refresh failed", exc_info=True)

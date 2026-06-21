"""On-screen virtual keyboard for the touchscreen kiosk.

Mirrors the layout in ``static/vkbd.js``: three pages (alpha, sym, sym2),
shift toggle, backspace, bottom row of comma/space/period/done. Sized for
a 480 px width landscape display.

Usage:
    vkb = VirtualKeyboard(parent=main_window)
    vkb.attach_to(main_window)   # auto show/hide on QLineEdit / QPlainTextEdit focus
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.widgets._vkb_layout import ROWS_ALPHA as _ROWS_ALPHA
from gui.widgets._vkb_layout import ROWS_SYM as _ROWS_SYM
from gui.widgets._vkb_layout import ROWS_SYM2 as _ROWS_SYM2

log = logging.getLogger(__name__)


_KEY_QSS = """
QPushButton {
    background: #2d2d44;
    color: #c9d1e0;
    border: none;
    border-radius: 4px;
    font-size: 13px;
    min-width: 26px;
    min-height: 30px;
}
QPushButton:pressed { background: #4a9eff; color: #ffffff; }
QPushButton[modkey="true"] { background: #1f2a40; }
QPushButton[modkey="true"]:checked { background: #4a9eff; color: #ffffff; }
"""


class VirtualKeyboard(QFrame):
    """Three-page software keyboard. Emits ``key_pressed(str)`` for chars,
    ``backspace`` for backspace, and ``done`` when the user dismisses it."""

    key_pressed = Signal(str)
    backspace = Signal()
    done = Signal()

    PAGE_ALPHA = 0
    PAGE_SYM = 1
    PAGE_SYM2 = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("vkb")
        self.setStyleSheet(_KEY_QSS + "QFrame#vkb { background: #1a1a2e; }")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # never steal focus

        self._page = self.PAGE_ALPHA
        self._shift = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 4)
        outer.setSpacing(2)

        self._rows_host = QWidget(self)
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        outer.addWidget(self._rows_host)

        self._render()

    # ------------------------------------------------------------------
    # Render

    def _clear_rows(self) -> None:
        while self._rows_layout.count():
            row_item = self._rows_layout.takeAt(0)
            row_w = row_item.widget()
            if row_w is not None:
                row_w.deleteLater()

    def _render(self) -> None:
        self._clear_rows()
        rows = (
            _ROWS_ALPHA if self._page == self.PAGE_ALPHA
            else _ROWS_SYM if self._page == self.PAGE_SYM
            else _ROWS_SYM2
        )
        for r, row_chars in enumerate(rows):
            row_w = QWidget(self._rows_host)
            row_layout = QHBoxLayout(row_w)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(2)

            # Shift key on last alpha row
            if r == 2 and self._page == self.PAGE_ALPHA:
                shift = QPushButton("⇧", row_w)
                shift.setProperty("modkey", True)
                shift.setCheckable(True)
                shift.setChecked(self._shift)
                shift.setMinimumWidth(34)
                shift.clicked.connect(self._toggle_shift)
                row_layout.addWidget(shift)

            for ch in row_chars:
                display = ch.upper() if (self._shift and self._page == self.PAGE_ALPHA) else ch
                btn = QPushButton(display, row_w)
                btn.clicked.connect(lambda _checked=False, c=display: self._press_char(c))
                row_layout.addWidget(btn)

            # Backspace on last row
            if r == len(rows) - 1:
                bs = QPushButton("⌫", row_w)
                bs.setProperty("modkey", True)
                bs.setMinimumWidth(34)
                bs.clicked.connect(self._press_backspace)
                row_layout.addWidget(bs)

            self._rows_layout.addWidget(row_w)

        # Bottom row: sym toggle, comma, space, period, done.
        bottom = QWidget(self._rows_host)
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(2)

        sym = QPushButton(
            "123" if self._page == self.PAGE_ALPHA
            else "#+=" if self._page == self.PAGE_SYM
            else "ABC",
            bottom,
        )
        sym.setProperty("modkey", True)
        sym.setMinimumWidth(40)
        sym.clicked.connect(self._toggle_sym)
        bl.addWidget(sym)

        comma = QPushButton(",", bottom)
        comma.clicked.connect(lambda: self._press_char(","))
        bl.addWidget(comma)

        space = QPushButton(" ", bottom)
        space.setMinimumWidth(120)
        space.clicked.connect(lambda: self._press_char(" "))
        bl.addWidget(space, 1)

        period = QPushButton(".", bottom)
        period.clicked.connect(lambda: self._press_char("."))
        bl.addWidget(period)

        done = QPushButton("✓", bottom)
        done.setProperty("modkey", True)
        done.setMinimumWidth(40)
        done.clicked.connect(self.done.emit)
        bl.addWidget(done)

        self._rows_layout.addWidget(bottom)

        # Keys must NOT take focus. If a key grabbed focus on tap, the
        # QApplication focusChanged signal would fire and hide the keyboard
        # mid-press — and the now-uncovered widget behind the key (the ✓ sits
        # right over the bottom tab bar) would receive the release and trigger,
        # e.g. switching to the Log tab. Keys drive the target field through
        # the controller's signals, never through focus, so NoFocus is correct.
        for btn in self.findChildren(QPushButton):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ------------------------------------------------------------------
    # Slots

    def _toggle_shift(self) -> None:
        self._shift = not self._shift
        self._render()

    def _toggle_sym(self) -> None:
        # Cycle ALPHA → SYM → SYM2 → ALPHA
        self._page = (self._page + 1) % 3
        self._render()

    def _press_char(self, ch: str) -> None:
        self.key_pressed.emit(ch)
        # auto-release shift after one keypress, like the web vkbd.
        if self._shift and self._page == self.PAGE_ALPHA:
            self._shift = False
            self._render()

    def _press_backspace(self) -> None:
        self.backspace.emit()


# ---------------------------------------------------------------------------
# Physical-keyboard detection
# ---------------------------------------------------------------------------

def external_keyboard_present() -> bool:
    """True when a physical keyboard is attached.

    udev names a keyboard's event node ``*-event-kbd`` (it only tags a device
    as a keyboard when it exposes the alphabetic key block), so this matches a
    real USB keyboard but NOT the rotary encoder / gpio-keys, which expose only
    a couple of keys and get no ``-kbd`` symlink. When one is present the
    on-screen keyboard is suppressed — the user types on the real one.
    """
    import glob
    try:
        return bool(
            glob.glob("/dev/input/by-id/*-event-kbd")
            or glob.glob("/dev/input/by-path/*usb*-event-kbd")
        )
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Auto-attach helper
# ---------------------------------------------------------------------------

class VkbController(QObject):
    """Watches focus changes and shows the keyboard when a text widget is focused.

    Install once on the main window:
        controller = VkbController(main_window)
    """

    def __init__(self, host: QWidget, block_widget: QWidget | None = None):
        super().__init__(host)
        self._host = host
        self._target: QWidget | None = None
        # Disabled while the keyboard is shown so a tap that lands on / leaks
        # through to it (e.g. the bottom tab bar sitting behind the keyboard)
        # can't be triggered by accident — typically passed the tab bar.
        self._block = block_widget

        self._kbd = VirtualKeyboard(parent=host)
        self._kbd.hide()
        # Position at the bottom of the host; resizes track the host.
        self._reposition()
        self._kbd.key_pressed.connect(self._on_char)
        self._kbd.backspace.connect(self._on_backspace)
        self._kbd.done.connect(self.hide_keyboard)

        # Application-wide filter: focus changes drive show/hide, and a press
        # anywhere outside the keyboard (and outside any text field) dismisses
        # it. On a touch-only kiosk this is the only reliable way to close the
        # keyboard — tapping a non-focusable widget never clears focus by
        # itself, which is why it used to get stuck open.
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
            app.installEventFilter(self)
        else:
            host.installEventFilter(self)

    # ------------------------------------------------------------------

    def _reposition(self) -> None:
        # Anchor to bottom edge, full width minus a 2 px margin.
        host_w = self._host.width()
        kbd_h = max(140, host_w // 3)
        self._kbd.setGeometry(0, self._host.height() - kbd_h, host_w, kbd_h)
        self._kbd.raise_()

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.Type.Resize and obj is self._host:
            self._reposition()
        elif et == QEvent.Type.MouseButtonPress and self._kbd.isVisible():
            if self._maybe_dismiss(event):
                # Swallow the dismissing tap so it doesn't also activate
                # whatever sits behind the keyboard.
                return True
        return super().eventFilter(obj, event)

    def _maybe_dismiss(self, event) -> bool:
        """Hide the keyboard when the user taps outside it and outside any text
        field. Returns True when it dismissed (the caller consumes the tap)."""
        from PySide6.QtWidgets import QApplication

        gp = event.globalPosition().toPoint()
        kbd_rect = QRect(self._kbd.mapToGlobal(QPoint(0, 0)), self._kbd.size())
        if kbd_rect.contains(gp):
            return False
        w = QApplication.widgetAt(gp)
        if isinstance(w, (QLineEdit, QPlainTextEdit, QTextEdit)):
            return False  # tapping another field: keep the keyboard up for it
        if self._target is not None:
            self._target.clearFocus()
        self.hide_keyboard()
        return True

    def _set_block(self, blocked: bool) -> None:
        if self._block is not None:
            self._block.setEnabled(not blocked)

    def _on_focus_changed(self, old: QWidget | None, new: QWidget | None) -> None:
        if (isinstance(new, (QLineEdit, QPlainTextEdit, QTextEdit))
                and not external_keyboard_present()):
            self._target = new
            self._reposition()
            self._kbd.show()
            self._set_block(True)
        else:
            # Hide only if focus left a text widget (clicking on the VKB
            # itself transfers focus to a button, but the buttons have
            # NoFocus — handled via FocusPolicy on the keyboard).
            self.hide_keyboard()

    def hide_keyboard(self) -> None:
        self._target = None
        self._kbd.hide()
        self._set_block(False)

    # ------------------------------------------------------------------

    def _on_char(self, ch: str) -> None:
        t = self._target
        if t is None:
            return
        if isinstance(t, QLineEdit):
            t.insert(ch)
        elif isinstance(t, (QPlainTextEdit, QTextEdit)):
            t.insertPlainText(ch)

    def _on_backspace(self) -> None:
        t = self._target
        if t is None:
            return
        if isinstance(t, QLineEdit):
            t.backspace()
        elif isinstance(t, (QPlainTextEdit, QTextEdit)):
            cursor = t.textCursor()
            if cursor.hasSelection():
                cursor.removeSelectedText()
            else:
                cursor.deletePreviousChar()

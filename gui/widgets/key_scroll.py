"""Arrow-key / page-key scrolling for the kiosk.

The 3.5" touch UI scrolls with the mouse wheel out of the box — Qt scroll
areas and item views handle wheel events natively. On the kiosk the only
other input besides touch is a rotary encoder (mapped to Up/Down keys) plus
an optional keyboard, so this controller makes Up/Down/PageUp/PageDown/
Home/End scroll the scroll area of the page currently on screen, regardless
of which widget holds focus (focus is often unset on a kiosk).

It deliberately stays out of the way:
- text fields and value controls (combo, spinbox, slider, dial) keep their
  native arrow behaviour, so editing and tweaking still work;
- item views (lists) keep native arrow navigation (which already scrolls);
- if the active page has nothing scrollable, the keys pass through untouched.

Install once on the main window:
    KeyScrollController(stack, parent=main_window)
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDial,
    QLineEdit,
    QPlainTextEdit,
    QSlider,
    QStackedWidget,
    QTextEdit,
    QWidget,
)

# Keys this controller acts on. Item views / value controls are excluded at
# the focus check below, so these only ever drive plain scrolling.
SCROLL_KEYS = frozenset({
    Qt.Key.Key_Up,
    Qt.Key.Key_Down,
    Qt.Key.Key_PageUp,
    Qt.Key.Key_PageDown,
    Qt.Key.Key_Home,
    Qt.Key.Key_End,
})

# Focused widgets that own the arrow keys — never hijack them.
_ARROW_OWNERS = (
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QComboBox,
    QAbstractSpinBox,
    QSlider,
    QDial,
    QAbstractItemView,
)

# Minimum per-press travel for pixel-scrolled areas, so arrows aren't glacial
# when Qt's singleStep is only a few pixels. ~1/6 of the 320 px panel height.
_MIN_LINE_PX = 48


def next_scroll_value(
    key: int,
    value: int,
    minimum: int,
    maximum: int,
    line_step: int,
    page_step: int,
) -> int | None:
    """Pure helper: new scrollbar value for a navigation key, clamped to range.

    Returns ``None`` for a key that is not a scroll key. No Qt widgets, so it
    is unit-testable in isolation.
    """
    if key == Qt.Key.Key_Home:
        new = minimum
    elif key == Qt.Key.Key_End:
        new = maximum
    elif key == Qt.Key.Key_Up:
        new = value - line_step
    elif key == Qt.Key.Key_Down:
        new = value + line_step
    elif key == Qt.Key.Key_PageUp:
        new = value - page_step
    elif key == Qt.Key.Key_PageDown:
        new = value + page_step
    else:
        return None
    return max(minimum, min(maximum, new))


class KeyScrollController(QObject):
    """Watches key presses app-wide and scrolls the visible page's scroll area."""

    def __init__(self, stack: QStackedWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self._stack = stack
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in SCROLL_KEYS:
            if self._handle(event):
                return True  # consume only when we actually scrolled
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------

    def _handle(self, event) -> bool:
        app = QApplication.instance()
        focus = app.focusWidget() if app is not None else None
        if isinstance(focus, _ARROW_OWNERS):
            return False  # let the focused control use the arrows itself
        area = self._target_area(focus)
        if area is None:
            return False
        bar = area.verticalScrollBar()
        if bar is None or bar.maximum() <= bar.minimum():
            return False  # nothing to scroll on this page
        new = next_scroll_value(
            event.key(),
            bar.value(),
            bar.minimum(),
            bar.maximum(),
            self._line_step(area, bar),
            bar.pageStep(),
        )
        if new is None or new == bar.value():
            return False
        bar.setValue(new)
        return True

    @staticmethod
    def _line_step(area: QAbstractScrollArea, bar) -> int:
        # Per-item areas scroll one row; pixel areas get a comfortable minimum.
        if (
            isinstance(area, QAbstractItemView)
            and area.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerItem
        ):
            return max(1, bar.singleStep())
        return max(bar.singleStep(), _MIN_LINE_PX)

    def _target_area(self, focus: QWidget | None) -> QAbstractScrollArea | None:
        # 1) a scroll area at/above the focused widget
        w = focus
        while w is not None:
            if isinstance(w, QAbstractScrollArea):
                return w
            w = w.parentWidget()
        # 2) fall back to the scroll area of the page currently on screen
        page = self._stack.currentWidget()
        if page is None:
            return None
        if isinstance(page, QAbstractScrollArea):
            return page
        return page.findChild(QAbstractScrollArea)

"""Tests for the pure scroll-value logic behind KeyScrollController.

Follows the project convention of testing GUI logic without booting a
QApplication: only the pure ``next_scroll_value`` helper is exercised here.
Importing ``Qt`` for the key enums does not require a display.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from gui.widgets.key_scroll import SCROLL_KEYS, next_scroll_value


def test_down_advances_by_line_step():
    assert next_scroll_value(Qt.Key.Key_Down, 100, 0, 1000, 48, 300) == 148


def test_up_retreats_by_line_step():
    assert next_scroll_value(Qt.Key.Key_Up, 100, 0, 1000, 48, 300) == 52


def test_page_keys_use_page_step():
    assert next_scroll_value(Qt.Key.Key_PageDown, 100, 0, 1000, 48, 300) == 400
    assert next_scroll_value(Qt.Key.Key_PageUp, 500, 0, 1000, 48, 300) == 200


def test_home_and_end_jump_to_edges():
    assert next_scroll_value(Qt.Key.Key_Home, 500, 0, 1000, 48, 300) == 0
    assert next_scroll_value(Qt.Key.Key_End, 500, 0, 1000, 48, 300) == 1000


def test_clamps_at_top():
    assert next_scroll_value(Qt.Key.Key_Up, 20, 0, 1000, 48, 300) == 0


def test_clamps_at_bottom():
    assert next_scroll_value(Qt.Key.Key_Down, 980, 0, 1000, 48, 300) == 1000


def test_non_scroll_key_returns_none():
    assert next_scroll_value(Qt.Key.Key_A, 100, 0, 1000, 48, 300) is None


def test_scroll_keys_set_matches_handled_keys():
    for key in (
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_PageUp,
        Qt.Key.Key_PageDown,
        Qt.Key.Key_Home,
        Qt.Key.Key_End,
    ):
        assert key in SCROLL_KEYS
        assert next_scroll_value(key, 100, 0, 1000, 48, 300) is not None

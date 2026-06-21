"""Tests for physical-keyboard detection used to suppress the on-screen
keyboard. Pure logic over a mocked filesystem — no QApplication needed."""

from __future__ import annotations

import glob

from gui.widgets.vkb import external_keyboard_present


def test_detects_usb_keyboard_by_id(monkeypatch):
    monkeypatch.setattr(
        glob, "glob",
        lambda pat: ["/dev/input/by-id/usb-Dell_KB216-event-kbd"] if "by-id" in pat else [],
    )
    assert external_keyboard_present() is True


def test_detects_usb_keyboard_by_path(monkeypatch):
    monkeypatch.setattr(
        glob, "glob",
        lambda pat: ["/dev/input/by-path/platform-3f980000.usb-usb-0:1.2:1.0-event-kbd"]
        if "by-path" in pat else [],
    )
    assert external_keyboard_present() is True


def test_no_keyboard_present(monkeypatch):
    # Touchscreen / rotary encoder produce no *-event-kbd symlink.
    monkeypatch.setattr(glob, "glob", lambda pat: [])
    assert external_keyboard_present() is False


def test_glob_error_is_safe(monkeypatch):
    def boom(pat):
        raise OSError("no /dev/input")
    monkeypatch.setattr(glob, "glob", boom)
    assert external_keyboard_present() is False

"""Tests for the pure state helpers behind the status-bar icons.

Only the pure ``power_state`` / ``gps_state`` functions are exercised — no Qt
widget is instantiated, matching the project's no-QApplication test convention.
Importing the module is fine: it only defines classes, it does not build them.
"""

from __future__ import annotations

from gui.widgets.status_icons import gps_state, power_state


# --- power_state (vcgencmd get_throttled bitmask) ---

def test_power_unknown_when_no_reading():
    assert power_state(None) == "unknown"


def test_power_ok_when_zero():
    assert power_state(0x0) == "ok"


def test_power_alert_on_active_condition():
    assert power_state(0x1) == "alert"       # under-voltage now
    assert power_state(0x4) == "alert"       # throttled now
    assert power_state(0x50005) == "alert"   # active bits present alongside past bits


def test_power_warn_when_only_occurred_since_boot():
    assert power_state(0x10000) == "warn"    # under-voltage occurred
    assert power_state(0x40000) == "warn"    # throttling occurred
    assert power_state(0x50000) == "warn"    # both past flags, nothing active


# --- gps_state (fix + satellite count) ---

def test_gps_none_without_fix():
    assert gps_state(False, None) == "none"
    assert gps_state(False, 10) == "none"


def test_gps_good_with_fix_but_unknown_sats():
    assert gps_state(True, None) == "good"


def test_gps_weak_with_few_sats():
    assert gps_state(True, 0) == "weak"
    assert gps_state(True, 3) == "weak"


def test_gps_good_with_enough_sats():
    assert gps_state(True, 4) == "good"
    assert gps_state(True, 12) == "good"

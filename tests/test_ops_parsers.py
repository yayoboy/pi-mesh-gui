"""Pure-logic tests for the new shell-out wrappers.

Anything that touches the network, /sys, or /dev/* is mocked out so
these tests don't need a Raspberry Pi. The intent is to lock down the
fragile parts (regex over config.txt, nmcli ``-t`` escape rules,
i2cdetect grid parsing) so future refactors don't silently break them.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# wifi_ops._split_terse — escape-aware splitter

def test_split_terse_basic():
    from wifi_ops import _split_terse
    assert _split_terse("a:b:c") == ["a", "b", "c"]


def test_split_terse_escaped_colon_preserved():
    from wifi_ops import _split_terse
    # nmcli escapes literal ':' in SSIDs and conn names as '\:'
    assert _split_terse(r"My\:SSID:80:WPA2") == ["My:SSID", "80", "WPA2"]


def test_split_terse_trailing_empty_field():
    from wifi_ops import _split_terse
    assert _split_terse("name:") == ["name", ""]


def test_split_terse_backslash_at_end():
    from wifi_ops import _split_terse
    # Pathological: trailing backslash should not crash.
    assert _split_terse("name\\") == ["name\\"]


# ---------------------------------------------------------------------------
# hardware_ops.i2c_scan — parse i2cdetect grid

def test_i2c_scan_parses_grid(monkeypatch):
    import hardware_ops

    sample = (
        "     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f\n"
        "00:                         -- -- -- -- -- -- -- --\n"
        "10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --\n"
        "20: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --\n"
        "30: -- -- -- -- -- -- -- -- -- -- -- -- 3c -- -- --\n"
        "40: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --\n"
        "50: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --\n"
        "60: -- -- -- -- -- -- -- -- 68 -- -- -- -- -- -- --\n"
        "70: -- -- -- -- -- -- 76 --\n"
    )

    async def _fake_run(*_a, **_kw):
        return 0, sample, ""

    monkeypatch.setattr(hardware_ops, "_run", _fake_run)
    monkeypatch.setattr(hardware_ops.shutil, "which", lambda _: "/usr/sbin/i2cdetect")

    result = asyncio.run(hardware_ops.i2c_scan(1))
    assert result == {"devices": ["0x3c", "0x68", "0x76"]}


def test_i2c_scan_missing_tool(monkeypatch):
    import hardware_ops
    monkeypatch.setattr(hardware_ops.shutil, "which", lambda _: None)
    result = asyncio.run(hardware_ops.i2c_scan(1))
    assert "error" in result
    assert result["devices"] == []


def test_i2c_scan_ignores_uu_reserved(monkeypatch):
    """UU (kernel driver bound) should not show up as a "device" hit."""
    import hardware_ops

    sample = (
        "     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f\n"
        "60: UU -- -- -- -- -- -- -- 68 -- -- -- -- -- -- --\n"
    )

    async def _fake_run(*_a, **_kw):
        return 0, sample, ""

    monkeypatch.setattr(hardware_ops, "_run", _fake_run)
    monkeypatch.setattr(hardware_ops.shutil, "which", lambda _: "/usr/sbin/i2cdetect")
    result = asyncio.run(hardware_ops.i2c_scan(1))
    assert result == {"devices": ["0x68"]}


# ---------------------------------------------------------------------------
# display_ops — config.txt rotation parsing + rewriting

def test_get_state_reads_dtoverlay_rotate(tmp_path, monkeypatch):
    import display_ops

    fake_cfg = tmp_path / "config.txt"
    fake_cfg.write_text(
        "# Display\n"
        "dtoverlay=mpi3501,rotate=90,touch-swapxy=true\n"
        "gpu_mem=64\n"
    )
    monkeypatch.setattr(display_ops, "_config_txt", lambda: fake_cfg)
    # No backlight on the dev machine.
    monkeypatch.setattr(display_ops, "_backlight_devices", lambda: [])

    state = asyncio.run(display_ops.get_state())
    assert state["rotation"] == 90


def test_get_state_falls_back_to_display_rotate(tmp_path, monkeypatch):
    import display_ops

    fake_cfg = tmp_path / "config.txt"
    fake_cfg.write_text("display_rotate=2\n")
    monkeypatch.setattr(display_ops, "_config_txt", lambda: fake_cfg)
    monkeypatch.setattr(display_ops, "_backlight_devices", lambda: [])

    state = asyncio.run(display_ops.get_state())
    assert state["rotation"] == 180


def test_set_rotation_rewrites_dtoverlay(tmp_path, monkeypatch):
    import display_ops

    fake_cfg = tmp_path / "config.txt"
    fake_cfg.write_text(
        "dtoverlay=mpi3501,rotate=0,touch-swapxy=true\n"
        "other=line\n"
    )
    monkeypatch.setattr(display_ops, "_config_txt", lambda: fake_cfg)

    asyncio.run(display_ops.set_rotation(270))
    out = fake_cfg.read_text()
    assert "rotate=270" in out
    assert "rotate=0" not in out
    # The rest of the line / file is untouched.
    assert "touch-swapxy=true" in out
    assert "other=line" in out


def test_set_rotation_adds_display_rotate_when_no_overlay(tmp_path, monkeypatch):
    import display_ops

    fake_cfg = tmp_path / "config.txt"
    fake_cfg.write_text("gpu_mem=64\n")
    monkeypatch.setattr(display_ops, "_config_txt", lambda: fake_cfg)

    asyncio.run(display_ops.set_rotation(180))
    out = fake_cfg.read_text()
    assert "display_rotate=2" in out
    assert "gpu_mem=64" in out


def test_set_rotation_rejects_unknown_angle():
    import display_ops
    with pytest.raises(ValueError):
        asyncio.run(display_ops.set_rotation(45))


# ---------------------------------------------------------------------------
# wifi_ops.scan — parse nmcli -t output

def test_wifi_scan_deduplicates_and_sorts(monkeypatch):
    import wifi_ops

    sample = (
        "HomeNet:78:WPA2\n"
        "Guest:55:--\n"
        "HomeNet:60:WPA2\n"   # weaker duplicate — must be dropped
        ":34:WPA2\n"          # empty SSID — must be skipped
    )

    async def _fake_run(*_a, **_kw):
        return 0, sample, ""

    monkeypatch.setattr(wifi_ops, "_have_nmcli", lambda: True)
    monkeypatch.setattr(wifi_ops, "_run", _fake_run)

    nets = asyncio.run(wifi_ops.scan())
    assert nets == [
        {"ssid": "HomeNet", "signal": 78, "security": "WPA2"},
        {"ssid": "Guest", "signal": 55, "security": "open"},
    ]


def test_wifi_scan_missing_nmcli(monkeypatch):
    import wifi_ops
    monkeypatch.setattr(wifi_ops, "_have_nmcli", lambda: False)
    with pytest.raises(RuntimeError):
        asyncio.run(wifi_ops.scan())


# ---------------------------------------------------------------------------
# hardware_ops.set_serial_port — rewrite config.env

def test_set_serial_port_rewrites_existing_line(tmp_path, monkeypatch):
    import hardware_ops

    fake_env = tmp_path / "config.env"
    fake_env.write_text(
        "# config\n"
        "SERIAL_PATH=/dev/ttyACM0\n"
        "DB_PATH=data/mesh.db\n"
    )
    monkeypatch.setattr(hardware_ops, "_CONFIG_ENV_PATH", fake_env)

    asyncio.run(hardware_ops.set_serial_port("/dev/ttyUSB0"))
    out = fake_env.read_text()
    assert "SERIAL_PATH=/dev/ttyUSB0" in out
    assert "SERIAL_PATH=/dev/ttyACM0" not in out
    assert "DB_PATH=data/mesh.db" in out


def test_set_serial_port_appends_when_absent(tmp_path, monkeypatch):
    import hardware_ops

    fake_env = tmp_path / "config.env"
    fake_env.write_text("DB_PATH=data/mesh.db\n")
    monkeypatch.setattr(hardware_ops, "_CONFIG_ENV_PATH", fake_env)

    asyncio.run(hardware_ops.set_serial_port("/dev/ttyACM1"))
    out = fake_env.read_text()
    assert "SERIAL_PATH=/dev/ttyACM1" in out
    assert "DB_PATH=data/mesh.db" in out


def test_set_serial_port_creates_when_missing(tmp_path, monkeypatch):
    import hardware_ops

    fake_env = tmp_path / "config.env"
    monkeypatch.setattr(hardware_ops, "_CONFIG_ENV_PATH", fake_env)

    asyncio.run(hardware_ops.set_serial_port("/dev/serial/by-id/usb-Heltec"))
    assert fake_env.exists()
    assert "SERIAL_PATH=/dev/serial/by-id/usb-Heltec" in fake_env.read_text()


# ---------------------------------------------------------------------------
# metrics_page._serialize_telemetry_rows — round trip CSV/JSON

def test_serialize_telemetry_csv_header_collects_keys():
    from gui.pages._telemetry_format import serialize_telemetry_rows

    rows = [
        {"ts": 1, "node_id": "!a", "ttype": "device", "data": {"battery_level": 80}},
        {"ts": 2, "node_id": "!a", "ttype": "environment", "data": {"temperature": 21.5}},
    ]
    csv_text = serialize_telemetry_rows(rows, "csv")
    header = csv_text.splitlines()[0]
    assert header == "ts,node_id,ttype,battery_level,temperature"


def test_serialize_telemetry_json_preserves_shape():
    import json
    from gui.pages._telemetry_format import serialize_telemetry_rows

    rows = [{"ts": 1, "node_id": "!a", "ttype": "device", "data": {"battery_level": 80}}]
    j = json.loads(serialize_telemetry_rows(rows, "json"))
    assert j == rows

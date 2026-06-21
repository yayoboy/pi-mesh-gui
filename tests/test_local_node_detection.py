"""Tests for local-node re-detection in meshtasticd_client.

These lock down the fix for the "wrong/stale node shown" bug: the local
identity must be derived from whatever board is actually attached, refresh on
reconnection, and never latch a value from a previous session. No hardware is
needed — the meshtastic interface is faked.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import meshtasticd_client as mc


@pytest.fixture(autouse=True)
def _reset_client_state():
    """Isolate the module-level globals each test mutates."""
    saved = (mc._interface, mc._local_id, dict(mc._node_cache), mc._loop, mc._connected)
    mc._interface = None
    mc._local_id = ""
    mc._node_cache = {}
    mc._loop = None  # skip the threadsafe UI-refresh scheduling
    mc._connected = False
    yield
    (mc._interface, mc._local_id, mc._connected) = (saved[0], saved[1], saved[4])
    mc._node_cache = saved[2]
    mc._loop = saved[3]


def _fake_iface(node_num: int | None = None, my_node_num: int | None = None):
    local_node = SimpleNamespace()
    if node_num is not None:
        local_node.nodeNum = node_num
    my_info = SimpleNamespace()
    if my_node_num is not None:
        my_info.my_node_num = my_node_num
    return SimpleNamespace(localNode=local_node, myInfo=my_info)


def test_derives_id_from_local_node():
    mc._interface = _fake_iface(node_num=0x3B0811EF)
    assert mc._set_local_id_from_interface() == "!3b0811ef"
    assert mc._local_id == "!3b0811ef"


def test_redetects_when_board_changes():
    mc._interface = _fake_iface(node_num=0x3B0811EF)
    mc._set_local_id_from_interface()
    # A different board is now attached: identity must follow it, not stick.
    mc._interface = _fake_iface(node_num=0x00003754)
    assert mc._set_local_id_from_interface() == "!00003754"
    assert mc._local_id == "!00003754"


def test_falls_back_to_my_info_when_local_node_unreadable():
    # localNode has no nodeNum attribute -> AttributeError -> fall back.
    mc._interface = _fake_iface(my_node_num=0x00003754)
    assert mc._set_local_id_from_interface() == "!00003754"


def test_no_interface_keeps_current_value():
    mc._local_id = "!deadbeef"
    mc._interface = None
    assert mc._set_local_id_from_interface() == "!deadbeef"


def test_connection_established_sets_id_and_connected():
    iface = _fake_iface(node_num=0x3B0811EF)
    mc._on_connection_established(interface=iface)
    assert mc._interface is iface
    assert mc._connected is True
    assert mc._local_id == "!3b0811ef"


def test_connection_lost_flips_connected():
    mc._connected = True
    mc._on_connection_lost()
    assert mc._connected is False

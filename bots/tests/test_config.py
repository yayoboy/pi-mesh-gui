"""Tests for ``bots.config.BotsConfig`` load/save round-trips.

The real ``database`` module is replaced with an in-memory fake so we
can verify that values persisted via ``set()`` are read back by
``load()`` — in particular per-bot params like
``bots.beacon.interval_seconds``, which used to silently revert to
defaults after a restart.
"""

from __future__ import annotations

import sys
import types

import pytest

from bots import DEFAULT_ENABLED
from bots.config import BotsConfig


def _fake_database(store: dict[str, str]) -> types.ModuleType:
    mod = types.ModuleType("database")

    async def get_setting(key, default=None):
        return store.get(key, default)

    async def set_setting(key, value):
        store[key] = value

    mod.get_setting = get_setting
    mod.set_setting = set_setting
    return mod


@pytest.fixture
def db_store(monkeypatch) -> dict[str, str]:
    store: dict[str, str] = {}
    monkeypatch.setitem(sys.modules, "database", _fake_database(store))
    return store


@pytest.mark.asyncio
async def test_load_reads_persisted_params(db_store):
    db_store["bots.beacon.interval_seconds"] = "1234"
    cfg = BotsConfig(":memory:")
    await cfg.load(DEFAULT_ENABLED)
    assert cfg.get_param("beacon", "interval_seconds", "600") == "1234"


@pytest.mark.asyncio
async def test_params_survive_restart_roundtrip(db_store):
    cfg = BotsConfig(":memory:")
    await cfg.load(DEFAULT_ENABLED)
    await cfg.set("bots.beacon.interval_seconds", "900")

    # Simulate a process restart: brand-new config object, same DB.
    cfg2 = BotsConfig(":memory:")
    await cfg2.load(DEFAULT_ENABLED)
    assert cfg2.get_param("beacon", "interval_seconds", "600") == "900"


@pytest.mark.asyncio
async def test_load_leaves_unset_params_to_default(db_store):
    cfg = BotsConfig(":memory:")
    await cfg.load(DEFAULT_ENABLED)
    assert cfg.get_param("beacon", "interval_seconds", "600") == "600"


@pytest.mark.asyncio
async def test_load_still_reads_prefix_and_enabled(db_store):
    db_store["bots.prefix"] = "@bot"
    db_store["bots.beacon.enabled"] = "1"
    cfg = BotsConfig(":memory:")
    await cfg.load(DEFAULT_ENABLED)
    assert cfg.prefix == "@bot"
    assert cfg.is_enabled("beacon") is True

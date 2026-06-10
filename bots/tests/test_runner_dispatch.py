"""Unit tests for ``bots.runner`` internals that don't need the radio.

We exercise the pure helpers (``_build_bot_message``, ``_is_dm_for_local``,
``resolve_destination`` integration) and the dispatch fan-out via mock
bots — no asyncio.Queue, no meshtasticd_client.
"""

from __future__ import annotations

import time
from typing import Iterable

import pytest

from bots.base import BotBase, BotMessage, BotReply
from bots.runner import (
    _build_bot_message,
    _is_dm_for_local,
    _safe_on_message,
    _RateLimiter,
)


# --- _build_bot_message --------------------------------------------------

def test_build_bot_message_parses_command_with_prefix():
    NOW = int(time.time())
    event = {"type": "message", "text": "!ping arg1", "from": "!a",
             "channel": 2, "is_dm": False, "ts": NOW}
    msg = _build_bot_message(event, "!", local_id="!local")
    assert msg.command == "ping"
    assert msg.args == ["arg1"]
    assert msg.channel == 2
    assert msg.from_id == "!a"
    assert msg.is_dm is False


def test_build_bot_message_no_prefix_match_yields_command_none():
    event = {"type": "message", "text": "hello", "from": "!a"}
    msg = _build_bot_message(event, "!", local_id="!local")
    assert msg.command is None
    assert msg.args == []


def test_build_bot_message_uses_event_is_dm_first():
    event = {"type": "message", "text": "x", "is_dm": True, "from": "!a"}
    msg = _build_bot_message(event, "!", local_id="!local")
    assert msg.is_dm is True


def test_build_bot_message_falls_back_to_destination_match():
    event = {"type": "message", "text": "x", "from": "!a", "destination": "!local"}
    msg = _build_bot_message(event, "!", local_id="!local")
    assert msg.is_dm is True


def test_build_bot_message_channel_defaults_to_zero():
    event = {"type": "message", "text": "x", "from": "!a"}
    msg = _build_bot_message(event, "!", local_id="!local")
    assert msg.channel == 0


def test_build_bot_message_ts_defaults_to_now_if_missing():
    event = {"type": "message", "text": "x", "from": "!a"}
    before = int(time.time())
    msg = _build_bot_message(event, "!", local_id="!local")
    assert before <= msg.ts <= int(time.time()) + 1


def test_build_bot_message_does_not_fall_back_to_db_row_id():
    # event["id"] is the integer DB row id, not a node id: it must never
    # leak into from_id (it broke the self-echo check / DM destinations).
    event = {"type": "message", "text": "x", "id": 42}
    msg = _build_bot_message(event, "!", local_id="!local")
    assert msg.from_id == ""


# --- _is_dm_for_local ----------------------------------------------------

def test_is_dm_for_local_true_when_destination_matches():
    assert _is_dm_for_local({"destination": "!local"}, "!local") is True


def test_is_dm_for_local_false_when_destination_differs():
    assert _is_dm_for_local({"destination": "!other"}, "!local") is False


def test_is_dm_for_local_false_for_broadcast_marker():
    assert _is_dm_for_local({"destination": "^all"}, "!local") is False


def test_is_dm_for_local_false_when_local_id_missing():
    assert _is_dm_for_local({"destination": "!local"}, "") is False


# --- _safe_on_message ----------------------------------------------------

class _AlwaysReplies(BotBase):
    name = "always"
    async def on_message(self, msg: BotMessage) -> Iterable[BotReply]:
        return (BotReply(text="ok"),)


class _Raises(BotBase):
    name = "boom"
    async def on_message(self, msg: BotMessage) -> Iterable[BotReply]:
        raise RuntimeError("kaboom")


def _msg():
    return BotMessage(
        from_id="!a", text="x", command=None, args=[],
        channel=0, is_dm=False, ts=0,
    )


@pytest.mark.asyncio
async def test_safe_on_message_returns_replies():
    out = await _safe_on_message(_AlwaysReplies(), _msg())
    assert [r.text for r in out] == ["ok"]


@pytest.mark.asyncio
async def test_safe_on_message_swallows_exception():
    out = await _safe_on_message(_Raises(), _msg())
    assert list(out) == []


# --- end-to-end dispatch via _dispatch_one -----------------------------

@pytest.mark.asyncio
async def test_dispatch_runs_every_enabled_bot_and_sends_replies(monkeypatch):
    from bots import config as config_mod
    from bots import runner

    sent: list[tuple[str, str, int]] = []

    class _FakeMC:
        @staticmethod
        def get_local_id() -> str:
            return "!local"

        @staticmethod
        async def send_text(text, dest, channel=0):
            sent.append((text, dest, channel))

    monkeypatch.setitem(__import__("sys").modules, "meshtasticd_client", _FakeMC)

    class _Cfg:
        prefix = "!"

        def __init__(self):
            self._enabled = {"always": True, "boom": True, "off": False}

        def is_enabled(self, name: str) -> bool:
            return self._enabled.get(name, False)

    fake_cfg = _Cfg()

    class _OffBot(BotBase):
        name = "off"
        async def on_message(self, msg: BotMessage) -> Iterable[BotReply]:
            return (BotReply(text="should-not-send"),)

    monkeypatch.setattr(runner._state, "bots",
                        [_AlwaysReplies(), _Raises(), _OffBot()])
    monkeypatch.setattr(runner._state, "config", fake_cfg)
    monkeypatch.setattr(runner, "_rate_limiter", _RateLimiter())

    msg = BotMessage(
        from_id="!sender", text="!always", command="always", args=[],
        channel=3, is_dm=False, ts=0,
    )

    await runner._dispatch_one(msg)

    # AlwaysReplies → "ok" broadcast on channel 3.
    assert ("ok", "^all", 3) in sent
    # Raises → swallowed (no send).
    # Off bot → not invoked.
    assert all(t[0] != "should-not-send" for t in sent)


# --- rate limiting --------------------------------------------------------

class _FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_rate_limiter_per_sender_cooldown():
    clock = _FakeClock()
    rl = _RateLimiter(cooldown=10.0, window=60.0, max_per_window=100,
                      clock=clock)
    assert rl.allow("!a") is True
    assert rl.allow("!a") is False           # within cooldown
    clock.advance(9.9)
    assert rl.allow("!a") is False           # still within cooldown
    clock.advance(0.2)
    assert rl.allow("!a") is True            # cooldown expired


def test_rate_limiter_cooldown_is_per_sender():
    clock = _FakeClock()
    rl = _RateLimiter(cooldown=10.0, window=60.0, max_per_window=100,
                      clock=clock)
    assert rl.allow("!a") is True
    assert rl.allow("!b") is True            # different sender unaffected


def test_rate_limiter_global_cap_and_window_expiry():
    clock = _FakeClock()
    rl = _RateLimiter(cooldown=0.0, window=60.0, max_per_window=3,
                      clock=clock)
    senders = [f"!s{i}" for i in range(5)]
    grants = [rl.allow(s) for s in senders]
    assert grants == [True, True, True, False, False]  # capped at 3/window
    clock.advance(60.0)
    assert rl.allow("!s9") is True           # old grants aged out


@pytest.mark.asyncio
async def test_dispatch_drops_rate_limited_commands(monkeypatch):
    from bots import runner

    sent: list[tuple[str, str, int]] = []

    class _FakeMC:
        @staticmethod
        async def send_text(text, dest, channel=0):
            sent.append((text, dest, channel))

    monkeypatch.setitem(__import__("sys").modules, "meshtasticd_client", _FakeMC)

    class _Cfg:
        prefix = "!"

        def is_enabled(self, name: str) -> bool:
            return True

    monkeypatch.setattr(runner._state, "bots", [_AlwaysReplies()])
    monkeypatch.setattr(runner._state, "config", _Cfg())
    clock = _FakeClock()
    monkeypatch.setattr(runner, "_rate_limiter",
                        _RateLimiter(cooldown=10.0, window=60.0,
                                     max_per_window=100, clock=clock))

    def cmd_msg(sender: str) -> BotMessage:
        return BotMessage(
            from_id=sender, text="!always", command="always", args=[],
            channel=0, is_dm=False, ts=0,
        )

    await runner._dispatch_one(cmd_msg("!spammer"))
    await runner._dispatch_one(cmd_msg("!spammer"))   # silently dropped
    assert len(sent) == 1

    clock.advance(10.1)
    await runner._dispatch_one(cmd_msg("!spammer"))   # cooldown expired
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_dispatch_does_not_rate_limit_non_command_messages(monkeypatch):
    from bots import runner

    sent: list[tuple[str, str, int]] = []

    class _FakeMC:
        @staticmethod
        async def send_text(text, dest, channel=0):
            sent.append((text, dest, channel))

    monkeypatch.setitem(__import__("sys").modules, "meshtasticd_client", _FakeMC)

    class _Cfg:
        prefix = "!"

        def is_enabled(self, name: str) -> bool:
            return True

    monkeypatch.setattr(runner._state, "bots", [_AlwaysReplies()])
    monkeypatch.setattr(runner._state, "config", _Cfg())
    monkeypatch.setattr(runner, "_rate_limiter",
                        _RateLimiter(cooldown=10.0, window=60.0,
                                     max_per_window=100, clock=_FakeClock()))

    plain = BotMessage(
        from_id="!a", text="hello", command=None, args=[],
        channel=0, is_dm=False, ts=0,
    )
    await runner._dispatch_one(plain)
    await runner._dispatch_one(plain)
    assert len(sent) == 2  # plain chat doesn't burn the sender's cooldown


# --- _message_loop drops events without a sender ------------------------

@pytest.mark.asyncio
async def test_message_loop_skips_events_without_from(monkeypatch):
    import asyncio

    from bots import runner

    dispatched: list[BotMessage] = []

    class _FakeMC:
        @staticmethod
        def get_local_id() -> str:
            return "!local"

    monkeypatch.setitem(__import__("sys").modules, "meshtasticd_client", _FakeMC)

    class _Cfg:
        prefix = "!"

        def is_enabled(self, name: str) -> bool:
            return True

    async def fake_dispatch(msg: BotMessage) -> None:
        dispatched.append(msg)

    queue: asyncio.Queue = asyncio.Queue()
    monkeypatch.setattr(runner._state, "queue", queue)
    monkeypatch.setattr(runner._state, "config", _Cfg())
    monkeypatch.setattr(runner, "_dispatch_one", fake_dispatch)

    # No "from" (only the DB row id) → must be skipped, not dispatched.
    queue.put_nowait({"type": "message", "text": "!ping", "id": 7})
    # Self-echo → skipped.
    queue.put_nowait({"type": "message", "text": "!ping", "from": "!local"})
    # Valid remote sender → dispatched.
    queue.put_nowait({"type": "message", "text": "!ping", "from": "!remote"})

    task = asyncio.get_running_loop().create_task(runner._message_loop())
    try:
        # Let the loop drain the queue.
        for _ in range(100):
            if queue.empty() and dispatched:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert [m.from_id for m in dispatched] == ["!remote"]

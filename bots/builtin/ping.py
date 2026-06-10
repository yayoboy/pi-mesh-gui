"""!ping → pong.

Note: we deliberately make no latency claim. ``msg.ts`` is stamped when
the packet is received locally, so any delta measured here is internal
queue latency, not radio transit time.
"""

from __future__ import annotations

from typing import Iterable

from bots.base import BotBase, BotMessage, BotReply


class PingBot(BotBase):
    name = "ping"
    description = "Risponde a !ping con 'pong'."
    default_enabled = True

    async def on_message(self, msg: BotMessage) -> Iterable[BotReply]:
        if not self.matches(msg, "ping"):
            return ()
        return (BotReply(text="pong"),)

"""Helper to schedule coroutines on the qasync event loop from Qt slots.

Qt slots are sync and cannot ``await``; the GUI thread runs the same asyncio
loop as the rest of the backend (via qasync), so every page used to grow its
own copy of:

    def _schedule(coro):
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(coro)

The policy-based lookup is deprecated since Python 3.10 and the body was
duplicated in ~9 files. This module exposes a single ``schedule(coro)`` that
uses ``asyncio.get_running_loop`` (which is always valid under qasync) and
gracefully no-ops with a warning if called outside a loop, e.g. from a unit
test that mounts a widget without an event loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Coroutine

log = logging.getLogger(__name__)


# Strong references to in-flight tasks. The event loop only keeps weak refs
# to tasks, so without this a scheduled task can be garbage-collected before
# it finishes; it also lets us log exceptions when they happen rather than
# at GC time. Mirrors the pattern used for the background tasks in gui.app.
_scheduled: set[asyncio.Task] = set()


def _on_task_done(task: asyncio.Task) -> None:
    _scheduled.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("scheduled task %s crashed", task.get_name())


def schedule(coro: Coroutine) -> asyncio.Task | None:
    """Schedule ``coro`` on the running qasync loop.

    Returns the created ``Task`` or ``None`` if no loop is running (the
    coroutine is closed in that case so the interpreter does not warn about
    a never-awaited coroutine).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warning("schedule(): no running loop, dropping %r", coro)
        coro.close()
        return None
    task = loop.create_task(coro)
    _scheduled.add(task)
    task.add_done_callback(_on_task_done)
    return task

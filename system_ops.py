"""System-level helpers shelled out from the GUI.

These functions used to live behind the FastAPI ``/api/system/*`` routes.
Now they're called directly from Qt slots via short ``asyncio.create_subprocess_exec``
wrappers so the event loop never blocks on shell IO.

All functions are idempotent and intentionally narrow — they wrap
``systemctl`` / filesystem operations and nothing more. Higher-level
policy (confirmation dialogs, double-confirms) lives in the GUI.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)


async def _run(*argv: str, timeout: float = 30.0) -> tuple[int, str, str]:
    """Run a command, return ``(rc, stdout, stderr)`` with text decoded."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "timeout"
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def reboot() -> None:
    """Reboot the host. ``systemctl`` handles the privileged side."""
    rc, _out, err = await _run("sudo", "systemctl", "reboot")
    if rc != 0:
        raise RuntimeError(f"reboot failed: {err.strip() or rc}")


async def shutdown() -> None:
    """Power off the host."""
    rc, _out, err = await _run("sudo", "systemctl", "poweroff")
    if rc != 0:
        raise RuntimeError(f"shutdown failed: {err.strip() or rc}")


async def pi_factory_reset(db_path: str) -> None:
    """Wipe the local pi-Mesh state and reboot.

    - Removes ``DB_PATH`` (and its WAL/SHM siblings).
    - Removes cached data folders (exports, screenshots).
    - Leaves map tiles intact (they're expensive to redownload).
    - Issues a reboot so the GUI service comes up clean.
    """
    db = Path(db_path)
    for ext in ("", "-wal", "-shm"):
        p = Path(str(db) + ext)
        try:
            p.unlink(missing_ok=True)
        except Exception:
            log.exception("could not remove %s", p)

    for sub in ("data/exports", "data/screenshots"):
        d = Path(sub)
        if d.exists():
            try:
                shutil.rmtree(d)
            except Exception:
                log.exception("could not remove %s", d)

    await reboot()

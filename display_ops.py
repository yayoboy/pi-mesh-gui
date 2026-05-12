"""Display (backlight + rotation) helpers for the Pi kiosk.

Backlight: the standard kernel exposes any panel that supports PWM
under ``/sys/class/backlight/<name>/brightness``. This includes the
official 7" touchscreen (``10-0045``) and most ``fbtft`` SPI panels
configured with a backlight pin (``rpi_backlight``). We write through
to whichever device we find first.

Rotation: framebuffer / X11 / Wayland can each be rotated, but the
3.5" SPI panels in this project are rotated at the dtoverlay level
in ``/boot/firmware/config.txt`` (or ``/boot/config.txt`` on Bullseye).
We rewrite the ``rotate=`` parameter on the existing ``dtoverlay=``
line and signal that a reboot is required. If no overlay is present
we fall back to writing ``display_rotate=`` for HDMI panels.

Both functions are async because they ultimately shell out to sudo,
but the IO itself is tiny.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


_BACKLIGHT_ROOT = Path("/sys/class/backlight")
_CONFIG_TXT_CANDIDATES = (
    Path("/boot/firmware/config.txt"),  # Bookworm+
    Path("/boot/config.txt"),           # Bullseye and earlier
)


def _backlight_devices() -> list[Path]:
    if not _BACKLIGHT_ROOT.exists():
        return []
    return sorted(p for p in _BACKLIGHT_ROOT.iterdir() if (p / "brightness").exists())


def _config_txt() -> Path | None:
    for p in _CONFIG_TXT_CANDIDATES:
        if p.exists():
            return p
    return None


async def _run(*argv: str, input_bytes: bytes | None = None, timeout: float = 10.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(input_bytes), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "timeout"
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Read

async def get_state() -> dict:
    """Return ``{'brightness': int, 'max_brightness': int, 'rotation': int}``.

    Values default to safe placeholders when the underlying file isn't
    readable (e.g., running on a desktop dev machine).
    """
    state: dict[str, int] = {"brightness": 255, "max_brightness": 255, "rotation": 0}

    devs = _backlight_devices()
    if devs:
        dev = devs[0]
        try:
            state["brightness"] = int((dev / "brightness").read_text().strip())
        except Exception:
            log.debug("read brightness failed", exc_info=True)
        try:
            state["max_brightness"] = int((dev / "max_brightness").read_text().strip())
        except Exception:
            log.debug("read max_brightness failed", exc_info=True)

    cfg = _config_txt()
    if cfg is not None:
        try:
            text = cfg.read_text()
            # First, look for rotate=N inside any dtoverlay= line (SPI panels).
            m = re.search(r"^dtoverlay=[^\n]*\brotate=(\d+)", text, re.MULTILINE)
            if m:
                state["rotation"] = int(m.group(1))
            else:
                # Fallback: HDMI ``display_rotate=`` value.
                m = re.search(r"^display_rotate=(\d+)", text, re.MULTILINE)
                if m:
                    rot_n = int(m.group(1))
                    # display_rotate uses 0=0°, 1=90°, 2=180°, 3=270°.
                    state["rotation"] = (rot_n * 90) % 360
        except Exception:
            log.debug("read config.txt failed", exc_info=True)

    return state


# ---------------------------------------------------------------------------
# Brightness

async def set_brightness(value: int) -> None:
    """Clamp ``value`` to the panel's ``max_brightness`` and write it."""
    devs = _backlight_devices()
    if not devs:
        raise RuntimeError("no backlight device under /sys/class/backlight")
    dev = devs[0]
    try:
        max_b = int((dev / "max_brightness").read_text().strip())
    except Exception:
        max_b = 255
    v = max(0, min(int(value), max_b))
    target = dev / "brightness"
    # Try direct write first; if EPERM, fall back to sudo tee.
    try:
        target.write_text(str(v))
        return
    except PermissionError:
        pass
    rc, _out, err = await _run("sudo", "tee", str(target), input_bytes=str(v).encode())
    if rc != 0:
        raise RuntimeError(f"set brightness failed: {err.strip() or rc}")


# ---------------------------------------------------------------------------
# Rotation

_VALID_ROTATIONS = (0, 90, 180, 270)


async def set_rotation(deg: int) -> None:
    """Persist ``deg`` rotation. Reboot is required to take effect.

    Rewrites the ``rotate=`` argument on any existing ``dtoverlay=`` line
    in ``config.txt``. If no overlay is present, sets
    ``display_rotate=`` instead (HDMI panels).
    """
    if int(deg) not in _VALID_ROTATIONS:
        raise ValueError(f"rotation must be one of {_VALID_ROTATIONS}")
    cfg = _config_txt()
    if cfg is None:
        raise RuntimeError("config.txt not found")

    text = cfg.read_text()
    deg = int(deg)

    new_text, n = re.subn(
        r"^(dtoverlay=[^\n]*?\brotate=)\d+",
        lambda m: f"{m.group(1)}{deg}",
        text,
        flags=re.MULTILINE,
    )
    if n == 0:
        # No overlay with rotate=, write display_rotate= for HDMI.
        rot_n = (deg // 90) & 0x3
        if re.search(r"^display_rotate=", text, re.MULTILINE):
            new_text = re.sub(
                r"^display_rotate=\d+",
                f"display_rotate={rot_n}",
                text,
                flags=re.MULTILINE,
            )
        else:
            new_text = text.rstrip() + f"\ndisplay_rotate={rot_n}\n"

    if new_text == text:
        return

    # Direct write requires root; tee via sudo otherwise.
    try:
        cfg.write_text(new_text)
        return
    except PermissionError:
        pass
    rc, _out, err = await _run("sudo", "tee", str(cfg), input_bytes=new_text.encode())
    if rc != 0:
        raise RuntimeError(f"set rotation failed: {err.strip() or rc}")

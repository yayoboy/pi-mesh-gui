"""Hardware-side helpers: I2C scan, RTC status, serial ports, GPIO test.

These used to live behind FastAPI routes in the deleted main.py.
Everything here is async-safe — anything that may block (subprocess,
gpio actuation) runs via ``asyncio.create_subprocess_exec`` or in a
default-loop executor.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


async def _run(*argv: str, timeout: float = 10.0) -> tuple[int, str, str]:
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


# ---------------------------------------------------------------------------
# I2C scan — ``i2cdetect -y <bus>``

async def i2c_scan(bus: int = 1) -> dict:
    """Return ``{'devices': ['0x3c', '0x68', ...]}`` for occupied addresses.

    Falls back to ``{'devices': [], 'error': ...}`` when ``i2cdetect`` is
    missing (apt-get install i2c-tools) or the bus is unavailable.
    """
    if not shutil.which("i2cdetect"):
        return {"devices": [], "error": "i2cdetect not installed (apt-get install i2c-tools)"}
    rc, out, err = await _run("i2cdetect", "-y", str(int(bus)))
    if rc != 0:
        return {"devices": [], "error": err.strip() or f"i2cdetect rc={rc}"}
    devices: list[str] = []
    # The grid skips the header row and the first column (the row label).
    for line in out.splitlines()[1:]:
        if ":" not in line:
            continue
        cells = line.split(":", 1)[1].split()
        for cell in cells:
            if cell in ("--", "UU"):
                continue
            if re.fullmatch(r"[0-9a-fA-F]{2}", cell):
                devices.append(f"0x{cell.lower()}")
    return {"devices": devices}


# ---------------------------------------------------------------------------
# RTC — /sys/class/rtc/rtc0 + i2cdetect probe

_RTC_KNOWN_MODELS = {
    0x68: "ds1307 / ds3231",
    0x51: "pcf8523",
    0x32: "pcf8563",
}


async def rtc_status() -> dict:
    """Return ``{configured, model, device, time}`` for the system RTC."""
    rtc_root = Path("/sys/class/rtc")
    rtc_dev: Path | None = None
    if rtc_root.exists():
        for p in sorted(rtc_root.iterdir()):
            if (p / "name").exists():
                rtc_dev = p
                break

    configured = rtc_dev is not None
    model = "—"
    device = "—"
    time_str = "—"

    if rtc_dev is not None:
        try:
            model = (rtc_dev / "name").read_text().strip()
        except Exception:
            pass
        device = f"/dev/{rtc_dev.name}"
        if shutil.which("hwclock"):
            rc, out, _err = await _run("sudo", "hwclock", "--show", "--utc")
            if rc == 0 and out.strip():
                time_str = out.strip()
        if time_str == "—":
            time_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        # No /sys/class/rtc node — probe for a chip on i2c-1.
        scan = await i2c_scan(1)
        for addr in scan.get("devices") or []:
            n = int(addr, 16)
            if n in _RTC_KNOWN_MODELS:
                model = _RTC_KNOWN_MODELS[n]
                configured = False  # detected but no kernel driver bound
                break

    return {
        "configured": configured,
        "model": model,
        "device": device,
        "time": time_str,
    }


# ---------------------------------------------------------------------------
# Serial ports

_CONFIG_ENV_PATH = Path("config.env")


def _list_serial_paths() -> list[str]:
    paths = set()
    for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*", "/dev/serial/by-id/*"):
        for p in glob.glob(pattern):
            paths.add(p)
    return sorted(paths)


def _read_current_serial() -> str:
    # Prefer the live SERIAL_PATH from the imported config module.
    try:
        import config as cfg_mod
        return cfg_mod.SERIAL_PATH
    except Exception:
        return os.environ.get("SERIAL_PATH", "/dev/ttyACM0")


async def serial_ports() -> dict:
    return {"ports": _list_serial_paths(), "current": _read_current_serial()}


async def set_serial_port(port: str) -> None:
    """Persist ``SERIAL_PATH=<port>`` in ``config.env`` (creates if missing)."""
    if not port:
        raise ValueError("port required")
    line = f"SERIAL_PATH={port}"
    if _CONFIG_ENV_PATH.exists():
        text = _CONFIG_ENV_PATH.read_text()
        new_text, n = re.subn(
            r"^SERIAL_PATH=.*$", line, text, count=1, flags=re.MULTILINE,
        )
        if n == 0:
            new_text = text.rstrip() + f"\n{line}\n"
    else:
        new_text = line + "\n"
    try:
        _CONFIG_ENV_PATH.write_text(new_text)
    except PermissionError:
        rc, _out, err = await _run("sudo", "tee", str(_CONFIG_ENV_PATH))
        if rc != 0:
            raise RuntimeError(f"could not write config.env: {err.strip() or rc}")


# ---------------------------------------------------------------------------
# GPIO test — pulse an output pin briefly so the user can verify wiring

def _do_gpio_test_sync(device: dict) -> str:
    """Run inside an executor. Imports ``gpiozero`` lazily."""
    try:
        from gpiozero import OutputDevice
    except Exception as exc:
        return f"gpiozero unavailable: {exc}"

    pin = device.get("pin_a")
    if pin is None:
        return "device has no pin_a configured"
    action = (device.get("action") or "pulse").lower()
    duration = 0.4
    try:
        with OutputDevice(int(pin), active_high=True, initial_value=False) as out:
            import time
            if action in ("pulse", "blink"):
                for _ in range(3):
                    out.on()
                    time.sleep(duration)
                    out.off()
                    time.sleep(duration)
                return f"pulsed pin {pin} ×3"
            if action == "on":
                out.on()
                time.sleep(duration)
                return f"set pin {pin} HIGH"
            if action == "off":
                out.off()
                time.sleep(duration)
                return f"set pin {pin} LOW"
            return f"unknown action {action!r}"
    except Exception as exc:
        return f"test failed: {exc}"


async def gpio_test(device: dict) -> dict:
    """Actuate ``device['pin_a']`` according to ``device['action']``."""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _do_gpio_test_sync, device)
    return {"result": result}

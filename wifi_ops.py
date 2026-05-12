"""Wi-Fi helpers wrapping nmcli.

The GUI used to POST to ``/api/config/wifi/*`` endpoints that lived in
the deleted ``main.py``. Now we shell out to ``nmcli`` directly via
``asyncio.create_subprocess_exec``. All commands are run with the
``-t`` flag for terse, colon-separated, script-friendly output.

NetworkManager is the supported network stack on Raspberry Pi OS
Bookworm; on Bullseye it's available via ``raspi-config`` →
"Advanced Options" → "Network config". If nmcli isn't installed,
every helper raises ``RuntimeError`` with a clear message.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

log = logging.getLogger(__name__)


def _have_nmcli() -> bool:
    return shutil.which("nmcli") is not None


async def _run(*argv: str, timeout: float = 30.0) -> tuple[int, str, str]:
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


def _split_terse(line: str) -> list[str]:
    """Split a nmcli ``-t`` row, respecting ``\\:`` escapes."""
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            buf.append(line[i + 1])
            i += 2
            continue
        if ch == ":":
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _require_nmcli() -> None:
    if not _have_nmcli():
        raise RuntimeError("nmcli not installed")


# ---------------------------------------------------------------------------
# Interface discovery

async def _wifi_interface() -> str:
    """First DEVICE whose TYPE is ``wifi``."""
    _require_nmcli()
    rc, out, err = await _run("nmcli", "-t", "-f", "DEVICE,TYPE", "dev")
    if rc != 0:
        raise RuntimeError(f"nmcli dev failed: {err.strip()}")
    for line in out.splitlines():
        parts = _split_terse(line)
        if len(parts) >= 2 and parts[1] == "wifi":
            return parts[0]
    raise RuntimeError("no wifi device found")


# ---------------------------------------------------------------------------
# Scan

async def scan(rescan: bool = True) -> list[dict]:
    """Return ``[{ssid, signal, security}]`` for nearby networks."""
    _require_nmcli()
    rc, out, err = await _run(
        "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
        "--rescan", "yes" if rescan else "no",
        timeout=20.0,
    )
    if rc != 0:
        raise RuntimeError(f"nmcli wifi list failed: {err.strip()}")
    seen: set[str] = set()
    networks: list[dict] = []
    for line in out.splitlines():
        parts = _split_terse(line)
        if len(parts) < 3:
            continue
        ssid, signal, sec = parts[0], parts[1], parts[2]
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        try:
            sig_int = int(signal)
        except ValueError:
            sig_int = 0
        networks.append({
            "ssid": ssid,
            "signal": sig_int,
            "security": "open" if sec in ("", "--") else sec,
        })
    networks.sort(key=lambda n: -n["signal"])
    return networks


# ---------------------------------------------------------------------------
# Status

async def status() -> dict:
    """Return ``{ssid, ip}`` of the active wifi connection (empty if none)."""
    _require_nmcli()
    try:
        iface = await _wifi_interface()
    except RuntimeError:
        return {"ssid": "", "ip": ""}

    rc, out, _err = await _run(
        "nmcli", "-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS", "dev", "show", iface,
    )
    if rc != 0:
        return {"ssid": "", "ip": ""}
    conn_name = ""
    ip = ""
    for line in out.splitlines():
        parts = _split_terse(line)
        if len(parts) < 2:
            continue
        key, val = parts[0], parts[1]
        if key == "GENERAL.CONNECTION":
            conn_name = val if val and val != "--" else ""
        elif key.startswith("IP4.ADDRESS") and not ip:
            ip = val.split("/")[0]

    ssid = ""
    if conn_name:
        # Pull 802-11-wireless.ssid out of the connection profile.
        rc2, out2, _err = await _run(
            "nmcli", "-t", "-s", "-f", "802-11-wireless.ssid",
            "connection", "show", conn_name,
        )
        if rc2 == 0:
            for line in out2.splitlines():
                parts = _split_terse(line)
                if len(parts) >= 2 and parts[0] == "802-11-wireless.ssid":
                    ssid = parts[1]
                    break
    return {"ssid": ssid, "ip": ip}


# ---------------------------------------------------------------------------
# Connect

async def connect(ssid: str, password: str) -> None:
    _require_nmcli()
    if not ssid:
        raise ValueError("ssid is required")
    args = ["nmcli", "dev", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    rc, _out, err = await _run(*args, timeout=45.0)
    if rc != 0:
        raise RuntimeError(err.strip() or f"connect failed (rc={rc})")


# ---------------------------------------------------------------------------
# Saved profiles

async def saved() -> list[dict]:
    """Return ``[{name}]`` for all 802-11-wireless saved connections."""
    _require_nmcli()
    rc, out, err = await _run("nmcli", "-t", "-f", "NAME,TYPE", "connection", "show")
    if rc != 0:
        raise RuntimeError(f"nmcli connection show failed: {err.strip()}")
    result = []
    for line in out.splitlines():
        parts = _split_terse(line)
        if len(parts) >= 2 and parts[1] == "802-11-wireless":
            result.append({"name": parts[0]})
    return result


async def forget(name: str) -> None:
    _require_nmcli()
    if not name:
        raise ValueError("name is required")
    rc, _out, err = await _run("nmcli", "connection", "delete", name)
    if rc != 0:
        raise RuntimeError(err.strip() or f"delete failed (rc={rc})")


# ---------------------------------------------------------------------------
# Static IP

async def set_ip(method: str, address: str = "", gateway: str = "", dns: str = "") -> None:
    """Apply ipv4.method = ``auto`` | ``manual`` to the active wifi profile."""
    _require_nmcli()
    if method not in ("auto", "manual"):
        raise ValueError("method must be 'auto' or 'manual'")
    st = await status()
    iface = await _wifi_interface()
    rc, out, _err = await _run(
        "nmcli", "-t", "-f", "GENERAL.CONNECTION", "dev", "show", iface,
    )
    conn_name = ""
    if rc == 0:
        for line in out.splitlines():
            parts = _split_terse(line)
            if len(parts) >= 2 and parts[0] == "GENERAL.CONNECTION":
                conn_name = parts[1]
                break
    if not conn_name or conn_name == "--":
        raise RuntimeError("no active wifi connection")

    args = ["nmcli", "connection", "modify", conn_name, "ipv4.method", method]
    if method == "manual":
        if not address:
            raise ValueError("address required for manual method")
        args += ["ipv4.addresses", address]
        if gateway:
            args += ["ipv4.gateway", gateway]
        if dns:
            args += ["ipv4.dns", dns.replace(",", " ")]
    rc, _out, err = await _run(*args)
    if rc != 0:
        raise RuntimeError(err.strip() or f"modify failed (rc={rc})")

    # Bring it back up so the change takes effect immediately.
    rc, _out, err = await _run("nmcli", "connection", "up", conn_name, timeout=30.0)
    if rc != 0:
        log.warning("nmcli connection up returned %d: %s", rc, err.strip())


# ---------------------------------------------------------------------------
# Access Point mode

async def ap_status() -> dict:
    """Return ``{active: bool, ssid: str}`` for an ``ap``-mode connection."""
    _require_nmcli()
    rc, out, _err = await _run("nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active")
    if rc != 0:
        return {"active": False, "ssid": ""}
    for line in out.splitlines():
        parts = _split_terse(line)
        if len(parts) >= 2 and parts[1] == "802-11-wireless":
            # Check mode for this connection.
            rc2, out2, _e = await _run(
                "nmcli", "-t", "-f", "802-11-wireless.mode",
                "connection", "show", parts[0],
            )
            if rc2 == 0 and any(p.endswith(":ap") for p in out2.splitlines()):
                return {"active": True, "ssid": parts[0]}
    return {"active": False, "ssid": ""}


async def ap_toggle() -> bool:
    """Toggle the saved AP profile up/down. Returns the new state.

    Picks the first saved 802-11-wireless connection whose
    ``802-11-wireless.mode`` is ``ap``. If none is configured, raises.
    """
    _require_nmcli()
    # Find an AP-mode saved profile.
    rc, out, err = await _run("nmcli", "-t", "-f", "NAME,TYPE", "connection", "show")
    if rc != 0:
        raise RuntimeError(f"connection show failed: {err.strip()}")
    ap_name = ""
    for line in out.splitlines():
        parts = _split_terse(line)
        if len(parts) >= 2 and parts[1] == "802-11-wireless":
            rc2, out2, _e = await _run(
                "nmcli", "-t", "-f", "802-11-wireless.mode",
                "connection", "show", parts[0],
            )
            if rc2 == 0:
                for r in out2.splitlines():
                    pp = _split_terse(r)
                    if len(pp) >= 2 and pp[0] == "802-11-wireless.mode" and pp[1] == "ap":
                        ap_name = parts[0]
                        break
        if ap_name:
            break
    if not ap_name:
        raise RuntimeError("no AP-mode profile configured (create one with nmcli con add ... type wifi mode ap)")

    # Is it currently active?
    rc, out, _err = await _run(
        "nmcli", "-t", "-f", "NAME", "connection", "show", "--active",
    )
    active_names = {_split_terse(l)[0] for l in out.splitlines() if l}
    if ap_name in active_names:
        rc, _out, err = await _run("nmcli", "connection", "down", ap_name)
        if rc != 0:
            raise RuntimeError(err.strip())
        return False
    rc, _out, err = await _run("nmcli", "connection", "up", ap_name, timeout=30.0)
    if rc != 0:
        raise RuntimeError(err.strip())
    return True

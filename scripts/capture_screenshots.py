"""Offscreen screenshot tool for the README and docs/.

Runs a second, transient ``MainWindow`` against the same SQLite database
that the live ``pimesh-gui`` service uses, *without* touching the radio
serial port. Each visible tab is selected programmatically and grabbed
into a PNG.

Typical invocation on the Pi::

    sudo QT_QPA_PLATFORM=offscreen python3 scripts/capture_screenshots.py \
        --out docs/screenshots --theme dark

The script imports the real GUI modules, so any layout drift is reflected
in the captured PNGs the next time you re-run it. It does **not** touch
``pimesh-gui.service`` — the offscreen window lives only as long as this
process.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def _patch_meshtasticd_client(db_path: str) -> None:
    """Stub the meshtasticd_client so node-page widgets have data to draw
    without opening the serial port (which is owned by the live service).
    """
    import asyncio as _asyncio
    import sqlite3

    import meshtasticd_client as mc

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    nodes: dict[str, dict] = {}
    try:
        for r in con.execute("SELECT * FROM nodes"):
            d = dict(r)
            nid = d.get("id") or d.get("node_id")
            if not nid:
                continue
            d["id"] = nid
            d["is_local"] = bool(d.get("is_local"))
            nodes[nid] = d
    except sqlite3.OperationalError:
        pass

    mc._node_cache = nodes
    mc._connected = True
    local = next((n for n in nodes.values() if n.get("is_local")), None)
    mc._local_id = (local or {}).get("id", "") if local else ""

    # Block the connect / disconnect coroutines so the qasync loop has no
    # pending serial work.
    async def _noop_connect() -> None:
        return None

    async def _noop_disconnect() -> None:
        return None

    mc.connect = _noop_connect  # type: ignore[assignment]
    mc.disconnect = _noop_disconnect  # type: ignore[assignment]
    mc.subscribe_events = lambda maxsize=500: _asyncio.Queue(maxsize=maxsize)  # type: ignore[assignment]


def _setup_qapplication(theme: str):
    import gui._qt_shim  # noqa: F401
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    from gui.app import apply_theme

    apply_theme(app, theme)
    return app


async def _async_main(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    _patch_meshtasticd_client(args.db)
    app = _setup_qapplication(args.theme)

    # Set up the same backend bits MainWindow expects.
    import config as cfg
    import database

    cfg.DB_PATH = args.db
    await database.init(args.db)

    from gui.core.eventbus import EventBus
    from gui.core.settings import init_from_database
    from gui.main_window import MainWindow

    settings = await init_from_database(args.db)
    # Force the requested theme into the in-memory cache so subscribers fire.
    settings.set("display.theme", args.theme)

    bus = EventBus()
    window = MainWindow()
    window.attach(bus, settings)
    window.show()
    # Let the layout settle before we start grabbing tabs.
    await asyncio.sleep(0.3)

    # Tab indices match the order in gui.main_window._TABS.
    tabs = [
        (0, "nodes",    "Nodi"),
        (1, "map",      "Mappa"),
        (2, "messages", "Messaggi"),
        (3, "config",   "Config"),
        (4, "metrics",  "Metriche"),
        (5, "log",      "Log"),
    ]
    for idx, slug, _title in tabs:
        window._select_tab(idx)
        # Some pages defer their first refresh to a background task; give
        # them one full tick to populate before grabbing.
        for _ in range(8):
            await asyncio.sleep(0.1)
            app.processEvents()
        path = out_dir / f"{args.theme}-{slug}.png"
        pixmap = window.grab()
        pixmap.save(str(path), "PNG")
        print(f"saved {path} ({pixmap.width()}x{pixmap.height()})")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/mesh.db",
                    help="SQLite database path (default: data/mesh.db).")
    ap.add_argument("--out", default="docs/screenshots",
                    help="Output directory for the PNGs.")
    ap.add_argument("--theme", default="dark", choices=["dark", "light", "hc"],
                    help="Theme to render (default: dark).")
    args = ap.parse_args()

    os.environ.setdefault("PIMESH_GUI_NO_VKB", "1")
    os.environ.setdefault("PIMESH_GUI_EMBEDDED_UVICORN", "0")

    import qasync

    app = _setup_qapplication(args.theme)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        return loop.run_until_complete(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())

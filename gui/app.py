"""GUI application bootstrap.

Glue between Qt (`QApplication` + `qasync`), the meshtasticd client,
the bots runner and the rest of the backend. Owns the async lifecycle:
setup → run until window closed → teardown.

Entry point: ``python -m gui`` calls :func:`main`.
"""

from __future__ import annotations

import asyncio
import logging
import sys

log = logging.getLogger("gui.app")


def _setup_logging() -> None:
    import config as cfg

    logging.basicConfig(
        level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _build_qapplication():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("pi-Mesh")
    app.setOrganizationName("pi-Mesh")
    app.setStyle("Fusion")

    # On the kiosk (linuxfb) we drive the touch screen and the mouse
    # cursor is only ever visible when a debug USB mouse is plugged in —
    # which leaks an X-style arrow into screenshots and partial repaints.
    # Hide it there; keep it visible on desktop dev so PySide6 windows
    # behave normally.
    if app.platformName().startswith("linuxfb"):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QCursor
        app.setOverrideCursor(QCursor(Qt.CursorShape.BlankCursor))
    return app


def apply_theme(app, palette_name: str = "dark", custom: dict | None = None) -> None:
    """Apply a palette to the running ``QApplication``.

    Sets both QSS (controls all custom styling) and QPalette (so native
    widgets that ignore stylesheets — file dialogs, message boxes — still
    pick up the colors).
    """
    from PySide6.QtGui import QColor, QPalette

    from gui.theme.palettes import PALETTES, get_palette
    from gui.theme.qss import build_qss

    if palette_name not in PALETTES and palette_name != "custom":
        palette_name = "dark"
    palette = get_palette(palette_name, custom=custom)
    app.setStyleSheet(build_qss(palette))

    qp = app.palette()
    qp.setColor(QPalette.ColorRole.Window,          QColor(palette["bg"]))
    qp.setColor(QPalette.ColorRole.WindowText,      QColor(palette["text"]))
    qp.setColor(QPalette.ColorRole.Base,            QColor(palette["panel"]))
    qp.setColor(QPalette.ColorRole.AlternateBase,   QColor(palette["bg"]))
    qp.setColor(QPalette.ColorRole.ToolTipBase,     QColor(palette["panel"]))
    qp.setColor(QPalette.ColorRole.ToolTipText,     QColor(palette["text"]))
    qp.setColor(QPalette.ColorRole.Text,            QColor(palette["text"]))
    qp.setColor(QPalette.ColorRole.Button,          QColor(palette["panel"]))
    qp.setColor(QPalette.ColorRole.ButtonText,      QColor(palette["text"]))
    qp.setColor(QPalette.ColorRole.Highlight,       QColor(palette["accent"]))
    qp.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    qp.setColor(QPalette.ColorRole.Link,            QColor(palette["accent"]))
    app.setPalette(qp)


async def _async_main(app, window) -> None:
    """Backend setup → run until window closes → teardown."""
    import config as cfg
    import database
    import meshtasticd_client

    from gui.core.eventbus import EventBus
    from gui.core.settings import init_from_database

    await database.init(cfg.DB_PATH)
    await database.cleanup_old_messages(cfg.DB_PATH, days=30)

    settings = await init_from_database(cfg.DB_PATH)

    apply_theme(app, settings.get("display.theme", "dark") or "dark")

    # Hot-reload: when display.theme changes from the Config page,
    # re-apply the palette without restarting.
    settings.subscribe("display.theme", lambda v: apply_theme(app, (v or "dark")))
    settings.subscribe("pimesh-accent", lambda _v: apply_theme(app, settings.get("display.theme", "dark") or "dark"))

    await meshtasticd_client.load_nodes_from_db()

    bus = EventBus()
    window.attach(bus, settings)

    # Periodic Pi telemetry feed. Without this nothing emits
    # rpi_telemetry on the event bus and Log/Metrics see no host
    # samples (each page would otherwise have to poll independently).
    import meshtasticd_client as _mc

    async def _rpi_telemetry_feed():
        import rpi_telemetry
        while True:
            try:
                data = rpi_telemetry.collect()
                _mc._enqueue_event({"type": "rpi_telemetry", **data})
            except Exception:
                log.exception("rpi_telemetry feed iteration failed")
            await asyncio.sleep(15)

    def _log_task_done(t):
        try:
            t.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("background task %s crashed", t.get_name())

    background = [
        asyncio.create_task(meshtasticd_client.connect(), name="meshtasticd-connect"),
        asyncio.create_task(_rpi_telemetry_feed(), name="rpi-telemetry-feed"),
        bus.start(),
    ]
    for _t in background:
        if isinstance(_t, asyncio.Task):
            _t.add_done_callback(_log_task_done)

    # Auto-reply bot framework. start() subscribes to the meshtasticd event
    # queue and dispatches incoming messages to bots that match their prefix.
    try:
        from bots import runner as bots_runner
        await bots_runner.start(cfg.DB_PATH)
    except Exception:
        log.exception("bots runner failed to start")
        bots_runner = None

    # MQTT bridge: forwards mesh traffic to a broker. Reads the cached
    # config written by Config → MQTT; start() is a no-op when ``enabled``
    # is false or paho-mqtt isn't installed.
    try:
        import mqtt_bridge
        mqtt_cfg = await database.get_config_cache(cfg.DB_PATH, "mqtt") or {}
        if cfg.MQTT_ENABLED and "enabled" not in mqtt_cfg:
            mqtt_cfg["enabled"] = True
        await mqtt_bridge.start(mqtt_cfg)
    except Exception:
        log.exception("mqtt bridge failed to start")

    window.show()

    quit_future: asyncio.Future = asyncio.Future()
    app.aboutToQuit.connect(lambda: (not quit_future.done()) and quit_future.set_result(None))

    try:
        await quit_future
    finally:
        log.info("shutting down")
        try:
            import mqtt_bridge
            await mqtt_bridge.stop()
        except Exception:
            log.exception("mqtt bridge stop failed")
        if bots_runner is not None:
            try:
                await bots_runner.stop()
            except Exception:
                log.exception("bots runner stop failed")
        for t in background:
            t.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        try:
            await meshtasticd_client.disconnect()
        except Exception:
            log.exception("disconnect failed")
        await database.close()


def main() -> int:
    _setup_logging()

    import qasync

    app = _build_qapplication()
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    from gui.main_window import MainWindow

    window = MainWindow()

    with loop:
        try:
            loop.run_until_complete(_async_main(app, window))
        except KeyboardInterrupt:
            log.info("interrupted")

    return 0

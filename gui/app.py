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
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    # Must be set before any QApplication instance is created. Without this
    # mixed-DPI setups (kiosk display + external HDMI) ship with the Qt 6
    # default Round policy, which produces tearing / fractional layouts on
    # non-integer scale factors.
    if QApplication.instance() is None:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("pi-Mesh")
    app.setOrganizationName("pi-Mesh")
    app.setStyle("Fusion")

    # Cursor visibility on the kiosk:
    # An earlier revision called setOverrideCursor(BlankCursor) on linuxfb
    # to keep screenshots clean. That made the GUI unusable once a USB
    # mouse was attached for field debugging. Now the cursor is shown by
    # default and can be hidden explicitly with PIMESH_GUI_NO_CURSOR=1.
    import os as _os
    if _os.environ.get("PIMESH_GUI_NO_CURSOR") == "1":
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QCursor
        app.setOverrideCursor(QCursor(Qt.CursorShape.BlankCursor))
    return app


def _try_get_settings():
    """Return the global Settings singleton, or ``None`` when it is not
    initialised yet (e.g. scripts/capture_screenshots.py calls apply_theme
    without a database)."""
    try:
        from gui.core.settings import get_settings
        return get_settings()
    except Exception:
        return None


def _load_custom_palette(settings) -> dict | None:
    """Parse the persisted ``pimesh-custom-theme`` setting into a palette dict.

    Returns ``None`` (with a logged warning) when the value is missing or
    invalid — the caller then falls back to the default theme instead of
    crashing at startup.
    """
    raw = settings.get("pimesh-custom-theme")
    if not raw:
        log.warning("theme is 'custom' but pimesh-custom-theme is not set")
        return None
    if isinstance(raw, dict):
        data = raw
    else:
        import json
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            log.warning("pimesh-custom-theme is not valid JSON, ignoring")
            return None
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        log.warning("pimesh-custom-theme is not a dict of color strings, ignoring")
        return None
    return data


def apply_theme(app, palette_name: str = "dark", custom: dict | None = None) -> None:
    """Apply a palette to the running ``QApplication``.

    Sets both QSS (controls all custom styling) and QPalette (so native
    widgets that ignore stylesheets — file dialogs, message boxes — still
    pick up the colors).

    Never raises on bad input: an unknown theme name or a missing/invalid
    ``pimesh-custom-theme`` falls back to the "dark" palette with a logged
    warning, so a bad persisted setting cannot crash the GUI at boot.
    Also reads ``pimesh-accent`` from settings (when available) and overrides
    the palette's accent color before the QSS is built.
    """
    from PySide6.QtGui import QColor, QPalette

    from gui.theme.palettes import PALETTES, get_palette
    from gui.theme.qss import build_qss

    if palette_name not in PALETTES and palette_name != "custom":
        log.warning("unknown theme %r, falling back to 'dark'", palette_name)
        palette_name = "dark"

    settings = _try_get_settings()

    if palette_name == "custom" and custom is None and settings is not None:
        custom = _load_custom_palette(settings)

    try:
        palette = dict(get_palette(palette_name, custom=custom))
    except (KeyError, ValueError) as exc:
        log.warning("theme %r unusable (%s), falling back to 'dark'", palette_name, exc)
        palette = dict(get_palette("dark"))

    # Accent override from the Config page color picker (pimesh-accent).
    if settings is not None:
        accent = settings.get("pimesh-accent")
        if accent:
            color = QColor(accent)
            if color.isValid():
                palette["accent"] = color.name()
            else:
                log.warning("ignoring invalid pimesh-accent %r", accent)

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
                # collect() reads sysfs and calls shutil.disk_usage which can
                # block briefly under heavy SD-card IO — offload to a thread.
                data = await asyncio.to_thread(rpi_telemetry.collect)
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

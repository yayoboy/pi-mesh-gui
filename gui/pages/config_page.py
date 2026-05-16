"""Config page: device identity, LoRa, channels, MQTT, display, WiFi, admin
and all per-module sections.

Page-level wiring only — individual section widgets live in
:mod:`gui.pages._config_sections` and :mod:`gui.pages._hardware_sections`.
"""

from __future__ import annotations

import asyncio
import logging

from PySide6.QtWidgets import (
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.pages._config_sections import (
    _AdminSection,
    _ChannelsSection,
    _DeviceSection,
    _DisplaySection,
    _LoraSection,
    _MqttSection,
    _WifiSection,
)

log = logging.getLogger(__name__)


class Page(QWidget):
    def __init__(self, eventbus, settings):
        super().__init__()
        self._eventbus = eventbus
        self._settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._status = QLabel("caricamento…")
        self._status.setProperty("role", "muted")
        layout.addWidget(self._status)

        # Wrap forms in a scroll area so the page works on a 320×480 portrait.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)

        self._device = _DeviceSection(self._save_device, body)
        self._lora = _LoraSection(self._save_lora, body)
        self._channels = _ChannelsSection(self._save_channel, body)
        self._mqtt = _MqttSection(self._save_mqtt, body)
        self._display = _DisplaySection(self._settings, body)
        self._wifi = _WifiSection(body)
        self._admin = _AdminSection(
            self._do_factory_reset, self._do_reboot, self._do_pi_factory_reset, body,
        )

        from gui.widgets.collapsible import CollapsibleSection

        sections: list[tuple[str, QWidget]] = [
            ("Dispositivo", self._device),
            ("LoRa", self._lora),
            ("Canali", self._channels),
            ("MQTT", self._mqtt),
            ("Schermo", self._display),
            ("WiFi", self._wifi),
            ("Amministrazione", self._admin),
        ]

        from gui.pages._module_section import ModuleSection
        from gui.pages._module_specs import ALL_MODULE_SPECS
        self._modules: list[ModuleSection] = []
        for spec in ALL_MODULE_SPECS:
            section = ModuleSection(spec, body)
            self._modules.append(section)
            sections.append((spec.title, section))

        from gui.pages._hardware_sections import (
            _AlertsSection,
            _ApSection,
            _CannedMessagesSection,
            _GpioSection,
            _I2cSection,
            _MapConfigSection,
            _RtcSection,
            _SerialSection,
            _UsbStorageSection,
        )
        from gui.pages._bots_section import _BotsSection
        sections.extend([
            ("Porta seriale",       _SerialSection(body)),
            ("Dispositivi GPIO",    _GpioSection(body)),
            ("Scan I2C",            _I2cSection(body)),
            ("RTC",                 _RtcSection(body)),
            ("Modalità AP",         _ApSection(body)),
            ("Allerte",             _AlertsSection(body)),
            ("Configurazione mappa", _MapConfigSection(body)),
            ("Messaggi preimpostati", _CannedMessagesSection(body)),
            ("Storage USB",         _UsbStorageSection(body)),
            ("Bot",                 _BotsSection(body)),
        ])

        from PySide6.QtWidgets import QGroupBox
        for i, (title, widget) in enumerate(sections):
            wrap = CollapsibleSection(title, body, expanded=(i == 0))
            # The Collapsible already shows the section title in its header.
            # Strip the inner QGroupBox title so it isn't rendered twice.
            if isinstance(widget, QGroupBox):
                widget.setTitle("")
                widget.setFlat(True)
            wrap.add_widget(widget)
            body_layout.addWidget(wrap)

        body_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        self._reload()

    def _reload(self) -> None:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(self._reload_async())

    async def _reload_async(self) -> None:
        import config as cfg
        import meshtasticd_client
        # Parallel fetch with a hard timeout — otherwise a stuck radio I/O
        # leaves the status label on "caricamento…" forever.
        try:
            node, lora, channels, mqtt = await asyncio.wait_for(
                asyncio.gather(
                    meshtasticd_client.get_node_config(cfg.DB_PATH),
                    meshtasticd_client.get_lora_config(cfg.DB_PATH),
                    meshtasticd_client.get_channels(cfg.DB_PATH),
                    meshtasticd_client.get_mqtt_config(cfg.DB_PATH),
                ),
                timeout=8.0,
            )
        except asyncio.TimeoutError:
            log.warning("config reload timed out")
            self._status.setText("timeout (radio non risponde)")
            self._status.setProperty("role", "warn")
            self._status.style().unpolish(self._status)
            self._status.style().polish(self._status)
            return
        except Exception:
            log.exception("config reload failed")
            self._status.setText("errore caricamento config")
            self._status.setProperty("role", "danger")
            self._status.style().unpolish(self._status)
            self._status.style().polish(self._status)
            return

        cached = node.get("cached") or lora.get("cached") or mqtt.get("cached")
        self._status.setText("in cache (radio offline)" if cached else "in linea")
        self._status.setProperty("role", "warn" if cached else "ok")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

        self._device.fill(node)
        self._lora.fill(lora)
        self._channels.fill(channels or [])
        self._mqtt.fill(mqtt or {})

        for section in self._modules:
            section.reload()

    # Save handlers -----------------------------------------------------

    def _save_device(self, *, long_name: str, short_name: str, role: str) -> None:
        if not long_name or not short_name:
            QMessageBox.warning(self, "Config", "Nome completo e breve sono obbligatori.")
            return
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(self._save_device_async(long_name, short_name, role))

    async def _save_device_async(self, long_name: str, short_name: str, role: str) -> None:
        try:
            import meshtasticd_client
            await meshtasticd_client.set_node_config(long_name, short_name, role)
        except Exception:
            log.exception("set_node_config failed")
            QMessageBox.critical(self, "Config", "Impossibile salvare la configurazione dispositivo.")
            return
        self._status.setText("config dispositivo in coda")
        self._status.setProperty("role", "ok")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _save_lora(self, *, region: str, preset: str) -> None:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(self._save_lora_async(region, preset))

    async def _save_lora_async(self, region: str, preset: str) -> None:
        try:
            import meshtasticd_client
            await meshtasticd_client.set_lora_config(region, preset)
        except Exception:
            log.exception("set_lora_config failed")
            QMessageBox.critical(self, "Config", "Impossibile salvare la configurazione LoRa.")
            return
        self._status.setText("config LoRa in coda")
        self._status.setProperty("role", "ok")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _save_mqtt(self, params: dict) -> None:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(self._save_mqtt_async(params))

    async def _save_mqtt_async(self, params: dict) -> None:
        # Persist locally, push to board's MQTT module config, restart bridge.
        try:
            import config as cfg
            import database
            import meshtasticd_client
            import mqtt_bridge
            await database.set_config_cache(cfg.DB_PATH, "mqtt", params)
            await meshtasticd_client.set_mqtt_config(params)
            await mqtt_bridge.restart(params)
        except Exception:
            log.exception("set_mqtt_config failed")
            QMessageBox.critical(self, "Config", "Impossibile salvare la configurazione MQTT.")
            return
        self._status.setText("config MQTT salvata")
        self._status.setProperty("role", "ok")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _save_channel(self, *, index: int, name: str, psk_b64: str) -> None:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(self._save_channel_async(index, name, psk_b64))

    def _do_reboot(self) -> None:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(self._reboot_pi())

    async def _reboot_pi(self) -> None:
        try:
            import system_ops
            await system_ops.reboot()
        except Exception:
            log.exception("reboot failed")
            QMessageBox.warning(self, "Amministrazione", "Impossibile riavviare.")

    def _do_factory_reset(self) -> None:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(self._factory_reset_async())

    def _do_pi_factory_reset(self) -> None:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(self._pi_factory_reset_async())

    async def _pi_factory_reset_async(self) -> None:
        try:
            import config as cfg
            import system_ops
            await system_ops.pi_factory_reset(cfg.DB_PATH)
        except Exception as exc:
            QMessageBox.warning(self, "Amministrazione", f"Errore reset Pi: {exc}")
            return
        self._status.setText("reset Pi in coda")
        self._status.setProperty("role", "warn")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    async def _factory_reset_async(self) -> None:
        try:
            import meshtasticd_client
            await meshtasticd_client.factory_reset()
        except Exception:
            log.exception("factory_reset failed")
            QMessageBox.warning(self, "Amministrazione", "Impossibile accodare il reset.")
            return
        self._status.setText("reset di fabbrica in coda")
        self._status.setProperty("role", "warn")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    async def _save_channel_async(self, index: int, name: str, psk_b64: str) -> None:
        try:
            import meshtasticd_client
            await meshtasticd_client.set_channel(index, name, psk_b64)
        except Exception:
            log.exception("set_channel failed")
            QMessageBox.critical(self, "Config", f"Impossibile salvare il canale {index}.")
            return
        self._status.setText(f"canale {index} in coda")
        self._status.setProperty("role", "ok")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
        self._reload()

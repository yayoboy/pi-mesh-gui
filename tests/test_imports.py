"""Smoke tests: every backend + GUI page module must import cleanly.

Catches the kind of regression where a top-level rename, a stale
``from main import …``, or a deleted helper bricks a whole tab — the
unit tests don't notice because nobody imports the affected module.

GUI page modules are skipped if PySide6 isn't available on the host
(CI machines without Qt), but their import is *attempted* — only
the PySide6 ImportError is excused.
"""

from __future__ import annotations

import importlib
import sys

import pytest


BACKEND_MODULES = [
    "config",
    "database",
    "meshtasticd_client",
    "mqtt_bridge",
    "rpi_telemetry",
    "usb_storage",
    "system_ops",
    "display_ops",
    "wifi_ops",
    "hardware_ops",
    "bots",
    "bots.runner",
    "bots.config",
    "bots.parser",
    "bots.base",
]

GUI_MODULES = [
    "gui",
    "gui.app",
    "gui.main_window",
    "gui.core.eventbus",
    "gui.core.event_dispatcher",
    "gui.core.settings",
    "gui.pages._telemetry_format",
    "gui.pages._message_format",
    "gui.pages._module_specs",
    "gui.pages._node_format",
    "gui.pages._psk",
    "gui.pages.config_page",
    "gui.pages.log_page",
    "gui.pages.map_page",
    "gui.pages.map_math",
    "gui.pages.messages_page",
    "gui.pages.metrics_page",
    "gui.pages.nodes_page",
    "gui.pages.telemetry_page",
    "gui.widgets.sparkline",
    "gui.widgets.sparkline_buffer",
    "gui.widgets.status_icons",
    "gui.widgets.toast",
    "gui.widgets.vkb",
    "gui.widgets.collapsible",
    "gui.widgets.animations",
]


def _try_import(name: str) -> None:
    """Import ``name``; only PySide6 missing is allowed to skip."""
    try:
        importlib.import_module(name)
    except ImportError as exc:
        if "PySide6" in str(exc) or "PyQt6" in str(exc):
            pytest.skip(f"Qt not available: {exc}")
        raise


@pytest.mark.parametrize("module_name", BACKEND_MODULES)
def test_backend_module_imports(module_name: str) -> None:
    _try_import(module_name)


@pytest.mark.parametrize("module_name", GUI_MODULES)
def test_gui_module_imports(module_name: str) -> None:
    _try_import(module_name)

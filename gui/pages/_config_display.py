"""``_DisplaySection`` — theme picker, brightness, custom-color editor.
Extracted from :mod:`gui.pages._config_sections`.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from gui.core.tasks import schedule as _schedule_qt

log = logging.getLogger(__name__)


class _DisplaySection(QGroupBox):
    """Theme picker + accent color + brightness + rotation."""

    def __init__(self, settings, parent=None):
        super().__init__("Schermo", parent)
        self._settings = settings

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(4)
        theme_row.addWidget(QLabel("Tema"))
        self._theme_buttons: dict[str, QPushButton] = {}
        for name in ("dark", "light", "hc", "custom"):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked, n=name: self._on_theme_clicked(n))
            theme_row.addWidget(btn)
            self._theme_buttons[name] = btn
        theme_row.addStretch(1)
        layout.addLayout(theme_row)

        accent_row = QHBoxLayout()
        accent_row.addWidget(QLabel("Accent"))
        self._accent_swatch = QPushButton("")
        # 44x44 minimum touch target for the kiosk's 3.5" display.
        self._accent_swatch.setFixedSize(44, 44)
        self._accent_swatch.clicked.connect(self._pick_accent)
        accent_row.addWidget(self._accent_swatch)
        accent_row.addStretch(1)
        layout.addLayout(accent_row)

        bri_row = QHBoxLayout()
        bri_row.addWidget(QLabel("Luminosità"))
        self._brightness = QSlider(Qt.Orientation.Horizontal)
        self._brightness.setRange(0, 255)
        self._brightness.setValue(255)
        self._brightness_value = QLabel("255")
        self._brightness_value.setMinimumWidth(28)
        self._brightness.valueChanged.connect(
            lambda v: self._brightness_value.setText(str(v))
        )
        self._brightness.sliderReleased.connect(self._on_brightness_release)
        bri_row.addWidget(self._brightness, 1)
        bri_row.addWidget(self._brightness_value)
        layout.addLayout(bri_row)

        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("Rotazione"))
        self._rotation_buttons: dict[int, QPushButton] = {}
        for deg in (0, 90, 180, 270):
            btn = QPushButton(f"{deg}°")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c, d=deg: self._on_rotation_clicked(d))
            rot_row.addWidget(btn)
            self._rotation_buttons[deg] = btn
        rot_row.addStretch(1)
        layout.addLayout(rot_row)

        self._refresh()
        _schedule_qt(self._fetch_display())

    def _refresh(self) -> None:
        if self._settings is None:
            return
        current = self._settings.get("display.theme", "dark") or "dark"
        for name, btn in self._theme_buttons.items():
            btn.setChecked(name == current)
        accent = self._settings.get("pimesh-accent") or "#4a9eff"
        self._set_swatch_color(accent)

    def _on_theme_clicked(self, name: str) -> None:
        if self._settings is None:
            return
        self._settings.set("display.theme", name)
        for n, btn in self._theme_buttons.items():
            btn.setChecked(n == name)

    def _pick_accent(self) -> None:
        if self._settings is None:
            return
        current = QColor(self._settings.get("pimesh-accent") or "#4a9eff")
        chosen = QColorDialog.getColor(current, self, "Colore accento")
        if chosen.isValid():
            value = chosen.name()
            self._settings.set("pimesh-accent", value)
            self._set_swatch_color(value)

    def _set_swatch_color(self, hex_color: str) -> None:
        self._accent_swatch.setStyleSheet(
            f"background:{hex_color}; border:1px solid #444; border-radius:6px;"
        )

    async def _fetch_display(self) -> None:
        try:
            import display_ops
            d = await display_ops.get_state()
            self._brightness.setRange(0, int(d.get("max_brightness", 255)))
            self._brightness.setValue(int(d.get("brightness", 255)))
            self._brightness_value.setText(str(self._brightness.value()))
            self._set_rotation_active(int(d.get("rotation", 0)))
        except Exception:
            log.debug("display fetch failed", exc_info=True)

    def _on_brightness_release(self) -> None:
        _schedule_qt(self._post_display(brightness=self._brightness.value()))

    def _on_rotation_clicked(self, deg: int) -> None:
        if QMessageBox.question(
            self, "Rotazione",
            f"Impostare rotazione a {deg}°? Il Pi si riavvierà per applicare.",
        ) != QMessageBox.StandardButton.Yes:
            self._refresh_rotation_buttons_from_settings()
            return
        self._set_rotation_active(deg)
        _schedule_qt(self._post_display(rotation=deg))

    def _refresh_rotation_buttons_from_settings(self) -> None:
        _schedule_qt(self._fetch_display())

    def _set_rotation_active(self, deg: int) -> None:
        for d, btn in self._rotation_buttons.items():
            btn.setChecked(d == deg)

    async def _post_display(self, *, brightness: int | None = None, rotation: int | None = None) -> None:
        if brightness is None and rotation is None:
            return
        try:
            import display_ops
            if brightness is not None:
                await display_ops.set_brightness(brightness)
            if rotation is not None:
                await display_ops.set_rotation(rotation)
        except Exception:
            log.exception("display apply failed")
            QMessageBox.warning(self, "Schermo", "Impossibile applicare la modifica.")



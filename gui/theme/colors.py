"""Widget-level semantic color tokens.

The QSS palette (``gui.theme.palettes``) drives stylesheet-resolvable widgets
— backgrounds, borders, role-tagged labels. But ``QPainter``-based widgets
(map markers, sparklines, status icons) cannot reach the stylesheet and
historically embedded hex literals in their code. This module centralizes
those colors per active palette so the kiosk theme stays consistent with the
QSS one.

Tokens are *not* required to round-trip through QSS, so they live separately
from ``palettes.PALETTES`` (whose round-trip is asserted by the existing
test suite).

Lookup pattern::

    from gui.theme.colors import get_widget_colors
    colors = get_widget_colors(settings.get("display.theme") or "dark")
    pen = QPen(QColor(colors["marker_remote"]))

For widgets that need to follow live theme changes, subscribe via
``Settings.subscribe("display.theme", callback)`` and re-pull on each call.
"""

from __future__ import annotations

from typing import Mapping

WidgetColors = Mapping[str, str]


# Keys that every variant below must define. New tokens go here first, then
# in every palette dict.
_TOKENS = (
    # Map / location overlays
    "marker_local",      # this device's own node marker
    "marker_remote",     # other nodes' markers
    "marker_label_outline",   # 1px outline behind marker label text
    "marker_label_text",      # marker label foreground
    "waypoint",          # mesh-broadcast waypoints
    "waypoint_outline",  # waypoint marker pen
    "custom_marker",     # user-placed POIs
    "custom_marker_outline",
    "traceroute",        # traceroute polyline
    # SNR ramp for neighbor links (good/mid/bad)
    "snr_good",
    "snr_mid",
    "snr_bad",
    # Battery level ramp
    "battery_full",
    "battery_warn",
    "battery_critical",
    # Sparkline series — semantic, not just "blue"/"green"/"red"
    "series_temp",
    "series_cpu",
    "series_ram",
    "series_battery",
    "series_default",
    # Misc UI accents that bypassed QSS historically
    "subtitle_text",     # secondary text in HTML-rendered rows
    "filter_active_bg",  # selected filter chip background
    "filter_active_text",
    "filter_active_border",
    "action_warn",       # text color of warning push buttons (shutdown)
    "action_danger",     # text color of destructive push buttons (factory reset)
)


_DARK: dict[str, str] = {
    "marker_local":            "#ff5722",
    "marker_remote":           "#4a9eff",
    "marker_label_outline":    "#000000",
    "marker_label_text":       "#ffffff",
    "waypoint":                "#ffeb3b",
    "waypoint_outline":        "#000000",
    "custom_marker":           "#9c27b0",
    "custom_marker_outline":   "#ffffff",
    "traceroute":              "#ffeb3b",
    "snr_good":                "#4caf50",
    "snr_mid":                 "#ff9800",
    "snr_bad":                 "#f44336",
    "battery_full":            "#4caf50",
    "battery_warn":            "#ff9800",
    "battery_critical":        "#f44336",
    "series_temp":             "#f44336",
    "series_cpu":              "#4caf50",
    "series_ram":              "#ff9800",
    "series_battery":          "#4caf50",
    "series_default":          "#4a9eff",
    "subtitle_text":           "#8a92a4",
    "filter_active_bg":        "#ffcf3a",
    "filter_active_text":      "#1a1a1a",
    "filter_active_border":    "#ffcf3a",
    "action_warn":             "#ffcf3a",
    "action_danger":           "#ef4444",
}


_LIGHT: dict[str, str] = {
    "marker_local":            "#d84315",
    "marker_remote":           "#1565c0",
    "marker_label_outline":    "#ffffff",
    "marker_label_text":       "#1a202c",
    "waypoint":                "#f9a825",
    "waypoint_outline":        "#1a202c",
    "custom_marker":           "#6a1b9a",
    "custom_marker_outline":   "#1a202c",
    "traceroute":              "#f9a825",
    "snr_good":                "#2e7d32",
    "snr_mid":                 "#e65100",
    "snr_bad":                 "#c62828",
    "battery_full":            "#2e7d32",
    "battery_warn":            "#e65100",
    "battery_critical":        "#c62828",
    "series_temp":             "#c62828",
    "series_cpu":              "#2e7d32",
    "series_ram":              "#e65100",
    "series_battery":          "#2e7d32",
    "series_default":          "#1565c0",
    "subtitle_text":           "#475569",
    "filter_active_bg":        "#f9a825",
    "filter_active_text":      "#1a202c",
    "filter_active_border":    "#f9a825",
    "action_warn":             "#b45309",
    "action_danger":           "#b91c1c",
}


_HC: dict[str, str] = {
    "marker_local":            "#ff8800",
    "marker_remote":           "#00ffff",
    "marker_label_outline":    "#000000",
    "marker_label_text":       "#ffffff",
    "waypoint":                "#ffff00",
    "waypoint_outline":        "#000000",
    "custom_marker":           "#ff00ff",
    "custom_marker_outline":   "#ffffff",
    "traceroute":              "#ffff00",
    "snr_good":                "#00ff00",
    "snr_mid":                 "#ff8800",
    "snr_bad":                 "#ff0000",
    "battery_full":            "#00ff00",
    "battery_warn":            "#ff8800",
    "battery_critical":        "#ff0000",
    "series_temp":             "#ff0000",
    "series_cpu":              "#00ff00",
    "series_ram":              "#ff8800",
    "series_battery":          "#00ff00",
    "series_default":          "#ffff00",
    "subtitle_text":           "#aaaaaa",
    "filter_active_bg":        "#ffff00",
    "filter_active_text":      "#000000",
    "filter_active_border":    "#ffff00",
    "action_warn":             "#ffff00",
    "action_danger":           "#ff0000",
}


_VARIANTS: dict[str, dict[str, str]] = {
    "dark":  _DARK,
    "light": _LIGHT,
    "hc":    _HC,
}


def _validate(variant: dict[str, str]) -> dict[str, str]:
    missing = [k for k in _TOKENS if k not in variant]
    if missing:
        raise ValueError(f"widget-colors variant missing keys: {missing}")
    return variant


for _name, _v in _VARIANTS.items():
    _validate(_v)


def get_widget_colors(palette_name: str | None) -> WidgetColors:
    """Return the widget-color token dict for ``palette_name``.

    Unknown / ``None`` / ``"custom"`` names fall back to ``"dark"`` (custom
    QSS palettes don't currently carry widget tokens; they inherit the dark
    variant — extending this is left as a follow-up).
    """
    if not palette_name:
        return _DARK
    return _VARIANTS.get(palette_name, _DARK)

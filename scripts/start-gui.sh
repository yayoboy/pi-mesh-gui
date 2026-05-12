#!/bin/bash
# scripts/start-gui.sh — Native Qt GUI launcher (xinit target).
# Used by systemd/pimesh-gui.service.

set -e

export DISPLAY=:0

# Touchscreen energy saving off so the kiosk display never blanks.
xset -dpms
xset s off
xset s noblank

# Borderless WM.
matchbox-window-manager -use_titlebar no &

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# Pick the venv interpreter if it exists, fall back to system Python with
# system-site-packages support (needed when PySide6 is installed via apt).
if [ -x "$REPO_DIR/venv/bin/python" ]; then
    PY="$REPO_DIR/venv/bin/python"
else
    PY="/usr/bin/python3"
fi

# Optional config (SERIAL_PATH, DB_PATH, LOG_LEVEL, MAP_*, ALERT_*, MQTT_ENABLED).
if [ -f "$REPO_DIR/config.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_DIR/config.env"
    set +a
fi

exec "$PY" -m gui

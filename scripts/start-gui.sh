#!/bin/bash
# scripts/start-gui.sh — Native Qt GUI launcher (xinit target).
# Used by systemd/pimesh-gui.service.

set -e

# X11 setup only applies when an X server is actually running; under
# QT_QPA_PLATFORM=linuxfb (the systemd unit) there is no DISPLAY and these
# tools would abort the launcher via set -e.
if [ "${QT_QPA_PLATFORM:-}" != "linuxfb" ] && command -v xset >/dev/null 2>&1; then
    export DISPLAY="${DISPLAY:-:0}"
    if xset q >/dev/null 2>&1; then
        # Touchscreen energy saving off so the kiosk display never blanks.
        xset -dpms
        xset s off
        xset s noblank

        # Hide mouse cursor on the touchscreen kiosk. unclutter is optional.
        if command -v unclutter >/dev/null 2>&1; then
            unclutter -idle 0.1 -root &
        fi

        # Borderless WM.
        if command -v matchbox-window-manager >/dev/null 2>&1; then
            matchbox-window-manager -use_titlebar no &
        fi
    fi
fi

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

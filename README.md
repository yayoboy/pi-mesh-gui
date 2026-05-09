# pi-mesh-gui

A native Qt GUI for [Meshtastic](https://meshtastic.org/) LoRa mesh radio networks, designed to run directly on a **Raspberry Pi** framebuffer with a **3.5" 320x480 touchscreen**. No browser, no web server — just a fast, touch-friendly kiosk app.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Qt](https://img.shields.io/badge/Qt6-PyQt6%20%7C%20PySide6-41cd52)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-c51a4a)

---

## Features

### Nodes
- Real-time list of all mesh nodes — online/offline status, SNR, battery, distance, last-heard
- Node detail dialog with admin actions: traceroute, request position, DM, remote reboot, factory reset, forget node

### Messaging
- Broadcast channel + direct message threads
- Canned messages — configurable quick-send presets
- Unread message badge on tab bar

### Map
- Offline OSM tile rendering with pan/zoom
- Node markers, waypoints, neighbor links, traceroute polylines
- Layer switcher (OSM, topo, satellite)
- Custom POI markers (add/edit/delete)

### Configuration
- Node, LoRa, Channels, MQTT, WiFi, Display, Serial, Alerts, Map, Canned Messages
- 9 module config sections (ExtNotif, S&F, Telemetry, Canned, Range Test, Detection Sensor, Ambient Light, Neighbor Info, Serial)
- Pi factory reset with double-confirm

### Telemetry & Metrics
- Raspberry Pi system: CPU temp, CPU load, RAM, disk usage, uptime
- Board telemetry: battery sparkline, voltage, channel/air utilization
- CSV/JSON export

### Log
- Live packet log with portnum filter pills
- TSV export

### Bots
- Extensible bot framework with 5 built-in bots: ping, status, nodes, help, beacon
- Auto-reply to mesh messages matching bot commands

### UI
- Dark theme, touch-optimized for 320x480 and 480x320
- Virtual keyboard for touchscreen input
- Toast notifications, collapsible config sections
- Vector status-bar icons, MQTT bridge status indicator

---

## Hardware

| Component | Details |
|-----------|---------|
| Raspberry Pi 3 A+ or newer | 512 MB RAM minimum |
| Meshtastic radio | Connected via USB — Heltec V3/V4, T-Beam, RAK, etc. |
| 3.5" 320x480 SPI TFT | MPI3501 or compatible, portrait and landscape supported |

> Talks directly to the radio via `meshtastic.SerialInterface`. The `meshtasticd` daemon is **not required** for ESP32-based boards.

---

## Installation

### 1 — Clone

```bash
cd ~
git clone https://github.com/yayoboy/pi-mesh-gui.git
cd pi-mesh-gui
```

### 2 — Install dependencies

On Raspberry Pi OS (Bookworm, armhf):

```bash
sudo apt install -y python3-pyqt6 python3-pyqt6.qtsvg libqt6svg6 \
    qt6-qpa-plugins libegl1 libxcb-cursor0
sudo pip3 install --break-system-packages -r requirements.txt
sudo pip3 install --break-system-packages qasync meshtastic aiosqlite paho-mqtt
```

> The GUI supports both PySide6 and PyQt6 via a built-in compatibility shim (`gui/_qt_shim.py`). Use whichever is available on your platform.

### 3 — Configure

```bash
cp config.env.example config.env
nano config.env
```

Key settings:

```env
SERIAL_PATH=/dev/ttyACM0
DB_PATH=/home/pimesh/pi-mesh-gui/data/mesh.db
```

### 4 — Run (test)

```bash
# On framebuffer (no X11):
QT_QPA_PLATFORM=linuxfb:fb=/dev/fb1 python3 -m gui

# On X11 / development:
DISPLAY=:0 python3 -m gui
```

### 5 — Install as a system service

```bash
sudo cp systemd/pimesh-gui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pimesh-gui
```

Check status:

```bash
sudo systemctl status pimesh-gui
journalctl -u pimesh-gui -f
```

### 6 — Offline map tiles (optional)

Place tiles at:

```
data/tiles/osm/{z}/{x}/{y}.png
data/tiles/topo/{z}/{x}/{y}.png
data/tiles/satellite/{z}/{x}/{y}.png
```

Use `scripts/manage-tiles.sh` to download and sync tiles for your region.

---

## Project Structure

```
pi-mesh-gui/
├── gui/
│   ├── __main__.py              # Entry point: python -m gui
│   ├── _qt_shim.py              # PySide6 ↔ PyQt6 compatibility layer
│   ├── app.py                   # QApplication bootstrap + qasync event loop
│   ├── main_window.py           # Main window, tab bar, status bar
│   ├── core/
│   │   └── eventbus.py          # Async event bus for meshtastic → UI events
│   ├── pages/
│   │   ├── nodes_page.py        # Node list + detail dialog
│   │   ├── messages_page.py     # Broadcast + DM messaging
│   │   ├── map_page.py          # Offline tile map with markers
│   │   ├── config_page.py       # Device configuration sections
│   │   ├── metrics_page.py      # Pi + board telemetry
│   │   ├── telemetry_page.py    # Battery sparkline + exports
│   │   └── log_page.py          # Packet log with filters
│   └── widgets/
│       ├── vkb.py               # Virtual keyboard
│       ├── toast.py             # Toast notifications
│       ├── collapsible.py       # Collapsible sections
│       ├── sparkline.py         # Sparkline chart widget
│       ├── status_icons.py      # Vector SVG status icons
│       └── animations.py        # Fade-in / transition effects
│
├── bots/                        # Meshtastic bot framework + 5 built-in bots
├── config.py                    # Configuration from environment
├── database.py                  # SQLite via aiosqlite
├── meshtasticd_client.py        # Serial interface + event/command queues
├── mqtt_bridge.py               # MQTT bridge
├── rpi_telemetry.py             # Raspberry Pi system metrics
├── usb_storage.py               # USB storage detection
├── scripts/                     # Display setup, touch calibration, tile management
├── systemd/                     # Service files
└── tests/                       # pytest — hardware-free mocks
```

---

## Deploy to Pi

```bash
sshpass -p pimesh rsync -avz \
  --exclude='.git' --exclude='__pycache__' --exclude='data/*.db' \
  ./ pimesh@pi-mesh2.local:~/pi-mesh-gui/

sshpass -p pimesh ssh pimesh@pi-mesh2.local "sudo systemctl restart pimesh-gui"
```

---

## Development

```bash
# Run tests (no hardware needed)
pip install -r requirements-dev.txt
pytest tests/ -v
```

---

## License

MIT

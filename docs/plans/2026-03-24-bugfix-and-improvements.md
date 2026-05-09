# Bugfix & Strategic Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 7 critical bugs identified in BUGFIX_REPORT.md and implement code-level strategic improvements from STRATEGIC_IMPROVEMENTS.md.

**Architecture:** Changes are grouped by file proximity — backend runtime fixes (meshtastic_client, gpio_handler, sensor_handler), security hardening (main.py), data-layer optimizations (database.py, watchdog.py), progressive web app support (static/, base.html), offline-map MBTiles serving (main.py), hardware alerts (gpio_handler.py), and system setup scripts (scripts/).

**Tech Stack:** Python 3.11, FastAPI, aiosqlite, asyncio, gpiozero, pytest + pytest-asyncio, Bash

**Reference files:** `BUGFIX_REPORT.md`, `STRATEGIC_IMPROVEMENTS.md`

---

## File Map

| File | Action | Reason |
|------|--------|--------|
| `meshtastic_client.py` | Modify | Bugs 1, 2, 7: connection race, blocking calls, silent errors |
| `gpio_handler.py` | Modify | Bugs 3, 4: when_held overwrite, missing _conn |
| `main.py` | Modify | Bugs 4(init), 5: pass _conn to gpio, config whitelist; also MBTiles route |
| `sensor_handler.py` | Modify | Bug 6: INA219 driver init overhead |
| `database.py` | Modify | Improvement: prune_sensor_readings, auto_vacuum |
| `watchdog.py` | Modify | Improvement: call prune_sensor_readings, add VACUUM |
| `templates/base.html` | Modify | PWA: meta tags + manifest link |
| `static/manifest.json` | Create | PWA: app manifest |
| `static/sw.js` | Create | PWA: service worker for static asset caching |
| `templates/map.html` | Modify | MBTiles: update tile layer URLs |
| `config.py` | Modify | Buzzer: add BUZZER_PIN variable |
| `config.env` | Modify | Buzzer: add BUZZER_PIN=0 default |
| `scripts/setup_zram.sh` | Create | Strategic: ZRAM swap |
| `scripts/auto_ap.sh` | Create | Strategic: fallback access point |
| `tests/test_meshtastic_client.py` | Modify | New tests for bugs 1, 2, 7 |
| `tests/test_gpio_handler.py` | Modify | New tests for bugs 3, 4 |
| `tests/test_sensor_handler.py` | Modify | New test for bug 6 |
| `tests/test_database.py` | Modify | New test for prune_sensor_readings |
| `tests/test_watchdog.py` | Modify | New test for maintenance task |

---

## Task 1: Fix meshtastic_client.py (Bugs 1, 2, 7)

**Files:**
- Modify: `meshtastic_client.py`
- Modify: `tests/test_meshtastic_client.py`

### Bug 1 — Connection loop race condition
`watchdog.py` calls `connect()` every 30s when disconnected, but `connect()` already loops internally. This spawns duplicate loops.

### Bug 2 — Blocking event loop
`sendText` and `writeConfig` are synchronous C-extension calls that block the entire asyncio event loop (blocks WebSocket, DB, etc.).

### Bug 7 — Silent async errors
`_bridge()` fires-and-forgets coroutines. Exceptions are swallowed silently.

- [ ] **Step 1: Write failing tests**

Read `tests/test_meshtastic_client.py` first. Append these tests:

```python
# Bug 1: concurrent connect() calls must not stack — SerialInterface called only once
@pytest.mark.asyncio
async def test_connect_not_reentrant():
    import meshtastic_client as mc
    mc._loop = asyncio.get_event_loop()
    mc._broadcast = AsyncMock()
    si_call_count = 0
    original_si = None

    class SlowSI:
        def __init__(self, *a, **kw):
            nonlocal si_call_count
            si_call_count += 1

    with patch("meshtastic.serial_interface.SerialInterface", SlowSI):
        # Fire two concurrent connect() tasks
        t1 = asyncio.create_task(mc.connect())
        t2 = asyncio.create_task(mc.connect())
        await asyncio.gather(t1, t2, return_exceptions=True)

    # _is_connecting guard must ensure SerialInterface is only created once
    assert si_call_count == 1, f"Expected 1 SerialInterface init, got {si_call_count}"

# Bug 2: send_message must use asyncio.to_thread
@pytest.mark.asyncio
async def test_send_message_uses_to_thread():
    import meshtastic_client as mc
    mock_iface = MagicMock()
    mc._interface = mock_iface
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
        await mc.send_message("ciao", 0, "^all")
        mock_thread.assert_called_once()

# Bug 7: _bridge logs errors from failed coroutines
def test_bridge_adds_done_callback():
    import meshtastic_client as mc
    loop = asyncio.new_event_loop()
    mc._loop = loop
    async def failing():
        raise ValueError("test error")
    future = MagicMock()
    future.cancelled.return_value = False
    future.result.side_effect = ValueError("test error")
    with patch("asyncio.run_coroutine_threadsafe", return_value=future) as mock_rcts:
        mc._bridge(failing())
        mock_rcts.assert_called_once()
        future.add_done_callback.assert_called_once()
    loop.close()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/yayoboy/Desktop/GitHub/pi-Mesh
python -m pytest tests/test_meshtastic_client.py::test_connect_not_reentrant tests/test_meshtastic_client.py::test_send_message_uses_to_thread tests/test_meshtastic_client.py::test_bridge_adds_done_callback -v
```

Expected: 1-3 failures (attribute missing or assertion failure).

- [ ] **Step 3: Apply fixes to meshtastic_client.py**

Add `_is_connecting = False` to module-level variables (after `_connected = False`):

```python
_is_connecting = False
```

Replace the `connect()` function:

```python
async def connect():
    global _interface, _connected, _is_connecting
    if _is_connecting or _connected:
        return
    if not _MESHTASTIC_AVAILABLE:
        logging.warning("meshtastic non disponibile, connect() no-op")
        return
    _is_connecting = True
    try:
        while True:
            try:
                _interface = meshtastic.serial_interface.SerialInterface(cfg.SERIAL_PORT)
                _connected = True
                logging.info("Connesso a Heltec V3")
                return
            except Exception as e:
                _connected = False
                logging.warning(f"Connessione fallita ({e}), riprovo in 10s...")
                await asyncio.sleep(10)
    finally:
        _is_connecting = False
```

Replace `send_message()`:

```python
async def send_message(text: str, channel: int = 0, destination: str = "^all"):
    if not _interface:
        raise RuntimeError("Non connesso")
    await asyncio.to_thread(_interface.sendText, text, channelIndex=channel, destinationId=destination)
```

Replace `set_config()` — wrap each `writeConfig` call in `asyncio.to_thread`:

```python
async def set_config(config_dict: dict):
    if not _interface:
        raise RuntimeError("Non connesso")
    node = _interface.getNode('^local')
    for section, values in config_dict.items():
        cfg_section = getattr(node.localConfig, section, None)
        if cfg_section is None:
            cfg_section = getattr(node.moduleConfig, section, None)
        if cfg_section:
            for k, v in values.items():
                setattr(cfg_section, k, v)
            await asyncio.to_thread(node.writeConfig, section)
```

Replace `_bridge()`:

```python
def _bridge(coro):
    if _loop and not _loop.is_closed():
        fut = asyncio.run_coroutine_threadsafe(coro, _loop)
        fut.add_done_callback(
            lambda f: logging.error(f"_bridge error: {f.exception()}") if not f.cancelled() and f.exception() else None
        )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_meshtastic_client.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add meshtastic_client.py tests/test_meshtastic_client.py
git commit -m "fix: connection race, blocking sendText/writeConfig, silent bridge errors"
```

---

## Task 2: Fix gpio_handler.py + main.py (Bugs 3, 4)

**Files:**
- Modify: `gpio_handler.py`
- Modify: `main.py`
- Modify: `tests/test_gpio_handler.py`

### Bug 3 — when_held overwrites long_press
Lines 48-49 in `gpio_handler.py` overwrite `btn1.when_held` and `btn2.when_held` that were set on lines 37, 41. The `long_press` encoder event is never sent.

### Bug 4 — _conn not passed to gpio_handler
`_graceful_shutdown()` imports and calls `database.sync_to_sd(_conn)` but `_conn` is always `None` because `init()` never receives it.

- [ ] **Step 1: Read tests/test_gpio_handler.py**

Read the file to understand existing test patterns before adding.

- [ ] **Step 2: Write failing tests**

Append to `tests/test_gpio_handler.py`:

```python
def test_long_press_not_overwritten_by_shutdown():
    """btn1.when_held must send long_press AND check shutdown, not just check shutdown."""
    import gpio_handler
    broadcast_calls = []
    async def fake_broadcast(data):
        broadcast_calls.append(data)
    import asyncio
    loop = asyncio.new_event_loop()
    # We patch GPIO unavailable path — when_held logic runs via check_shutdown
    # Test: init() signature accepts db_conn
    import inspect
    sig = inspect.signature(gpio_handler.init)
    assert 'db_conn' in sig.parameters, "init() must accept db_conn parameter"

def test_init_stores_db_conn():
    import gpio_handler, asyncio
    async def fake_broadcast(data): pass
    loop = asyncio.new_event_loop()
    conn = object()  # any sentinel
    gpio_handler.init(
        (17, 27, 22), (5, 6, 13),
        fake_broadcast, db_conn=conn, loop=loop
    )
    assert gpio_handler._conn is conn
    loop.close()
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
python -m pytest tests/test_gpio_handler.py::test_long_press_not_overwritten_by_shutdown tests/test_gpio_handler.py::test_init_stores_db_conn -v
```

Expected: FAIL (missing `db_conn` param).

- [ ] **Step 4: Fix gpio_handler.py**

Update `init()` signature and body — add `db_conn=None` parameter:

```python
def init(enc1_pins: tuple, enc2_pins: tuple, broadcast_fn, db_conn=None, loop=None):
    global _loop, _broadcast, _conn
    _broadcast = broadcast_fn
    _conn = db_conn
    _loop = loop or asyncio.get_event_loop()

    if not _GPIO_AVAILABLE:
        return

    enc1 = RotaryEncoder(enc1_pins[0], enc1_pins[1], pin_factory=_factory, wrap=False, max_steps=0)
    btn1 = Button(enc1_pins[2], pin_factory=_factory, hold_time=1.0)
    enc2 = RotaryEncoder(enc2_pins[0], enc2_pins[1], pin_factory=_factory, wrap=False, max_steps=0)
    btn2 = Button(enc2_pins[2], pin_factory=_factory, hold_time=1.0)

    def make_handler(encoder_num, action):
        def handler():
            _bridge_event(encoder_num, action)
        return handler

    enc1.when_rotated_clockwise         = make_handler(1, "cw")
    enc1.when_rotated_counter_clockwise = make_handler(1, "ccw")
    btn1.when_pressed                   = make_handler(1, "press")
    enc2.when_rotated_clockwise         = make_handler(2, "cw")
    enc2.when_rotated_counter_clockwise = make_handler(2, "ccw")
    btn2.when_pressed                   = make_handler(2, "press")

    def make_held_handler(encoder_num, other_btn):
        def handler():
            # Always send long_press for encoder navigation
            _bridge_event(encoder_num, "long_press")
            # Shutdown only when BOTH are held simultaneously
            if btn1.is_held and btn2.is_held:
                logging.info("Gesture shutdown rilevata")
                _bridge_coroutine(_graceful_shutdown())
        return handler

    btn1.when_held = make_held_handler(1, btn2)
    btn2.when_held = make_held_handler(2, btn1)
```

Update `main.py` lifespan to pass `_conn`:

```python
gpio_handler.init(
    (cfg.ENC1_A, cfg.ENC1_B, cfg.ENC1_SW),
    (cfg.ENC2_A, cfg.ENC2_B, cfg.ENC2_SW),
    broadcast,
    db_conn=_conn
)
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_gpio_handler.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add gpio_handler.py main.py tests/test_gpio_handler.py
git commit -m "fix: gpio when_held restores long_press event; pass db_conn for shutdown sync"
```

---

## Task 3: Security fix + sensor overhead (Bugs 5, 6)

**Files:**
- Modify: `main.py`
- Modify: `sensor_handler.py`
- Modify: `tests/test_sensor_handler.py`

### Bug 5 — Arbitrary config injection
`/api/remote-config` accepts any `section` key and calls `setattr` with arbitrary values on the Meshtastic node config object.

### Bug 6 — INA219 re-init on every read
`INA219Driver.read()` re-instantiates `INA219` and calls `configure()` on each poll (every 30s). This is wasteful and can cause I2C bus errors.

- [ ] **Step 1: Write failing tests for sensor overhead**

Read `tests/test_sensor_handler.py` first. Append:

```python
def test_ina219_driver_init_called_once():
    """INA219 driver must be instantiated in __init__, not in read()."""
    import inspect
    from sensor_handler import INA219Driver
    source = inspect.getsource(INA219Driver.__init__)
    # __init__ must reference INA219 or _driver
    assert '_driver' in source, "INA219Driver.__init__ must cache driver in self._driver"

def test_ina219_read_does_not_reimport():
    """read() must use self._driver, not create a new INA219 instance."""
    import inspect
    from sensor_handler import INA219Driver
    source = inspect.getsource(INA219Driver.read)
    assert 'INA219(' not in source, "read() must not instantiate INA219 — use self._driver"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_sensor_handler.py::test_ina219_driver_init_called_once tests/test_sensor_handler.py::test_ina219_read_does_not_reimport -v
```

Expected: FAIL.

- [ ] **Step 3: Fix sensor_handler.py — cache INA219 in __init__**

Replace `INA219Driver` class:

```python
class INA219Driver(BaseSensor):
    @property
    def name(self): return "ina219"

    def __init__(self, address: int):
        super().__init__(address)
        self._driver = None
        if _SMBUS_AVAILABLE:
            try:
                from ina219 import INA219
                self._driver = INA219(0.1, busnum=1, address=self.address)
                self._driver.configure()
            except Exception as e:
                logging.error(f"INA219 init error: {e}")

    def read(self) -> dict | None:
        if not self._driver:
            return None
        try:
            return {
                "voltage": round(self._driver.voltage(), 2),
                "current": round(self._driver.current(), 1),
                "power":   round(self._driver.power(), 1),
            }
        except Exception as e:
            logging.error(f"INA219 read error: {e}")
            return None
```

- [ ] **Step 4: Fix main.py — add config section whitelist**

Add this constant just before the `remote_config` route (around line 209):

```python
_ALLOWED_REMOTE_CONFIG_SECTIONS = {"device", "display", "network", "telemetry", "lora", "bluetooth", "position"}
```

Replace the `remote_config` handler body:

```python
@app.post("/api/remote-config")
async def remote_config(payload: dict):
    node_id = payload.pop("remote_node_id", None)
    if not node_id:
        return JSONResponse({"ok": False, "error": "node_id mancante"}, status_code=400)
    try:
        node = meshtastic_client._interface.getNode(node_id)
        for section, values in payload.items():
            if section not in _ALLOWED_REMOTE_CONFIG_SECTIONS:
                logging.warning(f"remote-config: sezione '{section}' non permessa, ignorata")
                continue
            if not isinstance(values, dict):
                continue
            cfg_section = getattr(node.localConfig, section, None) or getattr(node.moduleConfig, section, None)
            if cfg_section:
                for k, v in values.items():
                    setattr(cfg_section, k, v)
                await asyncio.to_thread(node.writeConfig, section)  # non-blocking
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
```

- [ ] **Step 5: Run all tests**

```bash
python -m pytest tests/test_sensor_handler.py tests/test_main.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add main.py sensor_handler.py tests/test_sensor_handler.py
git commit -m "fix: remote-config section whitelist; INA219 driver cached in __init__"
```

---

## Task 4: DB optimizations (database.py + watchdog.py)

**Files:**
- Modify: `database.py`
- Modify: `watchdog.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_watchdog.py`

**What's missing:**
- `sensor_readings` table grows unbounded (no pruning function)
- No `PRAGMA auto_vacuum = INCREMENTAL` (database fragments over time)
- Watchdog maintenance never prunes `sensor_readings` or runs `VACUUM`

- [ ] **Step 1: Write failing test for prune_sensor_readings**

Read `tests/test_database.py`. Append:

```python
@pytest.mark.asyncio
async def test_prune_sensor_readings():
    import database
    conn = await database.init_db(runtime_path=":memory:", persistent_path="/nonexistent")
    # Insert 20 readings
    for i in range(20):
        await database.save_sensor_reading(conn, "bme280", {"temp": i})
    # Prune keeping only last 5
    await database.prune_sensor_readings(conn, max_rows=5)
    rows = await database.get_sensor_readings(conn, "bme280", limit=100)
    assert len(rows) == 5
    await conn.close()
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
python -m pytest tests/test_database.py::test_prune_sensor_readings -v
```

Expected: FAIL with `AttributeError: module 'database' has no attribute 'prune_sensor_readings'`.

- [ ] **Step 3: Add prune_sensor_readings to database.py**

Add after `prune_telemetry()` (around line 140):

```python
async def prune_sensor_readings(conn, max_rows: int = 200):
    cur = await conn.execute("SELECT DISTINCT sensor_name FROM sensor_readings")
    names = [row[0] for row in await cur.fetchall()]
    for name in names:
        await conn.execute("""
            DELETE FROM sensor_readings WHERE id NOT IN (
                SELECT id FROM sensor_readings WHERE sensor_name=?
                ORDER BY timestamp DESC LIMIT ?
            ) AND sensor_name=?
        """, (name, max_rows, name))
    await conn.commit()
```

Also add `PRAGMA auto_vacuum = INCREMENTAL` to `_create_tables()` — insert **before** the `executescript` call. This pragma only takes effect on newly created databases; on existing deployed databases it is a no-op (changing vacuum mode on an existing DB requires `VACUUM INTO` migration, which is out of scope here):

```python
async def _create_tables(conn):
    await conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
    await conn.executescript("""
    CREATE TABLE IF NOT EXISTS messages (
    ...  # rest unchanged
```

- [ ] **Step 4: Update watchdog.py maintenance task**

Replace `db_maintenance_task`:

```python
async def db_maintenance_task(conn, interval: int = 3600):
    while True:
        await asyncio.sleep(interval)
        await database.prune_telemetry(conn)
        await database.prune_sensor_readings(conn)
        await conn.execute("PRAGMA incremental_vacuum")
        await conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        logging.debug("Manutenzione DB completata")
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_database.py tests/test_watchdog.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add database.py watchdog.py tests/test_database.py tests/test_watchdog.py
git commit -m "feat: prune_sensor_readings, auto_vacuum, incremental_vacuum in maintenance"
```

---

## Task 5: PWA support

**Files:**
- Modify: `templates/base.html`
- Create: `static/manifest.json`
- Create: `static/sw.js`

**Goal:** Cache static assets (CSS, JS, Chart.js) on first load so the dashboard loads instantly on subsequent visits even with slow radio-based Wi-Fi links.

- [ ] **Step 1: Read templates/base.html**

Read the file to find the `<head>` section for inserting meta tags.

- [ ] **Step 2: Create static/manifest.json**

```json
{
  "name": "pi-Mesh",
  "short_name": "pi-Mesh",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#1a1a1a",
  "theme_color": "#1a1a1a",
  "icons": [
    {
      "src": "/static/icon-192.png",
      "sizes": "192x192",
      "type": "image/png"
    }
  ]
}
```

- [ ] **Step 3: Create static/sw.js**

```javascript
// Service Worker — cache static assets only
const CACHE = 'pi-mesh-v1'
const STATIC = [
  '/static/style.css',
  '/static/app.js',
  '/static/chart.min.js',
]

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting())
  )
})

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', e => {
  // Only cache GET requests for static assets
  if (e.request.method !== 'GET') return
  const url = new URL(e.request.url)
  if (!STATIC.includes(url.pathname)) return
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  )
})
```

- [ ] **Step 4: Update templates/base.html**

In the `<head>` section, add after the existing `<meta>` tags:

```html
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#1a1a1a">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
```

Before the closing `</body>` tag, add:

```html
<script>
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/sw.js').catch(() => {})
}
</script>
```

- [ ] **Step 5: Verify (manual)**

```bash
python -m pytest tests/test_main.py -v
```

The service worker file is served correctly if the test_main.py static file tests pass.

- [ ] **Step 6: Commit**

```bash
git add templates/base.html static/manifest.json static/sw.js
git commit -m "feat: PWA manifest and service worker for static asset caching"
```

---

## Task 6: MBTiles offline map support

**Files:**
- Modify: `main.py`
- Modify: `templates/map.html`

**Goal:** Serve map tiles from a single `.mbtiles` SQLite file (`static/tiles/osm.mbtiles`, `static/tiles/topo.mbtiles`) instead of thousands of individual PNG files. Falls back to file-based tiles if `.mbtiles` not present.

**MBTiles format:** SQLite DB with table `tiles(zoom_level, tile_column, tile_row, tile_data)`. Note: MBTiles uses TMS tile row (`tile_row = (2^z - 1) - y`) so the Y coordinate must be flipped.

- [ ] **Step 1: Add MBTiles route to main.py**

Add import at top of `main.py` (after existing imports):

```python
import aiosqlite as _aiosqlite
```

Add this route after the `/api/status` route:

```python
@app.get("/tiles/{source}/{z}/{x}/{y}")
async def serve_tile(source: str, z: int, x: int, y: int):
    from fastapi.responses import Response
    # Validate source to prevent path traversal
    if source not in ("osm", "topo"):
        return JSONResponse({"error": "invalid source"}, status_code=400)
    mbtiles_path = f"static/tiles/{source}.mbtiles"
    if os.path.isfile(mbtiles_path):
        tms_y = (1 << z) - 1 - y  # flip Y for MBTiles TMS convention
        try:
            async with _aiosqlite.connect(mbtiles_path) as db:
                cur = await db.execute(
                    "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                    (z, x, tms_y)
                )
                row = await cur.fetchone()
                if row:
                    return Response(content=row[0], media_type="image/png")
        except Exception as e:
            logging.warning(f"MBTiles error {mbtiles_path}: {e}")
    # Fallback: file-based tile
    tile_path = f"static/tiles/{source}/{z}/{x}/{y}.png"
    if os.path.isfile(tile_path):
        with open(tile_path, "rb") as f:
            return Response(content=f.read(), media_type="image/png")
    return Response(status_code=204)  # No content (transparent tile)
```

- [ ] **Step 2: Update map.html tile URLs**

Read `templates/map.html`. Change tile layer URLs from:
```javascript
L.tileLayer('/tiles/osm/{z}/{x}/{y}.png',  ...)
L.tileLayer('/tiles/topo/{z}/{x}/{y}.png', ...)
```
to:
```javascript
L.tileLayer('/tiles/osm/{z}/{x}/{y}',  ...)
L.tileLayer('/tiles/topo/{z}/{x}/{y}', ...)
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_main.py -v
```

Expected: all pass (new route returns 204 when no tiles present, which is correct).

- [ ] **Step 4: Commit**

```bash
git add main.py templates/map.html
git commit -m "feat: MBTiles tile serving with file-based fallback"
```

---

## Task 7: Buzzer/LED hardware alerts

**Files:**
- Modify: `gpio_handler.py`
- Modify: `config.py`
- Modify: `config.env`
- Modify: `meshtastic_client.py`

**Goal:** When a new mesh message is received, emit 1 short beep. When a new node joins, emit 2 short beeps. Uses a piezo buzzer wired to a GPIO pin (optional — disabled if pin = 0).

- [ ] **Step 1: Add BUZZER_PIN to config.py and config.env**

In `config.py`, add after `ENC2_SW` line:

```python
BUZZER_PIN = int(os.getenv("BUZZER_PIN", "0"))  # 0 = disabled
```

In `config.env`, add:
```
BUZZER_PIN=0
```

- [ ] **Step 2: Add buzzer support to gpio_handler.py**

Add `_buzzer = None` to module-level variables.

At the end of `init()`, after the encoder setup block, add:

```python
    if cfg.BUZZER_PIN:
        try:
            from gpiozero import TonalBuzzer
            _buzzer = TonalBuzzer(cfg.BUZZER_PIN, pin_factory=_factory)
        except Exception as e:
            logging.warning(f"Buzzer non disponibile: {e}")
```

Add `beep()` function after `_bridge_coroutine()`:

```python
def beep(pattern: str = "single"):
    """pattern: 'single' (1 short), 'double' (2 short)"""
    if not _buzzer:
        return
    import threading
    def _play():
        try:
            from gpiozero import TonalBuzzer
            if pattern == "single":
                _buzzer.play(440)
                __import__("time").sleep(0.1)
                _buzzer.stop()
            elif pattern == "double":
                for _ in range(2):
                    _buzzer.play(440)
                    __import__("time").sleep(0.08)
                    _buzzer.stop()
                    __import__("time").sleep(0.08)
        except Exception as e:
            logging.debug(f"beep error: {e}")
    threading.Thread(target=_play, daemon=True).start()
```

- [ ] **Step 3: Wire beep to message reception in meshtastic_client.py**

In `_handle_message()`, after `await _broadcast(...)` line, add:

```python
    try:
        import gpio_handler
        gpio_handler.beep("single")
    except Exception:
        pass
```

In `_handle_user()`, after `await _broadcast({"type": "node", ...})` line, add:

```python
    try:
        import gpio_handler
        gpio_handler.beep("double")
    except Exception:
        pass
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/ -v
```

Expected: all pass (buzzer is a no-op when GPIO unavailable).

- [ ] **Step 5: Commit**

```bash
git add gpio_handler.py meshtastic_client.py config.py config.env
git commit -m "feat: optional buzzer beep on message receive and node join"
```

---

## Task 8: System setup scripts

**Files:**
- Create: `scripts/setup_zram.sh`
- Create: `scripts/auto_ap.sh`

**Goal:** Automate ZRAM swap configuration and auto-AP fallback for field deployments on Raspberry Pi 3 A+ (512MB RAM).

- [ ] **Step 1: Create scripts/ directory**

```bash
mkdir -p /Users/yayoboy/Desktop/GitHub/pi-Mesh/scripts
```

- [ ] **Step 2: Create scripts/setup_zram.sh**

```bash
#!/usr/bin/env bash
# Configura ZRAM swap compresso in RAM (equivale a ~700-800MB effettivi su Pi 3 A+)
# Eseguire una volta all'installazione. Richiede privilegi root.
set -euo pipefail

ZRAM_SIZE="256M"  # Compressa diventa ~512MB di swap effettivo

if ! command -v zramctl &>/dev/null; then
    echo "Installazione zram-tools..."
    apt-get install -y zram-tools
fi

# Carica modulo zram
modprobe zram

ZRAM_DEV=$(zramctl --find --size "$ZRAM_SIZE" --algorithm lz4)
mkswap "$ZRAM_DEV"
swapon --priority 100 "$ZRAM_DEV"

echo "ZRAM attivato: $ZRAM_DEV ($ZRAM_SIZE compressa)"

# Rendi persistente al riavvio via /etc/rc.local o systemd
UNIT="/etc/systemd/system/zram-swap.service"
cat > "$UNIT" <<EOF
[Unit]
Description=ZRAM Swap
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c "modprobe zram && DEV=\$(zramctl --find --size $ZRAM_SIZE --algorithm lz4) && mkswap \$DEV && swapon --priority 100 \$DEV"
ExecStop=/bin/bash -c "swapoff \$(zramctl | awk 'NR>1{print \$1}')"

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now zram-swap.service
echo "Servizio zram-swap.service attivato"
```

- [ ] **Step 3: Create scripts/auto_ap.sh**

```bash
#!/usr/bin/env bash
# Attiva un Access Point locale "pi-mesh-portal" se nessun Wi-Fi noto è disponibile.
# Prerequisiti: hostapd, dnsmasq installati. Eseguire come servizio o da rc.local.
set -euo pipefail

AP_SSID="pi-mesh-portal"
AP_PASS="meshtastic"
AP_IP="192.168.88.1"
CHECK_TIMEOUT=60  # secondi di attesa prima di attivare l'AP

check_wifi() {
    # Controlla se siamo connessi a una rete Wi-Fi (indirizzo IP assegnato su wlan0)
    ip addr show wlan0 2>/dev/null | grep -q "inet " && return 0 || return 1
}

echo "Attendo connessione Wi-Fi per ${CHECK_TIMEOUT}s..."
for i in $(seq 1 $CHECK_TIMEOUT); do
    if check_wifi; then
        echo "Wi-Fi connesso. AP non necessario."
        exit 0
    fi
    sleep 1
done

echo "Nessun Wi-Fi trovato — attivazione AP '$AP_SSID'..."

# Configura IP statico
ip addr add "${AP_IP}/24" dev wlan0 2>/dev/null || true

# Configura hostapd
cat > /tmp/hostapd_mesh.conf <<EOF
interface=wlan0
ssid=$AP_SSID
hw_mode=g
channel=6
wpa=2
wpa_passphrase=$AP_PASS
wpa_key_mgmt=WPA-PSK
EOF

# Configura dnsmasq (DHCP + DNS)
cat > /tmp/dnsmasq_mesh.conf <<EOF
interface=wlan0
dhcp-range=192.168.88.10,192.168.88.50,12h
address=/#/$AP_IP
EOF

pkill hostapd  2>/dev/null || true
pkill dnsmasq  2>/dev/null || true

hostapd -B /tmp/hostapd_mesh.conf
dnsmasq -C /tmp/dnsmasq_mesh.conf

echo "AP '$AP_SSID' attivo su $AP_IP — accedi a http://$AP_IP:8080"
```

- [ ] **Step 4: Make scripts executable and commit**

```bash
chmod +x /Users/yayoboy/Desktop/GitHub/pi-Mesh/scripts/setup_zram.sh
chmod +x /Users/yayoboy/Desktop/GitHub/pi-Mesh/scripts/auto_ap.sh
git add scripts/
git commit -m "feat: ZRAM swap and auto-AP fallback setup scripts"
```

---

## Final Verification

After all tasks are complete:

```bash
cd /Users/yayoboy/Desktop/GitHub/pi-Mesh
python -m pytest tests/ -v --tb=short
```

Expected: all existing + new tests pass with no regressions.

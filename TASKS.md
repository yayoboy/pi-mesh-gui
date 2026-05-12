# Open tasks

Things the test suite doesn't cover and that need to be exercised on a real
Raspberry Pi (with the Meshtastic board attached, nmcli, i2c-tools, etc.).
Tick each item once you've verified it on hardware.

## Hardware-test todos (cat #3 shell-outs)

These wrappers passed `ast.parse` and the unit-test suite, but their actual
side effects (subprocess calls, sysfs writes, GPIO pulses) cannot be
exercised in CI. Walk through each on the Pi.

### `system_ops.py`
- [ ] `system_ops.reboot()` — run **Config → Admin → Reboot**, confirm the
      Pi reboots and the service comes back up.
- [ ] `system_ops.shutdown()` — status bar power menu → "Spegni", confirm
      clean poweroff.
- [ ] `system_ops.pi_factory_reset(db_path)` — Config → Admin → Pi factory
      reset. Verify: DB + WAL/SHM gone, `data/exports`/`data/screenshots`
      purged, `data/tiles/` preserved, system reboots.
- [ ] Verify NOPASSWD sudoers is set for `systemctl reboot/poweroff` on
      the service user (otherwise the subprocess hangs on the password
      prompt).

### `display_ops.py`
- [ ] `display_ops.get_state()` — open Config → Display, confirm the
      brightness slider shows the panel's current value and the rotation
      buttons highlight the active angle.
- [ ] `display_ops.set_brightness()` — drag the slider, release, confirm
      backlight changes immediately (no reboot).
- [ ] `display_ops.set_rotation()` — pick 90°/180°/270°, confirm
      `/boot/firmware/config.txt` is rewritten and the Pi reboots into
      the new orientation.
- [ ] If the panel has no entry under `/sys/class/backlight/`, brightness
      slider falls back to "no backlight device" — verify the error path.

### `wifi_ops.py`  (requires NetworkManager / nmcli)
- [ ] `wifi_ops.scan()` — Config → WiFi → Scan: list populates with SSIDs,
      signal strengths and security types.
- [ ] `wifi_ops.status()` — banner shows `connected: <ssid>  <ip>`.
- [ ] `wifi_ops.connect(ssid, password)` — tap a network, enter password,
      verify connection succeeds and the banner updates.
- [ ] `wifi_ops.saved()` — Saved profiles dialog lists all 802-11-wireless
      connections.
- [ ] `wifi_ops.forget(name)` — double-tap a saved profile, confirm it's
      gone after refresh.
- [ ] `wifi_ops.set_ip()` — IP config dialog, set Static (e.g.
      `192.168.1.50/24`, gw `192.168.1.1`, DNS `8.8.8.8 1.1.1.1`), apply,
      verify with `ip addr show wlan0`.  Then switch back to DHCP.
- [ ] `wifi_ops.ap_status()` / `ap_toggle()` — needs a pre-configured AP
      profile. To create one once:
      ```bash
      sudo nmcli con add type wifi ifname wlan0 con-name pimesh-ap \
        ssid pi-Mesh autoconnect no
      sudo nmcli con modify pimesh-ap 802-11-wireless.mode ap \
        802-11-wireless.band bg ipv4.method shared
      sudo nmcli con modify pimesh-ap wifi-sec.key-mgmt wpa-psk \
        wifi-sec.psk "pickAPassword"
      ```
      Then verify Config → AP mode → toggle on/off.

### `hardware_ops.py`
- [ ] `hardware_ops.i2c_scan(bus=1)` — Config → I2C scan. Needs
      `i2c-tools` installed (`sudo apt install i2c-tools`) and the i2c
      bus enabled (`sudo raspi-config` → Interface Options → I2C).
- [ ] `hardware_ops.rtc_status()` — if an RTC HAT is fitted, verify
      `model` and `device` are populated and `time` matches `date -u`.
      Without an RTC, expect `configured: no`.
- [ ] `hardware_ops.serial_ports()` — Config → Serial port: ACM/USB
      ports are listed and the current `SERIAL_PATH` is preselected.
- [ ] `hardware_ops.set_serial_port(port)` — pick a different port,
      apply, confirm `config.env` is updated. Restart the service to
      bind to the new port.
- [ ] `hardware_ops.gpio_test(device)` — add a GPIO device with a real
      output pin (e.g. an LED on BCM 17), action `pulse`, click Test:
      verify three blinks. Needs `python3-gpiozero` installed.

### `usb_storage.py` integration
- [ ] Plug a FAT/exFAT USB stick: Config → USB storage section shows
      "mounted at /media/…  NNN MB free".
- [ ] Move tiles to USB: `data/tiles/` becomes a symlink into
      `/media/<label>/pi-mesh/tiles`. Verify by `readlink data/tiles`.
- [ ] Restore tiles to SD: symlink is gone and the directory is real
      again.
- [ ] Eject the USB stick, refresh: status shows "no USB mounted".

## Meshtastic radio (cat #2)
- [ ] `meshtasticd_client.send_admin(nid, 'request_telemetry')` —
      Node detail → Request telemetry, confirm a telemetry packet
      arrives from the remote node.
- [ ] `send_admin(nid, 'reboot')` — Node detail → Remote reboot,
      confirm the remote node disappears for ~30 s then re-appears.
- [ ] `send_admin(nid, 'factory_reset')` — destructive. Skip unless
      you have a spare node you can re-pair.
- [ ] `meshtasticd_client.send_waypoint(...)` — double-tap on the map
      → Send waypoint, verify another node receives it.

## MQTT bridge

- [ ] Config → MQTT → Save with ``enabled`` checked: the live status
      banner switches from "disabled" → "connecting…" → "connected".
- [ ] Restart the GUI service, confirm the bridge auto-starts using
      the persisted config (cached via ``database.set_config_cache('mqtt')``).
- [ ] Config → MQTT → Save with ``enabled`` un-checked: bridge stops,
      banner reverts to "disabled".
- [ ] Requires ``paho-mqtt`` installed (``pip install paho-mqtt`` or
      ``apt install python3-paho-mqtt``); banner shows
      "paho-mqtt not installed" otherwise.

## Known limits

- Reboot/poweroff and `config.txt` writes require `sudo`; the service
  user must have NOPASSWD sudoers entries or polkit rules.
- `display_ops.set_brightness` writes `/sys/class/backlight/<dev>/brightness`.
  Most distros restrict this to root — `sudo tee` fallback exists, so the
  same sudoers note applies.
- `display_ops.set_rotation` edits `/boot/firmware/config.txt`. Same.
- `wifi_ops.ap_toggle` only switches an *existing* AP profile up/down — it
  does not create one (see the snippet above).
- The bots framework and the MQTT bridge are both now started by the GUI
  process itself (no separate runner / bridge services). Disabling all
  bots or unchecking MQTT in Config leaves them idle but loaded.

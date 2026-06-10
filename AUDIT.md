# Audit del codice — pi-mesh-gui

Data: 2026-06-10 · Base: `fe875ab` · Suite di test: **353 passed, 15 skipped** (servono `aiosqlite` e `pytest-asyncio`, assenti da `requirements.txt`)

Audit completo di tutto il codice non di test (~14.400 righe): moduli backend, pacchetto `bots/`, infrastruttura e pagine GUI, script shell, unit systemd. Ogni finding indica file:riga, gravità e confidenza. I finding marcati **[verificato]** sono stati riconfermati manualmente o tramite esecuzione (pyflakes, `systemd-analyze verify`, esecuzione di `bc`).

---

## 1. Critici / Alta gravità

### 1.1 `hardware_ops.py:165` — il fallback `sudo tee` svuota `config.env` **[verificato]**
Il fallback su `PermissionError` esegue `await _run("sudo", "tee", path)` ma `_run` in questo modulo non passa nulla su stdin (a differenza di `display_ops._run`, che supporta `input_bytes`). Sotto systemd (stdin=/dev/null) `tee` legge EOF, esce con 0 e la funzione segnala successo dopo aver **troncato config.env a zero byte**. Con una tty reale resta appeso 10 s e viene ucciso. Confidenza: certa.

### 1.2 `meshtasticd_client.py:1372-1379` — la perdita della connessione seriale non viene mai rilevata
Il loop keep-alive è `while _connected:` ma nulla al suo interno può fallire: `_refresh_node_cache()` inghiotte ogni eccezione (riga 892) e leggere `_interface.nodes` è un accesso a dict che non fallisce a cavo staccato. Non c'è subscribe a `meshtastic.connection.lost` e solo `disconnect()` azzera `_connected`. Dopo uno scollegamento USB l'app resta "connessa" per sempre e la logica di riconnessione/backoff non gira mai. Confidenza: probabile.

### 1.3 Messaggi in uscita mai salvati nel DB (riscontro incrociato di 2 agenti)
`meshtasticd_client.send_text` (riga 115) si limita ad accodare il comando radio; nessun chiamante (`messages_page.py:279/416`, `_node_detail.py:242`, `bots/runner.py:280`) chiama mai `database.save_message` con `is_outgoing=True`. Conseguenze: i messaggi inviati spariscono a ogni reload/cambio canale/riavvio; `database.update_message_ack` (database.py:547) non può mai trovare la riga, quindi gli ACK non vengono mai persistiti; i thread DM perdono metà conversazione. Schema, logica ACK e test presuppongono che le righe outgoing esistano. Confidenza: probabile (grep su tutto il repo).

### 1.4 `gui/pages/_config_display.py:135,138,162` — `QMessageBox` mai importato: la rotazione schermo crasha **[verificato]**
`_on_rotation_clicked` e `_post_display` usano `QMessageBox.question/warning` ma l'import non esiste (confermato con pyflakes e ispezione). Toccare un pulsante di rotazione solleva `NameError` nello slot: la rotazione non è mai cambiabile e gli errori di apply non mostrano mai il dialogo. Confidenza: certa.

### 1.5 `gui/widgets/vkb.py:69,121,241-250` — la tastiera virtuale si chiude al primo tocco **[verificato]**
`NoFocus` è impostato solo sul frame (riga 69); i `QPushButton` mantengono la focus policy di default (la policy non si propaga ai figli — il commento alle righe 247-250 afferma il contrario). Il tap su un tasto emette `focusChanged(QLineEdit → QPushButton)` *prima* di `clicked`, quindi `_on_focus_changed` chiama `hide_keyboard()` e azzera `_target`: la tastiera si nasconde e non scrive nulla al primo tasto. Confidenza: certa.

### 1.6 `gui/app.py:106` + `gui/theme/palettes.py:79-82` — il tema "custom" blocca l'app a ogni avvio
La pagina Config offre `"custom"` (`_config_display.py:40`) e lo persiste, ma `get_palette("custom", custom=None)` solleva `ValueError`. A runtime l'eccezione è inghiottita dal subscriber, però il valore resta nel DB; al riavvio `apply_theme` esplode in `_async_main` prima del try/finally e la GUI crasha a ogni boot finché non si edita il DB a mano. La chiave `pimesh-custom-theme` viene caricata ma mai letta. Confidenza: certa.

### 1.7 `gui/pages/map_view.py:507-511` — overlay a coordinate stantie dopo lo zoom
Le coordinate di scena sono pixel Web-Mercator assoluti allo zoom corrente, quindi ogni item va riproiettato al cambio di zoom, ma `_reposition_markers()` è `pass`. I marker dei nodi si correggono solo col timer da 5 s, i waypoint con quello da 15 s, marker custom/traceroute/link vicini restano sbagliati (errore 2× per step di zoom) a tempo indeterminato. Confidenza: certa.

### 1.8 `requirements.txt` — clobberato: identico a `requirements-gui.txt` **[verificato]**
Contiene solo `PySide6-Essentials` (verificato con `diff`: i due file sono identici). `setup.sh install_core` non installa mai `meshtastic`, `aiosqlite`, `qasync` (tutti importati da `database.py`, `meshtasticd_client.py`, `gui/app.py`). Un'installazione pulita crasha all'import. Confidenza: certa.

### 1.9 `systemd/pimesh-gui.service:24` — il servizio ignora il venv creato da `setup.sh`
La unit esegue `/usr/bin/python3 -m gui` mentre `setup.sh` installa tutte le dipendenze pip in `$REPO_DIR/venv`. Esiste già il launcher venv-aware `scripts/start-gui.sh` ("Used by systemd/pimesh-gui.service") ma la unit non lo usa. Insieme a 1.8: il servizio distribuito non può partire su un Pi pulito. Confidenza: certa.

### 1.10 `scripts/manage-tiles.sh:84` — `_lon2x` in `bc` restituisce sempre 0 **[verificato dall'agente con esecuzione]**
Con `scale=0` bc tronca prima la divisione interna (`186.5/360 → 0`), quindi `x_min=x_max=0` per qualunque longitudine reale: il downloader di fallback scarica solo la colonna di tile x=0 (Pacifico) riportando successo. Confidenza: certa.

---

## 2. Gravità media

### Backend / client radio
- **`meshtasticd_client.py:1323-1333` — il command worker inghiotte ogni errore, senza timeout.** Ogni azione utente (invio, config, factory reset, admin) passa per `_command_worker` che cattura `Exception` e logga solo un warning, mentre la GUI ha già mostrato successo. `run_in_executor` non ha timeout: una chiamata meshtastic appesa (es. `setConfig` su link morto, vedi 1.2) blocca il worker per sempre, scartando in silenzio ogni comando successivo. Certa/probabile.
- **`meshtasticd_client.py:1307-1313` — `factory_reset()` non resetta.** `_do_factory_reset` chiama `setOwner('')` più una transazione vuota; l'API reale è `localNode.factoryReset()`. Il pulsante cancella il nome owner e logga "Factory reset executed" lasciando intatta la config del dispositivo. Probabile.
- **`meshtasticd_client.py:321, 112` — oggetti API sbagliati per le richieste di posizione.** `sendPosition` è un metodo di `MeshInterface`, non di `Node` → `AttributeError` inghiottito dal worker; `_interface.requestPosition(node_id)` non risulta esistere su `MeshInterface`. Probabile.
- **`meshtasticd_client.py:989/1021/1162` + `_message_format.py:37` + `_node_format.py:43` — `hopLimit` mostrato come "hops".** Viene salvato il TTL residuo, non gli hop percorsi (`hopStart − hopLimit`); lo stesso campo cambia semantica a seconda del percorso di aggiornamento (`hopsAway` nel refresh cache). Probabile.
- **`mqtt_bridge.py:25/74` — ponte MQTT morto end-to-end.** Nessuno chiama mai `set_ws_dispatch` (grep), e comunque i tipi emessi usano il trattino (`mqtt-message`) mentre `event_dispatcher` instrada sul prefisso `mqtt_`. Tutto il traffico MQTT→GUI è non funzionante. Certa.
- **`database.py:15-35` — connessione aiosqlite condivisa senza lock di transazione.** Le transazioni multi-statement (`bulk_upsert_nodes`, migrazioni, `delete_node`) si intrecciano agli `await` con `execute/commit` di altri task sulla stessa connessione: un `commit()` altrui può committare un bulk upsert a metà. Serve un `asyncio.Lock` attorno alle sezioni transazionali. Probabile.
- **`meshtasticd_client.py:1375-1379` — `SerialInterface` leakata nei retry di connect.** Il ramo `except` non chiama mai `_interface.close()`: ogni retry crea una nuova interfaccia mentre thread reader e FD della precedente restano vivi e continuano a pubblicare eventi. Probabile.

### Pagine GUI
- **`map_page.py:518-531` — i traceroute non risolvono mai gli hop.** Gli eventi `TRACEROUTE_APP` portano gli hop come interi grezzi (a differenza di `NEIGHBORINFO_APP`, che converte in `!%08x` a `meshtasticd_client.py:1214`), mentre `nodes_by_id` è indicizzato per stringhe `!hex`: ogni hop intermedio viene saltato e il traceroute è sempre una linea retta locale→destinazione. Probabile.
- **`map_page.py:199-206` — gli eventi di posizione corrompono etichetta e stile del marker.** `update_marker` senza `label`/`is_local` sovrascrive lo short-name con l'id grezzo e ri-stila il nodo locale come remoto fino al refresh dei 5 s. Certa.
- **`messages_page.py:260-266,455` — gli ACK spuntano il messaggio sbagliato.** `on_ack` ignora `event["node_id"]` e cerca dal basso il primo item contenente `"me:"` senza `"✓"` (il substring può combaciare anche con messaggi in arrivo); `ack_received` è connesso solo alla vista broadcast, quindi un ACK di DM spunta una riga broadcast. Probabile.
- **`messages_page.py:463-472` — un DM in arrivo non compare nella conversazione aperta.** `_on_incoming` chiama solo `self._dm.reload()` che aggiorna la *lista thread*, non il pannello messaggi aperto; il rebuild della lista azzera anche la selezione. Certa.
- **`messages_page.py:381-401` e `telemetry_page.py:136-159` — risultati async stantii applicati dopo il cambio di selezione.** Nessuna delle due ricontrolla `self._peer_id`/`self._selected_node` dopo l'`await`: vince l'ultima query che finisce (contenuto di A sotto l'header di B). Probabile.
- **`_config_sections.py:343-349` — PSK del canale accettata senza validazione; `_psk.is_valid_psk_b64` è codice morto.** Il testo va dritto a `set_channel`; input non valido esplode solo dopo, nel `base64.b64decode` del command worker, quando la pagina ha già mostrato successo. Nota: il validatore, se collegato, rifiuterebbe le PSK legali da 1 byte di Meshtastic (`AQ==`). Certa.
- **`_hardware_sections.py:381,438,471` — sezioni Map-config/USB operano su `data/tiles` ma la mappa legge `static/tiles`.** L'indicatore "Tiles present" è sbagliato e "Sposta tile su USB" ricolloca una directory che la mappa non usa, senza liberare spazio SD per il set reale. Certa.
- **`log_page.py:156-168,293-295` — Pausa perde eventi per sempre; Clear non svuota lo store.** In pausa `_append` ritorna prima di salvare in `self._lines` (il traffico è perso, non nascosto); `_on_clear` svuota solo il widget, quindi un cambio filtro resuscita le righe "cancellate" e l'export TSV le include. `_lines` è anche illimitato: leak lento su un kiosk 24/7. Certa.

### bots/ e deployment
- **`bots/config.py:38-57` — i parametri per-bot non vengono mai ricaricati dal DB.** `load()` legge solo `bots.prefix` e `bots.<name>.enabled`; i param (es. `bots.beacon.interval_seconds`) sono scritti ma mai riletti: dopo un riavvio l'intervallo beacon torna in silenzio al default di 600 s. Certa.
- **`bots/runner.py:200-211` — nessun rate limiting nel dispatch.** Qualunque nodo remoto può spammare `!ping`/`!nodes`/`!status` (anche in broadcast) e ogni messaggio produce una risposta broadcast senza cooldown, dedup o throttle: su LoRa è esaurimento di airtime/duty-cycle e amplificazione banale. Certa.
- **`systemd/pimesh-gui.service:32-33` — `StartLimitIntervalSec/Burst` in `[Service]` sono ignorati** (confermato da `systemd-analyze verify`): con `Restart=always` e `RestartSec=5` una GUI che crasha si riavvia all'infinito, al contrario di quanto promette il commento. Certa.
- **`systemd/meshtasticd.service:8` — flag errato e doppia apertura della seriale.** `--port` di meshtasticd è la porta TCP, non il device; in più la GUI apre direttamente `/dev/ttyACM0` (`SerialInterface`), quindi un daemon funzionante contenderebbe la porta. `setup.sh:84-92` abilita la unit senza installare il binario → restart loop perpetuo. Probabile.
- **`setup.sh:6` vs `pimesh-gui.service:11` — mismatch percorso/utente di installazione.** La unit hardcoda `/home/pimesh/pi-mesh-gui`; un clone altrove (es. `/home/pi/`) produce WorkingDirectory inesistente → fail + restart loop. Probabile.
- **`scripts/setup-permissions.sh:48-50` — wildcard sudoers `mount … * *`/`umount *` = escalation a root.** In sudoers `*` attraversa gli spazi: l'utente kiosk può montare filesystem arbitrari su `/etc` o `/usr/bin`, vanificando la whitelist minimale. Probabile.
- **`scripts/setup-rtc.sh:79-84` — la sed `s|^fi$|#fi|` corrompe `/lib/udev/hwclock-set`** commentando *tutti* i `fi` a colonna 0 (e ogni `exit 0` indentato), lasciando `if` sbilanciati in uno script invocato da udev. Probabile.
- **`scripts/auto_ap.sh:7-8,31-37` — password AP di default hardcoded (`meshtastic`), scritta world-readable in `/tmp`; ignora NetworkManager su Bookworm** (che possiede wlan0 e romperà l'AP). Probabile.
- **`scripts/setup-display.sh` + `calibrate-touch.sh` — stack X11 installato/calibrato, ma il servizio gira su linuxfb:** la calibrazione touch scritta in `/etc/X11/...` non è mai letta dalla GUI; `chmod 666 /dev/fb0` non è persistente e punta a fb0 mentre la unit usa fb1. Probabile.
- **`scripts/manage-tiles.sh:30,139-147` — sync verso il path obsoleto `pi-Mesh/static/tiles` e restart del servizio rimosso `pimesh`.** Drift dalla vecchia incarnazione web-app. Probabile.

---

## 3. Gravità bassa (selezione)

- `gui/widgets/vkb.py:168` — `done.clicked.connect(self.done.emit)` inoltra il bool di `clicked` a un `Signal()` senza parametri → `TypeError`, il tasto "✓" non fa nulla. Probabile.
- `gui/core/tasks.py:41` — `schedule()` scarta la Task: solo weak reference nel loop (possibile GC a metà volo) ed eccezioni riportate solo alla GC ("Task exception was never retrieved"); `app.py:135-150` mostra il pattern corretto. Probabile.
- `gui/app.py:111` — il selettore di colore accent persiste un valore che nessuno legge: feature morta presentata come funzionante. Certa.
- `gui/app.py:143-176` — fallimenti di setup tra lo spawn dei task e il try/finally lasciano girare i task di background mentre il loop viene chiuso. Possibile.
- `gui/core/settings.py:56-75` — scritture rapide sulla stessa chiave possono persistere fuori ordine (saver task indipendenti senza ordinamento per chiave). Possibile.
- `gui/widgets/collapsible.py:31-34` — `var(--text)` non è QSS valido: Qt rifiuta l'intera regola, il bottone header perde tutto lo styling. Certa.
- `gui/widgets/toast.py:114` — offset hardcoded 40 px contro `TABBAR_H = 44`: i toast sovrappongono la tab bar di 4 px. Certa.
- `gui/_qt_shim.py:43-49` — i submoduli PyQt6 falliti vengono saltati in silenzio: l'errore riemerge lontano dalla causa come `ModuleNotFoundError: PySide6.QtX`. Possibile.
- `gui/pages/_hardware_gpio.py:94-96` — `value() or None` trasforma il pin BCM 0 (valido) in NULL. Probabile.
- `gui/pages/map_page.py:243-249,177-178` — guardia solo su `latitude`: un nodo con lat valorizzata e lon `None` solleva `TypeError` e uccide in silenzio il task di refresh dei vicini. Possibile.
- `wifi_ops.py:172` (+ `_config_wifi.py:128-132`) — niente `--` prima dell'SSID in nmcli (un SSID che inizia con `-` viene letto come opzione); la password passa su argv, leggibile in `/proc/*/cmdline` per i 45 s del connect. Nessuna shell injection (exec usato correttamente). Probabile.
- `database.py:187` — il chmod 0600 non copre i sidecar `mesh.db-wal`/`-shm`, creati dopo con umask di default: PSK/password MQTT recenti restano world-readable nel WAL. Probabile.
- `usb_storage.py:86-87` — il ramo "already mounted" restituisce `None` come mountpoint: il device risulta non montato con spazio a zero. Certa.
- `usb_storage.py:186-190` — `restore_tiles_to_sd` rimuove il symlink prima della copia: se `copytree` fallisce a metà (USB estratta), `data/tiles` resta mancante/parziale senza rollback. Certa (logica).
- `system_ops.py:61-75` — `pi_factory_reset` cancella il DB con la connessione aiosqlite ancora aperta e usa path relativi alla CWD. Possibile.
- `bots/builtin/help.py:29` — `lstrip(prefix)` usa semantica char-set: con prefisso multi-carattere `@bot help beacon` → "Comando sconosciuto: eacon". Certa (meccanismo).
- `bots/runner.py:157` — fallback `from_id = event["id"]`, che è l'id intero di riga DB, non un node id: rompe il confronto anti-eco e produce destinazioni DM senza senso. Probabile.
- `bots/builtin/ping.py:19-21` — la "one-way RTT" è calcolata contro il timestamp di ricezione locale: misura la latenza di coda interna, non il transito radio. Certa.
- `scripts/download_tiles.py:94-96 vs :75` — il placeholder 0-byte per i 404 è vanificato dal check `getsize > 0`: ogni tile oceano viene ri-richiesta a ogni run e i PNG vuoti possono finire alla mappa come tile rotte. Certa.
- `scripts/capture_screenshots.py` — eseguito come documentato non può importare i moduli del progetto (`scripts/` su sys.path, non la cwd). Probabile.
- `setup.sh:23-28` vs `scripts/setup_zram.sh` — due meccanismi zram concorrenti (zramswap.service 50% RAM vs zram-swap.service 256M): swap impilati e unit duplicate. Probabile.
- `meshtasticd_client.py:880` — `raw_json` salvato come `str(dict)` Python, non JSON: qualunque futuro `json.loads` fallirà. Nota minore.

---

## 4. Aree verificate e risultate solide

- **Threading**: design a loop singolo qasync corretto; tutti gli eventi dal thread radio passano per `call_soon_threadsafe` → coda asyncio → segnali emessi sul thread GUI. Nessun accesso cross-thread a widget trovato.
- **`map_math.py`**: round-trip lat/lon↔tile, clamping ai poli e algebra dello zoom ancorato al cursore tutti corretti.
- **`SparklineBuffer`**, dispatcher eventi, `_psk.random_psk_b64` (crittograficamente ok, PSK mai loggate), formati nodo/telemetria: conformi ai test, nessun off-by-one.
- **Subprocess**: mai `shell=True` in tutto il repo; timeout presenti su ogni chiamata subprocess/rete nei moduli ops; sanitizzazione deliberata in `usb_storage`.
- **bots/**: nessun loop di risposta bot-a-bot possibile (gli echi propri non vengono accodati come eventi `message`); ben stratificato e testato.

## 5. Valutazione complessiva e priorità

I layer puri (matematica mappa, formattazione, buffer, dispatcher) sono solidi e ben testati; l'architettura asincrona è corretta. I problemi si concentrano in tre fasce:

1. **Pipeline radio fire-and-forget** (1.2, 1.3, 2-backend): ogni errore è nascosto all'utente, la disconnessione non è mai rilevata, i messaggi inviati non sono persistiti — combinati: "la radio ha smesso di funzionare in silenzio e niente te lo dice".
2. **Spigoli interattivi non testati** (1.4, 1.5, 1.6, 1.7): due crash certi, una tastiera virtuale inutilizzabile, overlay mappa sbagliati dopo lo zoom.
3. **Layer di deployment non ritestato dopo la migrazione GUI-only** (1.8, 1.9, sezione script): un'installazione pulita su Pi non produce un sistema funzionante.

Ordine di intervento suggerito: 1.1 (distrugge un file di config su un fallback di routine) → 1.8+1.9 (install rotta) → 1.4/1.5/1.6 (crash e UX) → 1.2+1.3+command-worker (affidabilità radio) → resto per gravità. Aggiungere `pyflakes`/`ruff` alla CI avrebbe intercettato 1.4 automaticamente.

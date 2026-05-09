# YAY-167 — Screenshot Feature Design

## Overview

Cattura dello schermo dal framebuffer del display (`/dev/fb0`) con salvataggio su USB esterna (se presente) o partizione boot della SD card. Attivato da un'icona fotocamera nella status bar.

## Requisiti

- Cattura framebuffer `/dev/fb0` tramite `fbgrab`
- Salvataggio prioritario su USB esterna, fallback su SD (`/boot/firmware/screenshots/`)
- Icona fotocamera nella status bar, sempre visibile
- Flash visivo dell'icona al momento dello scatto
- Toast con percorso del file salvato
- Naming incrementale: `screenshot_001.png`, `screenshot_002.png`, ...
- Nessuna galleria o gestione screenshot dalla UI

## Architettura

### Backend — Endpoint API

**File**: `routers/commands.py`

**Endpoint**: `POST /api/screenshot`

**Logica**:
1. Controlla USB via `usb_storage.get_usb_status()`
2. Se USB presente e montata → `dest_dir = <mountpoint>/pi-mesh/screenshots/`
3. Se no USB → `dest_dir = /boot/firmware/screenshots/`
4. Crea `dest_dir` se non esiste (`os.makedirs`)
5. Calcola prossimo numero incrementale: scansiona `dest_dir` per `screenshot_NNN.png`, prende il max e aggiunge 1
6. Esegue `fbgrab <dest_dir>/screenshot_NNN.png` via `subprocess.run`
7. Ritorna `{ "ok": true, "path": "screenshot_NNN.png", "location": "usb"|"sd" }`

**Errori**:
- `fbgrab` non installato → `{ "ok": false, "error": "fbgrab non installato" }`
- Cattura fallita → `{ "ok": false, "error": "<stderr>" }`
- Spazio insufficiente → non gestito esplicitamente (errore di fbgrab)

### Frontend — Status Bar

**File**: `templates/base.html`

**Modifica**: Aggiungere icona fotocamera SVG nella sezione destra della status bar, prima dell'icona batteria.

```html
<span id="screenshot-btn" title="Screenshot" onclick="takeScreenshot()"
      style="color:var(--muted); cursor:pointer; line-height:0;">
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
    <circle cx="12" cy="13" r="4"/>
  </svg>
</span>
```

**File**: `static/app.js`

**Funzione `takeScreenshot()`**:
1. Chiama `POST /api/screenshot`
2. Se successo:
   - Flash: imposta `color: white` sull'icona per 200ms, poi ripristina `var(--muted)`
   - Toast: `"Screenshot salvato: screenshot_NNN.png (USB)"` o `"(SD)"`
3. Se errore:
   - Toast errore con il messaggio

## Salvataggio — Percorsi

| Condizione | Percorso |
|---|---|
| USB presente e montata | `<mountpoint>/pi-mesh/screenshots/screenshot_NNN.png` |
| No USB | `/boot/firmware/screenshots/screenshot_NNN.png` |

## Numerazione Incrementale

- Scansiona la directory di destinazione per file matching `screenshot_(\d+)\.png`
- Prende il numero più alto trovato
- Nuovo file = max + 1, formattato a 3 cifre con zero-padding
- Se nessun file esistente, parte da `screenshot_001.png`

## Dipendenze

- `fbgrab` — da installare sul Pi: `sudo apt install fbgrab`
- `usb_storage.py` — modulo esistente, usato per rilevamento USB

## File da Modificare

1. `routers/commands.py` — aggiungere endpoint `POST /api/screenshot`
2. `templates/base.html` — aggiungere icona fotocamera nella status bar
3. `static/app.js` — aggiungere funzione `takeScreenshot()`

## Fuori Scope

- Galleria/viewer screenshot nella UI
- Formato diverso da PNG
- Screenshot da browser/HTML (solo framebuffer)
- Gestione spazio disco

# Map Controls Redesign — Spec

## Problema
- Pan/zoom bloccato: `maxBounds` con `maxBoundsViscosity: 1.0` impedisce di uscire dai bounds; `minZoom` troppo restrittivo
- Layer switcher `L.control.layers()` troppo grande su mobile, si sovrappone al panel toggle
- Controlli sparsi: panel toggle top-right, centra bottom-left, zoom bottom-right (incoerenti)
- Nodi visibili solo come pallini anonimi senza etichetta

## Design approvato

### 1. Pan/zoom libero
- Rimuovere `maxBounds` e `maxBoundsViscosity` dall'init mappa
- Rimuovere `minZoom` dal costruttore L.map (lasciare solo `maxZoom`)

### 2. Layer switcher compatto (custom)
- Rimuovere `L.control.layers()`
- Aggiungere gruppo di 3 pulsanti verticali in `map.html` (non in map.js)
- Pulsanti: Stradale (icona mappa), Topo (icona montagna), Satellite (icona satellite SVG)
- Stile: `background: rgba(10,12,20,0.55)`, `border: 1px solid rgba(42,58,74,0.5)`
- Pulsante attivo: highlight `var(--accent)` sul bordo
- Click → `leafletMap.removeLayer()` + `addTo()` del layer selezionato
- Nessuna emoji — solo SVG heroicons

### 3. Raggruppamento controlli
- **Top-right** (colonna verticale): panel toggle → layer switcher compatto
- **Bottom-right** (colonna verticale): centra-sulla-board → zoom +/−
- Spostare `btn-center-board` da bottom-left a bottom-right (sopra i pulsanti zoom)
- Zoom Leaflet: `position: 'bottomright'`
- Opacità uniforme `rgba(10,12,20,0.55)` su tutti i bottoni overlay

### 4. Nodi con etichetta (divIcon)
- In `updateMapMarker()`: sostituire `L.circleMarker` con `L.marker` + `L.divIcon`
- Il divIcon mostra un cerchio colorato con `short_name` centrato
- Dimensione: 34×34px, border-radius 50%, border 2px #fff, font-size 9px bold
- Colori: locale `#4a9eff` (+ glow), online `#4caf50`, offline `#555`
- Popup invariato (click apre info dettagliate)

## File modificati
- `static/map.js` — rimuovere maxBounds, rimuovere L.control.layers, updateMapMarker → divIcon
- `templates/map.html` — layer switcher custom, spostare btn-center-board, opacità 0.55
- `static/sw.js` — bump versione cache

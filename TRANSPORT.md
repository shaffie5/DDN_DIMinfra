# Transport modelling in DDN_DIMinfra

This document explains how the application models the **transport leg of
every raw-material component** declared in a Verantwoordingsnota (VN) or
Software-VN, both for the OpenStreetMap preview shown in the browser
**and** for the distance value pushed into the GPP-tool workbook.

The same pipeline runs for the classic VN flow (`/gpp/vn/...`) and the
Software VN flow (`/gpp/software-vn/...`); only the parser differs.

---

## 1. Pipeline overview

```
   VN / SVN Excel                  ┌────────────────────────────────┐
        │                          │  vn_to_gpp.map_plant()         │
        ▼                          │   • classify component         │
  vn_parser / software_vn_parser   │   • map mode_vn → mode_gpp     │
        │                          │   • _compute_distance()        │
        ▼                          └──────────────┬─────────────────┘
  VNPlant / SVNPlant                              │
        │                                         ▼
        │                              MappedComponent (per row):
        │                                 mode_gpp ∈ {Truck,Barge,
        │                                              Ship,Train,No}
        │                                 distance_km, distance_method
        ▼
  Browser preview map (Leaflet)  ◀─── flask_app._build_map_payload()
        │                              • geocode origin (Nominatim, cached)
        │                              • truck      → OSRM road geometry
        │                              • barge/ship → searoute waterway
        │                              • train      → straight line
        ▼
  GPP workbook (Input!L<row>)    ◀─── distance_km from MappedComponent
                                       (NEVER overwritten by the map router)
```

Two routing layers exist on purpose:

* **Source-of-truth distance** — the value written to GPP `Input!L{row}`
  comes from `vn_to_gpp._compute_distance()` and is what determines the
  EPD calculation.  It is either taken directly from the VN/SVN, looked
  up in the geocode + OSRM cache, or entered manually by the operator.
* **Visualisation distance / route geometry** — only used for the
  preview map. Computed by `flask_app._build_map_payload` so the
  operator can sanity-check that "Genk → Gaurain by barge" really does
  follow the Albertkanaal and not a 200 km diagonal across Limburg.
  This number never feeds back into the EPD result.

This separation is intentional: switching to a different waterway router
should never silently shift a published EPD.

---

## 2. Modes

The VN files use Dutch transport labels.  `vn_to_gpp.map_transport()`
collapses them onto the four GPP modes:

| `mode_vn` (VN/SVN cell) | `mode_gpp` | GPP `K` energy default |
|---|---|---|
| Vrachtwagen / Truck / blank | **Truck** | `Diesel_Euro6` |
| Binnenvaart / Barge | **Barge** | `Diesel_marine` |
| Schip / Ship | **Ship** | `Diesel_marine` |
| Trein / Train | **Train** | `Electricity_NL` |
| (none / on-site / Productieproces) | **Truck**, distance = 0.02 km | special-cased in `vn_to_gpp` |

The `mode_gpp` value drives **both** the GPP cell (`Input!J{row}`) and
which router is invoked for the preview map.

---

## 3. Distance computation (source of truth)

Implemented in `vn_to_gpp._compute_distance(plant, origin, mode_gpp)`.

1. **On-site** components (origin contains "Productieproces" /
   "On-site" / etc.) → `distance_km = 0.02`, method `"onsite"`. The
   token value > 0 is required by the GPP transport-check rule
   ("mode set ⇒ distance > 0").
2. **RAP (asphalt granulate)** → `distance_km = None`, method
   `"manual_required"`.  The browser blocks calculation until the
   operator fills it in (UI flag `manual_distance=True`).
3. **Truck** → `geo.geocode(origin)` (Nominatim, cached, with manual
   override file `data/geocode_overrides.json`) followed by
   `geo.osrm_route_km(plant, origin)` (driving distance + duration in
   minutes from public OSRM).  Method = `"osrm"`.  Falls back to
   `geo.haversine_km()` × a per-mode detour factor on OSRM failure.
4. **Barge / Ship / Train** → straight-line haversine × detour factor
   (1.30 default; configurable in `vn_to_gpp`).  These modes are not
   road-routed for the GPP value — using a maritime/canal router would
   shift EPD numbers in ways that are hard for the operator to vet.

The operator can always override any row through the preview UI; manual
distances are stored per-plant in
`data/session_store/<user>/{vn,svn}_manual_distances.json`.

---

## 4. Route geometry for the preview map

Implemented in `flask_app._build_map_payload()` and
`geo.waterway_route_geometry()`. Per component:

| `mode_gpp` | Router | Source label |
|---|---|---|
| **Truck** | `geo.osrm_route_geometry()` → public OSRM `route/v1/driving` GeoJSON | `osrm` |
| **Barge** / **Ship** | `geo.waterway_route_geometry()` → BRouter (if `BROUTER_URL` set) → searoute fallback | `brouter` / `searoute` (or `cache` on hit) |
| **Train** *(and any router failure)* | great-circle straight line between origin and plant | `straight` |

> ⚠️ **searoute caveat.** The `searoute` package is built on a global
> *maritime* network with very sparse inland coverage. For a Belgian
> inland leg such as Soignies → Gaurain (~30 km via the Canal
> Nimy-Blaton-Péronnes) it tends to detour out to the Schelde estuary
> and back, producing a route many times longer than the straight
> line. To prevent that nonsense reaching the UI we apply a sanity
> check: any routed length more than **3× the great-circle distance**
> is discarded and the leg falls back to a dashed straight line. The
> proper fix is to run a self-hosted **BRouter** instance with the
> bundled `barge.brf` profile — see §6 below.

### 4.1.bis  Self-hosted BRouter for inland waterways

A pre-wired Docker compose file and a custom `barge.brf` profile ship
in the repository:

```
docker-compose.brouter.yml
data/brouter/profiles/barge.brf
data/brouter/segments/.gitignore   # .rd5 tiles go here
```

Setup:

```bash
# 1) Download the segment tiles for your region (Belgium + NL):
mkdir -p data/brouter/segments && cd data/brouter/segments
curl -O http://brouter.de/brouter/segments4/E0_N50.rd5
curl -O http://brouter.de/brouter/segments4/E5_N50.rd5
curl -O http://brouter.de/brouter/segments4/E0_N55.rd5
curl -O http://brouter.de/brouter/segments4/E5_N55.rd5

# 2) Start the container (port 17777):
docker compose -f docker-compose.brouter.yml up -d

# 3) Tell Flask to use it:
export BROUTER_URL=http://127.0.0.1:17777     # PowerShell: $env:BROUTER_URL=...
python flask_app.py
```

`geo._brouter_route()` queries
`GET {BROUTER_URL}/brouter?lonlats=lon,lat|lon,lat&profile=barge&format=geojson`
and parses the `track-length` property to expose the routed length in
the UI tooltip. The `barge.brf` profile only allows `waterway=canal |
river | fairway | stream` and applies a small CEMT-class discount so
the router prefers main canals over tiny tributaries; locks add a
500 m cost penalty to model the lock-cycling delay.

You can override the profile via `BROUTER_PROFILE` (default `barge`)
and the request timeout via `BROUTER_TIMEOUT_S` (default 12 s).

### 4.1 Caching

* **Geocoding** — in-process `_GEOCODE_CACHE` plus
  `data/geocode_overrides.json` for manual pins.
* **Truck routes** — none (OSRM is fast); each preview load re-queries.
* **Waterway routes** — disk cache under `data/waterway_cache/<sha1>.json`
  keyed on `(mode, lat_a, lon_a, lat_b, lon_b)` rounded to 5 decimals.
  searoute is offline but networkx pathfinding is non-trivial for long
  legs; the cache makes repeat renders instant and reproducible.

### 4.2 Snapping origins to a navigable waterway

Nominatim resolves quarry names to the office building, which is rarely
on the canal.  searoute will then refuse to start (the nearest navigable
node is too far).  Manual quay coordinates can be pinned in
`data/waterway_terminals.json`:

```json
{
  "Genk": [50.9750, 5.5300],
  "Soignies": [50.5792, 4.0686]
}
```

When a Barge/Ship leg is requested, the routing function consults this
file first for both the origin and the asphalt plant.  The polyline is
then stitched as
`[origin marker] → [origin quay] → [waterway route] → [plant quay] → [plant marker]`
so the visual line still starts/ends at the markers but the long
middle portion follows the canal.

### 4.3 Fallback hierarchy (explicit)

```
Truck:  manual_override → OSRM       → straight line
Barge:  manual_override → searoute   → straight line  (dashed in UI)
Ship:   manual_override → searoute   → straight line  (dashed in UI)
Train:  manual_override → (none yet) → straight line  (dashed in UI)
```

Any polyline drawn from the straight-line fallback is rendered with a
dashed pattern in the legend so the operator can see at a glance which
routes are real geometry and which are estimates.

---

## 5. Frontend rendering (`templates/_origins_map.html`)

* **Plant** — red `P` divIcon marker on the base layer (always visible).
* **Origins** — coloured `circleMarker` per mode, grouped into per-mode
  `L.layerGroup`s.
* **Routes** — solid for routed geometry, dashed when the great-circle
  fallback is used. Barge/Ship lines are rendered as a wide
  semi-transparent blue ribbon under the green track to suggest a
  canal/river.
* **Layer control** (top-right) toggles each transport mode's markers
  and routes independently.
* **Popup** on each origin lists Categorie, Herkomst, Modus, and
  *Afstand (VN)* — the value that actually feeds the EPD.  When the
  router returns its own length it is added to the route tooltip
  ("… — 196 km vaarweg") so the operator can spot disagreement
  between the VN value and the routed length without it ever
  overwriting the source-of-truth.
* The partial loads after `window.load` to make sure the Leaflet copy
  from `base.html` is fully evaluated; loading a second copy of
  Leaflet inside the partial used to corrupt the SVG renderer and
  silently hide all `circleMarker`s.

---

## 6. Server endpoints

| Route | Purpose |
|---|---|
| `GET /gpp/vn/preview/<i>` | Render the VN preview page incl. map |
| `GET /gpp/vn/map.json` | Return the JSON payload for the Leaflet map (calls `_build_map_payload`) |
| `GET /gpp/software-vn/preview/<i>` | Same for the Software-VN flow |
| `GET /gpp/software-vn/map.json` | Same |

The map payload schema:

```json
{
  "plant":   { "lat": 50.56, "lon": 3.55, "label": "..." },
  "origins": [
    { "name": "...", "category": "coarse", "origin": "Genk",
      "mode": "Barge", "distance_km": 181.2,
      "lat": 50.97, "lon": 5.50 }
  ],
  "routes":  [
    { "name": "...", "mode": "Barge",
      "coords": [[lat, lon], ...],
      "source": "searoute|osrm|cache|straight",
      "routed_length_km": 196.4 }
  ]
}
```

---

## 7. Future work

* **Train** — no public router currently used.  Either pin manual
  routes via a dedicated overrides file, or integrate the
  Trafikverket / RNE OpenAPI when available.
* **Show routed-vs-VN delta** — flag rows where
  `routed_length_km` differs from `distance_km` by > 10 % so the
  operator can investigate before signing the EPD.

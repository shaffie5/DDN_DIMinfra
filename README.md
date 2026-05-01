# DDN — Digital Delivery Notes & Asphalt-Plant LCA

A Flask web application that combines two related workflows used by an
asphalt-plant operator:

1. **Digital Delivery Notes (DDN).** Create, route through 4-party
   signing (Client, Transporter, COPRO, Permit holder), and export to
   Excel for every asphalt delivery.
2. **Verantwoordingsnota → GPP / LCA pipeline.** Upload a
   *Verantwoordingsnota* (VN) workbook (or its newer *Software VN*
   variant), have the tool geocode every component origin, compute
   multimodal (truck → barge/ship → truck) transport distances, fill in
   the official Belgian Public-Procurement (GPP) calculation template,
   run its formulas via headless Excel, and present the resulting
   environmental-impact matrix.

The same Flask app serves both UIs from a single SQLite database and a
shared `data/` folder.

---

## Project structure

```
DDN_DIMinfra_New/
├─ flask_app.py               # Primary Flask entry point (this is the app)
├─ app.py                     # Legacy admin-log scaffold (see below)
│
├─ ddn/                       # Application package (all business logic)
│  ├─ __init__.py
│  ├─ _paths.py               # Single source of truth for filesystem paths
│  ├─ storage.py              # SQLite layer for delivery notes / signatures
│  ├─ excel_export.py         # DDN delivery-note .xlsx builder
│  ├─ geo.py                  # Geocoding + OSRM/waterway routing
│  ├─ mailer.py               # SMTP helper
│  ├─ ocr.py                  # Optional OCR for scanned notes
│  ├─ vn_parser.py            # Reads classic Verantwoordingsnota workbook
│  ├─ software_vn_parser.py   # Reads Software-VN workbook variant
│  ├─ vn_to_gpp.py            # Maps parsed components → GPP cells
│  ├─ gpp_engine.py           # Runs the GPP formulas via xlwings
│  └─ gpp_integration.py      # Glue between DDN and gpp_link
│
├─ gpp_link/                  # GPP standalone tools + Excel template
│
├─ templates/                 # Jinja2 templates (Flask default location)
├─ static/                    # CSS + JS  (Flask default location)
├─ data/                      # Persistent app data — see notes below
├─ output/gpp_filled/         # Generated GPP workbooks
│
├─ docs/                      # Developer docs (this README + extras)
│  ├─ ARCHITECTURE.md         # Module map + data flow
│  ├─ TRANSPORT.md            # Transport-mode / energy-mode reference
│  └─ README.legacy.md        # Old README (kept for reference)
│
├─ scripts/                   # Setup / one-off utilities
│  ├─ prepare-osrm.ps1        # Build local OSRM data
│  ├─ prepare-brouter.ps1     # Build local BRouter data
│  └─ geonames_to_geocode.py  # Build offline geocode index
│
├─ tools/                     # BRouter + JDK binaries (gitignored)
├─ docker-compose.routing.yml # OSRM + BRouter stack
├─ docker-compose.brouter.yml # BRouter only
├─ requirements.txt
└─ .gitignore
```

`data/` layout:

| Path                         | Purpose                                       |
|------------------------------|-----------------------------------------------|
| `ddn.sqlite`                 | Main DB (delivery notes, signatures, users)   |
| `signatures/`                | PNG signatures captured in the browser        |
| `logos/`                     | Plant / company logos shown on notes          |
| `geocode_overrides.json`     | ~500 MB GeoNames-derived offline geocoder     |
| `waterway_terminals.json`    | Manual quay pins (loading/unloading points)   |
| `waterway_cache/`            | On-disk cache of Overpass queries / routes    |
| `vn_uploads/`                | User-uploaded VN workbooks (per session)      |
| `software_vn_uploads/`       | User-uploaded Software-VN workbooks           |
| `session_store/`             | Per-session JSON state                        |
| `brouter/segments/`          | BRouter routing segments (large, gitignored)  |
| `osrm/`                      | OSRM preprocessed data (large, gitignored)    |

---

## Install & run

```powershell
git clone <repo>
cd DDN_DIMinfra_New
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python flask_app.py
```

The dev server listens on **http://127.0.0.1:5001**.

For OCR (`ocr.py`) you also need a Tesseract install on `PATH`. For the
GPP engine (`gpp_engine.py`) you need Microsoft Excel installed locally
because it uses `xlwings` to run the workbook formulas.

---

## Configuration (environment variables)

All settings have safe defaults; override only what you need.

### Server / security
| Variable                | Default     | Meaning                                |
|-------------------------|-------------|----------------------------------------|
| `DDN_SECRET_KEY`        | dev key     | Flask session signing key              |
| `DDN_COOKIE_SECURE`     | `0`         | Set to `1` behind HTTPS                |
| `DDN_MAX_UPLOAD_MB`     | `50`        | Max upload size                        |
| `DDN_SESSION_TTL_DAYS`  | `30`        | Session cookie lifetime                |
| `LOG_LEVEL`             | `INFO`      | Python logging level                   |

### Routing / geocoding
| Variable                            | Default                              | Meaning                                                  |
|-------------------------------------|--------------------------------------|----------------------------------------------------------|
| `DDN_OFFLINE`                       | `0`                                  | `1` disables Nominatim, OSRM, Overpass — all local       |
| `OSRM_URL`                          | `https://router.project-osrm.org`    | OSRM endpoint for road routing                           |
| `OSRM_TIMEOUT_S`                    | `10`                                 | Per-request timeout                                      |
| `BROUTER_URL`                       | (unset)                              | Optional local BRouter endpoint                          |
| `NOMINATIM_TIMEOUT_S`               | `8`                                  | Per-request timeout                                      |
| `NOMINATIM_MIN_INTERVAL_S`          | `1.0`                                | Throttle (Nominatim policy = 1 req/s)                    |
| `DDN_GEOCODE_COUNTRY_PREFERENCE`    | `be,nl,lu,fr,de,it,es,pl,no,dk,se`   | Tie-break order for ambiguous bare-name lookups          |
| `QUAY_SEARCH_RADIUS_KM`             | `20`                                 | Default search radius for nearest-quay (loading side)    |
| `PLANT_QUAY_SEARCH_RADIUS_KM`       | `50`                                 | Search radius for the unloading quay near the asphalt plant |

### SMTP (optional)
| Variable          | Meaning                                     |
|-------------------|---------------------------------------------|
| `DDN_SMTP_HOST`   | SMTP server                                 |
| `DDN_SMTP_PORT`   | e.g. 587                                    |
| `DDN_SMTP_USER`   | Login                                       |
| `DDN_SMTP_PASS`   | Password                                    |
| `DDN_SMTP_TLS`    | `true` / `false`                            |
| `DDN_FROM_EMAIL`  | From address used in outgoing mail          |

When SMTP is unset the app shows signing links in the UI instead of
mailing them — fine for local development.

---

## Main workflows

### A. Digital delivery note
1. Operator logs in → **Create note** (`/create`)
2. Enters lot, transporter, destination, signatures
3. Each party in turn opens their unique signing link (`/sign/<token>`)
4. After all 4 signatures the note is **released** and a final XLSX
   can be downloaded or e-mailed

### B. Verantwoordingsnota → GPP
1. **Upload** a VN or Software-VN workbook (`/gpp/vn` or `/gpp/software-vn`)
2. **Select plant** (`/.../select`) — the parser detects each plant sheet
3. **Preview** (`/.../preview/<idx>`) — components, geocoded origins,
   transport mode, distance per leg are shown on a map. Edit any
   origin in place if the auto-geocode is wrong.
4. **Calculate** — `vn_to_gpp` writes mapped values into the official
   GPP Excel template, `gpp_engine` runs its formulas via xlwings,
   the impact matrix is rendered.
5. **Download** the filled GPP workbook from `output/gpp_filled/`.

A failed validation check (sum ≠ 0) hides the result tables and shows
a red banner; the user must fix the inputs and recalculate.

---

## External services

| Service           | Used for                          | How to run locally                                   |
|-------------------|-----------------------------------|------------------------------------------------------|
| **OSRM**          | Road distances                    | `docker compose -f docker-compose.routing.yml up -d` |
| **BRouter**       | Inland-waterway routing fallback  | `docker compose -f docker-compose.brouter.yml up -d` |
| **Nominatim**     | Live geocoding fallback           | Public service (rate-limited), or self-host          |
| **Overpass API**  | Waterway graph + quay discovery   | Public service                                       |

When the public services are unreachable the app gracefully falls back
to haversine distances and the manual override JSON, so demos still
work offline.

---

## Known limitations

* **No automated tests.** Validate changes manually with the smoke
  flow: log in → upload sample VN → preview → calculate → download.
* `app.py` is a legacy scaffold (Flask-Login + Flask-SQLAlchemy with a
  separate `users.db`). It only powers the `/admin/logs` view. Either
  fold its admin route into `flask_app.py` and delete it, or keep it
  as a side utility (current state).
* Public OSRM and Overpass throttle / 429 under heavy use. Self-host
  or set `DDN_OFFLINE=1` if this becomes a problem.
* The GPP engine requires Microsoft Excel — it does not work on Linux
  / macOS without further work (e.g. swapping `xlwings` for
  `LibreOffice --headless --convert-to xlsx`).

---

## Future improvements

* **Move modules into a `ddn/` package** (Phase B of restructure).
  Centralise `BASE_DIR` / `DATA_DIR` resolution into one config module
  so paths no longer rely on `Path(__file__).parent`.
* Add a real test suite (`pytest`) covering parsers, vn_to_gpp
  classification, and the multimodal-leg builder.
* Replace the two backup-flavoured Flask apps (`flask_app.py` +
  `app.py`) with a single one — port the `/admin/logs` route across
  and delete `app.py`.
* Containerise the web app itself (currently only routing services
  are dockerised).
* Cache GPP formula evaluation results (warm-Excel) to cut response
  time of the *Calculate* step.

---

## See also

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module map, import
  graph, data-flow diagram and known risk hotspots.
* [docs/TRANSPORT.md](docs/TRANSPORT.md) — transport-mode and energy
  reference table used by `vn_to_gpp`.
* [docs/README.legacy.md](docs/README.legacy.md) — original short
  README, kept for reference.

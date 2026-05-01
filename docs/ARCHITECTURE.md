# Architecture

This document is the working map of the codebase. Pair it with the
[README](../README.md) for the user-facing description.

> Status: snapshot taken during the `chore/restructure` cleanup.
> Functionality unchanged — only docs, junk-deletions and folder moves
> were applied.

---

## 1. High-level module map

```
                     ┌─────────────────────────┐
                     │       flask_app.py      │  ← only Flask entry point in production
                     └────────────┬────────────┘
                                  │ (renders templates/, serves static/)
   ┌──────────────┬───────────────┼───────────────┬──────────────┐
   ▼              ▼               ▼               ▼              ▼
storage.py    excel_export.py   geo.py         mailer.py      ocr.py
(SQLite)      (DDN .xlsx)       (geo+routing)  (SMTP)         (Tesseract)

                                  │
                       VN / Software-VN pipeline
                                  │
   ┌────────────────────┬─────────┴─────────┬──────────────────┐
   ▼                    ▼                   ▼                  ▼
vn_parser.py    software_vn_parser.py   vn_to_gpp.py     gpp_engine.py
(read VN)       (read Software-VN)      (classify +      (xlwings → run
                                         map → cells)     GPP formulas)

                       gpp_integration.py  ──▶  gpp_link/
                                                ├─ standalone.py   (CLI)
                                                ├─ webservice.py   (separate Flask app)
                                                ├─ excel_updater.py
                                                ├─ file_manager.py
                                                ├─ config.py
                                                └─ PIONEERS GPP TOOL_*.xlsx
```

`app.py` (legacy) is **not** in the diagram on purpose: it runs as a
separate Flask app on port 5000 and only powers `/admin/logs`. It does
not import from any of the modules above.

---

## 2. Import graph (project modules only)

| From            | Imports                                                                 |
|-----------------|-------------------------------------------------------------------------|
| `flask_app`     | `storage`, `excel_export`, `geo`, `mailer`, `ocr`, `gpp_integration?`, `gpp_engine?`, `vn_parser?`, `vn_to_gpp?`, `software_vn_parser?` |
| `vn_to_gpp`     | `geo`, `vn_parser`                                                      |
| `excel_export`  | (none — has TODO marker for `gpp_integration`)                          |
| `gpp_integration` | `gpp_link.config`, `gpp_link.standalone`, `gpp_link.file_manager`     |
| `gpp_link.standalone` | `gpp_link.config`, `gpp_link.file_manager`, `gpp_link.excel_updater` |
| `gpp_link.webservice` | `gpp_link.standalone`, `gpp_link.config`                          |
| `app` (legacy)  | (none, self-contained)                                                  |

`?` = wrapped in `try/except ImportError` in `flask_app.py`. **A typo
in any of these import names will silently disable a route**; check the
log for `WARNING ... not available` after a restart.

---

## 3. Request flow — VN → GPP

```
POST /gpp/vn (upload XLSX)
  └▶ vn_parser.parse_vn_workbook(file)
       └▶ returns ParsedVN { plants: [...] }

GET /gpp/vn/select
  └▶ render vn_select.html (one card per plant)

GET /gpp/vn/preview/<plant_idx>
  ├▶ vn_to_gpp.classify_components(parsed_plant)
  ├▶ vn_to_gpp.build_mapping_dict(...)
  ├▶ flask_app._compute_component_legs(mapping)
  │   └▶ for each Barge/Ship row:
  │        - geo.geocode(origin)
  │        - geo.find_nearest_quay(origin_pt, radius=20 km)   ← loading quay
  │        - geo.find_nearest_quay(plant_pt,  radius=50 km)   ← unloading quay
  │        - geo.osrm_route_km(origin → loading quay)         (truck leg 1)
  │        - geo.waterway_route_km(loading quay → unloading)  (water leg)
  │        - geo.osrm_route_km(unloading quay → plant)        (truck leg 2)
  └▶ render vn_preview.html (table + map via /gpp/vn/map.json)

POST /gpp/vn/preview/<plant_idx>/edit
  └▶ overwrites a single component's coords / mode in the session JSON,
     then re-runs _compute_component_legs and redirects back to preview

POST /gpp/vn/calculate
  ├▶ vn_to_gpp.write_to_gpp_template(...)   → temp .xlsx
  ├▶ gpp_engine.run_calculation(temp_path)  → impact_matrix
  └▶ render vn_results.html
       (hides results + download button if validation check fails)
```

The Software-VN flow is identical with prefix `/gpp/software-vn/` and
parser `software_vn_parser`.

---

## 4. Data lifecycle

| Stage              | Stored as                                  | Cleared by                       |
|--------------------|--------------------------------------------|----------------------------------|
| Uploaded VN        | `data/vn_uploads/<sid>.xlsx`               | Manual, or session pruning       |
| Per-session state  | `data/session_store/<sid>/state.json`      | Session expiry                   |
| Geocode results    | in-process dict (`geo._GEOCODE_CACHE`)     | Process restart                  |
| Quay searches      | `data/waterway_cache/quays/*.json`         | Delete folder to invalidate      |
| Waterway networks  | `data/waterway_cache/networks/*.gpickle`   | Delete folder to invalidate      |
| GPP output         | `output/gpp_filled/GPP_*.xlsx`             | Delete folder; not referenced after download |
| DDN delivery notes | `data/ddn.sqlite` rows + `data/signatures/` | Manual                          |

> **Cache-invalidation gotcha.** When you change the search radius in
> `find_nearest_quay`, the disk cache will still serve the old
> negative result for previously-failed coordinates. Delete
> `data/waterway_cache/quays/` after such code changes.

---

## 5. Known risk hotspots

These are the places where an "innocent" refactor most often breaks
something:

1. **`__file__`-relative paths**
   - `storage.py`, `flask_app.py`, `gpp_engine.py`, `gpp_integration.py`,
     `gpp_link/config.py` all derive `BASE_DIR` from their own
     location. If any of them is moved into a package, these paths
     point to the wrong directory.
   - Mitigation in Phase B: introduce a single `ddn/_paths.py` and
     import `BASE_DIR`, `DATA_DIR`, `OUTPUT_DIR` from there.

2. **Silent optional imports** in `flask_app.py`
   - The `try: import vn_parser ... except: vn_parser = None` blocks
     swallow `ImportError`. After any rename or path change, search
     the log for `WARNING ... not available` to confirm nothing was
     dropped.

3. **Quay disk cache shadowing**
   - See note above; clear `data/waterway_cache/quays/` after radius
     or query changes in `geo.find_nearest_quay`.

4. **Public OSRM / Overpass throttling**
   - 429 responses are common during demos. The code falls back to
     haversine, but distance numbers will then be straight-line and
     ~10–20% lower than reality. Self-host for production.

5. **Excel / xlwings dependency**
   - `gpp_engine.run_calculation` requires Microsoft Excel locally.
     There is no Linux fallback.

6. **`app.py` vs `flask_app.py`**
   - Two separate Flask apps with two different auth stacks
     (`flask_login`+SQLAlchemy vs custom session). Don't run them on
     the same port. The legacy one's only justification is the
     `/admin/logs` view; consider porting and deleting.

---

## 6. Suggested Phase B restructure (NOT yet applied)

If/when behaviour-preserving refactor is approved:

```
DDN_DIMinfra_New/
├─ flask_app.py                # stays at root (entry point)
├─ ddn/                        # new package
│  ├─ __init__.py
│  ├─ _paths.py                # BASE_DIR / DATA_DIR / OUTPUT_DIR (one source)
│  ├─ storage.py
│  ├─ excel_export.py
│  ├─ mailer.py
│  ├─ ocr.py
│  ├─ geo.py
│  ├─ parsers/
│  │  ├─ vn_parser.py
│  │  └─ software_vn_parser.py
│  └─ gpp/
│     ├─ engine.py             # was gpp_engine.py
│     ├─ integration.py        # was gpp_integration.py
│     └─ vn_to_gpp.py
├─ gpp_link/                   # leave alone (it is its own self-contained subapp)
├─ templates/   static/   data/   output/   docs/   scripts/   tools/
└─ ...
```

Required follow-up edits in Phase B:

* `flask_app.py` imports → `from ddn import storage, geo, mailer, ocr, excel_export`
* `vn_to_gpp.py` → `from ddn import geo` and `from ddn.parsers import vn_parser`
* Replace every `Path(__file__).resolve().parent / "data"` with
  `from ddn._paths import DATA_DIR`.
* Smoke-test every route after the move.

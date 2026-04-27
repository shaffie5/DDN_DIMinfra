"""
flask_app.py — Flask version of the Digitale Leveringsbon application.

Run with:
    python flask_app.py
    # or
    flask --app flask_app run --debug --port 5000
"""

from __future__ import annotations

import base64
import io
import json
import random
import secrets
from datetime import datetime, date
from pathlib import Path
from typing import Any

from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, send_file, session, flash,
)

import excel_export
import geo
import mailer
import ocr
import storage

try:
    import gpp_integration
except Exception:
    gpp_integration = None

try:
    import gpp_engine
except Exception:
    gpp_engine = None

try:
    import vn_parser
    import vn_to_gpp
except Exception:
    vn_parser = None
    vn_to_gpp = None

try:
    import software_vn_parser
except Exception:
    software_vn_parser = None

# ─────────────────────────────────────────────────────────────────────
#  App Setup
# ─────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

APP_TITLE = "Digitale Leveringsbon"
BASE_DIR = Path(__file__).resolve().parent
LOGOS_DIR = BASE_DIR / "data" / "logos"

LOGO_FILES = [
    ("supar_logo.jpg", "SUPAR"),
    ("m4s.png", "University of Antwerp — M4S"),
    ("vlaio.png", "VLAIO"),
    ("DIMinfr@.png", "DIMinfr@"),
    ("pxl.png", "PXL Bouw & Industrie"),
]

ROLE_LABELS = {
    "client": "Opdrachtgever",
    "transporter": "Vervoerder",
    "copro": "COPRO",
    "permit_holder": "Vergunninghouder",
}

ENERGY_SOURCES = [
    "Diesel_Euro5", "Diesel_Euro6",
    "Biodiesel_4.5%", "Biodiesel_7%", "Biodiesel_10%",
    "Biodiesel_20%", "Biodiesel_100%",
    "Electric", "Electric_green",
]

_ENERGY_SOURCE_MIGRATION = {
    "Diesel": "Diesel_Euro5",
    "Biodiesel": "Biodiesel_100%",
}

DEMO_DATA = {
    "delivery_note_no": "DDN-2026-{rand}",
    "transport_company": "Van Hoeck Transport NV",
    "license_plate": "1-ABC-234",
    "origin_query": "Colas Belgium, Héron, Belgium",
    "destination_query": "E40 werf, Erpe-Mere, Belgium",
    "plant_address": "Colas Belgium NV, Rue de l'Industrie 20, 4217 Héron, Belgium",
    "plant_lat": 50.5468,
    "plant_lon": 5.0972,
    "site_address": "Wegenwerken E40, Erpe-Mere, 9420 Oost-Vlaanderen, Belgium",
    "site_lat": 50.9284,
    "site_lon": 3.9681,
    "client_address": "Agentschap Wegen en Verkeer\nGraaf de Ferrarisgebouw\nKoning Albert II-laan 20 bus 4\n1000 Brussel\nBelgium",
    "product_mixture_type": "AC 14 surf B50/70 (ABb-4C)",
    "application": "Surface course – road rehabilitation E40",
    "certificate": "COPRO-C-2026/0487",
    "declaration_of_performance": "DoP-BE-2026-AC14-0042",
    "technical_data_sheet": "TDS-AC14-SurfB5070-v3.2",
    "mechanical_resistance": "Class 3 (EN 12697-12)",
    "fuel_resistance": "Not required",
    "deicing_resistance": "Resistant (EN 12697-37)",
    "bitumen_aggregate_affinity": "Satisfactory (EN 12697-11)",
    "disposal": "Recyclable – cat. I",
    "bruto_kg": 28450.0,
    "tare_weight_empty_kg": 14200.0,
    "net_total_quantity_ton": 14.25,
    "email_client": "jan.desmet@bouwbedrijf.be",
    "email_transporter": "dispatch@vanhoeck-transport.be",
    "email_copro": "inspectie@copro.eu",
    "email_permit_holder": "vergunning@wegenbouw.be",
    "energy_source": "Diesel_Euro5",
}


def _migrate_energy_source(payload: dict) -> None:
    old = payload.get("energy_source")
    if old and old in _ENERGY_SOURCE_MIGRATION:
        payload["energy_source"] = _ENERGY_SOURCE_MIGRATION[old]


# ─────────────────────────────────────────────────────────────────────
#  Template Helpers
# ─────────────────────────────────────────────────────────────────────

def _logo_b64(filename: str) -> str | None:
    p = LOGOS_DIR / filename
    if not p.exists():
        return None
    suffix = p.suffix.lower().lstrip(".")
    mime = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "svg": "image/svg+xml", "webp": "image/webp",
    }.get(suffix, "image/png")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


@app.context_processor
def inject_globals():
    logos = []
    for fname, alt in LOGO_FILES:
        uri = _logo_b64(fname)
        if uri:
            logos.append({"uri": uri, "alt": alt})
    return {
        "app_title": APP_TITLE,
        "logos": logos,
        "role_labels": ROLE_LABELS,
        "energy_sources": ENERGY_SOURCES,
        "email_enabled": mailer.email_enabled(),
    }


def _safe_filename(note_id: str) -> str:
    return "DDN_" + "".join(c for c in note_id if c.isalnum() or c in {"-", "_"}) + ".xlsx"


# ─────────────────────────────────────────────────────────────────────
#  Routes — Pages
# ─────────────────────────────────────────────────────────────────────

@app.before_request
def _init_storage():
    storage.init_db()


@app.route("/")
def home():
    note_id = request.args.get("note")
    role = request.args.get("role")
    if note_id and role:
        return redirect(url_for("sign_page", note=note_id, role=role))
    return render_template("home.html")


@app.route("/create", methods=["GET"])
def create_note_page():
    return render_template("create_note.html", demo_data=DEMO_DATA)


@app.route("/create", methods=["POST"])
def create_note_submit():
    delivery_note_no = (request.form.get("delivery_note_no") or "").strip()
    if not delivery_note_no:
        flash("Leveringsbonnummer is verplicht.", "error")
        return redirect(url_for("create_note_page"))

    existing = storage.get_note_by_delivery_note_no(delivery_note_no)
    if existing:
        flash("Er bestaat al een leveringsbon met dit nummer.", "error")
        return redirect(url_for("create_note_page"))

    now = datetime.now()
    note_id = secrets.token_urlsafe(10)

    plant_lat = _safe_float(request.form.get("plant_lat"), 50.85)
    plant_lon = _safe_float(request.form.get("plant_lon"), 4.35)
    site_lat = _safe_float(request.form.get("site_lat"), 50.85)
    site_lon = _safe_float(request.form.get("site_lon"), 4.35)

    plant_point = geo.GeoPoint(lat=plant_lat, lon=plant_lon, label="Plant")
    site_point = geo.GeoPoint(lat=site_lat, lon=site_lon, label="Site")
    route = geo.osrm_route_km(plant_point, site_point)
    if route:
        distance_km = route[0]
    else:
        distance_km = geo.haversine_km(plant_point, site_point)

    payload = {
        "date": request.form.get("date") or date.today().isoformat(),
        "client_address": request.form.get("client_address") or "",
        "plant_address": request.form.get("plant_address") or "",
        "delivery_note_no": delivery_note_no,
        "site_address": request.form.get("site_address") or "",
        "departure_time": now.strftime("%H:%M"),
        "departure_time_iso": now.isoformat(timespec="seconds"),
        "arrival_time": "",
        "distance_km": float(distance_km),
        "plant_lat": plant_lat,
        "plant_lon": plant_lon,
        "site_lat": site_lat,
        "site_lon": site_lon,
        "transport_company": request.form.get("transport_company") or "",
        "license_plate": request.form.get("license_plate") or "",
        "transport_type": "Truck",
        "energy_source": request.form.get("energy_source") or "Diesel_Euro5",
        "product_mixture_type": request.form.get("product_mixture_type") or "",
        "application": request.form.get("application") or "",
        "certificate": request.form.get("certificate") or "",
        "declaration_of_performance": request.form.get("declaration_of_performance") or "",
        "technical_data_sheet": request.form.get("technical_data_sheet") or "",
        "mechanical_resistance": request.form.get("mechanical_resistance") or "",
        "fuel_resistance": request.form.get("fuel_resistance") or "",
        "deicing_resistance": request.form.get("deicing_resistance") or "",
        "bitumen_aggregate_affinity": request.form.get("bitumen_aggregate_affinity") or "",
        "disposal": request.form.get("disposal") or "",
        "bruto_kg": _safe_float(request.form.get("bruto_kg"), 0.0),
        "tare_weight_empty_kg": _safe_float(request.form.get("tare_weight_empty_kg"), 0.0),
        "net_total_quantity_ton": _safe_float(request.form.get("net_total_quantity_ton"), 0.0),
        "emails": {
            "client": request.form.get("email_client") or "",
            "transporter": request.form.get("email_transporter") or "",
            "copro": request.form.get("email_copro") or "",
            "permit_holder": request.form.get("email_permit_holder") or "",
        },
    }

    storage.create_note(note_id, delivery_note_no, payload)
    storage.set_status(note_id, "released")

    # Send emails if configured
    if mailer.email_enabled():
        for role in ROLE_LABELS:
            email = payload["emails"].get(role)
            if not email:
                continue
            link = url_for("sign_page", note=note_id, role=role, _external=True)
            try:
                mailer.send_email(
                    [email],
                    subject=f"Ondertekeningsverzoek leveringsbon ({delivery_note_no})",
                    body=(
                        "Gelieve de digitale leveringsbon te bekijken en "
                        f"te ondertekenen via deze link:\n\n{link}\n"
                    ),
                )
            except Exception:
                pass

    return render_template(
        "note_released.html",
        note_id=note_id,
        delivery_note_no=delivery_note_no,
        payload=payload,
    )


@app.route("/site-supervisor")
def site_supervisor_page():
    available = storage.list_delivery_note_nos(status="released", limit=200)
    return render_template("site_supervisor.html", available_notes=available)


@app.route("/receive-delivery", methods=["POST"])
def receive_delivery():
    dn = (request.form.get("delivery_note_no") or "").strip()
    if not dn:
        flash("Voer het leveringsbonnummer in.", "error")
        return redirect(url_for("site_supervisor_page"))

    note = storage.get_note_by_delivery_note_no(dn)
    if not note:
        flash("Geen leveringsbon gevonden voor dit nummer.", "error")
        return redirect(url_for("site_supervisor_page"))

    if note.get("status") == "pending":
        flash("Deze leveringsbon is nog niet vrijgegeven.", "error")
        return redirect(url_for("site_supervisor_page"))

    payload = note["payload"]
    _migrate_energy_source(payload)

    now = datetime.now()
    payload["arrival_time"] = now.strftime("%H:%M")
    payload["arrival_time_iso"] = now.isoformat(timespec="seconds")

    with storage.get_conn() as conn:
        conn.execute(
            "UPDATE delivery_notes SET payload_json=?, status=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False), "received", note["id"]),
        )

    sigs = storage.list_signatures(note["id"])
    xlsx_bytes = excel_export.build_delivery_note_xlsx(payload, sigs)

    # Email if configured
    emails = [
        payload.get("emails", {}).get("client"),
        payload.get("emails", {}).get("transporter"),
        payload.get("emails", {}).get("copro"),
        payload.get("emails", {}).get("permit_holder"),
    ]
    emails = [e for e in emails if e]
    emailed = False
    if emails and mailer.email_enabled():
        try:
            mailer.send_email(
                emails,
                subject=f"DDN (aankomst geregistreerd) ({payload.get('delivery_note_no') or note['id']})",
                body="Aankomsttijd is geregistreerd door de werftoezichter.",
                attachments=[(_safe_filename(note["id"]), xlsx_bytes,
                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")],
            )
            emailed = True
        except Exception:
            pass

    return render_template(
        "delivery_received.html",
        note=note,
        payload=payload,
        sigs=sigs,
        emailed=emailed,
        emails=emails,
    )


@app.route("/sign")
def sign_page():
    note_id = request.args.get("note", "")
    role = request.args.get("role", "")
    if not note_id or not role or role not in ROLE_LABELS:
        flash("Ongeldige ondertekeningslink.", "error")
        return redirect(url_for("home"))

    note = storage.get_note(note_id)
    if not note:
        flash("Onbekende leveringsbon.", "error")
        return redirect(url_for("home"))

    payload = note["payload"]
    _migrate_energy_source(payload)
    sigs = storage.list_signatures(note_id)

    return render_template(
        "sign.html",
        note_id=note_id,
        role=role,
        role_label=ROLE_LABELS[role],
        payload=payload,
        sigs=sigs,
        is_signed=role in sigs,
        fully_signed=storage.is_fully_signed(note_id),
    )


@app.route("/sign", methods=["POST"])
def sign_submit():
    note_id = request.form.get("note_id", "")
    role = request.form.get("role", "")
    signer_name = (request.form.get("signer_name") or "").strip()
    signature_data = request.form.get("signature_data", "")

    if not note_id or not role or role not in ROLE_LABELS:
        return jsonify({"error": "Ongeldige gegevens"}), 400

    note = storage.get_note(note_id)
    if not note:
        return jsonify({"error": "Onbekende leveringsbon"}), 404

    if not signature_data:
        return jsonify({"error": "Geen handtekening vastgelegd"}), 400

    # Decode base64 PNG from canvas
    if "," in signature_data:
        signature_data = signature_data.split(",", 1)[1]

    try:
        img_bytes = base64.b64decode(signature_data)
    except Exception:
        return jsonify({"error": "Ongeldige handtekeninggegevens"}), 400

    sig_path = storage.SIGNATURES_DIR / f"{note_id}_{role}.png"
    storage.SIGNATURES_DIR.mkdir(parents=True, exist_ok=True)
    sig_path.write_bytes(img_bytes)

    storage.upsert_signature(note_id, role, signer_name or None, str(sig_path))

    fully_signed = storage.is_fully_signed(note_id)
    if fully_signed:
        storage.mark_completed(note_id)
        payload = note["payload"]
        _migrate_energy_source(payload)
        sigs = storage.list_signatures(note_id)
        data_dir = BASE_DIR / "data" / "exports"
        out_path = data_dir / _safe_filename(note_id)
        excel_export.build_delivery_note_xlsx(payload, sigs, output_path=out_path)

    return jsonify({
        "success": True,
        "fully_signed": fully_signed,
        "message": f"Handtekening opgeslagen voor {ROLE_LABELS.get(role, role)}!",
    })


@app.route("/download/<note_id>")
def download_excel(note_id: str):
    note = storage.get_note(note_id)
    if not note:
        flash("Onbekende leveringsbon.", "error")
        return redirect(url_for("home"))

    payload = note["payload"]
    _migrate_energy_source(payload)
    sigs = storage.list_signatures(note_id)
    xlsx_bytes = excel_export.build_delivery_note_xlsx(payload, sigs)

    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=_safe_filename(note_id),
    )


# ─────────────────────────────────────────────────────────────────────
#  API Routes (AJAX)
# ─────────────────────────────────────────────────────────────────────

@app.route("/api/search-locations", methods=["POST"])
def api_search_locations():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if len(query) < 3:
        return jsonify([])

    from geopy.geocoders import Nominatim
    try:
        geolocator = Nominatim(user_agent="ddn_prototype")
        results = geolocator.geocode(query, exactly_one=False, limit=6)
        if not results:
            return jsonify([])
        return jsonify([
            {"label": str(getattr(r, "address", query)),
             "lat": float(r.latitude), "lon": float(r.longitude)}
            for r in results
        ])
    except Exception:
        return jsonify([])


@app.route("/api/route-info", methods=["POST"])
def api_route_info():
    data = request.get_json(silent=True) or {}
    plant_lat = _safe_float(data.get("plant_lat"), None)
    plant_lon = _safe_float(data.get("plant_lon"), None)
    site_lat = _safe_float(data.get("site_lat"), None)
    site_lon = _safe_float(data.get("site_lon"), None)

    if None in (plant_lat, plant_lon, site_lat, site_lon):
        return jsonify({"error": "Missing coordinates"}), 400

    plant_point = geo.GeoPoint(lat=plant_lat, lon=plant_lon, label="Plant")
    site_point = geo.GeoPoint(lat=site_lat, lon=site_lon, label="Site")

    route = geo.osrm_route_km(plant_point, site_point)
    route_coords = geo.osrm_route_geometry(plant_point, site_point)

    if route:
        distance_km, duration_min = route
        return jsonify({
            "distance_km": round(distance_km, 1),
            "duration_min": round(duration_min, 0),
            "source": "osrm",
            "route_coords": route_coords,
        })
    else:
        distance_km = geo.haversine_km(plant_point, site_point)
        return jsonify({
            "distance_km": round(distance_km, 1),
            "duration_min": None,
            "source": "haversine",
            "route_coords": None,
        })


@app.route("/api/ocr-scan", methods=["POST"])
def api_ocr_scan():
    if not ocr.is_available():
        return jsonify({"error": "OCR dependencies not installed", "missing": ocr.missing_dependencies()}), 503

    uploaded = request.files.get("file")
    if not uploaded:
        return jsonify({"error": "No file uploaded"}), 400

    try:
        raw_text, field_details = ocr.scan_and_extract_detailed(
            uploaded.stream,
            content_type=uploaded.content_type,
            filename=uploaded.filename,
        )
        return jsonify({"raw_text": raw_text, "fields": field_details})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/push-gpp", methods=["POST"])
def api_push_gpp():
    if gpp_integration is None:
        return jsonify({"error": "GPP integration not available"}), 503

    data = request.get_json(silent=True) or {}
    note_id = data.get("note_id", "")
    note = storage.get_note(note_id)
    if not note:
        return jsonify({"error": "Note not found"}), 404

    payload = note["payload"]
    _migrate_energy_source(payload)
    sigs = storage.list_signatures(note_id)

    try:
        result = gpp_integration.push_to_gpp(payload, sigs)
        return jsonify({"success": True, "message": result})
    except NotImplementedError:
        return jsonify({"error": "GPP push not yet implemented"}), 501
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calculate-gpp", methods=["POST"])
def api_calculate_gpp():
    if gpp_engine is None:
        return jsonify({"error": "GPP engine not available"}), 503

    data = request.get_json(silent=True) or {}
    note_id = data.get("note_id", "")
    note = storage.get_note(note_id)
    if not note:
        return jsonify({"error": "Note not found"}), 404

    payload = note["payload"]
    _migrate_energy_source(payload)

    try:
        engine = gpp_engine.GPPEngine()
        results = engine.calculate(payload)
        return jsonify({"success": True, "results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────
#  Verantwoordingsnota (VN) → GPP integration
# ─────────────────────────────────────────────────────────────────────

VN_UPLOAD_DIR = BASE_DIR / "data" / "vn_uploads"
VN_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@app.route("/gpp/vn", methods=["GET"])
def vn_upload_page():
    if vn_parser is None:
        flash("VN parser module is not available.", "error")
        return redirect(url_for("home"))
    return render_template("vn_upload.html")


@app.route("/gpp/vn", methods=["POST"])
def vn_upload_submit():
    if vn_parser is None:
        flash("VN parser module is not available.", "error")
        return redirect(url_for("home"))

    uploaded = request.files.get("vn_file")
    if not uploaded or not uploaded.filename:
        flash("Selecteer een Verantwoordingsnota Excel-bestand.", "error")
        return redirect(url_for("vn_upload_page"))

    try:
        raw = uploaded.read()
        vn_data = vn_parser.parse(raw, source_filename=uploaded.filename)
    except Exception as e:
        flash(f"Kon het VN-bestand niet inlezen: {e}", "error")
        return redirect(url_for("vn_upload_page"))

    # Persist to disk so the user can return to the selection page later.
    token = secrets.token_urlsafe(8)
    save_path = VN_UPLOAD_DIR / f"vn_{token}.xlsx"
    save_path.write_bytes(raw)

    session["vn_token"] = token
    session["vn_filename"] = uploaded.filename
    session["vn_data"] = vn_data.to_dict()

    return redirect(url_for("vn_select_plant"))


@app.route("/gpp/vn/select", methods=["GET"])
def vn_select_plant():
    if "vn_data" not in session:
        flash("Geen VN-bestand geladen. Upload eerst een bestand.", "error")
        return redirect(url_for("vn_upload_page"))
    return render_template(
        "vn_select.html",
        vn_data=session["vn_data"],
        vn_filename=session.get("vn_filename", ""),
    )


@app.route("/gpp/vn/preview/<int:plant_index>", methods=["GET"])
def vn_preview(plant_index: int):
    if "vn_data" not in session or vn_parser is None or vn_to_gpp is None:
        flash("Sessiegegevens verlopen. Upload het VN-bestand opnieuw.", "error")
        return redirect(url_for("vn_upload_page"))
    if plant_index not in (0, 1, 2):
        flash("Ongeldige asfaltcentrale.", "error")
        return redirect(url_for("vn_select_plant"))

    # Re-build VNData from the dict in the session
    plants_dicts = session["vn_data"]["plants"]
    if plant_index >= len(plants_dicts):
        flash("Asfaltcentrale niet gevonden in VN-bestand.", "error")
        return redirect(url_for("vn_select_plant"))

    plant = _vn_plant_from_dict(plants_dicts[plant_index])
    mapping = vn_to_gpp.map_plant(plant)

    # Cache on session for the calculate step
    session["vn_mapping"] = mapping.to_dict()
    session["vn_plant_index"] = plant_index

    return render_template(
        "vn_preview.html",
        mapping=mapping.to_dict(),
        plant_dict=plants_dicts[plant_index],
    )


@app.route("/gpp/vn/preview/<int:plant_index>/edit", methods=["POST"])
def vn_preview_edit(plant_index: int):
    """Apply manual herkomst / aanvoer-per overrides and re-render."""
    if "vn_data" not in session or vn_parser is None or vn_to_gpp is None:
        flash("Sessiegegevens verlopen. Upload het VN-bestand opnieuw.", "error")
        return redirect(url_for("vn_upload_page"))

    plants_dicts = session["vn_data"]["plants"]
    if plant_index not in (0, 1, 2) or plant_index >= len(plants_dicts):
        flash("Ongeldige asfaltcentrale.", "error")
        return redirect(url_for("vn_select_plant"))

    plant_dict = dict(plants_dicts[plant_index])
    plant_dict["components"] = _apply_origin_overrides(
        plant_dict.get("components", []), request.form
    )
    # Optional binder override
    bo = (request.form.get("binder_origin") or "").strip()
    bm = (request.form.get("binder_mode") or "").strip()
    if bo:
        plant_dict["binder_origin"] = bo
    if bm:
        plant_dict["binder_mode"] = bm

    plants_dicts[plant_index] = plant_dict
    session["vn_data"]["plants"] = plants_dicts
    session.modified = True

    return redirect(url_for("vn_preview", plant_index=plant_index))


@app.route("/gpp/vn/calculate", methods=["POST"])
def vn_calculate():
    if "vn_mapping" not in session:
        flash("Geen mapping beschikbaar. Begin opnieuw.", "error")
        return redirect(url_for("vn_upload_page"))
    if gpp_engine is None:
        flash("GPP engine is niet beschikbaar.", "error")
        return redirect(url_for("vn_select_plant"))

    mapping_dict = session["vn_mapping"]
    cell_payload: dict[str, Any] = dict(mapping_dict.get("general_cells") or {})
    for c in mapping_dict.get("components", []):
        r = c["gpp_row"]
        cell_payload[f"B{r}"] = round(c["pct_fraction"], 6)
        # Type column for slots that need it
        cat = c["category"]
        if cat in vn_to_gpp.GPP_TYPE_DEFAULTS:
            cell_payload[f"C{r}"] = vn_to_gpp.GPP_TYPE_DEFAULTS[cat]
        cell_payload[f"H{r}"] = c.get("origin") or ""
        cell_payload[f"J{r}"] = c["mode_gpp"]
        cell_payload[f"K{r}"] = c["energy_gpp"]
        cell_payload[f"L{r}"] = (
            int(round(c["distance_km"])) if c.get("distance_km") is not None else 0
        )
        for col in ("M", "N", "P", "Q"):
            cell_payload[f"{col}{r}"] = "No"
        cell_payload[f"O{r}"] = 0
        cell_payload[f"R{r}"] = 0

    # Minimum DDN payload — VN is the source of truth; we only need a
    # transport-to-site stub so GPP's section 5 doesn't trip its check.
    payload = {
        "transport_mode": "Truck",
        "energy_source":  "Diesel_Euro6",
        "distance_km":    20,
        "bruto_kg":       20000,
    }

    try:
        engine = gpp_engine.GPPEngine()
        results = engine.calculate(payload, extra_cells=cell_payload)
    except Exception as e:
        flash(f"GPP-berekening mislukt: {e}", "error")
        return redirect(url_for("vn_preview", plant_index=session.get("vn_plant_index", 0)))

    return render_template(
        "vn_results.html",
        results=results,
        mapping=mapping_dict,
    )


def _vn_plant_from_dict(d: dict[str, Any]):
    """Rebuild a :class:`vn_parser.VNPlant` from its serialised dict."""
    comps = [vn_parser.VNComponent(**c) for c in d.get("components", [])]
    plant_kwargs = {k: v for k, v in d.items() if k != "components"}
    return vn_parser.VNPlant(components=comps, **plant_kwargs)


# ─────────────────────────────────────────────────────────────────────
#  Software VN Routes (separate pipeline)
# ─────────────────────────────────────────────────────────────────────

SVN_UPLOAD_DIR = BASE_DIR / "data" / "software_vn_uploads"
SVN_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_SVN_EXTRA_KEYS = (
    "energy_source_primary_secondary",
    "electric_share_equipment",
    "wheel_loader_fuel",
)


def _apply_origin_overrides(components: list[dict[str, Any]],
                            form) -> list[dict[str, Any]]:
    """Return a new component list with manual origin/mode overrides.

    Form fields (one per component, indexed by VN row number):
        origin_<vn_row>   string  (empty → keep original)
        mode_<vn_row>     string  (empty → keep original)
    """
    out: list[dict[str, Any]] = []
    for c in components:
        c2 = dict(c)
        row = c.get("row")
        new_origin = (form.get(f"origin_{row}") or "").strip()
        new_mode = (form.get(f"mode_{row}") or "").strip()
        if new_origin:
            c2["origin"] = new_origin
        if new_mode:
            c2["mode"] = new_mode
        out.append(c2)
    return out


def _build_map_payload(mapping_dict: dict[str, Any]) -> dict[str, Any]:
    """Build a JSON-friendly OpenStreetMap payload for the preview map.

    For every component with a known origin, geocode the origin and (for
    Truck mode) fetch the OSRM road geometry from origin → plant.  All
    coordinates are returned as ``[lat, lon]`` pairs ready for Leaflet.
    """
    plant_lat = mapping_dict.get("plant_lat")
    plant_lon = mapping_dict.get("plant_lon")
    plant_pt = (
        geo.GeoPoint(lat=float(plant_lat), lon=float(plant_lon),
                     label=mapping_dict.get("plant_location") or "Plant")
        if plant_lat is not None and plant_lon is not None else None
    )

    origins: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    for c in mapping_dict.get("components", []):
        if not c.get("origin"):
            continue
        origin_pt = geo.geocode(c["origin"])
        if origin_pt is None:
            continue
        origins.append({
            "name":     c["name"],
            "category": c["category"],
            "origin":   c["origin"],
            "mode":     c["mode_gpp"],
            "distance_km": c.get("distance_km"),
            "lat": origin_pt.lat,
            "lon": origin_pt.lon,
        })
        if not plant_pt:
            continue
        coords: list[tuple[float, float]] | None = None
        if c["mode_gpp"] == "Truck":
            coords = geo.osrm_route_geometry(origin_pt, plant_pt)
        if not coords:
            # Straight-line fallback for Ship/Barge/Train or OSRM failure
            coords = [(origin_pt.lat, origin_pt.lon), (plant_pt.lat, plant_pt.lon)]
        routes.append({
            "name":   c["name"],
            "mode":   c["mode_gpp"],
            "coords": coords,
        })

    return {
        "plant": {
            "lat":   plant_pt.lat if plant_pt else None,
            "lon":   plant_pt.lon if plant_pt else None,
            "label": mapping_dict.get("plant_location") or "Plant",
        },
        "origins": origins,
        "routes":  routes,
    }


@app.route("/gpp/vn/map.json")
def vn_map_data():
    if "vn_mapping" not in session:
        return jsonify({"error": "no mapping in session"}), 404
    return jsonify(_build_map_payload(session["vn_mapping"]))


@app.route("/gpp/software-vn/map.json")
def software_vn_map_data():
    if "svn_mapping" not in session:
        return jsonify({"error": "no mapping in session"}), 404
    return jsonify(_build_map_payload(session["svn_mapping"]))


@app.route("/gpp/software-vn", methods=["GET"])
def software_vn_upload_page():
    if software_vn_parser is None:
        flash("Software VN parser module is not available.", "error")
        return redirect(url_for("home"))
    return render_template("software_vn_upload.html")


@app.route("/gpp/software-vn", methods=["POST"])
def software_vn_upload_submit():
    if software_vn_parser is None:
        flash("Software VN parser module is not available.", "error")
        return redirect(url_for("home"))

    uploaded = request.files.get("svn_file")
    if not uploaded or not uploaded.filename:
        flash("Selecteer een Software VN Excel-bestand.", "error")
        return redirect(url_for("software_vn_upload_page"))

    try:
        raw = uploaded.read()
        svn_data = software_vn_parser.parse(raw, source_filename=uploaded.filename)
    except Exception as e:
        flash(f"Kon het Software VN-bestand niet inlezen: {e}", "error")
        return redirect(url_for("software_vn_upload_page"))

    token = secrets.token_urlsafe(8)
    save_path = SVN_UPLOAD_DIR / f"svn_{token}.xlsx"
    save_path.write_bytes(raw)

    # Drop any stale Software-VN session keys from a previous upload so
    # the templates always render the new parsed schema.
    for k in ("svn_token", "svn_filename", "svn_data",
              "svn_mapping", "svn_plant_index"):
        session.pop(k, None)

    session["svn_token"] = token
    session["svn_filename"] = uploaded.filename
    session["svn_data"] = svn_data.to_dict()

    return redirect(url_for("software_vn_select_plant"))


@app.route("/gpp/software-vn/select", methods=["GET"])
def software_vn_select_plant():
    if "svn_data" not in session:
        flash("Geen Software VN-bestand geladen. Upload eerst een bestand.", "error")
        return redirect(url_for("software_vn_upload_page"))
    return render_template(
        "software_vn_select.html",
        svn_data=session["svn_data"],
        svn_filename=session.get("svn_filename", ""),
    )


@app.route("/gpp/software-vn/preview/<int:plant_index>", methods=["GET"])
def software_vn_preview(plant_index: int):
    if "svn_data" not in session or vn_parser is None or vn_to_gpp is None:
        flash("Sessiegegevens verlopen. Upload het Software VN-bestand opnieuw.", "error")
        return redirect(url_for("software_vn_upload_page"))
    if plant_index not in (0, 1, 2):
        flash("Ongeldige asfaltcentrale.", "error")
        return redirect(url_for("software_vn_select_plant"))

    plants_dicts = session["svn_data"]["plants"]
    if plant_index >= len(plants_dicts):
        flash("Asfaltcentrale niet gevonden in Software VN-bestand.", "error")
        return redirect(url_for("software_vn_select_plant"))

    plant_dict = plants_dicts[plant_index]
    # Rebuild a SVNPlant (duck-compatible with VNPlant for vn_to_gpp.map_plant)
    comps = [software_vn_parser.SVNComponent(**c) for c in plant_dict.get("components", [])]
    plant_kwargs = {k: v for k, v in plant_dict.items() if k != "components"}
    plant = software_vn_parser.SVNPlant(components=comps, **plant_kwargs)
    mapping = vn_to_gpp.map_plant(plant)

    session["svn_mapping"] = mapping.to_dict()
    session["svn_plant_index"] = plant_index

    return render_template(
        "software_vn_preview.html",
        mapping=mapping.to_dict(),
        plant_dict=plant_dict,
    )


@app.route("/gpp/software-vn/preview/<int:plant_index>/edit", methods=["POST"])
def software_vn_preview_edit(plant_index: int):
    """Apply manual herkomst / aanvoer-per overrides and re-render."""
    if "svn_data" not in session or software_vn_parser is None or vn_to_gpp is None:
        flash("Sessiegegevens verlopen. Upload het Software VN-bestand opnieuw.", "error")
        return redirect(url_for("software_vn_upload_page"))

    plants_dicts = session["svn_data"]["plants"]
    if plant_index not in (0, 1, 2) or plant_index >= len(plants_dicts):
        flash("Ongeldige asfaltcentrale.", "error")
        return redirect(url_for("software_vn_select_plant"))

    plant_dict = dict(plants_dicts[plant_index])
    plant_dict["components"] = _apply_origin_overrides(
        plant_dict.get("components", []), request.form
    )
    bo = (request.form.get("binder_origin") or "").strip()
    bm = (request.form.get("binder_mode") or "").strip()
    if bo:
        plant_dict["binder_origin"] = bo
    if bm:
        plant_dict["binder_mode"] = bm

    plants_dicts[plant_index] = plant_dict
    session["svn_data"]["plants"] = plants_dicts
    session.modified = True

    return redirect(url_for("software_vn_preview", plant_index=plant_index))


@app.route("/gpp/software-vn/calculate", methods=["POST"])
def software_vn_calculate():
    if "svn_mapping" not in session:
        flash("Geen mapping beschikbaar. Begin opnieuw.", "error")
        return redirect(url_for("software_vn_upload_page"))
    if gpp_engine is None:
        flash("GPP engine is niet beschikbaar.", "error")
        return redirect(url_for("software_vn_select_plant"))

    mapping_dict = session["svn_mapping"]
    cell_payload: dict[str, Any] = dict(mapping_dict.get("general_cells") or {})
    for c in mapping_dict.get("components", []):
        r = c["gpp_row"]
        cell_payload[f"B{r}"] = round(c["pct_fraction"], 6)
        cat = c["category"]
        if cat in vn_to_gpp.GPP_TYPE_DEFAULTS:
            cell_payload[f"C{r}"] = vn_to_gpp.GPP_TYPE_DEFAULTS[cat]
        cell_payload[f"H{r}"] = c.get("origin") or ""
        cell_payload[f"J{r}"] = c["mode_gpp"]
        cell_payload[f"K{r}"] = c["energy_gpp"]
        cell_payload[f"L{r}"] = (
            int(round(c["distance_km"])) if c.get("distance_km") is not None else 0
        )
        for col in ("M", "N", "P", "Q"):
            cell_payload[f"{col}{r}"] = "No"
        cell_payload[f"O{r}"] = 0
        cell_payload[f"R{r}"] = 0

    payload = {
        "transport_mode": "Truck",
        "energy_source":  "Diesel_Euro6",
        "distance_km":    20,
        "bruto_kg":       20000,
    }

    try:
        engine = gpp_engine.GPPEngine()
        results = engine.calculate(payload, extra_cells=cell_payload)
    except Exception as e:
        flash(f"GPP-berekening mislukt: {e}", "error")
        return redirect(url_for("software_vn_preview", plant_index=session.get("svn_plant_index", 0)))

    return render_template(
        "software_vn_results.html",
        results=results,
        mapping=mapping_dict,
    )


@app.route("/api/demo-data")
def api_demo_data():
    data = dict(DEMO_DATA)
    rand_num = f"{random.randint(0, 99999):05d}"
    data["delivery_note_no"] = f"DDN-2026-{rand_num}"
    return jsonify(data)


@app.route("/api/delivery-notes")
def api_delivery_notes():
    status = request.args.get("status")
    notes = storage.list_delivery_note_nos(status=status, limit=200)
    return jsonify(notes)


# ─────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────

def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ─────────────────────────────────────────────────────────────────────
#  Run
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    storage.init_db()
    app.run(debug=True, host="127.0.0.1", port=5001)

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
import logging
import os
import random
import re
import secrets
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any

from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, send_file, send_from_directory, session, flash, abort,
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

from ddn import excel_export, geo, mailer, ocr, storage
from ddn._paths import WATERWAY_TERMINALS_PATH

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("ddn")

try:
    from ddn import gpp_integration
except Exception:
    gpp_integration = None

try:
    from ddn import gpp_engine
except Exception:
    gpp_engine = None

try:
    from ddn import vn_parser, vn_to_gpp
except Exception:
    vn_parser = None
    vn_to_gpp = None

try:
    from ddn import software_vn_parser
except Exception:
    software_vn_parser = None

# ─────────────────────────────────────────────────────────────────────
#  App Setup
# ─────────────────────────────────────────────────────────────────────

app = Flask(__name__)
# Use SECRET_KEY env var when set so sessions survive restarts (otherwise
# a fresh random key invalidates every signing link). Fall back to an
# ephemeral key only for local dev.
app.secret_key = os.getenv("DDN_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("DDN_COOKIE_SECURE", "0") == "1",
    MAX_CONTENT_LENGTH=int(os.getenv("DDN_MAX_UPLOAD_MB", "32")) * 1024 * 1024,
)

# ─────────────────────────────────────────────────────────────────────
#  Authentication (Flask-Login + SQLAlchemy users.db)
# ─────────────────────────────────────────────────────────────────────
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///users.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True)
    password_hash = db.Column(db.String(256))


@login_manager.user_loader
def _load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None


with app.app_context():
    db.create_all()


# Public endpoints that do NOT require authentication.
# - login/logout: obvious
# - static/data: assets
# - sign_page / sign_submit: external supervisor signing via tokenised link
# - home: handled inline (only allowed with note+role query params, else
#   redirects to /login)
_PUBLIC_ENDPOINTS = {
    "login", "logout", "static", "data_files", "data_logos",
    "sign_page", "sign_submit",
}


@app.before_request
def _require_login():
    endpoint = request.endpoint or ""
    if endpoint in _PUBLIC_ENDPOINTS:
        return None
    # Allow the external signing entrypoint: /?note=...&role=...
    if endpoint == "home" and request.args.get("note") and request.args.get("role"):
        return None
    if current_user.is_authenticated:
        return None
    return redirect(url_for("login", next=request.path))


@app.route("/data/logos/<path:filename>")
def data_logos(filename):
    # Serve logo assets from data/logos/ (used by login page, etc.)
    return send_from_directory(LOGOS_DIR, filename)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()
        if user and user.password_hash and check_password_hash(user.password_hash, password):
            login_user(user)
            nxt = request.args.get("next") or url_for("home")
            # Avoid open-redirect: only allow same-site relative paths.
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("home")
            return redirect(nxt)
        error = "Invalid credentials"
    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# Hard cap on individual base64-encoded signature payload (1 MB raw PNG
# is already absurd for a touch signature; reject anything larger).
MAX_SIGNATURE_BYTES = 2 * 1024 * 1024
# Reject session_set keys that contain anything other than these chars
# so a forged key cannot escape the per-user directory via path tricks.
_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
# Reject session user_id tokens that don't look like a token_urlsafe(12)
# output (16 base64url chars) so a forged cookie cannot reach arbitrary
# directories on disk.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")
# Conservative email pattern \u2014 not RFC-5322 complete, but rejects the
# obvious junk (missing @, missing TLD, leading/trailing dots, spaces).
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)+$"
)


def _is_valid_email(addr: str | None) -> bool:
    if not addr:
        return False
    addr = addr.strip()
    return len(addr) <= 254 and bool(_EMAIL_RE.match(addr))

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
            email = (payload["emails"].get(role) or "").strip()
            if not email:
                continue
            if not _is_valid_email(email):
                log.warning("Skipping invalid signing-email address %r for role %s", email, role)
                flash(f"Ongeldig e-mailadres voor {role}: {email!r} — geen ondertekenmail verstuurd.", "error")
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
            except Exception as e:
                log.exception("Failed to send signing email to %s for role %s: %s", email, role, e)

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
    emails = [e.strip() for e in emails if e and e.strip()]
    invalid = [e for e in emails if not _is_valid_email(e)]
    if invalid:
        log.warning("Dropping invalid arrival-notification e-mail(s): %s", invalid)
    emails = [e for e in emails if _is_valid_email(e)]
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
        except Exception as e:
            log.exception("Failed to send arrival-notification email: %s", e)

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
    # note_id is generated server-side via secrets.token_urlsafe; reject
    # anything that doesn't fit that shape so a forged form value cannot
    # escape the signatures directory.
    if not _USER_ID_RE.match(note_id):
        return jsonify({"error": "Ongeldig leveringsbon-id"}), 400

    note = storage.get_note(note_id)
    if not note:
        return jsonify({"error": "Onbekende leveringsbon"}), 404

    if not signature_data:
        return jsonify({"error": "Geen handtekening vastgelegd"}), 400

    # Strip data: URL prefix
    if "," in signature_data:
        signature_data = signature_data.split(",", 1)[1]

    # Reject oversize payloads before decoding (raw + base64 overhead).
    if len(signature_data) > MAX_SIGNATURE_BYTES * 4 // 3 + 64:
        return jsonify({"error": "Handtekening is te groot"}), 413

    try:
        img_bytes = base64.b64decode(signature_data, validate=False)
    except Exception:
        return jsonify({"error": "Ongeldige handtekeninggegevens"}), 400

    if len(img_bytes) > MAX_SIGNATURE_BYTES:
        return jsonify({"error": "Handtekening is te groot"}), 413
    # PNG magic bytes — reject anything else (canvas.toDataURL produces PNG).
    if not img_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return jsonify({"error": "Ongeldig handtekeningformaat (PNG vereist)"}), 400

    sig_path = storage.SIGNATURES_DIR / f"{note_id}_{role}.png"
    storage.SIGNATURES_DIR.mkdir(parents=True, exist_ok=True)
    # Atomic write so a concurrent re-sign cannot leave a partial file.
    tmp_path = sig_path.with_suffix(".png.tmp")
    tmp_path.write_bytes(img_bytes)
    os.replace(tmp_path, sig_path)

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

# Disk-backed session store — Flask's default cookie session is capped
# at ~4 KB, but the parsed VN/SVN workbook + mapping easily exceed that
# (causing "Sessiegegevens verlopen" errors after a redirect).  We keep
# only a small per-user token in the cookie and persist the heavy
# JSON-serialisable blobs on disk under data/session_store/<user>/.
SESSION_STORE_DIR = BASE_DIR / "data" / "session_store"
SESSION_STORE_DIR.mkdir(parents=True, exist_ok=True)


def _session_user_dir() -> Path:
    """Return (and lazily create) this user's session-store directory."""
    user = session.get("user_id")
    if not user or not _USER_ID_RE.match(str(user)):
        user = secrets.token_urlsafe(12)
        session["user_id"] = user
    udir = SESSION_STORE_DIR / user
    udir.mkdir(parents=True, exist_ok=True)
    return udir


def _safe_session_key(key: str) -> str:
    if not isinstance(key, str) or not _SAFE_KEY_RE.match(key):
        raise ValueError(f"Unsafe session key: {key!r}")
    return key


def _validated_user_dir() -> Path | None:
    """Resolve the current user's session dir, only if the cookie token
    looks valid. Returns None for unknown / forged user_ids."""
    user = session.get("user_id")
    if not user or not _USER_ID_RE.match(str(user)):
        return None
    return SESSION_STORE_DIR / user


def session_set(key: str, value: Any) -> None:
    """Persist ``value`` (JSON-serialisable) under ``key`` for this user."""
    safe = _safe_session_key(key)
    path = _session_user_dir() / f"{safe}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(value, default=str), encoding="utf-8")
    os.replace(tmp, path)


def session_get(key: str, default: Any = None) -> Any:
    udir = _validated_user_dir()
    if udir is None:
        return default
    try:
        safe = _safe_session_key(key)
    except ValueError:
        return default
    path = udir / f"{safe}.json"
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("session_get(%r) failed: %s", key, e)
        return default


def session_has(key: str) -> bool:
    udir = _validated_user_dir()
    if udir is None:
        return False
    try:
        safe = _safe_session_key(key)
    except ValueError:
        return False
    return (udir / f"{safe}.json").exists()


def session_pop(key: str) -> None:
    udir = _validated_user_dir()
    if udir is None:
        return
    try:
        safe = _safe_session_key(key)
    except ValueError:
        return
    path = udir / f"{safe}.json"
    if path.exists():
        try:
            path.unlink()
        except OSError as e:
            log.warning("session_pop(%r) failed: %s", key, e)


SESSION_TTL_SECONDS = int(os.getenv("DDN_SESSION_TTL_DAYS", "7")) * 86400


def _sweep_session_store() -> None:
    """Delete user session dirs older than SESSION_TTL_SECONDS. Best-effort."""
    if not SESSION_STORE_DIR.exists():
        return
    cutoff = time.time() - SESSION_TTL_SECONDS
    for user_dir in SESSION_STORE_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        try:
            mtimes = [p.stat().st_mtime for p in user_dir.iterdir()] or [user_dir.stat().st_mtime]
            if max(mtimes) < cutoff:
                for p in user_dir.iterdir():
                    p.unlink(missing_ok=True)
                user_dir.rmdir()
                log.info("swept session dir %s", user_dir.name)
        except OSError as e:
            log.warning("sweep failed for %s: %s", user_dir, e)


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
    session_set("vn_data", vn_data.to_dict())
    session_pop("vn_mapping")

    return redirect(url_for("vn_select_plant"))


@app.route("/gpp/vn/select", methods=["GET"])
def vn_select_plant():
    if not session_has("vn_data"):
        flash("Geen VN-bestand geladen. Upload eerst een bestand.", "error")
        return redirect(url_for("vn_upload_page"))
    return render_template(
        "vn_select.html",
        vn_data=session_get("vn_data"),
        vn_filename=session.get("vn_filename", ""),
    )


@app.route("/gpp/vn/preview/<int:plant_index>", methods=["GET"])
def vn_preview(plant_index: int):
    if not session_has("vn_data") or vn_parser is None or vn_to_gpp is None:
        flash("Sessiegegevens verlopen. Upload het VN-bestand opnieuw.", "error")
        return redirect(url_for("vn_upload_page"))
    if plant_index not in (0, 1, 2):
        flash("Ongeldige asfaltcentrale.", "error")
        return redirect(url_for("vn_select_plant"))

    # Re-build VNData from the dict in the session store
    vn_data_dict = session_get("vn_data") or {}
    plants_dicts = vn_data_dict.get("plants", [])
    if plant_index >= len(plants_dicts):
        flash("Asfaltcentrale niet gevonden in VN-bestand.", "error")
        return redirect(url_for("vn_select_plant"))

    plant = _vn_plant_from_dict(plants_dicts[plant_index])
    mapping = vn_to_gpp.map_plant(plant)
    manual_dists = (session_get("vn_manual_distances") or {}).get(str(plant_index), {})
    mapping.apply_manual_distances(manual_dists)
    coverage = (session_get("vn_coverage") or {}).get(str(plant_index), {})
    mapping.apply_coverage_overrides(coverage)

    # Cache on the disk-backed store for the calculate step
    mapping_dict = mapping.to_dict()
    # Pre-compute multimodal transport legs (truck → barge → truck) so the
    # preview table can show the actual per-leg breakdown instead of just
    # the single waterway distance.  Cached on disk for later GPP write.
    try:
        _compute_component_legs(mapping_dict)
    except Exception as e:
        log.warning("preview leg computation failed: %s", e)
    session_set("vn_mapping", mapping_dict)
    session["vn_plant_index"] = plant_index

    return render_template(
        "vn_preview.html",
        mapping=mapping_dict,
        plant_dict=plants_dicts[plant_index],
    )


@app.route("/gpp/vn/preview/<int:plant_index>/edit", methods=["POST"])
def vn_preview_edit(plant_index: int):
    # Handle manual quay entry for guaranteed accuracy
    if request.form.get("add_manual_quay"):
        quay_origin = (request.form.get("manual_quay_origin") or "").strip()
        quay_lat = request.form.get("manual_quay_lat")
        quay_lon = request.form.get("manual_quay_lon")
        if quay_origin and quay_lat and quay_lon:
            try:
                quay_lat = float(quay_lat)
                quay_lon = float(quay_lon)
                # Load or create waterway_terminals.json
                quay_path = WATERWAY_TERMINALS_PATH
                if quay_path.exists():
                    with quay_path.open("r", encoding="utf-8") as f:
                        quay_data = json.load(f)
                else:
                    quay_data = {}
                # Don't overwrite comments or format keys
                if quay_origin.lower() not in [k.lower() for k in quay_data.keys() if not k.startswith("_")]:
                    quay_data[quay_origin] = [quay_lat, quay_lon]
                    with quay_path.open("w", encoding="utf-8") as f:
                        json.dump(quay_data, f, indent=2, ensure_ascii=False)
                    flash(f"Handmatige kade toegevoegd voor '{quay_origin}'.", "success")
                else:
                    flash(f"Kade voor '{quay_origin}' bestaat al.", "warning")
            except Exception as e:
                flash(f"Fout bij toevoegen van kade: {e}", "error")
        else:
            flash("Vul alle velden in voor een handmatige kade.", "error")
        return redirect(url_for("vn_preview", plant_index=plant_index))
    # Apply manual herkomst / aanvoer-per overrides and re-render.
    if not session_has("vn_data") or vn_parser is None or vn_to_gpp is None:
        flash("Sessiegegevens verlopen. Upload het VN-bestand opnieuw.", "error")
        return redirect(url_for("vn_upload_page"))

    vn_data_dict = session_get("vn_data") or {}
    plants_dicts = vn_data_dict.get("plants", [])
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
    vn_data_dict["plants"] = plants_dicts
    session_set("vn_data", vn_data_dict)

    # Manual distance overrides (per VN row, e.g. RAP → plant)
    md_all = session_get("vn_manual_distances") or {}
    md_plant = dict(md_all.get(str(plant_index), {}))
    for key, val in request.form.items():
        if not key.startswith("distance_"):
            continue
        row = key[len("distance_"):]
        v = (val or "").strip().replace(",", ".")
        if not v:
            md_plant.pop(row, None)
            continue
        try:
            md_plant[row] = float(v)
        except ValueError:
            flash(f"Ongeldige afstand voor rij {row}: {val!r}", "error")
    md_all[str(plant_index)] = md_plant
    session_set("vn_manual_distances", md_all)

    # Stockpile coverage overrides (Input!G{row}) per VN row.
    cov_all = session_get("vn_coverage") or {}
    cov_plant = dict(cov_all.get(str(plant_index), {}))
    for key, val in request.form.items():
        if not key.startswith("coverage_"):
            continue
        row = key[len("coverage_"):]
        sv = (val or "").strip().lower()
        if sv in ("yes", "ja"):
            cov_plant[row] = "Yes"
        else:
            cov_plant[row] = "No"
    cov_all[str(plant_index)] = cov_plant
    session_set("vn_coverage", cov_all)

    return redirect(url_for("vn_preview", plant_index=plant_index))


@app.route("/gpp/vn/calculate", methods=["POST"])
def vn_calculate():
    if not session_has("vn_mapping"):
        flash("Geen mapping beschikbaar. Begin opnieuw.", "error")
        return redirect(url_for("vn_upload_page"))
    if gpp_engine is None:
        flash("GPP engine is niet beschikbaar.", "error")
        return redirect(url_for("vn_select_plant"))

    mapping_dict = session_get("vn_mapping") or {}
    # Block calculation if any required distance is still missing.
    missing = [
        f"VN-rij {c['vn_row']} ({c.get('name') or c.get('category')})"
        for c in mapping_dict.get("components", [])
        if c.get("manual_distance") and c.get("distance_km") in (None, "")
    ]
    if missing:
        flash(
            "GPP-berekening geblokkeerd \u2014 ontbrekende afstand(en) voor: "
            + "; ".join(missing)
            + ". Vul alle handmatige afstanden in voordat je berekent.",
            "error",
        )
        return redirect(url_for("vn_preview",
                                plant_index=session.get("vn_plant_index", 0)))
    # Compute multimodal transport legs (truck-feeder + barge + truck-feeder)
    # for Barge/Ship components so the GPP Input sheet receives Route 1/2/3
    # instead of a single under-counted main leg.
    _compute_component_legs(mapping_dict)
    cell_payload: dict[str, Any] = dict(mapping_dict.get("general_cells") or {})
    seen_gpp_rows: set[int] = set()
    for c in mapping_dict.get("components", []):
        r = c["gpp_row"]
        # Step 2b in vn_to_gpp.map_plant appends "ghost" MappedComponents
        # for the "Extra teruggew. stof" rows; they share their gpp_row
        # with the coarse component they were folded into and would
        # otherwise overwrite that real coarse row's cells with zeros.
        if r in seen_gpp_rows:
            continue
        seen_gpp_rows.add(r)
        cell_payload[f"B{r}"] = round(c["pct_fraction"], 6)
        # Type column for slots that need it
        cat = c["category"]
        gpp_type = vn_to_gpp.gpp_type_for(cat, c.get("name"))
        if gpp_type is not None:
            cell_payload[f"C{r}"] = gpp_type
        cell_payload[f"H{r}"] = c.get("origin") or ""
        # Stockpile coverage (Input!G{r}); not applicable to binder row.
        if cat != "binder":
            cell_payload[f"G{r}"] = c.get("stockpile_covered") or "No"
        # Route 1/2/3 (J/K/L, M/N/O, P/Q/R) — multi-leg if available,
        # single-leg legacy write otherwise.
        _write_transport_routes(cell_payload, c)

    # Minimum DDN payload — VN is the source of truth; we only need a
    # transport-to-site stub so GPP's section 5 doesn't trip its check.
    payload = {
        "transport_mode": "Truck",
        "energy_source":  "Diesel_Euro6",
        "distance_km":    20,
        "bruto_kg":       20000,
    }

    # Where to save the populated GPP workbook so the user can download it.
    GPP_DOWNLOAD_DIR = BASE_DIR / "output" / "gpp_filled"
    GPP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"GPP_VN_{secrets.token_urlsafe(6)}.xlsx"
    save_path = GPP_DOWNLOAD_DIR / fname

    try:
        engine = gpp_engine.GPPEngine()
        results = engine.calculate(payload, extra_cells=cell_payload,
                                   save_to=save_path)
    except Exception as e:
        flash(f"GPP-berekening mislukt: {e}", "error")
        return redirect(url_for("vn_preview", plant_index=session.get("vn_plant_index", 0)))

    session["vn_gpp_filename"] = fname

    return render_template(
        "vn_results.html",
        results=results,
        mapping=mapping_dict,
        gpp_filename=fname,
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

    For Truck/Train/etc. components we return a single polyline as before.

    For Barge / Ship components we run the full inland-barge logistics
    pipeline:

      1. Geocode origin (quarry / source) and the asphalt plant.
      2. Search OpenStreetMap (Overpass) for nearby loading and
         unloading quays, using tags like ``waterway=dock``,
         ``man_made=pier|quay``, ``industrial=port``,
         ``landuse=port``, ``harbour=*``.
      3. Pick the nearest feasible quay within ``QUAY_SEARCH_RADIUS_KM``
         (default 20 km) of source / plant.  Manual overrides in
         ``data/waterway_terminals.json`` win.
      4. Compute road distance source → loading quay (OSRM).
      5. Compute waterway distance loading → unloading quay (BRouter
         or searoute, on a navigable network of canals/rivers/CEMT).
      6. Compute road distance unloading quay → plant (OSRM).
      7. Report all three plus tonne-km figures (using the recipe
         fraction as the per-functional-unit mass proxy).
      8. If any quay or waterway route is missing, attach a warning
         and leave the original VN distance as the source-of-truth.

    The resulting per-component records are returned in the
    ``logistics`` array; the ``routes`` and ``quays`` arrays are
    derived from them and drive the Leaflet rendering.
    """
    plant_lat = mapping_dict.get("plant_lat")
    plant_lon = mapping_dict.get("plant_lon")
    plant_pt = (
        geo.GeoPoint(lat=float(plant_lat), lon=float(plant_lon),
                     label=mapping_dict.get("plant_location") or "Plant")
        if plant_lat is not None and plant_lon is not None else None
    )
    plant_label = mapping_dict.get("plant_location")

    origins: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    quays: list[dict[str, Any]] = []
    logistics: list[dict[str, Any]] = []
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

        if c["mode_gpp"] == "Truck":
            coords = geo.osrm_route_geometry(origin_pt, plant_pt)
            routes.append({
                "name":   c["name"],
                "mode":   "Truck",
                "coords": coords or [(origin_pt.lat, origin_pt.lon),
                                     (plant_pt.lat, plant_pt.lon)],
                "source": "osrm" if coords else "straight",
                "routed_length_km": None,
                "leg":    "main",
            })
        elif c["mode_gpp"] in ("Barge", "Ship"):
            legs, leg_quays, log_rec = _waterway_logistics(
                c, origin_pt, plant_pt, plant_label,
            )
            routes.extend(legs)
            quays.extend(leg_quays)
            logistics.append(log_rec)
        else:
            # Train / No / unknown — straight line fallback.
            routes.append({
                "name":   c["name"],
                "mode":   c["mode_gpp"],
                "coords": [(origin_pt.lat, origin_pt.lon),
                           (plant_pt.lat, plant_pt.lon)],
                "source": "straight",
                "routed_length_km": None,
                "leg":    "main",
            })

    return {
        "plant": {
            "lat":   plant_pt.lat if plant_pt else None,
            "lon":   plant_pt.lon if plant_pt else None,
            "label": plant_label or "Plant",
        },
        "origins":   origins,
        "routes":    routes,
        "quays":     quays,
        "logistics": logistics,
        "quay_radius_km": geo.QUAY_SEARCH_RADIUS_KM,
    }


# ── Inland-barge auto-split: source → quay → quay → plant ─────────────
_QUAY_SPLIT_THRESHOLD_KM = 2.0  # below this we don't insert a truck hop


def _waterway_logistics(
    c: dict[str, Any],
    origin_pt: "geo.GeoPoint",
    plant_pt: "geo.GeoPoint",
    plant_label: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Run the 10-step inland-barge logistics pipeline for one component.

    Returns ``(routes, quays, logistics_record)``.  ``logistics_record``
    contains the per-leg breakdown and any warnings; it never raises —
    failures are surfaced as warnings on the record itself.
    """
    mode = c["mode_gpp"]
    name = c["name"]
    pct = float(c.get("pct_fraction") or 0.0)  # 0..1

    log_rec: dict[str, Any] = {
        "name": name, "mode": mode,
        "vn_distance_km": c.get("distance_km"),
        "pre_truck_km": None,
        "barge_km": None,
        "post_truck_km": None,
        "total_km": None,
        "pre_truck_tkm_per_t": None,   # tkm per tonne of asphalt produced
        "barge_tkm_per_t": None,
        "post_truck_tkm_per_t": None,
        "verified": False,
        "warnings": [],
        "source": "estimated_logistics",
    }

    # 1) Resolve loading + unloading quays — manual override wins,
    # otherwise Overpass nearest within QUAY_SEARCH_RADIUS_KM.
    # The plant's unloading quay gets a larger search radius (50 km)
    # because asphalt plants are often well inland and rely on a
    # regional transhipment terminal — the loading quay near the
    # quarry/source is usually much closer.
    plant_quay_radius_km = float(os.environ.get(
        "PLANT_QUAY_SEARCH_RADIUS_KM", "50",
    ))
    origin_quay = (geo.waterway_terminal_for(c.get("origin"))
                   or geo.find_nearest_quay(origin_pt))
    plant_quay = (geo.waterway_terminal_for(plant_label)
                  or geo.find_nearest_quay(plant_pt,
                                           radius_km=plant_quay_radius_km))

    if origin_quay is None:
        log_rec["warnings"].append(
            f"Geen laadkade gevonden binnen {geo.QUAY_SEARCH_RADIUS_KM:.0f} km "
            f"van {c.get('origin') or 'herkomst'}; voer afstand handmatig in."
        )
    if plant_quay is None:
        log_rec["warnings"].append(
            f"Geen loskade gevonden binnen {plant_quay_radius_km:.0f} km "
            f"van {plant_label or 'asfaltcentrale'}; voer afstand handmatig in."
        )

    routes: list[dict[str, Any]] = []
    quays: list[dict[str, Any]] = []

    # 2) Truck pre-leg: source → loading quay.
    pre_km: float | None = None
    if origin_quay is not None:
        crow_pre = geo.haversine_km(origin_pt, origin_quay)
        if crow_pre > _QUAY_SPLIT_THRESHOLD_KM:
            pre_geom = geo.osrm_route_geometry(origin_pt, origin_quay)
            pre_osrm = geo.osrm_route_km(origin_pt, origin_quay)
            pre_km = pre_osrm[0] if pre_osrm else crow_pre
            routes.append({
                "name":   f"{name} (truck → quay)",
                "mode":   "Truck",
                "coords": pre_geom or [(origin_pt.lat, origin_pt.lon),
                                       (origin_quay.lat, origin_quay.lon)],
                "source": "osrm" if pre_geom else "straight",
                "routed_length_km": round(pre_km, 1),
                "leg":    "pre_truck",
            })
        else:
            pre_km = 0.0
        quays.append({
            "lat": origin_quay.lat, "lon": origin_quay.lon,
            "label": origin_quay.label or "Loading quay",
            "kind": "load", "for": name,
        })

    # 3) Main barge / ship leg.
    barge_km: float | None = None
    if origin_quay is not None and plant_quay is not None:
        wcoords, wlen, wsrc = geo.waterway_route_geometry(
            origin_quay, plant_quay, mode=mode,
        )
        if wcoords:
            barge_km = wlen
            routes.append({
                "name":   f"{name} ({mode.lower()})",
                "mode":   mode,
                "coords": wcoords,
                "source": wsrc,
                "routed_length_km": round(wlen, 1) if wlen else None,
                "leg":    "main",
            })
        else:
            log_rec["warnings"].append(
                "Geen vaarroute gevonden tussen kades; voer afstand handmatig in."
            )
            routes.append({
                "name":   f"{name} ({mode.lower()})",
                "mode":   mode,
                "coords": [(origin_quay.lat, origin_quay.lon),
                           (plant_quay.lat, plant_quay.lon)],
                "source": "straight",
                "routed_length_km": None,
                "leg":    "main",
            })
    else:
        # No quay match in manual overrides — still try the waterway router
        # directly between origin and plant.  The Overpass/BRouter routers
        # internally snap to the nearest canal node, which effectively
        # locates a usable quay.  Only if that also fails do we fall back
        # to a straight line.
        wcoords, wlen, wsrc = geo.waterway_route_geometry(
            origin_pt, plant_pt, mode=mode,
        )
        if wcoords:
            barge_km = wlen
            routes.append({
                "name":   f"{name} ({mode.lower()})",
                "mode":   mode,
                "coords": wcoords,
                "source": wsrc,
                "routed_length_km": round(wlen, 1) if wlen else None,
                "leg":    "main",
            })
            # Infer load/unload quays from the snapped route endpoints so
            # we can still split out the truck pre/post-legs.
            load_lat, load_lon = wcoords[0]
            unload_lat, unload_lon = wcoords[-1]
            origin_quay = geo.GeoPoint(
                lat=float(load_lat), lon=float(load_lon),
                label=f"Loading quay (auto, {wsrc})",
            )
            plant_quay = geo.GeoPoint(
                lat=float(unload_lat), lon=float(unload_lon),
                label=f"Unloading quay (auto, {wsrc})",
            )
            # Drop the earlier "no quay" warnings — we recovered.
            log_rec["warnings"] = [
                w for w in log_rec["warnings"]
                if "laadkade" not in w and "loskade" not in w
            ]
            # Truck pre-leg now that we have an inferred loading quay.
            crow_pre = geo.haversine_km(origin_pt, origin_quay)
            if crow_pre > _QUAY_SPLIT_THRESHOLD_KM:
                pre_geom = geo.osrm_route_geometry(origin_pt, origin_quay)
                pre_osrm = geo.osrm_route_km(origin_pt, origin_quay)
                pre_km = pre_osrm[0] if pre_osrm else crow_pre
                routes.append({
                    "name":   f"{name} (truck → quay)",
                    "mode":   "Truck",
                    "coords": pre_geom or [(origin_pt.lat, origin_pt.lon),
                                           (origin_quay.lat, origin_quay.lon)],
                    "source": "osrm" if pre_geom else "straight",
                    "routed_length_km": round(pre_km, 1),
                    "leg":    "pre_truck",
                })
            else:
                pre_km = 0.0
            quays.append({
                "lat": origin_quay.lat, "lon": origin_quay.lon,
                "label": origin_quay.label or "Loading quay",
                "kind": "load", "for": name,
            })
        else:
            log_rec["warnings"].append(
                "Geen kade en geen vaarroute gevonden; afstand is hemelsbreed."
            )
            routes.append({
                "name":   f"{name} ({mode.lower()})",
                "mode":   mode,
                "coords": [(origin_pt.lat, origin_pt.lon),
                           (plant_pt.lat, plant_pt.lon)],
                "source": "straight",
                "routed_length_km": None,
                "leg":    "main",
            })

    # 4) Truck post-leg: unloading quay → plant.
    post_km: float | None = None
    if plant_quay is not None:
        crow_post = geo.haversine_km(plant_quay, plant_pt)
        if crow_post > _QUAY_SPLIT_THRESHOLD_KM:
            post_geom = geo.osrm_route_geometry(plant_quay, plant_pt)
            post_osrm = geo.osrm_route_km(plant_quay, plant_pt)
            post_km = post_osrm[0] if post_osrm else crow_post
            routes.append({
                "name":   f"{name} (quay → plant)",
                "mode":   "Truck",
                "coords": post_geom or [(plant_quay.lat, plant_quay.lon),
                                        (plant_pt.lat, plant_pt.lon)],
                "source": "osrm" if post_geom else "straight",
                "routed_length_km": round(post_km, 1),
                "leg":    "post_truck",
            })
        else:
            post_km = 0.0
        quays.append({
            "lat": plant_quay.lat, "lon": plant_quay.lon,
            "label": plant_quay.label or "Unloading quay",
            "kind": "unload", "for": name,
        })

    # 5) Fill the report record.
    log_rec["pre_truck_km"] = round(pre_km, 1) if pre_km is not None else None
    log_rec["barge_km"] = round(barge_km, 1) if barge_km is not None else None
    log_rec["post_truck_km"] = round(post_km, 1) if post_km is not None else None
    parts = [v for v in (pre_km, barge_km, post_km) if v is not None]
    log_rec["total_km"] = round(sum(parts), 1) if parts else None
    # tonne-km expressed per 1 tonne of asphalt produced; multiply by
    # actual tonnage at EPD time.  ``pct_fraction`` is 0..1.
    if pct > 0:
        if pre_km is not None:
            log_rec["pre_truck_tkm_per_t"] = round(pre_km * pct, 2)
        if barge_km is not None:
            log_rec["barge_tkm_per_t"] = round(barge_km * pct, 2)
        if post_km is not None:
            log_rec["post_truck_tkm_per_t"] = round(post_km * pct, 2)

    return routes, quays, log_rec


# ── Multi-leg transport for the GPP "Input" sheet ─────────────────────
#
# The GPP template supports up to three transport routes per component
# row: Route 1 in J/K/L (mode/energy/km), Route 2 in M/N/O, Route 3 in
# P/Q/R.  Inland-barge / sea-going components are intrinsically
# multi-modal — a barge leg almost always has a truck feeder before
# the loading quay and after the unloading quay.  The single-distance
# value coming out of vn_to_gpp models only the main waterway leg, so
# without splitting we under-report the road-truck contribution to the
# A2 transport impact.
#
# The helpers below reuse the same _waterway_logistics pipeline that
# drives the preview map (so quay coordinates, manual overrides and
# routed canal distances all match between map and GPP write).

# Default GPP energy_source values for the auto-generated feeder legs.
_FEEDER_TRUCK_ENERGY = "Diesel_Euro6"


def _compute_component_legs(mapping_dict: dict[str, Any]) -> None:
    """Mutate every Barge/Ship component in ``mapping_dict`` to attach a
    ``transport_legs`` list of ``{mode, energy, distance_km}`` dicts.

    Components with a manual distance override or missing geocoding are
    left untouched (single-leg behaviour).  Costs nothing extra after
    the user has already opened the preview map for the same plant —
    the underlying waterway/quay queries are disk-cached.
    """
    plant_lat = mapping_dict.get("plant_lat")
    plant_lon = mapping_dict.get("plant_lon")
    if plant_lat is None or plant_lon is None or geo is None:
        return
    plant_pt = geo.GeoPoint(
        lat=float(plant_lat), lon=float(plant_lon),
        label=mapping_dict.get("plant_location") or "Plant",
    )
    plant_label = mapping_dict.get("plant_location")

    for c in mapping_dict.get("components", []):
        if c.get("mode_gpp") not in ("Barge", "Ship"):
            continue
        if c.get("manual_distance") and c.get("distance_km") is not None:
            # Operator entered a single distance manually — respect it,
            # don't second-guess by splitting.
            continue
        if not c.get("origin"):
            continue
        try:
            origin_pt = geo.geocode(c["origin"])
        except Exception:
            origin_pt = None
        if origin_pt is None:
            continue
        try:
            _, _, log_rec = _waterway_logistics(
                c, origin_pt, plant_pt, plant_label,
            )
        except Exception as e:
            log.warning("transport-leg computation failed for %s: %s",
                        c.get("name"), e)
            continue

        legs: list[dict[str, Any]] = []
        pre_km = log_rec.get("pre_truck_km")
        if pre_km is not None and pre_km > 0:
            legs.append({"mode": "Truck", "energy": _FEEDER_TRUCK_ENERGY,
                         "distance_km": pre_km})
        barge_km = log_rec.get("barge_km")
        if barge_km is not None and barge_km > 0:
            legs.append({"mode": c["mode_gpp"],
                         "energy": c.get("energy_gpp") or "Diesel",
                         "distance_km": barge_km})
        post_km = log_rec.get("post_truck_km")
        if post_km is not None and post_km > 0:
            legs.append({"mode": "Truck", "energy": _FEEDER_TRUCK_ENERGY,
                         "distance_km": post_km})
        if legs:
            c["transport_legs"] = legs


def _write_transport_routes(cell_payload: dict[str, Any], c: dict[str, Any]) -> None:
    """Fill the GPP transport columns (J–R) for one component row.

    If ``c["transport_legs"]`` is populated (e.g. truck → barge → truck
    for a multimodal inland-barge component), Route 1/2/3 are filled
    accordingly.  Otherwise we keep the legacy single-leg behaviour.
    """
    r = c["gpp_row"]
    legs = c.get("transport_legs") or []

    if not legs:
        # Legacy single-route write.
        cell_payload[f"J{r}"] = c["mode_gpp"]
        cell_payload[f"K{r}"] = c["energy_gpp"]
        cell_payload[f"L{r}"] = (
            max(round(c["distance_km"], 2), 0.01)
            if c.get("distance_km") is not None else 0
        )
        for col in ("M", "N", "P", "Q"):
            cell_payload[f"{col}{r}"] = "No"
        cell_payload[f"O{r}"] = 0
        cell_payload[f"R{r}"] = 0
        return

    slots = (("J", "K", "L"), ("M", "N", "O"), ("P", "Q", "R"))
    for i, (mode_col, energy_col, dist_col) in enumerate(slots):
        if i < len(legs):
            leg = legs[i]
            cell_payload[f"{mode_col}{r}"] = leg["mode"]
            cell_payload[f"{energy_col}{r}"] = leg["energy"]
            cell_payload[f"{dist_col}{r}"] = max(
                round(float(leg["distance_km"]), 2), 0.01,
            )
        else:
            cell_payload[f"{mode_col}{r}"] = "No"
            cell_payload[f"{energy_col}{r}"] = "No"
            cell_payload[f"{dist_col}{r}"] = 0


@app.route("/gpp/vn/map.json")
def vn_map_data():
    if not session_has("vn_mapping"):
        return jsonify({"error": "no mapping in session"}), 404
    payload = _build_map_payload(session_get("vn_mapping"))
    log.info("vn_map_data: plant=%s origins=%d routes=%d",
             payload.get("plant"), len(payload.get("origins") or []),
             len(payload.get("routes") or []))
    return jsonify(payload)


@app.route("/gpp/software-vn/map.json")
def software_vn_map_data():
    if not session_has("svn_mapping"):
        return jsonify({"error": "no mapping in session"}), 404
    payload = _build_map_payload(session_get("svn_mapping"))
    log.info("software_vn_map_data: plant=%s origins=%d routes=%d",
             payload.get("plant"), len(payload.get("origins") or []),
             len(payload.get("routes") or []))
    return jsonify(payload)


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
    for k in ("svn_token", "svn_filename", "svn_plant_index"):
        session.pop(k, None)
    session_pop("svn_data")
    session_pop("svn_mapping")

    session["svn_token"] = token
    session["svn_filename"] = uploaded.filename
    session_set("svn_data", svn_data.to_dict())

    return redirect(url_for("software_vn_select_plant"))


@app.route("/gpp/software-vn/select", methods=["GET"])
def software_vn_select_plant():
    if not session_has("svn_data"):
        flash("Geen Software VN-bestand geladen. Upload eerst een bestand.", "error")
        return redirect(url_for("software_vn_upload_page"))
    return render_template(
        "software_vn_select.html",
        svn_data=session_get("svn_data"),
        svn_filename=session.get("svn_filename", ""),
    )


@app.route("/gpp/software-vn/preview/<int:plant_index>", methods=["GET"])
def software_vn_preview(plant_index: int):
    if not session_has("svn_data") or software_vn_parser is None or vn_to_gpp is None:
        flash("Sessiegegevens verlopen. Upload het Software VN-bestand opnieuw.", "error")
        return redirect(url_for("software_vn_upload_page"))
    if plant_index not in (0, 1, 2):
        flash("Ongeldige asfaltcentrale.", "error")
        return redirect(url_for("software_vn_select_plant"))

    svn_data_dict = session_get("svn_data") or {}
    plants_dicts = svn_data_dict.get("plants", [])
    if plant_index >= len(plants_dicts):
        flash("Asfaltcentrale niet gevonden in Software VN-bestand.", "error")
        return redirect(url_for("software_vn_select_plant"))

    plant_dict = plants_dicts[plant_index]
    # Rebuild a SVNPlant (duck-compatible with VNPlant for vn_to_gpp.map_plant)
    comps = [software_vn_parser.SVNComponent(**c) for c in plant_dict.get("components", [])]
    plant_kwargs = {k: v for k, v in plant_dict.items() if k != "components"}
    plant = software_vn_parser.SVNPlant(components=comps, **plant_kwargs)
    mapping = vn_to_gpp.map_plant(plant)
    manual_dists = (session_get("svn_manual_distances") or {}).get(str(plant_index), {})
    mapping.apply_manual_distances(manual_dists)
    coverage = (session_get("svn_coverage") or {}).get(str(plant_index), {})
    mapping.apply_coverage_overrides(coverage)

    session_set("svn_mapping", mapping.to_dict())
    session["svn_plant_index"] = plant_index

    return render_template(
        "software_vn_preview.html",
        mapping=mapping.to_dict(),
        plant_dict=plant_dict,
    )


@app.route("/gpp/software-vn/preview/<int:plant_index>/edit", methods=["POST"])
def software_vn_preview_edit(plant_index: int):
    """Apply manual herkomst / aanvoer-per overrides and re-render."""
    if not session_has("svn_data") or software_vn_parser is None or vn_to_gpp is None:
        flash("Sessiegegevens verlopen. Upload het Software VN-bestand opnieuw.", "error")
        return redirect(url_for("software_vn_upload_page"))

    svn_data_dict = session_get("svn_data") or {}
    plants_dicts = svn_data_dict.get("plants", [])
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
    svn_data_dict["plants"] = plants_dicts
    session_set("svn_data", svn_data_dict)

    md_all = session_get("svn_manual_distances") or {}
    md_plant = dict(md_all.get(str(plant_index), {}))
    for key, val in request.form.items():
        if not key.startswith("distance_"):
            continue
        row = key[len("distance_"):]
        v = (val or "").strip().replace(",", ".")
        if not v:
            md_plant.pop(row, None)
            continue
        try:
            md_plant[row] = float(v)
        except ValueError:
            flash(f"Ongeldige afstand voor rij {row}: {val!r}", "error")
    md_all[str(plant_index)] = md_plant
    session_set("svn_manual_distances", md_all)

    # Stockpile coverage overrides (Input!G{row}) per VN row.
    cov_all = session_get("svn_coverage") or {}
    cov_plant = dict(cov_all.get(str(plant_index), {}))
    for key, val in request.form.items():
        if not key.startswith("coverage_"):
            continue
        row = key[len("coverage_"):]
        sv = (val or "").strip().lower()
        if sv in ("yes", "ja"):
            cov_plant[row] = "Yes"
        else:
            cov_plant[row] = "No"
    cov_all[str(plant_index)] = cov_plant
    session_set("svn_coverage", cov_all)

    return redirect(url_for("software_vn_preview", plant_index=plant_index))


@app.route("/gpp/software-vn/calculate", methods=["POST"])
def software_vn_calculate():
    if not session_has("svn_mapping"):
        flash("Geen mapping beschikbaar. Begin opnieuw.", "error")
        return redirect(url_for("software_vn_upload_page"))
    if gpp_engine is None:
        flash("GPP engine is niet beschikbaar.", "error")
        return redirect(url_for("software_vn_select_plant"))

    mapping_dict = session_get("svn_mapping") or {}
    missing = [
        f"VN-rij {c['vn_row']} ({c.get('name') or c.get('category')})"
        for c in mapping_dict.get("components", [])
        if c.get("manual_distance") and c.get("distance_km") in (None, "")
    ]
    if missing:
        flash(
            "GPP-berekening geblokkeerd \u2014 ontbrekende afstand(en) voor: "
            + "; ".join(missing)
            + ". Vul alle handmatige afstanden in voordat je berekent.",
            "error",
        )
        return redirect(url_for("software_vn_preview",
                                plant_index=session.get("svn_plant_index", 0)))
    _compute_component_legs(mapping_dict)
    cell_payload: dict[str, Any] = dict(mapping_dict.get("general_cells") or {})
    seen_gpp_rows: set[int] = set()
    for c in mapping_dict.get("components", []):
        r = c["gpp_row"]
        # Step 2b in vn_to_gpp.map_plant appends "ghost" MappedComponents
        # for the "Extra teruggew. stof" rows that share their gpp_row
        # with the coarse component they were folded into. Those ghosts
        # carry pct_fraction=0 / origin="" / distance_km=None purely for
        # UI display — if we let them through here they would clobber
        # the real coarse row's cells (B/H/J/K/L) with zeros/blanks.
        if r in seen_gpp_rows:
            continue
        seen_gpp_rows.add(r)
        cell_payload[f"B{r}"] = round(c["pct_fraction"], 6)
        cat = c["category"]
        gpp_type = vn_to_gpp.gpp_type_for(cat, c.get("name"))
        if gpp_type is not None:
            cell_payload[f"C{r}"] = gpp_type
        cell_payload[f"H{r}"] = c.get("origin") or ""
        # Stockpile coverage (Input!G{r}); not applicable to binder row.
        if cat != "binder":
            cell_payload[f"G{r}"] = c.get("stockpile_covered") or "No"
        # Route 1/2/3 (J/K/L, M/N/O, P/Q/R) — multi-leg if available,
        # single-leg legacy write otherwise.
        _write_transport_routes(cell_payload, c)

    payload = {
        "transport_mode": "Truck",
        "energy_source":  "Diesel_Euro6",
        "distance_km":    20,
        "bruto_kg":       20000,
    }

    GPP_DOWNLOAD_DIR = BASE_DIR / "output" / "gpp_filled"
    GPP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"GPP_SVN_{secrets.token_urlsafe(6)}.xlsx"
    save_path = GPP_DOWNLOAD_DIR / fname

    try:
        engine = gpp_engine.GPPEngine()
        results = engine.calculate(payload, extra_cells=cell_payload,
                                   save_to=save_path)
    except Exception as e:
        flash(f"GPP-berekening mislukt: {e}", "error")
        return redirect(url_for("software_vn_preview", plant_index=session.get("svn_plant_index", 0)))

    session["svn_gpp_filename"] = fname

    return render_template(
        "software_vn_results.html",
        results=results,
        mapping=mapping_dict,
        gpp_filename=fname,
    )


@app.route("/gpp/download/<path:filename>")
def gpp_download(filename: str):
    """Serve a populated GPP workbook saved by the calculate routes."""
    # Restrict to filenames that were actually produced by this session.
    allowed = {session.get("vn_gpp_filename"), session.get("svn_gpp_filename")}
    if filename not in allowed:
        flash("Bestand niet beschikbaar voor download.", "error")
        return redirect(url_for("home"))
    file_path = BASE_DIR / "output" / "gpp_filled" / filename
    if not file_path.exists():
        flash("Bestand niet gevonden.", "error")
        return redirect(url_for("home"))
    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
    _sweep_session_store()
    app.run(
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
        host=os.getenv("DDN_HOST", "127.0.0.1"),
        port=int(os.getenv("DDN_PORT", "5001")),
    )

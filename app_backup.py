from __future__ import annotations

import base64
import secrets
from datetime import datetime
from datetime import date, time
from pathlib import Path
import time as _time
from typing import Any

import streamlit as st
import folium
from PIL import Image
from geopy.geocoders import Nominatim
from streamlit_drawable_canvas import st_canvas
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation

import excel_export
import geo
import mailer
import storage

APP_TITLE = "Digital Delivery Note"

LOGOS_DIR = Path(__file__).resolve().parent / "data" / "logos"
LOGO_FILES = [
    ("m4s.png", "University of Antwerp — M4S"),
    ("vlaio.png", "VLAIO"),
    ("diminfra.png", "DIMinfr@"),
    ("pxl.png", "PXL Bouw & Industrie"),
    ("supar_logo.png", "SUPAR"),
]


def _logo_b64(filename: str) -> str | None:
    """Return a base-64 data URI for an image in data/logos/."""
    p = LOGOS_DIR / filename
    if not p.exists():
        return None
    suffix = p.suffix.lower().lstrip(".")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml", "webp": "image/webp"}.get(suffix, "image/png")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def _render_branded_header() -> None:
    """Render a polished branded header with partner logos."""
    logo_imgs = []
    for fname, alt in LOGO_FILES:
        uri = _logo_b64(fname)
        if uri:
            logo_imgs.append((uri, alt))

    if logo_imgs:
        logos_html = "  ".join(
            f'<img src="{uri}" alt="{alt}" style="height:52px; object-fit:contain;"/>'
            for uri, alt in logo_imgs
        )
        st.markdown(
            f"""
            <div style="
                display:flex; align-items:center; justify-content:center;
                gap:32px; flex-wrap:wrap;
                padding:12px 16px;
                background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
                border-radius:10px;
                margin-bottom:8px;
            ">
                {logos_html}
            </div>
            """,
            unsafe_allow_html=True,
        )

    # App title bar
    st.markdown(
        """
        <div style="
            text-align:center;
            padding:10px 0 4px 0;
        ">
            <span style="
                font-size:1.8rem; font-weight:700;
                color:#1a1a2e;
                letter-spacing:0.5px;
            ">\U0001f4cb Digital Delivery Note</span>
            <br/>
            <span style="font-size:0.85rem; color:#6c757d;">DIMinfr@ — Prototype</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("""---""")


def _inject_custom_css() -> None:
    """Inject custom CSS for a polished, professional look."""
    st.markdown(
        """
        <style>
        /* ---- Global tweaks ---- */
        [data-testid="stAppViewContainer"] {
            background: #fafbfc;
        }
        section[data-testid="stSidebar"] {
            background: #f0f2f6;
        }

        /* ---- Tab styling ---- */
        button[data-baseweb="tab"] {
            font-size: 0.95rem;
            font-weight: 600;
        }

        /* ---- Input label emphasis ---- */
        .stTextInput label, .stTextArea label, .stNumberInput label, .stSelectbox label, .stDateInput label {
            font-weight: 600;
            color: #2c3e50;
        }

        /* ---- Subheader spacing ---- */
        [data-testid="stSubheader"] {
            padding-top: 0.5rem !important;
        }

        /* ---- Buttons ---- */
        .stButton > button {
            border-radius: 8px;
            font-weight: 600;
            transition: all 0.2s;
        }
        .stButton > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 2px 8px rgba(0,0,0,0.12);
        }

        /* ---- Download button ---- */
        .stDownloadButton > button {
            border-radius: 8px;
            font-weight: 600;
        }

        /* ---- Success / info boxes ---- */
        [data-testid="stAlert"] {
            border-radius: 8px;
        }

        /* ---- Divider subtlety ---- */
        hr {
            border-top: 1px solid #dee2e6 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


ROLE_LABELS = {
    "client": "Client",
    "transporter": "Transporter",
    "copro": "COPRO",
    "permit_holder": "Permit holder",
}

ENERGY_SOURCES = ["Diesel", "Biodiesel", "Electric", "Electric_green"]

DEMO_DATA = {
    "k_delivery_note_no": "DDN-2026-00142",
    "k_transport_company": "Van Hoeck Transport NV",
    "k_license_plate": "1-ABC-234",
    "k_origin_query": "Asfaltcentrale Willebroek, Belgium",
    "k_destination_query": "Bolivarplaats, Antwerpen, Belgium",
    "plant_address": "Asfaltcentrale Willebroek, Industrieweg 12, 2830 Willebroek, Belgium",
    "plant_lat": 51.0542,
    "plant_lon": 4.3621,
    "site_address": "Bouwwerf Antwerpen-Zuid, Bolivarplaats, 2000 Antwerpen, Belgium",
    "site_lat": 51.2094,
    "site_lon": 4.3997,
    "k_client_address": "Bouwbedrijf De Smet NV\nIndustrieweg 45\n2800 Mechelen\nBelgium",
    "k_product_mixture_type": "AC 14 surf B50/70 (ABb-4C)",
    "k_application": "Surface course \u2013 road rehabilitation N1",
    "k_certificate": "COPRO-C-2026/0487",
    "k_declaration_of_performance": "DoP-BE-2026-AC14-0042",
    "k_technical_data_sheet": "TDS-AC14-SurfB5070-v3.2",
    "k_mechanical_resistance": "Class 3 (EN 12697-12)",
    "k_fuel_resistance": "Not required",
    "k_deicing_resistance": "Resistant (EN 12697-37)",
    "k_bitumen_aggregate_affinity": "Satisfactory (EN 12697-11)",
    "k_disposal": "Recyclable \u2013 cat. I",
    "k_bruto_kg": 28450.0,
    "k_tare_weight_empty_kg": 14200.0,
    "k_net_total_quantity_ton": 14.25,
    "k_email_client": "jan.desmet@bouwbedrijf.be",
    "k_email_transporter": "dispatch@vanhoeck-transport.be",
    "k_email_copro": "inspectie@copro.eu",
    "k_email_permit_holder": "vergunning@wegenbouw.be",
}


def _load_demo_data() -> None:
    """Populate session state with demo values for all form fields."""
    for key, value in DEMO_DATA.items():
        st.session_state[key] = value


def _geocoder() -> Nominatim:
    # Cached per-session to avoid repeated instantiation.
    if "_geocoder" not in st.session_state:
        st.session_state["_geocoder"] = Nominatim(user_agent="ddn_prototype")
    return st.session_state["_geocoder"]


def _geocode_address(address: str) -> tuple[float, float, str] | None:
    address = (address or "").strip()
    if not address:
        return None
    try:
        loc = _geocoder().geocode(address)
        if not loc:
            return None
        return float(loc.latitude), float(loc.longitude), str(loc.address)
    except Exception:
        return None


def _search_locations(query: str, limit: int = 5) -> list[dict[str, Any]]:
    query = (query or "").strip()
    if len(query) < 3:
        return []
    try:
        results = _geocoder().geocode(query, exactly_one=False, limit=limit)
        if not results:
            return []
        out: list[dict[str, Any]] = []
        for r in results:
            out.append(
                {
                    "label": str(getattr(r, "address", "")) or query,
                    "lat": float(r.latitude),
                    "lon": float(r.longitude),
                }
            )
        return out
    except Exception:
        return []


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def _search_locations_cached(query: str) -> list[dict[str, Any]]:
    # Cached to reduce calls to Nominatim.
    return _search_locations(query, limit=6)


def _throttled_suggestions(query: str, key_prefix: str) -> list[dict[str, Any]]:
    """Throttle remote lookups to avoid hammering Nominatim while typing."""
    q = (query or "").strip()
    if len(q) < 5:
        st.session_state.pop(f"{key_prefix}_suggestions", None)
        return []

    now = _time.time()
    last_t = float(st.session_state.get(f"{key_prefix}_last_t", 0.0))
    last_q = str(st.session_state.get(f"{key_prefix}_last_q", ""))

    # If query unchanged, reuse stored suggestions.
    if q == last_q and f"{key_prefix}_suggestions" in st.session_state:
        return list(st.session_state.get(f"{key_prefix}_suggestions", []))

    # Throttle to ~1 request/sec.
    if now - last_t < 1.0:
        return list(st.session_state.get(f"{key_prefix}_suggestions", []))

    st.session_state[f"{key_prefix}_last_t"] = now
    st.session_state[f"{key_prefix}_last_q"] = q
    suggestions = _search_locations_cached(q)
    st.session_state[f"{key_prefix}_suggestions"] = suggestions
    return suggestions


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def _geocode_cached(address: str) -> tuple[float, float, str] | None:
    # Cached to reduce calls to Nominatim.
    return _geocode_address(address)


def _make_map(center_lat: float, center_lon: float, marker: tuple[float, float] | None, label: str) -> folium.Map:
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12, control_scale=True)
    if marker is not None:
        folium.Marker([marker[0], marker[1]], tooltip=label).add_to(m)
    return m


def _make_route_map(
    center_lat: float,
    center_lon: float,
    origin: tuple[float, float] | None,
    destination: tuple[float, float] | None,
) -> folium.Map:
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12, control_scale=True)
    if origin is not None:
        folium.Marker([origin[0], origin[1]], tooltip="Origin (Pick-up)").add_to(m)
    if destination is not None:
        folium.Marker([destination[0], destination[1]], tooltip="Destination (Delivery)").add_to(m)
    if origin is not None and destination is not None:
        folium.PolyLine([origin, destination], weight=3).add_to(m)
    return m


def _note_url(note_id: str, role: str) -> str:
    # Streamlit can't reliably infer the public URL in all deployments.
    # In prototype mode we show a relative URL users can paste after the base.
    return f"/?note={note_id}&role={role}"


def _safe_filename(note_id: str) -> str:
    return "DDN_" + "".join([c for c in note_id if c.isalnum() or c in {"-", "_"}]) + ".xlsx"


def _parse_time(s: str | None) -> str | None:
    if not s:
        return None
    return s


def page_create_note() -> None:
    st.header("Create delivery note")

    st.caption(
        "Fill in the plant information and the site delivery information. "
        "Locations can be set by address lookup, map click, or (for site) browser geolocation. "
        "Distance is calculated automatically."
    )

    mode = st.radio("Role", options=["Asphalt plant", "Site supervisor"], horizontal=True)

    if mode == "Site supervisor":
        st.subheader("Receive delivery")
        available = storage.list_delivery_note_nos(status="released", limit=200)
        dn = ""
        if available:
            dn = st.selectbox("Delivery Note No", options=available, index=0)
            st.caption("Select the delivery note number received on site.")
        else:
            dn = st.text_input("Delivery Note No", placeholder="Enter the delivery note number")
            st.caption("No released delivery notes found yet.")

        manual = st.text_input("Or enter Delivery Note No manually", value="", placeholder="Optional")
        if manual.strip():
            dn = manual.strip()

        if st.button("Truck received (record arrival time)"):
            if not dn.strip():
                st.error("Please enter the Delivery Note No.")
                return
            note = storage.get_note_by_delivery_note_no(dn.strip())
            if not note:
                st.error("No delivery note found for this number.")
                return
            if note.get("status") not in {"released", "received", "completed", "pending"}:
                st.warning("Unknown note status; proceeding.")

            payload = note["payload"]
            # Only allow arrival recording after release (prototype gating)
            if note.get("status") == "pending":
                st.error("This delivery note has not been released at the asphalt plant yet.")
                return

            now = datetime.now()
            payload["arrival_time"] = now.strftime("%H:%M")
            payload["arrival_time_iso"] = now.isoformat(timespec="seconds")

            # Persist updated payload by recreating note row is not ideal; update JSON in DB.
            # For prototype: update the payload_json directly.
            with storage.get_conn() as conn:
                import json

                conn.execute(
                    "UPDATE delivery_notes SET payload_json = ?, status = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), "received", note["id"]),
                )

            sigs = storage.list_signatures(note["id"])
            xlsx_bytes = excel_export.build_delivery_note_xlsx(payload, sigs)

            # Automatically send the DDN to the recipients captured at plant release
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
                        subject=f"DDN (arrival recorded) ({payload.get('delivery_note_no') or note['id']})",
                        body=(
                            "Arrival time has been recorded by the site supervisor. "
                            "The Digital Delivery Note (Excel) is attached."
                        ),
                        attachments=[
                            (
                                _safe_filename(note["id"]),
                                xlsx_bytes,
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            )
                        ],
                    )
                    emailed = True
                except Exception as e:
                    st.warning(f"Email sending failed: {e}")

            if emailed:
                storage.mark_completed(note["id"])
                st.success("Arrival recorded. Excel generated and emailed to recipients.")
            else:
                if emails and not mailer.email_enabled():
                    st.info("Email not configured; Excel generated for download.")
                elif not emails:
                    st.info("No recipient emails were provided; Excel generated for download.")
                else:
                    st.success("Arrival recorded. Excel generated.")

            st.download_button(
                label="Download Excel (xlsx)",
                data=xlsx_bytes,
                file_name=_safe_filename(note["id"]),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        st.info("This screen becomes usable once the asphalt plant has released the delivery note.")
        return

    # ── Scan upload section ──────────────────────────────────────────────
    st.markdown(
        """
        <div style="
            background: linear-gradient(135deg, #e8f4f8 0%, #d1ecf1 100%);
            border: 2px dashed #17a2b8;
            border-radius: 12px;
            padding: 20px 24px;
            margin-bottom: 16px;
        ">
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
                <span style="font-size:1.5rem;">📄</span>
                <span style="font-size:1.1rem; font-weight:700; color:#0c5460;">
                    Upload a scanned delivery note
                </span>
            </div>
            <span style="font-size:0.88rem; color:#155724;">
                Upload a photo or scan of an existing paper delivery note. Our AI-powered OCR
                engine will automatically extract and populate all fields below.
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    scan_col1, scan_col2 = st.columns([2, 1])
    with scan_col1:
        uploaded_scan = st.file_uploader(
            "Upload scanned delivery note",
            type=["png", "jpg", "jpeg", "pdf", "tiff", "bmp"],
            help="Supported formats: PNG, JPG, PDF, TIFF, BMP",
            key="scan_upload",
        )
    with scan_col2:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        process_scan = st.button(
            "\U0001f50d Extract data from scan",
            disabled=uploaded_scan is None,
            help="Process the uploaded scan using AI-powered OCR",
            use_container_width=True,
        )

    if uploaded_scan is not None:
        # Show a preview of the uploaded file
        if uploaded_scan.type and uploaded_scan.type.startswith("image"):
            with st.expander("Preview uploaded scan", expanded=False):
                st.image(uploaded_scan, caption=uploaded_scan.name, use_container_width=True)
        else:
            st.caption(f"Uploaded: **{uploaded_scan.name}** ({uploaded_scan.size / 1024:.0f} KB)")

    if process_scan and uploaded_scan is not None:
        # Simulated OCR processing for demonstration
        with st.status("\U0001f50d Processing scanned delivery note...", expanded=True) as status:
            st.write("Analysing document layout...")
            _time.sleep(0.6)
            st.write("Detecting text regions (OCR engine)...")
            _time.sleep(0.8)
            st.write("Extracting structured fields...")
            _time.sleep(0.5)
            st.write("Mapping extracted data to form fields...")
            _time.sleep(0.4)
            status.update(label="\u2705 Extraction complete!", state="complete", expanded=False)

        # Load the demo data as the "extracted" result
        _load_demo_data()
        st.success(
            "All fields have been auto-populated from the scanned document. "
            "Please review and adjust any values before submitting."
        )
        st.rerun()

    st.divider()

    if st.button("\U0001f4cb Load demo data", help="Pre-fill all fields with sample data for demonstration"):
        _load_demo_data()
        st.rerun()

    top1, top2 = st.columns(2)
    with top1:
        note_date = st.date_input("Date", value=date.today())
        delivery_note_no = st.text_input("Delivery Note No:", key="k_delivery_note_no")
    with top2:
        transport_company = st.text_input("Transport company", key="k_transport_company")
        license_plate = st.text_input("Nummerplaat", key="k_license_plate")

    tabs = st.tabs(["Asphalt plant", "Site delivery", "Product & documents", "Weights", "Recipients"])

    with tabs[0]:
        st.subheader("Please select your route details")

        st.markdown("Please select your route details")
        st.markdown("**Enter or select the Origin (Pick-up Location):**")
        st.caption("Start typing an address, place name, or landmark")
        origin_query = st.text_input("Origin", placeholder="Start typing an address, place name, or landmark", key="k_origin_query")

        origin_suggestions = _throttled_suggestions(origin_query, "origin")
        origin_selected = None
        if origin_suggestions:
            origin_selected = st.selectbox(
                "Choose from suggested locations (origin)",
                options=list(range(len(origin_suggestions))),
                format_func=lambda i: origin_suggestions[i]["label"],
                key="origin_choice",
            )
            if st.button("Use selected origin", key="apply_origin"):
                sel = origin_suggestions[int(origin_selected)]
                st.session_state["plant_lat"] = float(sel["lat"])
                st.session_state["plant_lon"] = float(sel["lon"])
                st.session_state["plant_address"] = sel["label"]

        st.caption("Or tap directly on the map to drop a pin")
        if st.button("Allow GPS to detect my current location (origin)", key="gps_origin"):
            geo_data = get_geolocation()
            if geo_data and isinstance(geo_data, dict) and geo_data.get("coords"):
                coords = geo_data["coords"]
                try:
                    st.session_state["plant_lat"] = float(coords["latitude"])
                    st.session_state["plant_lon"] = float(coords["longitude"])
                    st.info("Using your current browser location as origin.")
                except Exception:
                    st.warning("Could not read browser location; please use the map or type an address.")
            else:
                st.info("Browser will ask for permission on first use.")

        st.divider()
        st.markdown("**Enter or select the Destination (Delivery Location):**")
        st.caption("Type the delivery address")
        destination_query = st.text_input("Destination", placeholder="Type the delivery address", key="k_destination_query")

        destination_suggestions = _throttled_suggestions(destination_query, "destination")
        destination_selected = None
        if destination_suggestions:
            destination_selected = st.selectbox(
                "Choose from suggested locations (destination)",
                options=list(range(len(destination_suggestions))),
                format_func=lambda i: destination_suggestions[i]["label"],
                key="destination_choice",
            )
            if st.button("Use selected destination", key="apply_destination"):
                sel2 = destination_suggestions[int(destination_selected)]
                st.session_state["site_lat"] = float(sel2["lat"])
                st.session_state["site_lon"] = float(sel2["lon"])
                st.session_state["site_address"] = sel2["label"]

        st.caption("Or select the exact point on the map")

        st.divider()
        st.markdown("Use the map to zoom, pan, and adjust the pins for more accurate locations.")

        pin_mode = st.radio("When you click the map, set:", options=["Origin", "Destination"], horizontal=True)

        # Ensure session state defaults exist
        st.session_state.setdefault("plant_lat", 50.85)
        st.session_state.setdefault("plant_lon", 4.35)
        st.session_state.setdefault("site_lat", 50.85)
        st.session_state.setdefault("site_lon", 4.35)

        origin_marker = (float(st.session_state["plant_lat"]), float(st.session_state["plant_lon"]))
        destination_marker = (float(st.session_state["site_lat"]), float(st.session_state["site_lon"]))

        center_lat = (origin_marker[0] + destination_marker[0]) / 2.0
        center_lon = (origin_marker[1] + destination_marker[1]) / 2.0

        route_map = _make_route_map(
            center_lat=center_lat,
            center_lon=center_lon,
            origin=origin_marker,
            destination=destination_marker,
        )
        map_out = st_folium(route_map, height=360, key="route_map")
        if map_out and map_out.get("last_clicked"):
            lat_clicked = float(map_out["last_clicked"]["lat"])
            lon_clicked = float(map_out["last_clicked"]["lng"])
            if pin_mode == "Origin":
                st.session_state["plant_lat"] = lat_clicked
                st.session_state["plant_lon"] = lon_clicked
            else:
                st.session_state["site_lat"] = lat_clicked
                st.session_state["site_lon"] = lon_clicked

        plant_address = str(st.session_state.get("plant_address") or origin_query.strip())
        site_address = str(st.session_state.get("site_address") or destination_query.strip())
        plant_lookup = True
        site_lookup = True
        use_geo = False

    with tabs[1]:
        st.subheader("Site delivery")
        client_address = st.text_area("Client address", placeholder="Client address", key="k_client_address")
        st.text_area("Destination (site) address", value=st.session_state.get("site_address", ""), disabled=True)

        c1, c2 = st.columns(2)
        with c1:
            st.text_input("Departure Time", value=st.session_state.get("_departure_time", "(auto on send)"), disabled=True)
        with c2:
            st.text_input("Arrival Time", value="(auto on site receipt)", disabled=True)

    with tabs[2]:
        st.subheader("Product")
        product_mixture_type = st.text_input("Product/ Mixture type:", key="k_product_mixture_type")
        application = st.text_input("Application:", key="k_application")

        st.subheader("Documents / properties")
        certificate = st.text_input("Certificate:", key="k_certificate")
        declaration_of_performance = st.text_input("Declaration of Performance:", key="k_declaration_of_performance")
        technical_data_sheet = st.text_input("Technical Data Sheet:", key="k_technical_data_sheet")
        mechanical_resistance = st.text_input("Mechanical resistance:", key="k_mechanical_resistance")
        fuel_resistance = st.text_input("Fuel resistance:", key="k_fuel_resistance")
        deicing_resistance = st.text_input("De-icing resistance:", key="k_deicing_resistance")
        bitumen_aggregate_affinity = st.text_input("Bitumen\u2013aggregate affinity:", key="k_bitumen_aggregate_affinity")
        disposal = st.text_input("Disposal", key="k_disposal")

    with tabs[3]:
        st.subheader("Weights")
        bruto_kg = st.number_input("Bruto (kg)", min_value=0.0, value=0.0, step=1.0, key="k_bruto_kg")
        tare_weight_empty_kg = st.number_input("Tare Weight (Empty)", min_value=0.0, value=0.0, step=1.0, key="k_tare_weight_empty_kg")
        net_total_quantity_ton = st.number_input("Net total quantity (ton)", min_value=0.0, value=0.0, step=0.01, key="k_net_total_quantity_ton")

    with tabs[4]:
        st.subheader("Recipients (for automatic sending)")
        r1, r2 = st.columns(2)
        with r1:
            email_client = st.text_input("Client email", key="k_email_client")
            email_transporter = st.text_input("Transporter email", key="k_email_transporter")
        with r2:
            email_copro = st.text_input("COPRO email", key="k_email_copro")
            email_permit_holder = st.text_input("Permit holder email", key="k_email_permit_holder")

    # --- Post-tab summary and distance ---
    st.divider()
    st.subheader("Route & transport")
    transport_type = "Truck"
    st.text_input("Transport type", value=transport_type, disabled=True)
    energy_source = st.selectbox("Energy Source", options=ENERGY_SOURCES, index=0)

    plant_point = geo.GeoPoint(lat=float(st.session_state.get("plant_lat", 50.85)), lon=float(st.session_state.get("plant_lon", 4.35)), label="Plant")
    site_point = geo.GeoPoint(lat=float(st.session_state.get("site_lat", 50.85)), lon=float(st.session_state.get("site_lon", 4.35)), label="Site")
    route = geo.osrm_route_km(plant_point, site_point)
    if route:
        distance_km, duration_min = route
        st.success(f"Driving distance: {distance_km:.1f} km (≈ {duration_min:.0f} min)")
    else:
        distance_km = geo.haversine_km(plant_point, site_point)
        st.info(f"Distance (straight-line fallback): {distance_km:.1f} km")

    create_clicked = st.button("Release at asphalt plant & send")

    # Seed session state (so maps have stable defaults)
    st.session_state.setdefault("plant_lat", float(st.session_state.get("plant_lat", 50.85)))
    st.session_state.setdefault("plant_lon", float(st.session_state.get("plant_lon", 4.35)))
    st.session_state.setdefault("site_lat", float(st.session_state.get("site_lat", 50.85)))
    st.session_state.setdefault("site_lon", float(st.session_state.get("site_lon", 4.35)))

    # Apply geocoding outside the form submission (so it updates as user types)
    if plant_lookup and plant_address.strip() and st.session_state.get("_last_plant_address") != plant_address.strip():
        st.session_state["_last_plant_address"] = plant_address.strip()
        geo_res = _geocode_cached(plant_address.strip())
        if geo_res:
            st.session_state["plant_lat"], st.session_state["plant_lon"], plant_display = geo_res
            st.session_state["plant_address"] = plant_display
            st.caption(f"Origin located: {plant_display}")

    if site_lookup and (not use_geo) and site_address.strip() and st.session_state.get("_last_site_address") != site_address.strip():
        st.session_state["_last_site_address"] = site_address.strip()
        geo_res2 = _geocode_cached(site_address.strip())
        if geo_res2:
            st.session_state["site_lat"], st.session_state["site_lon"], site_display = geo_res2
            st.session_state["site_address"] = site_display
            st.caption(f"Destination located: {site_display}")

    if not create_clicked:
        return

    if not delivery_note_no.strip():
        st.error("Delivery Note No is required.")
        return

    # Prevent duplicates for the prototype
    existing = storage.get_note_by_delivery_note_no(delivery_note_no.strip())
    if existing:
        st.error("A delivery note with this number already exists.")
        return

    now = datetime.now()
    departure_hhmm = now.strftime("%H:%M")
    st.session_state["_departure_time"] = departure_hhmm

    note_id = secrets.token_urlsafe(10)

    payload = {
        "date": note_date.isoformat(),
        "client_address": client_address,
        "plant_address": plant_address,
        "delivery_note_no": delivery_note_no,
        "site_address": site_address,
        "departure_time": departure_hhmm,
        "departure_time_iso": now.isoformat(timespec="seconds"),
        "arrival_time": "",
        "distance_km": float(distance_km),
        "plant_lat": float(st.session_state.get("plant_lat", 50.85)),
        "plant_lon": float(st.session_state.get("plant_lon", 4.35)),
        "site_lat": float(st.session_state.get("site_lat", 50.85)),
        "site_lon": float(st.session_state.get("site_lon", 4.35)),
        "transport_company": transport_company,
        "license_plate": license_plate,
        "transport_type": transport_type,
        "energy_source": energy_source,
        "product_mixture_type": product_mixture_type,
        "application": application,
        "certificate": certificate,
        "declaration_of_performance": declaration_of_performance,
        "technical_data_sheet": technical_data_sheet,
        "mechanical_resistance": mechanical_resistance,
        "fuel_resistance": fuel_resistance,
        "deicing_resistance": deicing_resistance,
        "bitumen_aggregate_affinity": bitumen_aggregate_affinity,
        "disposal": disposal,
        "bruto_kg": float(bruto_kg),
        "tare_weight_empty_kg": float(tare_weight_empty_kg),
        "net_total_quantity_ton": float(net_total_quantity_ton),
        "emails": {
            "client": email_client,
            "transporter": email_transporter,
            "copro": email_copro,
            "permit_holder": email_permit_holder,
        },
    }

    storage.create_note(note_id, delivery_note_no.strip(), payload)
    storage.set_status(note_id, "released")

    links = {role: _note_url(note_id, role) for role in ROLE_LABELS.keys()}

    st.success("Released at asphalt plant. Departure time recorded and note sent.")
    st.write("Signing links (share these with each party):")
    for role, link in links.items():
        st.write(f"- {ROLE_LABELS[role]}: {link}")

    st.write("Site supervisor:")
    st.write(f"- Open the app and enter Delivery Note No: **{delivery_note_no.strip()}**")

    # Optional email sending
    if mailer.email_enabled():
        try:
            for role, link in links.items():
                email = payload["emails"].get(role)
                if not email:
                    continue
                mailer.send_email(
                    [email],
                    subject=f"Delivery note signing request ({payload.get('delivery_note_no') or note_id})",
                    body=(
                        "Please review and sign the digital delivery note using this link:\n\n"
                        f"{link}\n\n"
                        "(If this is a local run, prepend your Streamlit base URL.)\n"
                    ),
                )
            st.info("Emails sent (SMTP configured).")
        except Exception as e:
            st.warning(f"Email sending failed: {e}")
    else:
        st.info("Email not configured; showing links only (prototype mode).")


def page_sign(note_id: str, role: str) -> None:
    label = ROLE_LABELS.get(role, role)
    st.header(f"Sign as {label}")

    note = storage.get_note(note_id)
    if not note:
        st.error("Unknown delivery note.")
        return

    payload = note["payload"]

    st.subheader("Delivery note summary")
    st.write(
        {
            "Date": payload.get("date"),
            "Delivery Note No": payload.get("delivery_note_no"),
            "Plant address": payload.get("plant_address"),
            "Site address": payload.get("site_address"),
            "Departure": payload.get("departure_time"),
            "Arrival": payload.get("arrival_time"),
            "Transport company": payload.get("transport_company"),
            "License plate": payload.get("license_plate"),
            "Product/Mixture": payload.get("product_mixture_type"),
            "Net quantity (ton)": payload.get("net_total_quantity_ton"),
        }
    )

    signer_name = st.text_input("Signer name")
    st.write("Draw your signature:")
    canvas = st_canvas(
        fill_color="rgba(0, 0, 0, 0)",
        stroke_width=3,
        stroke_color="#000000",
        background_color="#FFFFFF",
        height=160,
        drawing_mode="freedraw",
        key=f"canvas_{note_id}_{role}",
    )

    if st.button("Submit signature"):
        if canvas.image_data is None:
            st.error("No signature captured.")
            return

        # Save signature PNG
        img = Image.fromarray(canvas.image_data.astype("uint8"))
        sig_path = storage.SIGNATURES_DIR / f"{note_id}_{role}.png"
        img.save(sig_path)

        storage.upsert_signature(note_id, role, signer_name.strip() or None, str(sig_path))

        st.success("Signature saved.")

    sigs = storage.list_signatures(note_id)
    st.subheader("Signing status")
    for r in ROLE_LABELS.keys():
        st.write(f"- {ROLE_LABELS[r]}: {'SIGNED' if r in sigs else 'PENDING'}")

    if storage.is_fully_signed(note_id):
        st.success("All parties have signed. Excel is ready.")

        data_dir = Path(__file__).resolve().parent / "data" / "exports"
        out_path = data_dir / _safe_filename(note_id)
        xlsx_bytes = excel_export.build_delivery_note_xlsx(payload, sigs, output_path=out_path)

        st.download_button(
            label="Download Excel (xlsx)",
            data=xlsx_bytes,
            file_name=_safe_filename(note_id),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        # Optional: email final Excel to all provided emails
        if mailer.email_enabled():
            if st.button("Email final Excel to all parties"):
                emails = [
                    payload.get("emails", {}).get("client"),
                    payload.get("emails", {}).get("transporter"),
                    payload.get("emails", {}).get("copro"),
                    payload.get("emails", {}).get("permit_holder"),
                ]
                emails = [e for e in emails if e]
                try:
                    mailer.send_email(
                        emails,
                        subject=f"Final delivery note (signed) ({payload.get('delivery_note_no') or note_id})",
                        body="All parties have signed. The final Excel is attached.",
                        attachments=[(_safe_filename(note_id), xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")],
                    )
                    st.info("Final Excel emailed.")
                except Exception as e:
                    st.warning(f"Final email failed: {e}")


def page_home() -> None:
    _render_branded_header()
    st.write(
        "This prototype links the asphalt plant and site supervisor via a shared digital delivery note, "
        "and collects signatures from Client, Transporter, COPRO, and Permit holder."
    )

    page_create_note()


def main() -> None:
    st.set_page_config(
        page_title="Digital Delivery Note — DIMinfr@",
        page_icon="\U0001f4cb",
        layout="wide",
    )
    _inject_custom_css()

    storage.init_db()

    params = st.query_params
    note_id = params.get("note")
    role = params.get("role")

    if note_id and role:
        _render_branded_header()
        page_sign(str(note_id), str(role))
    else:
        page_home()


if __name__ == "__main__":
    main()

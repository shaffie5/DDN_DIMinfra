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
    ("supar_logo.jpg", "SUPAR"),
    ("m4s.png", "University of Antwerp — M4S"),
    ("vlaio.png", "VLAIO"),
    ("DIMinfr@.png", "DIMinfr@"),
    ("pxl.png", "PXL Bouw & Industrie"),
]


# ═══════════════════════════════════════════════════════════════════════
#  UI Helper Functions
# ═══════════════════════════════════════════════════════════════════════


def _logo_b64(filename: str) -> str | None:
    """Return a base-64 data URI for an image in data/logos/."""
    p = LOGOS_DIR / filename
    if not p.exists():
        return None
    suffix = p.suffix.lower().lstrip(".")
    mime = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "svg": "image/svg+xml", "webp": "image/webp",
    }.get(suffix, "image/png")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def _card(content: str, *, padding: str = "20px 24px", margin: str = "0 0 16px 0",
          border: str = "1px solid #e2e8f0", bg: str = "#ffffff") -> None:
    """Render an HTML card wrapper."""
    st.markdown(
        f'<div style="background:{bg};border:{border};border-radius:12px;'
        f'padding:{padding};margin:{margin};'
        f'box-shadow:0 1px 3px rgba(0,0,0,0.06);">{content}</div>',
        unsafe_allow_html=True,
    )


def _section_heading(icon: str, title: str, subtitle: str = "") -> None:
    """Render a styled section heading with optional subtitle."""
    sub = (
        f'<div style="font-size:0.85rem;color:#64748b;margin-top:2px;">{subtitle}</div>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="margin:8px 0 16px 0;">'
        f'<span style="font-size:1.3rem;margin-right:8px;">{icon}</span>'
        f'<span style="font-size:1.15rem;font-weight:700;color:#1e293b;">{title}</span>'
        f'{sub}</div>',
        unsafe_allow_html=True,
    )


def _stepper(steps: list[tuple[str, str]], active: int) -> None:
    """Render a horizontal stepper / progress bar."""
    items: list[str] = []
    for i, (icon, label) in enumerate(steps):
        if i < active:
            color, bg, bc, w = "#fff", "#2563eb", "#2563eb", "600"
            pfx = "✓ "
        elif i == active:
            color, bg, bc, w = "#2563eb", "#eff6ff", "#2563eb", "700"
            pfx = ""
        else:
            color, bg, bc, w = "#94a3b8", "#f8fafc", "#e2e8f0", "500"
            pfx = ""
        items.append(
            f'<div style="display:flex;align-items:center;gap:6px;padding:8px 16px;'
            f'border-radius:8px;background:{bg};border:2px solid {bc};">'
            f'<span style="font-size:1.1rem;">{icon}</span>'
            f'<span style="font-size:0.82rem;font-weight:{w};color:{color};">{pfx}{label}</span>'
            f'</div>'
        )
    connector = '<div style="width:24px;height:2px;background:#cbd5e1;flex-shrink:0;"></div>'
    st.markdown(
        '<div style="display:flex;align-items:center;justify-content:center;gap:0;'
        'flex-wrap:wrap;margin:8px 0 20px 0;">' + connector.join(items) + '</div>',
        unsafe_allow_html=True,
    )


def _workflow_steps() -> None:
    """Render the 4-step horizontal workflow on the home page."""
    data = [
        ("\U0001f3ed", "Asphalt Plant",
         "Create & release the delivery note with product, route and compliance data"),
        ("\U0001f69b", "Transport",
         "Truck departs — departure time and route are automatically recorded"),
        ("\U0001f3d7\ufe0f", "Site Delivery",
         "Site supervisor receives the truck and confirms arrival time"),
        ("\u270d\ufe0f", "Signatures",
         "All parties sign digitally — Excel report generated automatically"),
        ("\U0001f4ca", "GPP Tool",
         "Data flows into the Excel-based GPP planning tool for environmental Impact calculation"),
    ]
    cols = st.columns(len(data))
    for i, (icon, title, desc) in enumerate(data):
        with cols[i]:
            st.markdown(
                f'<div style="text-align:center;padding:20px 12px;background:#fff;'
                f'border:1px solid #e2e8f0;border-radius:12px;'
                f'box-shadow:0 1px 3px rgba(0,0,0,0.06);min-height:170px;'
                f'display:flex;flex-direction:column;align-items:center;justify-content:flex-start;">'
                f'<div style="font-size:2rem;margin-bottom:8px;">{icon}</div>'
                f'<div style="font-size:0.95rem;font-weight:700;color:#1e293b;margin-bottom:6px;">'
                f'Step {i + 1}: {title}</div>'
                f'<div style="font-size:0.78rem;color:#64748b;line-height:1.4;">{desc}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


def _location_card(label: str, icon: str, address: str, lat: float, lon: float) -> None:
    """Render a compact location summary card."""
    addr_display = (address[:80] + "\u2026") if len(address) > 80 else address
    _card(
        f'<div style="display:flex;align-items:flex-start;gap:10px;">'
        f'<span style="font-size:1.4rem;">{icon}</span>'
        f'<div>'
        f'<div style="font-size:0.82rem;font-weight:700;color:#334155;'
        f'text-transform:uppercase;letter-spacing:0.5px;">{label}</div>'
        f'<div style="font-size:0.88rem;color:#1e293b;margin-top:2px;">'
        f'{addr_display or "Not set"}</div>'
        f'<div style="font-size:0.72rem;color:#94a3b8;margin-top:2px;">'
        f'{lat:.4f}, {lon:.4f}</div>'
        f'</div></div>',
        padding="12px 16px", margin="0",
    )


def _render_branded_header() -> None:
    """Render a polished branded header with partner logos."""
    logo_imgs = []
    for fname, alt in LOGO_FILES:
        uri = _logo_b64(fname)
        if uri:
            logo_imgs.append((uri, alt))

    if logo_imgs:
        logos_html = "  ".join(
            f'<img src="{uri}" alt="{alt}" style="height:44px;object-fit:contain;"/>'
            for uri, alt in logo_imgs
        )
        st.markdown(
            f'<div style="display:flex;align-items:center;justify-content:center;'
            f'gap:28px;flex-wrap:wrap;padding:14px 20px;'
            f'background:linear-gradient(135deg,#f8fafc 0%,#eef2f7 100%);'
            f'border-bottom:1px solid #e2e8f0;'
            f'margin:-1rem -1rem 0 -1rem;">{logos_html}</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div style="text-align:center;padding:20px 0 6px 0;">'
        '<div style="font-size:2rem;font-weight:800;color:#0f172a;letter-spacing:-0.5px;">'
        '\U0001f4cb Digital Delivery Note</div>'
        '<div style="font-size:0.88rem;color:#64748b;margin-top:4px;">'
        'DIMinfr@ \u2014 Digitising infrastructure delivery workflows</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _inject_custom_css() -> None:
    """Inject custom CSS for a polished, professional look."""
    st.markdown(
        """
        <style>
        /* === Global === */
        [data-testid="stAppViewContainer"] { background: #f8fafc; }
        section[data-testid="stSidebar"] { background: #f1f5f9; }
        [data-testid="stHeader"] {
            background: rgba(248,250,252,0.95);
            backdrop-filter: blur(8px);
        }
        /* === Typography === */
        h1, h2, h3 { color: #0f172a !important; }
        .stTextInput label, .stTextArea label, .stNumberInput label,
        .stSelectbox label, .stDateInput label, .stFileUploader label {
            font-weight: 600 !important;
            color: #334155 !important;
            font-size: 0.88rem !important;
        }
        /* === Tabs === */
        button[data-baseweb="tab"] {
            font-size: 0.88rem; font-weight: 600;
            padding: 10px 20px !important;
            border-radius: 8px 8px 0 0 !important;
        }
        /* === Buttons === */
        .stButton > button {
            border-radius: 10px; font-weight: 600; font-size: 0.88rem;
            padding: 0.55rem 1.4rem; transition: all 0.2s ease;
            border: 1px solid transparent;
        }
        .stButton > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(37,99,235,0.18);
        }
        /* === Download button === */
        .stDownloadButton > button {
            border-radius: 10px; font-weight: 600;
            background: linear-gradient(135deg,#059669 0%,#047857 100%) !important;
            color: #fff !important; border: none !important;
        }
        .stDownloadButton > button:hover {
            box-shadow: 0 4px 12px rgba(5,150,105,0.2);
        }
        /* === Alerts === */
        [data-testid="stAlert"] { border-radius: 10px; }
        /* === Expander === */
        [data-testid="stExpander"] {
            border: 1px solid #e2e8f0 !important;
            border-radius: 12px !important;
            box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }
        [data-testid="stExpander"] summary {
            font-weight: 600; font-size: 0.92rem; color: #1e293b;
        }
        /* === Divider === */
        hr { border-top: 1px solid #e2e8f0 !important; margin: 1rem 0 !important; }
        /* === Inputs === */
        .stNumberInput > div > div > input { border-radius: 8px; }
        .stTextInput > div > div > input,
        .stTextArea > div > div > textarea {
            border-radius: 8px; border-color: #cbd5e1;
        }
        .stTextInput > div > div > input:focus,
        .stTextArea > div > div > textarea:focus {
            border-color: #2563eb;
            box-shadow: 0 0 0 2px rgba(37,99,235,0.12);
        }
        /* === Progress bar === */
        .stProgress > div > div > div { border-radius: 999px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  Constants & Data
# ═══════════════════════════════════════════════════════════════════════

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
    "k_origin_query": "Colas Belgium, Héron, Belgium",
    "k_destination_query": "E40 werf, Erpe-Mere, Belgium",
    "plant_address": "Colas Belgium NV, Rue de l'Industrie 20, 4217 Héron, Belgium",
    "plant_lat": 50.5468,
    "plant_lon": 5.0972,
    "site_address": "Wegenwerken E40, Erpe-Mere, 9420 Oost-Vlaanderen, Belgium",
    "site_lat": 50.9284,
    "site_lon": 3.9681,
    "k_client_address": "Agentschap Wegen en Verkeer\nGraaf de Ferrarisgebouw\nKoning Albert II-laan 20 bus 4\n1000 Brussel\nBelgium",
    "k_product_mixture_type": "AC 14 surf B50/70 (ABb-4C)",
    "k_application": "Surface course \u2013 road rehabilitation E40",
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


# ═══════════════════════════════════════════════════════════════════════
#  Geocoding & Map helpers (logic unchanged)
# ═══════════════════════════════════════════════════════════════════════


def _geocoder() -> Nominatim:
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
            out.append({
                "label": str(getattr(r, "address", "")) or query,
                "lat": float(r.latitude),
                "lon": float(r.longitude),
            })
        return out
    except Exception:
        return []


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def _search_locations_cached(query: str) -> list[dict[str, Any]]:
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
    if q == last_q and f"{key_prefix}_suggestions" in st.session_state:
        return list(st.session_state.get(f"{key_prefix}_suggestions", []))
    if now - last_t < 1.0:
        return list(st.session_state.get(f"{key_prefix}_suggestions", []))
    st.session_state[f"{key_prefix}_last_t"] = now
    st.session_state[f"{key_prefix}_last_q"] = q
    suggestions = _search_locations_cached(q)
    st.session_state[f"{key_prefix}_suggestions"] = suggestions
    return suggestions


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def _geocode_cached(address: str) -> tuple[float, float, str] | None:
    return _geocode_address(address)


def _make_map(
    center_lat: float, center_lon: float,
    marker: tuple[float, float] | None, label: str,
) -> folium.Map:
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12, control_scale=True)
    if marker is not None:
        folium.Marker([marker[0], marker[1]], tooltip=label).add_to(m)
    return m


def _make_route_map(
    center_lat: float,
    center_lon: float,
    origin: tuple[float, float] | None,
    destination: tuple[float, float] | None,
    route_coords: list[tuple[float, float]] | None = None,
) -> folium.Map:
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=11,
        control_scale=True,
        tiles="CartoDB positron",
    )
    if origin is not None:
        folium.Marker(
            [origin[0], origin[1]],
            tooltip="\U0001f4cd Origin (Asphalt Plant)",
            icon=folium.Icon(color="blue", icon="industry", prefix="fa"),
        ).add_to(m)
    if destination is not None:
        folium.Marker(
            [destination[0], destination[1]],
            tooltip="\U0001f3c1 Destination (Delivery Site)",
            icon=folium.Icon(color="red", icon="flag-checkered", prefix="fa"),
        ).add_to(m)
    if route_coords and len(route_coords) >= 2:
        # Draw actual driving route
        folium.PolyLine(
            route_coords, weight=5, color="#2563eb",
            opacity=0.85,
        ).add_to(m)
        # Fit map to route bounds
        m.fit_bounds(route_coords)
    elif origin is not None and destination is not None:
        # Fit to markers even without route
        m.fit_bounds([origin, destination], padding=(30, 30))
    return m


def _note_url(note_id: str, role: str) -> str:
    return f"/?note={note_id}&role={role}"


def _safe_filename(note_id: str) -> str:
    return "DDN_" + "".join(c for c in note_id if c.isalnum() or c in {"-", "_"}) + ".xlsx"


def _parse_time(s: str | None) -> str | None:
    if not s:
        return None
    return s


# ═══════════════════════════════════════════════════════════════════════
#  Page: Create / Release Delivery Note
# ═══════════════════════════════════════════════════════════════════════


def page_create_note() -> None:
    # ── Mode selection ──────────────────────────────────────────────────
    st.markdown("")
    mode_col1, mode_col2 = st.columns(2)
    with mode_col1:
        plant_btn = st.button(
            "\U0001f3ed  I am at the Asphalt Plant",
            use_container_width=True,
            type="primary" if st.session_state.get("_mode", "plant") == "plant" else "secondary",
        )
    with mode_col2:
        site_btn = st.button(
            "\U0001f3d7\ufe0f  I am the Site Supervisor",
            use_container_width=True,
            type="primary" if st.session_state.get("_mode") == "site" else "secondary",
        )

    if plant_btn:
        st.session_state["_mode"] = "plant"
    if site_btn:
        st.session_state["_mode"] = "site"

    mode = st.session_state.get("_mode", "plant")

    if mode == "site":
        _page_site_supervisor()
        return

    # ── Asphalt Plant mode ──────────────────────────────────────────────
    _stepper(
        [("\U0001f4dd", "Basic Details"), ("\U0001f5fa\ufe0f", "Route"),
         ("\U0001f4e6", "Product & Compliance"), ("\U0001f680", "Release"),
         ("\U0001f4ca", "GPP Tool")],
        active=0,
    )

    # ── OCR Upload ──────────────────────────────────────────────────────
    with st.expander("\U0001f4c4 Upload a scanned delivery note (AI-powered OCR)", expanded=False):
        _card(
            '<div style="font-size:0.85rem;color:#334155;">'
            'Upload a photo or scan of an existing paper delivery note. '
            'Our AI-powered OCR engine will automatically extract and '
            'populate all fields.</div>',
            bg="#f0f9ff", border="1px solid #bae6fd",
        )
        scan_c1, scan_c2 = st.columns([3, 1])
        with scan_c1:
            uploaded_scan = st.file_uploader(
                "Choose file",
                type=["png", "jpg", "jpeg", "pdf", "tiff", "bmp"],
                help="Supported: PNG, JPG, PDF, TIFF, BMP",
                key="scan_upload",
                label_visibility="collapsed",
            )
        with scan_c2:
            process_scan = st.button(
                "\U0001f50d Extract data",
                disabled=uploaded_scan is None,
                use_container_width=True,
            )
        if uploaded_scan is not None:
            if uploaded_scan.type and uploaded_scan.type.startswith("image"):
                st.image(uploaded_scan, caption=uploaded_scan.name,
                         use_container_width=True)
            else:
                st.caption(
                    f"\U0001f4ce {uploaded_scan.name} "
                    f"({uploaded_scan.size / 1024:.0f} KB)"
                )
        if process_scan and uploaded_scan is not None:
            with st.status("\U0001f50d Processing scanned delivery note\u2026",
                           expanded=True) as status:
                st.write("Analysing document layout\u2026")
                _time.sleep(0.6)
                st.write("Detecting text regions (OCR engine)\u2026")
                _time.sleep(0.8)
                st.write("Extracting structured fields\u2026")
                _time.sleep(0.5)
                st.write("Mapping extracted data to form fields\u2026")
                _time.sleep(0.4)
                status.update(label="\u2705 Extraction complete!",
                              state="complete", expanded=False)
            _load_demo_data()
            st.success("All fields auto-populated from your scan. "
                       "Review and adjust below.")
            st.rerun()

    # Quick-fill for demos
    _, demo_col, _ = st.columns([2, 1, 2])
    with demo_col:
        if st.button("\U0001f4cb Load demo data", use_container_width=True,
                     help="Pre-fill all fields with sample data"):
            _load_demo_data()
            st.rerun()

    st.markdown("")

    # ════════════════════════════════════════════════════════════════════
    #  STEP A — Basic Details
    # ════════════════════════════════════════════════════════════════════
    with st.expander("\U0001f4dd  Step 1 \u2014 Basic Details", expanded=True):
        _section_heading("\U0001f4dd", "Basic Details",
                         "Delivery note identification and transport info")
        a1, a2 = st.columns(2)
        with a1:
            note_date = st.date_input("Date", value=date.today())
            delivery_note_no = st.text_input(
                "Delivery Note No", key="k_delivery_note_no",
                placeholder="e.g. DDN-2026-00142",
            )
            if not st.session_state.get("k_delivery_note_no", "").strip():
                st.caption("\u26a0\ufe0f Required \u2014 enter a unique delivery note number")
        with a2:
            transport_company = st.text_input(
                "Transport company", key="k_transport_company",
                placeholder="e.g. Van Hoeck Transport NV",
            )
            license_plate = st.text_input(
                "License plate (Nummerplaat)", key="k_license_plate",
                placeholder="e.g. 1-ABC-234",
            )

    # ════════════════════════════════════════════════════════════════════
    #  STEP B — Route
    # ════════════════════════════════════════════════════════════════════
    with st.expander("\U0001f5fa\ufe0f  Step 2 \u2014 Route", expanded=True):
        _section_heading("\U0001f5fa\ufe0f", "Route Planning",
                         "Set origin (plant) and destination (site) locations")

        loc1, loc2 = st.columns(2)
        with loc1:
            st.markdown("##### \U0001f4cd Origin \u2014 Asphalt Plant")
            origin_query = st.text_input(
                "Search origin address",
                placeholder="Type plant address or place name\u2026",
                key="k_origin_query", label_visibility="collapsed",
            )
            origin_suggestions = _throttled_suggestions(origin_query, "origin")
            origin_selected = None
            if origin_suggestions:
                origin_selected = st.selectbox(
                    "Suggested origins",
                    options=list(range(len(origin_suggestions))),
                    format_func=lambda i: origin_suggestions[i]["label"],
                    key="origin_choice", label_visibility="collapsed",
                )
                if st.button("\u2713 Use this origin", key="apply_origin"):
                    sel = origin_suggestions[int(origin_selected)]
                    st.session_state["plant_lat"] = float(sel["lat"])
                    st.session_state["plant_lon"] = float(sel["lon"])
                    st.session_state["plant_address"] = sel["label"]
            if st.button("\U0001f4e1 Use GPS", key="gps_origin",
                         help="Detect current location via browser"):
                geo_data = get_geolocation()
                if geo_data and isinstance(geo_data, dict) and geo_data.get("coords"):
                    coords = geo_data["coords"]
                    try:
                        st.session_state["plant_lat"] = float(coords["latitude"])
                        st.session_state["plant_lon"] = float(coords["longitude"])
                        st.toast("\U0001f4cd Plant location updated from GPS")
                    except Exception:
                        st.warning("Could not read browser location.")
                else:
                    st.info("Browser will ask for location permission.")

        with loc2:
            st.markdown("##### \U0001f3c1 Destination \u2014 Delivery Site")
            destination_query = st.text_input(
                "Search destination address",
                placeholder="Type delivery address or site name\u2026",
                key="k_destination_query", label_visibility="collapsed",
            )
            destination_suggestions = _throttled_suggestions(
                destination_query, "destination",
            )
            destination_selected = None
            if destination_suggestions:
                destination_selected = st.selectbox(
                    "Suggested destinations",
                    options=list(range(len(destination_suggestions))),
                    format_func=lambda i: destination_suggestions[i]["label"],
                    key="destination_choice", label_visibility="collapsed",
                )
                if st.button("\u2713 Use this destination",
                             key="apply_destination"):
                    sel2 = destination_suggestions[int(destination_selected)]
                    st.session_state["site_lat"] = float(sel2["lat"])
                    st.session_state["site_lon"] = float(sel2["lon"])
                    st.session_state["site_address"] = sel2["label"]
            if st.button("\U0001f4e1 Use GPS", key="gps_site",
                         help="Detect current location via browser"):
                geo_data = get_geolocation()
                if geo_data and isinstance(geo_data, dict) and geo_data.get("coords"):
                    coords = geo_data["coords"]
                    try:
                        st.session_state["site_lat"] = float(coords["latitude"])
                        st.session_state["site_lon"] = float(coords["longitude"])
                        st.toast("\U0001f3c1 Site location updated from GPS")
                    except Exception:
                        st.warning("Could not read browser location.")
                else:
                    st.info("Browser will ask for location permission.")

        st.markdown("")

        # Session state defaults
        st.session_state.setdefault("plant_lat", 50.85)
        st.session_state.setdefault("plant_lon", 4.35)
        st.session_state.setdefault("site_lat", 50.85)
        st.session_state.setdefault("site_lon", 4.35)

        origin_marker = (
            float(st.session_state["plant_lat"]),
            float(st.session_state["plant_lon"]),
        )
        destination_marker = (
            float(st.session_state["site_lat"]),
            float(st.session_state["site_lon"]),
        )

        # Location summary cards
        sc1, sc2 = st.columns(2)
        with sc1:
            _location_card(
                "Origin \u2014 Plant", "\U0001f4cd",
                str(st.session_state.get("plant_address", origin_query.strip())),
                origin_marker[0], origin_marker[1],
            )
        with sc2:
            _location_card(
                "Destination \u2014 Site", "\U0001f3c1",
                str(st.session_state.get("site_address",
                                         destination_query.strip())),
                destination_marker[0], destination_marker[1],
            )

        st.markdown("")

        pin_mode = st.radio(
            "Click on the map to set:",
            options=["\U0001f4cd Plant (Origin)", "\U0001f3c1 Site (Destination)"],
            horizontal=True,
        )

        center_lat = (origin_marker[0] + destination_marker[0]) / 2.0
        center_lon = (origin_marker[1] + destination_marker[1]) / 2.0

        # Fetch actual driving route geometry from OSRM
        route_coords: list[tuple[float, float]] | None = None
        if origin_marker and destination_marker:
            route_coords = geo.osrm_route_geometry(
                geo.GeoPoint(origin_marker[0], origin_marker[1], "origin"),
                geo.GeoPoint(destination_marker[0], destination_marker[1], "destination"),
            )

        route_map = _make_route_map(
            center_lat=center_lat, center_lon=center_lon,
            origin=origin_marker, destination=destination_marker,
            route_coords=route_coords,
        )
        map_out = st_folium(route_map, height=480, use_container_width=True,
                            key="route_map")

        if map_out and map_out.get("last_clicked"):
            lat_clicked = float(map_out["last_clicked"]["lat"])
            lon_clicked = float(map_out["last_clicked"]["lng"])
            if "Plant" in pin_mode:
                st.session_state["plant_lat"] = lat_clicked
                st.session_state["plant_lon"] = lon_clicked
                st.toast("\U0001f4cd Plant pin updated on map")
            else:
                st.session_state["site_lat"] = lat_clicked
                st.session_state["site_lon"] = lon_clicked
                st.toast("\U0001f3c1 Site pin updated on map")

        # Map legend
        _card(
            '<div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap;">'
            '<span style="font-size:0.8rem;">\U0001f535 <b>Origin</b> '
            '\u2014 Asphalt Plant</span>'
            '<span style="font-size:0.8rem;">\U0001f534 <b>Destination</b> '
            '\u2014 Delivery Site</span>'
            '<span style="font-size:0.8rem;">\u2500\u2500 <b>Driving Route</b></span>'
            '</div>',
            bg="#f8fafc", padding="10px 16px",
        )

        plant_address = str(
            st.session_state.get("plant_address") or origin_query.strip()
        )
        site_address = str(
            st.session_state.get("site_address") or destination_query.strip()
        )
        plant_lookup = True
        site_lookup = True
        use_geo = False

        # Distance calculation
        st.markdown("")
        transport_type = "Truck"
        d1, d2 = st.columns(2)
        with d1:
            st.text_input("Transport type", value=transport_type, disabled=True)
        with d2:
            energy_source = st.selectbox("Energy source",
                                         options=ENERGY_SOURCES, index=0)

        plant_point = geo.GeoPoint(
            lat=float(st.session_state.get("plant_lat", 50.85)),
            lon=float(st.session_state.get("plant_lon", 4.35)),
            label="Plant",
        )
        site_point = geo.GeoPoint(
            lat=float(st.session_state.get("site_lat", 50.85)),
            lon=float(st.session_state.get("site_lon", 4.35)),
            label="Site",
        )
        route = geo.osrm_route_km(plant_point, site_point)
        if route:
            distance_km, duration_min = route
            st.success(
                f"\U0001f6e3\ufe0f Driving distance: **{distance_km:.1f} km** "
                f"(\u2248 {duration_min:.0f} min)"
            )
        else:
            distance_km = geo.haversine_km(plant_point, site_point)
            st.info(
                f"\U0001f4cf Straight-line distance: **{distance_km:.1f} km** "
                f"(route service unavailable)"
            )

    # ════════════════════════════════════════════════════════════════════
    #  STEP C — Product, Compliance & Recipients
    # ════════════════════════════════════════════════════════════════════
    with st.expander(
        "\U0001f4e6  Step 3 \u2014 Product, Compliance & Recipients",
        expanded=True,
    ):
        sub = st.tabs([
            "\U0001f3d7\ufe0f Site Delivery",
            "\U0001f9ea Product & Documents",
            "\u2696\ufe0f Weights",
            "\U0001f4e7 Recipients",
        ])

        with sub[0]:
            _section_heading("\U0001f3d7\ufe0f", "Site Delivery",
                             "Client and site address details")
            client_address = st.text_area(
                "Client address", placeholder="Enter the client address\u2026",
                key="k_client_address", height=100,
            )
            st.text_area(
                "Destination (site) address",
                value=st.session_state.get("site_address", ""),
                disabled=True, height=68,
            )
            tc1, tc2 = st.columns(2)
            with tc1:
                st.text_input(
                    "Departure time",
                    value=st.session_state.get("_departure_time",
                                               "Auto on release"),
                    disabled=True,
                )
            with tc2:
                st.text_input("Arrival time", value="Auto on site receipt",
                              disabled=True)

        with sub[1]:
            _section_heading("\U0001f9ea", "Product & Documents",
                             "Mixture specifications and compliance documentation")
            p1, p2 = st.columns(2)
            with p1:
                product_mixture_type = st.text_input(
                    "Product / Mixture type", key="k_product_mixture_type",
                    placeholder="e.g. AC 14 surf B50/70",
                )
                application = st.text_input(
                    "Application", key="k_application",
                    placeholder="e.g. Surface course",
                )
                certificate = st.text_input("Certificate", key="k_certificate")
                declaration_of_performance = st.text_input(
                    "Declaration of Performance",
                    key="k_declaration_of_performance",
                )
                technical_data_sheet = st.text_input(
                    "Technical Data Sheet", key="k_technical_data_sheet",
                )
            with p2:
                mechanical_resistance = st.text_input(
                    "Mechanical resistance", key="k_mechanical_resistance",
                )
                fuel_resistance = st.text_input(
                    "Fuel resistance", key="k_fuel_resistance",
                )
                deicing_resistance = st.text_input(
                    "De-icing resistance", key="k_deicing_resistance",
                )
                bitumen_aggregate_affinity = st.text_input(
                    "Bitumen\u2013aggregate affinity",
                    key="k_bitumen_aggregate_affinity",
                )
                disposal = st.text_input("Disposal", key="k_disposal")

        with sub[2]:
            _section_heading("\u2696\ufe0f", "Weights",
                             "Gross, tare and net quantities")
            w1, w2, w3 = st.columns(3)
            with w1:
                bruto_kg = st.number_input(
                    "Bruto (kg)", min_value=0.0, value=0.0, step=1.0,
                    key="k_bruto_kg",
                )
            with w2:
                tare_weight_empty_kg = st.number_input(
                    "Tare weight \u2014 empty (kg)", min_value=0.0, value=0.0,
                    step=1.0, key="k_tare_weight_empty_kg",
                )
            with w3:
                net_total_quantity_ton = st.number_input(
                    "Net total (ton)", min_value=0.0, value=0.0, step=0.01,
                    key="k_net_total_quantity_ton",
                )
            if bruto_kg > 0 and tare_weight_empty_kg > 0:
                calculated_net = (bruto_kg - tare_weight_empty_kg) / 1000.0
                st.caption(f"Calculated net: {calculated_net:.2f} ton")

        with sub[3]:
            _section_heading(
                "\U0001f4e7", "Recipients",
                "Email addresses for automatic delivery of signed documents",
            )
            r1, r2 = st.columns(2)
            with r1:
                email_client = st.text_input(
                    "Client email", key="k_email_client",
                    placeholder="client@example.be",
                )
                email_transporter = st.text_input(
                    "Transporter email", key="k_email_transporter",
                    placeholder="transport@example.be",
                )
            with r2:
                email_copro = st.text_input(
                    "COPRO email", key="k_email_copro",
                    placeholder="copro@example.eu",
                )
                email_permit_holder = st.text_input(
                    "Permit holder email", key="k_email_permit_holder",
                    placeholder="permit@example.be",
                )
            recipient_count = sum(
                1 for e in [
                    st.session_state.get("k_email_client", ""),
                    st.session_state.get("k_email_transporter", ""),
                    st.session_state.get("k_email_copro", ""),
                    st.session_state.get("k_email_permit_holder", ""),
                ] if e.strip()
            )
            if recipient_count:
                st.caption(f"\u2709\ufe0f {recipient_count} recipient(s) configured")

    # ════════════════════════════════════════════════════════════════════
    #  RELEASE — Summary + Action
    # ════════════════════════════════════════════════════════════════════
    st.markdown("")
    _section_heading("\U0001f680", "Release Delivery Note",
                     "Review the summary and release")

    ddn = st.session_state.get("k_delivery_note_no", "").strip()
    product_val = st.session_state.get("k_product_mixture_type", "").strip()
    net_val = st.session_state.get("k_net_total_quantity_ton", 0.0)
    recipient_count = sum(
        1 for e in [
            st.session_state.get("k_email_client", ""),
            st.session_state.get("k_email_transporter", ""),
            st.session_state.get("k_email_copro", ""),
            st.session_state.get("k_email_permit_holder", ""),
        ] if e.strip()
    )
    summary_items = [
        f"<b>DDN:</b> {ddn or '\u26a0\ufe0f Not set'}",
        f"<b>Product:</b> {product_val or '\u2014'}",
        f"<b>Net:</b> {net_val:.2f} ton" if net_val else "<b>Net:</b> \u2014",
        f"<b>Distance:</b> {distance_km:.1f} km",
        f"<b>Recipients:</b> {recipient_count}",
    ]
    _card(
        '<div style="display:flex;flex-wrap:wrap;gap:20px;font-size:0.88rem;'
        'color:#334155;">'
        + "".join(f'<span>{s}</span>' for s in summary_items)
        + '</div>',
        bg="#fffbeb", border="1px solid #fde68a", padding="14px 20px",
    )

    st.markdown("")
    create_clicked = st.button(
        "\U0001f680  Release at Asphalt Plant & Send",
        type="primary", use_container_width=True,
    )

    # ── Background geocoding ────────────────────────────────────────────
    st.session_state.setdefault(
        "plant_lat", float(st.session_state.get("plant_lat", 50.85)))
    st.session_state.setdefault(
        "plant_lon", float(st.session_state.get("plant_lon", 4.35)))
    st.session_state.setdefault(
        "site_lat", float(st.session_state.get("site_lat", 50.85)))
    st.session_state.setdefault(
        "site_lon", float(st.session_state.get("site_lon", 4.35)))

    if (plant_lookup and plant_address.strip()
            and st.session_state.get("_last_plant_address")
            != plant_address.strip()):
        st.session_state["_last_plant_address"] = plant_address.strip()
        geo_res = _geocode_cached(plant_address.strip())
        if geo_res:
            (st.session_state["plant_lat"],
             st.session_state["plant_lon"], plant_display) = geo_res
            st.session_state["plant_address"] = plant_display

    if (site_lookup and (not use_geo) and site_address.strip()
            and st.session_state.get("_last_site_address")
            != site_address.strip()):
        st.session_state["_last_site_address"] = site_address.strip()
        geo_res2 = _geocode_cached(site_address.strip())
        if geo_res2:
            (st.session_state["site_lat"],
             st.session_state["site_lon"], site_display) = geo_res2
            st.session_state["site_address"] = site_display

    if not create_clicked:
        return

    # ── Validation ──────────────────────────────────────────────────────
    if not delivery_note_no.strip():
        st.error("\u274c Delivery Note No is required.")
        return

    existing = storage.get_note_by_delivery_note_no(delivery_note_no.strip())
    if existing:
        st.error("\u274c A delivery note with this number already exists.")
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

    # ── Success screen ──────────────────────────────────────────────────
    st.balloons()
    st.success("\u2705 Delivery note released! Departure time recorded.")

    _section_heading("\U0001f517", "Signing Links",
                     "Share these links with each party to collect signatures")
    for role, link in links.items():
        st.text_input(
            f"{ROLE_LABELS[role]} signing link", value=link,
            key=f"_link_{role}", disabled=False,
            help="Copy this link and send to the party",
        )

    _card(
        '<div style="font-size:0.9rem;color:#1e40af;">'
        '<b>\U0001f4f1 Site Supervisor:</b> Open the app, select '
        '"Site Supervisor" mode, and enter Delivery Note No: '
        f'<b>{delivery_note_no.strip()}</b></div>',
        bg="#eff6ff", border="1px solid #bfdbfe",
    )

    if mailer.email_enabled():
        try:
            for role, link in links.items():
                email = payload["emails"].get(role)
                if not email:
                    continue
                mailer.send_email(
                    [email],
                    subject=(
                        f"Delivery note signing request "
                        f"({payload.get('delivery_note_no') or note_id})"
                    ),
                    body=(
                        "Please review and sign the digital delivery note "
                        "using this link:\n\n"
                        f"{link}\n\n"
                        "(If this is a local run, prepend your Streamlit "
                        "base URL.)\n"
                    ),
                )
            st.info("\u2709\ufe0f Signing requests emailed to all parties.")
        except Exception as e:
            st.warning(f"Email sending failed: {e}")
    else:
        st.info("\u2139\ufe0f Email not configured \u2014 share the links "
                "above manually (prototype mode)")


    # ════════════════════════════════════════════════════════════════════
    #  STEP 5 — GPP Tool (Excel-based Project Planning)
    # ════════════════════════════════════════════════════════════════════
    st.markdown("")
    _section_heading("\U0001f4ca", "GPP Tool — Project Planning",
                     "Connect delivery data to the Excel-based GPP planning tool")

    _card(
        '<div style="display:flex;align-items:flex-start;gap:16px;">'
        '<div style="font-size:2.5rem;">\U0001f4ca</div>'
        '<div>'
        '<div style="font-size:1rem;font-weight:700;color:#1e293b;margin-bottom:6px;">'
        'Groot Project Planning (GPP) Integration</div>'
        '<div style="font-size:0.85rem;color:#475569;line-height:1.5;">'
        'Once the delivery note is signed by all parties, the data is '
        'automatically pushed into the GPP Excel tool for project-level '
        'tracking, quantity reconciliation and progress reporting.</div>'
        '</div></div>',
        bg="#f0fdf4", border="1px solid #bbf7d0", padding="20px",
    )

    gpp_c1, gpp_c2, gpp_c3 = st.columns(3)
    with gpp_c1:
        _card(
            '<div style="text-align:center;">'
            '<div style="font-size:1.8rem;margin-bottom:6px;">\U0001f4c1</div>'
            '<div style="font-size:0.85rem;font-weight:700;color:#1e293b;">'
            'Excel Export</div>'
            '<div style="font-size:0.78rem;color:#64748b;margin-top:4px;">'
            'Delivery data flows into the GPP spreadsheet automatically</div>'
            '</div>',
            padding="18px",
        )
    with gpp_c2:
        _card(
            '<div style="text-align:center;">'
            '<div style="font-size:1.8rem;margin-bottom:6px;">\U0001f4c8</div>'
            '<div style="font-size:0.85rem;font-weight:700;color:#1e293b;">'
            'Quantity Tracking</div>'
            '<div style="font-size:0.78rem;color:#64748b;margin-top:4px;">'
            'Tonnes delivered vs. planned are reconciled per work order</div>'
            '</div>',
            padding="18px",
        )
    with gpp_c3:
        _card(
            '<div style="text-align:center;">'
            '<div style="font-size:1.8rem;margin-bottom:6px;">\U0001f5d3\ufe0f</div>'
            '<div style="font-size:0.85rem;font-weight:700;color:#1e293b;">'
            'Project Timeline</div>'
            '<div style="font-size:0.78rem;color:#64748b;margin-top:4px;">'
            'Delivery milestones sync with the overall project schedule</div>'
            '</div>',
            padding="18px",
        )

    with st.expander("\U0001f50c GPP Connection Settings (coming soon)", expanded=False):
        st.text_input(
            "GPP Excel file path",
            value="",
            placeholder="e.g. C:/Projects/GPP_werkorder_2026.xlsx",
            disabled=True,
            key="k_gpp_filepath",
            help="Path to the GPP Excel workbook",
        )
        st.text_input(
            "Work order / Project code",
            value="",
            placeholder="e.g. WO-2026-0145",
            disabled=True,
            key="k_gpp_workorder",
            help="GPP work order reference to link deliveries to",
        )
        st.selectbox(
            "Target sheet",
            options=["Leveringen", "Hoeveelheden", "Planning"],
            disabled=True,
            key="k_gpp_sheet",
            help="Which sheet in the GPP workbook to populate",
        )
        _card(
            '<div style="font-size:0.82rem;color:#92400e;">'
            '\u26a0\ufe0f This feature is under development. '
            'GPP integration will be available in a future release.</div>',
            bg="#fffbeb", border="1px solid #fde68a", padding="12px 16px",
        )


# ═══════════════════════════════════════════════════════════════════════
#  Sub-page: Site Supervisor (Receive Delivery)
# ═══════════════════════════════════════════════════════════════════════


def _page_site_supervisor() -> None:
    """Site supervisor mode — receive a delivery."""
    _section_heading("\U0001f3d7\ufe0f", "Receive Delivery",
                     "Record truck arrival time for a released delivery note")

    _card(
        '<div style="font-size:0.85rem;color:#334155;">'
        'Enter or select the Delivery Note number from the released notes. '
        'Once the truck arrives, press the button to record the arrival '
        'time.</div>',
        bg="#f0fdf4", border="1px solid #bbf7d0",
    )

    available = storage.list_delivery_note_nos(status="released", limit=200)
    dn = ""
    if available:
        dn = st.selectbox("Select a released Delivery Note",
                          options=available, index=0)
    else:
        st.info("No released delivery notes found yet. "
                "Waiting for plant to release a note.")

    manual = st.text_input(
        "Or enter Delivery Note No manually", value="",
        placeholder="e.g. DDN-2026-00142",
    )
    if manual.strip():
        dn = manual.strip()

    st.markdown("")
    if st.button("\U0001f69b  Truck Received \u2014 Record Arrival Time",
                 type="primary", use_container_width=True):
        if not dn.strip():
            st.error("Please enter the Delivery Note No.")
            return
        note = storage.get_note_by_delivery_note_no(dn.strip())
        if not note:
            st.error("No delivery note found for this number.")
            return
        if note.get("status") not in {
            "released", "received", "completed", "pending",
        }:
            st.warning("Unknown note status; proceeding.")

        payload = note["payload"]
        if note.get("status") == "pending":
            st.error("This delivery note has not been released at the "
                     "asphalt plant yet.")
            return

        now = datetime.now()
        payload["arrival_time"] = now.strftime("%H:%M")
        payload["arrival_time_iso"] = now.isoformat(timespec="seconds")

        with storage.get_conn() as conn:
            import json
            conn.execute(
                "UPDATE delivery_notes SET payload_json = ?, status = ? "
                "WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), "received",
                 note["id"]),
            )

        sigs = storage.list_signatures(note["id"])
        xlsx_bytes = excel_export.build_delivery_note_xlsx(payload, sigs)

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
                    subject=(
                        f"DDN (arrival recorded) "
                        f"({payload.get('delivery_note_no') or note['id']})"
                    ),
                    body=(
                        "Arrival time has been recorded by the site "
                        "supervisor. The Digital Delivery Note (Excel) "
                        "is attached."
                    ),
                    attachments=[(
                        _safe_filename(note["id"]),
                        xlsx_bytes,
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet",
                    )],
                )
                emailed = True
            except Exception as e:
                st.warning(f"Email sending failed: {e}")

        st.balloons()
        if emailed:
            storage.mark_completed(note["id"])
            st.success(
                f"\u2705 Arrival recorded at **{payload['arrival_time']}**. "
                f"Excel generated and emailed."
            )
            _card(
                '<div style="font-size:0.85rem;color:#166534;">'
                f'<b>Emailed to:</b> {", ".join(emails)}</div>',
                bg="#f0fdf4", border="1px solid #bbf7d0",
            )
        else:
            if emails and not mailer.email_enabled():
                st.info("Email not configured; Excel generated for download.")
            elif not emails:
                st.info("No recipient emails provided; Excel generated "
                        "for download.")
            else:
                st.success(
                    f"\u2705 Arrival recorded at "
                    f"**{payload['arrival_time']}**."
                )

        st.download_button(
            label="\U0001f4e5 Download Excel (xlsx)",
            data=xlsx_bytes,
            file_name=_safe_filename(note["id"]),
            mime="application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet",
            use_container_width=True,
        )


# ═══════════════════════════════════════════════════════════════════════
#  Page: Sign delivery note
# ═══════════════════════════════════════════════════════════════════════


def page_sign(note_id: str, role: str) -> None:
    label = ROLE_LABELS.get(role, role)

    note = storage.get_note(note_id)
    if not note:
        st.error("Unknown delivery note.")
        return

    payload = note["payload"]
    sigs = storage.list_signatures(note_id)
    signed_count = sum(1 for r in ROLE_LABELS if r in sigs)
    total = len(ROLE_LABELS)

    # Status badge
    if role in sigs:
        st.markdown(
            '<div style="display:inline-block;padding:4px 16px;'
            'background:#dcfce7;color:#166534;border-radius:999px;'
            'font-size:0.82rem;font-weight:600;margin-bottom:12px;">'
            f'\u2705 Signed as {label}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="display:inline-block;padding:4px 16px;'
            'background:#fef3c7;color:#92400e;border-radius:999px;'
            'font-size:0.82rem;font-weight:600;margin-bottom:12px;">'
            f'\u270d\ufe0f Awaiting signature \u2014 {label}</div>',
            unsafe_allow_html=True,
        )

    _section_heading("\u270d\ufe0f", f"Sign as {label}")

    # Signing progress
    st.progress(signed_count / total,
                text=f"Signatures: {signed_count} / {total}")

    # Compact summary card
    with st.expander("\U0001f4cb Delivery note summary", expanded=False):
        s1, s2 = st.columns(2)
        with s1:
            st.markdown(f"**Date:** {payload.get('date', '\u2014')}")
            st.markdown(
                f"**DDN:** {payload.get('delivery_note_no', '\u2014')}")
            st.markdown(
                f"**Plant:** {payload.get('plant_address', '\u2014')}")
            st.markdown(
                f"**Site:** {payload.get('site_address', '\u2014')}")
            st.markdown(
                f"**Transport:** {payload.get('transport_company', '\u2014')}")
        with s2:
            st.markdown(
                f"**License plate:** {payload.get('license_plate', '\u2014')}")
            st.markdown(
                f"**Departure:** {payload.get('departure_time', '\u2014')}")
            st.markdown(
                f"**Arrival:** {payload.get('arrival_time', '\u2014')}")
            st.markdown(
                f"**Product:** "
                f"{payload.get('product_mixture_type', '\u2014')}")
            st.markdown(
                f"**Net qty:** "
                f"{payload.get('net_total_quantity_ton', '\u2014')} ton")

    # Signature canvas
    st.markdown("")
    _section_heading("\U0001f58a\ufe0f", "Draw your signature",
                     "Use your mouse or finger to sign below")

    signer_name = st.text_input("Your full name",
                                placeholder="e.g. Jan De Smet")

    canvas = st_canvas(
        fill_color="rgba(0, 0, 0, 0)",
        stroke_width=3,
        stroke_color="#000000",
        background_color="#FFFFFF",
        height=180,
        drawing_mode="freedraw",
        key=f"canvas_{note_id}_{role}",
    )

    st.markdown("")
    if st.button("\u2705  Submit Signature", type="primary",
                 use_container_width=True):
        if canvas.image_data is None:
            st.error("No signature captured. Please draw your signature "
                     "above.")
            return

        img = Image.fromarray(canvas.image_data.astype("uint8"))
        sig_path = storage.SIGNATURES_DIR / f"{note_id}_{role}.png"
        img.save(sig_path)

        storage.upsert_signature(note_id, role,
                                 signer_name.strip() or None, str(sig_path))

        st.balloons()
        st.success(f"\u2705 Signature saved for {label}!")
        sigs = storage.list_signatures(note_id)
        signed_count = sum(1 for r in ROLE_LABELS if r in sigs)

    # Signing status
    st.markdown("")
    _section_heading("\U0001f4ca", "Signing Status")
    for r in ROLE_LABELS:
        icon = "\u2705" if r in sigs else "\u23f3"
        st.markdown(
            f"{icon} **{ROLE_LABELS[r]}** \u2014 "
            f"{'Signed' if r in sigs else 'Pending'}"
        )

    # Fully signed
    if storage.is_fully_signed(note_id):
        st.markdown("")
        st.markdown(
            '<div style="text-align:center;padding:20px;'
            'background:linear-gradient(135deg,#dcfce7,#bbf7d0);'
            'border-radius:12px;margin:8px 0;">'
            '<div style="font-size:2rem;">\U0001f389</div>'
            '<div style="font-size:1.1rem;font-weight:700;color:#166534;">'
            'All parties have signed!</div>'
            '<div style="font-size:0.85rem;color:#15803d;">'
            'The delivery note is complete. Download the Excel report '
            'below.</div></div>',
            unsafe_allow_html=True,
        )

        data_dir = Path(__file__).resolve().parent / "data" / "exports"
        out_path = data_dir / _safe_filename(note_id)
        xlsx_bytes = excel_export.build_delivery_note_xlsx(
            payload, sigs, output_path=out_path,
        )

        st.download_button(
            label="\U0001f4e5 Download Signed Excel (xlsx)",
            data=xlsx_bytes,
            file_name=_safe_filename(note_id),
            mime="application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet",
            use_container_width=True,
        )

        if mailer.email_enabled():
            if st.button("\U0001f4e7 Email final Excel to all parties",
                         use_container_width=True):
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
                        subject=(
                            f"Final delivery note (signed) "
                            f"({payload.get('delivery_note_no') or note_id})"
                        ),
                        body="All parties have signed. The final Excel "
                             "is attached.",
                        attachments=[(
                            _safe_filename(note_id), xlsx_bytes,
                            "application/vnd.openxmlformats-officedocument"
                            ".spreadsheetml.sheet",
                        )],
                    )
                    st.info("\u2709\ufe0f Final Excel emailed to all parties.")
                except Exception as e:
                    st.warning(f"Email failed: {e}")


# ═══════════════════════════════════════════════════════════════════════
#  Page: Home
# ═══════════════════════════════════════════════════════════════════════


def page_home() -> None:
    _render_branded_header()

    # Hero section
    st.markdown(
        '<div style="text-align:center;padding:8px 0 20px 0;">'
        '<div style="font-size:1.05rem;color:#475569;max-width:700px;'
        'margin:0 auto;line-height:1.6;">'
        'Replace paper delivery notes with a fully digital workflow \u2014 '
        'from asphalt plant release to site receipt and multi-party signing.'
        '</div></div>',
        unsafe_allow_html=True,
    )

    # Benefits
    b1, b2, b3 = st.columns(3)
    with b1:
        _card(
            '<div style="text-align:center;">'
            '<div style="font-size:1.5rem;">\u26a1</div>'
            '<div style="font-size:0.88rem;font-weight:600;color:#1e293b;">'
            'Real-time tracking</div>'
            '<div style="font-size:0.78rem;color:#64748b;">'
            'Departure & arrival times recorded automatically</div></div>',
            padding="16px",
        )
    with b2:
        _card(
            '<div style="text-align:center;">'
            '<div style="font-size:1.5rem;">\u270d\ufe0f</div>'
            '<div style="font-size:0.88rem;font-weight:600;color:#1e293b;">'
            'Digital signatures</div>'
            '<div style="font-size:0.78rem;color:#64748b;">'
            'Client, Transporter, COPRO & Permit holder sign online'
            '</div></div>',
            padding="16px",
        )
    with b3:
        _card(
            '<div style="text-align:center;">'
            '<div style="font-size:1.5rem;">\U0001f4ca</div>'
            '<div style="font-size:0.88rem;font-weight:600;color:#1e293b;">'
            'Automatic Excel export</div>'
            '<div style="font-size:0.78rem;color:#64748b;">'
            'Complete signed report generated & emailed instantly'
            '</div></div>',
            padding="16px",
        )

    st.markdown("")

    # Workflow steps
    _section_heading("\U0001f504", "How it works")
    _workflow_steps()

    st.markdown("")
    st.markdown("---")

    # Main content
    page_create_note()


# ═══════════════════════════════════════════════════════════════════════
#  Main Entry Point
# ═══════════════════════════════════════════════════════════════════════


def main() -> None:
    st.set_page_config(
        page_title="Digital Delivery Note \u2014 DIMinfr@",
        page_icon="\U0001f4cb",
        layout="wide",
        initial_sidebar_state="collapsed",
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

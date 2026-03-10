from __future__ import annotations

import base64
import secrets
import gpp_integration
from datetime import datetime
from datetime import date
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
import ocr
import storage

APP_TITLE = "Digitale Leveringsbon"

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
        ("\U0001f3ed", "Asfaltcentrale",
         "Maak en geef de leveringsbon vrij met product-, route- en conformiteitsgegevens"),
        ("\U0001f69b", "Transport",
         "Vrachtwagen vertrekt — vertrektijd en route worden automatisch geregistreerd"),
        ("\U0001f3d7\ufe0f", "Levering op werf",
         "Werftoezichter ontvangt de vrachtwagen en bevestigt de aankomsttijd"),
        ("\u270d\ufe0f", "Handtekeningen",
         "Alle partijen tekenen digitaal — Excel-rapport wordt automatisch gegenereerd"),
        ("\U0001f4ca", "GPP Tool",
         "Gegevens vloeien in de Excel-gebaseerde GPP-planningstool voor milieu-impactberekening"),
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
                f'Stap {i + 1}: {title}</div>'
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
        f'{addr_display or "Niet ingesteld"}</div>'
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
        '\U0001f4cb Digitale Leveringsbon</div>'
        '<div style="font-size:0.88rem;color:#64748b;margin-top:4px;">'
        'DIMinfr@ \u2014 Digitalisering van leveringsprocessen in de infrastructuur</div>'
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
    "client": "Opdrachtgever",
    "transporter": "Vervoerder",
    "copro": "COPRO",
    "permit_holder": "Vergunninghouder",
}

ENERGY_SOURCES = ["Diesel_Euro5","Diesel_Euro6", "Biodiesel_4.5%","Biodiesel_7%","Biodiesel_10%","Biodiesel_20%","Biodiesel_100%","Electric","Electric_green"]

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
            tooltip="\U0001f4cd Herkomst (Asfaltcentrale)",
            icon=folium.Icon(color="blue", icon="industry", prefix="fa"),
        ).add_to(m)
    if destination is not None:
        folium.Marker(
            [destination[0], destination[1]],
            tooltip="\U0001f3c1 Bestemming (Leveringswerf)",
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
#  OCR scanning pipeline with field-by-field review
# ═══════════════════════════════════════════════════════════════════════


def _run_ocr_pipeline(
    *,
    source_type: str = "file",
    uploaded_file: Any = None,
    content_type: str | None = None,
    filename: str | None = None,
    image_bytes: bytes | None = None,
) -> None:
    """Execute the OCR pipeline and show a field-by-field review panel.

    Supports both file uploads and camera captures.
    """
    if not ocr.is_available():
        missing = ocr.missing_dependencies()
        st.error(
            f"OCR-afhankelijkheden niet geïnstalleerd: {', '.join(missing)}. "
            "Voer uit: `pip install pytesseract PyMuPDF` en installeer "
            "[Tesseract](https://github.com/UB-Mannheim/tesseract/wiki)."
        )
        return

    with st.status(
        "\U0001f50d Leveringsbon scannen en gegevens extraheren\u2026",
        expanded=True,
    ) as status:
        st.write("\U0001f4f7 Beeld voorbereiden\u2026")
        raw_text = ""
        field_details: list[dict[str, Any]] = []

        try:
            if source_type == "camera" and image_bytes is not None:
                st.write("\U0001f9e0 Tekst herkennen (OCR)\u2026")
                raw_text, field_details = ocr.scan_image_bytes(image_bytes)
            elif uploaded_file is not None:
                st.write("\U0001f9e0 Tekst herkennen (OCR)\u2026")
                raw_text, field_details = ocr.scan_and_extract_detailed(
                    uploaded_file,
                    content_type=content_type,
                    filename=filename,
                )
            else:
                status.update(
                    label="\u274c Geen invoer ontvangen",
                    state="error",
                )
                return
        except Exception as exc:
            status.update(
                label="\u274c OCR mislukt",
                state="error", expanded=True,
            )
            st.error(f"OCR-verwerkingsfout: {exc}")
            return

        if field_details:
            st.write(
                f"\U0001f50e {len(field_details)} veld(en) herkend\u2026"
            )
            status.update(
                label=f"\u2705 {len(field_details)} veld(en) geëxtraheerd",
                state="complete", expanded=False,
            )
        elif raw_text:
            status.update(
                label="\u26a0\ufe0f Tekst gevonden, geen velden herkend",
                state="complete", expanded=False,
            )
        else:
            status.update(
                label="\u26a0\ufe0f Geen tekst gedetecteerd",
                state="complete", expanded=False,
            )

    # --- Store results in session state for the review panel -----------
    if field_details:
        st.session_state["_ocr_field_details"] = field_details
        st.session_state["_ocr_raw_text"] = raw_text
    elif raw_text:
        st.session_state["_ocr_raw_text"] = raw_text
        st.warning(
            "OCR heeft tekst gedetecteerd maar kon geen specifieke "
            "velden herkennen. Bekijk de ruwe tekst hieronder en vul "
            "het formulier handmatig in."
        )
    else:
        st.warning(
            "Er kon geen tekst uit dit document worden geëxtraheerd. "
            "Controleer de beeldkwaliteit en probeer opnieuw."
        )

    # --- Field-by-field review panel -----------------------------------
    details = st.session_state.get("_ocr_field_details", [])
    if details:
        st.markdown("")
        _section_heading(
            "\U0001f4cb", "Herkende gegevens — controleer en bevestig",
            "Vink de velden aan die u wilt overnemen in het formulier",
        )

        # Build toggle states
        for i, fld in enumerate(details):
            toggle_key = f"_ocr_accept_{i}"
            st.session_state.setdefault(toggle_key, True)

        # Render as a styled table-like layout
        # Header
        hdr_cols = st.columns([0.5, 2, 3, 3])
        with hdr_cols[0]:
            st.markdown("**\u2714**")
        with hdr_cols[1]:
            st.markdown("**Veld**")
        with hdr_cols[2]:
            st.markdown("**Herkende waarde**")
        with hdr_cols[3]:
            st.markdown("**Bron (OCR-fragment)**")

        for i, fld in enumerate(details):
            toggle_key = f"_ocr_accept_{i}"
            row_cols = st.columns([0.5, 2, 3, 3])
            with row_cols[0]:
                st.checkbox(
                    "Accepteer", value=True, key=toggle_key,
                    label_visibility="collapsed",
                )
            with row_cols[1]:
                st.markdown(
                    f'<span style="font-size:0.85rem;font-weight:600;'
                    f'color:#334155;">{fld["label"]}</span>',
                    unsafe_allow_html=True,
                )
            with row_cols[2]:
                display_val = fld["value"]
                if isinstance(display_val, float):
                    display_val = f"{display_val:,.2f}".replace(",", " ")
                st.markdown(
                    f'<span style="font-size:0.88rem;color:#1e293b;'
                    f'font-weight:500;">{display_val}</span>',
                    unsafe_allow_html=True,
                )
            with row_cols[3]:
                src = fld.get("source", "")
                if src:
                    st.caption(f'"{src}"')

        st.markdown("")

        # Accept / reject buttons
        btn_c1, btn_c2, btn_c3 = st.columns([2, 2, 1])
        with btn_c1:
            apply_btn = st.button(
                "\u2705 Geselecteerde velden overnemen",
                type="primary",
                use_container_width=True,
            )
        with btn_c2:
            clear_btn = st.button(
                "\u274c Scan verwijderen",
                use_container_width=True,
            )
        with btn_c3:
            select_all = st.button(
                "\u2611 Alles",
                use_container_width=True,
            )

        if select_all:
            for i in range(len(details)):
                st.session_state[f"_ocr_accept_{i}"] = True
            st.rerun()

        if clear_btn:
            for i in range(len(details)):
                st.session_state.pop(f"_ocr_accept_{i}", None)
            st.session_state.pop("_ocr_field_details", None)
            st.session_state.pop("_ocr_raw_text", None)
            st.rerun()

        if apply_btn:
            applied = 0
            for i, fld in enumerate(details):
                if st.session_state.get(f"_ocr_accept_{i}", False):
                    st.session_state[fld["key"]] = fld["value"]
                    applied += 1
            # Clean up review state
            for i in range(len(details)):
                st.session_state.pop(f"_ocr_accept_{i}", None)
            st.session_state.pop("_ocr_field_details", None)
            st.session_state.pop("_ocr_raw_text", None)
            st.toast(
                f"\u2705 {applied} veld(en) overgenomen in het formulier"
            )
            st.rerun()

    # --- Raw OCR text (always available if text was found) -------------
    raw = st.session_state.get("_ocr_raw_text", "")
    if raw:
        with st.expander("\U0001f4c4 Ruwe OCR-tekst", expanded=False):
            st.text_area(
                "Geëxtraheerde tekst",
                value=raw,
                height=200,
                disabled=True,
                label_visibility="collapsed",
            )


# ═══════════════════════════════════════════════════════════════════════
#  Page: Create / Release Delivery Note
# ═══════════════════════════════════════════════════════════════════════


def page_create_note() -> None:
    # ── Mode selection ──────────────────────────────────────────────────
    st.markdown("")
    mode_col1, mode_col2 = st.columns(2)
    with mode_col1:
        plant_btn = st.button(
            "\U0001f3ed  Ik ben bij de Asfaltcentrale",
            use_container_width=True,
            type="primary" if st.session_state.get("_mode", "plant") == "plant" else "secondary",
        )
    with mode_col2:
        site_btn = st.button(
            "\U0001f3d7\ufe0f  Ik ben de Werftoezichter",
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
        [("\U0001f4dd", "Basisgegevens"), ("\U0001f5fa\ufe0f", "Route"),
         ("\U0001f4e6", "Product & Conformiteit"), ("\U0001f680", "Vrijgave"),
         ("\U0001f4ca", "GPP Tool")],
        active=0,
    )

    # ── OCR Scan ────────────────────────────────────────────────────────
    with st.expander("\U0001f4f7 Scan leveringsbon — automatisch invullen via OCR", expanded=False):
        _card(
            '<div style="display:flex;align-items:flex-start;gap:14px;">'
            '<div style="font-size:2rem;">\U0001f4f7</div>'
            '<div>'
            '<div style="font-size:0.95rem;font-weight:700;color:#1e293b;margin-bottom:4px;">'
            'Automatisch velden invullen via OCR</div>'
            '<div style="font-size:0.85rem;color:#475569;line-height:1.5;">'
            'Upload een scan of PDF van een bestaande papieren leveringsbon. Herkende gegevens worden '
            'automatisch in het formulier geplaatst.</div>'
            '</div></div>',
            bg="#f0f9ff", border="1px solid #bae6fd", padding="16px 20px",
        )

        scan_c1, scan_c2 = st.columns([3, 1])
        with scan_c1:
            uploaded_scan = st.file_uploader(
                "Kies bestand",
                type=["png", "jpg", "jpeg", "pdf", "tiff", "bmp"],
                help="Ondersteund: PNG, JPG, PDF, TIFF, BMP",
                key="scan_upload",
                label_visibility="collapsed",
            )
        with scan_c2:
            process_scan = st.button(
                "\U0001f50d Scannen",
                disabled=uploaded_scan is None,
                use_container_width=True,
                type="primary",
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

        # --- Process: file upload ---
        if process_scan and uploaded_scan is not None:
            _run_ocr_pipeline(
                source_type="file",
                uploaded_file=uploaded_scan,
                content_type=getattr(uploaded_scan, "type", None),
                filename=getattr(uploaded_scan, "name", None),
            )

    # Quick-fill for demos
    _, demo_col, _ = st.columns([2, 1, 2])
    with demo_col:
        if st.button("\U0001f4cb Demogegevens laden", use_container_width=True,
                     help="Vul alle velden in met voorbeeldgegevens"):
            _load_demo_data()
            st.rerun()

    st.markdown("")

    # ════════════════════════════════════════════════════════════════════
    #  STEP A — Basic Details
    # ════════════════════════════════════════════════════════════════════
    with st.expander("\U0001f4dd  Stap 1 \u2014 Basisgegevens", expanded=True):
        _section_heading("\U0001f4dd", "Basisgegevens",
                         "Identificatie leveringsbon en transportinfo")
        a1, a2 = st.columns(2)
        with a1:
            note_date = st.date_input("Datum", value=date.today())
            delivery_note_no = st.text_input(
                "Leveringsbonnummer", key="k_delivery_note_no",
                placeholder="bijv. DDN-2026-00142",
            )
            if not st.session_state.get("k_delivery_note_no", "").strip():
                st.caption("\u26a0\ufe0f Verplicht \u2014 voer een uniek leveringsbonnummer in")
        with a2:
            transport_company = st.text_input(
                "Transportbedrijf", key="k_transport_company",
                placeholder="bijv. Van Hoeck Transport NV",
            )
            license_plate = st.text_input(
                "Nummerplaat", key="k_license_plate",
                placeholder="bijv. 1-ABC-234",
            )

    # ════════════════════════════════════════════════════════════════════
    #  STEP B — Route
    # ════════════════════════════════════════════════════════════════════
    with st.expander("\U0001f5fa\ufe0f  Stap 2 \u2014 Route", expanded=True):
        _section_heading("\U0001f5fa\ufe0f", "Routeplanning",
                         "Stel herkomst (centrale) en bestemming (werf) in")

        loc1, loc2 = st.columns(2)
        with loc1:
            st.markdown("##### \U0001f4cd Herkomst \u2014 Asfaltcentrale")
            origin_query = st.text_input(
                "Zoek herkomstadres",
                placeholder="Typ adres of plaatsnaam van de centrale\u2026",
                key="k_origin_query", label_visibility="collapsed",
            )
            origin_suggestions = _throttled_suggestions(origin_query, "origin")
            origin_selected = None
            if origin_suggestions:
                origin_selected = st.selectbox(
                    "Voorgestelde herkomsten",
                    options=list(range(len(origin_suggestions))),
                    format_func=lambda i: origin_suggestions[i]["label"],
                    key="origin_choice", label_visibility="collapsed",
                )
                if st.button("\u2713 Gebruik deze herkomst", key="apply_origin"):
                    sel = origin_suggestions[int(origin_selected)]
                    st.session_state["plant_lat"] = float(sel["lat"])
                    st.session_state["plant_lon"] = float(sel["lon"])
                    st.session_state["plant_address"] = sel["label"]
            if st.button("\U0001f4e1 Gebruik GPS", key="gps_origin",
                         help="Detecteer huidige locatie via browser"):
                geo_data = get_geolocation()
                if geo_data and isinstance(geo_data, dict) and geo_data.get("coords"):
                    coords = geo_data["coords"]
                    try:
                        st.session_state["plant_lat"] = float(coords["latitude"])
                        st.session_state["plant_lon"] = float(coords["longitude"])
                        st.toast("\U0001f4cd Centralelocatie bijgewerkt via GPS")
                    except Exception:
                        st.warning("Kon browserlocatie niet lezen.")
                else:
                    st.info("Browser zal om locatietoestemming vragen.")

        with loc2:
            st.markdown("##### \U0001f3c1 Bestemming \u2014 Werf")
            destination_query = st.text_input(
                "Zoek bestemmingsadres",
                placeholder="Typ leveradres of werfnaam\u2026",
                key="k_destination_query", label_visibility="collapsed",
            )
            destination_suggestions = _throttled_suggestions(
                destination_query, "destination",
            )
            destination_selected = None
            if destination_suggestions:
                destination_selected = st.selectbox(
                    "Voorgestelde bestemmingen",
                    options=list(range(len(destination_suggestions))),
                    format_func=lambda i: destination_suggestions[i]["label"],
                    key="destination_choice", label_visibility="collapsed",
                )
                if st.button("\u2713 Gebruik deze bestemming",
                             key="apply_destination"):
                    sel2 = destination_suggestions[int(destination_selected)]
                    st.session_state["site_lat"] = float(sel2["lat"])
                    st.session_state["site_lon"] = float(sel2["lon"])
                    st.session_state["site_address"] = sel2["label"]
            if st.button("\U0001f4e1 Gebruik GPS", key="gps_site",
                         help="Detecteer huidige locatie via browser"):
                geo_data = get_geolocation()
                if geo_data and isinstance(geo_data, dict) and geo_data.get("coords"):
                    coords = geo_data["coords"]
                    try:
                        st.session_state["site_lat"] = float(coords["latitude"])
                        st.session_state["site_lon"] = float(coords["longitude"])
                        st.toast("\U0001f3c1 Werflocatie bijgewerkt via GPS")
                    except Exception:
                        st.warning("Kon browserlocatie niet lezen.")
                else:
                    st.info("Browser zal om locatietoestemming vragen.")

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
                "Herkomst \u2014 Centrale", "\U0001f4cd",
                str(st.session_state.get("plant_address", origin_query.strip())),
                origin_marker[0], origin_marker[1],
            )
        with sc2:
            _location_card(
                "Bestemming \u2014 Werf", "\U0001f3c1",
                str(st.session_state.get("site_address",
                                         destination_query.strip())),
                destination_marker[0], destination_marker[1],
            )

        st.markdown("")

        pin_mode = st.radio(
            "Klik op de kaart om in te stellen:",
            options=["\U0001f4cd Centrale (Herkomst)", "\U0001f3c1 Werf (Bestemming)"],
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
            if "Centrale" in pin_mode:
                st.session_state["plant_lat"] = lat_clicked
                st.session_state["plant_lon"] = lon_clicked
                st.toast("\U0001f4cd Centralepin bijgewerkt op kaart")
            else:
                st.session_state["site_lat"] = lat_clicked
                st.session_state["site_lon"] = lon_clicked
                st.toast("\U0001f3c1 Werfpin bijgewerkt op kaart")

        # Map legend
        _card(
            '<div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap;">'
            '<span style="font-size:0.8rem;">\U0001f535 <b>Herkomst</b> '
            '\u2014 Asfaltcentrale</span>'
            '<span style="font-size:0.8rem;">\U0001f534 <b>Bestemming</b> '
            '\u2014 Leveringswerf</span>'
            '<span style="font-size:0.8rem;">\u2500\u2500 <b>Rijroute</b></span>'
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
            st.text_input("Transporttype", value=transport_type, disabled=True)
        with d2:
            energy_source = st.selectbox("Energiebron",
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
                f"\U0001f6e3\ufe0f Rijafstand: **{distance_km:.1f} km** "
                f"(\u2248 {duration_min:.0f} min)"
            )
        else:
            distance_km = geo.haversine_km(plant_point, site_point)
            st.info(
                f"\U0001f4cf Hemelsbreed: **{distance_km:.1f} km** "
                f"(routeservice niet beschikbaar)"
            )

    # ════════════════════════════════════════════════════════════════════
    #  STEP C — Product, Compliance & Recipients
    # ════════════════════════════════════════════════════════════════════
    with st.expander(
        "\U0001f4e6  Stap 3 \u2014 Product, Conformiteit & Ontvangers",
        expanded=True,
    ):
        sub = st.tabs([
            "\U0001f3d7\ufe0f Levering op werf",
            "\U0001f9ea Product & Documenten",
            "\u2696\ufe0f Gewichten",
            "\U0001f4e7 Ontvangers",
        ])

        with sub[0]:
            _section_heading("\U0001f3d7\ufe0f", "Levering op werf",
                             "Opdrachtgever- en werfadresgegevens")
            client_address = st.text_area(
                "Adres opdrachtgever", placeholder="Voer het adres van de opdrachtgever in\u2026",
                key="k_client_address", height=100,
            )
            st.text_area(
                "Bestemming (werf) adres",
                value=st.session_state.get("site_address", ""),
                disabled=True, height=68,
            )
            tc1, tc2 = st.columns(2)
            with tc1:
                st.text_input(
                    "Vertrektijd",
                    value=st.session_state.get("_departure_time",
                                               "Automatisch bij vrijgave"),
                    disabled=True,
                )
            with tc2:
                st.text_input("Aankomsttijd", value="Automatisch bij ontvangst op werf",
                              disabled=True)

        with sub[1]:
            _section_heading("\U0001f9ea", "Product & Documenten",
                             "Mengselspecificaties en conformiteitsdocumentatie")
            p1, p2 = st.columns(2)
            with p1:
                product_mixture_type = st.text_input(
                    "Product / Mengseltype", key="k_product_mixture_type",
                    placeholder="bijv. AC 14 surf B50/70",
                )
                application = st.text_input(
                    "Toepassing", key="k_application",
                    placeholder="bijv. Toplaag",
                )
                certificate = st.text_input("Certificaat", key="k_certificate")
                declaration_of_performance = st.text_input(
                    "Prestatieverklaring",
                    key="k_declaration_of_performance",
                )
                technical_data_sheet = st.text_input(
                    "Technische Fiche", key="k_technical_data_sheet",
                )
            with p2:
                mechanical_resistance = st.text_input(
                    "Mechanische weerstand", key="k_mechanical_resistance",
                )
                fuel_resistance = st.text_input(
                    "Brandstofbestendigheid", key="k_fuel_resistance",
                )
                deicing_resistance = st.text_input(
                    "Dooizoutbestendigheid", key="k_deicing_resistance",
                )
                bitumen_aggregate_affinity = st.text_input(
                    "Bitumen\u2013aggregaat hechting",
                    key="k_bitumen_aggregate_affinity",
                )
                disposal = st.text_input("Verwijdering", key="k_disposal")

        with sub[2]:
            _section_heading("\u2696\ufe0f", "Gewichten",
                             "Bruto-, tarra- en nettohoeveelheden")
            w1, w2, w3 = st.columns(3)
            with w1:
                bruto_kg = st.number_input(
                    "Bruto (kg)", min_value=0.0, value=0.0, step=1.0,
                    key="k_bruto_kg",
                )
            with w2:
                tare_weight_empty_kg = st.number_input(
                    "Tarra gewicht \u2014 leeg (kg)", min_value=0.0, value=0.0,
                    step=1.0, key="k_tare_weight_empty_kg",
                )
            with w3:
                net_total_quantity_ton = st.number_input(
                    "Netto totaal (ton)", min_value=0.0, value=0.0, step=0.01,
                    key="k_net_total_quantity_ton",
                )
            if bruto_kg > 0 and tare_weight_empty_kg > 0:
                calculated_net = (bruto_kg - tare_weight_empty_kg) / 1000.0
                st.caption(f"Berekend netto: {calculated_net:.2f} ton")

        with sub[3]:
            _section_heading(
                "\U0001f4e7", "Ontvangers",
                "E-mailadressen voor automatische verzending van ondertekende documenten",
            )
            r1, r2 = st.columns(2)
            with r1:
                email_client = st.text_input(
                    "E-mail opdrachtgever", key="k_email_client",
                    placeholder="client@example.be",
                )
                email_transporter = st.text_input(
                    "E-mail vervoerder", key="k_email_transporter",
                    placeholder="transport@example.be",
                )
            with r2:
                email_copro = st.text_input(
                    "E-mail COPRO", key="k_email_copro",
                    placeholder="copro@example.eu",
                )
                email_permit_holder = st.text_input(
                    "E-mail vergunninghouder", key="k_email_permit_holder",
                    placeholder="vergunning@voorbeeld.be",
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
                st.caption(f"\u2709\ufe0f {recipient_count} ontvanger(s) geconfigureerd")

    # ════════════════════════════════════════════════════════════════════
    #  RELEASE — Summary + Action
    # ════════════════════════════════════════════════════════════════════
    EM_DASH = "—"
    WARNING_TEXT = "⚠️ Niet ingesteld"

    st.markdown("")
    _section_heading("\U0001f680", "Leveringsbon Vrijgeven",
                     "Bekijk de samenvatting en geef vrij")

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
        f"<b>DDN:</b> {ddn or WARNING_TEXT}",
        f"<b>Product:</b> {product_val or EM_DASH}",
        f"<b>Netto:</b> {net_val:.2f} ton" if net_val else f"<b>Netto:</b> {EM_DASH}",
        f"<b>Afstand:</b> {distance_km:.1f} km",
        f"<b>Ontvangers:</b> {recipient_count}",
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
        "\U0001f680  Vrijgeven bij Asfaltcentrale & Verzenden",
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
        st.error("\u274c Leveringsbonnummer is verplicht.")
        return

    existing = storage.get_note_by_delivery_note_no(delivery_note_no.strip())
    if existing:
        st.error("\u274c Er bestaat al een leveringsbon met dit nummer.")
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
    st.success("\u2705 Leveringsbon vrijgegeven! Vertrektijd geregistreerd.")

    _section_heading("\U0001f517", "Ondertekeningslinks",
                     "Deel deze links met elke partij om handtekeningen te verzamelen")
    for role, link in links.items():
        st.text_input(
            f"{ROLE_LABELS[role]} ondertekeningslink", value=link,
            key=f"_link_{role}", disabled=False,
            help="Kopieer deze link en stuur naar de partij",
        )

    _card(
        '<div style="font-size:0.9rem;color:#1e40af;">'
        '<b>\U0001f4f1 Werftoezichter:</b> Open de app, selecteer '
        '"Werftoezichter" modus, en voer Leveringsbonnummer in: '
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
                        f"Ondertekeningsverzoek leveringsbon "
                        f"({payload.get('delivery_note_no') or note_id})"
                    ),
                    body=(
                        "Gelieve de digitale leveringsbon te bekijken en "
                        "te ondertekenen via deze link:\n\n"
                        f"{link}\n\n"
                        "(Bij een lokale uitvoering, voeg uw Streamlit "
                        "basis-URL toe.)\n"
                    ),
                )
            st.info("\u2709\ufe0f Ondertekeningsverzoeken per e-mail verzonden naar alle partijen.")
        except Exception as e:
            st.warning(f"E-mailverzending mislukt: {e}")
    else:
        st.info("\u2139\ufe0f E-mail niet geconfigureerd \u2014 deel de links "
                "hierboven handmatig (prototype modus)")


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
            'Leveringsgegevens vloeien automatisch in het GPP-werkblad</div>'
            '</div>',
            padding="18px",
        )
    with gpp_c2:
        _card(
            '<div style="text-align:center;">'
            '<div style="font-size:1.8rem;margin-bottom:6px;">\U0001f4c8</div>'
            '<div style="font-size:0.85rem;font-weight:700;color:#1e293b;">'
            'Hoeveelheidsopvolging</div>'
            '<div style="font-size:0.78rem;color:#64748b;margin-top:4px;">'
            'Geleverde versus geplande tonnen worden per werkorder afgestemd</div>'
            '</div>',
            padding="18px",
        )
    with gpp_c3:
        _card(
            '<div style="text-align:center;">'
            '<div style="font-size:1.8rem;margin-bottom:6px;">\U0001f5d3\ufe0f</div>'
            '<div style="font-size:0.85rem;font-weight:700;color:#1e293b;">'
            'Projectplanning</div>'
            '<div style="font-size:0.78rem;color:#64748b;margin-top:4px;">'
            'Leveringsmijlpalen synchroniseren met het totale projectschema</div>'
            '</div>',
            padding="18px",
        )

    with st.expander("\U0001f50c GPP-verbindingsinstellingen (binnenkort beschikbaar)", expanded=False):
        st.text_input(
            "GPP Excel-bestandspad",
            value="",
            placeholder="bijv. C:/Projecten/GPP_werkorder_2026.xlsx",
            disabled=True,
            key="k_gpp_filepath",
            help="Pad naar het GPP Excel-werkboek",
        )
        st.text_input(
            "Werkorder / Projectcode",
            value="",
            placeholder="bijv. WO-2026-0145",
            disabled=True,
            key="k_gpp_workorder",
            help="GPP-werkorderreferentie om leveringen aan te koppelen",
        )
        st.selectbox(
            "Doelblad",
            options=["Leveringen", "Hoeveelheden", "Planning"],
            disabled=True,
            key="k_gpp_sheet",
            help="Welk blad in het GPP-werkboek moet worden ingevuld",
        )


# ═══════════════════════════════════════════════════════════════════════
#  Sub-page: Site Supervisor (Receive Delivery)
# ═══════════════════════════════════════════════════════════════════════


def _page_site_supervisor() -> None:
    """Site supervisor mode — receive a delivery."""

    if "arrival_registered" not in st.session_state:
        st.session_state.arrival_registered = False

    if "current_note_id" not in st.session_state:
        st.session_state.current_note_id = None

    _section_heading(
        "\U0001f3d7\ufe0f",
        "Levering Ontvangen",
        "Registreer aankomsttijd vrachtwagen voor een vrijgegeven leveringsbon",
    )

    _card(
        '<div style="font-size:0.85rem;color:#334155;">'
        'Voer het leveringsbonnummer in of selecteer het uit de vrijgegeven bonnen. '
        'Zodra de vrachtwagen aankomt, druk op de knop om de aankomsttijd '
        'te registreren.</div>',
        bg="#f0fdf4",
        border="1px solid #bbf7d0",
    )

    available = storage.list_delivery_note_nos(status="released", limit=200)

    dn = ""
    if available:
        dn = st.selectbox(
            "Selecteer een vrijgegeven leveringsbon",
            options=available,
            index=0,
        )
    else:
        st.info(
            "Nog geen vrijgegeven leveringsbonnen gevonden. "
            "Wachten tot de centrale een bon vrijgeeft."
        )

    manual = st.text_input(
        "Of voer leveringsbonnummer handmatig in",
        value="",
        placeholder="bijv. DDN-2026-00142",
    )

    if manual.strip():
        dn = manual.strip()

    st.markdown("")

    # ---------------------------------------------------------
    # ARRIVAL REGISTRATION
    # ---------------------------------------------------------

    if st.button(
        "\U0001f69b  Vrachtwagen Ontvangen — Aankomsttijd Registreren",
        type="primary",
        use_container_width=True,
        key="arrival_btn",
    ):

        if not dn.strip():
            st.error("Voer het leveringsbonnummer in.")
            st.stop()

        note = storage.get_note_by_delivery_note_no(dn.strip())

        if not note:
            st.error("Geen leveringsbon gevonden voor dit nummer.")
            st.stop()

        if note.get("status") == "pending":
            st.error(
                "Deze leveringsbon is nog niet vrijgegeven bij de asfaltcentrale."
            )
            st.stop()

        payload = note["payload"]

        now = datetime.now()
        payload["arrival_time"] = now.strftime("%H:%M")
        payload["arrival_time_iso"] = now.isoformat(timespec="seconds")

        with storage.get_conn() as conn:
            import json

            conn.execute(
                "UPDATE delivery_notes SET payload_json=?, status=? WHERE id=?",
                (
                    json.dumps(payload, ensure_ascii=False),
                    "received",
                    note["id"],
                ),
            )

        st.session_state.arrival_registered = True
        st.session_state.current_note_id = note["id"]

    # ---------------------------------------------------------
    # POST-ARRIVAL PANEL
    # ---------------------------------------------------------

    if st.session_state.arrival_registered:

        note = storage.get_note(st.session_state.current_note_id)
        payload = note["payload"]

        st.success(
            f"Aankomst geregistreerd om **{payload['arrival_time']}**."
        )

        sigs = storage.list_signatures(note["id"])

        xlsx_bytes = excel_export.build_delivery_note_xlsx(payload, sigs)

        st.markdown("")

        # -----------------------------------------------------
        # GPP PUSH
        # -----------------------------------------------------

        if st.button(
            "Verstuur naar GPP",
            key="push_gpp",
            use_container_width=True,
        ):

            try:

                with st.spinner("Versturen naar GPP..."):

                    result = gpp_integration.push_to_gpp(
                        payload,
                        sigs,
                        log_func=st.text,
                    )

                # Extract path from result string
                path_line = result.split("Saved to:")[-1].strip()
                gpp_file_path = Path(path_line)

                if gpp_file_path.exists():
                    st.session_state.gpp_xlsx_bytes = gpp_file_path.read_bytes()
                else:
                    st.warning("GPP Excel bestand niet gevonden.")

                st.success(
                    f"Leveringsbon succesvol naar GPP verstuurd.\n{result}"
                )

            except NotImplementedError:
                st.warning("GPP push is nog niet geïmplementeerd.")

            except Exception as e:
                st.error(f"GPP verzending mislukt: {e}")

        # -----------------------------------------------------
        # EMAIL
        # -----------------------------------------------------

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
                        f"DDN (aankomst geregistreerd) "
                        f"({payload.get('delivery_note_no') or note['id']})"
                    ),
                    body=(
                        "Aankomsttijd is geregistreerd door de "
                        "werftoezichter. De Digitale Leveringsbon "
                        "(Excel) is bijgevoegd."
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
                st.warning(f"E-mailverzending mislukt: {e}")

        st.balloons()
        if emailed:
            storage.mark_completed(note["id"])
            st.success(
                f"Excel gegenereerd en verzonden per e-mail."
            )
            _card(
                '<div style="font-size:0.85rem;color:#166534;">'
                f'<b>Verzonden naar:</b> {", ".join(emails)}</div>',
                bg="#f0fdf4",
                border="1px solid #bbf7d0",
            )
        else:
            if emails and not mailer.email_enabled():
                st.info(
                    "E-mail niet geconfigureerd; Excel gegenereerd om te downloaden."
                )

            elif not emails:

                st.info(
                    "Geen e-mailadressen opgegeven; Excel gegenereerd om te downloaden."
                )

        # -----------------------------------------------------
        # EXCEL DOWNLOAD
        # -----------------------------------------------------

        download_bytes = st.session_state.get("gpp_xlsx_bytes", None) or xlsx_bytes

        st.download_button(
            label="\U0001f4e5 Download Excel (xlsx)",
            data=download_bytes,
            file_name=_safe_filename(note["id"]),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

        st.markdown("")

        # -----------------------------------------------------
        # RESET FOR NEXT DELIVERY
        # -----------------------------------------------------

        if st.button("Nieuwe levering verwerken", key="reset_page"):

            st.session_state.arrival_registered = False
            st.session_state.current_note_id = None

            st.rerun()


# ═══════════════════════════════════════════════════════════════════════
#  Page: Sign delivery note
# ═══════════════════════════════════════════════════════════════════════


def page_sign(note_id: str, role: str) -> None:
    label = ROLE_LABELS.get(role, role)

    if f"signed_{note_id}_{role}" not in st.session_state:
        st.session_state[f"signed_{note_id}_{role}"] = False

    note = storage.get_note(note_id)
    if not note:
        st.error("Onbekende leveringsbon.")
        return

    payload = note["payload"]
    sigs = storage.list_signatures(note_id)
    signed_count = sum(1 for r in ROLE_LABELS if r in sigs)
    total = len(ROLE_LABELS)

    # Status badge
    if role in sigs or st.session_state[f"signed_{note_id}_{role}"]:
        st.markdown(
            '<div style="display:inline-block;padding:4px 16px;'
            'background:#dcfce7;color:#166534;border-radius:999px;'
            'font-size:0.82rem;font-weight:600;margin-bottom:12px;">'
            f'\u2705 Ondertekend als {label}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="display:inline-block;padding:4px 16px;'
            'background:#fef3c7;color:#92400e;border-radius:999px;'
            'font-size:0.82rem;font-weight:600;margin-bottom:12px;">'
            f'\u270d\ufe0f Wacht op handtekening \u2014 {label}</div>',
            unsafe_allow_html=True,
        )

    _section_heading("\u270d\ufe0f", f"Ondertekenen als {label}")

    # Signing progress
    st.progress(signed_count / total,
                text=f"Handtekeningen: {signed_count} / {total}")

    # Compact summary card
    EM_DASH = "—"
    with st.expander("Samenvatting leveringsbon", expanded=False):
        s1, s2 = st.columns(2)
        with s1:
            st.markdown(f"**Datum:** {payload.get('date', EM_DASH)}")
            st.markdown(f"**DDN:** {payload.get('delivery_note_no', EM_DASH)}")
            st.markdown(f"**Centrale:** {payload.get('plant_address', EM_DASH)}")
            st.markdown(f"**Werf:** {payload.get('site_address', EM_DASH)}")
            st.markdown(f"**Transport:** {payload.get('transport_company', EM_DASH)}")
        with s2:
            st.markdown(f"**Nummerplaat:** {payload.get('license_plate', EM_DASH)}")
            st.markdown(f"**Vertrek:** {payload.get('departure_time', EM_DASH)}")
            st.markdown(f"**Aankomst:** {payload.get('arrival_time', EM_DASH)}")
            st.markdown(f"**Product:** {payload.get('product_mixture_type', EM_DASH)}")
            st.markdown(f"**Netto hvh:** {payload.get('net_total_quantity_ton', EM_DASH)} ton")

    # Signature canvas
    st.markdown("")
    _section_heading("\U0001f58a\ufe0f", "Teken uw handtekening",
                     "Gebruik uw muis of vinger om hieronder te tekenen")

    signer_name = st.text_input("Uw volledige naam",
                                placeholder="bijv. Jan De Smet")

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

    # --- Submit signature button ---
    if st.button("\u2705  Handtekening Indienen", type="primary",
                 use_container_width=True, key=f"submit_sig_{note_id}_{role}"):

        if canvas.image_data is None:
            st.error("Geen handtekening vastgelegd. Teken uw handtekening hierboven.")
        else:
            img = Image.fromarray(canvas.image_data.astype("uint8"))
            sig_path = storage.SIGNATURES_DIR / f"{note_id}_{role}.png"
            img.save(sig_path)

            storage.upsert_signature(note_id, role,
                                     signer_name.strip() or None, str(sig_path))

            st.session_state[f"signed_{note_id}_{role}"] = True
            st.balloons()
            st.success(f"\u2705 Handtekening opgeslagen voor {label}!")

            # Refresh signature list and count
            sigs = storage.list_signatures(note_id)
            signed_count = sum(1 for r in ROLE_LABELS if r in sigs)

    # --- Signing status ---
    st.markdown("")
    _section_heading("\U0001f4ca", "Ondertekeningsstatus")
    for r in ROLE_LABELS:
        icon = "\u2705" if r in sigs or st.session_state.get(f"signed_{note_id}_{r}", False) else "\u23f3"
        st.markdown(
            f"{icon} **{ROLE_LABELS[r]}** — "
            f"{'Ondertekend' if r in sigs or st.session_state.get(f'signed_{note_id}_{r}', False) else 'In afwachting'}"
        )

    # --- Fully signed ---
    if storage.is_fully_signed(note_id):
        st.markdown("")
        st.markdown(
            '<div style="text-align:center;padding:20px;'
            'background:linear-gradient(135deg,#dcfce7,#bbf7d0);'
            'border-radius:12px;margin:8px 0;">'
            '<div style="font-size:2rem;">\U0001f389</div>'
            '<div style="font-size:1.1rem;font-weight:700;color:#166534;">'
            'Alle partijen hebben ondertekend!</div>'
            '<div style="font-size:0.85rem;color:#15803d;">'
            'De leveringsbon is compleet. Download het Excel-rapport '
            'hieronder.</div></div>',
            unsafe_allow_html=True,
        )

        # Reload latest payload & signatures so the Excel reflects any
        # updates made after this page was first opened (e.g. arrival_time
        # set by the site supervisor).
        fresh_note = storage.get_note(note_id)
        if fresh_note:
            payload = fresh_note["payload"]
        sigs = storage.list_signatures(note_id)

        data_dir = Path(__file__).resolve().parent / "data" / "exports"
        out_path = data_dir / _safe_filename(note_id)
        xlsx_bytes = excel_export.build_delivery_note_xlsx(payload, sigs, output_path=out_path)

        st.markdown("")
        if st.button("Verstuur aankomst naar GPP", use_container_width=True, key=f"push_gpp_{note_id}"):
            try:
                with st.spinner("Versturen naar GPP..."):
                    gpp_integration.push_to_gpp(payload, sigs, log_func=st.text)
                st.success("Aankomst succesvol naar GPP verstuurd.")
            except NotImplementedError:
                st.warning("GPP push is nog niet geïmplementeerd.")
            except Exception as e:
                st.error(f"GPP verzending mislukt: {e}")

        st.download_button(
            label="\U0001f4e5 Download Ondertekende Excel (xlsx)",
            data=xlsx_bytes,
            file_name=_safe_filename(note_id),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

        if mailer.email_enabled():
            if st.button("\U0001f4e7 Definitieve Excel naar alle partijen e-mailen",
                         use_container_width=True, key=f"email_final_{note_id}"):
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
                        subject=(f"Definitieve leveringsbon (ondertekend) "
                                 f"({payload.get('delivery_note_no') or note_id})"),
                        body="Alle partijen hebben ondertekend. De definitieve Excel is bijgevoegd.",
                        attachments=[(
                            _safe_filename(note_id), xlsx_bytes,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )],
                    )
                    st.info("\u2709\ufe0f Definitieve Excel verzonden naar alle partijen.")
                except Exception as e:
                    st.warning(f"E-mail mislukt: {e}")


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
        'Vervang papieren leveringsbonnen door een volledig digitale workflow \u2014 '
        'van vrijgave bij de asfaltcentrale tot ontvangst op de werf en ondertekening door meerdere partijen.'
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
            'Realtime tracking</div>'
            '<div style="font-size:0.78rem;color:#64748b;">'
            'Vertrek- & aankomsttijden worden automatisch geregistreerd</div></div>',
            padding="16px",
        )
    with b2:
        _card(
            '<div style="text-align:center;">'
            '<div style="font-size:1.5rem;">\u270d\ufe0f</div>'
            '<div style="font-size:0.88rem;font-weight:600;color:#1e293b;">'
            'Digitale handtekeningen</div>'
            '<div style="font-size:0.78rem;color:#64748b;">'
            'Opdrachtgever, Vervoerder, COPRO & Vergunninghouder tekenen online'
            '</div></div>',
            padding="16px",
        )
    with b3:
        _card(
            '<div style="text-align:center;">'
            '<div style="font-size:1.5rem;">\U0001f4ca</div>'
            '<div style="font-size:0.88rem;font-weight:600;color:#1e293b;">'
            'Automatische Excel-export</div>'
            '<div style="font-size:0.78rem;color:#64748b;">'
            'Volledig ondertekend rapport wordt automatisch gegenereerd & per e-mail verzonden'
            '</div></div>',
            padding="16px",
        )

    st.markdown("")

    # Workflow steps
    _section_heading("\U0001f504", "Hoe het werkt")
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
        page_title="Digitale Leveringsbon \u2014 DIMinfr@",
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

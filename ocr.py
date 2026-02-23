"""
ocr.py — OCR scanning and field extraction for Digital Delivery Notes.

Extracts text from uploaded images (PNG, JPG, TIFF, BMP) and PDFs,
then maps recognised values to delivery-note form fields using
pattern matching.

Dependencies:
    pip install pytesseract Pillow PyMuPDF

Tesseract must also be installed on the system:
    - Windows: https://github.com/UB-Mannheim/tesseract/wiki
    - Linux  : sudo apt install tesseract-ocr
    - macOS  : brew install tesseract
"""

from __future__ import annotations

import io
import os
import re
import shutil
from pathlib import Path
from typing import Any, BinaryIO

from PIL import Image, ImageEnhance, ImageFilter

# ---------------------------------------------------------------------------
#  Optional dependency flags
# ---------------------------------------------------------------------------

_HAS_TESSERACT = False
try:
    import pytesseract  # type: ignore
    # On Windows, help pytesseract find the Tesseract binary
    if not shutil.which("tesseract"):
        _win_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.path.isfile(_win_path):
            pytesseract.pytesseract.tesseract_cmd = _win_path
    _HAS_TESSERACT = True
except ImportError:
    pass

_HAS_FITZ = False
try:
    import fitz  # PyMuPDF  # type: ignore
    _HAS_FITZ = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
#  Low-level OCR helpers
# ---------------------------------------------------------------------------


def _preprocess_image(image: Image.Image) -> Image.Image:
    """Apply preprocessing steps to improve OCR accuracy.

    Steps:
      1. Convert to grayscale
      2. Upscale small images (< 1500px wide) for better character detection
      3. Enhance contrast
      4. Sharpen
      5. Apply adaptive-style binarisation via a high-contrast threshold
    """
    # 1. Grayscale
    gray = image.convert("L")

    # 2. Upscale small images
    w, h = gray.size
    if w < 1500:
        scale = 1500 / w
        gray = gray.resize(
            (int(w * scale), int(h * scale)),
            Image.Resampling.LANCZOS,
        )

    # 3. Contrast enhancement
    gray = ImageEnhance.Contrast(gray).enhance(1.8)

    # 4. Sharpen
    gray = gray.filter(ImageFilter.SHARPEN)

    # 5. Binarise – use a high-pass point transform to clean noise
    threshold = 160
    gray = gray.point(lambda px: 255 if int(px) > threshold else 0, "L")

    return gray


def ocr_image(image: Image.Image, lang: str = "eng+nld") -> str:
    """Run Tesseract OCR on a PIL Image and return the raw text."""
    if not _HAS_TESSERACT:
        raise RuntimeError(
            "pytesseract is not installed. "
            "Run: pip install pytesseract  "
            "and install Tesseract: "
            "https://github.com/UB-Mannheim/tesseract/wiki"
        )
    processed = _preprocess_image(image)
    # Use Tesseract with page segmentation mode 3 (fully automatic)
    custom_config = r"--oem 3 --psm 3"
    text: str = pytesseract.image_to_string(
        processed, lang=lang, config=custom_config,
    )
    return text


def ocr_pdf(file_bytes: bytes, lang: str = "eng+nld") -> str:
    """Extract text from a PDF.

    Strategy:
      1. Try native text extraction with PyMuPDF (fast, no OCR needed).
      2. If the PDF is image-based (very little text), render each page
         to an image and run Tesseract OCR.
    """
    if not _HAS_FITZ:
        raise RuntimeError(
            "PyMuPDF is not installed. Run: pip install PyMuPDF"
        )

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages_text: list[str] = []

    for page in doc:
        text = page.get_text("text")
        if not isinstance(text, str):
            text = str(text) if text is not None else ""
        if len(text.strip()) > 30:
            # Page has embedded / selectable text
            pages_text.append(text)
        else:
            # Image-based page → render and OCR
            if not _HAS_TESSERACT:
                pages_text.append(text)
                continue
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            pages_text.append(ocr_image(img, lang=lang))

    doc.close()
    return "\n\n".join(pages_text)


# ---------------------------------------------------------------------------
#  Scanning entry point
# ---------------------------------------------------------------------------


def extract_text(uploaded_file: BinaryIO, content_type: str | None = None,
                 filename: str | None = None, lang: str = "eng+nld") -> str:
    """Read an uploaded file and return OCR / extracted text.

    Parameters
    ----------
    uploaded_file : file-like
        Streamlit ``UploadedFile`` or any binary stream.
    content_type : str | None
        MIME type (e.g. ``"image/png"``, ``"application/pdf"``).
    filename : str | None
        Original filename — used as fallback to detect type.
    lang : str
        Tesseract language string (default ``"eng+nld"`` for
        English + Dutch).

    Returns
    -------
    str
        The extracted raw text.
    """
    raw = uploaded_file.read()
    ct = (content_type or "").lower()
    fn = (filename or "").lower()

    is_pdf = "pdf" in ct or fn.endswith(".pdf")

    if is_pdf:
        return ocr_pdf(raw, lang=lang)

    # Assume image
    img = Image.open(io.BytesIO(raw))
    return ocr_image(img, lang=lang)


# ---------------------------------------------------------------------------
#  Field extraction — pattern-based mapping
# ---------------------------------------------------------------------------

# Human-readable labels for each session-state key (Dutch).
FIELD_LABELS: dict[str, str] = {
    # ── 1. Document Header ──────────────────────────────────────────────
    "k_company_name":           "Bedrijfsnaam",
    "k_company_branch":         "Vestiging / Afdeling",
    "k_company_address":        "Bedrijfsadres",
    "k_company_postal":         "Postcode bedrijf",
    "k_company_city":           "Stad bedrijf",
    "k_company_tel_bridge":     "Tel. centrale",
    "k_company_tel_orders":     "Tel. bestellingen",
    "k_ce_number":              "CE-markeringsnummer",
    # ── 2. Delivery Identification ──────────────────────────────────────
    "k_document_type":          "Documenttype (Afvoer/Aanvoer)",
    "k_delivery_note_no":       "Leveringsbonnummer / Afvoernummer",
    "k_document_number":        "Documentnummer",
    "k_document_serial":        "Volgnummer",
    "k_ticket_number":          "Ticketnummer",
    # ── 3. Weights ──────────────────────────────────────────────────────
    "k_bruto_kg":               "Bruto (kg)",
    "k_tare_weight_empty_kg":   "Tarra gewicht (kg)",
    "k_net_total_quantity_ton": "Nettohoeveelheid (kg)",
    "k_total_kg":               "Totaal (kg)",
    # ── 4. Product Information ──────────────────────────────────────────
    "k_product_mixture_type":   "Product / Mengseltype",
    "k_asphalt_layer_type":     "Laagtype (bijv. onderlaag)",
    "k_grain_size":             "Korrelgrootte",
    "k_standard_ref":           "Normeringsreferentie (PTV / EN)",
    "k_asphalt_class":          "Klasse (bijv. klasse OE)",
    "k_certificate":            "Certificaat",
    "k_declaration_of_performance": "Prestatieverklaring (DoP)",
    "k_technical_data_sheet":   "Technische Fiche / Snelcode",
    "k_additives":              "Toevoegsels",
    # ── 5. Application ──────────────────────────────────────────────────
    "k_application":            "Toepassing",
    "k_mechanical_resistance":  "Mechanische weerstand",
    "k_fuel_resistance":        "Weerstand tegen brandstof",
    "k_deicing_resistance":     "Weerstand tegen ontdooiing",
    "k_bitumen_aggregate_affinity": "Affiniteit bitumen-aggregaat",
    # ── 6. Project / Client ─────────────────────────────────────────────
    "k_werf_client":            "Werf / Klant",
    "k_werf_number":            "Werfnummer / Projectnummer",
    "k_address":                "Adres",
    "k_site_address":           "Werfadres",
    "k_site_street":            "Straat werf",
    "k_site_postal":            "Postcode werf",
    "k_site_city":              "Stad werf",
    # ── 7. Logistics ────────────────────────────────────────────────────
    "k_date":                   "Datum",
    "k_time":                   "Uur",
    "k_transport_company":      "Transportbedrijf / Vervoerder",
    "k_license_plate":          "Nummerplaat",
    "k_driver_name":            "Naam chauffeur",
    # ── 8. Signatures & Authorization ───────────────────────────────────
    "k_copro_ref":              "COPRO-referentie",
    # ── 9. Footer ───────────────────────────────────────────────────────
    "k_company_email":          "E-mail bedrijf",
    "k_company_website":        "Website bedrijf",
    # ── Extra fields for broader compatibility ──────────────────────────
    "k_client_address":         "Adres opdrachtgever",
    "k_disposal":               "Verwijdering",
    "k_origin_query":           "Herkomst (centrale)",
    "k_destination_query":      "Bestemming (werf)",
    "k_email_client":           "E-mail opdrachtgever",
    "k_email_transporter":      "E-mail vervoerder",
    "k_email_copro":            "E-mail COPRO",
    "k_email_permit_holder":    "E-mail vergunninghouder",
}

# ---------------------------------------------------------------------------
# Regex patterns aligned to the delivery note structure
# ---------------------------------------------------------------------------

_FIELD_PATTERNS: list[tuple[str, list[re.Pattern[str]]]] = [

    # ── 1. Document Header ───────────────────────────────────────────────
    ("k_company_name", [
        re.compile(r"^([A-Z][A-Za-z0-9 &.,\-']{4,}(?:\s+NV|\s+BV|\s+SA|\s+BVBA)?)\s*$", re.MULTILINE),
    ]),
    ("k_company_address", [
        re.compile(r"(?:([A-Za-z][A-Za-z0-9 ]{3,}),\s*([0-9]{4})\s+([A-Za-z ]+))", re.IGNORECASE),
    ]),
    ("k_ce_number", [
        re.compile(r"CE\s*[-–]?\s*([0-9]{3,})", re.IGNORECASE),
        re.compile(r"(?:conformit[eé]|marking)[^0-9]*([0-9]{3,})", re.IGNORECASE),
    ]),

    # ── 2. Delivery Identification ──────────────────────────────────────
    ("k_document_type", [
        re.compile(r"\b(Afvoer|Aanvoer)\b", re.IGNORECASE),
    ]),
    ("k_delivery_note_no", [
        re.compile(r"(?:Afvoer|Aanvoer)\s*:?\s+([0-9]{4,})", re.IGNORECASE),
        re.compile(r"(?:lever(?:ings?)?bon|DDN|bon\s*(?:nr|n\xb0|nummer))\s*:?\s*([A-Z0-9\-/]{3,})", re.IGNORECASE),
    ]),
    ("k_document_number", [
        re.compile(r"Nr\.?\s+([0-9]{3,}\.?[0-9]*)", re.IGNORECASE),
        re.compile(r"Documentnummer\s*:?\s*([0-9]{3,}\.?[0-9]*)", re.IGNORECASE),
    ]),
    ("k_document_serial", [
        re.compile(r"\b([0-9]{6,7})\b"),
    ]),
    ("k_ticket_number", [
        re.compile(r"(?:ticket|bon)\s*(?:nr|n\xb0)?\s*:?\s*([A-Z0-9]{4,})", re.IGNORECASE),
    ]),

    # ── 3. Weights ───────────────────────────────────────────────────────
    # Extract ONLY the first number on each weight line (ignore trailer numbers like Pt refs)
    ("k_bruto_kg", [
        re.compile(r"Bruto\s*:?\s*([0-9]+[.,][0-9]+)", re.IGNORECASE),
        re.compile(r"Bruto[^\d\n]*([0-9]+[.,][0-9]+)", re.IGNORECASE),
        re.compile(r"Bruto[^\d\n]*([0-9]+)", re.IGNORECASE),
    ]),
    ("k_tare_weight_empty_kg", [
        re.compile(r"Tarra\s*:?\s*([0-9]+[.,][0-9]+)", re.IGNORECASE),
        re.compile(r"Tarra[^\d\n]*([0-9]+[.,][0-9]+)", re.IGNORECASE),
        re.compile(r"Tarra[^\d\n]*([0-9]+)", re.IGNORECASE),
    ]),
    ("k_net_total_quantity_ton", [
        re.compile(r"Nettohoeveelheid\s*:?\s*([0-9]+[.,][0-9]+)", re.IGNORECASE),
        re.compile(r"Nettohoeveelheid[^\d\n]*([0-9]+[.,][0-9]+)", re.IGNORECASE),
        re.compile(r"Nettohoeveelheid[^\d\n]*([0-9]+)", re.IGNORECASE),
    ]),
    ("k_total_kg", [
        re.compile(r"Totaal\s*:?\s*([0-9]+[.,][0-9]+)", re.IGNORECASE),
        re.compile(r"Totaal[^\d\n]*([0-9]+[.,][0-9]+)", re.IGNORECASE),
        re.compile(r"Totaal[^\d\n]*([0-9]+)", re.IGNORECASE),
    ]),

    # ── 4. Product Information ───────────────────────────────────────────
    ("k_product_mixture_type", [
        # Full product line: "AC 20 onderlaag 50/70"
        re.compile(r"Product\s*:?\s*([A-Z]{2}\s*\d{1,2}[^\n]{2,80})", re.IGNORECASE),
        re.compile(r"\b(AC\s*\d{1,2}\s+\w[^\n]{3,80})", re.IGNORECASE),
        re.compile(r"\b((?:SMA|PA|ZOAB|ABb|EME)\s*\d{0,2}\s*\w[^\n]{2,80})", re.IGNORECASE),
    ]),
    ("k_asphalt_layer_type", [
        re.compile(r"\b(onderlaag|tussenlaag|deklaag|toplaag|slijtlaag|bindlaag)\b", re.IGNORECASE),
    ]),
    ("k_grain_size", [
        re.compile(r"\b([0-9]+\s*/\s*[0-9]+)\s*(?:mm)?\b"),
    ]),
    ("k_standard_ref", [
        re.compile(r"\b((?:PTV|EN|NBN)\s*[0-9][\w\-/.]{2,20})\b", re.IGNORECASE),
    ]),
    ("k_asphalt_class", [
        re.compile(r"klasse\s+([A-Z0-9]{1,6}(?:\s+\w{1,10})?)", re.IGNORECASE),
    ]),
    ("k_certificate", [
        re.compile(r"Certificaat\s*:?\s*([^\n]{4,60})", re.IGNORECASE),
        re.compile(r"(?:certifica(?:te|at))\s*[\s:.\-]*([A-Z0-9][\w\-/. ]{4,40})", re.IGNORECASE),
    ]),
    ("k_declaration_of_performance", [
        re.compile(r"Prestatieverklaring\s*:?\s*([^\n]{4,80})", re.IGNORECASE),
        re.compile(r"\b(DoP\s*[0-9][\w\-/.]{4,40})\b", re.IGNORECASE),
    ]),
    ("k_technical_data_sheet", [
        re.compile(r"Technische\s*fiche\s*:?\s*([^\n]{4,80})", re.IGNORECASE),
        re.compile(r"snelcode\s*:?\s*([0-9][\w/.\-]{3,20})", re.IGNORECASE),
        re.compile(r"(?:TDS|technische\s*fiche)\s*[\s:.\-]*([A-Z0-9][\w\-/.]{4,40})", re.IGNORECASE),
    ]),
    ("k_additives", [
        re.compile(r"Toevoegsels\s*:?\s*([A-Za-z0-9 ,.'\-]{1,80})", re.IGNORECASE),
    ]),

    # ── 5. Application ───────────────────────────────────────────────────
    ("k_application", [
        re.compile(r"Toepassing\s*:?\s*([A-Za-z][^\n]{5,100})", re.IGNORECASE),
        re.compile(r"(?:application|gebruik)\s*[\s:.\-]*([A-Za-z][^\n]{5,80})", re.IGNORECASE),
    ]),
    ("k_mechanical_resistance", [
        re.compile(r"Mechanische\s*weerstand\s*:?\s*(NPD|[A-Za-z0-9][^\n]{1,40})", re.IGNORECASE),
    ]),
    ("k_fuel_resistance", [
        re.compile(r"Weerstand\s*tegen\s*brandstof\s*:?\s*(NPD|[A-Za-z0-9][^\n]{1,40})", re.IGNORECASE),
    ]),
    ("k_deicing_resistance", [
        re.compile(r"Weerstand\s*tegen\s*ontdooiing\s*:?\s*(NPD|[A-Za-z0-9][^\n]{1,40})", re.IGNORECASE),
    ]),
    ("k_bitumen_aggregate_affinity", [
        re.compile(r"Affiniteit\s*bitumen-aggregaat\s*:?\s*(NPD|[A-Za-z0-9][^\n]{1,40})", re.IGNORECASE),
        re.compile(r"bitumen[\s\-]*aggregaat\s*:?\s*(NPD|[A-Za-z0-9][^\n]{1,40})", re.IGNORECASE),
    ]),

    # ── 6. Project / Client ──────────────────────────────────────────────
    ("k_werf_client", [
        # Capture company name before the project code on the same line
        re.compile(r"Werf\s*/\s*Klant\s*:?\s*([A-Za-z][^\t\n]{3,60}?)(?:\s{3,}|\t)", re.IGNORECASE),
        re.compile(r"Werf\s*/\s*Klant\s*:?\s*([A-Za-z][^\n]{3,80})", re.IGNORECASE),
    ]),
    ("k_werf_number", [
        # Capture project code: WIN00291 - Kerncentrale Doel
        re.compile(r"\b(W[A-Z0-9]{4,8}\s*[-–]\s*[A-Za-z][^\n]{3,60})", re.IGNORECASE),
        re.compile(r"\b(WM[0-9]{5,})\b", re.IGNORECASE),
        re.compile(r"(?:Werf|Project)\s*(?:nr|nummer)\s*:?\s*([A-Za-z0-9\-]{3,30})", re.IGNORECASE),
    ]),
    ("k_address", [
        # Street + city on next line: "Klinkaardstraat 198\n2950 Kapellen"
        re.compile(r"Adres\s*:?\s*([A-Za-z][^\n]{5,80})", re.IGNORECASE),
    ]),
    ("k_site_street", [
        re.compile(r"([A-Za-z][a-z]+(?:straat|laan|weg|dreef|plein|steenweg|dijk)[^\n]{0,30})", re.IGNORECASE),
    ]),
    ("k_site_postal", [
        re.compile(r"\b([0-9]{4})\s+[A-Za-z]"),
    ]),
    ("k_site_city", [
        re.compile(r"\b[0-9]{4}\s+([A-Za-z][a-z]{2,})\b"),
    ]),
    ("k_site_address", [
        re.compile(r"Werfadres\s*:?\s*([^\n]{5,80})", re.IGNORECASE),
        re.compile(r"((?:[0-9]+)?Kerncentrale[^\n]{0,40})", re.IGNORECASE),
    ]),

    # ── 7. Logistics ─────────────────────────────────────────────────────
    ("k_date", [
        re.compile(r"Datum\s*:?\s*([0-9]{2}[-./][0-9]{2}[-./][0-9]{4})"),
        re.compile(r"\b([0-9]{2}[-./][0-9]{2}[-./][0-9]{4})\b"),
    ]),
    ("k_time", [
        re.compile(r"Uur\s*:?\s*([0-9]{1,2}:[0-9]{2})"),
        re.compile(r"\b([0-9]{1,2}:[0-9]{2})\b"),
    ]),
    ("k_transport_company", [
        re.compile(r"Vervoerder\s*:?\s*([A-Za-z][A-Za-z0-9 &.,\-']{3,50})", re.IGNORECASE),
        re.compile(r"(?:transporteur|transport\s*(?:company|bedrijf|firma))\s*:?\s*([A-Za-z][A-Za-z0-9 &.,\-']{3,50})", re.IGNORECASE),
    ]),
    ("k_license_plate", [
        # Belgian new: 1-ABC-234
        re.compile(r"\b(\d-[A-Z]{3}-\d{3})\b"),
        # Belgian alternate digits: 1VvGW775, 1WGW775
        re.compile(r"Nummerplaat\s*:?\s*([A-Z0-9]{6,10})", re.IGNORECASE),
        # Belgian old: ABC-123
        re.compile(r"\b([A-Z]{3}-\d{3})\b"),
        re.compile(r"(?:kenteken|nummerplaat|plaat|registratie)[\s:.\-]*([A-Z0-9][\w\- ]{3,10})", re.IGNORECASE),
    ]),
    ("k_driver_name", [
        re.compile(r"(?:chauffeur|bestuurder|driver)\s*:?\s*([A-Za-z][A-Za-z .\-']{3,50})", re.IGNORECASE),
    ]),

    # ── 8. Signatures & Authorization ────────────────────────────────────
    ("k_copro_ref", [
        re.compile(r"(?:COPRO|copro)\s*[-–:]\s*([A-Z0-9][\w/.\-]{3,30})", re.IGNORECASE),
    ]),

    # ── 9. Footer ────────────────────────────────────────────────────────
    ("k_company_email", [
        re.compile(r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"),
    ]),
    ("k_company_website", [
        re.compile(r"(?:www\.|https?://)([\w.\-/]{4,60})", re.IGNORECASE),
    ]),

    # ── Extra: origin/destination/client for routing ──────────────────
    ("k_client_address", [
        re.compile(r"(?:opdrachtgever|bouwheer)[\s:.\-]*\n?((?:[A-Za-z0-9].+\n?){1,5})", re.IGNORECASE),
    ]),
    ("k_origin_query", [
        re.compile(r"(?:herkomst|centrale|plant|fabriek)[\s:.\-]*([A-Za-z][^\n]{5,80})", re.IGNORECASE),
    ]),
    ("k_destination_query", [
        re.compile(r"(?:levering\s*adres|leveringsadres|aflever\s*adres)[\s:.\-]*([A-Za-z0-9][^\n]{5,80})", re.IGNORECASE),
    ]),
    ("k_disposal", [
        re.compile(r"(?:disposal|afval|verwijdering)[\s:.\-]*([^\n]{4,60})", re.IGNORECASE),
    ]),
]

# Email patterns (role-specific)
_EMAIL_FIELD_PATTERNS: list[tuple[str, list[re.Pattern[str]]]] = [
    ("k_email_client", [
        re.compile(
            r"(?:client|opdrachtgever)\s*e?-?mail[\s:.\-]*"
            r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
            re.IGNORECASE,
        ),
    ]),
    ("k_email_transporter", [
        re.compile(
            r"(?:transport(?:eur|er)?)\s*e?-?mail[\s:.\-]*"
            r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
            re.IGNORECASE,
        ),
    ]),
    ("k_email_copro", [
        re.compile(
            r"(?:copro|inspectie)\s*e?-?mail[\s:.\-]*"
            r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
            re.IGNORECASE,
        ),
    ]),
    ("k_email_permit_holder", [
        re.compile(
            r"(?:permit\s*holder|vergunning(?:houder)?)\s*e?-?mail[\s:.\-]*"
            r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
            re.IGNORECASE,
        ),
    ]),
]

# Generic email fallback — pick up any email addresses in the document
_GENERIC_EMAIL_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"
)

# Keys whose values must be parsed as floats
_NUMERIC_KEYS = frozenset({
    "k_bruto_kg", "k_tare_weight_empty_kg", "k_net_total_quantity_ton", "k_total_kg",
})


def _parse_number(s: str) -> float:
    """Convert a Dutch/Belgian OCR number string to float.

    Dutch format rules:
    - Period (.) = thousands separator  e.g. 31.880  → 31 880  → 31880.0
    - Comma (,)  = decimal  separator   e.g. 1.234,56 → 1234.56
    Handles mixed formats and plain integers/decimals gracefully.
    """
    s = s.strip().replace(" ", "")
    if not s:
        return 0.0

    has_dot   = "." in s
    has_comma = "," in s

    if has_dot and has_comma:
        # e.g. "1.234,56" — dot = thousands, comma = decimal
        s = s.replace(".", "").replace(",", ".")
    elif has_comma:
        # Only comma — treat as decimal separator (e.g. "17,160" → 17.160)
        s = s.replace(",", ".")
    elif has_dot:
        # Only dot — if exactly 3 digits follow it, it's a thousands separator
        # e.g. "31.880" → 31880,  but "31.88" stays as 31.88
        if re.match(r"^\d{1,3}(\.\d{3})+$", s):
            s = s.replace(".", "")          # remove thousands separators → integer string
        # else: treat the dot as a decimal point (e.g. "3.5")

    s = s.rstrip(".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _normalise_text(text: str) -> str:
    """Normalise OCR text for more reliable pattern matching."""
    # Collapse multiple spaces / tabs
    text = re.sub(r"[ \t]+", " ", text)
    # Normalise common OCR ligature / encoding issues
    text = text.replace("\u2014", "-").replace("\u2013", "-")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    return text


def extract_fields(text: str) -> dict[str, Any]:
    """Parse OCR text and return a dict of recognised form field values.

    Keys match ``st.session_state`` keys used in app.py so values can be
    applied directly.
    """
    text = _normalise_text(text)
    fields: dict[str, Any] = {}

    for key, patterns in _FIELD_PATTERNS + _EMAIL_FIELD_PATTERNS:
        for pat in patterns:
            m = pat.search(text)
            if m:
                val = m.group(1).strip()
                # For weights, only extract the first number (ignore extra numbers)
                if key in _NUMERIC_KEYS:
                    # Find the first number in the matched string
                    num_match = re.search(r"[0-9]+[.,]?[0-9]*", val)
                    num = _parse_number(num_match.group(0)) if num_match else 0.0
                    if num > 0:
                        fields[key] = num
                else:
                    # Trim trailing punctuation artefacts
                    val = val.rstrip(":;,.- ")
                    if val:
                        fields[key] = val
                break  # first matching pattern wins

    # --- Fallback: pick up any stray email addresses -------------------
    if not any(k.startswith("k_email_") for k in fields):
        all_emails = _GENERIC_EMAIL_RE.findall(text)
        email_keys = [
            "k_email_client", "k_email_transporter",
            "k_email_copro", "k_email_permit_holder",
        ]
        for addr, key in zip(all_emails, email_keys):
            fields[key] = addr

    return fields


def extract_fields_detailed(text: str) -> list[dict[str, Any]]:
    """Like :func:`extract_fields` but returns per-field metadata.

    Each entry is a dict with keys:
      - ``key``   : session-state key (e.g. ``"k_license_plate"``)
      - ``label`` : human-readable Dutch label
      - ``value`` : extracted value (str or float)
      - ``source``: the raw snippet that matched

    This format is used by the review UI in app.py.
    """
    text = _normalise_text(text)
    results: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for key, patterns in _FIELD_PATTERNS + _EMAIL_FIELD_PATTERNS:
        for pat in patterns:
            m = pat.search(text)
            if m and key not in seen_keys:
                raw_snippet = m.group(0).strip()
                val = m.group(1).strip()
                if key in _NUMERIC_KEYS:
                    # Extract only the FIRST number to avoid trailer numbers
                    num_match = re.search(r"[0-9]+[.,][0-9]+|[0-9]+", val)
                    if not num_match:
                        break
                    num = _parse_number(num_match.group(0))
                    if num <= 0:
                        break
                    val = num
                else:
                    val = val.rstrip(":;,.- ")
                    if not val:
                        break
                results.append({
                    "key": key,
                    "label": FIELD_LABELS.get(key, key),
                    "value": val,
                    "source": raw_snippet[:120],
                })
                seen_keys.add(key)
                break

    return results


def extract_structured(text: str) -> dict[str, Any]:
    """Extract all fields and return a fully structured JSON-compatible dict.

    The structure mirrors the 10-section delivery note schema:
    1. document_header
    2. delivery_identification
    3. weights
    4. product_information
    5. application
    6. project_client
    7. logistics
    8. signatures
    9. footer
    10. raw_text_full_transcription

    This is the richer sibling of :func:`extract_fields` and is suitable
    for logging, display and API output.
    """
    fields = extract_fields(text)

    def _get(key: str, default: Any = "") -> Any:
        return fields.get(key, default)

    structured: dict[str, Any] = {
        "document_header": {
            "company_name":     _get("k_company_name"),
            "company_branch":   _get("k_company_branch"),
            "company_address":  _get("k_company_address"),
            "company_postal":   _get("k_company_postal"),
            "company_city":     _get("k_company_city"),
            "tel_bridge":       _get("k_company_tel_bridge"),
            "tel_orders":       _get("k_company_tel_orders"),
            "ce_number":        _get("k_ce_number"),
        },
        "delivery_identification": {
            "document_type":    _get("k_document_type"),
            "afvoer_number":    _get("k_delivery_note_no"),
            "document_number":  _get("k_document_number"),
            "document_serial":  _get("k_document_serial"),
            "ticket_number":    _get("k_ticket_number"),
        },
        "weights": {
            "bruto_kg":                  _get("k_bruto_kg", 0.0),
            "tare_kg":                   _get("k_tare_weight_empty_kg", 0.0),
            "netto_kg":                  _get("k_net_total_quantity_ton", 0.0),
            "totaal_kg":                 _get("k_total_kg", 0.0),
        },
        "product_information": {
            "mixture_type":              _get("k_product_mixture_type"),
            "layer_type":                _get("k_asphalt_layer_type"),
            "grain_size":                _get("k_grain_size"),
            "standard_ref":              _get("k_standard_ref"),
            "asphalt_class":             _get("k_asphalt_class"),
            "certificate":               _get("k_certificate"),
            "declaration_of_performance": _get("k_declaration_of_performance"),
            "technical_data_sheet":      _get("k_technical_data_sheet"),
            "additives":                 _get("k_additives"),
        },
        "application": {
            "description":               _get("k_application"),
            "mechanical_resistance":     _get("k_mechanical_resistance"),
            "fuel_resistance":           _get("k_fuel_resistance"),
            "deicing_resistance":        _get("k_deicing_resistance"),
            "bitumen_aggregate_affinity": _get("k_bitumen_aggregate_affinity"),
        },
        "project_client": {
            "werf_client":               _get("k_werf_client"),
            "project_number":            _get("k_werf_number"),
            "address":                   _get("k_address"),
            "site_address":              _get("k_site_address"),
            "site_street":               _get("k_site_street"),
            "site_postal":               _get("k_site_postal"),
            "site_city":                 _get("k_site_city"),
            "client_address":            _get("k_client_address"),
        },
        "logistics": {
            "date":                      _get("k_date"),
            "time":                      _get("k_time"),
            "transport_company":         _get("k_transport_company"),
            "license_plate":             _get("k_license_plate"),
            "driver_name":               _get("k_driver_name"),
        },
        "signatures": {
            "copro_ref":                 _get("k_copro_ref"),
        },
        "footer": {
            "company_email":             _get("k_company_email"),
            "company_website":           _get("k_company_website"),
        },
        "emails": {
            "client":                    _get("k_email_client"),
            "transporter":               _get("k_email_transporter"),
            "copro":                     _get("k_email_copro"),
            "permit_holder":             _get("k_email_permit_holder"),
        },
        "raw_text_full_transcription":   text,
    }

    return structured


# ---------------------------------------------------------------------------
#  Convenience: full pipeline
# ---------------------------------------------------------------------------


def scan_and_extract(uploaded_file: BinaryIO,
                     content_type: str | None = None,
                     filename: str | None = None,
                     lang: str = "eng+nld") -> tuple[str, dict[str, Any]]:
    """Run OCR on *uploaded_file* and extract structured fields.

    Returns
    -------
    (raw_text, fields)
        *raw_text* is the full OCR output, *fields* is a dict ready
        to be merged into ``st.session_state``.
    """
    raw = extract_text(uploaded_file, content_type, filename, lang)
    fields = extract_fields(raw)
    return raw, fields


def scan_and_extract_detailed(
    uploaded_file: BinaryIO,
    content_type: str | None = None,
    filename: str | None = None,
    lang: str = "eng+nld",
) -> tuple[str, list[dict[str, Any]]]:
    """Run OCR and return per-field detailed results for UI review.

    Returns
    -------
    (raw_text, field_details)
        *field_details* is a list of dicts with key/label/value/source.
    """
    raw = extract_text(uploaded_file, content_type, filename, lang)
    details = extract_fields_detailed(raw)
    return raw, details


# ---------------------------------------------------------------------------
#  Scan from raw bytes (for camera input)
# ---------------------------------------------------------------------------


def scan_image_bytes(
    image_bytes: bytes, lang: str = "eng+nld",
) -> tuple[str, list[dict[str, Any]]]:
    """OCR raw image bytes (e.g. from ``st.camera_input``).

    Returns (raw_text, field_details).
    """
    img = Image.open(io.BytesIO(image_bytes))
    raw = ocr_image(img, lang=lang)
    details = extract_fields_detailed(raw)
    return raw, details


# ---------------------------------------------------------------------------
#  Dependency check
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """Return True if at least image-based OCR can run."""
    return _HAS_TESSERACT


def missing_dependencies() -> list[str]:
    """Return a list of missing optional packages."""
    missing: list[str] = []
    if not _HAS_TESSERACT:
        missing.append("pytesseract")
    if not _HAS_FITZ:
        missing.append("PyMuPDF")
    return missing

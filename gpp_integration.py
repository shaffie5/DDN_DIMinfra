"""
gpp_integration.py
==================
Integration layer between this DDN application and the GPP Excel tool.

# ─────────────────────────────────────────────────────────────────────────────
# HOW GPP FITS IN
# ─────────────────────────────────────────────────────────────────────────────
#
# GPP is an external Excel-based tool that needs to receive / export delivery
# note data.  There are two integration directions:
#
#  A) DDN  ──►  GPP   (push payload data into a GPP template or workbook)
#  B) GPP  ──►  DDN   (read back values from a GPP-produced workbook)
#
# This module owns both directions.  Call it from app.py and/or
# excel_export.py at the clearly marked TODO points.
#
# ─────────────────────────────────────────────────────────────────────────────
# STEP-BY-STEP INTEGRATION GUIDE FOR THE COLLEAGUE
# ─────────────────────────────────────────────────────────────────────────────
#
#  1. Implement `build_gpp_workbook(payload, signatures)` below.
#     • Load (or create) the GPP Excel template.
#     • Map each payload key to the correct GPP cell / named range.
#     • Return the finished workbook as bytes.
#
#  2. Implement `read_gpp_workbook(xlsx_bytes)` below.
#     • Open the bytes as an openpyxl workbook.
#     • Extract the fields that GPP writes back (e.g. GPP approval status,
#       GPP reference number, computed totals …).
#     • Return a dict — the caller will merge it into the DDN payload.
#
#  3. In excel_export.py → build_delivery_note_xlsx():
#     Look for the comment:
#         # ── GPP INTEGRATION POINT A ──
#     Call `gpp_integration.build_gpp_workbook(payload, signatures)` there
#     and attach / embed the result as an additional sheet or attachment.
#
#  4. In app.py → the two xlsx_bytes build sites:
#     Look for the comments:
#         # ── GPP INTEGRATION POINT B ──
#     After xlxs_bytes is built, optionally call
#     `gpp_integration.push_to_gpp(payload, signatures)` to send the data
#     to GPP automatically.
#
# ─────────────────────────────────────────────────────────────────────────────
# PAYLOAD KEY REFERENCE  (from ocr.py → FIELD_LABELS)
# ─────────────────────────────────────────────────────────────────────────────
#
#  Document identification
#    payload["delivery_note_no"]          Afvoernummer / leveringsbonnummer
#    payload["document_number"]           Nr. (bijv. 1300.828)
#    payload["document_serial"]           Volgnummer (bijv. 0072478)
#
#  Weights  (floats, Dutch thousands-separator already resolved)
#    payload["bruto_kg"]                  Bruto gewicht (kg)
#    payload["tare_weight_empty_kg"]      Tarra gewicht (kg)
#    payload["net_total_quantity_ton"]    Nettohoeveelheid (kg)
#    payload["total_kg"]                  Totaal (kg)
#
#  Product
#    payload["product_mixture_type"]      Mengseltype  (bijv. AC 20 onderlaag 50/70)
#    payload["asphalt_layer_type"]        Laagtype
#    payload["grain_size"]                Korrelgrootte
#    payload["certificate"]               Certificaat
#    payload["declaration_of_performance"] Prestatieverklaring (DoP)
#    payload["technical_data_sheet"]      Technische fiche / snelcode
#    payload["additives"]                 Toevoegsels
#
#  Application
#    payload["application"]               Toepassing
#    payload["mechanical_resistance"]     Mechanische weerstand
#    payload["fuel_resistance"]           Weerstand tegen brandstof
#    payload["deicing_resistance"]        Weerstand tegen ontdooiing
#    payload["bitumen_aggregate_affinity"] Affiniteit bitumen-aggregaat
#
#  Project / Client
#    payload["werf_client"]               Werf / Klant
#    payload["werf_number"]               Werfnummer / Projectnummer
#    payload["address"]                   Adres
#    payload["site_address"]              Werfadres
#    payload["client_address"]            Adres opdrachtgever
#
#  Logistics
#    payload["date"]                      Datum (dd-mm-yyyy)
#    payload["departure_time"]            Vertrektijd
#    payload["arrival_time"]              Aankomsttijd
#    payload["distance_km"]              Afstand (km)
#    payload["transport_company"]         Vervoerder
#    payload["license_plate"]             Nummerplaat
#    payload["transport_type"]            Transporttype
#    payload["energy_source"]             Energiebron
#
#  Signatures dict  (keyed by role: "client", "copro", "transporter",
#                                   "permit_holder")
#    sig["signer_name"]                   Naam ondertekenaar
#    sig["signed_at"]                     Tijdstip ondertekening (ISO)
#    sig["signature_path"]                Pad naar PNG handtekening
#
# ─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import shutil
import tempfile

from typing import Any
from pathlib import Path
from datetime import datetime
from gpp_link.config import Config
from gpp_link.standalone import standalone
from gpp_link.file_manager import FileManager


# ─────────────────────────────────────────────────────────────────────────────
# TODO A — DDN → GPP
# ─────────────────────────────────────────────────────────────────────────────

def build_gpp_workbook(
    payload: dict[str, Any],
    signatures: dict[str, dict[str, Any]],
) -> bytes:
    """Populate the GPP Excel template with delivery note data.

    Parameters
    ----------
    payload:
        The full DDN payload dict (see key reference above).
    signatures:
        Dict of signature metadata keyed by role.

    Returns
    -------
    bytes
        The finished GPP workbook as raw bytes (xlsx).

    Implementation notes
    --------------------
    • Load the GPP template from a known path (e.g. data/gpp_template.xlsx).
    • Write each payload value to its corresponding GPP cell / named range.
    • Embed signature images where required by GPP.
    • Save to BytesIO and return .getvalue().
    """
    # TODO: implement GPP workbook population
    raise NotImplementedError("GPP workbook builder not yet implemented.")


# ─────────────────────────────────────────────────────────────────────────────
# TODO B — GPP → DDN
# ─────────────────────────────────────────────────────────────────────────────

def read_gpp_workbook(xlsx_bytes: bytes) -> dict[str, Any]:
    """Extract fields written back by GPP into a DDN-compatible dict.

    Parameters
    ----------
    xlsx_bytes:
        Raw bytes of a GPP-produced Excel file.

    Returns
    -------
    dict
        Keys match DDN payload keys.  The caller should merge this into
        the existing payload (existing values take precedence unless GPP
        provides an authoritative override — decide with the project team).

    Implementation notes
    --------------------
    • Open with openpyxl.load_workbook(BytesIO(xlsx_bytes)).
    • Read the GPP-specific cells (approval status, GPP ref, totals …).
    • Return a flat dict of DDN payload keys.
    """
    # TODO: implement GPP read-back
    raise NotImplementedError("GPP read-back not yet implemented.")


# ─────────────────────────────────────────────────────────────────────────────
# TODO C — optional: push to GPP API / shared drive
# ─────────────────────────────────────────────────────────────────────────────

def push_to_gpp(
    payload: dict[str, Any],
    signatures: dict[str, dict[str, Any]],
    log_func=print,
) -> str:
    """Send the completed delivery note to GPP (API call, file drop, etc.).

    Called from app.py at the GPP INTEGRATION POINT B markers after the
    Excel has been built.  Implement whichever transport GPP requires:
    REST API, SFTP drop, shared network folder, etc.

    Transfer the generated DDN Excel into the GPP workbook
    using the standalone Excel transfer engine.
    """
    # TODO: implement GPP push / export
    # raise NotImplementedError("GPP push not yet implemented.")

    log_func("DONE!!!!!")
    BASE_DIR = Path(__file__).resolve().parent
    OUTPUT_DIR = BASE_DIR / "output"

    # Validate configuration
    config_valid, config_errors = Config.validate()
    if not config_valid:
        raise RuntimeError(f"GPP configuration invalid: {config_errors}")

    # Create isolated processing environment
    # session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    with tempfile.TemporaryDirectory() as tmpdir_raw:
        tmpdir = Path(tmpdir_raw)

        mapping_path_real = Config.get_mapping_file("DDN")

        source_path = tmpdir / "ddn_source.xlsx"
        target_path = tmpdir / Config.TARGET_TEMPLATE.name
        mapping_path = tmpdir / mapping_path_real.name

        # Build DDN Excel from payload
        from excel_export import build_delivery_note_xlsx
        xlsx_bytes = build_delivery_note_xlsx(payload, signatures)
        source_path.write_bytes(xlsx_bytes)

        # Copy template + mapping
        if not FileManager.copy_file_safe(Config.TARGET_TEMPLATE, target_path):
            raise RuntimeError("Failed to prepare GPP template.")

        if not FileManager.copy_file_safe(mapping_path_real, mapping_path):
            raise RuntimeError("Failed to prepare mapping file.")

        # Run standalone processor
        result_msg = standalone(
            str(source_path),
            str(target_path),
            str(mapping_path),
        )

        # Persist final result
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_name = f"GPP_updated_{timestamp}.xlsx"
        final_path = OUTPUT_DIR / final_name

        shutil.copy2(target_path, final_path)

        return f"{result_msg}\nSaved to: {final_path}"

"""
gpp_engine.py
=============
Use the PIONEERS GPP TOOL Excel workbook as a backend calculation engine.

xlwings drives a headless Excel instance so that all formulas (including
array formulas, XLOOKUP, cross-sheet references) are evaluated natively.

Workflow
--------
1. Copy the GPP template to a temp working copy.
2. Write DDN payload values into the **Input** sheet cells.
3. Force Excel to recalculate.
4. Read computed results from **Results**, **DDN_Results_TP**,
   **Result Dashboard**, and **Aux - Check** sheets.
5. Return a structured Python dict to the Streamlit frontend.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import xlwings as xw

GPP_TEMPLATE = Path(__file__).resolve().parent / "gpp_link" / "PIONEERS GPP TOOL_20260310.xlsx"

# ─────────────────────────────────────────────────────────────────────
#  DDN Payload → GPP Input Cell Mapping
# ─────────────────────────────────────────────────────────────────────
#
# Keys come from the DDN app payload dict.
# Values are the cell addresses on the GPP "Input" sheet.

INPUT_CELL_MAP: dict[str, str] = {
    # Section 5: Transportation of Asphalt Mix to Site (row 63)
    "transport_mode":      "B63",   # e.g. "Truck"
    "energy_source":       "C63",   # e.g. "Diesel_Euro5"
    "distance_km":         "D63",   # driving distance in km
    "bruto_kg":            "E63",   # gross weight in kg
}

# 19 PEF impact category labels, in the exact order used across all
# result sheets (rows 4-22 of the Results sheet).
IMPACT_CATEGORIES: list[str] = [
    "Acidification",
    "Climate change",
    "Climate change: biogenic",
    "Climate change: fossil",
    "Land use and land use change",
    "Ecotoxicity: freshwater",
    "Energy resources: non-renewable",
    "Eutrophication: freshwater",
    "Eutrophication: marine",
    "Eutrophication: terrestrial",
    "Human toxicity: carcinogenic",
    "Human toxicity: non-carcinogenic",
    "Ionising radiation: human health",
    "Land use",
    "Material resources: metals/minerals",
    "Ozone depletion",
    "Particulate matter formation",
    "Photochemical oxidant formation: human health",
    "Water use",
]

# Lifecycle stage column headers in Results rows 4-22 (columns C-M).
LIFECYCLE_STAGES: list[str] = [
    "A1 - Primary Raw Material",
    "C3' - Secondary Raw Material",
    "A2 - Transport Primary",
    "C2' - Transport Secondary",
    "A3 - Production",
    "A4 - Transport",
    "A5 - Construction",
    "C1 - Deconstruction",
    "D - Burdens & Savings",
    "Total A1-A3,C2',C3'",
    "Total A1-A5,C1,C2',C3'",
]


# ─────────────────────────────────────────────────────────────────────
#  Engine
# ─────────────────────────────────────────────────────────────────────

class GPPEngine:
    """Drive the GPP Excel tool as a headless calculation backend."""

    def calculate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Write DDN inputs, recalculate Excel, read results.

        Parameters
        ----------
        payload : dict
            DDN delivery note payload (same dict used throughout app.py).

        Returns
        -------
        dict with keys:
            pef_score        – float  (total PEF single score, mPt/ton)
            gwp_total        – float  (global warming kgCO₂-eq/ton, cradle-to-gate)
            check_ok         – bool   (True if all GPP validation checks pass)
            impact_matrix    – list[dict]  (19 rows × lifecycle stages)
            single_scores    – list[dict]  (19 rows × lifecycle stages, normalised)
            transport_impacts – list[dict] (19 rows, per-ton + total)
            transport_single_scores – list[dict] (16 rows, single score)
            dashboard        – dict   (general info from Result Dashboard)
        """
        tmpdir = tempfile.mkdtemp(prefix="gpp_calc_")
        work_copy = Path(tmpdir) / GPP_TEMPLATE.name
        shutil.copy2(GPP_TEMPLATE, work_copy)

        app: xw.App | None = None
        try:
            app = xw.App(visible=False)
            app.display_alerts = False
            app.screen_updating = False

            wb = app.books.open(str(work_copy))
            inp = wb.sheets["Input"]

            # ── Write inputs ────────────────────────────────────────
            for key, cell_addr in INPUT_CELL_MAP.items():
                value = payload.get(key)
                if value is not None:
                    inp.range(cell_addr).value = value

            # ── Force recalculation ─────────────────────────────────
            app.calculate()

            # ── Read Results sheet (rows 4-22, cols A-M) ────────────
            res = wb.sheets["Results"]
            impact_raw = res.range("A4:M22").value          # 19 rows × 13 cols
            single_raw = res.range("A28:O47").value         # 19 rows × 15 cols

            # ── Read DDN_Results_TP ─────────────────────────────────
            ddn_tp = wb.sheets["DDN_Results_TP"]
            tp_bruto = ddn_tp.range("B2").value             # tons
            tp_distance = ddn_tp.range("B3").value          # km
            tp_impacts_raw = ddn_tp.range("A8:C26").value   # 19 rows
            tp_single_raw = ddn_tp.range("A30:C45").value   # 16 rows
            tp_single_total = ddn_tp.range("B46:C46").value # totals

            # ── Read Result Dashboard headlines ─────────────────────
            dash = wb.sheets["Result Dashboard"]
            pef_score = dash.range("E6").value
            gwp_total = dash.range("E7").value
            dashboard_info = {
                "date":          dash.range("B4").value,
                "mixture_id":    dash.range("B5").value,
                "mixture_sb250": dash.range("B6").value,
                "mixture_en":    dash.range("B7").value,
                "plant":         dash.range("B8").value,
                "temperature":   dash.range("B9").value,
                "binder_pct":    dash.range("B10").value,
                "binder_repl":   dash.range("B11").value,
                "bulk_density":  dash.range("B12").value,
            }

            # ── Read Aux - Check ────────────────────────────────────
            chk = wb.sheets["Aux - Check"]
            check_sum = chk.range("B11").value

            wb.close()

        finally:
            if app is not None:
                app.quit()
            # Clean up temp directory
            shutil.rmtree(tmpdir, ignore_errors=True)

        # ── Pack into structured output ─────────────────────────────
        result = _pack_results(
            pef_score=pef_score,
            gwp_total=gwp_total,
            check_sum=check_sum,
            impact_raw=impact_raw,
            single_raw=single_raw,
            tp_bruto=tp_bruto,
            tp_distance=tp_distance,
            tp_impacts_raw=tp_impacts_raw,
            tp_single_raw=tp_single_raw,
            tp_single_total=tp_single_total,
            dashboard_info=dashboard_info,
        )
        result["energy_source"] = payload.get("energy_source")
        return result


# ─────────────────────────────────────────────────────────────────────
#  Result Packing
# ─────────────────────────────────────────────────────────────────────

def _safe_float(v: Any) -> float | None:
    """Coerce a value to float, returning None for errors/strings."""
    if v is None:
        return None
    try:
        f = float(v)
        return f
    except (ValueError, TypeError):
        return None


def _pack_results(
    *,
    pef_score,
    gwp_total,
    check_sum,
    impact_raw,
    single_raw,
    tp_bruto,
    tp_distance,
    tp_impacts_raw,
    tp_single_raw,
    tp_single_total,
    dashboard_info,
) -> dict[str, Any]:
    """Transform raw xlwings arrays into clean structured dicts."""

    # Impact matrix: 19 impact categories × lifecycle stages
    impact_matrix = []
    if impact_raw:
        for row in impact_raw:
            if row and row[0]:
                entry = {"category": str(row[0]).strip()}
                # Columns C-M → indices 2..12
                for i, stage in enumerate(LIFECYCLE_STAGES):
                    entry[stage] = _safe_float(row[2 + i]) if len(row) > 2 + i else None
                impact_matrix.append(entry)

    # Single scores: 19 categories, columns D-O (indices 3..14)
    single_score_stages = LIFECYCLE_STAGES + ["Total (incl. D)"]
    single_scores = []
    if single_raw:
        for row in single_raw:
            if row and row[0]:
                entry = {"category": str(row[0]).strip()}
                # Columns D-O → indices 3..14
                for i, stage in enumerate(single_score_stages):
                    entry[stage] = _safe_float(row[3 + i]) if len(row) > 3 + i else None
                single_scores.append(entry)

    # Transport impacts (per-ton and total-for-delivery)
    transport_impacts = []
    if tp_impacts_raw:
        for row in tp_impacts_raw:
            if row and row[0]:
                transport_impacts.append({
                    "category": str(row[0]).strip(),
                    "per_ton": _safe_float(row[1]) if len(row) > 1 else None,
                    "total":   _safe_float(row[2]) if len(row) > 2 else None,
                })

    # Transport single scores
    transport_single_scores = []
    if tp_single_raw:
        for row in tp_single_raw:
            if row and row[0]:
                transport_single_scores.append({
                    "category": str(row[0]).strip(),
                    "per_ton_mpt": _safe_float(row[1]) if len(row) > 1 else None,
                    "total_mpt":   _safe_float(row[2]) if len(row) > 2 else None,
                })

    # Check if GPP validation passes (sum of all checks should equal 0)
    check_ok = _safe_float(check_sum) == 0.0 if check_sum is not None else None

    return {
        "pef_score": _safe_float(pef_score),
        "gwp_total": _safe_float(gwp_total),
        "check_ok": check_ok,
        "impact_matrix": impact_matrix,
        "single_scores": single_scores,
        "transport_impacts": transport_impacts,
        "transport_single_scores": transport_single_scores,
        "transport_single_total": {
            "per_ton_mpt": _safe_float(tp_single_total[0]) if tp_single_total else None,
            "total_mpt":   _safe_float(tp_single_total[1]) if tp_single_total and len(tp_single_total) > 1 else None,
        },
        "transport_bruto_ton": _safe_float(tp_bruto),
        "transport_distance_km": _safe_float(tp_distance),
        "dashboard": dashboard_info,
    }

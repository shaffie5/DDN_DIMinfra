"""
vn_to_gpp.py
============
Classify components from a parsed VN sheet and translate them into the
cell-level inputs the GPP Excel "Input" sheet expects.

Per project requirements:
* Production temperature is **never** overwritten — leave GPP defaults.
* Energy source for the inbound trucks is always ``Diesel_Euro6``
  (simplification).  Other transport modes use the closest GPP energy
  source (Barge → ``Diesel``, Ship → ``Heavy_fuel_oil``, Train →
  ``Diesel``).
* GPP "Input" sheet has 3 coarse + 3 crushed-fine + 3 natural-fine + 2
  filler + 2 RAP + 2 other-waste + 3 additive slots.  If the VN file
  contains more entries in any class an error is raised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

import geo
from vn_parser import VNComponent, VNPlant


# ─────────────────────────────────────────────────────────────────────
#  Translation tables
# ─────────────────────────────────────────────────────────────────────

# VN plant heating fuel  →  GPP "Plant primary energy" value
PLANT_ENERGY_MAP: dict[str, str] = {
    "aardgas":      "Natural_gas",
    "propaan":      "Propane",
    "elektrisch":   "Electricity",
}

# VN "aanvoer per"  →  (GPP transport mode, GPP energy source)
TRANSPORT_MAP: dict[str, tuple[str, str]] = {
    "vrachtwagen":  ("Truck",  "Diesel_Euro6"),
    "truck":        ("Truck",  "Diesel_Euro6"),
    "schip":        ("Ship",   "Heavy_fuel_oil"),
    "ship":         ("Ship",   "Heavy_fuel_oil"),
    "binnenvaart":  ("Barge",  "Diesel"),
    "barge":        ("Barge",  "Diesel"),
    "trein":        ("Train",  "Diesel"),
    "train":        ("Train",  "Diesel"),
}
DEFAULT_TRANSPORT = ("Truck", "Diesel_Euro6")


# Classification → GPP "Input" sheet row anchors
GPP_SLOTS: dict[str, list[int]] = {
    "binder":          [39],
    "coarse":          [40, 41, 42],   # >2 mm crushed
    "crushed_fine":    [43, 44, 45],   # ≤2 mm crushed
    "natural_fine":    [46, 47, 48],   # ≤2 mm natural
    "filler":          [49, 50],
    "rap":             [51, 52],
    "other_waste":     [53, 54],
    "additive":        [55, 56, 57],
}

# Default material "Type" cell (column C on Input) for those slots that
# require it.  (Coarse / fine aggregate slots use the row default.)
GPP_TYPE_DEFAULTS: dict[str, str] = {
    "binder":      "Conventional_Bitumen",
    "filler":      "Limestone_residue",
    "rap":         "RAP",
    "other_waste": "No_other_waste",
    "additive":    "No_additives",
}

# Naming hints (Dutch + English).  Order matters — checked top-down.
_BINDER_KEYS    = ("bindmiddel", "bitumen", "binder")
_FILLER_KEYS    = ("vulstof", "filler")
_RAP_KEYS       = ("asfaltgranulaat", "stapel", "rap")
_ADDITIVE_KEYS  = (
    "afdruipremmer", "kleurstof", "trinidad", "uintah",
    "additief", "additive", "wax", "amine",
)
_NAT_FINE_KEYS  = ("zand", "sable", "sand")  # natural fine indicator

_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*/\s*(\d+(?:[.,]\d+)?)")


def classify_component(name: str) -> str:
    """Map a VN component name to a GPP slot category."""
    n = (name or "").lower()
    if any(k in n for k in _BINDER_KEYS):
        return "binder"
    if any(k in n for k in _FILLER_KEYS):
        return "filler"
    if any(k in n for k in _RAP_KEYS):
        return "rap"
    if any(k in n for k in _ADDITIVE_KEYS):
        return "additive"
    # Aggregate: decide via grain size + nat/synth keyword
    m = _SIZE_RE.search(n)
    if m:
        lo = float(m.group(1).replace(",", "."))
        if lo > 2.0:
            return "coarse"
        # ≤2 mm
        if any(k in n for k in _NAT_FINE_KEYS):
            return "natural_fine"
        return "crushed_fine"
    # No grain size → assume natural fine if it mentions sand
    if any(k in n for k in _NAT_FINE_KEYS):
        return "natural_fine"
    # Fallback: treat as additive so user notices it in the preview.
    return "additive"


def map_transport(vn_mode: str | None) -> tuple[str, str]:
    """Translate the Dutch ``aanvoer per`` term to (mode, energy)."""
    if not vn_mode:
        return DEFAULT_TRANSPORT
    return TRANSPORT_MAP.get(vn_mode.strip().lower(), DEFAULT_TRANSPORT)


def map_plant_energy(vn_energy: str | None) -> str:
    if not vn_energy:
        return "Natural_gas"
    return PLANT_ENERGY_MAP.get(vn_energy.strip().lower(), "Natural_gas")


# ─────────────────────────────────────────────────────────────────────
#  Mapping result objects
# ─────────────────────────────────────────────────────────────────────

@dataclass
class MappedComponent:
    vn_row: int
    name: str
    category: str          # binder / coarse / crushed_fine / ...
    gpp_row: int           # destination row on Input sheet
    pct_fraction: float    # 0..1 (GPP wants fraction, not %)
    origin: str | None
    mode_vn: str | None
    mode_gpp: str          # Truck / Barge / Ship / Train / No
    energy_gpp: str        # Diesel_Euro6 / Heavy_fuel_oil / ...
    distance_km: float | None  # geocoded; None if origin missing or geocode failed
    distance_method: str   # "osrm" | "haversine" | "skipped" | "failed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MappingResult:
    plant_id: str
    plant_index: int
    plant_location: str
    plant_lat: float | None
    plant_lon: float | None
    general_cells: dict[str, Any]   # cell address → value (for B6, B7, B8, ...)
    components: list[MappedComponent]
    warnings: list[str]
    binder_extra: dict[str, Any] | None = None  # info about virgin binder origin

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["components"] = [c.to_dict() for c in self.components]
        return d

    def to_cell_payload(self) -> dict[str, Any]:
        """Return a flat dict ``{cell_address: value}`` ready for the
        GPP engine to write."""
        cells: dict[str, Any] = dict(self.general_cells)
        for c in self.components:
            r = c.gpp_row
            cells[f"B{r}"] = round(c.pct_fraction, 6)
            if c.category in GPP_TYPE_DEFAULTS:
                cells[f"C{r}"] = GPP_TYPE_DEFAULTS[c.category]
            cells[f"H{r}"] = c.origin or ""
            # Route 1
            cells[f"J{r}"] = c.mode_gpp
            cells[f"K{r}"] = c.energy_gpp
            cells[f"L{r}"] = (
                int(round(c.distance_km)) if c.distance_km is not None else 0
            )
            # Route 2 / Route 3 explicitly disabled
            cells[f"M{r}"] = "No"
            cells[f"N{r}"] = "No"
            cells[f"O{r}"] = 0
            cells[f"P{r}"] = "No"
            cells[f"Q{r}"] = "No"
            cells[f"R{r}"] = 0
        return cells


# ─────────────────────────────────────────────────────────────────────
#  Mapping engine
# ─────────────────────────────────────────────────────────────────────

def _compute_distance(plant_pt: geo.GeoPoint | None,
                      origin: str | None,
                      mode_gpp: str) -> tuple[float | None, str]:
    if not origin:
        return None, "skipped"
    origin_pt = geo.geocode(origin)
    if origin_pt is None or plant_pt is None:
        return None, "failed"
    if mode_gpp == "Truck":
        result = geo.osrm_route_km(origin_pt, plant_pt)
        if result is not None:
            return result[0], "osrm"
    # Ship / Barge / Train / OSRM failure → straight-line proxy
    return geo.haversine_km(origin_pt, plant_pt), "haversine"


def map_plant(plant: VNPlant) -> MappingResult:
    """Build the full GPP cell payload for a single plant column."""
    warnings: list[str] = []

    plant_pt = geo.geocode(plant.plant_location) if plant.plant_location else None
    if plant.plant_location and plant_pt is None:
        warnings.append(
            f"Could not geocode plant location: {plant.plant_location!r}. "
            "Distances will be 0."
        )

    # ── General cells (Input sheet rows 5-21) ───────────────────────
    general_cells: dict[str, Any] = {}
    if plant.date:
        general_cells["B5"] = plant.date
    if plant.mixture_id:
        general_cells["B6"] = plant.mixture_id
    if plant.mixture_sb250:
        general_cells["B7"] = plant.mixture_sb250
    if plant.mixture_en:
        general_cells["B8"] = plant.mixture_en
    if plant.total_binder_pct is not None:
        general_cells["B9"] = round(plant.total_binder_pct / 100.0, 6)
    if plant.plant_location:
        general_cells["B14"] = plant.plant_location
    if plant.plant_capacity_tph is not None:
        general_cells["B16"] = plant.plant_capacity_tph
    general_cells["B17"] = map_plant_energy(plant.plant_energy)
    # NOTE: B21 (production temperature) is intentionally NOT written.

    # ── Allocate components to GPP rows ─────────────────────────────
    free_slots: dict[str, list[int]] = {k: list(v) for k, v in GPP_SLOTS.items()}
    mapped: list[MappedComponent] = []

    # 1) Insert the virgin binder row (row 39) from the dedicated
    #    binder block (rows 19-23) on the VN sheet.
    if plant.binder_pct is not None and plant.binder_pct > 0:
        mode_gpp, energy_gpp = map_transport(plant.binder_mode)
        dist, method = _compute_distance(plant_pt, plant.binder_origin, mode_gpp)
        mapped.append(MappedComponent(
            vn_row=20,
            name=plant.binder_type or "Virgin binder",
            category="binder",
            gpp_row=free_slots["binder"].pop(0),
            pct_fraction=plant.binder_pct / 100.0,
            origin=plant.binder_origin,
            mode_vn=plant.binder_mode,
            mode_gpp=mode_gpp,
            energy_gpp=energy_gpp,
            distance_km=dist,
            distance_method=method,
        ))

    # 2) Loop over composition rows.
    for comp in plant.components:
        if comp.pct <= 0 and not comp.extra:
            continue
        cat = classify_component(comp.name)
        slot_list = free_slots.get(cat)
        if not slot_list:
            warnings.append(
                f"GPP has no remaining {cat} slot for VN row {comp.row} "
                f"({comp.name!r}); component skipped."
            )
            continue
        gpp_row = slot_list.pop(0)
        mode_gpp, energy_gpp = map_transport(comp.mode)
        dist, method = _compute_distance(plant_pt, comp.origin, mode_gpp)
        mapped.append(MappedComponent(
            vn_row=comp.row,
            name=comp.name,
            category=cat,
            gpp_row=gpp_row,
            pct_fraction=comp.pct / 100.0,
            origin=comp.origin,
            mode_vn=comp.mode,
            mode_gpp=mode_gpp,
            energy_gpp=energy_gpp,
            distance_km=dist,
            distance_method=method,
        ))

    # 3) Sanity-check totals (sum of fractions ≈ 1.0)
    total_frac = sum(c.pct_fraction for c in mapped)
    if not (0.99 <= total_frac <= 1.01):
        warnings.append(
            f"Sum of mapped component fractions = {total_frac:.4f}; "
            "GPP requires it to equal 1.00 (±0.001)."
        )

    return MappingResult(
        plant_id=plant.plant_id,
        plant_index=plant.plant_index,
        plant_location=plant.plant_location or "",
        plant_lat=plant_pt.lat if plant_pt else None,
        plant_lon=plant_pt.lon if plant_pt else None,
        general_cells=general_cells,
        components=mapped,
        warnings=warnings,
    )

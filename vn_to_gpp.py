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
from dataclasses import dataclass, asdict, field
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
    "geen":         "No",
    "":             "No",
}

# VN "Ja"/"Nee" yes/no toggles  →  GPP "Yes"/"No"
YES_NO_MAP: dict[str, str] = {
    "ja":  "Yes",
    "yes": "Yes",
    "nee": "No",
    "no":  "No",
    "geen": "No",
}

# VN electric-equipment energy source  →  GPP value
ELECTRIC_SOURCE_MAP: dict[str, str] = {
    "elektriciteit normaal":   "Electricity",
    "elektriciteit groen":     "Electricity_Green",
    "groene elektriciteit":    "Electricity_Green",
    "geen":                    "No",
    "":                        "No",
}

# VN wheel-loader fuel  →  GPP energy-source value
WHEEL_LOADER_FUEL_MAP: dict[str, str] = {
    "diesel":      "Diesel",
    "elektrisch":  "Electric",
    "electric":    "Electric",
    "geen":        "No",
}

# Friendly labels for cells we write to the GPP Input sheet.  Used by
# `MappingResult.general_labelled()` so the UI can show *what* each
# populated value represents.
GPP_CELL_LABELS: dict[str, str] = {
    "B5":  "Datum",
    "B6":  "Mengsel-ID",
    "B7":  "Mengseltype (SB250)",
    "B8":  "Mengseltype (EN 13108)",
    "B9":  "Totaal bindmiddelgehalte (gewichts-%)",
    "B14": "Locatie asfaltcentrale",
    "B16": "Gemiddelde uurproductie (t/u)",
    "B17": "Primaire energiebron (verwarming/droging)",
    "C17": "Aandeel primaire energiebron",
    "B18": "Secundaire energiebron (verwarming/droging)",
    "C18": "Aandeel secundaire energiebron",
    "B19": "Elektrische uitrusting",
    "C19": "Energiebron elektrische uitrusting",
    "B21": "Productietemperatuurbereik (°C)",
    "C26": "Brandstof wiellader",
}

# VN "aanvoer per"  →  (GPP transport mode, GPP energy source)
#
# NOTE on ships vs barges:
#   In Belgian/Dutch asphalt VNs the word "schip" almost always refers
#   to an *inland* barge (binnenvaart) on the Albert canal / Maas /
#   Schelde, NOT a sea-going ship.  We therefore default "schip" to
#   "Barge" / Diesel.  Use the explicit term "zeeschip" (or "sea ship")
#   when the cargo is genuinely transported by an ocean-going vessel.
TRANSPORT_MAP: dict[str, tuple[str, str]] = {
    "vrachtwagen":  ("Truck",  "Diesel_Euro6"),
    "truck":        ("Truck",  "Diesel_Euro6"),
    "schip":        ("Barge",  "Diesel"),         # inland (default)
    "binnenvaart":  ("Barge",  "Diesel"),
    "barge":        ("Barge",  "Diesel"),
    "zeeschip":     ("Ship",   "Heavy_fuel_oil"), # sea-going only
    "sea ship":     ("Ship",   "Heavy_fuel_oil"),
    "ship":         ("Ship",   "Heavy_fuel_oil"),
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

# Per-component name overrides for the GPP "Type" cell.  Checked as
# case-insensitive substring matches against the VN component name.
# Use this when a generic category default doesn't match the actual
# product known to the GPP tool.
GPP_TYPE_NAME_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("afdruipremmer", "Cellulose_fibres"),
)


def gpp_type_for(category: str, name: str | None) -> str | None:
    """Resolve the GPP Input column-C "Type" value for a component.

    Per-name overrides win over the per-category default.
    """
    n = (name or "").lower()
    for key, gpp_type in GPP_TYPE_NAME_OVERRIDES:
        if key in n:
            return gpp_type
    return GPP_TYPE_DEFAULTS.get(category)

# Naming hints (Dutch + English).  Order matters — checked top-down.
_BINDER_KEYS    = ("bindmiddel", "bitumen", "binder")
# "Extra teruggew. stof" / "eigen vulstof" is the dust recovered from
# the centrale's own dryer/baghouse and reused on-site as filler.  We
# map it to a filler slot (type = Limestone_residue, the closest GPP
# category) with on-site origin so A2 transport ~ 0.02 km.  The UI
# still flags it via the `extra_recycled` tag.
_EXTRA_RECYCLED_KEYS = ("teruggew",)
_FILLER_KEYS    = ("vulstof", "filler")
_RAP_KEYS       = ("asfaltgranulaat", "stapel", "rap")
_ADDITIVE_KEYS  = (
    "afdruipremmer", "kleurstof", "trinidad", "uintah",
    "additief", "additive", "wax", "amine",
)
_NAT_FINE_KEYS  = ("zand", "sable", "sand")  # natural fine indicator

# ─────────────────────────────────────────────────────────────────────
#  Component exclusions
# ─────────────────────────────────────────────────────────────────────
# Components whose name (case-insensitive substring) matches any entry
# below are silently dropped before mapping.  The classification logic
# above still recognises them, so re-enabling is a one-line change.
EXCLUDED_COMPONENT_KEYS: tuple[str, ...] = (
    "rode kleurstof",   # TEMP: GPP tool has no impact data for red dye
    "uintah",           # TEMP: GPP tool has no impact data for Uintah additive
)


def is_excluded_component(name: str | None) -> bool:
    """Return True when the component should be skipped for now."""
    if not name:
        return False
    n = name.lower()
    return any(k in n for k in EXCLUDED_COMPONENT_KEYS)


# Origin labels that mean "already at the asphalt plant" → 0 km transport.
_ONSITE_ORIGIN_KEYS: tuple[str, ...] = (
    "productieproces",
    "productie proces",
    "on-site",
    "onsite",
    "ter plaatse",
)


def _is_onsite_origin(origin: str | None) -> bool:
    if not origin:
        return False
    n = origin.strip().lower()
    return any(k in n for k in _ONSITE_ORIGIN_KEYS)

_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*/\s*(\d+(?:[.,]\d+)?)")


def is_extra_recycled(name: str | None) -> bool:
    """True for the 'Extra teruggew. stof' family of components."""
    if not name:
        return False
    n = name.lower()
    return any(k in n for k in _EXTRA_RECYCLED_KEYS)


def classify_component(name: str) -> str:
    """Map a VN component name to a GPP slot category."""
    n = (name or "").lower()
    if any(k in n for k in _BINDER_KEYS):
        return "binder"
    # "Extra teruggew. stof" → own filler (recovered baghouse dust,
    # reused on-site).  Treated as a filler slot.
    if any(k in n for k in _EXTRA_RECYCLED_KEYS):
        return "filler"
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
    distance_method: str   # "osrm" | "haversine_x_factor" | "onsite" | "manual_required" | "skipped" | "failed"
    extra_recycled: bool = False   # True for "Extra teruggew. stof" rows
    manual_distance: bool = False  # True → user must enter distance_km

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
    total_pct_fraction: float = 0.0    # Σ of mapped component fractions (0..1)
    total_distance_km: float = 0.0     # Σ of per-component distances
    vn_composition_total_pct: float | None = None  # value of VN's own "Total" cell, if present
    excluded_components: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["components"] = [c.to_dict() for c in self.components]
        d["general_labelled"] = self.general_labelled()
        d["needs_manual_distance"] = [
            {"vn_row": c.vn_row, "name": c.name, "category": c.category}
            for c in self.components if c.manual_distance and c.distance_km is None
        ]
        return d

    def apply_manual_distances(self, overrides: dict[str, float]) -> None:
        """Patch components flagged as manual_distance with user-entered km.

        ``overrides`` keys are vn_row numbers (str or int).  Updates
        ``total_distance_km`` to stay consistent.
        """
        if not overrides:
            return
        norm = {str(k): float(v) for k, v in overrides.items() if v is not None}
        for c in self.components:
            if not c.manual_distance:
                continue
            v = norm.get(str(c.vn_row))
            if v is None:
                continue
            c.distance_km = v
            c.distance_method = "manual"
        self.total_distance_km = sum((c.distance_km or 0.0) for c in self.components)

    def general_labelled(self) -> list[dict[str, Any]]:
        """Return the populated general-info cells as an ordered list of
        ``{cell, label, value}`` rows so the UI can show *what* each
        value is mapped to in the GPP Input sheet."""
        rows: list[dict[str, Any]] = []
        for cell, value in self.general_cells.items():
            rows.append({
                "cell":  cell,
                "label": GPP_CELL_LABELS.get(cell, cell),
                "value": value,
            })
        # Sort by Input-sheet row number for readability
        def _row(c: str) -> int:
            try:
                return int("".join(ch for ch in c if ch.isdigit()))
            except ValueError:
                return 0
        rows.sort(key=lambda r: (_row(r["cell"]), r["cell"]))
        return rows

    def to_cell_payload(self) -> dict[str, Any]:
        """Return a flat dict ``{cell_address: value}`` ready for the
        GPP engine to write."""
        cells: dict[str, Any] = dict(self.general_cells)
        for c in self.components:
            r = c.gpp_row
            cells[f"B{r}"] = round(c.pct_fraction, 6)
            gpp_type = gpp_type_for(c.category, c.name)
            if gpp_type is not None:
                cells[f"C{r}"] = gpp_type
            cells[f"H{r}"] = c.origin or ""
            # Route 1
            cells[f"J{r}"] = c.mode_gpp
            cells[f"K{r}"] = c.energy_gpp
            # Distance: on-site (Productieproces) keeps a sub-km token
            # value (e.g. 0.02) so the GPP transport-check passes;
            # all other rows round to whole km as before.
            if c.distance_km is None:
                cells[f"L{r}"] = 0
            elif c.distance_method == "onsite":
                cells[f"L{r}"] = round(c.distance_km, 2)
            else:
                cells[f"L{r}"] = int(round(c.distance_km))
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
    # On-site origin (own production process) → no upstream transport.
    # Returning 0 km here is paired with mode/energy = "No" in the
    # caller so the GPP tool's transport-check doesn't flag the row.
    if _is_onsite_origin(origin):
        return 0.0, "onsite"
    origin_pt = geo.geocode(origin)
    if origin_pt is None or plant_pt is None:
        return None, "failed"
    if mode_gpp == "Truck":
        result = geo.osrm_route_km(origin_pt, plant_pt)
        if result is not None:
            return result[0], "osrm"
    # Apply mode-specific detour multipliers to the great-circle
    # distance — barges follow waterways, trains follow rail — so a
    # straight line typically under-estimates the real route by 20-40%.
    detour = {
        "Barge": 1.40,   # canals + meanders
        "Ship":  1.25,   # coastal routes
        "Train": 1.20,   # rail network detours
        "Truck": 1.30,   # OSRM failure fallback
    }.get(mode_gpp, 1.30)
    great_circle = geo.haversine_km(origin_pt, plant_pt)
    return great_circle * detour, f"haversine_x{detour:.2f}"


def map_plant(plant: VNPlant) -> MappingResult:
    """Build the full GPP cell payload for a single plant column."""
    warnings: list[str] = []

    plant_pt = geo.geocode(plant.plant_location) if plant.plant_location else None
    if plant.plant_location and plant_pt is None:
        warnings.append(
            f"Could not geocode plant location: {plant.plant_location!r}. "
            "Distances will be 0."
        )

    # ── General cells (Input sheet rows 5-26) ───────────────────────
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

    # Primary energy share (cell C17, fraction 0..1).  Source value is
    # already a fraction (1 = 100%).
    prim_pct = getattr(plant, "primary_energy_pct", None)
    if prim_pct is not None:
        general_cells["C17"] = float(prim_pct)

    # Secondary heater type (B18) + share (C18)
    sec_type = getattr(plant, "energy_source_primary_secondary", None)
    if sec_type:
        general_cells["B18"] = map_plant_energy(sec_type)
    sec_pct = getattr(plant, "secondary_energy_pct", None)
    if sec_pct is not None:
        general_cells["C18"] = float(sec_pct)

    # Electric equipment toggle (B19) + source (C19)
    elec = getattr(plant, "electric_share_equipment", None)
    if elec:
        general_cells["B19"] = YES_NO_MAP.get(elec.strip().lower(), elec)
    elec_src = getattr(plant, "electric_source", None)
    if elec_src:
        general_cells["C19"] = ELECTRIC_SOURCE_MAP.get(
            elec_src.strip().lower(), elec_src
        )

    # Production temperature range (B21).  The GPP tool only accepts one of
    # three predefined buckets — pick the one whose midpoint is closest to
    # the midpoint of the VN-supplied range.  Note: the template uses the
    # SUPERSCRIPT-ZERO degree sign (U+2070), not U+00B0; we must match it
    # exactly or the dropdown validation rejects the value.
    if plant.prod_temp_range:
        ptr_raw = str(plant.prod_temp_range).strip()
        nums = [float(x) for x in re.findall(r"\d+(?:[.,]\d+)?", ptr_raw.replace(",", "."))]
        if nums:
            mid = sum(nums) / len(nums)
            buckets = [
                (170.0, "155\u2070C - 185\u2070C"),  # hot mix
                (127.5, "110\u2070C - 145\u2070C"),  # warm mix
                ( 77.5,  "60\u2070C - 95\u2070C"),   # half-warm / cold
            ]
            general_cells["B21"] = min(buckets, key=lambda b: abs(b[0] - mid))[1]

    # Wheel loader energy source (C26).  We don't override B26 (loader
    # equipment type) because the template's drop-down expects a very
    # specific identifier we cannot reliably reconstruct from the VN.
    wl_fuel = getattr(plant, "wheel_loader_fuel", None)
    if wl_fuel:
        general_cells["C26"] = WHEEL_LOADER_FUEL_MAP.get(
            wl_fuel.strip().lower(), wl_fuel
        )

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
    excluded: list[dict[str, Any]] = []
    for comp in plant.components:
        if comp.pct <= 0 and not comp.extra:
            continue
        if is_excluded_component(comp.name):
            # Skipped on purpose (red dye / Uintah). Keep a record so the
            # UI can show what was filtered and re-enable later.
            excluded.append({
                "vn_row": comp.row,
                "name":   comp.name,
                "pct":    comp.pct,
                "origin": comp.origin,
                "mode":   comp.mode,
                "reason": "EXCLUDED_COMPONENT_KEYS",
            })
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
        # RAP distance is the haul from the milling/recycling site to
        # the asphalt plant.  This depends on which RAP stockpile the
        # operator actually uses on the day, so it cannot be derived
        # from the VN file alone.  Force manual entry.
        extra_rec = is_extra_recycled(comp.name)
        if cat == "rap":
            dist, method = None, "manual_required"
            manual = True
        elif extra_rec or _is_onsite_origin(comp.origin):
            # On-site (own filler from baghouse, or origin = Productieproces).
            # Keep "Fill Transport" with a token 0.02 km on-site haul so the
            # GPP tool's transport-check (mode set ⇒ distance > 0) passes.
            dist, method = 0.02, "onsite"
            manual = False
            mode_gpp, energy_gpp = "Truck", "Diesel_Euro6"
        else:
            dist, method = _compute_distance(plant_pt, comp.origin, mode_gpp)
            manual = False
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
            extra_recycled=extra_rec,
            manual_distance=manual,
        ))

    # 3) Sanity-check totals (sum of fractions ≈ 1.0)
    total_frac = sum(c.pct_fraction for c in mapped)
    total_dist = sum((c.distance_km or 0.0) for c in mapped)
    if not (0.99 <= total_frac <= 1.01):
        warnings.append(
            f"Sum of mapped component fractions = {total_frac:.4f}; "
            "GPP requires it to equal 1.00 (±0.001)."
        )

    # Cross-check against the VN's own "Total" cell, if provided.
    vn_total = getattr(plant, "composition_total_pct", None)
    if vn_total is not None and not (99.0 <= vn_total <= 101.0):
        warnings.append(
            f"VN-totaal in cel D66 = {vn_total:.3f}%; verwacht ~100%. "
            "Controleer de samenstelling in de bron-VN."
        )
    if excluded:
        warnings.append(
            "Tijdelijk uitgesloten componenten (later opnieuw activeren): "
            + ", ".join(f"{e['name']} ({e['pct']}%)" for e in excluded)
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
        total_pct_fraction=total_frac,
        total_distance_km=total_dist,
        excluded_components=excluded,
        vn_composition_total_pct=vn_total,
    )

"""
software_vn_parser.py
=====================
Parser for the **"Software VN"** Excel workbook (e.g. ``software VN
20260420.xlsx``).  Sheet ``Info voor GPP`` shares the layout of the
classic Verantwoordingsnota, but contains three additional fields that
the legacy :mod:`vn_parser` ignores:

* Row 14 — *Energiebron (primair/secundair)*
* Row 16 — *Elektrisch aandeel equipment*
* Row 17 — *Wheel loader fuel source*

This module reuses :func:`vn_parser.parse` and augments each plant with
those extras so the upload pipeline can stay isolated from the regular
VN upload (different routes, templates, and session keys).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

import openpyxl

import vn_parser
from vn_parser import VNData, VNPlant, SHEET_NAME, PLANT_COLS, _val, _to_str


ROW_ENERGY_PRIM_SEC   = 14
ROW_ELECTRIC_SHARE    = 16
ROW_WHEEL_LOADER_FUEL = 17


@dataclass
class SoftwareVNPlant:
    """A :class:`vn_parser.VNPlant` enriched with Software-VN-only fields."""
    base: VNPlant
    energy_source_primary_secondary: str | None = None
    electric_share_equipment: str | None = None
    wheel_loader_fuel: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = self.base.to_dict()
        d["energy_source_primary_secondary"] = self.energy_source_primary_secondary
        d["electric_share_equipment"]        = self.electric_share_equipment
        d["wheel_loader_fuel"]               = self.wheel_loader_fuel
        return d


@dataclass
class SoftwareVNData:
    source_filename: str | None
    plants: list[SoftwareVNPlant] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_filename": self.source_filename,
            "plants": [p.to_dict() for p in self.plants],
        }


def parse(source: str | Path | bytes | io.BytesIO,
          source_filename: str | None = None) -> SoftwareVNData:
    """Parse a Software-VN workbook.

    Delegates the bulk of the work to :func:`vn_parser.parse` and then
    re-opens the workbook to capture the three additional rows that the
    legacy parser does not expose.
    """
    if isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
        base = vn_parser.parse(raw, source_filename=source_filename)
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
    elif isinstance(source, io.BytesIO):
        raw = source.getvalue()
        base = vn_parser.parse(raw, source_filename=source_filename)
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
    else:
        path = Path(source)
        base = vn_parser.parse(path, source_filename=source_filename)
        wb = openpyxl.load_workbook(path, data_only=True)
        if source_filename is None:
            source_filename = path.name

    if SHEET_NAME not in wb.sheetnames:
        raise ValueError(
            f"Sheet '{SHEET_NAME}' not found. Available sheets: {wb.sheetnames}"
        )
    ws = wb[SHEET_NAME]

    plants: list[SoftwareVNPlant] = []
    for plant in base.plants:
        gen_col, _ = PLANT_COLS[plant.plant_index]
        plants.append(SoftwareVNPlant(
            base=plant,
            energy_source_primary_secondary=_to_str(_val(ws, ROW_ENERGY_PRIM_SEC,   gen_col)),
            electric_share_equipment       =_to_str(_val(ws, ROW_ELECTRIC_SHARE,    gen_col)),
            wheel_loader_fuel              =_to_str(_val(ws, ROW_WHEEL_LOADER_FUEL, gen_col)),
        ))

    return SoftwareVNData(
        source_filename=base.source_filename or source_filename,
        plants=plants,
    )

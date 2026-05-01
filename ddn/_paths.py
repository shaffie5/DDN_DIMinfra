"""Single source of truth for project filesystem paths.

All modules under ``ddn/`` should derive paths from here instead of
deriving them from their own ``__file__`` location. This keeps moves
inside the package (and any future renames) safe.
"""

from __future__ import annotations

from pathlib import Path

# This file lives at <repo>/ddn/_paths.py — go up one level for repo root.
BASE_DIR: Path = Path(__file__).resolve().parent.parent

DATA_DIR: Path = BASE_DIR / "data"
OUTPUT_DIR: Path = BASE_DIR / "output"
GPP_LINK_DIR: Path = BASE_DIR / "gpp_link"

# Convenience sub-paths
WATERWAY_CACHE_DIR: Path = DATA_DIR / "waterway_cache"
QUAY_CACHE_DIR: Path = WATERWAY_CACHE_DIR / "quays"
WATERWAY_NETWORK_CACHE_DIR: Path = WATERWAY_CACHE_DIR / "networks"
GEOCODE_OVERRIDES_PATH: Path = DATA_DIR / "geocode_overrides.json"
WATERWAY_TERMINALS_PATH: Path = DATA_DIR / "waterway_terminals.json"
GPP_TEMPLATE_PATH: Path = GPP_LINK_DIR / "PIONEERS GPP TOOL_20260310.xlsx"

__all__ = [
    "BASE_DIR",
    "DATA_DIR",
    "OUTPUT_DIR",
    "GPP_LINK_DIR",
    "WATERWAY_CACHE_DIR",
    "QUAY_CACHE_DIR",
    "WATERWAY_NETWORK_CACHE_DIR",
    "GEOCODE_OVERRIDES_PATH",
    "WATERWAY_TERMINALS_PATH",
    "GPP_TEMPLATE_PATH",
]

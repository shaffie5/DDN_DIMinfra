from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("ddn.geo")

# Tunables (env-driven so deployment can adjust without code changes).
OSRM_TIMEOUT_S = float(os.getenv("OSRM_TIMEOUT_S", "10.0"))
NOMINATIM_TIMEOUT_S = float(os.getenv("NOMINATIM_TIMEOUT_S", "8.0"))
NOMINATIM_MIN_INTERVAL_S = float(os.getenv("NOMINATIM_MIN_INTERVAL_S", "1.0"))

_GEOCODE_CACHE: dict[str, "GeoPoint | None"] = {}
_GEOCODE_LOCK = threading.Lock()
_NOMINATIM_LOCK = threading.Lock()
_LAST_NOMINATIM_CALL = 0.0

_OVERRIDES_PATH = Path(__file__).resolve().parent / "data" / "geocode_overrides.json"
_OVERRIDES: dict[str, tuple[float, float]] | None = None


def _load_overrides() -> dict[str, tuple[float, float]]:
    """Lazy-load the manual geocode override table.

    File format: ``{"address string": [lat, lon], ...}``.  Keys starting
    with ``_`` are treated as comments.  Lookup is case- and
    whitespace-insensitive.
    """
    global _OVERRIDES
    if _OVERRIDES is not None:
        return _OVERRIDES
    out: dict[str, tuple[float, float]] = {}
    if _OVERRIDES_PATH.exists():
        try:
            raw = json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    out[k.strip().lower()] = (float(v[0]), float(v[1]))
        except Exception as e:
            log.warning("Failed to load geocode overrides %s: %s", _OVERRIDES_PATH, e)
    _OVERRIDES = out
    return out


def _nominatim_throttle() -> None:
    """Block until at least NOMINATIM_MIN_INTERVAL_S since the previous
    call. Required by Nominatim's usage policy (1 req/sec).
    """
    global _LAST_NOMINATIM_CALL
    with _NOMINATIM_LOCK:
        elapsed = time.time() - _LAST_NOMINATIM_CALL
        if elapsed < NOMINATIM_MIN_INTERVAL_S:
            time.sleep(NOMINATIM_MIN_INTERVAL_S - elapsed)
        _LAST_NOMINATIM_CALL = time.time()


def geocode(query: str, timeout_s: float | None = None) -> "GeoPoint | None":
    """Geocode a free-form address/place via Nominatim. Cached in-process.

    Looks up :data:`_OVERRIDES_PATH` first so manually pinned addresses
    bypass Nominatim entirely.  Returns None if the query cannot be
    resolved.
    """
    if timeout_s is None:
        timeout_s = NOMINATIM_TIMEOUT_S
    if not query or not query.strip():
        return None
    key = query.strip().lower()
    with _GEOCODE_LOCK:
        if key in _GEOCODE_CACHE:
            return _GEOCODE_CACHE[key]

    # 1) Manual override table (no network call, no rate-limit)
    overrides = _load_overrides()
    if key in overrides:
        lat, lon = overrides[key]
        gp = GeoPoint(lat=lat, lon=lon, label=f"{query} (override)")
        with _GEOCODE_LOCK:
            _GEOCODE_CACHE[key] = gp
        return gp

    # 2) Public Nominatim
    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "DDN-DIMinfra/1.0 (geocode)"}

    # Try the full query first; if it fails, progressively simplify
    # ("Company, City" → "City" → last whitespace token) so e.g.
    # "Ankersmit Maastricht" resolves to Maastricht.
    candidates: list[str] = [query.strip()]
    if "," in query:
        tail = query.rsplit(",", 1)[-1].strip()
        if tail and tail not in candidates:
            candidates.append(tail)
    parts = query.strip().split()
    if len(parts) > 1 and parts[-1] not in candidates:
        candidates.append(parts[-1])

    for q in candidates:
        params = {"q": q, "format": "json", "limit": 1}
        _nominatim_throttle()
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout_s)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                continue
            item = data[0]
            gp = GeoPoint(
                lat=float(item["lat"]),
                lon=float(item["lon"]),
                label=item.get("display_name") or query,
            )
            with _GEOCODE_LOCK:
                _GEOCODE_CACHE[key] = gp
            return gp
        except Exception as e:
            log.warning("Nominatim geocode failed for %r: %s", q, e)
            continue
    with _GEOCODE_LOCK:
        _GEOCODE_CACHE[key] = None
    return None


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float
    label: str | None = None


def _is_valid_lat_lon(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    # Fallback straight-line distance.
    r = 6371.0
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = math.radians(b.lat - a.lat)
    dlon = math.radians(b.lon - a.lon)

    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def osrm_route_km(a: GeoPoint, b: GeoPoint, timeout_s: float | None = None) -> tuple[float, float] | None:
    """Returns (distance_km, duration_min) for driving route using public OSRM.

    If OSRM fails, returns None and caller can fallback to haversine.
    """
    if timeout_s is None:
        timeout_s = OSRM_TIMEOUT_S
    if not (_is_valid_lat_lon(a.lat, a.lon) and _is_valid_lat_lon(b.lat, b.lon)):
        log.warning("osrm_route_km: invalid coordinates a=%s b=%s", a, b)
        return None

    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        f"{a.lon},{a.lat};{b.lon},{b.lat}"
        "?overview=false&alternatives=false&steps=false"
    )
    try:
        resp = requests.get(url, timeout=timeout_s)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        routes = data.get("routes")
        if not routes:
            return None
        route = routes[0]
        dist_km = float(route["distance"]) / 1000.0
        dur_min = float(route["duration"]) / 60.0
        return dist_km, dur_min
    except Exception as e:
        log.warning("OSRM route failed: %s", e)
        return None


def osrm_route_geometry(
    a: GeoPoint, b: GeoPoint, timeout_s: float | None = None,
) -> list[tuple[float, float]] | None:
    """Return the full driving route as a list of (lat, lon) waypoints.

    Uses the public OSRM demo server with full geometry.
    Returns None on failure.
    """
    if timeout_s is None:
        timeout_s = OSRM_TIMEOUT_S
    if not (_is_valid_lat_lon(a.lat, a.lon) and _is_valid_lat_lon(b.lat, b.lon)):
        log.warning("osrm_route_geometry: invalid coordinates a=%s b=%s", a, b)
        return None

    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        f"{a.lon},{a.lat};{b.lon},{b.lat}"
        "?overview=full&geometries=geojson&alternatives=false&steps=false"
    )
    try:
        resp = requests.get(url, timeout=timeout_s)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        routes = data.get("routes")
        if not routes:
            return None
        coords = routes[0]["geometry"]["coordinates"]
        # OSRM returns [lon, lat]; convert to [lat, lon] for folium
        return [(float(c[1]), float(c[0])) for c in coords]
    except Exception as e:
        log.warning("OSRM geometry failed: %s", e)
        return None

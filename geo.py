from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import requests


_GEOCODE_CACHE: dict[str, "GeoPoint | None"] = {}


def geocode(query: str, timeout_s: float = 8.0) -> "GeoPoint | None":
    """Geocode a free-form address/place via Nominatim. Cached in-process.

    Returns None if the query cannot be resolved.
    """
    if not query or not query.strip():
        return None
    key = query.strip().lower()
    if key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[key]
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 1}
    headers = {"User-Agent": "DDN-DIMinfra/1.0 (geocode)"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout_s)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            _GEOCODE_CACHE[key] = None
            return None
        item = data[0]
        gp = GeoPoint(
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            label=item.get("display_name") or query,
        )
        _GEOCODE_CACHE[key] = gp
        # be polite to the public Nominatim service
        time.sleep(1.0)
        return gp
    except Exception:
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


def osrm_route_km(a: GeoPoint, b: GeoPoint, timeout_s: float = 10.0) -> tuple[float, float] | None:
    """Returns (distance_km, duration_min) for driving route using public OSRM.

    If OSRM fails, returns None and caller can fallback to haversine.
    """
    if not (_is_valid_lat_lon(a.lat, a.lon) and _is_valid_lat_lon(b.lat, b.lon)):
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
    except Exception:
        return None


def osrm_route_geometry(
    a: GeoPoint, b: GeoPoint, timeout_s: float = 10.0,
) -> list[tuple[float, float]] | None:
    """Return the full driving route as a list of (lat, lon) waypoints.

    Uses the public OSRM demo server with full geometry.
    Returns None on failure.
    """
    if not (_is_valid_lat_lon(a.lat, a.lon) and _is_valid_lat_lon(b.lat, b.lon)):
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
    except Exception:
        return None

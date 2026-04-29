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


# ─────────────────────────────────────────────────────────────────────
#  Inland-waterway / sea routing (Barge / Ship)
# ─────────────────────────────────────────────────────────────────────

# Disk cache: routed geometries never change for a given coordinate pair
# + mode, and external routers (searoute, BRouter) are slow / rate-limited.
_WATERWAY_CACHE_DIR = Path(__file__).resolve().parent / "data" / "waterway_cache"
_WATERWAY_OVERRIDES_PATH = (
    Path(__file__).resolve().parent / "data" / "waterway_terminals.json"
)
_WATERWAY_OVERRIDES: dict[str, tuple[float, float]] | None = None

# Optional self-hosted BRouter for inland waterways. When the env var
# BROUTER_URL is set (e.g. http://127.0.0.1:17777) we try BRouter first
# with the bundled barge.brf profile, then fall back to searoute.
BROUTER_URL = os.getenv("BROUTER_URL", "").rstrip("/")
BROUTER_PROFILE = os.getenv("BROUTER_PROFILE", "barge")
BROUTER_TIMEOUT_S = float(os.getenv("BROUTER_TIMEOUT_S", "12.0"))

try:
    import searoute as _searoute  # type: ignore
    _SEAROUTE_AVAILABLE = True
except Exception:  # pragma: no cover - optional dep
    _searoute = None
    _SEAROUTE_AVAILABLE = False


def _load_waterway_overrides() -> dict[str, tuple[float, float]]:
    """Lazy-load manual terminal overrides.

    File format (``data/waterway_terminals.json``)::

        {
          "Genk": [50.9655, 5.5001],
          "Soignies": [50.5792, 4.0686]
        }

    The lookup key is the free-form origin string after lower-casing and
    trimming.  Use this to snap a quarry to its nearest navigable quay
    when Nominatim returns the office address rather than the loading
    point.
    """
    global _WATERWAY_OVERRIDES
    if _WATERWAY_OVERRIDES is not None:
        return _WATERWAY_OVERRIDES
    out: dict[str, tuple[float, float]] = {}
    if _WATERWAY_OVERRIDES_PATH.exists():
        try:
            raw = json.loads(_WATERWAY_OVERRIDES_PATH.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    out[k.strip().lower()] = (float(v[0]), float(v[1]))
        except Exception as e:
            log.warning("Failed to load waterway overrides %s: %s",
                        _WATERWAY_OVERRIDES_PATH, e)
    _WATERWAY_OVERRIDES = out
    return out


def waterway_terminal_for(label: str | None) -> "GeoPoint | None":
    """Return the manual terminal/quay coordinate for ``label`` if defined."""
    if not label:
        return None
    overrides = _load_waterway_overrides()
    hit = overrides.get(label.strip().lower())
    if hit is None:
        return None
    return GeoPoint(lat=hit[0], lon=hit[1], label=f"{label} (waterway terminal)")


def _waterway_cache_key(a: "GeoPoint", b: "GeoPoint", mode: str) -> Path:
    import hashlib
    raw = f"{mode}|{a.lat:.5f},{a.lon:.5f}|{b.lat:.5f},{b.lon:.5f}"
    h = hashlib.sha1(raw.encode()).hexdigest()
    return _WATERWAY_CACHE_DIR / f"{h}.json"


def _waterway_cache_load(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _waterway_cache_save(path: Path, payload: dict) -> None:
    try:
        _WATERWAY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as e:
        log.warning("waterway cache write failed: %s", e)


def waterway_route_geometry(
    a: "GeoPoint", b: "GeoPoint", mode: str = "Barge",
) -> tuple[list[tuple[float, float]] | None, float | None, str]:
    """Return ``(coords, length_km, source)`` for an inland-waterway leg.

    ``coords`` is a list of ``(lat, lon)`` waypoints suitable for Leaflet,
    or ``None`` on failure (caller should fall back to a straight line).
    ``length_km`` is the routed great-distance in km when available.
    ``source`` is one of ``"cache"``, ``"brouter"``, ``"searoute"``,
    ``"none"``.

    Routing hierarchy:

      1. Disk cache (instant, offline).
      2. Self-hosted BRouter on the bundled ``barge.brf`` waterway
         profile, if ``BROUTER_URL`` is set.  This is the most
         accurate option for Belgian / Dutch inland canals.
      3. The public ``searoute`` package (offline, MIT-licensed) — a
         maritime router with sparse inland coverage.  Results are
         length-checked: anything more than 3× the great-circle
         distance is rejected.
      4. ``None`` → caller draws a dashed straight line.
    """
    if not (_is_valid_lat_lon(a.lat, a.lon) and _is_valid_lat_lon(b.lat, b.lon)):
        return None, None, "none"

    cache_path = _waterway_cache_key(a, b, mode)
    cached = _waterway_cache_load(cache_path)
    if cached and cached.get("coords"):
        return (
            [tuple(c) for c in cached["coords"]],
            cached.get("length_km"),
            "cache",
        )

    crow_km = haversine_km(a, b)

    # 2) BRouter (self-hosted, OSM waterway profile) — preferred.
    if BROUTER_URL:
        bcoords, blen = _brouter_route(a, b)
        if bcoords and (not blen or crow_km == 0 or blen <= 3.0 * crow_km):
            _waterway_cache_save(cache_path, {
                "coords": bcoords, "length_km": blen, "mode": mode,
                "source": "brouter",
            })
            return bcoords, blen, "brouter"

    # 3) searoute fallback (maritime).
    if _SEAROUTE_AVAILABLE:
        try:
            feat = _searoute.searoute([a.lon, a.lat], [b.lon, b.lat])
            coords_lonlat = feat["geometry"]["coordinates"]
            coords = [(float(c[1]), float(c[0])) for c in coords_lonlat]
            length_km = float(feat["properties"].get("length") or 0.0) or None
            if length_km and crow_km > 0 and length_km > 3.0 * crow_km:
                log.info("searoute route rejected (routed=%.1fkm vs crow=%.1fkm,"
                         " ratio %.1f×)", length_km, crow_km, length_km / crow_km)
            else:
                _waterway_cache_save(cache_path, {
                    "coords": coords, "length_km": length_km, "mode": mode,
                    "source": "searoute",
                })
                return coords, length_km, "searoute"
        except Exception as e:
            log.warning("searoute waterway route failed (%s → %s): %s", a, b, e)

    return None, None, "none"


def _brouter_route(
    a: "GeoPoint", b: "GeoPoint",
) -> tuple[list[tuple[float, float]] | None, float | None]:
    """Query a self-hosted BRouter instance for a waterway route.

    Returns ``(coords_lat_lon, length_km)`` or ``(None, None)`` on
    failure.  Requires ``BROUTER_URL`` env var pointing at the BRouter
    HTTP endpoint (default port 17777) and the ``barge`` profile to
    be present in the BRouter profiles directory.
    """
    url = (
        f"{BROUTER_URL}/brouter"
        f"?lonlats={a.lon:.6f},{a.lat:.6f}|{b.lon:.6f},{b.lat:.6f}"
        f"&profile={BROUTER_PROFILE}"
        "&alternativeidx=0&format=geojson"
    )
    try:
        resp = requests.get(url, timeout=BROUTER_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
        feats = data.get("features") or []
        if not feats:
            return None, None
        geom = feats[0].get("geometry") or {}
        coords_lonlat = geom.get("coordinates") or []
        if len(coords_lonlat) < 2:
            return None, None
        coords = [(float(c[1]), float(c[0])) for c in coords_lonlat]
        props = feats[0].get("properties") or {}
        length_km: float | None = None
        try:
            length_km = float(props.get("track-length") or 0.0) / 1000.0
            if length_km <= 0:
                length_km = None
        except (TypeError, ValueError):
            length_km = None
        return coords, length_km
    except Exception as e:
        log.warning("BRouter waterway route failed (%s → %s): %s", a, b, e)
        return None, None

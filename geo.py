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


# ─── Overpass-based quay finder ────────────────────────────────────────
#
# When no manual override is configured we ask Overpass for nearby
# inland-waterway loading/unloading points and return the closest one
# within the configured search radius.

OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
OVERPASS_TIMEOUT_S = float(os.getenv("OVERPASS_TIMEOUT_S", "20.0"))
QUAY_SEARCH_RADIUS_KM = float(os.getenv("QUAY_SEARCH_RADIUS_KM", "20.0"))
_QUAY_CACHE_DIR = Path(__file__).resolve().parent / "data" / "waterway_cache" / "quays"


def _quay_cache_path(lat: float, lon: float, radius_km: float) -> Path:
    import hashlib
    raw = f"{lat:.4f},{lon:.4f}|{radius_km:.1f}"
    return _QUAY_CACHE_DIR / f"{hashlib.sha1(raw.encode()).hexdigest()}.json"


def find_nearest_quay(
    pt: "GeoPoint", radius_km: float | None = None,
) -> "GeoPoint | None":
    """Find the nearest inland-waterway loading/unloading quay to ``pt``.

    Queries Overpass for OSM features tagged as quays / docks / piers /
    ports / harbours within ``radius_km`` (default
    ``QUAY_SEARCH_RADIUS_KM``, 20 km) and returns the closest one as a
    :class:`GeoPoint`, or ``None`` if nothing is found.

    Results are cached on disk per (lat, lon, radius) tuple so repeated
    lookups for the same quarry/plant never re-query Overpass.
    """
    if not _is_valid_lat_lon(pt.lat, pt.lon):
        return None
    radius_km = radius_km if radius_km is not None else QUAY_SEARCH_RADIUS_KM
    cache_path = _quay_cache_path(pt.lat, pt.lon, radius_km)
    cached = _waterway_cache_load(cache_path)
    if cached is not None:
        if cached.get("quay") is None:
            return None
        q = cached["quay"]
        return GeoPoint(lat=q["lat"], lon=q["lon"], label=q.get("label"))

    radius_m = int(radius_km * 1000)
    # We accept anything that strongly implies an inland transhipment
    # point: explicit quay / dock / pier / port tags, plus generic
    # mooring + harbour features.
    query = f"""
[out:json][timeout:{int(OVERPASS_TIMEOUT_S)}];
(
  node(around:{radius_m},{pt.lat},{pt.lon})["waterway"="dock"];
  way(around:{radius_m},{pt.lat},{pt.lon})["waterway"="dock"];
  node(around:{radius_m},{pt.lat},{pt.lon})["man_made"="pier"];
  way(around:{radius_m},{pt.lat},{pt.lon})["man_made"="pier"];
  node(around:{radius_m},{pt.lat},{pt.lon})["man_made"="quay"];
  way(around:{radius_m},{pt.lat},{pt.lon})["man_made"="quay"];
  node(around:{radius_m},{pt.lat},{pt.lon})["industrial"="port"];
  way(around:{radius_m},{pt.lat},{pt.lon})["industrial"="port"];
  node(around:{radius_m},{pt.lat},{pt.lon})["landuse"="port"];
  way(around:{radius_m},{pt.lat},{pt.lon})["landuse"="port"];
  node(around:{radius_m},{pt.lat},{pt.lon})["harbour"];
  way(around:{radius_m},{pt.lat},{pt.lon})["harbour"];
);
out center tags 50;
""".strip()

    candidates: list[dict[str, Any]] = []
    try:
        # Overpass returns 406 if the User-Agent is the bare
        # python-requests/* default (their abuse policy), so identify
        # ourselves explicitly and ask for JSON.
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers={
                "User-Agent": "DDN-DIMinfra/1.0 (waterway-quay-finder)",
                "Accept": "application/json",
            },
            timeout=OVERPASS_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        for el in data.get("elements", []):
            if el.get("type") == "node":
                lat, lon = el.get("lat"), el.get("lon")
            else:  # way / relation: use the centroid
                c = el.get("center") or {}
                lat, lon = c.get("lat"), c.get("lon")
            if lat is None or lon is None:
                continue
            tags = el.get("tags", {}) or {}
            name = (tags.get("name") or tags.get("ref")
                    or tags.get("waterway") or tags.get("man_made")
                    or tags.get("industrial") or tags.get("harbour")
                    or "quay")
            candidates.append({
                "lat": float(lat), "lon": float(lon),
                "name": str(name),
                "dist_km": haversine_km(
                    pt, GeoPoint(lat=float(lat), lon=float(lon)),
                ),
            })
    except Exception as e:
        log.warning("Overpass quay query failed near (%.4f,%.4f): %s",
                    pt.lat, pt.lon, e)
        # Don't cache transient network failures.
        return None

    if not candidates:
        _waterway_cache_save(cache_path, {"quay": None})
        return None

    candidates.sort(key=lambda x: x["dist_km"])
    best = candidates[0]
    _waterway_cache_save(cache_path, {"quay": {
        "lat": best["lat"], "lon": best["lon"],
        "label": f"{best['name']} (~{best['dist_km']:.1f} km)",
    }})
    return GeoPoint(lat=best["lat"], lon=best["lon"],
                    label=f"{best['name']} (~{best['dist_km']:.1f} km)")


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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as e:
        log.warning("waterway cache write failed: %s", e)


# ─── Overpass-based inland waterway router ─────────────────────────────
#
# Builds a NetworkX graph from waterway=canal/river/fairway segments
# returned by Overpass for the bounding box that covers both quays
# (with a margin), then runs Dijkstra to get the actual canal route.
# This is what makes Belgian inland routes (Albertkanaal, Maas, Schelde,
# Canal Nimy-Blaton) follow the real water — without needing BRouter
# or Docker.

_OVERPASS_NETWORK_CACHE_DIR = (
    Path(__file__).resolve().parent / "data" / "waterway_cache" / "networks"
)
_INLAND_BBOX_MARGIN_DEG = float(os.getenv("INLAND_BBOX_MARGIN_DEG", "0.20"))
_INLAND_SNAP_MAX_KM = float(os.getenv("INLAND_SNAP_MAX_KM", "8.0"))


def _bbox_cache_path(min_lat: float, min_lon: float,
                     max_lat: float, max_lon: float) -> Path:
    import hashlib
    raw = f"{min_lat:.2f},{min_lon:.2f},{max_lat:.2f},{max_lon:.2f}"
    return _OVERPASS_NETWORK_CACHE_DIR / f"{hashlib.sha1(raw.encode()).hexdigest()}.json"


def _fetch_inland_waterways(min_lat: float, min_lon: float,
                            max_lat: float, max_lon: float
                            ) -> list[list[tuple[float, float]]] | None:
    """Return a list of waterway polylines (lat,lon) inside the bbox.

    Cached on disk per bbox.  ``None`` means the Overpass query failed
    transiently (so the caller should not poison the cache).
    """
    cache = _bbox_cache_path(min_lat, min_lon, max_lat, max_lon)
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            return [[(float(p[0]), float(p[1])) for p in line]
                    for line in data.get("lines", [])]
        except Exception:
            pass

    query = f"""
[out:json][timeout:{int(OVERPASS_TIMEOUT_S)}];
(
  way({min_lat},{min_lon},{max_lat},{max_lon})["waterway"="canal"];
  way({min_lat},{min_lon},{max_lat},{max_lon})["waterway"="river"];
  way({min_lat},{min_lon},{max_lat},{max_lon})["waterway"="fairway"];
);
out geom;
""".strip()

    try:
        resp = requests.post(
            OVERPASS_URL, data={"data": query},
            headers={
                "User-Agent": "DDN-DIMinfra/1.0 (waterway-router)",
                "Accept": "application/json",
            },
            timeout=OVERPASS_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("Overpass waterway-network query failed for bbox "
                    "(%.2f,%.2f,%.2f,%.2f): %s",
                    min_lat, min_lon, max_lat, max_lon, e)
        return None

    lines: list[list[tuple[float, float]]] = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        lines.append([(float(g["lat"]), float(g["lon"])) for g in geom])

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"lines": lines}), encoding="utf-8")
    except OSError as e:
        log.warning("waterway-network cache write failed: %s", e)
    return lines


def _overpass_inland_route(
    a: "GeoPoint", b: "GeoPoint",
) -> tuple[list[tuple[float, float]] | None, float | None]:
    """Build a NetworkX graph from local OSM waterways and shortest-path it.

    Returns ``(coords, length_km)`` or ``(None, None)`` if Overpass
    failed, NetworkX is unavailable, or no path exists between the
    snapped endpoints.
    """
    try:
        import networkx as nx
    except ImportError:
        return None, None

    margin = _INLAND_BBOX_MARGIN_DEG
    min_lat = min(a.lat, b.lat) - margin
    max_lat = max(a.lat, b.lat) + margin
    min_lon = min(a.lon, b.lon) - margin
    max_lon = max(a.lon, b.lon) + margin

    lines = _fetch_inland_waterways(min_lat, min_lon, max_lat, max_lon)
    if not lines:
        return None, None

    # Quantise nodes to ~11 m so adjoining ways share endpoints in the
    # graph (OSM ways don't always reuse the same node IDs at junctions
    # when fetched with `out geom`).
    def key(lat: float, lon: float) -> tuple[int, int]:
        return (round(lat * 1e4), round(lon * 1e4))

    nodes: dict[tuple[int, int], tuple[float, float]] = {}
    g = nx.Graph()
    for line in lines:
        prev_k = None
        for lat, lon in line:
            k = key(lat, lon)
            if k not in nodes:
                nodes[k] = (lat, lon)
            if prev_k is not None and prev_k != k:
                d = haversine_km(
                    GeoPoint(lat=nodes[prev_k][0], lon=nodes[prev_k][1]),
                    GeoPoint(lat=lat, lon=lon),
                )
                if g.has_edge(prev_k, k):
                    if d < g[prev_k][k]["weight"]:
                        g[prev_k][k]["weight"] = d
                else:
                    g.add_edge(prev_k, k, weight=d)
            prev_k = k

    if g.number_of_nodes() == 0:
        return None, None

    def nearest_node(pt: "GeoPoint") -> tuple[tuple[int, int], float]:
        best_k = None
        best_d = float("inf")
        for k, (lat, lon) in nodes.items():
            d = haversine_km(pt, GeoPoint(lat=lat, lon=lon))
            if d < best_d:
                best_d, best_k = d, k
        return best_k, best_d

    src, src_d = nearest_node(a)
    dst, dst_d = nearest_node(b)
    if src is None or dst is None:
        return None, None
    if src_d > _INLAND_SNAP_MAX_KM or dst_d > _INLAND_SNAP_MAX_KM:
        log.info("Overpass inland router: quay snap too far "
                 "(src=%.1f km, dst=%.1f km > %.0f km)",
                 src_d, dst_d, _INLAND_SNAP_MAX_KM)
        return None, None

    try:
        path = nx.shortest_path(g, src, dst, weight="weight")
    except nx.NetworkXNoPath:
        log.info("Overpass inland router: no waterway path between snapped "
                 "endpoints (graph has %d nodes / %d edges)",
                 g.number_of_nodes(), g.number_of_edges())
        return None, None
    except Exception as e:
        log.warning("Overpass inland router: pathfinding error: %s", e)
        return None, None

    coords = [nodes[k] for k in path]
    length_km = sum(g[path[i]][path[i + 1]]["weight"] for i in range(len(path) - 1))
    return coords, length_km


def waterway_route_geometry(
    a: "GeoPoint", b: "GeoPoint", mode: str = "Barge",
) -> tuple[list[tuple[float, float]] | None, float | None, str]:
    """Return ``(coords, length_km, source)`` for an inland-waterway leg.

    ``coords`` is a list of ``(lat, lon)`` waypoints suitable for Leaflet,
    or ``None`` on failure (caller should fall back to a straight line).
    ``length_km`` is the routed great-distance in km when available.
    ``source`` is one of ``"cache"``, ``"brouter"``, ``"overpass"``,
    ``"searoute"``, ``"none"``.

    Routing hierarchy (mode-aware):

      * **Barge** (inland canals): cache → BRouter → Overpass-graph
        (OSM ``waterway=canal|river|fairway`` + Dijkstra) → searoute
        (low snap tolerance) → ``None``.
      * **Ship** (coastal / transoceanic / sea-going): cache → searoute
        (high snap tolerance for deep-water ports) → BRouter →
        Overpass-graph → ``None``.

    For Ship mode the snap tolerance defaults to
    ``SEAROUTE_SHIP_MAX_SNAP_KM`` (60 km) since container / bulk
    terminals are routinely 20–50 km up an estuary (Antwerp on the
    Schelde, Hamburg on the Elbe, Rotterdam on the Nieuwe Maas, etc.)
    and the global maritime network's nearest node is on the open
    coast.
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
    is_ship = (mode or "").strip().lower() == "ship"

    def _try_brouter():
        if not BROUTER_URL:
            return None
        bcoords, blen = _brouter_route(a, b)
        if bcoords and (not blen or crow_km == 0 or blen <= 3.0 * crow_km):
            _waterway_cache_save(cache_path, {
                "coords": bcoords, "length_km": blen, "mode": mode,
                "source": "brouter",
            })
            return bcoords, blen, "brouter"
        return None

    def _try_inland():
        ocoords, olen = _overpass_inland_route(a, b)
        if ocoords and (not olen or crow_km == 0 or olen <= 4.0 * crow_km):
            coords = [(a.lat, a.lon)] + ocoords + [(b.lat, b.lon)]
            _waterway_cache_save(cache_path, {
                "coords": coords, "length_km": olen, "mode": mode,
                "source": "overpass",
            })
            return coords, olen, "overpass"
        return None

    def _try_searoute():
        if not _SEAROUTE_AVAILABLE:
            return None
        try:
            feat = _searoute.searoute([a.lon, a.lat], [b.lon, b.lat])
            coords_lonlat = feat["geometry"]["coordinates"]
            coords = [(float(c[1]), float(c[0])) for c in coords_lonlat]
            length_km = float(feat["properties"].get("length") or 0.0) or None
            snap_a = haversine_km(a, GeoPoint(lat=coords[0][0], lon=coords[0][1])) if coords else None
            snap_b = haversine_km(b, GeoPoint(lat=coords[-1][0], lon=coords[-1][1])) if coords else None
            # Coastal/sea-going ships tolerate a much larger snap than
            # inland barges — global maritime nodes are sparse and deep-
            # water ports are commonly tens of km up an estuary.
            if is_ship:
                max_snap = float(os.getenv("SEAROUTE_SHIP_MAX_SNAP_KM", "60.0"))
                max_ratio = 2.5  # transoceanic detours are real (Cape, Suez…)
            else:
                max_snap = float(os.getenv("SEAROUTE_MAX_SNAP_KM", "10.0"))
                max_ratio = 3.0
            if (snap_a is not None and snap_a > max_snap) or \
               (snap_b is not None and snap_b > max_snap):
                log.info("searoute rejected (endpoint snap %.1f / %.1f km "
                         "exceeds %.0f km for mode=%s)",
                         snap_a or 0.0, snap_b or 0.0, max_snap, mode)
                return None
            if length_km and crow_km > 0 and length_km > max_ratio * crow_km:
                log.info("searoute rejected (routed=%.1fkm vs crow=%.1fkm,"
                         " ratio %.1f× exceeds %.1f× for mode=%s)",
                         length_km, crow_km, length_km / crow_km, max_ratio, mode)
                return None
            # Splice exact quay coords so the polyline visibly touches
            # the orange quay markers; the connector is a short straight
            # segment to/from the maritime network entry point.
            coords = [(a.lat, a.lon)] + coords + [(b.lat, b.lon)]
            _waterway_cache_save(cache_path, {
                "coords": coords, "length_km": length_km, "mode": mode,
                "source": "searoute",
            })
            return coords, length_km, "searoute"
        except Exception as e:
            log.warning("searoute waterway route failed (%s → %s): %s", a, b, e)
            return None

    if is_ship:
        # Maritime / transoceanic first, inland canal fallback for the
        # rare port-to-port-via-canal case (e.g. Rhine-bound coasters).
        order = (_try_searoute, _try_brouter, _try_inland)
    else:
        # Inland canals first (Barge / default), maritime as last resort
        # for short-sea coastal hops the inland network can't model.
        order = (_try_brouter, _try_inland, _try_searoute)

    for step in order:
        result = step()
        if result is not None:
            return result

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

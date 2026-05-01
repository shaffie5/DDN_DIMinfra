"""Geocoding and multimodal-routing primitives for the DDN tool.

Provides:

* :func:`geocode` – free-form address → :class:`GeoPoint`, backed by
  (1) a manual override JSON, (2) a GeoNames-derived name index with
  fuzzy / article-stripped fallbacks, and (3) optional Nominatim live
  lookup when ``DDN_OFFLINE`` is unset.
* :func:`osrm_route_km` – road distance via the configured OSRM
  endpoint, with haversine fallback on error / 4xx / 5xx.
* :func:`waterway_route_km` and helpers – inland-waterway routing via
  Overpass + NetworkX shortest path; sea-leg fallback via ``searoute``.
* :func:`find_nearest_quay` – picks the closest navigable
  loading/unloading point to a given coordinate, combining a manual
  pin file with on-the-fly Overpass queries.

All network calls are short-circuited when ``DDN_OFFLINE=1`` and all
results are aggressively cached (in-process + on-disk where useful).
"""
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
OSRM_URL = os.getenv("OSRM_URL", "https://router.project-osrm.org").rstrip("/")
OSRM_TIMEOUT_S = float(os.getenv("OSRM_TIMEOUT_S", "10.0"))
NOMINATIM_TIMEOUT_S = float(os.getenv("NOMINATIM_TIMEOUT_S", "8.0"))
NOMINATIM_MIN_INTERVAL_S = float(os.getenv("NOMINATIM_MIN_INTERVAL_S", "1.0"))

# Master switch: set DDN_OFFLINE=1 to disable all live network routing
# (OSRM road routing + Overpass inland-waterway graph). Defaults to live.
OFFLINE_MODE = os.getenv("DDN_OFFLINE", "0") == "1"

_GEOCODE_CACHE: dict[str, "GeoPoint | None"] = {}
_GEOCODE_LOCK = threading.Lock()
_NOMINATIM_LOCK = threading.Lock()
_LAST_NOMINATIM_CALL = 0.0

_OVERRIDES_PATH = Path(__file__).resolve().parent / "data" / "geocode_overrides.json"
_OVERRIDES: dict[str, tuple[float, float]] | None = None
# Secondary index: name-only -> (lat, lon) chosen from the most-preferred
# country in COUNTRY_PREFERENCE (so "genk" resolves to genk, BE not the US).
_NAME_INDEX: dict[str, tuple[float, float]] | None = None
# Tertiary index: aggressively normalised name (hyphens->space, articles
# stripped, multi-space collapsed) so user inputs like
# "Koudekerk-aan-de-Rijn" still match GeoNames "Koudekerk aan den Rijn".
_NAME_INDEX_NORM: dict[str, tuple[float, float]] | None = None

# Country-code preference for ambiguous bare-name lookups (lower index = higher priority).
COUNTRY_PREFERENCE: tuple[str, ...] = tuple(
    (os.getenv("DDN_GEOCODE_COUNTRY_PREFERENCE")
     or "be,nl,lu,fr,de,it,es,pl,no,dk,se").lower().split(",")
)

# Common articles / linking words to strip when fuzzy-matching place names.
_ARTICLE_TOKENS: frozenset[str] = frozenset({
    # Dutch
    "de", "den", "der", "het", "een", "aan", "op", "in", "te", "ten", "ter",
    # French
    "le", "la", "les", "l", "du", "des", "d", "au", "aux", "sur", "sous",
    # German
    "am", "im", "an", "auf", "bei", "vom", "zur", "zum",
    # English
    "the", "of", "on", "at",
})


def _aggr_norm(s: str) -> str:
    """Aggressive normalisation: lowercase, hyphen/apostrophe -> space,
    drop common articles, collapse whitespace.  Used as a last-resort
    fuzzy match between user input and the GeoNames-derived index.
    """
    if not s:
        return ""
    s = s.lower().replace("-", " ").replace("'", " ").replace("'", " ")
    toks = [t for t in s.split() if t and t not in _ARTICLE_TOKENS]
    return " ".join(toks)


def _load_overrides() -> dict[str, tuple[float, float]]:
    """Lazy-load the manual geocode override table.

    File format: ``{"address string": [lat, lon], ...}``.  Keys starting
    with ``_`` are treated as comments.  Lookup is case- and
    whitespace-insensitive.

    Also builds a secondary "name only" index so queries like ``"Genk"``
    can resolve against GeoNames-derived keys like ``"genk, be"``.  When
    the same bare name occurs in multiple countries, the entry from the
    earliest country in :data:`COUNTRY_PREFERENCE` wins.
    """
    global _OVERRIDES, _NAME_INDEX, _NAME_INDEX_NORM
    if _OVERRIDES is not None:
        return _OVERRIDES
    out: dict[str, tuple[float, float]] = {}
    name_best: dict[str, tuple[int, tuple[float, float]]] = {}
    norm_best: dict[str, tuple[int, tuple[float, float]]] = {}
    pref_rank = {cc: i for i, cc in enumerate(COUNTRY_PREFERENCE)}
    fallback_rank = len(COUNTRY_PREFERENCE) + 1
    if _OVERRIDES_PATH.exists():
        try:
            raw = json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if k.startswith("_"):
                    continue
                if not (isinstance(v, (list, tuple)) and len(v) == 2):
                    continue
                key = k.strip().lower()
                try:
                    coord = (float(v[0]), float(v[1]))
                except (TypeError, ValueError):
                    continue
                out[key] = coord
                # Build name-only index from "name, cc" keys.
                if "," in key:
                    name_part, _, cc_part = key.rpartition(",")
                    name_part = name_part.strip()
                    cc_part = cc_part.strip()
                    if name_part and len(cc_part) == 2:
                        rank = pref_rank.get(cc_part, fallback_rank)
                        cur = name_best.get(name_part)
                        if cur is None or rank < cur[0]:
                            name_best[name_part] = (rank, coord)
                        norm = _aggr_norm(name_part)
                        if norm and norm != name_part:
                            cur_n = norm_best.get(norm)
                            if cur_n is None or rank < cur_n[0]:
                                norm_best[norm] = (rank, coord)
        except Exception as e:
            log.warning("Failed to load geocode overrides %s: %s", _OVERRIDES_PATH, e)
    _OVERRIDES = out
    _NAME_INDEX = {n: c for n, (_, c) in name_best.items()}
    # Don't shadow exact name hits with the looser normalised form.
    _NAME_INDEX_NORM = {n: c for n, (_, c) in norm_best.items() if n not in _NAME_INDEX}
    log.info("Geocode overrides loaded: %d full keys, %d name-only fallbacks, %d normalised fallbacks",
             len(out), len(_NAME_INDEX), len(_NAME_INDEX_NORM))
    return out


def _name_index() -> dict[str, tuple[float, float]]:
    if _NAME_INDEX is None:
        _load_overrides()
    return _NAME_INDEX or {}


def _name_index_norm() -> dict[str, tuple[float, float]]:
    if _NAME_INDEX_NORM is None:
        _load_overrides()
    return _NAME_INDEX_NORM or {}


def _lookup_name_only(query_key: str) -> tuple[float, float] | None:
    """Try a bare-name match against the GeoNames-derived index.

    Strips trailing house-number / street tokens progressively so e.g.
    ``"robijnstraat 1"`` -> ``"robijnstraat"`` still resolves.
    """
    idx = _name_index()
    if not idx:
        return None
    # Try the full query first, then progressively strip leading address parts.
    candidates = [query_key]
    # Strip postal codes / numbers from start and end of each comma-part.
    parts = [p.strip() for p in query_key.split(",") if p.strip()]
    if len(parts) > 1:
        # Try last part (often the city).
        candidates.append(parts[-1])
        # And first part.
        candidates.append(parts[0])
    # Whitespace-token fallback for "BrandName City" patterns without a
    # comma (e.g. "Ankersmit Maastricht", "Sibelco Dessel"): try the last
    # word as the city, then the first word.  Only useful when the input
    # has no commas — comma-separated inputs are already covered above.
    if "," not in query_key:
        ws_parts = query_key.split()
        if len(ws_parts) > 1:
            candidates.append(ws_parts[-1])
            candidates.append(ws_parts[0])
    for cand in candidates:
        if cand in idx:
            return idx[cand]
        # Strip trailing numeric tokens (e.g. "robijnstraat 1" -> "robijnstraat").
        tokens = cand.split()
        while tokens and tokens[-1].replace(".", "").replace("-", "").isdigit():
            tokens.pop()
        # Strip leading numeric tokens (e.g. postal code "3000 hasselt" -> "hasselt").
        while tokens and tokens[0].replace(".", "").replace("-", "").isdigit():
            tokens.pop(0)
        stripped = " ".join(tokens).strip()
        if stripped and stripped != cand and stripped in idx:
            return idx[stripped]
    # Aggressive-normalisation fallback: handles hyphenated multi-word place
    # names and article variants ("Koudekerk-aan-de-Rijn" vs
    # "Koudekerk aan den Rijn", "L'Hospitalet" vs "Hospitalet", ...).
    norm_idx = _name_index_norm()
    if norm_idx:
        for cand in candidates:
            n = _aggr_norm(cand)
            if not n:
                continue
            hit = norm_idx.get(n) or idx.get(n)
            if hit is not None:
                return hit
    return None



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

    # 2) Bare-name fallback against GeoNames-derived index
    #    (resolves "Genk" -> "genk, be", "Robijnstraat 1" -> "robijnstraat", etc.)
    name_hit = _lookup_name_only(key)
    if name_hit is not None:
        lat, lon = name_hit
        gp = GeoPoint(lat=lat, lon=lon, label=f"{query} (geonames)")
        with _GEOCODE_LOCK:
            _GEOCODE_CACHE[key] = gp
        return gp

    # OFFLINE MODE: Do not use Nominatim or any external geocoding. Only use manual overrides.
    log.warning(f"No local geocode for '{query}'. Location may be outside cached region. Fallback to haversine/manual.")
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


def _osrm_request(a: GeoPoint, b: GeoPoint, *, geometry: bool,
                  timeout_s: float | None = None) -> dict | None:
    """Call OSRM /route/v1/driving and return the parsed JSON, or None."""
    if OFFLINE_MODE:
        return None
    if not (_is_valid_lat_lon(a.lat, a.lon) and _is_valid_lat_lon(b.lat, b.lon)):
        return None
    url = (f"{OSRM_URL}/route/v1/driving/"
           f"{a.lon:.6f},{a.lat:.6f};{b.lon:.6f},{b.lat:.6f}")
    params = {
        "overview": "full" if geometry else "false",
        "geometries": "geojson",
        "alternatives": "false",
        "steps": "false",
    }
    try:
        resp = requests.get(
            url,
            params=params,
            timeout=timeout_s or OSRM_TIMEOUT_S,
            headers={"User-Agent": "DDN-DIMinfra/1.0 (road-router)"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("OSRM request failed (%s -> %s): %s",
                    (a.lat, a.lon), (b.lat, b.lon), e)
        return None
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    return data


def osrm_route_km(a: GeoPoint, b: GeoPoint, timeout_s: float | None = None) -> tuple[float, float] | None:
    """Returns (distance_km, duration_min) for the driving route.

    Uses OSRM (public demo server by default, or self-hosted via
    ``OSRM_URL``).  Falls back to haversine when OSRM is unreachable so
    the UI still gets a number.
    """
    if not (_is_valid_lat_lon(a.lat, a.lon) and _is_valid_lat_lon(b.lat, b.lon)):
        return None
    data = _osrm_request(a, b, geometry=False, timeout_s=timeout_s)
    if data is not None:
        route = data["routes"][0]
        dist_km = float(route.get("distance", 0.0)) / 1000.0
        dur_min = float(route.get("duration", 0.0)) / 60.0
        if dist_km > 0:
            return (dist_km, dur_min)
    # Fallback so UI still gets a number when OSRM is unreachable.
    log.warning("osrm_route_km: falling back to haversine for %s -> %s",
                (a.lat, a.lon), (b.lat, b.lon))
    return (haversine_km(a, b), None)


def osrm_route_geometry(
    a: GeoPoint, b: GeoPoint, timeout_s: float | None = None,
) -> list[tuple[float, float]] | None:
    """Return the full driving route as a list of (lat, lon) waypoints.

    Falls back to a 2-point straight line when OSRM is unreachable.
    """
    if not (_is_valid_lat_lon(a.lat, a.lon) and _is_valid_lat_lon(b.lat, b.lon)):
        return None
    data = _osrm_request(a, b, geometry=True, timeout_s=timeout_s)
    if data is not None:
        try:
            geom = data["routes"][0]["geometry"]["coordinates"]
            # GeoJSON returns [lon, lat]; Leaflet expects (lat, lon).
            return [(float(lat), float(lon)) for lon, lat in geom]
        except (KeyError, IndexError, TypeError, ValueError) as e:
            log.warning("osrm_route_geometry: malformed response: %s", e)
    log.warning("osrm_route_geometry: falling back to straight line for %s -> %s",
                (a.lat, a.lon), (b.lat, b.lon))
    return [(a.lat, a.lon), (b.lat, b.lon)]


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

    Strategy:
      1. Manual overrides in :file:`data/waterway_terminals.json` win
         when within ``radius_km``.
      2. Otherwise query Overpass for nearby OSM features tagged as
         quays / docks / piers / harbours within ``radius_km`` (default
         :data:`QUAY_SEARCH_RADIUS_KM`, 20 km).
      3. As a last resort snap to the nearest navigable waterway node
         (canal / river / fairway) within the same radius — useful at
         locations that have a usable bank but no explicit quay tag.

    Results are cached on disk per (lat, lon, radius) tuple.
    Returns ``None`` if nothing is within the radius.
    """
    if not _is_valid_lat_lon(pt.lat, pt.lon):
        return None
    snap_radius = radius_km if radius_km is not None else QUAY_SEARCH_RADIUS_KM

    # 1) Manual overrides.
    overrides = _load_waterway_overrides()
    best_override: GeoPoint | None = None
    best_override_dist = float("inf")
    for label, (lat, lon) in overrides.items():
        d = haversine_km(pt, GeoPoint(lat=lat, lon=lon))
        if d < best_override_dist:
            best_override_dist = d
            best_override = GeoPoint(lat=lat, lon=lon, label=label)
    if best_override is not None and best_override_dist <= snap_radius:
        return best_override

    if OFFLINE_MODE:
        log.warning(
            "No manual quay within %.1f km for %s and OFFLINE_MODE is set; "
            "skipping Overpass lookup.", snap_radius, pt,
        )
        return None

    # 2) Overpass quay/dock/harbour query, cached on disk.
    cache = _quay_cache_path(pt.lat, pt.lon, snap_radius)
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("hit"):
                return GeoPoint(
                    lat=float(cached["lat"]), lon=float(cached["lon"]),
                    label=cached.get("label") or "Quay (osm)",
                )
            if cached.get("hit") is False:
                # Negative cache: known absence of any quay; fall through
                # to the waterway-snap fallback below before giving up.
                pass
            else:
                return None
        except Exception:
            pass

    radius_m = int(snap_radius * 1000)
    query = f"""
[out:json][timeout:{int(OVERPASS_TIMEOUT_S)}];
(
  node["waterway"="dock"](around:{radius_m},{pt.lat:.6f},{pt.lon:.6f});
  way["waterway"="dock"](around:{radius_m},{pt.lat:.6f},{pt.lon:.6f});
  node["man_made"~"^(pier|quay|wharf|mooring)$"](around:{radius_m},{pt.lat:.6f},{pt.lon:.6f});
  way["man_made"~"^(pier|quay|wharf|mooring)$"](around:{radius_m},{pt.lat:.6f},{pt.lon:.6f});
  node["mooring"](around:{radius_m},{pt.lat:.6f},{pt.lon:.6f});
  way["mooring"](around:{radius_m},{pt.lat:.6f},{pt.lon:.6f});
  node["industrial"="port"](around:{radius_m},{pt.lat:.6f},{pt.lon:.6f});
  way["industrial"="port"](around:{radius_m},{pt.lat:.6f},{pt.lon:.6f});
  way["landuse"="port"](around:{radius_m},{pt.lat:.6f},{pt.lon:.6f});
  node["harbour"](around:{radius_m},{pt.lat:.6f},{pt.lon:.6f});
  way["harbour"](around:{radius_m},{pt.lat:.6f},{pt.lon:.6f});
);
out center 50;
""".strip()

    quay_pt: GeoPoint | None = None
    try:
        resp = requests.post(
            OVERPASS_URL, data={"data": query},
            headers={
                "User-Agent": "DDN-DIMinfra/1.0 (quay-finder)",
                "Accept": "application/json",
            },
            timeout=OVERPASS_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("Overpass quay query failed for %s: %s", pt, e)
        data = None

    if data is not None:
        best: tuple[float, float, str] | None = None
        best_dist = float("inf")
        for el in data.get("elements", []):
            lat = el.get("lat")
            lon = el.get("lon")
            if lat is None or lon is None:
                center = el.get("center") or {}
                lat = center.get("lat")
                lon = center.get("lon")
            if lat is None or lon is None:
                continue
            d = haversine_km(pt, GeoPoint(lat=float(lat), lon=float(lon)))
            if d < best_dist and d <= snap_radius:
                tags = el.get("tags") or {}
                label = (tags.get("name") or tags.get("man_made")
                         or tags.get("waterway") or tags.get("harbour")
                         or "Quay")
                best = (float(lat), float(lon), str(label))
                best_dist = d
        if best is not None:
            quay_pt = GeoPoint(
                lat=best[0], lon=best[1],
                label=f"{best[2]} (osm, {best_dist:.1f} km)",
            )

    # 3) Fallback: snap to the nearest navigable waterway segment
    # (canal/river/fairway) within the radius.  This rescues bank-side
    # plants that have no explicit quay tag in OSM.
    if quay_pt is None:
        snap = _nearest_waterway_node(pt, snap_radius)
        if snap is not None:
            quay_pt = snap

    # Cache (positive or negative) for future calls.
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        if quay_pt is not None:
            cache.write_text(json.dumps({
                "hit": True, "lat": quay_pt.lat, "lon": quay_pt.lon,
                "label": quay_pt.label,
            }), encoding="utf-8")
        else:
            cache.write_text(json.dumps({"hit": False}), encoding="utf-8")
    except OSError as e:
        log.warning("quay cache write failed: %s", e)

    if quay_pt is None:
        log.info("No quay within %.1f km for %s", snap_radius, pt)
    return quay_pt


def _nearest_waterway_node(
    pt: "GeoPoint", radius_km: float,
) -> "GeoPoint | None":
    """Snap ``pt`` to the closest point on a navigable waterway.

    Reuses :func:`_fetch_inland_waterways` so the bbox is shared with
    the waterway-routing cache.  Returns ``None`` if Overpass fails or
    no waterway node is within ``radius_km``.
    """
    deg = max(radius_km / 110.0, 0.05)  # ~110 km per degree latitude
    lines = _fetch_inland_waterways(
        pt.lat - deg, pt.lon - deg, pt.lat + deg, pt.lon + deg,
    )
    if not lines:
        return None
    best: tuple[float, float] | None = None
    best_dist = float("inf")
    for line in lines:
        for lat, lon in line:
            d = haversine_km(pt, GeoPoint(lat=lat, lon=lon))
            if d < best_dist:
                best_dist = d
                best = (lat, lon)
    if best is None or best_dist > radius_km:
        return None
    return GeoPoint(
        lat=best[0], lon=best[1],
        label=f"Waterway snap (osm, {best_dist:.1f} km)",
    )


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

    if OFFLINE_MODE:
        return None, None

    # Bbox enclosing both endpoints + a margin so the routing graph
    # extends beyond the straight line (otherwise the path may dead-end
    # at the bbox edge).
    margin = _INLAND_BBOX_MARGIN_DEG
    min_lat = min(a.lat, b.lat) - margin
    max_lat = max(a.lat, b.lat) + margin
    min_lon = min(a.lon, b.lon) - margin
    max_lon = max(a.lon, b.lon) + margin

    lines = _fetch_inland_waterways(min_lat, min_lon, max_lat, max_lon)
    if not lines:
        return None, None

    # Build a graph: each consecutive pair of points in a way becomes an edge
    # weighted by haversine distance.  Coordinates are quantised so adjacent
    # ways that share an endpoint connect into one component.
    g = nx.Graph()

    def _q(p: tuple[float, float]) -> tuple[float, float]:
        return (round(p[0], 5), round(p[1], 5))

    for line in lines:
        for p1, p2 in zip(line, line[1:]):
            n1, n2 = _q(p1), _q(p2)
            if n1 == n2:
                continue
            d = haversine_km(GeoPoint(lat=p1[0], lon=p1[1]),
                             GeoPoint(lat=p2[0], lon=p2[1]))
            if g.has_edge(n1, n2):
                if d < g[n1][n2]["weight"]:
                    g[n1][n2]["weight"] = d
            else:
                g.add_edge(n1, n2, weight=d)

    if g.number_of_nodes() == 0:
        return None, None

    # Snap each endpoint to its nearest graph node (within INLAND_SNAP_MAX_KM).
    def _snap(pt: GeoPoint) -> tuple[float, float] | None:
        best_node = None
        best_d = _INLAND_SNAP_MAX_KM
        for node in g.nodes:
            d = haversine_km(pt, GeoPoint(lat=node[0], lon=node[1]))
            if d < best_d:
                best_d = d
                best_node = node
        return best_node

    src = _snap(a)
    dst = _snap(b)
    if src is None or dst is None:
        return None, None

    try:
        path = nx.shortest_path(g, src, dst, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None, None

    coords = [(float(lat), float(lon)) for lat, lon in path]
    length_km = 0.0
    for p1, p2 in zip(coords, coords[1:]):
        length_km += haversine_km(GeoPoint(lat=p1[0], lon=p1[1]),
                                  GeoPoint(lat=p2[0], lon=p2[1]))
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

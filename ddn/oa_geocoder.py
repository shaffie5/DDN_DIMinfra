"""Offline street-level geocoding helpers.

Two backends are exposed, both *opt-in* (the main `geocode()` in
`ddn.geo` only calls them when they are configured / present):

1. :func:`oa_lookup` — queries a local SQLite FTS5 index built from
   OpenAddresses CSVs (see ``scripts/build_oa_index.py``). Disk cost
   for BeNeLux is ~150–250 MB and the lookup is pure local SQL: no
   network, no Java, no Docker.

2. :func:`pelias_lookup` — queries a self-hosted Pelias HTTP endpoint
   (see ``docker-compose.pelias.yml``). Heavier (~3–8 GB on disk for
   BeNeLux) but supports fuzzy matching for unseen addresses.

Both return ``(lat, lon, label)`` or ``None`` and never raise.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from typing import Optional

import requests

from ._paths import OPENADDRESSES_DB_PATH

log = logging.getLogger("ddn.geo.offline")

# ---------------------------------------------------------------------------
# OpenAddresses SQLite backend
# ---------------------------------------------------------------------------
_OA_CONN: sqlite3.Connection | None = None
_OA_LOCK = threading.Lock()
_OA_AVAILABLE: bool | None = None  # tri-state: None=untested

# Matches "29" or "29A" or "29 bis" anywhere in the query.
_HOUSE_RE = re.compile(r"\b(\d{1,5}[a-zA-Z]?)\b")
_POST_RE = re.compile(r"\b(\d{4})\b")  # BE/NL postcode


def _oa_connect() -> sqlite3.Connection | None:
    global _OA_CONN, _OA_AVAILABLE
    if _OA_AVAILABLE is False:
        return None
    if _OA_CONN is not None:
        return _OA_CONN
    with _OA_LOCK:
        if _OA_CONN is not None:
            return _OA_CONN
        if not OPENADDRESSES_DB_PATH.exists():
            _OA_AVAILABLE = False
            return None
        try:
            conn = sqlite3.connect(
                f"file:{OPENADDRESSES_DB_PATH}?mode=ro",
                uri=True, check_same_thread=False,
            )
            # Sanity: required tables present?
            cur = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table','virtual table') "
                "AND name IN ('addresses','addresses_fts')"
            )
            names = {r[0] for r in cur.fetchall()}
            if "addresses" not in names:
                log.warning("OpenAddresses DB at %s missing 'addresses' table",
                            OPENADDRESSES_DB_PATH)
                _OA_AVAILABLE = False
                return None
            _OA_CONN = conn
            _OA_AVAILABLE = True
            log.info("OpenAddresses offline geocoder loaded: %s",
                     OPENADDRESSES_DB_PATH)
            return _OA_CONN
        except Exception as e:
            log.warning("Failed to open OpenAddresses DB: %s", e)
            _OA_AVAILABLE = False
            return None


def _split_query(query: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Parse a free-form address into (street, house, postcode, city).

    Heuristic, tuned for BE/NL/LU style "Iepermanlei 29, 2610 Antwerpen".
    """
    s = query.strip()
    house = None
    postcode = None
    city = None
    street = None

    # Split on comma if present
    if "," in s:
        left, right = (p.strip() for p in s.split(",", 1))
    else:
        left, right = s, ""

    # Postcode + city from right-hand side
    if right:
        m = _POST_RE.search(right)
        if m:
            postcode = m.group(1)
            city = right.replace(postcode, "").strip(" ,") or None
        else:
            city = right or None

    # House number from left-hand side (last numeric token)
    matches = list(_HOUSE_RE.finditer(left))
    if matches:
        house = matches[-1].group(1)
        street = (left[:matches[-1].start()] + left[matches[-1].end():]).strip(" ,")
    else:
        street = left.strip() or None

    return street, house, postcode, city


def _fts_quote(token: str) -> str:
    # FTS5 needs double-quoted phrases; escape inner quotes.
    t = token.replace('"', '""')
    return f'"{t}"'


def oa_lookup(query: str) -> tuple[float, float, str] | None:
    """Look up *query* in the local OpenAddresses SQLite index."""
    conn = _oa_connect()
    if conn is None:
        return None
    street, house, postcode, city = _split_query(query)
    if not street:
        return None

    has_fts = True
    try:
        conn.execute("SELECT 1 FROM addresses_fts LIMIT 1")
    except sqlite3.OperationalError:
        has_fts = False

    rows: list[tuple] = []
    try:
        if has_fts:
            # Build a phrase match on street + (postcode|city) for ranking.
            tokens = [_fts_quote(street)]
            if postcode:
                tokens.append(_fts_quote(postcode))
            elif city:
                tokens.append(_fts_quote(city.split()[0]))
            q = " AND ".join(tokens)
            sql = (
                "SELECT a.lat, a.lon, a.street, a.number, a.postcode, a.city "
                "FROM addresses_fts f JOIN addresses a ON a.rowid=f.rowid "
                "WHERE addresses_fts MATCH ? "
                "LIMIT 50"
            )
            rows = conn.execute(sql, (q,)).fetchall()
        else:
            # Fallback: LIKE query.
            sql = (
                "SELECT lat, lon, street, number, postcode, city FROM addresses "
                "WHERE street LIKE ? "
                + (" AND postcode = ?" if postcode else
                   (" AND city LIKE ?" if city else ""))
                + " LIMIT 50"
            )
            params: list = [f"%{street}%"]
            if postcode:
                params.append(postcode)
            elif city:
                params.append(f"%{city}%")
            rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        log.warning("OpenAddresses lookup failed for %r: %s", query, e)
        return None

    if not rows:
        return None

    # Pick best row: prefer matching house number, then matching postcode,
    # then matching city.
    def score(row: tuple) -> int:
        lat, lon, st, num, pc, ct = row
        s = 0
        if house and num and str(num).strip().lower() == house.lower():
            s += 100
        if postcode and pc and str(pc).strip() == postcode:
            s += 30
        if city and ct and city.lower() in str(ct).lower():
            s += 10
        return s

    best = max(rows, key=score)
    lat, lon, st, num, pc, ct = best
    label_parts = [p for p in (
        f"{st or ''} {num or ''}".strip(),
        f"{pc or ''} {ct or ''}".strip(),
    ) if p]
    label = ", ".join(label_parts) or query
    return float(lat), float(lon), f"{label} (openaddresses)"


# ---------------------------------------------------------------------------
# Pelias HTTP backend
# ---------------------------------------------------------------------------
PELIAS_URL = os.getenv("PELIAS_URL", "").rstrip("/")
PELIAS_TIMEOUT_S = float(os.getenv("PELIAS_TIMEOUT_S", "5.0"))
_PELIAS_DOWN: bool = False  # circuit-breaker after first failure


def pelias_lookup(query: str) -> tuple[float, float, str] | None:
    """Query a self-hosted Pelias instance. No-op if PELIAS_URL is unset."""
    global _PELIAS_DOWN
    if not PELIAS_URL or _PELIAS_DOWN:
        return None
    try:
        resp = requests.get(
            f"{PELIAS_URL}/v1/search",
            params={"text": query, "size": 1},
            timeout=PELIAS_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("Pelias lookup failed for %r: %s — disabling for session", query, e)
        _PELIAS_DOWN = True
        return None
    feats = (data or {}).get("features") or []
    if not feats:
        return None
    f = feats[0]
    coords = (f.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        return None
    lon, lat = float(coords[0]), float(coords[1])
    label = (f.get("properties") or {}).get("label") or query
    return lat, lon, f"{label} (pelias)"


__all__ = ["oa_lookup", "pelias_lookup", "PELIAS_URL"]

"""Build a compact OpenAddresses SQLite + FTS5 index for offline geocoding.

Workflow
--------
1. Manually download OpenAddresses CSV extracts you care about and put
   them anywhere on disk. Recommended sources:

     https://results.openaddresses.io/                (per-country runs)
     https://batch.openaddresses.io/data              (collection zips)

   For BeNeLux you typically want at minimum:
     * eu/be/*.csv      (Belgium, ~300 MB raw)
     * eu/nl/*.csv      (Netherlands, ~150 MB raw)
     * eu/lu/*.csv      (Luxembourg, ~5 MB raw)

   Each CSV must have the standard OpenAddresses columns:
     LON, LAT, NUMBER, STREET, UNIT, CITY, DISTRICT, REGION,
     POSTCODE, ID, HASH

2. Run this script pointing at the folder that contains those CSVs
   (recursively scanned):

     python scripts/build_oa_index.py --csv-dir D:\\openaddresses\\eu

   Result: data/openaddresses.sqlite (~150–250 MB for BeNeLux).
   This file is consumed automatically by ddn.oa_geocoder.

3. (Optional) drop the raw CSVs after the build — only the SQLite is
   needed at runtime.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import time
from pathlib import Path

from ddn._paths import OPENADDRESSES_DB_PATH

# Required columns we care about (OpenAddresses standard).
COLS = ("LON", "LAT", "NUMBER", "STREET", "POSTCODE", "CITY", "REGION")


def _open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;
        PRAGMA cache_size = -200000;  -- ~200 MB

        CREATE TABLE addresses (
            id        INTEGER PRIMARY KEY,
            lat       REAL NOT NULL,
            lon       REAL NOT NULL,
            number    TEXT,
            street    TEXT,
            postcode  TEXT,
            city      TEXT,
            region    TEXT
        );
        """
    )
    return conn


def _build_fts(conn: sqlite3.Connection) -> None:
    print("Building FTS5 index ...")
    conn.executescript(
        """
        CREATE VIRTUAL TABLE addresses_fts USING fts5(
            street, number, postcode, city, region,
            content='addresses', content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );
        INSERT INTO addresses_fts(rowid, street, number, postcode, city, region)
            SELECT id, street, number, postcode, city, region FROM addresses;
        """
    )


def _ingest_csv(conn: sqlite3.Connection, csv_path: Path,
                batch_size: int = 50_000) -> int:
    inserted = 0
    batch: list[tuple] = []
    sql = (
        "INSERT INTO addresses (lat, lon, number, street, postcode, city, region) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    try:
        fh = csv_path.open("r", encoding="utf-8", newline="", errors="replace")
    except OSError as e:
        print(f"  ! cannot open {csv_path}: {e}", file=sys.stderr)
        return 0
    with fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return 0
        # Tolerate either upper or lower case OA exports.
        norm = {n.upper(): n for n in reader.fieldnames}
        if not {"LON", "LAT", "STREET"}.issubset(norm):
            print(f"  ! skip {csv_path.name} (missing LON/LAT/STREET)",
                  file=sys.stderr)
            return 0

        def g(row: dict, key: str) -> str:
            v = row.get(norm.get(key, ""), "")
            return v.strip() if isinstance(v, str) else ""

        for row in reader:
            try:
                lon = float(g(row, "LON"))
                lat = float(g(row, "LAT"))
            except (TypeError, ValueError):
                continue
            street = g(row, "STREET")
            if not street:
                continue
            batch.append((
                lat, lon,
                g(row, "NUMBER") or None,
                street,
                g(row, "POSTCODE") or None,
                g(row, "CITY") or None,
                g(row, "REGION") or None,
            ))
            if len(batch) >= batch_size:
                conn.executemany(sql, batch)
                inserted += len(batch)
                batch.clear()
    if batch:
        conn.executemany(sql, batch)
        inserted += len(batch)
    return inserted


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv-dir", type=Path, required=True,
                   help="Folder containing OpenAddresses CSV files (recursive).")
    p.add_argument("--out", type=Path, default=OPENADDRESSES_DB_PATH,
                   help=f"Output SQLite path (default: {OPENADDRESSES_DB_PATH}).")
    p.add_argument("--no-fts", action="store_true",
                   help="Skip the FTS5 index (smaller DB, slower queries).")
    args = p.parse_args()

    if not args.csv_dir.exists():
        p.error(f"--csv-dir does not exist: {args.csv_dir}")

    csvs = sorted(args.csv_dir.rglob("*.csv"))
    if not csvs:
        p.error(f"No .csv files found under {args.csv_dir}")

    print(f"Building {args.out} from {len(csvs)} CSV file(s) ...")
    t0 = time.time()
    conn = _open_db(args.out)
    total = 0
    try:
        with conn:
            for i, c in enumerate(csvs, 1):
                n = _ingest_csv(conn, c)
                total += n
                print(f"  [{i:>3}/{len(csvs)}] +{n:>7,d} rows  {c.relative_to(args.csv_dir)}")
        print(f"Indexing {total:,} rows ...")
        with conn:
            conn.execute("CREATE INDEX idx_street ON addresses (street)")
            conn.execute("CREATE INDEX idx_postcode ON addresses (postcode)")
        if not args.no_fts:
            with conn:
                _build_fts(conn)
        with conn:
            conn.execute("VACUUM")
    finally:
        conn.close()

    size_mb = args.out.stat().st_size / (1024 * 1024)
    dt = time.time() - t0
    print(f"\nDone. {total:,} addresses → {args.out} ({size_mb:.1f} MB) in {dt:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

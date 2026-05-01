"""Pre-warm the on-disk geocoder cache for street-level offline use.

Run once with internet access. After this finishes every listed address
will be served from ``data/geocode_cache_nominatim.json`` even when
``DDN_OFFLINE=1`` is set.

Usage
-----
    # Warm a list of addresses (one per line) from a text file:
    python scripts/prewarm_geocoder.py --file addresses.txt

    # Warm every component origin from VN/Software-VN uploads on disk:
    python scripts/prewarm_geocoder.py --scan-uploads

    # Warm a few addresses directly from the command line:
    python scripts/prewarm_geocoder.py "Iepermanlei 29, 2610 Antwerpen" \
                                       "Stationsstraat 1, 3500 Hasselt"
"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path
from typing import Iterable

from ddn import geo, software_vn_parser, vn_parser
from ddn._paths import (
    BASE_DIR,
    NOMINATIM_CACHE_PATH,
)


def _addresses_from_file(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _addresses_from_parsed(parsed) -> list[str]:
    """Extract every plant_location + component origin from a parsed VN/SVN."""
    out: set[str] = set()
    for plant in getattr(parsed, "plants", []) or []:
        if getattr(plant, "plant_location", None):
            out.add(plant.plant_location)
        for c in getattr(plant, "components", []) or []:
            origin = getattr(c, "origin", None)
            if origin:
                out.add(origin)
    return sorted(out)


def _addresses_from_uploads() -> list[str]:
    """Pull every distinct origin from VN + Software-VN upload folders."""
    out: set[str] = set()
    for folder, parser, ext in (
        (BASE_DIR / "data" / "vn_uploads", vn_parser, "*.xlsx"),
        (BASE_DIR / "data" / "software_vn_uploads", software_vn_parser, "*.xlsx"),
    ):
        if not folder.exists():
            continue
        for xlsx in folder.glob(ext):
            try:
                data = parser.parse_workbook(xlsx) if hasattr(parser, "parse_workbook") \
                    else parser.parse(xlsx)
            except Exception as e:
                print(f"  ! skip {xlsx.name}: {e}", file=sys.stderr)
                continue
            out.update(_addresses_from_parsed(data))
    return sorted(out)


def warm_addresses(addresses: Iterable[str], *, quiet: bool = False) -> tuple[int, int]:
    """Resolve every address through ``ddn.geo.geocode`` so it lands in cache.

    Returns ``(resolved, missed)``. Safe to call from a background thread.
    Skips already-cached entries (those resolve instantly from memory/disk).
    """
    seen: set[str] = set()
    unique: list[str] = []
    for q in addresses:
        if not q:
            continue
        k = q.strip().lower()
        if k and k not in seen:
            seen.add(k)
            unique.append(q.strip())
    ok = miss = 0
    for q in unique:
        try:
            gp = geo.geocode(q)
        except Exception as e:
            if not quiet:
                print(f"  ! error {q!r}: {e}", file=sys.stderr)
            miss += 1
            continue
        if gp is None:
            miss += 1
        else:
            ok += 1
    return ok, miss


def warm_from_parsed_async(parsed) -> threading.Thread:
    """Fire-and-forget background warm-up triggered after a successful upload."""
    addrs = _addresses_from_parsed(parsed)
    t = threading.Thread(
        target=warm_addresses, args=(addrs,), kwargs={"quiet": True},
        name="geocode-prewarm", daemon=True,
    )
    t.start()
    return t


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("addresses", nargs="*", help="Addresses to geocode.")
    p.add_argument("--file", type=Path, help="Text file with one address per line.")
    p.add_argument("--scan-uploads", action="store_true",
                   help="Geocode every origin from VN/Software-VN uploads on disk.")
    args = p.parse_args()

    todo: list[str] = list(args.addresses)
    if args.file:
        todo += _addresses_from_file(args.file)
    if args.scan_uploads:
        todo += _addresses_from_uploads()

    # De-duplicate while preserving order
    seen: set[str] = set()
    unique = []
    for q in todo:
        k = q.strip().lower()
        if k and k not in seen:
            seen.add(k)
            unique.append(q.strip())

    if not unique:
        p.error("No addresses provided. Use positional args, --file, or --scan-uploads.")

    print(f"Pre-warming {len(unique)} address(es) → {NOMINATIM_CACHE_PATH}")
    if geo.OFFLINE_MODE:
        print("WARNING: DDN_OFFLINE=1 is set; only cached entries will resolve.",
              file=sys.stderr)

    ok = miss = 0
    for i, q in enumerate(unique, 1):
        gp = geo.geocode(q)
        if gp is None:
            print(f"  [{i:>4}/{len(unique)}] MISS  {q}")
            miss += 1
        else:
            print(f"  [{i:>4}/{len(unique)}] {gp.lat:>9.5f},{gp.lon:>9.5f}  {q}")
            ok += 1

    print(f"\nDone. resolved={ok} missed={miss}")
    return 0 if miss == 0 else 1


if __name__ == "__main__":
    sys.exit(main())



def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("addresses", nargs="*", help="Addresses to geocode.")
    p.add_argument("--file", type=Path, help="Text file with one address per line.")
    p.add_argument("--scan-uploads", action="store_true",
                   help="Geocode every origin from VN/Software-VN uploads on disk.")
    args = p.parse_args()

    todo: list[str] = list(args.addresses)
    if args.file:
        todo += _addresses_from_file(args.file)
    if args.scan_uploads:
        todo += _addresses_from_uploads()

    # De-duplicate while preserving order
    seen: set[str] = set()
    unique = []
    for q in todo:
        k = q.strip().lower()
        if k and k not in seen:
            seen.add(k)
            unique.append(q.strip())

    if not unique:
        p.error("No addresses provided. Use positional args, --file, or --scan-uploads.")

    print(f"Pre-warming {len(unique)} address(es) → {NOMINATIM_CACHE_PATH}")
    if geo.OFFLINE_MODE:
        print("WARNING: DDN_OFFLINE=1 is set; only cached entries will resolve.",
              file=sys.stderr)

    ok = miss = 0
    for i, q in enumerate(unique, 1):
        gp = geo.geocode(q)
        if gp is None:
            print(f"  [{i:>4}/{len(unique)}] MISS  {q}")
            miss += 1
        else:
            print(f"  [{i:>4}/{len(unique)}] {gp.lat:>9.5f},{gp.lon:>9.5f}  {q}")
            ok += 1

    print(f"\nDone. resolved={ok} missed={miss}")
    return 0 if miss == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

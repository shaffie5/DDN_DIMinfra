# GeoNames to geocode_overrides.json Converter
"""
This script downloads the GeoNames allCountries.zip dataset, extracts it, and converts it to the geocode_overrides.json format used by your app.

- Output: data/geocode_overrides.json
- Each entry: {"placename, country": [lat, lon]}
- You can adjust the fields as needed for your use case.

Usage (must be run from the repository root, since output and temp
files are written relative to the current working directory):

    python scripts/geonames_to_geocode.py

Outputs ``data/geocode_overrides.json`` (~500 MB). Temp files
``allCountries.zip`` and ``allCountries.txt`` are written to the
current directory and may be deleted after the run.
"""
import os
import json
import zipfile
import urllib.request

GEONAMES_URL = "https://download.geonames.org/export/dump/allCountries.zip"
OUTPUT_PATH = os.path.join("data", "geocode_overrides.json")
TMP_ZIP = "allCountries.zip"
TMP_TXT = "allCountries.txt"

print("Downloading GeoNames dataset (this may take a while)...")
urllib.request.urlretrieve(GEONAMES_URL, TMP_ZIP)

print("Extracting dataset...")
with zipfile.ZipFile(TMP_ZIP, 'r') as zip_ref:
    zip_ref.extractall()

print("Parsing and converting to JSON...")
geo_dict = {}
with open(TMP_TXT, encoding="utf-8") as f:
    for line in f:
        parts = line.strip().split('\t')
        if len(parts) < 10:
            continue
        name = parts[1]
        country = parts[8]
        lat = float(parts[4])
        lon = float(parts[5])
        key = f"{name}, {country}".lower()
        geo_dict[key] = [lat, lon]

print(f"Writing {len(geo_dict)} entries to {OUTPUT_PATH} ...")
os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
with open(OUTPUT_PATH, "w", encoding="utf-8") as out:
    json.dump(geo_dict, out, ensure_ascii=False, indent=2)

print("Done. You can now use data/geocode_overrides.json for offline global geocoding.")

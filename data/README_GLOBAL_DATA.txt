# README: Global Geocoding and Quay Data

This project uses offline global geocoding and quay data for maximum reliability and privacy.

## Geocoding (Place to Lat/Lon)
- Source: GeoNames allCountries.zip (https://download.geonames.org/export/dump/allCountries.zip)
- Usage: Run `python geonames_to_geocode.py` to generate data/geocode_overrides.json
- Output: data/geocode_overrides.json (millions of entries, several GB)

## Quay Data (Ports/Terminals)
- Source: Manually curated or merged from open datasets (e.g., UN/LOCODE, World Port Index)
- Usage: Add entries to data/waterway_terminals.json in the format {"quay name, country": [lat, lon]}
- Output: data/waterway_terminals.json

## Notes
- Both files are used for fully offline operation.
- For smaller deployments, you can filter to only the countries/regions you need.
- For global coverage, ensure enough disk space (5–10 GB recommended).

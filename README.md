# Digital Delivery Note (Prototype)

Streamlit prototype for an asphalt mixture digital delivery note with 4-party signing (Client, Transporter, COPRO, Permit holder) and Excel output.

## Run

```powershell
cd c:\Repositories\DDN_NEW
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

## Email (optional)

If you want the app to send signing links and the final Excel by email, set these environment variables before running:

- `DDN_SMTP_HOST`
- `DDN_SMTP_PORT` (e.g. 587)
- `DDN_SMTP_USER`
- `DDN_SMTP_PASS`
- `DDN_SMTP_TLS` (`true`/`false`)
- `DDN_FROM_EMAIL`

If not set, the app will show the signing links in-app (prototype mode).

## Maps + distance

The prototype uses:

- OpenStreetMap rendering via `folium` + `streamlit-folium`
- Address lookup via Nominatim (`geopy`)
- Driving distance via the public OSRM demo endpoint (fallback to straight-line if unavailable)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for instructions on how to commit changes to this repository.

# Spotter Backend Assessment — Fuel Route Optimizer

A Django REST API that plans USA driving routes, finds viable fuel stations near the route, and calculates a cost-aware refueling plan under fixed vehicle constraints.

## Assignment objective

Given a start and finish location in the USA, return a drivable route, GeoJSON geometry, required fuel stops, fuel costs, and a shareable local map view. The vehicle starts with a full tank and must never travel more than its 500-mile range between refueling opportunities.

## Features

- USA location validation and geocoding using a local Census Gazetteer, with Census Geocoder fallback for street addresses.
- OSRM driving routes with full GeoJSON `LineString` geometry.
- Spatial station lookup along a configurable route corridor using Shapely.
- Cost-aware fuel planning with ordered stops and range feasibility checks.
- Per-location, OSRM, and complete-route caching to minimize repeated external calls.
- Leaflet map page with route, start/finish, fuel-stop markers, and trip statistics.
- Structured JSON validation and service-error responses.

## Request flow

```text
POST /api/v1/routes/
  -> validate input
  -> geocode start and finish
  -> request one OSRM route
  -> find nearby fuel stations along the GeoJSON route
  -> optimize fuel purchases and stops
  -> cache and return JSON + map URL
```

## Project structure

```text
config/                 Django settings and root URLs
fuel/                   API views, models, serializers, services, and tests
fuel/services/          Geocoding, routing, station finder, fuel optimizer
fuel/templates/fuel/    Leaflet map template
data/                   Assessment fuel-price CSV and Census Gazetteer data
docs/                   Architecture and assessment notes
```

## Setup

Python 3.12 was used for validation.

```bash
python -m venv .venv
.venv\Scripts\activate             # Windows PowerShell
# source .venv/bin/activate         # Linux/macOS
python -m pip install -r requirements.txt
python manage.py migrate
```

Copy `.env.example` to `.env` only when local configuration is needed. Do not commit `.env` files. Supported settings include routing/geocoding service URLs, request timeouts, and cache configuration; defaults in `config/settings.py` support local development.

## Run

```bash
python manage.py runserver 127.0.0.1:8000
```

## API

```text
POST http://127.0.0.1:8000/api/v1/routes/
```

Example body:

```json
{
  "start": "Dallas, TX",
  "finish": "Atlanta, GA"
}
```

The response contains resolved `start`/`finish` locations; `route` distance, duration, and GeoJSON geometry; `vehicle` assumptions; fuel totals; ordered `fuel_stops`; and `map_url`.

For a successful result, open the returned relative map path against the local host:

```text
http://127.0.0.1:8000/api/v1/routes/map/<route-id>/
```

## Fuel-planning assumptions

- Tank capacity: 50 gallons.
- Fuel economy: 10 MPG.
- Maximum range: 500 miles.
- Starting fuel: 50 gallons at zero purchase cost.
- The optimizer selects feasible stations in route-progress order and minimizes purchase cost while preserving range constraints.

Station candidates are projected onto the route and filtered within the configured corridor. OSRM is called once per cold route calculation, not once per station. Repeated equivalent requests use Django cache layers for geocoding, routing, and complete route results.

## Map

The map endpoint renders Leaflet with an OpenStreetMap base layer, the route line, start/finish markers, optional numbered fuel-stop markers, stop details, and route/fuel statistics.

## Tests

```bash
python manage.py check
python manage.py test
```

Fresh-environment validation with dependencies from `requirements.txt` used Django 6.1.1 and completed with 63 passing tests and no check issues.

## Limitations

- The service supports USA locations only.
- Live route and address lookups depend on OSRM and Census service availability.
- Map results are cache-backed and expire with the configured cache timeout.

## Demo

Loom walkthrough: 
Part 1: https://www.loom.com/share/b73b73d69dc54bac9d5b8faf74a4a48d
Part 2: https://www.loom.com/share/50d17f9e7d1b49e182fb2c4ca647a6bb
Part 3: https://www.loom.com/share/0006a699b62c48e3af64f5f820ad2424

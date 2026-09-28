# Spotter Fuel Route Optimizer - Architecture Documentation

## Overview

The application is a Django-based REST API designed to calculate an optimal driving route between any two USA locations, locate cost-effective fuel stops along the driving corridor, and calculate the total money spent on fuel subject to vehicle constraints.

## Vehicle Constraints & Assumptions

- **Vehicle Range:** 500 miles maximum range
- **Efficiency:** 10 miles per gallon (MPG)
- **Tank Capacity:** 50 gallons (`500 / 10`)
- **Starting Fuel:** Starts with a full tank (50 gallons) at $0 cost

## Key Subsystems

```
[Client / Postman]
       |
       v
POST /api/v1/routes/
       |
       v
[RouteService] ----------------------- [Cache: LocMemCache]
       |
       +---> [GeocodingService] (Start & Finish geocoding: US Census Geocoder)
       |
       +---> [RoutingService] (Driving route & GeoJSON: Project OSRM)
       |
       +---> [StationFinderService] (Spatial candidate lookup: Shapely LineString + STRtree)
       |
       +---> [FuelOptimizerService] (Greedy cost-minimization algorithm)
       |
       v
[JSON Response + Leaflet Map URL]
```

## Data Ingestion & Preprocessing

- Source: `data/fuel-prices-for-be-assessment.csv`
- Preprocessing:
  - Non-USA entries filtered out.
  - Stations deduplicated using the lowest observed price.
  - Geocoding coordinates precomputed during ingestion (`manage.py import_stations`) and stored locally in database. Zero geocoding performed for stations at API request time.

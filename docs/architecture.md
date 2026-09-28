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

## Phase 10: Route Orchestration API Reference

### 1. Endpoint

```http
POST /api/v1/routes/
Content-Type: application/json
```

### 2. Request Parameters

| Field | Type | Required | Description | Default |
|---|---|---|---|---|
| `start` | string | Yes | Start location name, street address, or coordinates (`"lat, lon"`) | — |
| `finish` | string | Yes | Finish location name, street address, or coordinates (`"lat, lon"`) | — |
| `corridor_miles` | float | No | Lateral search corridor radius along route line (0.1 - 50.0 miles) | `5.0` |

#### Request Example

```json
{
  "start": "Austin, TX",
  "finish": "Dallas, TX",
  "corridor_miles": 5.0
}
```

### 3. Successful Response Structure (`200 OK`)

```json
{
  "start": {
    "input": "Austin, TX",
    "latitude": 30.2672,
    "longitude": -97.7431,
    "precision": "approximate",
    "matched_address": "Austin city, TX",
    "city": "Austin",
    "state": "TX",
    "source": "census_gazetteer"
  },
  "finish": {
    "input": "Dallas, TX",
    "latitude": 32.7767,
    "longitude": -96.797,
    "precision": "approximate",
    "matched_address": "Dallas city, TX",
    "city": "Dallas",
    "state": "TX",
    "source": "census_gazetteer"
  },
  "route": {
    "distance_miles": 195.0,
    "distance_meters": 313822.0,
    "duration_minutes": 180.0,
    "duration_hours": 3.0,
    "geometry": {
      "type": "LineString",
      "coordinates": [
        [-97.7431, 30.2672],
        [-96.797, 32.7767]
      ]
    }
  },
  "vehicle": {
    "max_range_miles": 500,
    "mpg": 10,
    "tank_capacity_gallons": 50
  },
  "fuel": {
    "starting_fuel_gallons": 50.0,
    "total_consumed_gallons": 19.5,
    "total_purchased_gallons": 0.0,
    "total_cost": 0.0,
    "fuel_remaining_gallons": 30.5
  },
  "fuel_stops": [],
  "map_url": "/api/v1/routes/map/9f2e7b1a8c3d4e5f/"
}
```

#### Long Route With Refueling Stops (`200 OK`)

When distance exceeds 500 miles, optimal fuel stops are identified along the driving corridor:

```json
{
  "fuel_stops": [
    {
      "station_id": 72773,
      "name": "RaceTrac #2626",
      "city": "Dallas",
      "state": "TX",
      "latitude": 32.793333,
      "longitude": -96.766513,
      "price_per_gallon": 2.864,
      "price_decimal": "2.8640",
      "mile_marker": 0.91,
      "gallons_purchased": 0.09,
      "cost": 0.26
    }
  ]
}
```

### 4. Error Handling & Machine-Readable Error Structure

All error responses adhere to a consistent JSON envelope:

```json
{
  "error": {
    "code": "MACHINE_READABLE_CODE",
    "message": "Human readable description.",
    "details": {}
  }
}
```

#### Standard Error Codes and HTTP Statuses

| Error Code | HTTP Status | Trigger Condition |
|---|---|---|
| `INVALID_INPUT` | `400 Bad Request` | Missing required fields, blank strings, or out-of-range parameters. |
| `INVALID_LOCATION` | `400 Bad Request` | Coordinates outside USA bounds or non-USA state/province (e.g. Canada). |
| `UNRESOLVED_LOCATION` | `422 Unprocessable Entity` | Address or city name could not be resolved by US Census Geocoder or Gazetteer. |
| `INVALID_COORDINATES` | `400 Bad Request` | Latitude outside `[-90, 90]` or longitude outside `[-180, 180]`. |
| `NO_ROUTE_FOUND` | `422 Unprocessable Entity` | OSRM reported no drivable road network connecting points. |
| `NO_FEASIBLE_FUEL_PLAN` | `422 Unprocessable Entity` | Gap between reachable stations exceeds 500-mile vehicle range. |
| `EXTERNAL_SERVICE_ERROR`| `502 Bad Gateway` | Network timeout or connection failure contacting Census or OSRM. |
| `INTERNAL_SERVER_ERROR` | `500 Internal Server Error`| Unhandled server exception (tracebacks suppressed). |

#### Infeasible Fuel Plan Example (`422 Unprocessable Entity`)

```json
{
  "error": {
    "code": "NO_FEASIBLE_FUEL_PLAN",
    "message": "Route distance 800.0 mi exceeds reachable range without available intermediate fuel stations."
  }
}
```

### 5. Orchestration Flow & Caching

1. **Input Normalization & Cache Check**: Computes a SHA-256 hash of normalized start, finish, and corridor miles. If cached in Django cache (`full_route_<sha256>`), returns immediately with zero external calls.
2. **Start & Finish Geocoding**: Queries local Census Gazetteer first; falls back to US Census Geocoder API for street addresses. Results cached individually (`geocode:<clean_input>`).
3. **OSRM Route Calculation**: Coordinates formatted as `lon,lat;lon,lat`. Cached by coordinate signature (`osrm_route:<signature>`).
4. **Candidate Station Spatial Index**: Filters precomputed station coordinates using Shapely `STRtree` spatial index within buffer corridor.
5. **Greedy Lookahead Optimizer**: Computes globally cost-efficient fuel purchase plan respecting 500-mile range and downstream station prices.
6. **Result Assembly & Map Cache**: Saves route response under `route_map:<route_id>` for map consumption (Phase 11).

# Backend Django Engineer Assessment --- Implementation Plan

## 1. Purpose

This document is the single source of truth for implementing the Spotter
Backend Django Engineer assessment.

The implementation must be:

-   Correct
-   Simple enough to review
-   Production-minded without over-engineering
-   Fast
-   Testable
-   Well documented
-   Easy to explain in a 5-minute Loom
-   Strictly within the assessment scope

The assessment requires an API that:

1.  Accepts a USA start location and USA finish location.
2.  Calculates a driving route.
3.  Finds cost-effective fuel stops along the route.
4.  Supports a vehicle with a maximum range of 500 miles.
5.  Assumes 10 miles per gallon.
6.  Returns the total money spent on fuel.
7.  Returns route/map information.
8.  Uses the supplied fuel-price CSV.
9.  Uses a free map/routing API.
10. Minimizes external routing/geocoding API calls.
11. Uses the latest stable Django version.
12. Is demonstrated through Postman (or equivalent) in a Loom video of 5
    minutes or less.

------------------------------------------------------------------------

# 2. Critical Agent Rules

## 2.1 Scope Lock

The coding agent MUST implement only the requirements and architecture
defined in this document.

The agent MUST NOT:

-   Add unrelated features.
-   Change the architecture without permission.
-   Replace Django with another framework.
-   Add unnecessary microservices.
-   Add Celery unless explicitly approved.
-   Add Kubernetes.
-   Add authentication unless required.
-   Add payment systems.
-   Add unrelated UI.
-   Add unrelated APIs.
-   Modify environment secrets.
-   Invent data.
-   Silently change business assumptions.
-   Silently change fuel optimization rules.
-   Silently change API contracts.
-   Delete existing user work if working inside an existing repository.
-   Add dependencies merely because they are convenient.

## 2.2 Ask Before Proceeding

If the agent encounters any of the following, it MUST STOP and ask for
confirmation before making the change:

-   A requirement is ambiguous.
-   A business rule is missing.
-   A CSV field has an unexpected meaning.
-   Duplicate station prices cannot be interpreted safely.
-   The selected geocoding/routing API changes the expected
    architecture.
-   A dependency is unavailable or incompatible.
-   A proposed change would materially alter the API response.
-   A proposed change would materially alter the fuel optimization
    algorithm.
-   A security or data-integrity issue requires changing the planned
    architecture.
-   The agent believes another architecture would be significantly
    better.
-   A required external service has changed its terms or limits.
-   The agent wants to add a feature not explicitly requested.
-   The agent wants to remove a planned component.

The agent must not "guess and continue" in these situations.

## 2.3 No Silent Refactoring

The agent may fix obvious syntax/type/test failures inside files it is
already implementing.

The agent MUST NOT perform broad refactoring unrelated to the current
task.

If a refactor is useful but changes architecture or scope, stop and ask.

## 2.4 Small, Reviewable Steps

Implementation must be performed in small stages:

1.  Project setup
2.  Configuration
3.  Data model
4.  CSV ingestion
5.  Geocoding/preprocessing
6.  Routing service
7.  Spatial station search
8.  Fuel optimization
9.  API
10. Map
11. Tests
12. Documentation
13. Final validation

After each major stage, verify that the implementation still matches
this document.

------------------------------------------------------------------------

# 3. Recommended Technology Stack

## Backend

-   Python
-   Latest stable Django
-   Django REST Framework

## Data

-   SQLite is acceptable for a simple assessment implementation.
-   PostgreSQL is preferred if Docker/PostgreSQL is already part of the
    chosen setup.
-   Do not introduce PostgreSQL solely for complexity.

## HTTP

Use one consistent HTTP client such as:

-   `httpx`

Do not mix several HTTP libraries without a reason.

## Geospatial

-   Shapely
-   STRtree for spatial candidate lookup where appropriate

## Routing

Recommended:

-   OSRM

The route API should return full route geometry in GeoJSON format.

## Geocoding

Recommended:

-   US Census Geocoder for USA addresses.

Station coordinates must be precomputed during data ingestion and must
NOT be geocoded on every user request.

## Map

-   Leaflet
-   OpenStreetMap-compatible tiles, subject to the selected tile
    provider's usage policy

The map should be a lightweight HTML page served by Django.

## Caching

Use Django's cache framework.

For a simple local assessment:

-   LocMemCache is acceptable.

If Redis is already part of the environment:

-   Redis can be used.

Do not add Redis solely to make the project look more complex.

------------------------------------------------------------------------

# 4. High-Level Architecture

``` text
                         ┌─────────────────────┐
                         │      Postman        │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Django REST API     │
                         │ POST /api/v1/routes │
                         └──────────┬──────────┘
                                    │
                           Normalize / Validate
                                    │
                                    ▼
                            ┌───────────────┐
                            │ Route Cache   │
                            └───────┬───────┘
                              HIT   │   MISS
                               │    │
                               │    ▼
                               │  Geocode
                               │  Start + Finish
                               │    │
                               │    ▼
                               │   OSRM
                               │    │
                               └────┬─┘
                                    ▼
                              Route Geometry
                                    │
                                    ▼
                          Spatial Station Search
                                    │
                                    ▼
                            Fuel Optimizer
                                    │
                                    ▼
                         Cost + Fuel Stop Plan
                                    │
                                    ▼
                         JSON + GeoJSON response
                                    │
                                    ▼
                             Leaflet Map
```

------------------------------------------------------------------------

# 5. Project Directory

Use a clean separation of concerns.

``` text
spotter-fuel-route/
│
├── manage.py
├── requirements.txt
├── README.md
├── ASSESSMENT_PLAN.md
├── .env.example
├── .gitignore
│
├── data/
│   └── fuel-prices-for-be-assessment.csv
│
├── config/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
│
├── fuel/
│   ├── __init__.py
│   ├── admin.py
│   ├── apps.py
│   ├── models.py
│   ├── urls.py
│   ├── views.py
│   ├── serializers.py
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── geocoding.py
│   │   ├── routing.py
│   │   ├── station_finder.py
│   │   ├── fuel_optimizer.py
│   │   └── route_service.py
│   │
│   ├── management/
│   │   ├── __init__.py
│   │   └── commands/
│   │       ├── __init__.py
│   │       └── import_stations.py
│   │
│   ├── templates/
│   │   └── fuel/
│   │       └── map.html
│   │
│   └── tests/
│       ├── __init__.py
│       ├── test_optimizer.py
│       ├── test_station_finder.py
│       ├── test_api.py
│       └── test_data_import.py
│
└── docs/
    └── architecture.md
```

------------------------------------------------------------------------

# 6. Responsibility of Each File

## `models.py`

Contains the persistent station representation.

Recommended model:

``` text
FuelStation
- station_id
- name
- address
- city
- state
- price
- latitude
- longitude
- source
- is_active
```

Do not add fields that are not useful.

If the source CSV has a meaningful unique identifier, preserve it.

## `serializers.py`

Responsible for:

-   validating API input
-   serializing API output where appropriate

Input should validate:

-   start exists
-   finish exists
-   strings are non-empty
-   locations represent USA locations

## `views.py`

Keep views thin.

The view should:

1.  Validate input.
2.  Call the route service.
3.  Return the result.
4.  Convert known service exceptions into appropriate HTTP responses.

The view must NOT contain the fuel optimization algorithm.

## `services/geocoding.py`

Responsible only for geocoding locations.

It should:

-   accept a location string
-   call the configured geocoder
-   normalize the result
-   return latitude/longitude
-   handle known API failures

It should not know anything about fuel stations.

## `services/routing.py`

Responsible only for route calculation.

It should:

-   accept start coordinates
-   accept finish coordinates
-   call OSRM
-   request driving route
-   request full GeoJSON geometry
-   return normalized route data

It should not contain fuel logic.

## `services/station_finder.py`

Responsible for:

-   loading station coordinates
-   building/using spatial index
-   finding stations near the route
-   projecting candidates onto the route
-   calculating route mile markers

It should not decide how much fuel to buy.

## `services/fuel_optimizer.py`

Contains the core business algorithm.

Inputs:

-   ordered candidate stations
-   route distance
-   vehicle range
-   MPG
-   starting fuel assumption

Outputs:

-   selected stops
-   gallons purchased per stop
-   cost per stop
-   total gallons purchased
-   total cost

This should be a pure/testable component as much as possible.

## `services/route_service.py`

This is the orchestration layer.

It coordinates:

``` text
geocoding
→ routing
→ station finder
→ optimizer
→ response
```

It should also coordinate caching.

## `management/commands/import_stations.py`

Responsible for offline data preparation.

It must:

1.  Read CSV.
2.  Validate columns.
3.  Remove non-USA records.
4.  Remove exact duplicate records.
5.  Resolve duplicate station IDs according to the documented price
    rule.
6.  Geocode station records.
7.  Apply fallback coordinate logic when necessary.
8.  Save records to database.
9.  Report failures clearly.

No station geocoding should happen during normal API requests.

------------------------------------------------------------------------

# 7. CSV Data Processing

The supplied CSV must be treated as source data.

Observed dataset characteristics:

-   8,151 rows
-   6,738 unique truckstop IDs
-   Duplicate station IDs exist
-   Some duplicate IDs have different prices
-   Canadian records exist
-   Coordinates are not directly provided

The implementation must not silently assume that every row is a unique
station.

## 7.1 USA Filtering

The assessment accepts locations within the USA.

Canadian station records should not be used as USA fuel candidates.

The importer should filter using the dataset's state/province
information.

Do not rely on city name alone.

## 7.2 Duplicate Handling

Important:

The CSV does not provide a reliable timestamp/version field for deciding
which duplicate price is the latest.

Therefore the implementation must NOT claim that a selected price is the
"latest price".

Recommended assessment rule:

1.  Preserve source observations while importing if practical.
2.  For the station records used by the optimizer, select the lowest
    supplied price for a station ID when multiple distinct prices exist.
3.  Document this as an explicit assessment assumption.

Reason:

The assignment asks for cost-effective fuel stops, while the source data
does not provide a timestamp to establish the current price.

If a reviewer expects a different interpretation, this rule is easy to
explain and change.

------------------------------------------------------------------------

# 8. Station Geocoding Strategy

Station coordinates are required for spatial route matching.

They must be generated during preprocessing.

## Primary

Use US Census Geocoder where the station address can be resolved.

## Fallback

If exact address geocoding fails:

``` text
City + State
      ↓
City-level coordinate
```

The fallback must be clearly marked as lower precision.

## Important

Do NOT do this:

``` text
API request
    ↓
geocode every station
    ↓
calculate route
```

That would create thousands of external calls.

Instead:

``` text
CSV import
    ↓
geocode once
    ↓
save coordinates
    ↓
API uses local coordinates
```

------------------------------------------------------------------------

# 9. User Location Geocoding

For the API request:

``` json
{
  "start": "Los Angeles, CA",
  "finish": "New York, NY"
}
```

The application needs coordinates.

Recommended flow:

``` text
Normalize input
      ↓
Check location cache
      ↓
Cache miss?
      ↓
Geocoder
      ↓
latitude/longitude
```

Both start and finish may require geocoding.

This means a cold request may use:

-   2 geocoding calls
-   1 routing call

Total: 3 external API calls.

This is acceptable under the assignment.

Repeated requests should use cache.

------------------------------------------------------------------------

# 10. Routing Strategy

Use OSRM for driving routes.

Conceptually:

``` text
start longitude,latitude
            +
finish longitude,latitude
            ↓
OSRM
            ↓
distance
duration
GeoJSON LineString
```

Request the complete route geometry.

The application should not make a routing request for every fuel
station.

That would violate the API-call constraint and would be unnecessarily
slow.

------------------------------------------------------------------------

# 11. Spatial Station Search

Do not compare every station using an external routing service.

Use local geospatial processing.

Recommended:

-   Shapely
-   STRtree or another appropriate spatial index

Process:

``` text
OSRM route
     ↓
Shapely LineString
     ↓
route corridor
     ↓
spatial query
     ↓
candidate stations
```

Initial corridor:

``` text
5 miles
```

Make this configurable:

``` python
FUEL_STOP_CORRIDOR_MILES = 5
```

The value should not be hardcoded throughout the codebase.

------------------------------------------------------------------------

# 12. Mile Marker Calculation

Each candidate station needs an approximate position along the route.

Conceptually:

``` text
START
  |
  |--- 100 miles ---|
                    Station A
  |
  |--- 150 miles ---|
                    Station B
  |
  |--- 200 miles ---|
                    Station C
  |
FINISH
```

Store:

``` text
mile_marker
```

relative to route start.

The optimizer then works on an ordered one-dimensional route.

------------------------------------------------------------------------

# 13. Fuel Model

The assessment specifies:

``` text
Maximum range = 500 miles
MPG = 10
```

Therefore:

``` text
Tank capacity = 500 / 10
              = 50 gallons
```

These should be configuration/constants, not magic numbers spread
throughout the code.

Example:

``` python
VEHICLE_MAX_RANGE_MILES = 500
VEHICLE_MPG = 10
VEHICLE_TANK_CAPACITY_GALLONS = (
    VEHICLE_MAX_RANGE_MILES / VEHICLE_MPG
)
```

------------------------------------------------------------------------

# 14. Starting Fuel Assumption

The assessment does not specify the starting fuel level.

Use:

``` text
Starting fuel = full tank
```

Document this prominently.

Therefore:

``` text
Initial fuel = 50 gallons
Initial fuel cost = $0
```

The vehicle only purchases additional fuel when necessary.

------------------------------------------------------------------------

# 15. Fuel Optimization Algorithm

This is the most important business logic.

The objective is to minimize fuel cost subject to:

-   maximum vehicle range = 500 miles
-   10 MPG
-   fuel stations along the route
-   vehicle cannot run out of fuel

## Greedy Strategy

At each selected station:

1.  Look ahead up to the maximum reachable distance.
2.  Find the first station with a lower fuel price.
3.  If a cheaper station is reachable:
    -   buy only enough fuel to reach it.
4.  If no cheaper station is reachable:
    -   fill the tank as appropriate.
5.  Never exceed tank capacity.
6.  Never allow fuel to fall below the amount needed to reach the next
    selected station/destination.

This is a classic route fuel optimization strategy.

## Example

``` text
Current station = $4.00

Within reachable range:

Station B = $3.20
```

Do not fill the tank at \$4.00.

Buy enough to reach Station B.

If:

``` text
Current station = $3.20

No cheaper station within 500 miles
```

then fill the tank as needed.

------------------------------------------------------------------------

# 16. Fuel Calculation

For a route segment:

``` text
distance = 120 miles
MPG = 10

fuel required = 120 / 10
              = 12 gallons
```

For a station:

``` text
price = $3.25
fuel purchased = 20 gallons

cost = 20 × 3.25
     = $65.00
```

The final response should contain:

-   fuel consumed
-   fuel purchased
-   cost per stop
-   total fuel cost

------------------------------------------------------------------------

# 17. Important Feasibility Rule

If two required refueling points are more than 500 miles apart and no
reachable station exists:

``` text
The route is infeasible under the vehicle assumptions.
```

Return a controlled API error rather than inventing a fuel stop.

Example:

``` json
{
  "error": {
    "code": "NO_FEASIBLE_FUEL_PLAN",
    "message": "The route cannot be completed with a maximum vehicle range of 500 miles using the available fuel stations."
  }
}
```

Use an appropriate HTTP status such as `422`.

------------------------------------------------------------------------

# 18. API Design

## Endpoint

``` http
POST /api/v1/routes/
```

## Request

``` json
{
  "start": "Los Angeles, CA",
  "finish": "New York, NY"
}
```

## Response shape

``` json
{
  "start": {
    "input": "Los Angeles, CA",
    "latitude": 34.0522,
    "longitude": -118.2437
  },
  "finish": {
    "input": "New York, NY",
    "latitude": 40.7128,
    "longitude": -74.0060
  },
  "route": {
    "distance_miles": 2785.4,
    "duration_minutes": 2480,
    "geometry": {
      "type": "LineString",
      "coordinates": []
    }
  },
  "vehicle": {
    "max_range_miles": 500,
    "mpg": 10,
    "tank_capacity_gallons": 50
  },
  "fuel": {
    "starting_fuel_gallons": 50,
    "total_consumed_gallons": 278.54,
    "total_purchased_gallons": 250.12,
    "total_cost": 856.42
  },
  "fuel_stops": [
    {
      "station_id": 123,
      "name": "Example Truck Stop",
      "latitude": 35.1,
      "longitude": -100.2,
      "price_per_gallon": 3.19,
      "mile_marker": 540.2,
      "gallons_purchased": 41.2,
      "cost": 131.43
    }
  ],
  "map_url": "/api/v1/routes/map/<route-id>/"
}
```

Actual values must come from the application. Do not hardcode example
values.

------------------------------------------------------------------------

# 19. Map Endpoint

Provide a simple map page:

``` http
GET /api/v1/routes/map/<route-id>/
```

The page should use Leaflet to display:

-   Start marker
-   Destination marker
-   Route line
-   Selected fuel stops

Fuel stop popup should show:

``` text
Station
Price per gallon
Mile marker
Gallons purchased
Stop cost
```

Do not build a complex frontend application.

A small Django template is sufficient.

------------------------------------------------------------------------

# 20. Caching

Caching is important because the assignment explicitly asks for quick
results and minimal external API calls.

## Cache location geocoding

Key:

``` text
geocode:{normalized_location}
```

## Cache route

Key:

``` text
route:{normalized_start}:{normalized_finish}
```

## Cache complete result

A complete route result may also be cached if practical.

The exact cache implementation must not change the API response.

------------------------------------------------------------------------

# 21. Performance Strategy

The expensive operations are:

1.  External geocoding
2.  External routing
3.  Geospatial station search

Optimization:

``` text
Station geocoding
→ one-time preprocessing

Station data
→ local database

Spatial index
→ local processing

Geocoding
→ cached

Routing
→ cached

Fuel optimization
→ pure local computation
```

Cold request target:

``` text
At most 3 external calls
```

Cached request:

``` text
0 external calls
```

Do not claim a specific response time until it has been measured.

------------------------------------------------------------------------

# 22. Error Handling

Handle at least:

## Invalid input

``` text
start missing
finish missing
empty string
```

Return `400`.

## Geocoding failure

Return a controlled error.

## Location outside USA

Reject the request.

## Routing API failure

Return a controlled service error.

## No stations near route

Return a meaningful error.

## No feasible fuel plan

Return `422`.

Do not expose raw stack traces or external API secrets.

------------------------------------------------------------------------

# 23. Tests

Tests are required.

## `test_optimizer.py`

Test:

1.  Route shorter than 500 miles.
2.  Cheaper station within range.
3.  No cheaper station within range.
4.  Multiple fuel stops.
5.  Tank capacity.
6.  Exact 500-mile boundary.
7.  No reachable station.
8.  Destination reachable without another stop.
9.  Correct total cost.

## `test_station_finder.py`

Test:

-   station inside corridor
-   station outside corridor
-   ordering by mile marker

## `test_data_import.py`

Test:

-   CSV parsing
-   USA filtering
-   duplicate handling
-   price normalization
-   invalid rows

## `test_api.py`

Test:

-   valid request
-   invalid request
-   successful response shape
-   impossible route
-   external-service failure handling

External APIs should not be called during unit tests.

Mock them.

------------------------------------------------------------------------

# 24. Code Quality Rules

The code must follow these principles:

## Single Responsibility

Each service should have one clear responsibility.

Bad:

``` python
view()
    geocode()
    call_osrm()
    parse_csv()
    optimize_fuel()
    render_html()
```

Good:

``` python
view()
    → route_service.calculate_route()
```

And:

``` text
route_service
    → geocoding service
    → routing service
    → station finder
    → optimizer
```

## No Magic Numbers

Avoid:

``` python
if distance > 500:
```

Prefer:

``` python
if distance > VEHICLE_MAX_RANGE_MILES:
```

## Type Hints

Use Python type hints for service functions.

## Clear Names

Prefer:

``` python
calculate_fuel_required()
find_nearby_stations()
calculate_optimal_fuel_plan()
```

Avoid vague names such as:

``` python
do_it()
process()
handle_data()
```

## Small Functions

Avoid very large functions.

## No Duplicate Business Logic

The 500-mile range and 10 MPG should have one source of truth.

------------------------------------------------------------------------

# 25. Environment Configuration

Use `.env` for configurable external services where necessary.

Example:

``` env
DJANGO_DEBUG=True
DJANGO_SECRET_KEY=change-me

ROUTING_API_BASE_URL=https://router.project-osrm.org

FUEL_STOP_CORRIDOR_MILES=5
VEHICLE_MAX_RANGE_MILES=500
VEHICLE_MPG=10
```

Never commit real secrets.

`.env.example` must contain placeholders only.

------------------------------------------------------------------------

# 26. Dependency Management

Use a `requirements.txt`.

Keep dependencies minimal.

Example categories:

``` text
Django
djangorestframework
httpx
shapely
python-dotenv
```

Add database-specific dependencies only if required.

Do not add packages that duplicate existing functionality.

------------------------------------------------------------------------

# 27. Local Setup

README should provide:

``` bash
python -m venv .venv
```

Windows:

``` bash
.venv\Scripts\activate
```

Linux/macOS:

``` bash
source .venv/bin/activate
```

Install:

``` bash
pip install -r requirements.txt
```

Create migrations:

``` bash
python manage.py makemigrations
python manage.py migrate
```

Import station data:

``` bash
python manage.py import_stations
```

Run server:

``` bash
python manage.py runserver
```

API:

``` text
POST http://127.0.0.1:8000/api/v1/routes/
```

------------------------------------------------------------------------

# 28. Data Import Must Be Repeatable

Running:

``` bash
python manage.py import_stations
```

multiple times must not create uncontrolled duplicate database records.

Use:

-   `update_or_create`
-   bulk operations where appropriate
-   database uniqueness constraints

The command should clearly report:

``` text
Rows read
Rows skipped
USA rows
Duplicate rows
Stations imported
Geocoding successes
Geocoding failures
```

------------------------------------------------------------------------

# 29. README Requirements

The final `README.md` must contain:

1.  Project overview
2.  Assessment requirements
3.  Technology stack
4.  Architecture
5.  Folder structure
6.  Setup instructions
7.  CSV import instructions
8.  Geocoding approach
9.  Routing approach
10. Fuel optimization algorithm
11. API documentation
12. Example request
13. Example response
14. Map instructions
15. Testing instructions
16. Performance strategy
17. Caching strategy
18. External API call count
19. Assumptions
20. Limitations
21. Future improvements

------------------------------------------------------------------------

# 30. Assumptions to Document

The final README must explicitly document:

## Vehicle

``` text
Maximum range: 500 miles
MPG: 10
Tank capacity: 50 gallons
```

## Starting fuel

``` text
Vehicle starts with a full tank.
```

## Fuel prices

``` text
The supplied CSV does not contain a reliable timestamp/version field.
Where a station ID has multiple distinct prices, the lowest supplied price is used for cost optimization.
```

## Station proximity

``` text
Stations within a configurable route corridor are treated as candidates.
```

## Road detours

``` text
Candidate station selection uses geospatial proximity rather than routing every station individually, in order to satisfy the external API call constraint.
```

## Geocoding

``` text
Station coordinates are precomputed during data import.
Address-level geocoding is preferred; city/state fallback may be used when an address cannot be resolved.
```

------------------------------------------------------------------------

# 31. What NOT to Put in the Assessment

Do not spend time on:

-   User registration
-   Login
-   Admin dashboard redesign
-   React frontend
-   Mobile app
-   Payment
-   Notifications
-   Background workers
-   Kubernetes
-   CI/CD unless easy and already available
-   Complex cloud infrastructure
-   Advanced authentication
-   Multiple routing providers
-   Individual route API call per fuel station

The objective is a strong backend assessment, not a large product.

------------------------------------------------------------------------

# 32. Agent Implementation Order

The coding agent must follow this exact order unless the user approves a
change.

## Phase 1 --- Inspect

-   Inspect repository.
-   Inspect CSV.
-   Inspect existing files if any.
-   Confirm Python/Django environment.
-   Do not modify anything yet.

## Phase 2 --- Confirm

Before implementation, report:

-   detected CSV columns
-   detected row count
-   duplicate behavior
-   proposed data model
-   proposed external APIs
-   proposed architecture

If anything conflicts with this document, STOP and ask.

## Phase 3 --- Bootstrap

Create:

-   Django project
-   fuel app
-   requirements
-   environment configuration
-   basic URLs

## Phase 4 --- Data Model

Create station model and migrations.

Run tests/migrations.

## Phase 5 --- Importer

Implement:

``` text
manage.py import_stations
```

Test it with the supplied CSV.

## Phase 6 --- Geocoding

Implement station preprocessing.

Do not geocode stations at API request time.

## Phase 7 --- Routing

Implement OSRM service.

Add mocks/tests.

## Phase 8 --- Station Finder

Implement route corridor and spatial search.

## Phase 9 --- Optimizer

Implement and thoroughly test the fuel optimization algorithm.

## Phase 10 --- API

Connect everything through the orchestration service.

## Phase 11 --- Map

Implement minimal Leaflet map.

## Phase 12 --- Performance

Add caching and measure real performance.

## Phase 13 --- Tests

Run:

``` bash
python manage.py test
```

Fix failures.

## Phase 14 --- Documentation

Complete:

-   README.md
-   architecture documentation
-   assumptions
-   API examples

## Phase 15 --- Final Review

Run:

``` bash
python manage.py check
python manage.py test
```

Also perform a real Postman request.

Only after all of this should the agent consider the assessment
implementation complete.

------------------------------------------------------------------------

# 33. Agent Stop Conditions

The agent MUST stop and ask the user before:

-   changing the fuel algorithm
-   changing the 500-mile constraint
-   changing the 10 MPG assumption
-   changing the starting-fuel assumption
-   changing the API response contract
-   changing the routing provider
-   changing the geocoding strategy
-   adding a major dependency
-   changing database architecture
-   adding a new external service
-   modifying source CSV
-   deleting source data
-   changing duplicate-price policy
-   adding unrelated functionality

------------------------------------------------------------------------

# 34. Final Acceptance Checklist

The implementation is complete only when all are true:

-   [ ] Latest stable Django used
-   [ ] Django REST Framework used
-   [ ] CSV imported successfully
-   [ ] Canada/non-USA stations excluded from candidate data
-   [ ] Duplicate station handling documented
-   [ ] Station coordinates precomputed
-   [ ] Geocoding is not performed per station during requests
-   [ ] Start/finish geocoding works
-   [ ] OSRM route works
-   [ ] Route geometry returned
-   [ ] Route distance returned
-   [ ] Fuel stations found near route
-   [ ] Stations ordered by route position
-   [ ] 500-mile range enforced
-   [ ] 10 MPG enforced
-   [ ] Starting full-tank assumption enforced
-   [ ] Fuel optimization tested
-   [ ] Total fuel cost calculated
-   [ ] Impossible route handled
-   [ ] API caching implemented
-   [ ] External API calls minimized
-   [ ] Map works
-   [ ] Unit tests pass
-   [ ] API tests pass
-   [ ] Django system check passes
-   [ ] README complete
-   [ ] No secrets committed
-   [ ] Git repository clean and understandable
-   [ ] Postman demo works

------------------------------------------------------------------------

# 35. Git Commit Strategy

Use small meaningful commits.

Recommended:

``` text
feat: bootstrap django assessment project
feat: add fuel station model
feat: add station csv importer
feat: add station geocoding pipeline
feat: add osrm routing service
feat: add spatial station finder
feat: add fuel optimization algorithm
feat: add route api
feat: add route caching
feat: add leaflet route map
test: add fuel optimizer tests
test: add route api tests
docs: add assessment architecture and setup
```

Do not create one giant commit containing the entire project if
avoidable.

------------------------------------------------------------------------

# 36. Loom Demo Plan --- Maximum 5 Minutes

## 0:00--0:30

Explain:

> "This is a Django REST API that accepts two US locations, calculates a
> driving route, identifies cost-effective fuel stops based on a
> 500-mile vehicle range and 10 MPG, and returns the total fuel cost."

## 0:30--1:30

Show project structure.

Explain:

``` text
services/
    geocoding.py
    routing.py
    station_finder.py
    fuel_optimizer.py
    route_service.py
```

Emphasize separation of concerns.

## 1:30--3:00

Open Postman.

Send:

``` json
{
  "start": "Los Angeles, CA",
  "finish": "Las Vegas, NV"
}
```

Show:

-   route distance
-   fuel stops
-   prices
-   gallons
-   costs
-   total cost

## 3:00--4:00

Show map.

Explain route + selected fuel stations.

## 4:00--5:00

Explain:

-   station preprocessing
-   caching
-   3 maximum external calls on a cold request
-   local spatial search
-   tested fuel optimization

Keep the Loom focused.

------------------------------------------------------------------------

# 37. Final Engineering Principle

The goal is NOT to write the largest amount of code.

The goal is:

``` text
Correctness
    +
Clear architecture
    +
Fast execution
    +
Minimal external calls
    +
Testable business logic
    +
Clear documentation
```

A reviewer should be able to open the repository and understand the
system within a few minutes.

The most important code should be easy to find:

``` text
fuel/services/fuel_optimizer.py
```

The most important API orchestration should be easy to find:

``` text
fuel/services/route_service.py
```

The data ingestion should be easy to find:

``` text
fuel/management/commands/import_stations.py
```

The API endpoint should be thin:

``` text
fuel/views.py
```

This separation is intentional and should not be collapsed into one
large file.

------------------------------------------------------------------------

# 38. Agent Instruction --- Final

Before modifying the project:

1.  Read this entire document.
2.  Inspect the current repository and CSV.
3.  Report what you found.
4.  Compare the current state against this plan.
5.  Ask the user if anything materially conflicts.

Then implement one phase at a time.

Do NOT make assumptions about ambiguous requirements.

Do NOT expand scope.

Do NOT silently change architecture.

Do NOT silently change business rules.

Do NOT overwrite user work.

When a decision is required, STOP and ASK.

When implementation is complete, provide:

-   files changed
-   commands executed
-   tests executed
-   test results
-   known limitations
-   any remaining questions

No feature is considered complete merely because the code "looks
correct"; it must be tested.

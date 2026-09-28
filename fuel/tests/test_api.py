"""API-level tests for POST /api/v1/routes/ endpoint.

Covers requirements from Phase 10:
1. Successful short route (<= 500 miles, 0 stops, $0 cost)
2. Successful long route (requiring fuel stops, verified ordering, cost/gallons)
3. Invalid request (missing/blank start or finish -> 400)
4. Unresolved location (machine-readable error -> 422)
5. Non-USA location (e.g. Canadian province -> 400)
6. No feasible fuel plan (unavoidable > 500-mile gap -> 422 with code NO_FEASIBLE_FUEL_PLAN)
7. Routing failure (OSRM connectivity/timeout error -> 502)
8. Caching / external call count (repeated request uses cache, does not duplicate calls)
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from fuel.models import FuelStation
from fuel.services.geocoding import (
    GeocodingConnectionError,
    GeocodingResult,
    InvalidLocationError,
    UnresolvedLocationError,
)
from fuel.services.routing import (
    NoRouteFoundError,
    RoutingConnectionError,
    RoutingResult,
)
from fuel.services.station_finder import StationFinder


class RouteCalculateApiTestCase(APITestCase):
    def setUp(self):
        cache.clear()
        StationFinder.clear_cache()
        self.url = reverse('fuel:calculate_route')

    def tearDown(self):
        cache.clear()
        StationFinder.clear_cache()

    @patch('fuel.services.route_service.RoutingService')
    @patch('fuel.services.route_service.GeocodingService')
    def test_successful_short_route(self, mock_geocoding_cls, mock_routing_cls):
        """Test short route (195 miles <= 500 miles): 0 fuel stops, $0 cost, HTTP 200."""
        # Mock geocoding
        mock_geo = MagicMock()
        mock_geocoding_cls.return_value = mock_geo
        mock_geo.geocode.side_effect = [
            GeocodingResult(
                input_text="Austin, TX",
                latitude=30.2672,
                longitude=-97.7431,
                precision="approximate",
                city="Austin",
                state="TX",
                source="census_gazetteer",
            ),
            GeocodingResult(
                input_text="Dallas, TX",
                latitude=32.7767,
                longitude=-96.7970,
                precision="approximate",
                city="Dallas",
                state="TX",
                source="census_gazetteer",
            ),
        ]

        # Mock routing
        mock_routing = MagicMock()
        mock_routing_cls.return_value = mock_routing
        mock_routing.calculate_route.return_value = RoutingResult(
            start_coordinates=(30.2672, -97.7431),
            finish_coordinates=(32.7767, -96.7970),
            distance_meters=313822.0,
            distance_miles=195.0,
            duration_seconds=10800.0,
            duration_hours=3.0,
            geometry={
                "type": "LineString",
                "coordinates": [[-97.7431, 30.2672], [-96.7970, 32.7767]],
            },
            waypoints=[],
        )

        response = self.client.post(
            self.url,
            {"start": "Austin, TX", "finish": "Dallas, TX"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        # Route checks
        self.assertIn("route", data)
        self.assertEqual(data["route"]["distance_miles"], 195.0)
        self.assertEqual(data["route"]["geometry"]["type"], "LineString")

        # Vehicle assumptions
        self.assertEqual(data["vehicle"]["max_range_miles"], 500)
        self.assertEqual(data["vehicle"]["mpg"], 10)
        self.assertEqual(data["vehicle"]["tank_capacity_gallons"], 50)

        # Fuel plan checks
        self.assertEqual(data["fuel"]["starting_fuel_gallons"], 50.0)
        self.assertEqual(data["fuel"]["total_consumed_gallons"], 19.5)
        self.assertEqual(data["fuel"]["total_purchased_gallons"], 0.0)
        self.assertEqual(data["fuel"]["total_cost"], 0.0)
        self.assertEqual(data["fuel"]["fuel_remaining_gallons"], 30.5)

        # Fuel stops checks
        self.assertEqual(len(data["fuel_stops"]), 0)

        # Map URL
        self.assertIn("map_url", data)
        self.assertTrue(data["map_url"].startswith("/api/v1/routes/map/"))

    @patch('fuel.services.route_service.RoutingService')
    @patch('fuel.services.route_service.GeocodingService')
    def test_successful_long_route_with_fuel_stops(self, mock_geocoding_cls, mock_routing_cls):
        """Test long route (700 miles > 500 miles): requires optimal refueling, HTTP 200."""
        # Create database fuel stations along route corridor
        # Route is along lon = -100.0 from lat 30.0 to 40.0 (~690 miles)
        FuelStation.objects.create(
            station_id=1001,
            name="Intermediate Cheap Fuel",
            address="Hwy 83 Mile 400",
            city="Canadian",
            state="TX",
            price=Decimal("3.1000"),
            latitude=35.8,  # ~400 miles along route
            longitude=-100.01,
            geocode_precision=FuelStation.GEOCODE_PRECISION_ADDRESS,
            is_active=True,
        )

        # Mock geocoding
        mock_geo = MagicMock()
        mock_geocoding_cls.return_value = mock_geo
        mock_geo.geocode.side_effect = [
            GeocodingResult(
                input_text="South Start, TX",
                latitude=30.0,
                longitude=-100.0,
                precision="coordinates",
                source="coordinates",
            ),
            GeocodingResult(
                input_text="North Finish, NE",
                latitude=40.0,
                longitude=-100.0,
                precision="coordinates",
                source="coordinates",
            ),
        ]

        # Mock routing
        mock_routing = MagicMock()
        mock_routing_cls.return_value = mock_routing
        mock_routing.calculate_route.return_value = RoutingResult(
            start_coordinates=(30.0, -100.0),
            finish_coordinates=(40.0, -100.0),
            distance_meters=1112000.0,
            distance_miles=690.0,
            duration_seconds=36000.0,
            duration_hours=10.0,
            geometry={
                "type": "LineString",
                "coordinates": [
                    [-100.0, 30.0],
                    [-100.0, 35.8],
                    [-100.0, 40.0],
                ],
            },
            waypoints=[],
        )

        response = self.client.post(
            self.url,
            {"start": "South Start, TX", "finish": "North Finish, NE"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        # Fuel stops must exist and be ordered
        self.assertGreaterEqual(len(data["fuel_stops"]), 1)
        stop = data["fuel_stops"][0]
        self.assertEqual(stop["station_id"], 1001)
        self.assertEqual(stop["city"], "Canadian")
        self.assertEqual(stop["state"], "TX")
        self.assertGreater(stop["gallons_purchased"], 0.0)
        self.assertGreater(stop["cost"], 0.0)

        # Fuel totals
        self.assertEqual(data["fuel"]["total_consumed_gallons"], 69.0)
        self.assertAlmostEqual(
            data["fuel"]["total_purchased_gallons"],
            stop["gallons_purchased"],
            places=2
        )
        self.assertAlmostEqual(data["fuel"]["total_cost"], stop["cost"], places=2)

    def test_missing_start_field(self):
        """Test missing start field returns 400 with INVALID_INPUT code."""
        response = self.client.post(
            self.url,
            {"finish": "Dallas, TX"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = response.json()
        self.assertEqual(data["error"]["code"], "INVALID_INPUT")
        self.assertIn("start", data["error"]["details"])

    def test_blank_location_fields(self):
        """Test blank start or finish returns 400 with INVALID_INPUT code."""
        response = self.client.post(
            self.url,
            {"start": "  ", "finish": "Dallas, TX"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = response.json()
        self.assertEqual(data["error"]["code"], "INVALID_INPUT")

    @patch('fuel.services.route_service.GeocodingService')
    def test_unresolved_location(self, mock_geocoding_cls):
        """Test unresolvable location returns 422 with UNRESOLVED_LOCATION code."""
        mock_geo = MagicMock()
        mock_geocoding_cls.return_value = mock_geo
        mock_geo.geocode.side_effect = UnresolvedLocationError(
            "Location 'Nowhere Town 99999' could not be resolved."
        )

        response = self.client.post(
            self.url,
            {"start": "Nowhere Town 99999", "finish": "Dallas, TX"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        data = response.json()
        self.assertEqual(data["error"]["code"], "UNRESOLVED_LOCATION")
        self.assertIn("could not be resolved", data["error"]["message"])

    def test_non_usa_location_rejected(self):
        """Test Canadian province input returns 400 with INVALID_LOCATION code."""
        response = self.client.post(
            self.url,
            {"start": "Toronto, ON", "finish": "New York, NY"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = response.json()
        self.assertEqual(data["error"]["code"], "INVALID_LOCATION")
        self.assertIn("Only USA locations are supported", data["error"]["message"])

    @patch('fuel.services.route_service.RoutingService')
    @patch('fuel.services.route_service.GeocodingService')
    def test_no_feasible_fuel_plan(self, mock_geocoding_cls, mock_routing_cls):
        """Test route with unavoidable > 500-mile gap returns 422 with NO_FEASIBLE_FUEL_PLAN code."""
        # Mock geocoding
        mock_geo = MagicMock()
        mock_geocoding_cls.return_value = mock_geo
        mock_geo.geocode.side_effect = [
            GeocodingResult(input_text="Remote A", latitude=35.0, longitude=-100.0, precision="coordinates"),
            GeocodingResult(input_text="Remote B", latitude=45.0, longitude=-100.0, precision="coordinates"),
        ]

        # Route distance is 800 miles, but NO fuel stations exist in database
        mock_routing = MagicMock()
        mock_routing_cls.return_value = mock_routing
        mock_routing.calculate_route.return_value = RoutingResult(
            start_coordinates=(35.0, -100.0),
            finish_coordinates=(45.0, -100.0),
            distance_meters=1287475.0,
            distance_miles=800.0,
            duration_seconds=43200.0,
            duration_hours=12.0,
            geometry={
                "type": "LineString",
                "coordinates": [[-100.0, 35.0], [-100.0, 45.0]],
            },
            waypoints=[],
        )

        response = self.client.post(
            self.url,
            {"start": "Remote A", "finish": "Remote B"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        data = response.json()
        self.assertEqual(data["error"]["code"], "NO_FEASIBLE_FUEL_PLAN")
        self.assertIn("exceeds", data["error"]["message"].lower())

    @patch('fuel.services.route_service.RoutingService')
    @patch('fuel.services.route_service.GeocodingService')
    def test_routing_failure_returns_502(self, mock_geocoding_cls, mock_routing_cls):
        """Test external OSRM service failure returns 502 with EXTERNAL_SERVICE_ERROR code."""
        mock_geo = MagicMock()
        mock_geocoding_cls.return_value = mock_geo
        mock_geo.geocode.side_effect = [
            GeocodingResult(input_text="Start", latitude=34.0, longitude=-118.0, precision="coordinates"),
            GeocodingResult(input_text="Finish", latitude=40.0, longitude=-74.0, precision="coordinates"),
        ]

        mock_routing = MagicMock()
        mock_routing_cls.return_value = mock_routing
        mock_routing.calculate_route.side_effect = RoutingConnectionError(
            "Failed to connect to OSRM routing service."
        )

        response = self.client.post(
            self.url,
            {"start": "Start", "finish": "Finish"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        data = response.json()
        self.assertEqual(data["error"]["code"], "EXTERNAL_SERVICE_ERROR")

    @patch('fuel.services.route_service.RoutingService')
    @patch('fuel.services.route_service.GeocodingService')
    def test_repeated_request_uses_cache(self, mock_geocoding_cls, mock_routing_cls):
        """Test that identical repeated request retrieves cached result without re-querying services."""
        mock_geo = MagicMock()
        mock_geocoding_cls.return_value = mock_geo
        mock_geo.geocode.side_effect = [
            GeocodingResult(input_text="Austin, TX", latitude=30.2672, longitude=-97.7431, precision="approximate"),
            GeocodingResult(input_text="Dallas, TX", latitude=32.7767, longitude=-96.7970, precision="approximate"),
        ]

        mock_routing = MagicMock()
        mock_routing_cls.return_value = mock_routing
        mock_routing.calculate_route.return_value = RoutingResult(
            start_coordinates=(30.2672, -97.7431),
            finish_coordinates=(32.7767, -96.7970),
            distance_meters=313822.0,
            distance_miles=195.0,
            duration_seconds=10800.0,
            duration_hours=3.0,
            geometry={
                "type": "LineString",
                "coordinates": [[-97.7431, 30.2672], [-96.7970, 32.7767]],
            },
            waypoints=[],
        )

        payload = {"start": "Austin, TX", "finish": "Dallas, TX"}

        # First request
        resp1 = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_geo.geocode.call_count, 2)
        self.assertEqual(mock_routing.calculate_route.call_count, 1)

        # Second request (must be served from cache)
        resp2 = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp1.json(), resp2.json())

        # External services must NOT have been called again
        self.assertEqual(mock_geo.geocode.call_count, 2)
        self.assertEqual(mock_routing.calculate_route.call_count, 1)

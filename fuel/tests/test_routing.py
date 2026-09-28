"""Unit tests for RoutingService."""

from unittest.mock import MagicMock, patch
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
import httpx

from fuel.services.routing import (
    InvalidRouteCoordinatesError,
    NoRouteFoundError,
    RoutingConnectionError,
    RoutingError,
    RoutingResult,
    RoutingService,
)


class RoutingServiceTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.service = RoutingService(base_url="https://router.project-osrm.org")

    def tearDown(self):
        cache.clear()

    @patch("httpx.Client.get")
    def test_calculate_route_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": "Ok",
            "routes": [
                {
                    "distance": 435136.6,
                    "duration": 17912.5,
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [-118.2437, 34.0522],
                            [-116.0, 35.0],
                            [-115.1398, 36.1699],
                        ],
                    },
                }
            ],
            "waypoints": [
                {"name": "Los Angeles", "location": [-118.2437, 34.0522]},
                {"name": "Las Vegas", "location": [-115.1398, 36.1699]},
            ],
        }
        mock_get.return_value = mock_response

        result = self.service.calculate_route(
            start_lat=34.0522,
            start_lon=-118.2437,
            finish_lat=36.1699,
            finish_lon=-115.1398,
        )

        self.assertIsInstance(result, RoutingResult)
        self.assertAlmostEqual(result.distance_meters, 435136.6, places=1)
        self.assertAlmostEqual(result.distance_miles, 270.38, places=2)
        self.assertAlmostEqual(result.duration_seconds, 17912.5, places=1)
        self.assertAlmostEqual(result.duration_hours, 4.98, places=2)
        self.assertEqual(result.geometry["type"], "LineString")
        self.assertEqual(len(result.geometry["coordinates"]), 3)
        self.assertFalse(result.is_cached)

        # Verify OSRM coordinate order in URL: lon,lat
        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        self.assertIn("-118.2437,34.0522;-115.1398,36.1699", called_url)

    @patch("httpx.Client.get")
    def test_calculate_route_caching(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": "Ok",
            "routes": [
                {
                    "distance": 160934.4,  # Exactly 100 miles
                    "duration": 3600.0,    # Exactly 1 hour
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-100.0, 35.0], [-99.0, 35.0]],
                    },
                }
            ],
            "waypoints": [],
        }
        mock_get.return_value = mock_response

        # First call (cold) -> HTTP call is made
        res1 = self.service.calculate_route(35.0, -100.0, 35.0, -99.0)
        self.assertFalse(res1.is_cached)
        self.assertEqual(mock_get.call_count, 1)

        # Second call (warm) -> Returned from cache, no new HTTP call
        res2 = self.service.calculate_route(35.0, -100.0, 35.0, -99.0)
        self.assertTrue(res2.is_cached)
        self.assertEqual(mock_get.call_count, 1)  # Still 1
        self.assertAlmostEqual(res2.distance_miles, 100.0, places=1)

    @patch("httpx.Client.get")
    def test_calculate_route_no_route_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": "NoRoute",
            "message": "Impossible to find a route between coordinates",
        }
        mock_get.return_value = mock_response

        with self.assertRaises(NoRouteFoundError):
            self.service.calculate_route(21.3, -157.8, 34.0, -118.2)

    @patch("httpx.Client.get")
    def test_calculate_route_timeout(self, mock_get):
        mock_get.side_effect = httpx.TimeoutException("Connection timed out")

        with self.assertRaises(RoutingConnectionError):
            self.service.calculate_route(34.0522, -118.2437, 36.1699, -115.1398)

    @patch("httpx.Client.get")
    def test_calculate_route_network_failure(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("Failed to connect")

        with self.assertRaises(RoutingConnectionError):
            self.service.calculate_route(34.0522, -118.2437, 36.1699, -115.1398)

    @patch("httpx.Client.get")
    def test_calculate_route_server_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.text = "Bad Gateway"
        mock_get.return_value = mock_response

        with self.assertRaises(RoutingConnectionError):
            self.service.calculate_route(34.0522, -118.2437, 36.1699, -115.1398)

    def test_invalid_coordinates(self):
        # Latitude > 90
        with self.assertRaises(InvalidRouteCoordinatesError):
            self.service.calculate_route(95.0, -100.0, 35.0, -100.0)

        # Longitude < -180
        with self.assertRaises(InvalidRouteCoordinatesError):
            self.service.calculate_route(35.0, -185.0, 35.0, -100.0)

    def test_routing_result_serialization(self):
        result = RoutingResult(
            start_coordinates=(34.0522, -118.2437),
            finish_coordinates=(36.1699, -115.1398),
            distance_meters=435136.6,
            distance_miles=270.38,
            duration_seconds=17912.5,
            duration_hours=4.98,
            geometry={"type": "LineString", "coordinates": [[-118.2, 34.0], [-115.1, 36.1]]},
            waypoints=[],
            is_cached=False,
        )
        data = result.to_dict()
        self.assertEqual(data["distance_miles"], 270.38)
        self.assertEqual(data["duration_hours"], 4.98)

        reconstructed = RoutingResult.from_dict(data, is_cached=True)
        self.assertEqual(reconstructed.distance_miles, 270.38)
        self.assertTrue(reconstructed.is_cached)

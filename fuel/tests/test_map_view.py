"""Tests for Phase 11 Leaflet route map view and template."""

from unittest.mock import patch
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import resolve, reverse

from fuel.views import RouteMapView


class RouteMapViewTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.route_id = "test1234abcd5678"
        self.sample_route_data = {
            "start": {
                "input": "Los Angeles, CA",
                "latitude": 34.0522,
                "longitude": -118.2437,
                "precision": "approximate",
                "matched_address": "Los Angeles city, CA",
                "city": "Los Angeles",
                "state": "CA",
                "source": "census_gazetteer",
            },
            "finish": {
                "input": "Las Vegas, NV",
                "latitude": 36.1699,
                "longitude": -115.1398,
                "precision": "approximate",
                "matched_address": "Las Vegas city, NV",
                "city": "Las Vegas",
                "state": "NV",
                "source": "census_gazetteer",
            },
            "route": {
                "distance_miles": 270.4,
                "distance_meters": 435136.6,
                "duration_minutes": 298.5,
                "duration_hours": 4.98,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [-118.2437, 34.0522],
                        [-116.5, 35.0],
                        [-115.1398, 36.1699],
                    ],
                },
            },
            "vehicle": {
                "max_range_miles": 500,
                "mpg": 10,
                "tank_capacity_gallons": 50,
            },
            "fuel": {
                "starting_fuel_gallons": 50.0,
                "total_consumed_gallons": 27.04,
                "total_purchased_gallons": 15.5,
                "total_cost": 49.60,
                "fuel_remaining_gallons": 38.46,
            },
            "fuel_stops": [
                {
                    "station_id": 9001,
                    "name": "Barstow Express Travel Center",
                    "city": "Barstow",
                    "state": "CA",
                    "latitude": 34.8958,
                    "longitude": -117.0173,
                    "price_per_gallon": 3.200,
                    "price_decimal": "3.2000",
                    "mile_marker": 115.2,
                    "gallons_purchased": 15.5,
                    "cost": 49.60,
                }
            ],
            "map_url": f"/api/v1/routes/map/{self.route_id}/",
        }

    def tearDown(self):
        cache.clear()

    def test_map_url_resolves(self):
        """1. Map URL resolves to RouteMapView."""
        url = reverse("fuel:route_map", kwargs={"route_id": self.route_id})
        self.assertEqual(url, f"/api/v1/routes/map/{self.route_id}/")

        match = resolve(url)
        self.assertEqual(match.func.view_class, RouteMapView)

    def test_map_page_returns_200_for_valid_route(self):
        """2. Map page returns HTTP 200 for a valid cached route."""
        cache.set(f"route_map:{self.route_id}", self.sample_route_data, timeout=3600)

        response = self.client.get(reverse("fuel:route_map", kwargs={"route_id": self.route_id}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "fuel/map.html")
        self.assertEqual(response.context["route_id"], self.route_id)
        self.assertIsNotNone(response.context["route_data"])
        self.assertIsNone(response.context["error"])

    def test_map_page_contains_leaflet_markup(self):
        """3. Map page contains Leaflet/map markup and scripts."""
        cache.set(f"route_map:{self.route_id}", self.sample_route_data, timeout=3600)

        response = self.client.get(reverse("fuel:route_map", kwargs={"route_id": self.route_id}))
        content = response.content.decode("utf-8")

        # Map DOM container
        self.assertIn('<div id="map"></div>', content)
        # Leaflet CDN assets
        self.assertIn("leaflet.css", content)
        self.assertIn("leaflet.js", content)
        # Esri public basemap tile source
        self.assertIn("server.arcgisonline.com", content)

    def test_route_geometry_available_to_template(self):
        """4. Route geometry is available to the template and rendered in JSON script tag."""
        cache.set(f"route_map:{self.route_id}", self.sample_route_data, timeout=3600)

        response = self.client.get(reverse("fuel:route_map", kwargs={"route_id": self.route_id}))
        self.assertIn("route_data", response.context)
        self.assertEqual(
            response.context["route_data"]["route"]["geometry"]["type"],
            "LineString"
        )
        self.assertEqual(
            len(response.context["route_data"]["route"]["geometry"]["coordinates"]),
            3
        )

        content = response.content.decode("utf-8")
        # Check json_script id and coordinates present
        self.assertIn('id="route-data"', content)
        self.assertIn("-118.2437", content)
        self.assertIn("34.0522", content)

    def test_fuel_stop_data_available_to_template(self):
        """5. Fuel-stop data is available to template and displayed in information panel."""
        cache.set(f"route_map:{self.route_id}", self.sample_route_data, timeout=3600)

        response = self.client.get(reverse("fuel:route_map", kwargs={"route_id": self.route_id}))
        content = response.content.decode("utf-8")

        # Stop information checks
        self.assertIn("Barstow Express Travel Center", content)
        self.assertIn("Barstow", content)
        self.assertIn("CA", content)
        self.assertIn("3.200", content)
        self.assertIn("49.60", content)
        self.assertIn("115.2", content)
        self.assertIn("15.5", content)

        # Overview summary checks
        self.assertIn("270.4", content)  # Distance miles
        self.assertIn("Stop #1", content)

    def test_nonexistent_route_id_returns_404(self):
        """6. Invalid/nonexistent route ID returns an appropriate 404 response."""
        response = self.client.get(reverse("fuel:route_map", kwargs={"route_id": "nonexistent_id_999"}))
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "fuel/map.html")
        self.assertIsNotNone(response.context["error"])
        self.assertIn("nonexistent_id_999", response.context["error"])

        content = response.content.decode("utf-8")
        self.assertIn("Route Not Found", content)

    @patch("fuel.services.routing.RoutingService.calculate_route")
    @patch("httpx.Client.get")
    def test_map_page_does_not_trigger_osrm_call(self, mock_httpx_get, mock_calc_route):
        """7. Map page consumes cached result and does NOT trigger a new OSRM call."""
        cache.set(f"route_map:{self.route_id}", self.sample_route_data, timeout=3600)

        response = self.client.get(reverse("fuel:route_map", kwargs={"route_id": self.route_id}))
        self.assertEqual(response.status_code, 200)

        mock_calc_route.assert_not_called()
        mock_httpx_get.assert_not_called()

    @patch("fuel.services.geocoding.GeocodingService.geocode")
    @patch("httpx.Client.get")
    def test_map_page_does_not_trigger_geocoding_call(self, mock_httpx_get, mock_geocode):
        """8. Map page consumes cached result and does NOT trigger new geocoding calls."""
        cache.set(f"route_map:{self.route_id}", self.sample_route_data, timeout=3600)

        response = self.client.get(reverse("fuel:route_map", kwargs={"route_id": self.route_id}))
        self.assertEqual(response.status_code, 200)

        mock_geocode.assert_not_called()
        mock_httpx_get.assert_not_called()

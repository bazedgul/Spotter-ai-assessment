"""Unit tests for GeocodingService."""

from unittest.mock import MagicMock, patch
from django.core.cache import cache
from django.test import TestCase
import httpx

from fuel.services.geocoding import (
    GeocodingConnectionError,
    GeocodingError,
    GeocodingResult,
    GeocodingService,
    InvalidLocationError,
    UnresolvedLocationError,
)


class GeocodingServiceTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.service = GeocodingService()

    def tearDown(self):
        cache.clear()

    def test_empty_or_whitespace_location(self):
        with self.assertRaises(InvalidLocationError):
            self.service.geocode("")

        with self.assertRaises(InvalidLocationError):
            self.service.geocode("   ")

    def test_direct_coordinates_usa(self):
        result = self.service.geocode("34.0522, -118.2437")
        self.assertEqual(result.precision, "coordinates")
        self.assertEqual(result.source, "coordinates")
        self.assertAlmostEqual(result.latitude, 34.0522, places=4)
        self.assertAlmostEqual(result.longitude, -118.2437, places=4)

    def test_direct_coordinates_outside_usa(self):
        # Paris, France: lat ~48.85, lon ~2.35 (outside USA longitude bounds)
        with self.assertRaises(InvalidLocationError):
            self.service.geocode("48.8566, 2.3522")

    def test_canadian_province_rejected(self):
        with self.assertRaises(InvalidLocationError):
            self.service.geocode("Toronto, ON")

        with self.assertRaises(InvalidLocationError):
            self.service.geocode("Vancouver, BC")

    def test_city_state_gazetteer_resolution(self):
        # Los Angeles, CA -> resolved via local Census Gazetteer
        res_la = self.service.geocode("Los Angeles, CA")
        self.assertIsInstance(res_la, GeocodingResult)
        self.assertEqual(res_la.precision, "approximate")
        self.assertEqual(res_la.source, "census_gazetteer")
        self.assertEqual(res_la.state, "CA")
        self.assertAlmostEqual(res_la.latitude, 34.019, places=2)
        self.assertAlmostEqual(res_la.longitude, -118.410, places=2)

        # Las Vegas, NV
        res_lv = self.service.geocode("Las Vegas, NV")
        self.assertEqual(res_lv.precision, "approximate")
        self.assertEqual(res_lv.source, "census_gazetteer")
        self.assertEqual(res_lv.state, "NV")
        self.assertAlmostEqual(res_lv.latitude, 36.233, places=2)
        self.assertAlmostEqual(res_lv.longitude, -115.264, places=2)

        # Full state name support: "Dallas, Texas"
        res_dallas = self.service.geocode("Dallas, Texas")
        self.assertEqual(res_dallas.precision, "approximate")
        self.assertEqual(res_dallas.state, "TX")

    def test_geocoding_caching(self):
        # First call (cold)
        res1 = self.service.geocode("Amarillo, TX")
        self.assertIsNotNone(res1)

        # Verify key is now in cache
        cached = cache.get("geocode:amarillo__tx")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["input"], "Amarillo, TX")

        # Second call returns identical result from cache
        res2 = self.service.geocode("Amarillo, TX")
        self.assertEqual(res1.latitude, res2.latitude)
        self.assertEqual(res1.longitude, res2.longitude)

    @patch("httpx.Client.get")
    def test_street_address_census_geocoder_exact_match(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "addressMatches": [
                    {
                        "coordinates": {"x": -77.035189, "y": 38.898702},
                        "matchedAddress": "1600 PENNSYLVANIA AVE NW, WASHINGTON, DC, 20500",
                        "addressComponents": {
                            "city": "WASHINGTON",
                            "state": "DC",
                            "zip": "20500",
                        },
                    }
                ]
            }
        }
        mock_get.return_value = mock_response

        result = self.service.geocode("1600 Pennsylvania Ave NW, Washington, DC 20500")
        self.assertEqual(result.precision, "exact")
        self.assertEqual(result.source, "census_geocoder")
        self.assertAlmostEqual(result.latitude, 38.898702, places=4)
        self.assertAlmostEqual(result.longitude, -77.035189, places=4)
        self.assertEqual(result.city, "WASHINGTON")
        self.assertEqual(result.state, "DC")

    @patch("httpx.Client.get")
    def test_street_address_failure_does_not_blindly_fallback(self, mock_get):
        # Per Phase 7 Rule 4: Do NOT blindly reuse station city-centroid fallback
        # for arbitrary user start/finish input when an address was specified.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "addressMatches": []
            }
        }
        mock_get.return_value = mock_response

        with self.assertRaises(UnresolvedLocationError):
            self.service.geocode("999999 Fake Rd, Dallas, TX")

    @patch("httpx.Client.get")
    def test_census_geocoder_timeout(self, mock_get):
        mock_get.side_effect = httpx.TimeoutException("Census Geocoder timed out")

        with self.assertRaises(GeocodingConnectionError):
            self.service.geocode("100 Main St, Dallas, TX")

    @patch("httpx.Client.get")
    def test_unresolvable_unknown_location(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "addressMatches": []
            }
        }
        mock_get.return_value = mock_response

        with self.assertRaises(UnresolvedLocationError):
            self.service.geocode("TotallyFakeCity12345, ZZ")

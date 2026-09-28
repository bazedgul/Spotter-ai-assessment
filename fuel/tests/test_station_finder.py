"""Unit tests for StationFinder service."""

from decimal import Decimal
from django.test import TestCase

from fuel.models import FuelStation
from fuel.services.station_finder import CandidateStation, StationFinder, haversine_miles


class StationFinderTestCase(TestCase):
    def setUp(self):
        StationFinder.clear_cache()
        # Route heading North along lon = -100.0 from lat = 35.0 to 37.0 (~138 miles)
        # Segments: (-100.0, 35.0) -> (-100.0, 36.0) -> (-100.0, 37.0)
        self.route_coords = [
            [-100.0, 35.0],
            [-100.0, 36.0],
            [-100.0, 37.0],
        ]
        self.geojson_route = {
            "type": "LineString",
            "coordinates": self.route_coords,
        }

    def tearDown(self):
        StationFinder.clear_cache()

    def test_station_inside_corridor(self):
        # Station at lon = -100.02, lat = 35.5 (approx 1.1 miles West of route)
        test_stations = [
            {
                'station_id': 101,
                'name': 'Inside Corridor Stop',
                'address': 'Hwy 83',
                'city': 'Shamrock',
                'state': 'TX',
                'price': Decimal('3.299'),
                'latitude': 35.5,
                'longitude': -100.02,
                'geocode_precision': 'address',
            }
        ]
        finder = StationFinder(corridor_miles=5.0, stations=test_stations)
        candidates = finder.find_stations_along_route(self.geojson_route)

        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c.station_id, 101)
        self.assertLessEqual(c.distance_to_route, 5.0)
        self.assertAlmostEqual(c.distance_to_route, 1.1, delta=0.5)
        # Lat 35.5 is halfway between 35.0 and 36.0 (~34.5 miles from start)
        self.assertAlmostEqual(c.mile_marker, 34.5, delta=2.0)

    def test_station_outside_corridor(self):
        # Station at lon = -100.25, lat = 35.5 (approx 14 miles West of route)
        test_stations = [
            {
                'station_id': 102,
                'name': 'Far Outside Corridor Stop',
                'address': 'County Rd 5',
                'city': 'FarAway',
                'state': 'TX',
                'price': Decimal('2.999'),
                'latitude': 35.5,
                'longitude': -100.25,
                'geocode_precision': 'address',
            }
        ]
        finder = StationFinder(corridor_miles=5.0, stations=test_stations)
        candidates = finder.find_stations_along_route(self.geojson_route)

        self.assertEqual(len(candidates), 0)

    def test_unresolved_station_ignored(self):
        # Station with None coordinates
        test_stations = [
            {
                'station_id': 103,
                'name': 'Unresolved Stop',
                'address': 'Unknown',
                'city': 'Lost',
                'state': 'TX',
                'price': Decimal('3.199'),
                'latitude': None,
                'longitude': None,
                'geocode_precision': 'unresolved',
            }
        ]
        finder = StationFinder(corridor_miles=5.0, stations=test_stations)
        candidates = finder.find_stations_along_route(self.geojson_route)

        self.assertEqual(len(candidates), 0)

    def test_stations_before_and_after_route(self):
        test_stations = [
            # Station physically before start: lat = 34.8 (South of lat 35.0 start)
            {
                'station_id': 201,
                'name': 'Before Start Stop',
                'address': 'Hwy 83 S',
                'city': 'Childress',
                'state': 'TX',
                'price': Decimal('3.00'),
                'latitude': 34.8,
                'longitude': -100.0,
                'geocode_precision': 'address',
            },
            # Station physically past finish: lat = 37.2 (North of lat 37.0 finish)
            {
                'station_id': 202,
                'name': 'Past Finish Stop',
                'address': 'Hwy 83 N',
                'city': 'Liberal',
                'state': 'KS',
                'price': Decimal('3.10'),
                'latitude': 37.2,
                'longitude': -100.0,
                'geocode_precision': 'address',
            },
            # Valid station along the route
            {
                'station_id': 203,
                'name': 'On Route Stop',
                'address': 'Hwy 83 Middle',
                'city': 'Perryton',
                'state': 'TX',
                'price': Decimal('3.20'),
                'latitude': 36.4,
                'longitude': -100.0,
                'geocode_precision': 'address',
            }
        ]
        finder = StationFinder(corridor_miles=5.0, stations=test_stations)
        candidates = finder.find_stations_along_route(self.geojson_route)

        # Only station 203 should be selected; 201 (before) and 202 (after) must be filtered out
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].station_id, 203)

    def test_multiple_stations_near_same_section(self):
        # Two stations at nearly the same mile marker (~mile 69) with different prices
        test_stations = [
            {
                'station_id': 301,
                'name': 'East Side Stop',
                'address': 'Exit 100 E',
                'city': 'Midway',
                'state': 'OK',
                'price': Decimal('3.499'),
                'latitude': 36.0,
                'longitude': -99.98,  # ~1.1 miles East
                'geocode_precision': 'address',
            },
            {
                'station_id': 302,
                'name': 'West Side Cheap Stop',
                'address': 'Exit 100 W',
                'city': 'Midway',
                'state': 'OK',
                'price': Decimal('3.199'),
                'latitude': 36.0,
                'longitude': -100.02,  # ~1.1 miles West
                'geocode_precision': 'address',
            },
        ]
        finder = StationFinder(corridor_miles=5.0, stations=test_stations)
        candidates = finder.find_stations_along_route(self.geojson_route)

        self.assertEqual(len(candidates), 2)
        # Should be ordered by mile marker, then price ascending (cheaper first)
        self.assertEqual(candidates[0].station_id, 302)
        self.assertEqual(candidates[1].station_id, 301)
        self.assertAlmostEqual(candidates[0].mile_marker, candidates[1].mile_marker, delta=0.5)

    def test_multi_segment_route_ordering(self):
        # Route making a right turn:
        # Segment 1: (-100.0, 35.0) -> (-100.0, 36.0) (North, ~69 mi)
        # Segment 2: (-100.0, 36.0) -> (-98.0, 36.0) (East, ~112 mi)
        # Total ~181 miles
        turn_route = [
            [-100.0, 35.0],
            [-100.0, 36.0],
            [-98.0, 36.0],
        ]
        test_stations = [
            # On second segment (East) at lon = -99.0, lat = 36.0 (~mile 125)
            {
                'station_id': 402,
                'name': 'Segment 2 Stop',
                'address': 'Hwy 60',
                'city': 'Enid',
                'state': 'OK',
                'price': Decimal('3.30'),
                'latitude': 36.0,
                'longitude': -99.0,
                'geocode_precision': 'address',
            },
            # On first segment (North) at lon = -100.0, lat = 35.5 (~mile 34.5)
            {
                'station_id': 401,
                'name': 'Segment 1 Stop',
                'address': 'Hwy 83',
                'city': 'Arnett',
                'state': 'OK',
                'price': Decimal('3.40'),
                'latitude': 35.5,
                'longitude': -100.0,
                'geocode_precision': 'address',
            },
        ]
        # Pass stations in reverse order to ensure finder correctly orders by route progress
        finder = StationFinder(corridor_miles=5.0, stations=test_stations)
        candidates = finder.find_stations_along_route(turn_route)

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].station_id, 401)
        self.assertEqual(candidates[1].station_id, 402)
        self.assertLess(candidates[0].mile_marker, candidates[1].mile_marker)

    def test_database_stations_integration(self):
        # Test loading from database via FuelStation model
        FuelStation.objects.create(
            station_id=9901,
            name="DB Test Stop",
            address="Hwy 83",
            city="Shamrock",
            state="TX",
            price=Decimal("3.2500"),
            latitude=35.5,
            longitude=-100.01,
            geocode_precision=FuelStation.GEOCODE_PRECISION_ADDRESS,
            is_active=True,
        )
        FuelStation.objects.create(
            station_id=9902,
            name="DB Unresolved Stop",
            address="Hwy 83",
            city="Nowhere",
            state="TX",
            price=Decimal("3.1000"),
            latitude=None,
            longitude=None,
            geocode_precision=FuelStation.GEOCODE_PRECISION_UNRESOLVED,
            is_active=True,
        )

        StationFinder.clear_cache()
        finder = StationFinder(corridor_miles=5.0)
        candidates = finder.find_stations_along_route(self.geojson_route)

        # Unresolved 9902 should not be in candidates
        candidate_ids = [c.station_id for c in candidates]
        self.assertIn(9901, candidate_ids)
        self.assertNotIn(9902, candidate_ids)

    def test_loop_route_mile_marker_accuracy(self):
        # Route forming a loop or interchange:
        # Segment 0: (-100.0, 35.0) -> (-100.0, 36.0) (North, ~69.1 mi)
        # Segment 1: (-100.0, 36.0) -> (-99.0, 36.0)  (East,  ~55.9 mi)
        # Segment 2: (-99.0, 36.0)  -> (-99.0, 35.0)  (South, ~69.1 mi)
        # Segment 3: (-99.0, 35.0)  -> (-100.0, 35.0) (West,  ~56.6 mi, returns to start lat/lon)
        # Segment 4: (-100.0, 35.0) -> (-100.0, 34.0) (South, ~69.1 mi)
        # Total route length ~320 miles.
        loop_route = [
            [-100.0, 35.0],
            [-100.0, 36.0],
            [-99.0, 36.0],
            [-99.0, 35.0],
            [-100.0, 35.0],
            [-100.0, 34.0],
        ]
        # Station at (-99.5, 35.0), located halfway along Segment 3 (~222.4 miles from start)
        test_stations = [
            {
                'station_id': 888,
                'name': 'Loop Segment 3 Stop',
                'address': 'Hwy 152',
                'city': 'Cordell',
                'state': 'OK',
                'price': Decimal('3.15'),
                'latitude': 35.0,
                'longitude': -99.5,
                'geocode_precision': 'address',
            }
        ]
        finder = StationFinder(corridor_miles=5.0, stations=test_stations)
        candidates = finder.find_stations_along_route(loop_route)

        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c.station_id, 888)
        # Mile marker should be accurately ~222.4 miles, not corrupted by earlier (-100.0, 35.0) vertex
        self.assertAlmostEqual(c.mile_marker, 222.4, delta=2.0)
        self.assertLessEqual(c.distance_to_route, 5.0)

    def test_corridor_boundary_station_included(self):
        # Station exactly on route LineString (detour distance ~0)
        test_stations = [
            {
                'station_id': 701,
                'name': 'Exact Route Stop',
                'address': 'Hwy 83 Mile 50',
                'city': 'Gage',
                'state': 'OK',
                'price': Decimal('3.20'),
                'latitude': 35.72,
                'longitude': -100.0,
                'geocode_precision': 'address',
            }
        ]
        finder = StationFinder(corridor_miles=5.0, stations=test_stations)
        candidates = finder.find_stations_along_route(self.geojson_route)

        self.assertEqual(len(candidates), 1)
        self.assertAlmostEqual(candidates[0].distance_to_route, 0.0, delta=0.1)


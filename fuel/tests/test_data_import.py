"""Unit tests for FuelStation model and import_stations command."""

import os
import tempfile
from decimal import Decimal
from django.core.management import call_command
from django.db.utils import IntegrityError
from django.test import TestCase

from fuel.models import FuelStation


class FuelStationModelTestCase(TestCase):
    def setUp(self):
        self.station = FuelStation.objects.create(
            station_id=1001,
            name="Test Express Truck Stop",
            address="I-40, EXIT 100",
            city="Amarillo",
            state="TX",
            rack_id=500,
            price=Decimal("3.2590"),
            latitude=35.1981,
            longitude=-101.8331,
            geocode_precision=FuelStation.GEOCODE_PRECISION_ADDRESS,
            is_active=True,
            source="opis_csv"
        )

    def test_station_creation(self):
        self.assertEqual(self.station.station_id, 1001)
        self.assertEqual(self.station.name, "Test Express Truck Stop")
        self.assertEqual(self.station.state, "TX")
        self.assertEqual(self.station.price, Decimal("3.2590"))
        self.assertTrue(self.station.has_coordinates)
        self.assertEqual(self.station.geocode_precision, FuelStation.GEOCODE_PRECISION_ADDRESS)
        self.assertIn("Test Express Truck Stop (#1001)", str(self.station))

    def test_station_id_uniqueness(self):
        with self.assertRaises(IntegrityError):
            FuelStation.objects.create(
                station_id=1001,
                name="Another Stop",
                address="US-66",
                city="Amarillo",
                state="TX",
                price=Decimal("3.4990"),
            )

    def test_nullable_coordinates_and_precision_choice(self):
        station_no_coords = FuelStation.objects.create(
            station_id=2002,
            name="Pending Geocode Stop",
            address="I-10, EXIT 50",
            city="Blythe",
            state="CA",
            price=Decimal("4.1590"),
            latitude=None,
            longitude=None,
            geocode_precision=FuelStation.GEOCODE_PRECISION_UNRESOLVED,
        )
        self.assertFalse(station_no_coords.has_coordinates)
        self.assertEqual(station_no_coords.geocode_precision, FuelStation.GEOCODE_PRECISION_UNRESOLVED)


class ImportStationsCommandTestCase(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = os.path.join(self.temp_dir.name, "test_prices.csv")
        self.cache_path = os.path.join(self.temp_dir.name, "test_cache.json")
        self.gazetteer_path = os.path.join(self.temp_dir.name, "test_gaz.txt")

        # Create dummy mini-Gazetteer for testing
        with open(self.gazetteer_path, "w", encoding="utf-8") as f:
            f.write("USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG\n")
            f.write("TX\t1234567\t12345678\tAmarillo city\t25\tA\t100000\t1000\t50.0\t0.5\t35.1981\t-101.8331\n")
            f.write("OK\t7654321\t87654321\tBig Cabin town\t43\tA\t50000\t500\t2.0\t0.1\t36.5423\t-95.2220\n")

        # Create sample test CSV
        csv_content = """OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price
100,PILOT TRAVEL CENTER #1,"I-40, EXIT 10",Amarillo,TX,101,3.899
100,PILOT TRAVEL CENTER #1,"I-40, EXIT 10",Amarillo,TX,101,3.299
100,PILOT TRAVEL CENTER #1,"I-40, EXIT 10",Amarillo,TX,101,3.599
200,WOODSHED OF BIG CABIN,"I-44, EXIT 283",Big Cabin,OK,202,2.999
200,WOODSHED OF BIG CABIN,"I-44, EXIT 283",Big Cabin,OK,202,2.999
300,ONTARIO PLAZA,"HWY 401",London,ON,303,3.450
400,MALFORMED PRICE,"US-50",Montrose,CO,404,invalid_price
,MISSING ID,"US-60",Phoenix,AZ,505,3.199
"""
        with open(self.csv_path, "w", encoding="utf-8") as f:
            f.write(csv_content)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_import_and_deduplication(self):
        call_command(
            'import_stations',
            csv=self.csv_path,
            cache_file=self.cache_path,
            gazetteer_file=self.gazetteer_path,
            no_network=True
        )

        # Total stations in DB should be 2 (ID 100, ID 200). ID 300 is Canadian (excluded). 400 & missing ID are malformed (skipped).
        self.assertEqual(FuelStation.objects.count(), 2)

        # Station 100 had prices [3.899, 3.299, 3.599] -> Lowest price rule: 3.2990
        s100 = FuelStation.objects.get(station_id=100)
        self.assertEqual(s100.price, Decimal("3.2990"))
        self.assertEqual(s100.city, "Amarillo")
        self.assertEqual(s100.state, "TX")
        self.assertEqual(s100.geocode_precision, FuelStation.GEOCODE_PRECISION_CITY_FALLBACK)
        self.assertAlmostEqual(s100.latitude, 35.1981, places=4)

        # Station 200 had duplicate identical rows -> 1 record kept
        s200 = FuelStation.objects.get(station_id=200)
        self.assertEqual(s200.price, Decimal("2.9990"))
        self.assertEqual(s200.state, "OK")

        # Non-USA station 300 was excluded
        self.assertFalse(FuelStation.objects.filter(station_id=300).exists())

    def test_idempotent_reimport(self):
        # First import
        call_command(
            'import_stations',
            csv=self.csv_path,
            cache_file=self.cache_path,
            gazetteer_file=self.gazetteer_path,
            no_network=True
        )
        first_count = FuelStation.objects.count()

        # Second import
        call_command(
            'import_stations',
            csv=self.csv_path,
            cache_file=self.cache_path,
            gazetteer_file=self.gazetteer_path,
            no_network=True
        )
        second_count = FuelStation.objects.count()

        self.assertEqual(first_count, second_count)
        self.assertEqual(second_count, 2)

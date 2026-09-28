"""Unit tests for FuelStation model and data import preparation."""

from decimal import Decimal
from django.test import TestCase
from django.db.utils import IntegrityError
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
                station_id=1001,  # Duplicate station_id
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

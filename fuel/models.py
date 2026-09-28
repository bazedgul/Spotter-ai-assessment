"""Data models for fuel route assessment."""

from django.db import models


class FuelStation(models.Model):
    """
    Represents a fuel station / truck stop candidate along USA driving routes.
    Data is preprocessed and imported from OPIS fuel-prices CSV.
    """
    GEOCODE_PRECISION_ADDRESS = 'address'
    GEOCODE_PRECISION_CITY_FALLBACK = 'city_fallback'
    GEOCODE_PRECISION_UNRESOLVED = 'unresolved'

    GEOCODE_PRECISION_CHOICES = [
        (GEOCODE_PRECISION_ADDRESS, 'Address / Intersection Match'),
        (GEOCODE_PRECISION_CITY_FALLBACK, 'City Centroid Fallback'),
        (GEOCODE_PRECISION_UNRESOLVED, 'Unresolved'),
    ]

    station_id = models.IntegerField(
        unique=True,
        db_index=True,
        help_text="Unique OPIS Truckstop ID from CSV"
    )
    name = models.CharField(
        max_length=255,
        help_text="Truckstop name"
    )
    address = models.CharField(
        max_length=255,
        help_text="Street, exit, or highway address"
    )
    city = models.CharField(
        max_length=100,
        db_index=True,
        help_text="City name"
    )
    state = models.CharField(
        max_length=10,
        db_index=True,
        help_text="Two-letter US state code (e.g., TX, CA)"
    )
    rack_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Rack ID from source CSV if present"
    )
    price = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        help_text="Retail fuel price per gallon (USD)"
    )
    latitude = models.FloatField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Latitude coordinate in decimal degrees"
    )
    longitude = models.FloatField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Longitude coordinate in decimal degrees"
    )
    geocode_precision = models.CharField(
        max_length=50,
        choices=GEOCODE_PRECISION_CHOICES,
        default=GEOCODE_PRECISION_ADDRESS,
        help_text="Precision level of station coordinates"
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this station is active for route optimization"
    )
    source = models.CharField(
        max_length=100,
        default='opis_csv',
        help_text="Source identifier for data lineage"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Fuel Station"
        verbose_name_plural = "Fuel Stations"
        ordering = ['station_id']
        indexes = [
            models.Index(fields=['state', 'is_active'], name='fuel_station_state_active_idx'),
            models.Index(fields=['latitude', 'longitude'], name='fuel_station_coords_idx'),
        ]

    def __str__(self) -> str:
        return f"{self.name} (#{self.station_id}) - {self.city}, {self.state} [${self.price}]"

    @property
    def has_coordinates(self) -> bool:
        """Returns True if station has valid coordinates for spatial indexing."""
        return self.latitude is not None and self.longitude is not None

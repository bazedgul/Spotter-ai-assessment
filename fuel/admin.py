"""Django admin configuration for fuel app."""

from django.contrib import admin
from fuel.models import FuelStation


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = ('station_id', 'name', 'city', 'state', 'price', 'geocode_precision', 'is_active')
    list_filter = ('state', 'geocode_precision', 'is_active')
    search_fields = ('station_id', 'name', 'city', 'address')
    ordering = ('station_id',)

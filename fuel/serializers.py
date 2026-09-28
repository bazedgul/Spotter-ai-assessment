"""Serializers for fuel routing API."""

from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """Validates start and finish locations."""
    start = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        help_text="Start location (e.g., 'Los Angeles, CA' or '34.0522, -118.2437')",
    )
    finish = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        help_text="Finish location (e.g., 'New York, NY' or '40.7128, -74.0060')",
    )
    corridor_miles = serializers.FloatField(
        required=False,
        default=None,
        min_value=0.1,
        max_value=50.0,
        help_text="Optional route corridor radius in miles (defaults to 5.0)",
    )

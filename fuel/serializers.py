"""Serializers for fuel routing API."""

from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """Validates start and finish locations."""
    start = serializers.CharField(required=True, allow_blank=False, trim_whitespace=True)
    finish = serializers.CharField(required=True, allow_blank=False, trim_whitespace=True)

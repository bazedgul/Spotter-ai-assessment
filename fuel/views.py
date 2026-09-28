"""Views for fuel app."""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.views import View
from django.http import HttpResponse


class RouteCalculateView(APIView):
    """Calculates route and optimal fuel stops between start and finish USA locations."""

    def post(self, request, *args, **kwargs):
        return Response({"message": "Endpoint bootstrap ready"}, status=status.HTTP_200_OK)


class RouteMapView(View):
    """Renders interactive Leaflet map for a calculated route."""

    def get(self, request, route_id, *args, **kwargs):
        return HttpResponse("Map bootstrap ready", content_type="text/plain")

"""Views for fuel routing app."""

import logging
from django.http import HttpResponse
from django.views import View
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from fuel.serializers import RouteRequestSerializer
from fuel.services.fuel_optimizer import NoFeasibleFuelPlanError
from fuel.services.geocoding import (
    GeocodingConnectionError,
    InvalidLocationError,
    UnresolvedLocationError,
)
from fuel.services.route_service import RouteService
from fuel.services.routing import (
    InvalidRouteCoordinatesError,
    NoRouteFoundError,
    RoutingConnectionError,
    RoutingError,
)

logger = logging.getLogger(__name__)


class RouteCalculateView(APIView):
    """Calculates route and optimal fuel stops between start and finish USA locations."""

    def post(self, request, *args, **kwargs):
        """Processes route calculation request.

        Expects JSON payload with 'start' and 'finish' locations, and optional 'corridor_miles'.
        """
        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "error": {
                        "code": "INVALID_INPUT",
                        "message": "Invalid request parameters.",
                        "details": serializer.errors,
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated_data = serializer.validated_data
        start_location = validated_data["start"]
        finish_location = validated_data["finish"]
        corridor_miles = validated_data.get("corridor_miles")

        route_service = RouteService(corridor_miles=corridor_miles)

        try:
            result = route_service.calculate_route_and_fuel_plan(
                start=start_location,
                finish=finish_location,
                corridor_miles=corridor_miles,
            )
            return Response(result, status=status.HTTP_200_OK)

        except InvalidLocationError as exc:
            logger.warning(f"Invalid location input: {exc}")
            return Response(
                {
                    "error": {
                        "code": "INVALID_LOCATION",
                        "message": str(exc),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except UnresolvedLocationError as exc:
            logger.warning(f"Location could not be resolved: {exc}")
            return Response(
                {
                    "error": {
                        "code": "UNRESOLVED_LOCATION",
                        "message": str(exc),
                    }
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        except InvalidRouteCoordinatesError as exc:
            logger.warning(f"Invalid route coordinates: {exc}")
            return Response(
                {
                    "error": {
                        "code": "INVALID_COORDINATES",
                        "message": str(exc),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except NoRouteFoundError as exc:
            logger.warning(f"No route found between locations: {exc}")
            return Response(
                {
                    "error": {
                        "code": "NO_ROUTE_FOUND",
                        "message": str(exc),
                    }
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        except NoFeasibleFuelPlanError as exc:
            logger.warning(f"No feasible fuel plan: {exc}")
            return Response(
                {
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                    }
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        except (GeocodingConnectionError, RoutingConnectionError) as exc:
            logger.error(f"External service connectivity error: {exc}")
            return Response(
                {
                    "error": {
                        "code": "EXTERNAL_SERVICE_ERROR",
                        "message": "An external routing or geocoding service is temporarily unavailable. Please try again.",
                    }
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        except RoutingError as exc:
            logger.error(f"Routing provider error: {exc}")
            return Response(
                {
                    "error": {
                        "code": "ROUTING_SERVICE_ERROR",
                        "message": "Error returned by routing service.",
                    }
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        except Exception as exc:
            logger.exception(f"Unexpected error during route calculation: {exc}")
            return Response(
                {
                    "error": {
                        "code": "INTERNAL_SERVER_ERROR",
                        "message": "An unexpected error occurred while processing the route calculation.",
                    }
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RouteMapView(View):
    """Renders interactive Leaflet map for a calculated route (Phase 11)."""

    def get(self, request, route_id, *args, **kwargs):
        return HttpResponse("Map bootstrap ready", content_type="text/plain")

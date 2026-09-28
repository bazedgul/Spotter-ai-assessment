"""Route orchestration service coordinating geocoding, routing, station finding, and fuel optimization.

Requirements from ASSESSMENT_PLAN.md:
- Orchestrates the full route calculation pipeline:
    1. Geocoding start and finish USA locations (Census Geocoder / Gazetteer)
    2. Requesting route distance, duration, and GeoJSON geometry (OSRM)
    3. Spatial candidate fuel station search along the route corridor (Shapely / STRtree)
    4. Optimal fuel stop planning (Greedy lookahead optimizer)
- Centralized caching across pipeline steps to minimize external API calls.
- Fast and deterministic execution.
- Pure and testable independent of HTTP views.
"""

import hashlib
import logging
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.cache import cache

from fuel.services.fuel_optimizer import FuelOptimizationPlan, FuelOptimizer
from fuel.services.geocoding import GeocodingResult, GeocodingService
from fuel.services.routing import RoutingResult, RoutingService
from fuel.services.station_finder import StationFinder

logger = logging.getLogger(__name__)


class RouteService:
    """Orchestrates geocoding, routing, station finding, and fuel optimization into an end-to-end pipeline."""

    def __init__(
        self,
        geocoding_service: Optional[GeocodingService] = None,
        routing_service: Optional[RoutingService] = None,
        station_finder: Optional[StationFinder] = None,
        fuel_optimizer: Optional[FuelOptimizer] = None,
        corridor_miles: Optional[float] = None,
    ):
        """Initializes RouteService with optional injected service dependencies for testing or custom configuration."""
        self.geocoding_service = geocoding_service or GeocodingService()
        self.routing_service = routing_service or RoutingService()
        self.corridor_miles = (
            corridor_miles
            if corridor_miles is not None
            else getattr(settings, "FUEL_STOP_CORRIDOR_MILES", 5.0)
        )
        self.station_finder = station_finder or StationFinder(corridor_miles=self.corridor_miles)
        self.fuel_optimizer = fuel_optimizer or FuelOptimizer()

    def _make_route_id(
        self,
        start_lat: float,
        start_lon: float,
        finish_lat: float,
        finish_lon: float
    ) -> str:
        """Generates a deterministic 16-character identifier for a route pair."""
        raw = f"{start_lat:.5f},{start_lon:.5f}:{finish_lat:.5f},{finish_lon:.5f}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def calculate_route_and_fuel_plan(
        self,
        start: str,
        finish: str,
        corridor_miles: Optional[float] = None,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """Executes the end-to-end routing and fuel optimization pipeline.

        Args:
            start: Start location string (address, city/state, or lat,lon).
            finish: Finish location string (address, city/state, or lat,lon).
            corridor_miles: Optional search corridor override in miles.
            use_cache: Whether to read/write results from/to Django cache.

        Returns:
            Dictionary matching the API response specification with start, finish, route, vehicle, fuel,
            fuel_stops, and map_url.

        Raises:
            InvalidLocationError: If start or finish is invalid or outside the USA.
            UnresolvedLocationError: If start or finish cannot be resolved to coordinates.
            InvalidRouteCoordinatesError: If resolved coordinates are out of valid range.
            NoRouteFoundError: If no drivable route connects start and finish.
            NoFeasibleFuelPlanError: If vehicle cannot complete route within 500-mile range.
            GeocodingConnectionError: If geocoder service times out or is unreachable.
            RoutingConnectionError: If OSRM routing service times out or is unreachable.
        """
        norm_start = start.strip()
        norm_finish = finish.strip()
        eff_corridor = corridor_miles if corridor_miles is not None else self.corridor_miles

        norm_key = f"{norm_start.lower()}:{norm_finish.lower()}:{eff_corridor:.1f}"
        hash_key = hashlib.sha256(norm_key.encode("utf-8")).hexdigest()
        full_cache_key = f"full_route_{hash_key}"

        if use_cache:
            cached_result = cache.get(full_cache_key)
            if cached_result is not None:
                logger.debug(f"Full route cache hit for key: {full_cache_key}")
                return cached_result

        # Step 1: Geocode start and finish locations
        logger.info(f"Geocoding route start: '{norm_start}' and finish: '{norm_finish}'")
        start_result: GeocodingResult = self.geocoding_service.geocode(norm_start, use_cache=use_cache)
        finish_result: GeocodingResult = self.geocoding_service.geocode(norm_finish, use_cache=use_cache)

        # Step 2: Calculate driving route via OSRM
        logger.info(
            f"Calculating driving route: ({start_result.latitude}, {start_result.longitude}) -> "
            f"({finish_result.latitude}, {finish_result.longitude})"
        )
        route_result: RoutingResult = self.routing_service.calculate_route(
            start_lat=start_result.latitude,
            start_lon=start_result.longitude,
            finish_lat=finish_result.latitude,
            finish_lon=finish_result.longitude,
            use_cache=use_cache,
        )

        # Step 3: Find candidate fuel stations along the route corridor
        logger.info(
            f"Searching fuel stations along route corridor ({eff_corridor} miles) for {route_result.distance_miles:.1f} mi route"
        )
        candidate_stations = self.station_finder.find_stations_along_route(
            route_geometry=route_result.geometry,
            corridor_miles=eff_corridor,
        )
        logger.info(f"Found {len(candidate_stations)} candidate fuel stations in corridor.")

        # Step 4: Calculate optimal fuel stops
        logger.info("Computing optimal refueling plan...")
        fuel_plan: FuelOptimizationPlan = self.fuel_optimizer.optimize(
            candidate_stations=candidate_stations,
            route_distance_miles=route_result.distance_miles,
        )

        # Step 5: Format structured response
        route_id = self._make_route_id(
            start_lat=start_result.latitude,
            start_lon=start_result.longitude,
            finish_lat=finish_result.latitude,
            finish_lon=finish_result.longitude,
        )

        response_data: Dict[str, Any] = {
            "start": start_result.to_dict(),
            "finish": finish_result.to_dict(),
            "route": {
                "distance_miles": route_result.distance_miles,
                "distance_meters": route_result.distance_meters,
                "duration_minutes": round(route_result.duration_seconds / 60.0, 1),
                "duration_hours": route_result.duration_hours,
                "geometry": route_result.geometry,
            },
            "vehicle": {
                "max_range_miles": int(self.fuel_optimizer.max_range),
                "mpg": int(self.fuel_optimizer.mpg),
                "tank_capacity_gallons": int(self.fuel_optimizer.tank_capacity),
            },
            "fuel": {
                "starting_fuel_gallons": fuel_plan.starting_fuel_gallons,
                "total_consumed_gallons": fuel_plan.total_consumed_gallons,
                "total_purchased_gallons": fuel_plan.total_purchased_gallons,
                "total_cost": fuel_plan.total_cost,
                "fuel_remaining_gallons": fuel_plan.fuel_remaining_gallons,
            },
            "fuel_stops": [stop.to_dict() for stop in fuel_plan.fuel_stops],
            "map_url": f"/api/v1/routes/map/{route_id}/",
        }

        if use_cache:
            cache_timeout = getattr(settings, "CACHES", {}).get("default", {}).get("TIMEOUT", 86400)
            cache.set(full_cache_key, response_data, timeout=cache_timeout)
            # Store route plan for map retrieval (Phase 11)
            cache.set(f"route_map:{route_id}", response_data, timeout=cache_timeout)

        return response_data

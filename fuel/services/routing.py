"""Routing service for calculating driving routes via OSRM.

Requirements from ASSESSMENT_PLAN.md:
- Provider: OSRM (Open Source Routing Machine).
- Configurable base URL via Django settings / environment (ROUTING_API_BASE_URL).
- Requests full route geometry suitable for Leaflet rendering (overview=full, geometries=geojson).
- Prefer GeoJSON LineString geometry.
- Returns distance (meters and converted miles), duration (seconds and hours), and geometry.
- Normal route requests require approximately 1 external OSRM call.
- Route caching via Django cache framework for repeated identical requests.
- No repeated routing calls for individual fuel stations.
"""

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class RoutingError(Exception):
    """Base exception for routing failures."""
    pass


class RoutingConnectionError(RoutingError):
    """Raised when the routing provider cannot be reached or times out."""
    pass


class NoRouteFoundError(RoutingError):
    """Raised when no drivable route exists between coordinates."""
    pass


class InvalidRouteCoordinatesError(RoutingError):
    """Raised when coordinates provided for routing are invalid."""
    pass


@dataclass
class RoutingResult:
    """Normalized routing response containing geometry, distance, and duration."""
    start_coordinates: Tuple[float, float]  # (latitude, longitude)
    finish_coordinates: Tuple[float, float]  # (latitude, longitude)
    distance_meters: float
    distance_miles: float
    duration_seconds: float
    duration_hours: float
    geometry: Dict[str, Any]  # GeoJSON LineString: {"type": "LineString", "coordinates": [[lon, lat], ...]}
    waypoints: List[Dict[str, Any]]
    is_cached: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the result to a JSON-compatible dictionary."""
        return {
            "start_coordinates": list(self.start_coordinates),
            "finish_coordinates": list(self.finish_coordinates),
            "distance_meters": round(self.distance_meters, 1),
            "distance_miles": round(self.distance_miles, 2),
            "duration_seconds": round(self.duration_seconds, 1),
            "duration_hours": round(self.duration_hours, 2),
            "geometry": self.geometry,
            "waypoints": self.waypoints,
            "is_cached": self.is_cached,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], is_cached: bool = True) -> "RoutingResult":
        """Reconstructs RoutingResult from cached dictionary."""
        return cls(
            start_coordinates=tuple(data["start_coordinates"]),
            finish_coordinates=tuple(data["finish_coordinates"]),
            distance_meters=float(data["distance_meters"]),
            distance_miles=float(data["distance_miles"]),
            duration_seconds=float(data["duration_seconds"]),
            duration_hours=float(data["duration_hours"]),
            geometry=data["geometry"],
            waypoints=data.get("waypoints", []),
            is_cached=is_cached,
        )


class RoutingService:
    """Service to compute driving routes using OSRM."""

    # Conversion constant: 1 mile = 1609.344 meters
    METERS_PER_MILE = 1609.344

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        client: Optional[httpx.Client] = None
    ):
        """Initializes the routing service with configurable base URL and timeout."""
        raw_url = base_url or getattr(settings, "ROUTING_API_BASE_URL", "https://router.project-osrm.org")
        self.base_url = raw_url.rstrip("/")
        self.timeout = timeout if timeout is not None else getattr(settings, "ROUTING_TIMEOUT_SECONDS", 15.0)
        self._external_client = client

    def _validate_coordinates(self, lat: float, lon: float, label: str = "Location") -> None:
        """Validates that latitude and longitude fall within legitimate geographic bounds."""
        if not (-90.0 <= lat <= 90.0):
            raise InvalidRouteCoordinatesError(f"{label} latitude {lat} is out of bounds [-90, 90].")
        if not (-180.0 <= lon <= 180.0):
            raise InvalidRouteCoordinatesError(f"{label} longitude {lon} is out of bounds [-180, 180].")

    def _make_cache_key(
        self,
        start_lat: float,
        start_lon: float,
        finish_lat: float,
        finish_lon: float
    ) -> str:
        """Constructs a deterministic cache key for start and finish coordinates rounded to ~1m precision."""
        return f"route:{start_lon:.5f},{start_lat:.5f}:{finish_lon:.5f},{finish_lat:.5f}"

    def calculate_route(
        self,
        start_lat: float,
        start_lon: float,
        finish_lat: float,
        finish_lon: float,
        use_cache: bool = True
    ) -> RoutingResult:
        """Calculates a driving route between start and finish coordinates.

        Args:
            start_lat: Start latitude in degrees.
            start_lon: Start longitude in degrees.
            finish_lat: Finish latitude in degrees.
            finish_lon: Finish longitude in degrees.
            use_cache: Whether to check and store results in Django cache.

        Returns:
            RoutingResult containing distance, duration, and GeoJSON geometry.

        Raises:
            InvalidRouteCoordinatesError: If coordinates are out of valid range.
            RoutingConnectionError: If OSRM is unreachable or times out.
            NoRouteFoundError: If no drivable route connects the two coordinates.
            RoutingError: On malformed responses or unexpected provider errors.
        """
        self._validate_coordinates(start_lat, start_lon, label="Start")
        self._validate_coordinates(finish_lat, finish_lon, label="Finish")

        cache_key = self._make_cache_key(start_lat, start_lon, finish_lat, finish_lon)

        if use_cache:
            cached_data = cache.get(cache_key)
            if cached_data is not None:
                logger.debug(f"Routing cache hit for key: {cache_key}")
                return RoutingResult.from_dict(cached_data, is_cached=True)

        # OSRM coordinate order: {longitude},{latitude}
        url = f"{self.base_url}/route/v1/driving/{start_lon},{start_lat};{finish_lon},{finish_lat}"
        params = {
            "overview": "full",
            "geometries": "geojson",
        }

        logger.info(f"Requesting OSRM route: {start_lat},{start_lon} -> {finish_lat},{finish_lon}")

        try:
            if self._external_client:
                response = self._external_client.get(url, params=params, timeout=self.timeout)
            else:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.get(url, params=params)
        except httpx.TimeoutException as exc:
            logger.error(f"OSRM request timed out after {self.timeout}s: {exc}")
            raise RoutingConnectionError(
                f"OSRM routing service timed out after {self.timeout}s at {self.base_url}."
            ) from exc
        except httpx.RequestError as exc:
            logger.error(f"OSRM network error: {exc}")
            raise RoutingConnectionError(
                f"Failed to connect to OSRM routing service at {self.base_url}: {exc}"
            ) from exc

        if response.status_code != 200:
            logger.error(f"OSRM returned unexpected HTTP status {response.status_code}: {response.text}")
            raise RoutingConnectionError(
                f"OSRM service returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            payload = response.json()
        except Exception as exc:
            logger.error(f"Failed to parse OSRM JSON response: {exc}")
            raise RoutingError("Invalid JSON returned by OSRM routing service.") from exc

        code = payload.get("code")
        if code != "Ok":
            message = payload.get("message", "No detailed message provided by OSRM.")
            if code in ("NoRoute", "NoSegment"):
                logger.warning(f"OSRM reported no drivable route: code={code}, msg={message}")
                raise NoRouteFoundError(
                    f"No drivable route found between ({start_lat}, {start_lon}) and ({finish_lat}, {finish_lon}): {message}"
                )
            logger.error(f"OSRM returned error code {code}: {message}")
            raise RoutingError(f"OSRM error [{code}]: {message}")

        routes = payload.get("routes")
        if not routes or not isinstance(routes, list):
            raise RoutingError("OSRM response did not contain any valid routes.")

        primary_route = routes[0]
        distance_meters = float(primary_route.get("distance", 0.0))
        duration_seconds = float(primary_route.get("duration", 0.0))
        distance_miles = distance_meters / self.METERS_PER_MILE
        duration_hours = duration_seconds / 3600.0

        geometry = primary_route.get("geometry")
        if not geometry or geometry.get("type") != "LineString" or not geometry.get("coordinates"):
            raise RoutingError("OSRM route did not return a valid GeoJSON LineString geometry.")

        waypoints = payload.get("waypoints", [])

        result = RoutingResult(
            start_coordinates=(start_lat, start_lon),
            finish_coordinates=(finish_lat, finish_lon),
            distance_meters=distance_meters,
            distance_miles=distance_miles,
            duration_seconds=duration_seconds,
            duration_hours=duration_hours,
            geometry=geometry,
            waypoints=waypoints,
            is_cached=False,
        )

        if use_cache:
            cache_timeout = getattr(settings, "CACHES", {}).get("default", {}).get("TIMEOUT", 86400)
            cache.set(cache_key, result.to_dict(), timeout=cache_timeout)
            logger.debug(f"Cached route result under key: {cache_key}")

        return result

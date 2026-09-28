"""Spatial station finder service for locating fuel stations along a route corridor.

Requirements from ASSESSMENT_PLAN.md:
- Uses existing locally imported FuelStation dataset.
- Uses Shapely / STRtree spatial indexing for candidate search.
- Only stations with valid latitude and longitude participate in spatial search.
- Unresolved stations in the database are ignored (never fabricated coordinates).
- Default corridor: FUEL_STOP_CORRIDOR_MILES = 5.0 (configurable via settings / parameter).
- Searches along the entire route geometry (not just start/finish points).
- Computes mile_marker (distance along route from start) and distance_to_route (detour distance).
- Filters out stations physically before start or past destination.
- Orders candidates strictly by route direction (mile_marker ascending, then price ascending).
- Zero external routing or geocoding calls during spatial search.
- Purely testable independent of API views and optimizer.
"""

import bisect
import logging
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from django.conf import settings
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from fuel.models import FuelStation

logger = logging.getLogger(__name__)

# Mean Earth radius in miles (WGS84 spherical approximation)
EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes great-circle distance in miles between two coordinates using the Haversine formula."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2.0) ** 2) + math.cos(phi1) * math.cos(phi2) * (math.sin(dlambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_MILES * c


class StationFinderError(Exception):
    """Base exception for station finder errors."""
    pass


@dataclass
class CandidateStation:
    """Represents a fuel station identified within the route corridor with route position metadata."""
    station_id: int
    name: str
    address: str
    city: str
    state: str
    price: Decimal
    latitude: float
    longitude: float
    geocode_precision: str
    mile_marker: float  # Miles from route start to the projected point on route
    distance_to_route: float  # Perpendicular distance in miles from station to route

    def to_dict(self) -> Dict[str, Any]:
        """Serializes candidate station to a JSON-compatible dictionary."""
        return {
            "station_id": self.station_id,
            "name": self.name,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "price": float(self.price),
            "price_decimal": str(self.price),
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "geocode_precision": self.geocode_precision,
            "mile_marker": round(self.mile_marker, 2),
            "distance_to_route": round(self.distance_to_route, 2),
        }


class StationFinder:
    """Finds and ranks fuel stations within a geographic corridor along a route LineString."""

    _cached_db_stations: Optional[List[Dict[str, Any]]] = None
    _cached_db_tree: Optional[STRtree] = None

    def __init__(
        self,
        corridor_miles: Optional[float] = None,
        stations: Optional[List[Dict[str, Any]]] = None
    ):
        """Initializes StationFinder.

        Args:
            corridor_miles: Maximum off-route perpendicular distance in miles (defaults to settings).
            stations: Optional explicit list of station dictionaries for testing or custom dataset.
                      If None, loads active stations with valid coordinates from database.
        """
        self.default_corridor_miles = (
            corridor_miles
            if corridor_miles is not None
            else getattr(settings, "FUEL_STOP_CORRIDOR_MILES", 5.0)
        )

        if stations is not None:
            # Custom station dataset (used for unit tests or mocked data)
            self._stations, self._tree = self._build_index(stations)
        else:
            # Database stations (lazy-loaded class-level singleton)
            if StationFinder._cached_db_stations is None or StationFinder._cached_db_tree is None:
                self._load_database_stations()
            self._stations = StationFinder._cached_db_stations
            self._tree = StationFinder._cached_db_tree

    @classmethod
    def clear_cache(cls) -> None:
        """Clears the cached in-memory station index (useful after database updates)."""
        cls._cached_db_stations = None
        cls._cached_db_tree = None

    def _load_database_stations(self) -> None:
        """Loads active stations with non-null coordinates from the database and builds STRtree."""
        qs = FuelStation.objects.filter(
            is_active=True,
            latitude__isnull=False,
            longitude__isnull=False
        ).values(
            'station_id',
            'name',
            'address',
            'city',
            'state',
            'price',
            'latitude',
            'longitude',
            'geocode_precision'
        )

        station_records = list(qs)
        stations, tree = self._build_index(station_records)
        StationFinder._cached_db_stations = stations
        StationFinder._cached_db_tree = tree
        logger.info(f"Loaded {len(stations)} active stations into StationFinder spatial index.")

    def _build_index(self, station_list: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[STRtree]]:
        """Filters valid stations and constructs a Shapely STRtree index."""
        valid_stations = []
        points = []

        for st in station_list:
            lat = st.get('latitude')
            lon = st.get('longitude')
            if lat is not None and lon is not None:
                try:
                    lat_f = float(lat)
                    lon_f = float(lon)
                    if -90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0:
                        valid_stations.append(st)
                        points.append(Point(lon_f, lat_f))
                except (ValueError, TypeError):
                    continue

        if not points:
            return [], None

        tree = STRtree(points)
        return valid_stations, tree

    def find_stations_along_route(
        self,
        route_geometry: Union[Dict[str, Any], Sequence[Tuple[float, float]], Sequence[List[float]]],
        corridor_miles: Optional[float] = None
    ) -> List[CandidateStation]:
        """Identifies candidate fuel stations located within the specified corridor along the route.

        Args:
            route_geometry: GeoJSON geometry dict, or sequence of [lon, lat] coordinate pairs.
            corridor_miles: Search corridor radius in miles. Defaults to configured corridor.

        Returns:
            List of CandidateStation instances ordered by mile_marker ascending, then price ascending.
        """
        max_corridor = corridor_miles if corridor_miles is not None else self.default_corridor_miles

        # Extract coordinates
        if isinstance(route_geometry, dict):
            coords = route_geometry.get("coordinates", [])
        else:
            coords = list(route_geometry)

        if len(coords) < 2 or not self._stations or self._tree is None:
            return []

        # Ensure coordinates are tuples of floats (lon, lat)
        route_points = [(float(c[0]), float(c[1])) for c in coords]
        route_line = LineString(route_points)

        # 1. Compute cumulative geographic distances (miles) and Euclidean segment lengths (degrees)
        cum_distances = [0.0]
        cum_euc_distances = [0.0]
        for i in range(len(route_points) - 1):
            seg_dist = haversine_miles(
                route_points[i][1], route_points[i][0],
                route_points[i + 1][1], route_points[i + 1][0]
            )
            cum_distances.append(cum_distances[-1] + seg_dist)
            cum_euc_distances.append(
                cum_euc_distances[-1] + math.hypot(
                    route_points[i + 1][0] - route_points[i][0],
                    route_points[i + 1][1] - route_points[i][1]
                )
            )

        total_route_length = cum_distances[-1]

        # 2. Query spatial index using a conservative degree buffer
        # At US latitudes (~24-50 N), 1 deg longitude >= ~44.3 miles.
        deg_buffer = (max_corridor / 40.0) + 0.02
        buffered_box = route_line.buffer(deg_buffer)
        candidate_indices = self._tree.query(buffered_box)

        if len(candidate_indices) == 0:
            return []

        # Vector representations of first and last non-zero segments to detect stations outside route endpoints
        first_seg_vec = (0.0, 0.0)
        for i in range(len(route_points) - 1):
            dx = route_points[i + 1][0] - route_points[i][0]
            dy = route_points[i + 1][1] - route_points[i][1]
            if dx != 0.0 or dy != 0.0:
                first_seg_vec = (dx, dy)
                break

        last_seg_vec = (0.0, 0.0)
        for i in range(len(route_points) - 1, 0, -1):
            dx = route_points[i][0] - route_points[i - 1][0]
            dy = route_points[i][1] - route_points[i - 1][1]
            if dx != 0.0 or dy != 0.0:
                last_seg_vec = (dx, dy)
                break

        candidate_stations: List[CandidateStation] = []

        for idx in candidate_indices:
            st = self._stations[idx]
            st_lat = float(st['latitude'])
            st_lon = float(st['longitude'])
            st_pt = Point(st_lon, st_lat)

            # Project station onto route LineString
            proj_dist = route_line.project(st_pt)
            nearest_pt = route_line.interpolate(proj_dist)

            # Perpendicular detour distance in miles
            detour_dist_miles = haversine_miles(st_lat, st_lon, nearest_pt.y, nearest_pt.x)
            if detour_dist_miles > max_corridor:
                continue

            # Check if station is physically behind the start point
            if math.isclose(proj_dist, 0.0, abs_tol=1e-9):
                vec_to_st = (st_lon - route_points[0][0], st_lat - route_points[0][1])
                dot_start = (first_seg_vec[0] * vec_to_st[0]) + (first_seg_vec[1] * vec_to_st[1])
                if dot_start < 0:
                    continue  # Station is behind start

            # Check if station is physically past the destination point
            if math.isclose(proj_dist, route_line.length, abs_tol=1e-9):
                vec_from_dest = (st_lon - route_points[-1][0], st_lat - route_points[-1][1])
                dot_finish = (last_seg_vec[0] * vec_from_dest[0]) + (last_seg_vec[1] * vec_from_dest[1])
                if dot_finish > 0:
                    continue  # Station is past destination

            # Compute route mile marker along the journey
            # Find the segment containing nearest_pt via monotonic cumulative Euclidean distance
            seg_idx = bisect.bisect_right(cum_euc_distances, proj_dist) - 1
            seg_idx = max(0, min(seg_idx, len(route_points) - 2))

            dist_seg_start_to_near = haversine_miles(
                route_points[seg_idx][1], route_points[seg_idx][0],
                nearest_pt.y, nearest_pt.x
            )
            mile_marker = cum_distances[seg_idx] + dist_seg_start_to_near
            mile_marker = max(0.0, min(mile_marker, total_route_length))

            candidate_stations.append(
                CandidateStation(
                    station_id=int(st['station_id']),
                    name=st.get('name', ''),
                    address=st.get('address', ''),
                    city=st.get('city', ''),
                    state=st.get('state', ''),
                    price=Decimal(str(st['price'])),
                    latitude=st_lat,
                    longitude=st_lon,
                    geocode_precision=st.get('geocode_precision', 'address'),
                    mile_marker=mile_marker,
                    distance_to_route=detour_dist_miles,
                )
            )

        # Sort candidate stations strictly by route progress (mile marker ascending, then price ascending)
        candidate_stations.sort(key=lambda s: (s.mile_marker, s.price))
        return candidate_stations

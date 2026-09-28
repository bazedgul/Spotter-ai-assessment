"""Fuel optimizer service for calculating cost-optimal refueling stops along a route.

Requirements from ASSESSMENT_PLAN.md:
- Vehicle assumptions:
    * Maximum vehicle range: 500 miles (VEHICLE_MAX_RANGE_MILES)
    * Fuel economy: 10 MPG (VEHICLE_MPG)
    * Tank capacity: 50 gallons (VEHICLE_TANK_CAPACITY_GALLONS)
    * Starting fuel: full tank (50 gallons, VEHICLE_STARTING_FUEL_GALLONS, $0 initial cost)
- Optimization requirements:
    * Strictly respects 500-mile maximum vehicle range at all times.
    * Considers distance between fuel stops and downstream pricing (not just globally cheapest).
    * If a cheaper reachable station exists downstream, buys ONLY enough fuel to reach it.
    * If no cheaper station is reachable, fills the tank as appropriate.
    * Avoids unnecessary stops where vehicle can safely reach a better downstream option.
    * Calculates fuel purchased, fuel remaining, gallons used, cost per stop, total cost.
    * Fuel stop order strictly follows travel direction.
    * Tank capacity never exceeds 50 gallons; fuel never falls below 0.
    * Raises NoFeasibleFuelPlanError with code 'NO_FEASIBLE_FUEL_PLAN' when route cannot be completed.
    * Pure and testable service independent of HTTP views.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Sequence, Union

from django.conf import settings

from fuel.services.station_finder import CandidateStation

logger = logging.getLogger(__name__)


class OptimizerError(Exception):
    """Base exception for fuel optimizer errors."""
    pass


class NoFeasibleFuelPlanError(OptimizerError):
    """Raised when the route cannot be completed within vehicle range constraints."""
    def __init__(self, message: str, code: str = "NO_FEASIBLE_FUEL_PLAN"):
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass
class FuelStop:
    """Represents a scheduled fuel stop with purchase and pricing details."""
    station_id: int
    name: str
    latitude: float
    longitude: float
    price_per_gallon: Decimal
    mile_marker: float
    gallons_purchased: Decimal
    cost: Decimal
    city: str = ""
    state: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serializes fuel stop to a JSON-compatible dictionary."""
        return {
            "station_id": self.station_id,
            "name": self.name,
            "city": self.city,
            "state": self.state,
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "price_per_gallon": float(self.price_per_gallon),
            "price_decimal": str(self.price_per_gallon),
            "mile_marker": round(self.mile_marker, 2),
            "gallons_purchased": float(self.gallons_purchased),
            "cost": float(self.cost),
        }


@dataclass
class FuelOptimizationPlan:
    """Result of fuel optimization containing summary metrics and ordered fuel stops."""
    route_distance_miles: float
    starting_fuel_gallons: float
    total_consumed_gallons: float
    total_purchased_gallons: float
    total_cost: float
    fuel_stops: List[FuelStop]
    fuel_remaining_gallons: float

    def to_dict(self) -> Dict[str, Any]:
        """Serializes optimization plan to dictionary matching ASSESSMENT_PLAN.md specification."""
        return {
            "vehicle": {
                "max_range_miles": 500,
                "mpg": 10,
                "tank_capacity_gallons": 50,
            },
            "fuel": {
                "starting_fuel_gallons": round(self.starting_fuel_gallons, 2),
                "total_consumed_gallons": round(self.total_consumed_gallons, 2),
                "total_purchased_gallons": round(self.total_purchased_gallons, 2),
                "total_cost": round(self.total_cost, 2),
                "fuel_remaining_gallons": round(self.fuel_remaining_gallons, 2),
            },
            "fuel_stops": [stop.to_dict() for stop in self.fuel_stops],
        }


class FuelOptimizer:
    """Calculates the cost-optimal refueling plan along an ordered set of route stations."""

    def __init__(
        self,
        max_range_miles: Optional[float] = None,
        mpg: Optional[float] = None,
        starting_fuel_gallons: Optional[float] = None,
    ):
        """Initializes the optimizer with configurable vehicle parameters."""
        self.max_range = float(
            max_range_miles
            if max_range_miles is not None
            else getattr(settings, "VEHICLE_MAX_RANGE_MILES", 500.0)
        )
        self.mpg = float(
            mpg
            if mpg is not None
            else getattr(settings, "VEHICLE_MPG", 10.0)
        )
        self.tank_capacity = self.max_range / self.mpg
        self.starting_fuel = float(
            starting_fuel_gallons
            if starting_fuel_gallons is not None
            else getattr(settings, "VEHICLE_STARTING_FUEL_GALLONS", self.tank_capacity)
        )

    def optimize(
        self,
        candidate_stations: Sequence[Union[CandidateStation, Dict[str, Any]]],
        route_distance_miles: float,
    ) -> FuelOptimizationPlan:
        """Computes the optimal fuel plan minimizing total cost while adhering to vehicle constraints.

        Args:
            candidate_stations: Candidate stations along the route.
            route_distance_miles: Total route distance in miles.

        Returns:
            FuelOptimizationPlan with selected fuel stops, gallons, costs, and consumption.

        Raises:
            NoFeasibleFuelPlanError: If a required route gap exceeds vehicle range with no available stations.
        """
        if route_distance_miles <= 0:
            return FuelOptimizationPlan(
                route_distance_miles=0.0,
                starting_fuel_gallons=self.starting_fuel,
                total_consumed_gallons=0.0,
                total_purchased_gallons=0.0,
                total_cost=0.0,
                fuel_stops=[],
                fuel_remaining_gallons=self.starting_fuel,
            )

        # 1. Normalize stations to dictionary representation and ensure sorted order by mile marker
        station_list = []
        for s in candidate_stations:
            if isinstance(s, CandidateStation):
                s_dict = {
                    'station_id': s.station_id,
                    'name': s.name,
                    'city': s.city,
                    'state': s.state,
                    'latitude': s.latitude,
                    'longitude': s.longitude,
                    'price': Decimal(str(s.price)),
                    'mile_marker': float(s.mile_marker),
                }
            else:
                s_dict = {
                    'station_id': int(s['station_id']),
                    'name': str(s.get('name', '')),
                    'city': str(s.get('city', '')),
                    'state': str(s.get('state', '')),
                    'latitude': float(s.get('latitude', 0.0)),
                    'longitude': float(s.get('longitude', 0.0)),
                    'price': Decimal(str(s['price'])),
                    'mile_marker': float(s['mile_marker']),
                }
            # Only stations strictly within route boundaries participate
            if 0.0 <= s_dict['mile_marker'] <= route_distance_miles:
                station_list.append(s_dict)

        # Order candidates strictly by route direction (mile_marker ascending, then price ascending)
        station_list.sort(key=lambda x: (x['mile_marker'], x['price']))

        # 2. Build route nodes: [0: START, 1..K: STATIONS, K+1: DESTINATION]
        nodes = [{
            'station_id': 0,
            'name': 'START',
            'latitude': 0.0,
            'longitude': 0.0,
            'price': Decimal('Infinity'),
            'mile_marker': 0.0,
        }]
        nodes.extend(station_list)
        nodes.append({
            'station_id': -1,
            'name': 'FINISH',
            'latitude': 0.0,
            'longitude': 0.0,
            'price': Decimal('0.0'),
            'mile_marker': float(route_distance_miles),
        })

        n_nodes = len(nodes)
        dest_idx = n_nodes - 1

        # 3. Backward reachability analysis: Verify route feasibility
        can_reach_dest = [False] * n_nodes
        can_reach_dest[dest_idx] = True

        for i in range(dest_idx - 1, -1, -1):
            for j in range(i + 1, n_nodes):
                gap = nodes[j]['mile_marker'] - nodes[i]['mile_marker']
                if gap > self.max_range + 1e-9:
                    break
                if can_reach_dest[j]:
                    can_reach_dest[i] = True
                    break

        if not can_reach_dest[0]:
            logger.warning(
                f"No feasible fuel plan: Route distance {route_distance_miles:.1f} mi exceeds reachable range."
            )
            raise NoFeasibleFuelPlanError(
                f"The route cannot be completed with a maximum vehicle range of {self.max_range:.0f} miles "
                "using the available fuel stations. Route gap exceeds vehicle range.",
                code="NO_FEASIBLE_FUEL_PLAN"
            )

        # 4. Greedy lookahead optimization simulation
        current_idx = 0
        current_fuel = self.starting_fuel
        fuel_stops: List[FuelStop] = []
        total_purchased = Decimal('0.0')
        total_cost = Decimal('0.0')

        while current_idx < dest_idx:
            curr_pos = nodes[current_idx]['mile_marker']
            curr_price = nodes[current_idx]['price']
            dist_to_dest = nodes[dest_idx]['mile_marker'] - curr_pos

            # Can we reach destination with current fuel in tank?
            fuel_to_dest = dist_to_dest / self.mpg
            if fuel_to_dest <= current_fuel + 1e-9:
                current_fuel -= fuel_to_dest
                current_idx = dest_idx
                break

            # Reachable horizon from current position
            # At start (idx=0), we cannot purchase fuel, so effective range is current_fuel * mpg.
            # At a station (idx > 0), we can purchase up to full tank capacity.
            effective_range = self.max_range if current_idx > 0 else (current_fuel * self.mpg)

            # Find all reachable, feasible downstream nodes
            reachable_nodes = []
            for j in range(current_idx + 1, n_nodes):
                gap = nodes[j]['mile_marker'] - curr_pos
                if gap > effective_range + 1e-9:
                    break
                if can_reach_dest[j]:
                    reachable_nodes.append(j)

            if not reachable_nodes:
                raise NoFeasibleFuelPlanError(
                    f"NO_FEASIBLE_FUEL_PLAN: No reachable fuel station ahead from mile {curr_pos:.1f}.",
                    code="NO_FEASIBLE_FUEL_PLAN"
                )

            # --- AT START POINT (mile 0) ---
            if current_idx == 0:
                # Vehicle starts with full tank (50 gallons).
                # Choose the best first stop among reachable_nodes:
                # Prefer the station with the lowest price that can feasibly continue to destination.
                station_candidates = [j for j in reachable_nodes if j != dest_idx]
                if not station_candidates and dest_idx in reachable_nodes:
                    current_fuel -= dist_to_dest / self.mpg
                    current_idx = dest_idx
                    break

                min_price = min(nodes[j]['price'] for j in station_candidates)
                # Pick the furthest station with min_price
                best_first = [j for j in station_candidates if nodes[j]['price'] == min_price][-1]

                # Drive directly to best_first using starting fuel
                dist = nodes[best_first]['mile_marker'] - curr_pos
                current_fuel -= dist / self.mpg
                current_idx = best_first
                continue

            # --- AT A FUEL STATION (current_idx > 0) ---
            # Rule 1: Look for the FIRST station j in range (dist <= max_range) with price < curr_price.
            cheaper_target = None
            for j in reachable_nodes:
                if j == dest_idx:
                    cheaper_target = dest_idx
                    break
                if nodes[j]['price'] < curr_price:
                    cheaper_target = j
                    break

            if cheaper_target is not None:
                # A cheaper station (or destination) is reachable!
                # Purchase ONLY the exact amount needed to reach it.
                dist_to_cheaper = nodes[cheaper_target]['mile_marker'] - curr_pos
                fuel_needed = dist_to_cheaper / self.mpg
                gallons_to_buy = max(0.0, fuel_needed - current_fuel)

                # Cap purchase at remaining tank capacity
                gallons_to_buy = min(gallons_to_buy, self.tank_capacity - current_fuel)

                if gallons_to_buy > 1e-6:
                    gal_dec = Decimal(str(round(gallons_to_buy, 4)))
                    stop_cost = (gal_dec * curr_price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    fuel_stops.append(
                        FuelStop(
                            station_id=nodes[current_idx]['station_id'],
                            name=nodes[current_idx]['name'],
                            latitude=nodes[current_idx]['latitude'],
                            longitude=nodes[current_idx]['longitude'],
                            price_per_gallon=curr_price,
                            mile_marker=curr_pos,
                            gallons_purchased=gal_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                            cost=stop_cost,
                            city=nodes[current_idx].get('city', ''),
                            state=nodes[current_idx].get('state', ''),
                        )
                    )
                    total_purchased += gal_dec
                    total_cost += stop_cost
                    current_fuel += gallons_to_buy

                current_fuel -= fuel_needed
                current_idx = cheaper_target
            else:
                # No cheaper station in reach.
                if dest_idx in reachable_nodes:
                    # Destination is reachable: Buy ONLY enough to reach destination!
                    fuel_needed = dist_to_dest / self.mpg
                    gallons_to_buy = max(0.0, fuel_needed - current_fuel)
                    gallons_to_buy = min(gallons_to_buy, self.tank_capacity - current_fuel)

                    if gallons_to_buy > 1e-6:
                        gal_dec = Decimal(str(round(gallons_to_buy, 4)))
                        stop_cost = (gal_dec * curr_price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                        fuel_stops.append(
                            FuelStop(
                                station_id=nodes[current_idx]['station_id'],
                                name=nodes[current_idx]['name'],
                                latitude=nodes[current_idx]['latitude'],
                                longitude=nodes[current_idx]['longitude'],
                                price_per_gallon=curr_price,
                                mile_marker=curr_pos,
                                gallons_purchased=gal_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                                cost=stop_cost,
                                city=nodes[current_idx].get('city', ''),
                                state=nodes[current_idx].get('state', ''),
                            )
                        )
                        total_purchased += gal_dec
                        total_cost += stop_cost
                        current_fuel += gallons_to_buy

                    current_fuel -= fuel_needed
                    current_idx = dest_idx
                    break
                else:
                    # Destination unreachable and no cheaper station in reach.
                    # Fill tank to full capacity (50 gallons) at this locally cheap station!
                    gallons_to_buy = self.tank_capacity - current_fuel
                    if gallons_to_buy > 1e-6:
                        gal_dec = Decimal(str(round(gallons_to_buy, 4)))
                        stop_cost = (gal_dec * curr_price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                        fuel_stops.append(
                            FuelStop(
                                station_id=nodes[current_idx]['station_id'],
                                name=nodes[current_idx]['name'],
                                latitude=nodes[current_idx]['latitude'],
                                longitude=nodes[current_idx]['longitude'],
                                price_per_gallon=curr_price,
                                mile_marker=curr_pos,
                                gallons_purchased=gal_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                                cost=stop_cost,
                                city=nodes[current_idx].get('city', ''),
                                state=nodes[current_idx].get('state', ''),
                            )
                        )
                        total_purchased += gal_dec
                        total_cost += stop_cost
                        current_fuel += gallons_to_buy

                    # Advance to the cheapest available station in reachable range
                    station_options = [j for j in reachable_nodes if j != dest_idx]
                    if not station_options:
                        raise NoFeasibleFuelPlanError(
                            f"NO_FEASIBLE_FUEL_PLAN: No station to advance to from mile {curr_pos:.1f}.",
                            code="NO_FEASIBLE_FUEL_PLAN"
                        )

                    min_p = min(nodes[j]['price'] for j in station_options)
                    best_next = [j for j in station_options if nodes[j]['price'] == min_p][-1]

                    dist_to_next = nodes[best_next]['mile_marker'] - curr_pos
                    current_fuel -= dist_to_next / self.mpg
                    current_idx = best_next

        total_consumed = route_distance_miles / self.mpg
        current_fuel = max(0.0, current_fuel)

        return FuelOptimizationPlan(
            route_distance_miles=round(route_distance_miles, 2),
            starting_fuel_gallons=round(self.starting_fuel, 2),
            total_consumed_gallons=round(total_consumed, 2),
            total_purchased_gallons=float(total_purchased.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
            total_cost=float(total_cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
            fuel_stops=fuel_stops,
            fuel_remaining_gallons=round(current_fuel, 2),
        )

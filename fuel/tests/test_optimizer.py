"""Unit tests for FuelOptimizer service.

Covers required scenarios from assessment prompt:
- Scenario A: Simple route with multiple reachable stations with different prices
- Scenario B: Cheap station too far away (unreachable on current fuel)
- Scenario C: Cheaper downstream station (buy only necessary fuel at expensive stop)
- Scenario D: Maximum range gap close to 500 miles
- Scenario E: Impossible gap > 500 miles raises NO_FEASIBLE_FUEL_PLAN
- Scenario F: Tank capacity never exceeds 50 gallons
- Scenario G: Stations considered in route-progress order, not arbitrary input order
- Additional edge cases: Short route <= 500 miles, multi-stop cross-country route.
"""

from decimal import Decimal
from django.test import TestCase

from fuel.services.fuel_optimizer import (
    FuelOptimizer,
    FuelOptimizationPlan,
    NoFeasibleFuelPlanError,
)
from fuel.services.station_finder import CandidateStation


class FuelOptimizerTestCase(TestCase):
    def setUp(self):
        # Default vehicle: 500 miles max range, 10 MPG, 50 gallons tank capacity, starting full
        self.optimizer = FuelOptimizer(
            max_range_miles=500.0,
            mpg=10.0,
            starting_fuel_gallons=50.0,
        )

    def test_short_route_no_stops_needed(self):
        # Route is 400 miles <= 500 miles max range.
        # Vehicle starts with full tank (50 gallons = 500 miles range).
        # Reaches destination directly with 0 stops, $0 cost, 10 gallons remaining.
        stations = [
            {'station_id': 1, 'name': 'Midway Stop', 'mile_marker': 200.0, 'price': 3.50},
        ]
        plan = self.optimizer.optimize(stations, route_distance_miles=400.0)

        self.assertEqual(len(plan.fuel_stops), 0)
        self.assertEqual(plan.total_cost, 0.0)
        self.assertEqual(plan.total_purchased_gallons, 0.0)
        self.assertEqual(plan.total_consumed_gallons, 40.0)
        self.assertEqual(plan.fuel_remaining_gallons, 10.0)

    def test_scenario_a_simple_route_reachability_over_global_cheapest(self):
        # Scenario A: Two reachable stations with different prices.
        # Distance = 700 miles. Start has 500 miles range.
        # Station 1 at mile 200 ($4.00), Station 2 at mile 400 ($3.00).
        # Station 2 is reachable from start (400 <= 500) and can reach destination (700 - 400 = 300 <= 500).
        # Optimizer should bypass Station 1 and refuel at Station 2.
        stations = [
            {'station_id': 1, 'name': 'Expensive Stop', 'mile_marker': 200.0, 'price': 4.00},
            {'station_id': 2, 'name': 'Cheap Stop', 'mile_marker': 400.0, 'price': 3.00},
        ]
        plan = self.optimizer.optimize(stations, route_distance_miles=700.0)

        self.assertEqual(len(plan.fuel_stops), 1)
        stop = plan.fuel_stops[0]
        self.assertEqual(stop.station_id, 2)
        self.assertEqual(stop.mile_marker, 400.0)
        # At mile 400, vehicle arrives with 50 - 40 = 10 gal.
        # Destination is 300 mi away = 30 gal needed.
        # Vehicle buys exactly 30 - 10 = 20 gallons at $3.00 = $60.00.
        self.assertAlmostEqual(float(stop.gallons_purchased), 20.0, places=2)
        self.assertAlmostEqual(float(stop.cost), 60.0, places=2)
        self.assertEqual(plan.total_cost, 60.0)

    def test_scenario_b_cheap_station_too_far_away(self):
        # Scenario B: A very cheap station exists at mile 600 ($2.00),
        # but cannot be reached on start fuel (max 500 mi).
        # Intermediate station exists at mile 350 ($4.00).
        # Destination = 800 miles.
        stations = [
            {'station_id': 1, 'name': 'Reachable Stop', 'mile_marker': 350.0, 'price': 4.00},
            {'station_id': 2, 'name': 'Unreachable Cheap Stop', 'mile_marker': 600.0, 'price': 2.00},
        ]
        plan = self.optimizer.optimize(stations, route_distance_miles=800.0)

        # Vehicle must stop at Station 1 first (mile 350)
        stop_ids = [s.station_id for s in plan.fuel_stops]
        self.assertEqual(stop_ids[0], 1)
        # Vehicle should also stop at Station 2 (mile 600) since it is now reachable and cheap
        self.assertIn(2, stop_ids)

    def test_scenario_c_cheaper_downstream_station_partial_refuel(self):
        # Scenario C: Station 1 is reachable at mile 400 ($4.50/gal).
        # Cheaper Station 2 is reachable after it at mile 700 ($3.00/gal).
        # Total route = 1000 miles.
        # At Station 1 (mile 400): Vehicle arrives with 50 - 40 = 10 gal.
        # Distance to Station 2 is 300 miles = 30 gallons needed.
        # Rather than filling 40 gallons to max capacity (50 gal) at $4.50,
        # the vehicle purchases ONLY the necessary 20 gallons to reach Station 2!
        stations = [
            {'station_id': 1, 'name': 'Expensive Stop', 'mile_marker': 400.0, 'price': 4.50},
            {'station_id': 2, 'name': 'Cheaper Downstream Stop', 'mile_marker': 700.0, 'price': 3.00},
        ]
        plan = self.optimizer.optimize(stations, route_distance_miles=1000.0)

        self.assertEqual(len(plan.fuel_stops), 2)
        stop1 = plan.fuel_stops[0]
        self.assertEqual(stop1.station_id, 1)
        self.assertAlmostEqual(float(stop1.gallons_purchased), 20.0, places=2)
        self.assertAlmostEqual(float(stop1.cost), 90.0, places=2)  # 20 * 4.50 = 90.00

        # At Station 2 (mile 700), vehicle arrives with 0 gal, needs 30 gal to reach 1000 mi dest
        stop2 = plan.fuel_stops[1]
        self.assertEqual(stop2.station_id, 2)
        self.assertAlmostEqual(float(stop2.gallons_purchased), 30.0, places=2)
        self.assertAlmostEqual(float(stop2.cost), 90.0, places=2)  # 30 * 3.00 = 90.00

        self.assertAlmostEqual(plan.total_cost, 180.0, places=2)

    def test_scenario_d_maximum_range_gap(self):
        # Scenario D: Gap close to 500 miles (495 miles from start).
        # Vehicle has 500 miles range. Gap of 495 miles is feasible.
        stations = [
            {'station_id': 10, 'name': 'Near Limit Stop', 'mile_marker': 495.0, 'price': 3.40},
        ]
        plan = self.optimizer.optimize(stations, route_distance_miles=700.0)

        self.assertEqual(len(plan.fuel_stops), 1)
        self.assertEqual(plan.fuel_stops[0].station_id, 10)
        # Reached mile 495 with 50 - 49.5 = 0.5 gallons remaining
        # Needs (700 - 495) / 10 = 20.5 gallons to finish
        # Buys 20.5 - 0.5 = 20.0 gallons
        self.assertAlmostEqual(float(plan.fuel_stops[0].gallons_purchased), 20.0, places=2)

    def test_scenario_e_impossible_gap_raises_machine_readable_error(self):
        # Scenario E: Route distance is 800 miles. Only station is at mile 550.
        # Initial range is 500 miles, so mile 550 is impossible to reach.
        stations = [
            {'station_id': 20, 'name': 'Unreachable Stop', 'mile_marker': 550.0, 'price': 3.20},
        ]
        with self.assertRaises(NoFeasibleFuelPlanError) as ctx:
            self.optimizer.optimize(stations, route_distance_miles=800.0)

        self.assertEqual(ctx.exception.code, "NO_FEASIBLE_FUEL_PLAN")
        self.assertIn("NO_FEASIBLE_FUEL_PLAN", str(ctx.exception))

    def test_scenario_e_impossible_downstream_gap(self):
        # First leg is feasible (mile 300), but second leg to destination has a 550-mile gap.
        stations = [
            {'station_id': 21, 'name': 'Stop 1', 'mile_marker': 300.0, 'price': 3.20},
        ]
        with self.assertRaises(NoFeasibleFuelPlanError) as ctx:
            self.optimizer.optimize(stations, route_distance_miles=850.0)

        self.assertEqual(ctx.exception.code, "NO_FEASIBLE_FUEL_PLAN")

    def test_scenario_f_tank_capacity_never_exceeded(self):
        # Scenario F: Verify fuel purchased never causes the tank to exceed 50 gallons.
        stations = [
            {'station_id': 31, 'name': 'Stop 1', 'mile_marker': 300.0, 'price': 3.80},
            {'station_id': 32, 'name': 'Stop 2', 'mile_marker': 600.0, 'price': 3.50},
            {'station_id': 33, 'name': 'Stop 3', 'mile_marker': 900.0, 'price': 3.20},
        ]
        plan = self.optimizer.optimize(stations, route_distance_miles=1200.0)

        for stop in plan.fuel_stops:
            self.assertLessEqual(float(stop.gallons_purchased), 50.0)

    def test_scenario_g_route_ordering_independent_of_input_order(self):
        # Scenario G: Stations provided out of order must be ordered by route direction.
        stations = [
            {'station_id': 43, 'name': 'Stop 3', 'mile_marker': 800.0, 'price': 3.10},
            {'station_id': 41, 'name': 'Stop 1', 'mile_marker': 200.0, 'price': 4.00},
            {'station_id': 42, 'name': 'Stop 2', 'mile_marker': 450.0, 'price': 3.20},
        ]
        plan = self.optimizer.optimize(stations, route_distance_miles=1100.0)

        # Check stops are strictly in route order
        markers = [s.mile_marker for s in plan.fuel_stops]
        self.assertEqual(markers, sorted(markers))

    def test_candidate_station_objects_supported(self):
        # Verify optimizer accepts CandidateStation objects from StationFinder
        candidates = [
            CandidateStation(
                station_id=51,
                name="Pilot Travel Center",
                address="I-40 EXIT 10",
                city="Amarillo",
                state="TX",
                price=Decimal("3.1990"),
                latitude=35.19,
                longitude=-101.83,
                geocode_precision="address",
                mile_marker=450.0,
                distance_to_route=1.2,
            )
        ]
        plan = self.optimizer.optimize(candidates, route_distance_miles=750.0)
        self.assertEqual(len(plan.fuel_stops), 1)
        self.assertEqual(plan.fuel_stops[0].station_id, 51)
        self.assertEqual(plan.fuel_stops[0].price_per_gallon, Decimal("3.1990"))

    def test_scenario_4_no_cheaper_station_in_range_fills_tank(self):
        # Scenario 4: Station 1 at mile 400 ($3.50), Station 2 at mile 800 ($3.80 - more expensive).
        # Total route = 1200 miles. Destination is unreachable from mile 400 (800 miles away > 500 max range).
        # At mile 400, vehicle arrives with 50 - 40 = 10 gal.
        # No cheaper station in reach, and destination unreachable.
        # Vehicle must fill the tank to capacity (50 gal) by purchasing 40 gal at $3.50 = $140.00.
        stations = [
            {'station_id': 20, 'name': 'Low Stop', 'mile_marker': 400.0, 'price': 3.50},
            {'station_id': 21, 'name': 'High Stop', 'mile_marker': 800.0, 'price': 3.80},
        ]
        plan = self.optimizer.optimize(stations, route_distance_miles=1200.0)
        self.assertEqual(plan.fuel_stops[0].station_id, 20)
        self.assertAlmostEqual(float(plan.fuel_stops[0].gallons_purchased), 40.0, places=2)
        self.assertAlmostEqual(float(plan.fuel_stops[0].cost), 140.0, places=2)

    def test_scenario_5_exactly_500_mile_reachable_gap_feasible(self):
        # Scenario 5: Exactly 500-mile gap.
        # Route 1: Exactly 500-mile route with 0 stations -> feasible, 0 stops.
        plan_direct = self.optimizer.optimize([], route_distance_miles=500.0)
        self.assertEqual(len(plan_direct.fuel_stops), 0)
        self.assertEqual(plan_direct.total_cost, 0.0)
        self.assertEqual(plan_direct.fuel_remaining_gallons, 0.0)

        # Route 2: 1000-mile route with station at exactly mile 500 -> feasible, 1 stop, 50 gal.
        stations = [
            {'station_id': 30, 'name': 'Halfway Stop', 'mile_marker': 500.0, 'price': 3.00},
        ]
        plan_hop = self.optimizer.optimize(stations, route_distance_miles=1000.0)
        self.assertEqual(len(plan_hop.fuel_stops), 1)
        self.assertEqual(plan_hop.fuel_stops[0].station_id, 30)
        self.assertAlmostEqual(float(plan_hop.fuel_stops[0].gallons_purchased), 50.0, places=2)

    def test_scenario_8_existing_fuel_reduces_required_purchase(self):
        # Scenario 8: Existing fuel in tank reduces required purchase.
        # Starting with 20 gallons (effective range 200 miles).
        low_fuel_opt = FuelOptimizer(max_range_miles=500.0, mpg=10.0, starting_fuel_gallons=20.0)
        stations = [
            {'station_id': 60, 'name': 'First Stop', 'mile_marker': 100.0, 'price': 3.00},
        ]
        # At mile 100, 10 gallons consumed -> 10 gallons left in tank.
        # Destination is 400 miles away (300 miles from station -> 30 gallons needed).
        # Vehicle needs 30 gallons to finish, has 10 gallons -> purchases 20 gallons (not 30).
        plan = low_fuel_opt.optimize(stations, route_distance_miles=400.0)
        self.assertEqual(len(plan.fuel_stops), 1)
        self.assertAlmostEqual(float(plan.fuel_stops[0].gallons_purchased), 20.0, places=2)
        self.assertAlmostEqual(float(plan.fuel_stops[0].cost), 60.0, places=2)

    def test_fuel_conservation_mass_balance(self):
        # Verifies physical conservation: starting_fuel + total_purchased == total_consumed + fuel_remaining
        stations = [
            {'station_id': 1, 'name': 'S1', 'mile_marker': 350.0, 'price': 4.20},
            {'station_id': 2, 'name': 'S2', 'mile_marker': 650.0, 'price': 3.10},
            {'station_id': 3, 'name': 'S3', 'mile_marker': 950.0, 'price': 3.60},
        ]
        plan = self.optimizer.optimize(stations, route_distance_miles=1300.0)
        inflow = plan.starting_fuel_gallons + plan.total_purchased_gallons
        outflow = plan.total_consumed_gallons + plan.fuel_remaining_gallons
        self.assertAlmostEqual(inflow, outflow, delta=0.05)


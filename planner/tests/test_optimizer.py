import random

from django.test import SimpleTestCase

from planner.services.optimizer import InfeasibleRoute, Station, optimise, optimise_dp


def st(mile, price, ref=None):
    return Station(ref if ref is not None else f"s{mile}", mile, price)


class GreedyTests(SimpleTestCase):
    def test_buys_only_enough_to_reach_cheaper_station(self):
        plan = optimise([st(0, 4.0), st(100, 3.0)], total_miles=400, max_range=500, mpg=10)
        self.assertEqual([round(s.gallons, 6) for s in plan.stops], [10.0, 30.0])
        self.assertAlmostEqual(plan.total_cost, 10 * 4.0 + 30 * 3.0)

    def test_fills_up_when_current_station_is_cheapest(self):
        plan = optimise([st(0, 3.0), st(450, 4.0)], total_miles=800, max_range=500, mpg=10)
        self.assertAlmostEqual(plan.stops[0].gallons, 50.0)  # full tank of cheap fuel
        self.assertAlmostEqual(plan.stops[1].gallons, 30.0)  # 800-500 miles still needed
        self.assertAlmostEqual(plan.total_gallons, 80.0)

    def test_short_trip_with_full_tank_costs_nothing(self):
        plan = optimise([st(10, 3.0)], total_miles=300, max_range=500, mpg=10, start_fuel_miles=500)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, 0.0)

    def test_gap_longer_than_range_is_infeasible(self):
        with self.assertRaises(InfeasibleRoute):
            optimise([st(0, 3.0), st(600, 3.0)], total_miles=900, max_range=500, mpg=10)


class DynamicProgrammeTests(SimpleTestCase):
    def test_matches_greedy_optimum_without_penalty(self):
        rng = random.Random(7)
        for _ in range(150):
            total = rng.uniform(100, 2800)
            stations = sorted(
                [st(float(rng.randint(0, int(total))), round(rng.uniform(2.7, 4.5), 3), k) for k in range(rng.randint(1, 200))],
                key=lambda s: (s.mile, s.price),
            )
            stations.insert(0, st(0.0, 3.5, "origin"))
            try:
                greedy = optimise(stations, total, 500, 10)
            except InfeasibleRoute:
                with self.assertRaises(InfeasibleRoute):
                    optimise_dp(stations, total, 500, 10)
                continue
            dp = optimise_dp(stations, total, 500, 10)
            # 1-mile fuel grid: at most one mile (0.1 gal) of rounding at the destination.
            self.assertAlmostEqual(dp.total_cost, greedy.total_cost, delta=0.1 * 4.5 + 1e-6)

    def test_gallons_cover_the_whole_trip(self):
        stations = [st(0, 3.2), st(120, 3.0), st(480, 3.4), st(700, 2.9), st(1050, 3.1)]
        plan = optimise_dp(stations, 1300, 500, 10, stop_penalty=0)
        self.assertAlmostEqual(plan.total_gallons, 130.0)
        for stop in plan.stops:
            self.assertLessEqual(stop.fuel_on_departure_miles, 500 + 1e-9)

    def test_stop_penalty_removes_micro_stops(self):
        # Two nearly identical cheap stations 10 miles apart invite a pointless extra stop.
        stations = [st(0, 3.50), st(300, 3.00), st(310, 2.99), st(700, 3.40)]
        cheapest = optimise_dp(stations, 1000, 500, 10, stop_penalty=0)
        practical = optimise_dp(stations, 1000, 500, 10, stop_penalty=5)
        self.assertLess(len(practical.stops), len(cheapest.stops))
        self.assertLess(practical.total_cost - cheapest.total_cost, 5)

    def test_start_full_tank(self):
        plan = optimise_dp([st(200, 3.0), st(600, 3.5)], 900, 500, 10, start_fuel_miles=500)
        self.assertAlmostEqual(plan.total_gallons, 40.0)  # 900 - 500 miles already in the tank
        self.assertEqual(plan.stops[0].station.mile, 200)

    def test_infeasible(self):
        with self.assertRaises(InfeasibleRoute):
            optimise_dp([st(0, 3.0), st(100, 3.0), st(700, 3.0)], 800, 500, 10)

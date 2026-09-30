"""Minimum-cost refuelling along a fixed route (the classic "gas station problem").

Two solvers:

``optimise_dp`` (used by the API)
    Dynamic programme over (station, fuel-in-tank) at 1-mile fuel resolution, with an
    optional fixed ``stop_penalty`` ($) per stop. Pure cost minimisation happily makes a
    1-gallon stop to save 5 cents; a small penalty removes such micro-stops while the
    reported cost remains the real fuel spend. Vectorised with numpy: O(stations x range).

``optimise`` (greedy, exact for zero stop penalty; used as a test oracle)
    Given stations at known positions along the route, a tank that holds ``max_range``
    miles of fuel and a price per gallon at each station, the following greedy is
    provably optimal (fuel is fungible, so cheap fuel should displace expensive fuel):

  At station i, with price p_i:
    * If a cheaper station j is reachable on the current tank-range, buy only enough
      to reach the *first* such j, then drive there.
    * Else, if the destination is reachable, buy only enough to reach it and stop.
    * Else, fill the tank here (this is the cheapest fuel for the next ``max_range``
      miles) and drive to the cheapest station within range.

Runs in O(n log n) thanks to a precomputed "next cheaper station" table.
"""

from dataclasses import dataclass, field

import numpy as np

EPS = 1e-9


class InfeasibleRoute(ValueError):
    """Some gap between consecutive usable stations exceeds the vehicle range."""

    def __init__(self, message, gap_start_mile, gap_end_mile):
        super().__init__(message)
        self.gap_start_mile = gap_start_mile
        self.gap_end_mile = gap_end_mile


@dataclass(frozen=True)
class Station:
    ref: object  # caller's handle (e.g. index into a StationIndex)
    mile: float  # position along the route
    price: float  # $ per gallon


@dataclass
class Stop:
    station: Station
    gallons: float
    cost: float
    fuel_on_arrival_miles: float
    fuel_on_departure_miles: float


@dataclass
class Plan:
    stops: list = field(default_factory=list)
    total_gallons: float = 0.0
    total_cost: float = 0.0


def _next_cheaper(stations):
    """next_cheaper[i] = smallest j > i with price_j < price_i, else None (monotonic stack)."""
    result = [None] * len(stations)
    stack = []
    for j, st in enumerate(stations):
        while stack and st.price < stations[stack[-1]].price - EPS:
            result[stack.pop()] = j
        stack.append(j)
    return result


def optimise(stations, total_miles, max_range, mpg, start_fuel_miles=0.0):
    """Return the cheapest refuelling Plan.

    ``stations`` must be sorted by ``mile``. With ``start_fuel_miles == 0`` there must
    be a station at mile 0 (the caller provides the origin fill-up station).
    """
    if max_range <= 0 or mpg <= 0:
        raise ValueError("max_range and mpg must be positive")
    stations = [s for s in stations if -EPS <= s.mile <= total_miles + EPS]
    start_fuel_miles = min(max(start_fuel_miles, 0.0), max_range)
    plan = Plan()

    if start_fuel_miles + EPS >= total_miles:
        return plan  # no fuel needs to be bought

    nxt = _next_cheaper(stations)
    fuel = start_fuel_miles

    # Drive (without buying) to the first station; the fuel we started with is sunk.
    i = 0
    if not stations or stations[0].mile > fuel + EPS:
        first = stations[0].mile if stations else total_miles
        raise InfeasibleRoute(
            f"No fuel station within reach of the start (next one at mile {first:.0f}).", 0.0, first
        )
    fuel -= stations[0].mile

    while True:
        here = stations[i]
        reach = here.mile + max_range
        arrival_fuel = fuel

        j = nxt[i]
        # Skip forward over "cheaper" stations that are actually out of reach.
        if j is not None and stations[j].mile <= min(reach, total_miles) + EPS:
            need = stations[j].mile - here.mile
            target, go_to = need, j
        elif total_miles <= reach + EPS:
            target, go_to = total_miles - here.mile, None
        else:
            # Fill up, then move to the cheapest station within range (farthest on ties).
            target = max_range
            best = None
            k = i + 1
            while k < len(stations) and stations[k].mile <= reach + EPS:
                if best is None or stations[k].price <= stations[best].price + EPS:
                    best = k
                k += 1
            if best is None:
                gap_end = stations[i + 1].mile if i + 1 < len(stations) else total_miles
                raise InfeasibleRoute(
                    f"Gap of {gap_end - here.mile:.0f} miles after mile {here.mile:.0f} "
                    f"exceeds the {max_range:.0f}-mile range.",
                    here.mile,
                    gap_end,
                )
            go_to = best

        buy = max(0.0, target - fuel)
        if buy > EPS:
            gallons = buy / mpg
            cost = gallons * here.price
            fuel += buy
            plan.stops.append(Stop(here, gallons, cost, arrival_fuel, fuel))
            plan.total_gallons += gallons
            plan.total_cost += cost

        if go_to is None:
            return plan
        fuel -= stations[go_to].mile - here.mile
        i = go_to


def optimise_dp(stations, total_miles, max_range, mpg, start_fuel_miles=0.0, stop_penalty=0.0,
                resolution_miles=1.0):
    """Cheapest plan minimising ``fuel cost + stop_penalty * number_of_stops``.

    State: fuel in tank on arrival at station i, in units of ``resolution_miles``.
    At each station we may buy any amount (paying the stop penalty once) then drive to
    the next station. Station positions are snapped to the unit grid so leg lengths are
    exact integer differences (no cumulative rounding drift).
    """
    if max_range <= 0 or mpg <= 0:
        raise ValueError("max_range and mpg must be positive")
    u = resolution_miles
    stations = [s for s in stations if -EPS <= s.mile <= total_miles + EPS]
    cap = int(np.floor(max_range / u + EPS))
    start_units = int(np.floor(min(max(start_fuel_miles, 0.0), max_range) / u + EPS))
    end_pos = int(np.ceil(total_miles / u - EPS))
    if start_units >= end_pos:
        return Plan()

    positions = [int(round(s.mile / u)) for s in stations] + [end_pos]
    levels = np.arange(cap + 1)
    INF = np.inf

    # cost[f] = min $ to arrive at the current node with f units of fuel.
    cost = np.full(cap + 1, INF)
    first = positions[0] if stations else end_pos
    if first > start_units:
        raise InfeasibleRoute(f"No fuel station within reach of the start (next one at mile {first * u:.0f}).",
                              0.0, first * u)
    cost[start_units - first] = 0.0

    # Back-pointers per station: arrival level each departure level came from.
    back = []
    for i, st in enumerate(stations):
        unit_price = st.price * u / mpg
        # buy[f] = min over f' <= f of cost[f'] + (f - f') * unit_price  (+ penalty if f' < f)
        adj = cost - levels * unit_price
        run_min = np.minimum.accumulate(adj)
        run_arg = _running_argmin(adj)
        bought = run_min + levels * unit_price + stop_penalty
        depart = np.where(bought < cost - EPS, bought, cost)
        src = np.where(bought < cost - EPS, run_arg, levels)
        back.append(src)

        d = positions[i + 1] - positions[i]
        nxt = np.full(cap + 1, INF)
        if d <= cap:
            nxt[: cap + 1 - d] = depart[d:]
        if not np.isfinite(nxt).any():
            raise InfeasibleRoute(
                f"Gap of {d * u:.0f} miles after mile {st.mile:.0f} exceeds the {max_range:.0f}-mile range.",
                st.mile, positions[i + 1] * u,
            )
        cost = nxt

    # Arrive at destination with f units; cheapest is (almost always) f = 0.
    f = int(np.argmin(cost))
    plan = Plan()
    for i in range(len(stations) - 1, -1, -1):
        depart_level = f + (positions[i + 1] - positions[i])
        arrive_level = int(back[i][depart_level])
        if depart_level > arrive_level:
            gallons = (depart_level - arrive_level) * u / mpg
            cost_i = gallons * stations[i].price
            plan.stops.append(Stop(stations[i], gallons, cost_i, arrive_level * u, depart_level * u))
            plan.total_gallons += gallons
            plan.total_cost += cost_i
        f = arrive_level
    plan.stops.reverse()
    return plan


def _running_argmin(a):
    """Index of the running minimum of ``a`` (ties -> latest index, i.e. buy as little as needed)."""
    idx = np.arange(len(a))
    is_new_min = a <= np.minimum.accumulate(a) + EPS
    return np.maximum.accumulate(np.where(is_new_min, idx, 0))

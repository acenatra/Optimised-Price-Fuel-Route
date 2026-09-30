"""Orchestrates geocoding -> routing -> corridor matching -> optimisation."""

import logging
import time
from dataclasses import dataclass

import numpy as np
from django.conf import settings
from django.core.cache import cache

from . import geocoding, routing
from .geo import cumulative_miles, decimate
from .optimizer import InfeasibleRoute, Station, optimise_dp
from .station_index import StationIndex

log = logging.getLogger(__name__)


class PlanningError(ValueError):
    """A user-facing planning failure (bad input, no stations, no feasible plan)."""


@dataclass(frozen=True)
class PlanRequest:
    start: str
    finish: str
    start_full: bool = False  # True: depart with a full tank (only en-route purchases are costed)
    include_geometry: bool = True
    stop_penalty: float | None = None  # $ per stop used only for optimisation; None -> settings default


def plan_trip(req: PlanRequest):
    t0 = time.perf_counter()
    cfg = settings.FUEL_PLANNER
    max_range, mpg = cfg["MAX_RANGE_MILES"], cfg["MILES_PER_GALLON"]
    penalty = cfg["STOP_PENALTY_DOLLARS"] if req.stop_penalty is None else max(0.0, float(req.stop_penalty))

    index = StationIndex.get()
    if not len(index):
        raise PlanningError("No fuel stations loaded. Run `python manage.py load_stations` first.")

    start = geocoding.resolve(req.start)
    finish = geocoding.resolve(req.finish)
    external_calls = sum(loc.source == "nominatim" for loc in (start, finish))  # upper bound (may be cached)

    cache_key = (
        f"plan:v1:{start.lat:.5f},{start.lon:.5f}:{finish.lat:.5f},{finish.lon:.5f}:"
        f"{int(req.start_full)}:{int(req.include_geometry)}:{penalty:.2f}"
    )
    cached = cache.get(cache_key)
    if cached:
        cached["meta"] = {**cached["meta"], "cached": True, "routing_api_calls": 0,
                          "geocoding_api_calls_max": external_calls,
                          "total_ms": round((time.perf_counter() - t0) * 1000, 1)}
        return cached

    route, called = routing.get_route(start.lat, start.lon, finish.lat, finish.lon)
    t_route = time.perf_counter()

    # Origin fill-up (empty tank at departure): cheapest station near the start point.
    origin_stop = None
    if not req.start_full:
        idx, dist = index.nearest(start.lat, start.lon, cfg["ORIGIN_RADIUS_MILES"])
        best = idx[np.argmin(index.price[idx])] if len(idx) else idx[0]
        origin_stop = (int(best), float(dist[list(idx).index(best)]))

    plan = candidates = corridor = None
    last_error = None
    for corridor in cfg["CORRIDOR_MILES"]:
        candidates = index.along_route(route.lats, route.lons, corridor, route.distance_miles)
        stations = [
            Station(ref=(int(i), float(off)), mile=float(m), price=float(p))
            for i, m, off, p in zip(candidates.idx, candidates.route_mile, candidates.off_route_miles,
                                    candidates.price)
        ]
        if origin_stop:
            i, off = origin_stop
            stations.insert(0, Station(ref=(i, off, "origin"), mile=0.0, price=float(index.price[i])))
        try:
            plan = optimise_dp(
                stations, route.distance_miles, max_range, mpg,
                start_fuel_miles=max_range if req.start_full else 0.0,
                stop_penalty=penalty,
            )
            break
        except InfeasibleRoute as exc:
            last_error = exc
            log.info("Corridor %.0f mi infeasible (%s); widening", corridor, exc)
    if plan is None:
        raise PlanningError(f"No feasible fuelling plan: {last_error}")

    stops = []
    for n, stop in enumerate(plan.stops, start=1):
        i, off, *tag = stop.station.ref
        stops.append({
            "order": n,
            **index.describe(i),
            "price_per_gallon": round(stop.station.price, 4),
            "route_mile": round(stop.station.mile, 1),
            "off_route_miles": round(off, 1),
            "is_origin_fillup": bool(tag),
            "gallons": round(stop.gallons, 2),
            "cost": round(stop.cost, 2),
            "fuel_on_arrival_gallons": round(stop.fuel_on_arrival_miles / mpg, 2),
            "fuel_on_departure_gallons": round(stop.fuel_on_departure_miles / mpg, 2),
        })

    trip_gallons = route.distance_miles / mpg
    result = {
        "start": start.as_dict(),
        "finish": finish.as_dict(),
        "vehicle": {"max_range_miles": max_range, "miles_per_gallon": mpg,
                    "tank_gallons": max_range / mpg, "starts_with_full_tank": req.start_full},
        "route": {
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_seconds / 3600, 2),
            "geometry": _geometry(route) if req.include_geometry else None,
        },
        "fuel_stops": stops,
        "summary": {
            "number_of_stops": len(stops),
            "total_gallons_purchased": round(plan.total_gallons, 2),
            "total_fuel_cost": round(plan.total_cost, 2),
            "trip_fuel_gallons": round(trip_gallons, 2),
            "average_price_paid": round(plan.total_cost / plan.total_gallons, 4) if plan.total_gallons else None,
            "candidate_stations_in_corridor": int(len(candidates.idx)),
            "corridor_miles": corridor,
            "stop_penalty_used_for_optimisation": penalty,
        },
        "meta": {
            "cached": False,
            "routing_api_calls": int(called),
            "geocoding_api_calls_max": external_calls,
            "geocode_and_route_ms": round((t_route - t0) * 1000, 1),
            "optimisation_ms": round((time.perf_counter() - t_route) * 1000, 1),
            "total_ms": round((time.perf_counter() - t0) * 1000, 1),
        },
    }
    cache.set(cache_key, result)
    return result


def _geometry(route):
    cum = cumulative_miles(route.lats, route.lons)
    lats, lons = decimate(route.lats, route.lons, cum, settings.FUEL_PLANNER["GEOMETRY_STEP_MILES"])
    coords = np.round(np.column_stack((lons, lats)), 5).tolist()
    return {"type": "LineString", "coordinates": coords}

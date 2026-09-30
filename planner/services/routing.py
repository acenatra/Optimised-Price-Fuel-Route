"""Driving route from the free OSRM API — exactly one HTTP call per (uncached) route."""

import logging
from dataclasses import dataclass

import numpy as np
from django.conf import settings
from django.core.cache import cache

from .http import ExternalServiceError, get_json

log = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344


class RouteNotFound(ValueError):
    pass


@dataclass
class Route:
    distance_miles: float
    duration_seconds: float
    lats: np.ndarray
    lons: np.ndarray


def decode_polyline(encoded, precision=6):
    """Decode a Google encoded polyline (OSRM `polyline6`) into (lats, lons) arrays."""
    values = []
    index, n = 0, len(encoded)
    while index < n:
        result, shift = 0, 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        values.append(~(result >> 1) if result & 1 else result >> 1)
    coords = np.cumsum(np.asarray(values, dtype=np.int64).reshape(-1, 2), axis=0) / (10.0 ** precision)
    return coords[:, 0], coords[:, 1]


def get_route(start_lat, start_lon, end_lat, end_lon):
    key = f"route:v1:{start_lat:.5f},{start_lon:.5f}:{end_lat:.5f},{end_lon:.5f}"
    cached = cache.get(key)
    if cached:
        return cached, False

    url = (
        f"{settings.FUEL_PLANNER['OSRM_BASE_URL'].rstrip('/')}/route/v1/driving/"
        f"{start_lon:.6f},{start_lat:.6f};{end_lon:.6f},{end_lat:.6f}"
    )
    status, data = get_json(url, params={"overview": "full", "geometries": "polyline6", "steps": "false"})
    code = data.get("code") if isinstance(data, dict) else None
    if code in {"NoRoute", "NoSegment"}:
        raise RouteNotFound("No drivable route found between the two locations.")
    if status != 200 or code != "Ok" or not data.get("routes"):
        raise ExternalServiceError(f"Routing service error (HTTP {status}, code={code}).")

    r = data["routes"][0]
    lats, lons = decode_polyline(r["geometry"])
    route = Route(r["distance"] / METERS_PER_MILE, r["duration"], lats, lons)
    cache.set(key, route)
    return route, True

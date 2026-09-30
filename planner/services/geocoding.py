"""Resolve user-supplied locations to coordinates with as few external calls as possible.

Resolution order:
  1. "lat,lon" literal                      -> no external call
  2. "City, ST" in the local Census gazetteer -> no external call (DB index lookup)
  3. Nominatim (OpenStreetMap) free-text search, cached -> 1 external call
"""

import logging
import re
from dataclasses import asdict, dataclass

from django.conf import settings
from django.core.cache import cache

from ..models import Place
from .http import ExternalServiceError, get_json
from .names import normalise, split_city_state

log = logging.getLogger(__name__)

_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

# Generous box around the 50 states (incl. Alaska & Hawaii).
_US_BOUNDS = {"lat": (18.0, 72.0), "lon": (-180.0, -65.0)}


class LocationError(ValueError):
    """Input could not be resolved to a location in the USA."""


@dataclass(frozen=True)
class Location:
    query: str
    name: str
    lat: float
    lon: float
    source: str  # "coordinates" | "gazetteer" | "nominatim"

    def as_dict(self):
        return asdict(self)


def in_usa_bounds(lat, lon):
    return _US_BOUNDS["lat"][0] <= lat <= _US_BOUNDS["lat"][1] and _US_BOUNDS["lon"][0] <= lon <= _US_BOUNDS["lon"][1]


def resolve(query):
    query = (query or "").strip()
    if not query:
        raise LocationError("Location is required.")

    m = _LATLON.match(query)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if not in_usa_bounds(lat, lon):
            raise LocationError(f"'{query}' is not inside the USA (expected 'lat,lon').")
        return Location(query, f"{lat:.5f},{lon:.5f}", lat, lon, "coordinates")

    parsed = split_city_state(query)
    if parsed:
        city, state = parsed
        place = Place.objects.filter(state=state, key=normalise(city)).only("name", "lat", "lon").first()
        if place:
            return Location(query, f"{place.name}, {state}", place.lat, place.lon, "gazetteer")

    return _nominatim(query)


def _nominatim(query):
    cache_key = f"geocode:v1:{normalise(query)}"
    hit = cache.get(cache_key)
    if hit:
        return Location(**hit)

    status, data = get_json(
        settings.FUEL_PLANNER["NOMINATIM_URL"],
        params={"q": query, "format": "jsonv2", "countrycodes": "us", "limit": 1},
    )
    if status != 200 or not isinstance(data, list):
        raise ExternalServiceError(f"Geocoder returned HTTP {status}")
    if not data:
        raise LocationError(f"Could not find '{query}' in the USA. Try 'City, ST' or 'lat,lon'.")
    top = data[0]
    loc = Location(query, top.get("display_name", query), float(top["lat"]), float(top["lon"]), "nominatim")
    cache.set(cache_key, loc.as_dict(), timeout=60 * 60 * 24 * 30)
    return loc

"""In-memory spatial index over all fuel stations.

The ~6.7k stations are loaded from the DB once per process into numpy arrays, so a
request never touches the database for station data. Matching stations to a route
is a single KD-tree query (milliseconds, even for coast-to-coast routes).
"""

import threading
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from ..models import FuelStation
from .geo import chord_to_miles, densify, miles_to_chord, to_unit_xyz

# Route is resampled to at most this spacing before matching, so "distance to route"
# is accurate to ~ +/- 0.1 mile even on long straight interstates.
_ROUTE_SAMPLE_MILES = 0.25


@dataclass
class Candidates:
    """Stations inside the route corridor, sorted by distance along the route."""

    idx: np.ndarray  # indices into StationIndex arrays
    route_mile: np.ndarray  # position along the route (miles from origin)
    off_route_miles: np.ndarray
    price: np.ndarray


class StationIndex:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        rows = list(
            FuelStation.objects.order_by("id").values_list(
                "opis_id", "name", "address", "city", "state", "price", "lat", "lon"
            )
        )
        self.opis_id = np.array([r[0] for r in rows], dtype=np.int64)
        self.name = [r[1] for r in rows]
        self.address = [r[2] for r in rows]
        self.city = [r[3] for r in rows]
        self.state = [r[4] for r in rows]
        self.price = np.array([float(r[5]) for r in rows], dtype=float)
        self.lat = np.array([r[6] for r in rows], dtype=float)
        self.lon = np.array([r[7] for r in rows], dtype=float)
        self.xyz = to_unit_xyz(self.lat, self.lon) if rows else np.empty((0, 3))
        self.tree = cKDTree(self.xyz) if rows else None

    def __len__(self):
        return len(self.opis_id)

    @classmethod
    def get(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def invalidate(cls):
        with cls._lock:
            cls._instance = None

    def nearest(self, lat, lon, radius_miles=None):
        """Indices + distances of stations near a point (all within radius, or the single nearest)."""
        point = to_unit_xyz([lat], [lon])[0]
        if radius_miles is not None:
            idx = np.asarray(self.tree.query_ball_point(point, miles_to_chord(radius_miles)), dtype=int)
            if len(idx):
                return idx, chord_to_miles(np.linalg.norm(self.xyz[idx] - point, axis=1))
        dist, i = self.tree.query(point)
        return np.array([i]), np.array([chord_to_miles(dist)])

    def along_route(self, route_lats, route_lons, corridor_miles, route_length_miles=None):
        """Stations within ``corridor_miles`` of the route polyline."""
        lats, lons, cum = densify(route_lats, route_lons, _ROUTE_SAMPLE_MILES)
        if route_length_miles and cum[-1] > 0:
            cum = cum * (route_length_miles / cum[-1])  # align with the router's reported distance

        # Nearest dense route sample for every station, bounded by the corridor width.
        # Stations outside the corridor come back with dist=inf and are dropped.
        route_tree = cKDTree(to_unit_xyz(lats, lons))
        dist, nearest = route_tree.query(self.xyz, distance_upper_bound=float(miles_to_chord(corridor_miles)))
        cand = np.flatnonzero(np.isfinite(dist))
        off = chord_to_miles(dist[cand])
        pos = cum[nearest[cand]]

        order = np.lexsort((self.price[cand], pos))  # by route position, then price
        return Candidates(cand[order], pos[order], off[order], self.price[cand[order]])

    def describe(self, i):
        return {
            "opis_id": int(self.opis_id[i]),
            "name": self.name[i],
            "address": self.address[i],
            "city": self.city[i],
            "state": self.state[i],
            "lat": round(float(self.lat[i]), 6),
            "lon": round(float(self.lon[i]), 6),
        }

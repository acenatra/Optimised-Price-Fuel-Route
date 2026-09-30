"""Vectorised geodesy helpers (numpy)."""

import numpy as np

EARTH_RADIUS_MI = 3958.7613


def to_unit_xyz(lat, lon):
    """Lat/lon in degrees -> points on the unit sphere, shape (N, 3)."""
    lat = np.radians(np.asarray(lat, dtype=float))
    lon = np.radians(np.asarray(lon, dtype=float))
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def miles_to_chord(miles):
    """Great-circle distance (miles) -> straight-line chord length on the unit sphere."""
    return 2.0 * np.sin(np.asarray(miles, dtype=float) / (2.0 * EARTH_RADIUS_MI))


def chord_to_miles(chord):
    return 2.0 * EARTH_RADIUS_MI * np.arcsin(np.clip(np.asarray(chord, dtype=float) / 2.0, 0.0, 1.0))


def haversine_miles(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(v, dtype=float)) for v in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2.0 * EARTH_RADIUS_MI * np.arcsin(np.sqrt(a))


def cumulative_miles(lats, lons):
    """Cumulative distance along a polyline, starting at 0."""
    seg = haversine_miles(lats[:-1], lons[:-1], lats[1:], lons[1:])
    return np.concatenate(([0.0], np.cumsum(seg)))


def densify(lats, lons, max_step_miles):
    """Insert points so no polyline segment is longer than ``max_step_miles``.

    Long straight interstate segments can have vertices many miles apart; densifying
    lets a nearest-vertex lookup approximate "distance to the route" accurately.
    Returns (lats, lons, cumulative_miles).
    """
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    seg = haversine_miles(lats[:-1], lons[:-1], lats[1:], lons[1:])
    pieces = np.maximum(1, np.ceil(seg / max_step_miles).astype(int))
    # For each segment emit `pieces` points at fractions 0, 1/n, ..., (n-1)/n; then the final vertex.
    seg_idx = np.repeat(np.arange(len(seg)), pieces)
    starts = np.repeat(np.cumsum(pieces) - pieces, pieces)
    frac = (np.arange(len(seg_idx)) - starts) / pieces[seg_idx]
    # Linear interpolation in lat/lon is fine at sub-mile spacing.
    out_lat = np.append(lats[seg_idx] + (lats[seg_idx + 1] - lats[seg_idx]) * frac, lats[-1])
    out_lon = np.append(lons[seg_idx] + (lons[seg_idx + 1] - lons[seg_idx]) * frac, lons[-1])
    seg_cum = np.concatenate(([0.0], np.cumsum(seg)))
    out_cum = np.append(seg_cum[seg_idx] + seg[seg_idx] * frac, seg_cum[-1])
    return out_lat, out_lon, out_cum


def decimate(lats, lons, cum, step_miles):
    """Keep roughly one vertex per ``step_miles`` (always keeps first and last)."""
    if len(lats) <= 2 or step_miles <= 0:
        return lats, lons
    _, keep = np.unique(np.floor(cum / step_miles), return_index=True)
    keep = np.union1d(keep, [len(lats) - 1])
    return lats[keep], lons[keep]

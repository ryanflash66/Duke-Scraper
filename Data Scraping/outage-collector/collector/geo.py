"""Geometry helper utilities (placeholder)."""

from __future__ import annotations

from math import radians, cos, sin, asin, sqrt
from typing import Iterable, Tuple


def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Compute distance between two lat/lng pairs in kilometers (approx)."""
    lat1, lon1 = a
    lat2, lon2 = b
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    lat1 = radians(lat1)
    lat2 = radians(lat2)

    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


def point_in_bbox(lat: float, lng: float, bbox: Tuple[float, float, float, float]) -> bool:
    """Return True if point lies within bbox (min_lat, max_lat, min_lng, max_lng)."""
    min_lat, max_lat, min_lng, max_lng = bbox
    return min_lat <= lat <= max_lat and min_lng <= lng <= max_lng


def point_in_polygon(lat: float, lng: float, polygon: Iterable[Tuple[float, float]]) -> bool:
    """Ray casting point-in-polygon test in latitude/longitude space."""

    points = list(polygon)
    if len(points) < 3:
        return False

    inside = False
    j = len(points) - 1
    for i, (plat, plng) in enumerate(points):
        qlat, qlng = points[j]

        if (plat > lat) != (qlat > lat):
            intersection_lng = (qlng - plng) * (lat - plat) / ((qlat - plat) or 1e-12) + plng
            if lng < intersection_lng:
                inside = not inside
        j = i

    return inside

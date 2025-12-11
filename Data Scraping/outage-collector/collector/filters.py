"""Spatial filters for outage events."""

from __future__ import annotations

from math import cos, radians
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .datatypes import BBoxPolicy, LatLng
from .geo import haversine_km, point_in_bbox, point_in_polygon


EventType = Dict[str, Any]
Point = Tuple[float, float]
BBoxTuple = Tuple[float, float, float, float]


def filter_in_bbox(
    events: Iterable[EventType],
    bbox: Union[BBoxTuple, Dict[str, float]],
    *,
    policy: Union[BBoxPolicy, str] = BBoxPolicy.CENTROID,
    viewport_polygon: Optional[Sequence[Point]] = None,
) -> List[EventType]:
    """Filter events to those intersecting the bounding box under the given policy."""

    bbox_tuple = _coerce_bbox(bbox)
    policy_enum = _coerce_policy(policy)
    viewport_pts = list(viewport_polygon or [])

    filtered: List[EventType] = []
    for event in events:
        centroid = _extract_coordinates(event)
        polygon = _extract_polygon(event)

        include = False
        if policy_enum is BBoxPolicy.CENTROID:
            if centroid and point_in_bbox(centroid[0], centroid[1], bbox_tuple):
                include = True
        elif policy_enum is BBoxPolicy.VERTEX:
            if polygon:
                include = any(point_in_bbox(lat, lng, bbox_tuple) for lat, lng in polygon)
            elif centroid and point_in_bbox(centroid[0], centroid[1], bbox_tuple):
                include = True
        elif policy_enum is BBoxPolicy.INTERSECTS:
            if polygon and _polygon_intersects_bbox(polygon, bbox_tuple):
                include = True
            elif centroid and point_in_bbox(centroid[0], centroid[1], bbox_tuple):
                include = True

        if not include:
            continue

        if viewport_pts:
            inside_viewport = False
            if centroid:
                inside_viewport = point_in_polygon(centroid[0], centroid[1], viewport_pts)
            elif polygon:
                inside_viewport = any(point_in_polygon(lat, lng, viewport_pts) for lat, lng in polygon)
            if not inside_viewport:
                continue

        filtered.append(event)

    return filtered


def filter_in_radius(
    events: Iterable[EventType],
    center: Union[Point, Dict[str, float], LatLng],
    radius_km: float,
) -> List[EventType]:
    """Return events whose centroid or polygon lies within the radius of the centre point."""

    if radius_km <= 0:
        return []

    centre_point = _coerce_point(center)
    if not centre_point:
        return list(events)

    filtered: List[EventType] = []
    for event in events:
        centroid = _extract_coordinates(event)
        polygon = _extract_polygon(event)

        distances: List[float] = []
        if centroid:
            distances.append(haversine_km(centre_point, centroid))
        if polygon:
            distances.extend(haversine_km(centre_point, vertex) for vertex in polygon)

        if distances and min(distances) <= radius_km:
            filtered.append(event)

    return filtered


def expand_bbox(
    bbox: Union[BBoxTuple, Dict[str, float]],
    padding_km: float,
    reference_lat: float,
) -> BBoxTuple:
    """Expand a bounding box by the given padding expressed in kilometres."""

    if padding_km <= 0:
        return _coerce_bbox(bbox)

    min_lat, max_lat, min_lng, max_lng = _coerce_bbox(bbox)
    lat_padding = padding_km / 111.32  # Approx kilometres per degree latitude

    cos_lat = max(cos(radians(reference_lat)), 1e-6)
    lng_padding = padding_km / (111.32 * cos_lat)

    return (
        min_lat - lat_padding,
        max_lat + lat_padding,
        min_lng - lng_padding,
        max_lng + lng_padding,
    )


def _polygon_intersects_bbox(polygon: Sequence[Point], bbox: BBoxTuple) -> bool:
    min_lat, max_lat, min_lng, max_lng = bbox

    # Any vertex inside the bbox?
    if any(point_in_bbox(lat, lng, bbox) for lat, lng in polygon):
        return True

    # Any bbox corner inside the polygon?
    corners = (
        (min_lat, min_lng),
        (min_lat, max_lng),
        (max_lat, min_lng),
        (max_lat, max_lng),
    )
    if any(point_in_polygon(lat, lng, polygon) for lat, lng in corners):
        return True

    return False


def _extract_coordinates(event: EventType) -> Optional[Point]:
    for lat_key, lng_key, swap in (
        ("lat", "lng", False),
        ("latitude", "longitude", False),
        ("Latitude", "Longitude", False),
        ("y", "x", False),
        ("Y", "X", False),
    ):
        if lat_key in event and lng_key in event:
            return _coerce_point((event[lat_key], event[lng_key]), swap)

    geometry = event.get("geometry") if isinstance(event, dict) else None
    if isinstance(geometry, dict):
        if {"y", "x"}.issubset(geometry):
            return _coerce_point((geometry["y"], geometry["x"]))
        if "coordinates" in geometry:
            coords = geometry["coordinates"]
            point = _coerce_point(coords, assume_lng_lat=True)
            if point:
                return point

    fallback = _coerce_point(event)
    if fallback:
        return fallback

    return None


def _extract_polygon(event: EventType) -> List[Point]:
    candidates: Sequence[Any] | None = None
    for key in ("polygon", "convexHull", "hull"):
        value = event.get(key)
        if value:
            candidates = value
            break

    if not candidates:
        geometry = event.get("geometry") if isinstance(event, dict) else None
        if isinstance(geometry, dict):
            for key in ("rings", "paths"):
                if key in geometry:
                    candidates = geometry[key]
                    break

    points: List[Point] = []
    if candidates:
        for candidate in candidates:
            if isinstance(candidate, (list, tuple)) and candidate and isinstance(candidate[0], (list, tuple, dict)):
                for sub_candidate in candidate:
                    point = _coerce_point(sub_candidate, assume_lng_lat=True)
                    if point:
                        points.append(point)
            else:
                point = _coerce_point(candidate, assume_lng_lat=True)
                if point:
                    points.append(point)

    return points


def _coerce_bbox(bbox: Union[BBoxTuple, Dict[str, float]]) -> BBoxTuple:
    if isinstance(bbox, dict):
        return (
            float(bbox["min_lat"]),
            float(bbox["max_lat"]),
            float(bbox["min_lng"]),
            float(bbox["max_lng"]),
        )
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        min_lat, max_lat, min_lng, max_lng = map(float, bbox)
        if max_lat < min_lat or max_lng < min_lng:
            raise ValueError("Invalid bounding box: min values must be <= max values")
        return (min_lat, max_lat, min_lng, max_lng)
    raise TypeError("Bounding box must be a dict or a four-element iterable")


def _coerce_policy(policy: Union[BBoxPolicy, str]) -> BBoxPolicy:
    if isinstance(policy, BBoxPolicy):
        return policy
    return BBoxPolicy(str(policy).lower())


def _coerce_point(
    value: Any,
    swap: bool = False,
    assume_lng_lat: bool = False,
) -> Optional[Point]:
    if isinstance(value, (LatLng,)):
        return (float(value.lat), float(value.lng))

    if isinstance(value, dict):
        if {"lat", "lng"}.issubset(value):
            return (float(value["lat"]), float(value["lng"]))
        if {"latitude", "longitude"}.issubset(value):
            return (float(value["latitude"]), float(value["longitude"]))
        if {"Latitude", "Longitude"}.issubset(value):
            return (float(value["Latitude"]), float(value["Longitude"]))
        if {"y", "x"}.issubset(value):
            return (float(value["y"]), float(value["x"]))
        if {"deviceLatitudeLocation", "deviceLongitudeLocation"}.issubset(value):
            return (
                float(value["deviceLatitudeLocation"]),
                float(value["deviceLongitudeLocation"]),
            )
        if {"DeviceLatitude", "DeviceLongitude"}.issubset(value):
            return (float(value["DeviceLatitude"]), float(value["DeviceLongitude"]))

    if isinstance(value, (list, tuple)) and len(value) >= 2:
        first, second = value[0], value[1]
        if assume_lng_lat or swap:
            return (float(second), float(first))
        return (float(first), float(second))

    return None

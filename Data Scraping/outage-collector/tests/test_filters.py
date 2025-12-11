"""Tests for spatial filter helpers."""

from __future__ import annotations

from typing import Dict, List

from collector.datatypes import BBoxPolicy
from collector.filters import expand_bbox, filter_in_bbox, filter_in_radius


_SAMPLE_EVENTS: List[Dict[str, object]] = [
    {"event_id": "centroid-in", "lat": 35.8, "lng": -78.6},
    {"event_id": "centroid-out", "lat": 34.0, "lng": -80.0},
    {
        "event_id": "polygon-slice",
        "geometry": {
            "rings": [
                [
                    [-78.7, 35.7],
                    [-78.3, 35.7],
                    [-78.3, 35.9],
                    [-78.7, 35.9],
                    [-78.7, 35.7],
                ]
            ]
        },
    },
]

_BBOX = (35.6, 36.0, -78.9, -78.4)
_CENTER = (35.7796, -78.6382)


def test_filter_in_bbox_centroid():
    result = filter_in_bbox(_SAMPLE_EVENTS, _BBOX, policy=BBoxPolicy.CENTROID)
    ids = {event["event_id"] for event in result}
    assert "centroid-in" in ids
    assert "centroid-out" not in ids


def test_filter_in_bbox_vertex_intersects():
    result = filter_in_bbox(_SAMPLE_EVENTS, _BBOX, policy=BBoxPolicy.INTERSECTS)
    ids = {event["event_id"] for event in result}
    assert "polygon-slice" in ids


def test_filter_viewport_polygon():
    viewport = [(35.7, -78.7), (35.9, -78.7), (35.9, -78.5), (35.7, -78.5)]
    result = filter_in_bbox(
        _SAMPLE_EVENTS,
        _BBOX,
        policy=BBoxPolicy.INTERSECTS,
        viewport_polygon=viewport,
    )
    ids = {event["event_id"] for event in result}
    assert "polygon-slice" in ids
    assert "centroid-in" in ids


def test_filter_in_radius_selects_by_distance():
    result = filter_in_radius(_SAMPLE_EVENTS, _CENTER, radius_km=50)
    ids = {event["event_id"] for event in result}
    assert "centroid-in" in ids
    assert "centroid-out" not in ids


def test_expand_bbox_padding_increases_bounds():
    expanded = expand_bbox(_BBOX, padding_km=5.0, reference_lat=_CENTER[0])
    assert expanded[0] < _BBOX[0]
    assert expanded[1] > _BBOX[1]
    assert expanded[2] < _BBOX[2]
    assert expanded[3] > _BBOX[3]


def test_filter_in_bbox_handles_device_coordinates():
    event = {
        "event_id": "device",
        "deviceLatitudeLocation": 35.78,
        "deviceLongitudeLocation": -78.64,
    }
    result = filter_in_bbox([event], _BBOX, policy=BBoxPolicy.CENTROID)
    assert result and result[0]["event_id"] == "device"

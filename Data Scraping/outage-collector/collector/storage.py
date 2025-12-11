"""Output writers for normalized outage events."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from .datatypes import LatLng, NormalizedEvent


def write_csv(events: Iterable[NormalizedEvent], output_dir: Path) -> Path:
    """Write normalized events to a CSV file and return the file path."""

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "outage_events.csv"
    fieldnames = [
        "event_id",
        "jurisdiction",
        "lat",
        "lng",
        "customers_affected",
        "cause_code",
    "cause_description",
        "status_text",
        "estimated_restoration_at",
        "first_reported_at",
        "zip_code",
        "collected_at",
        "polygon",
    ]

    with path.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for event in events:
            writer.writerow(_event_to_csv_row(event))

    return path


def write_geojson(events: Iterable[NormalizedEvent], output_dir: Path) -> Path:
    """Write normalized events to a GeoJSON FeatureCollection."""

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "outage_events.geojson"

    features = [_event_to_geojson_feature(event) for event in events]
    collection = {
        "type": "FeatureCollection",
        "features": features,
    }

    path.write_text(json.dumps(collection, indent=2, default=_json_default), encoding="utf-8")
    return path


def _event_to_csv_row(event: NormalizedEvent) -> dict[str, object]:
    polygon_serialized = "|".join(f"{pt.lat},{pt.lng}" for pt in event.polygon or [])
    return {
        "event_id": event.event_id,
        "jurisdiction": event.jurisdiction,
        "lat": event.lat,
        "lng": event.lng,
        "customers_affected": event.customers_affected,
        "cause_code": event.cause_code,
    "cause_description": event.cause_description,
        "status_text": event.status_text,
        "estimated_restoration_at": event.estimated_restoration_at.isoformat() if event.estimated_restoration_at else None,
        "first_reported_at": event.first_reported_at.isoformat() if event.first_reported_at else None,
        "zip_code": event.zip_code,
        "collected_at": event.collected_at.isoformat(),
        "polygon": polygon_serialized,
    }


def _event_to_geojson_feature(event: NormalizedEvent) -> dict[str, object]:
    properties: dict[str, object | None] = {
        "event_id": event.event_id,
        "customers_affected": event.customers_affected,
        "cause_code": event.cause_code,
    "cause_description": event.cause_description,
        "jurisdiction": event.jurisdiction,
        "status_text": event.status_text,
        "estimated_restoration_at": event.estimated_restoration_at.isoformat() if event.estimated_restoration_at else None,
        "first_reported_at": event.first_reported_at.isoformat() if event.first_reported_at else None,
        "zip_code": event.zip_code,
        "collected_at": event.collected_at.isoformat(),
    }

    geometry: dict[str, object]
    if event.polygon:
        geometry = {
            "type": "Polygon",
            "coordinates": _polygon_coordinates(event.polygon),
        }
    else:
        geometry = {
            "type": "Point",
            "coordinates": [event.lng, event.lat],
        }

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": properties,
    }


def _polygon_coordinates(polygon: Iterable[LatLng]) -> List[List[List[float]]]:
    ring = [[vertex.lng, vertex.lat] for vertex in polygon]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return [ring]


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="python")
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)

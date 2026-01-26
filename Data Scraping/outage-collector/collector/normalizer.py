"""Normalization helpers for Duke Energy outage payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from .datatypes import LatLng, NormalizedEvent
from .filters import _coerce_point, _extract_polygon


def normalize_record(record: Dict[str, Any], jurisdiction: str) -> NormalizedEvent:
    """Convert a raw outage dictionary into a `NormalizedEvent`."""

    event_id = _coerce_event_id(record)
    centroid = _coerce_point(record) or (0.0, 0.0)
    polygon_points = _extract_polygon(record)

    customers_affected = _first_int(
        record,
        [
            "customersAffected",
            "custaff",
            "CustomersAffected",
            "customersAffectedNumber",
            "customersAffectedSum",
        ],
    )

    cause = _first_value(record, ["cause", "causeCode", "outageCause", "Cause"], default=None)
    cause_description = _first_value(
        record,
        [
            "causeDescription",
            "CauseDescription",
        ],
        default=None,
    )
    estimated_restoration = _coerce_timestamp(
        record,
        [
            "estimatedRestoration",
            "estimatedRestorationTime",
            "etr",
            "estimated_restoration_time",
        ],
    )
    first_reported = _coerce_timestamp(
        record,
        [
            "firstReported",
            "firstReportedTime",
            "createtime",
            "reportedAt",
        ],
    )
    status_text = _first_value(record, ["status", "statusText", "Status"], default=None)
    zip_code = _first_value(record, ["zip", "zipcode", "postalCode"], default=None)

    polygon = [LatLng(lat=lat, lng=lng) for lat, lng in polygon_points] or None

    collected_at = datetime.now(tz=timezone.utc)

    return NormalizedEvent(
        event_id=event_id,
        lat=centroid[0],
        lng=centroid[1],
        customers_affected=customers_affected,
        polygon=polygon,
        cause_code=cause,
    cause_description=str(cause_description) if cause_description else None,
        jurisdiction=jurisdiction,
        collected_at=collected_at,
        estimated_restoration_at=estimated_restoration,
        first_reported_at=first_reported,
        status_text=status_text,
        zip_code=zip_code,
    )


def normalize_records(records: Iterable[Dict[str, Any]], jurisdiction: str) -> List[NormalizedEvent]:
    """Normalize a sequence of records."""

    return [normalize_record(record, jurisdiction) for record in records]


def _coerce_event_id(record: Dict[str, Any]) -> str:
    candidate = _first_value(
        record,
        [
            "eventId",
            "event_id",
            "eventNumber",
            "event_number",
            "sourceEventNumber",
            "objectid",
            "OBJECTID",
        ],
        default="",
    )
    return str(candidate)


def _first_value(record: Dict[str, Any], keys: Iterable[str], default: Optional[Any] = None) -> Optional[Any]:
    for key in keys:
        if key in record:
            return record[key]
    return default


def _first_int(record: Dict[str, Any], keys: Iterable[str], default: int = 0) -> int:
    value = _first_value(record, keys, default=None)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_timestamp(record: Dict[str, Any], keys: Iterable[str]) -> Optional[datetime]:
    value = _first_value(record, keys, default=None)
    if value is None:
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    try:
        # ArcGIS often uses epoch milliseconds
        if isinstance(value, (int, float)) and value > 1e12:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
    except Exception:  # pragma: no cover - defensive guard
        return None

    return None

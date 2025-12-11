"""Tests for the normalization helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from collector.datatypes import NormalizedEvent
from collector.enrichment import _apply_detail, _parse_timestamp
from collector.normalizer import normalize_record, normalize_records


def test_normalize_record_basic_fields():
    record = {
        "sourceEventNumber": 123,
        "deviceLatitudeLocation": 35.8,
        "deviceLongitudeLocation": -78.6,
        "customersAffectedNumber": "42",
        "outageCause": "Storm",
        "estimatedRestoration": 1_735_000_000_000,
        "firstReported": "2025-09-29T12:00:00Z",
        "status": "Crew Assigned",
        "zip": "27601",
    }

    event = normalize_record(record, jurisdiction="DEP")

    assert event.event_id == "123"
    assert event.lat == 35.8
    assert event.lng == -78.6
    assert event.customers_affected == 42
    assert event.cause_code == "Storm"
    assert event.status_text == "Crew Assigned"
    assert event.zip_code == "27601"
    assert event.jurisdiction == "DEP"
    assert event.estimated_restoration_at == datetime.fromtimestamp(1_735_000_000, tz=timezone.utc)
    assert event.first_reported_at == datetime(2025, 9, 29, 12, 0, tzinfo=timezone.utc)


def test_normalize_record_falls_back_on_defaults():
    event = normalize_record({}, jurisdiction="DEP")
    assert event.event_id == ""
    assert event.lat == 0.0
    assert event.lng == 0.0
    assert event.customers_affected == 0
    assert event.polygon is None
    assert event.estimated_restoration_at is None
    assert event.first_reported_at is None


def test_normalize_records_produces_list():
    records = [{"sourceEventNumber": "abc"}, {"sourceEventNumber": "def"}]
    normalized = normalize_records(records, jurisdiction="DEP")
    ids = [event.event_id for event in normalized]
    assert ids == ["abc", "def"]
from collector import normalizer


def test_normalize_record_returns_event():
    record = {
        "sourceEventNumber": "123",
        "deviceLatitudeLocation": 35.0,
        "deviceLongitudeLocation": -78.0,
        "customersAffectedNumber": 10,
    }
    event = normalizer.normalize_record(record, jurisdiction="DEP")
    assert event.event_id == "123"
    assert event.jurisdiction == "DEP"


def _build_event() -> NormalizedEvent:
    return NormalizedEvent(
        event_id="6069355",
        lat=35.7897,
        lng=-78.6645,
        customers_affected=1,
        jurisdiction="DEC",
        cause_code="unplanned",
        status_text=None,
        estimated_restoration_at=None,
        first_reported_at=None,
        polygon=None,
        zip_code=None,
    )


def _sample_detail_payload() -> Dict[str, Any]:
    return {
        "sourceEventNumber": "6069355",
        "maxCustomersAffectedNumber": 4,
        "estimatedRestorationTime": "2025-09-30T18:15:00.000+00:00",
        "crewStatTxt": "ASSIGNED",
        "startTime": "2025-09-30T15:39:10.000+00:00",
        "trfPolygonXyLoc": [
            {"lat": 35.7933, "lng": -78.6458},
            {"lat": 35.7940, "lng": -78.6460},
        ],
        "causeDescription": "Tree damage",
        "customersAffectedSum": 3,
    }


def test_apply_detail_merges_payload():
    event = _build_event()
    detail = _sample_detail_payload()
    enriched = _apply_detail(event, detail)
    assert enriched is not None
    assert enriched.status_text == "ASSIGNED"
    assert enriched.cause_code == "unplanned"
    assert enriched.cause_description == "Tree damage"
    assert enriched.customers_affected == 3
    assert enriched.estimated_restoration_at == datetime(2025, 9, 30, 18, 15, tzinfo=timezone.utc)
    assert enriched.first_reported_at == datetime(2025, 9, 30, 15, 39, 10, tzinfo=timezone.utc)
    assert enriched.polygon and len(enriched.polygon) == 2


def test_apply_detail_returns_none_when_no_updates():
    event = _build_event()
    assert _apply_detail(event, {}) is None


def test_parse_timestamp_handles_iso_string():
    value = "2025-09-30T18:15:00.000+00:00"
    parsed = _parse_timestamp(value)
    assert parsed == datetime(2025, 9, 30, 18, 15, tzinfo=timezone.utc)


def test_parse_timestamp_handles_epoch_seconds():
    parsed = _parse_timestamp(1_695_092_500)
    assert isinstance(parsed, datetime)

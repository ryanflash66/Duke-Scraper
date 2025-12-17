"""Tests for storage output writers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from collector.datatypes import LatLng, NormalizedEvent
from collector.storage import write_csv, write_geojson


def _sample_event() -> NormalizedEvent:
    return NormalizedEvent(
        event_id="abc",
        lat=35.8,
        lng=-78.6,
        customers_affected=10,
        polygon=[LatLng(lat=35.8, lng=-78.6), LatLng(lat=35.81, lng=-78.61)],
        cause_code="Storm",
        jurisdiction="DEP",
        status_text="Crew Assigned",
        estimated_restoration_at=datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc),
        first_reported_at=datetime(2025, 9, 30, 8, 0, tzinfo=timezone.utc),
        zip_code="27601",
    )


def test_write_csv(tmp_path: Path):
    event = _sample_event()
    path = write_csv([event], tmp_path)

    content = path.read_text(encoding="utf-8").splitlines()
    assert content[0].startswith("event_id,jurisdiction")
    assert "abc" in content[1]


def test_write_geojson(tmp_path: Path):
    event = _sample_event()
    path = write_geojson([event], tmp_path)

    data = path.read_text(encoding="utf-8")
    assert "FeatureCollection" in data
    assert "abc" in data
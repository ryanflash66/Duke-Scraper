"""Tests for scheduler orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pytest

from collector.datatypes import (
    BBoxPolicy,
    BoundingBox,
    DiscoveryTemplate,
    ExportFormat,
    LatLng,
    PlacePreset,
    PollCycleStats,
    RawPayload,
    ResolvedConfig,
)
from collector.poller import PollCycleResult
from collector.scheduler import poll_and_export
from collector.service import ServiceState


def _make_config(tmp_path: Path) -> ResolvedConfig:
    preset = PlacePreset(
        jurisdiction="DEP",
        center=LatLng(lat=35.7796, lng=-78.6382),
        radius_km=50.0,
        bbox=BoundingBox(min_lat=35.6, max_lat=36.0, min_lng=-78.9, max_lng=-78.4),
        polygon=[
            LatLng(lat=35.7, lng=-78.7),
            LatLng(lat=35.9, lng=-78.7),
            LatLng(lat=35.9, lng=-78.5),
            LatLng(lat=35.7, lng=-78.5),
        ],
        query_params={},
    )

    return ResolvedConfig(
        place="raleigh",
        preset=preset,
        poll_interval_s=60.0,
        max_events_per_cycle=50,
        export_format=ExportFormat.BOTH,
        output_dir=tmp_path,
        bbox_policy=BBoxPolicy.CENTROID,
        radius_km=50.0,
        padding_km=0.0,
        viewport_only=False,
        rebootstrap_on_401=False,
        log_level="INFO",
        discovery_url=None,
        discovery_override_url=None,
        bootstrap_cache_path=tmp_path / "bootstrap.json",
        playwright_timeout_s=60,
        jurisdiction="DEP",
        query_params={},
        raw_cli={},
        raw_env={},
    )


@pytest.mark.asyncio
async def test_poll_and_export_updates_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    state = ServiceState()

    raw_event = {
        "sourceEventNumber": "abc",
        "deviceLatitudeLocation": 35.8,
        "deviceLongitudeLocation": -78.6,
        "customersAffectedNumber": 12,
        "status": "Crew Assigned",
    }

    stats = PollCycleStats(
        polled_at=datetime.now(tz=timezone.utc),
        events_seen=1,
        events_emitted=1,
        errors=0,
        duration_s=0.5,
    )
    template = DiscoveryTemplate(url="https://example.com/feed")
    payload = RawPayload(request=template, response={})
    result = PollCycleResult(
        template=template,
        payload=payload,
        events=[raw_event],
        stats=stats,
        rebootstrap_performed=False,
    )

    async def fake_run_poll_cycle(*args, **kwargs) -> PollCycleResult:  # type: ignore[override]
        return result

    monkeypatch.setattr("collector.scheduler.run_poll_cycle", fake_run_poll_cycle)

    await poll_and_export(config, state)

    assert state.last_stats == stats
    assert state.last_error is None
    assert len(state.last_events) == 1
    assert state.last_events[0].event_id == "abc"
    assert state.last_template == template
    assert (tmp_path / "outage_events.csv").exists()
    assert (tmp_path / "outage_events.geojson").exists()
    assert set(state.last_exports) == {"csv", "geojson"}


@pytest.mark.asyncio
async def test_poll_and_export_records_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    state = ServiceState()

    async def failing_poll_cycle(*args, **kwargs):  # type: ignore[override]
        raise RuntimeError("boom")

    monkeypatch.setattr("collector.scheduler.run_poll_cycle", failing_poll_cycle)

    await poll_and_export(config, state)

    assert state.last_error == "boom"
    assert state.last_stats is None
    assert state.last_events == []
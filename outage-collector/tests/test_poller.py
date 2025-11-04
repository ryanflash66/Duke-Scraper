"""Tests for polling workflow helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Mapping, Optional

import httpx
import pytest

from collector.datatypes import (
    BBoxPolicy,
    BoundingBox,
    DiscoveryTemplate,
    ExportFormat,
    LatLng,
    PlacePreset,
    ResolvedConfig,
)
from collector.poller import PollCycleResult, run_poll_cycle, select_events


def _make_config(
    tmp_path: Path,
    *,
    rebootstrap_on_401: bool = False,
    query_params: Optional[Mapping[str, object]] = None,
) -> ResolvedConfig:
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
        query_params=dict(query_params or {}),
    )

    return ResolvedConfig(
        place="raleigh",
        preset=preset,
        poll_interval_s=60.0,
        max_events_per_cycle=10,
        export_format=ExportFormat.BOTH,
        output_dir=tmp_path,
        bbox_policy=BBoxPolicy.INTERSECTS,
        radius_km=50.0,
        padding_km=0.0,
        viewport_only=False,
        rebootstrap_on_401=rebootstrap_on_401,
        log_level="INFO",
        discovery_url=None,
        discovery_override_url=None,
        bootstrap_cache_path=tmp_path / "bootstrap.json",
        playwright_timeout_s=60,
        jurisdiction="DEP",
        query_params=dict(query_params or {}),
        raw_cli={},
        raw_env={},
    )


def test_select_events_applies_filters(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    events: List[Dict[str, object]] = [
        {"event_id": "inside", "lat": 35.8, "lng": -78.6},
        {"event_id": "outside", "lat": 34.0, "lng": -80.0},
        {
            "event_id": "polygon",
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

    selected = select_events(events, config=config, max_events=2)
    ids = {event["event_id"] for event in selected}
    assert ids == {"inside", "polygon"}


@pytest.mark.asyncio
async def test_run_poll_cycle_success(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    template = DiscoveryTemplate(url="https://example.com/feed", method="GET")
    payload = {
        "events": [
            {"event_id": "1", "lat": 35.78, "lng": -78.64},
            {"event_id": "2", "lat": 35.81, "lng": -78.62},
        ]
    }

    async def bootstrapper(cfg: ResolvedConfig, force: bool) -> DiscoveryTemplate:
        assert not force
        return template

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json=payload, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await run_poll_cycle(config, client=client, bootstrapper=bootstrapper)
    finally:
        await client.aclose()

    assert isinstance(result, PollCycleResult)
    assert len(result.events) == 2
    assert not result.rebootstrap_performed


@pytest.mark.asyncio
async def test_run_poll_cycle_rebootstrap(tmp_path: Path) -> None:
    config = _make_config(tmp_path, rebootstrap_on_401=True)
    template = DiscoveryTemplate(url="https://example.com/feed", method="GET")
    payload = {
        "events": [
            {"event_id": "99", "lat": 35.78, "lng": -78.64},
        ]
    }

    calls: List[bool] = []

    async def bootstrapper(cfg: ResolvedConfig, force: bool) -> DiscoveryTemplate:
        calls.append(force)
        return template

    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(status_code=401, request=request)
        return httpx.Response(status_code=200, json=payload, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await run_poll_cycle(config, client=client, bootstrapper=bootstrapper)
    finally:
        await client.aclose()

    assert isinstance(result, PollCycleResult)
    assert result.rebootstrap_performed is True
    assert len(result.events) == 1
    assert calls == [False, True]


@pytest.mark.asyncio
async def test_run_poll_cycle_rebootstrap_on_shape_mismatch(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    template = DiscoveryTemplate(url="https://example.com/feed", method="GET")

    payload_good = {
        "events": [
            {"event_id": "shape", "lat": 35.79, "lng": -78.63},
        ]
    }

    calls: List[bool] = []
    attempts = {"count": 0}

    async def bootstrapper(cfg: ResolvedConfig, force: bool) -> DiscoveryTemplate:
        calls.append(force)
        return template

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(status_code=200, json={"status": "ok"}, request=request)
        return httpx.Response(status_code=200, json=payload_good, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await run_poll_cycle(config, client=client, bootstrapper=bootstrapper)
    finally:
        await client.aclose()

    assert isinstance(result, PollCycleResult)
    assert result.rebootstrap_performed is True
    assert len(result.events) == 1
    assert calls == [False, True]


@pytest.mark.asyncio
async def test_run_poll_cycle_applies_extra_query_params(tmp_path: Path) -> None:
    config = _make_config(tmp_path, query_params={"includeDetails": "true", "jurisdiction": "override"})
    template = DiscoveryTemplate(
        url="https://example.com/feed",
        method="GET",
        query={"existing": "value"},
    )

    payload = {"events": [{"event_id": "extra", "lat": 35.8, "lng": -78.6}]}

    async def bootstrapper(cfg: ResolvedConfig, force: bool) -> DiscoveryTemplate:  # pragma: no cover - simple stub
        return template

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        assert params.get("existing") == "value"
        assert params.get("includeDetails") == "true"
        # jurisdiction override should be replaced with config.jurisdiction
        assert params.get("jurisdiction") == "DEP"
        return httpx.Response(status_code=200, json=payload, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await run_poll_cycle(config, client=client, bootstrapper=bootstrapper)
    finally:
        await client.aclose()

    assert len(result.events) == 1

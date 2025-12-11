from __future__ import annotations

from pathlib import Path

import pytest

from collector import discovery
from collector.datatypes import (
    BBoxPolicy,
    BoundingBox,
    DiscoveryTemplate,
    ExportFormat,
    LatLng,
    PlacePreset,
    ResolvedConfig,
)


def _make_config(tmp_path: Path, discovery_url: str | None = None) -> ResolvedConfig:
    preset = PlacePreset(
        jurisdiction="DEP",
        center=LatLng(lat=35.0, lng=-78.0),
        radius_km=35.0,
        bbox=BoundingBox(min_lat=34.5, max_lat=35.5, min_lng=-78.5, max_lng=-77.5),
        polygon=[
            LatLng(lat=34.5, lng=-78.5),
            LatLng(lat=35.5, lng=-78.5),
            LatLng(lat=35.5, lng=-77.5),
        ],
        query_params={},
    )
    return ResolvedConfig(
        place="raleigh",
        preset=preset,
        poll_interval_s=90.0,
        max_events_per_cycle=1000,
        export_format=ExportFormat.BOTH,
        output_dir=tmp_path,
        bbox_policy=BBoxPolicy.CENTROID,
        radius_km=35.0,
        padding_km=0.0,
        viewport_only=False,
        rebootstrap_on_401=True,
        log_level="INFO",
        discovery_url=discovery_url,
        discovery_override_url=None,
        bootstrap_cache_path=tmp_path / "cache.json",
        playwright_timeout_s=5,
        jurisdiction="DEP",
        query_params={},
        raw_cli={},
        raw_env={},
    )


def _sample_template() -> DiscoveryTemplate:
    return DiscoveryTemplate(
        url="https://outagemap.duke-energy.com/resources/data/outage-maps/events",
        method="GET",
        headers={"authorization": "Bearer token", "accept": "application/json"},
        query={"jurisdiction": ["DEP"], "format": ["json"]},
        body=None,
    )


def test_url_is_candidate_accepts_outage_feed():
    assert discovery.url_is_candidate("https://outagemap.duke-energy.com/resources/data/outage-maps/features")


def test_url_is_candidate_rejects_static_assets():
    assert not discovery.url_is_candidate("https://outagemap.duke-energy.com/tiles/1/2/3.png")
    assert not discovery.url_is_candidate("https://outagemap.duke-energy.com/config/app.config.json")


def test_url_is_candidate_rejects_instrumentation_path():
    url = "https://outagemap.duke-energy.com/rb_bf07599nnv?type=js3"
    assert not discovery.url_is_candidate(url)


def test_url_is_candidate_rejects_analytics_host():
    url = "https://www.google-analytics.com/j/collect?v=1&dl=https://outagemap.duke-energy.com/"
    assert not discovery.url_is_candidate(url)

def test_url_is_candidate_rejects_non_allowlisted_host():
    url = "https://dukeenergy.azure-api.net/outage-maps/outages"
    assert not discovery.url_is_candidate(url)


def test_cache_round_trip(tmp_path: Path):
    config = _make_config(tmp_path)
    template = _sample_template()
    sources = discovery.determine_start_urls(config)

    discovery.save_cached_template(config.bootstrap_cache_path, template, sources)
    loaded = discovery.load_cached_template(config.bootstrap_cache_path, sources)

    assert loaded == template

def test_determine_start_urls_prioritizes_ncsc(tmp_path: Path):
    config = _make_config(tmp_path)
    start_urls = discovery.determine_start_urls(config)

    assert start_urls[0].endswith("/current-outages/ncsc")


@pytest.mark.asyncio
async def test_bootstrap_honors_override(tmp_path: Path):
    config = _make_config(tmp_path)
    override_url = "https://prod.apigee.duke-energy.app/outage-maps/api/outages?jurisdiction=DEP"
    config_override = config.model_copy(update={"discovery_override_url": override_url})

    template = await discovery.bootstrap(config_override)

    assert template.url == override_url
    assert template.headers.get("accept") == "application/json"
    assert template.query["jurisdiction"] == ["DEP"]


def test_cache_invalidated_when_sources_change(tmp_path: Path):
    config = _make_config(tmp_path)
    template = _sample_template()
    original_sources = ["https://outagemap.duke-energy.com/#/current"]
    discovery.save_cached_template(config.bootstrap_cache_path, template, original_sources)

    mismatch_sources = ["https://another.example"]
    loaded = discovery.load_cached_template(config.bootstrap_cache_path, mismatch_sources)

    assert loaded is None


def test_cache_rejects_invalid_candidate(tmp_path: Path):
    config = _make_config(tmp_path)
    bad_template = DiscoveryTemplate(url="https://www.google-analytics.com/j/collect", method="GET")
    sources = discovery.determine_start_urls(config)

    discovery.save_cached_template(config.bootstrap_cache_path, bad_template, sources)
    loaded = discovery.load_cached_template(config.bootstrap_cache_path, sources)

    assert loaded is None


def test_sanitize_headers_removes_sensitive_entries():
    headers = {
        ":authority": "outagemap.duke-energy.com",
        "Accept": "application/json",
        "Cookie": "top-secret",
        "Content-Type": "application/json",
        "Sec-Fetch-Mode": "cors",
        "Authorization": "Bearer abc",
        "X-Api-Key": "xyz",
    }

    sanitized = discovery._sanitize_headers(headers)  # type: ignore[attr-defined]

    assert "Cookie" not in sanitized
    assert ":authority" not in sanitized
    assert "Sec-Fetch-Mode" not in sanitized
    assert sanitized["Content-Type"] == "application/json"
    assert sanitized["Authorization"].startswith("Bearer")
    assert sanitized.get("X-Api-Key") == "xyz"


def test_collect_outage_keys_detects_nested_payload():
    payload = {
        "meta": {"timestamp": "2025-01-01T00:00:00Z"},
        "data": [
            {
                "EventId": "123",
                "customersAffectedNumber": 42,
                "geometry": {
                    "coordinates": [
                        {"latitude": 35.1, "longitude": -78.9},
                    ]
                },
            }
        ],
    }

    keys = discovery._collect_outage_keys(payload)  # type: ignore[attr-defined]

    assert "eventid" in keys
    assert "customersaffectednumber" in keys
    assert "latitude" in keys
    assert "longitude" in keys


@pytest.mark.asyncio
async def test_bootstrap_uses_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config = _make_config(tmp_path)
    template = _sample_template()
    sources = discovery.determine_start_urls(config)
    discovery.save_cached_template(config.bootstrap_cache_path, template, sources)

    called = False

    async def fake_discover(_context):
        nonlocal called
        called = True
        return template

    monkeypatch.setattr(discovery, "_discover_with_playwright", fake_discover)

    result = await discovery.bootstrap(config)

    assert result == template
    assert called is False


@pytest.mark.asyncio
async def test_bootstrap_force_refresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config = _make_config(tmp_path)
    template_cached = _sample_template()
    sources = discovery.determine_start_urls(config)
    discovery.save_cached_template(config.bootstrap_cache_path, template_cached, sources)

    refreshed_template = DiscoveryTemplate(
        url="https://api.duke-energy.com/outage-maps/new",
        method="GET",
        headers={"authorization": "Bearer new"},
        query={},
        body=None,
    )

    async def fake_discover(_context):
        return refreshed_template

    monkeypatch.setattr(discovery, "_discover_with_playwright", fake_discover)

    result = await discovery.bootstrap(config, force_refresh=True)

    assert result == refreshed_template


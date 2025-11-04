"""Tests for FastAPI service endpoints and runtime state."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from collector.datatypes import LatLng, NormalizedEvent, PollCycleStats
from collector.service import ServiceState, app, attach_state


def _sample_state() -> ServiceState:
    state = ServiceState()
    stats = PollCycleStats(
        polled_at=datetime.now(tz=timezone.utc),
        events_seen=2,
        events_emitted=2,
        errors=0,
        duration_s=1.2,
    )
    events = [
        NormalizedEvent(
            event_id="abc",
            lat=35.8,
            lng=-78.6,
            customers_affected=10,
            jurisdiction="DEP",
        ),
        NormalizedEvent(
            event_id="def",
            lat=35.81,
            lng=-78.61,
            customers_affected=5,
            jurisdiction="DEP",
        ),
    ]
    exports = {"csv": "/tmp/outage_events.csv"}
    state.record_success(stats, events, exports)
    return state


def test_healthz_initializing() -> None:
    state = ServiceState()
    attach_state(state)
    client = TestClient(app)

    response = client.get("/healthz")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "initializing"


def test_healthz_with_stats_and_error() -> None:
    state = _sample_state()
    state.record_error("upstream failure")
    attach_state(state)
    client = TestClient(app)

    response = client.get("/healthz")
    payload = response.json()

    assert payload["status"] == "degraded"
    assert payload["error"] == "upstream failure"


def test_stats_endpoint_returns_snapshot() -> None:
    state = _sample_state()
    attach_state(state)
    client = TestClient(app)

    response = client.get("/stats")
    payload = response.json()

    assert response.status_code == 200
    assert payload["stats"]["events_seen"] == 2
    assert payload["exports"] == {"csv": "/tmp/outage_events.csv"}


def test_stats_endpoint_503_without_data() -> None:
    state = ServiceState()
    attach_state(state)
    client = TestClient(app)

    response = client.get("/stats")
    assert response.status_code == 503


def test_latest_events_endpoint_limits_results() -> None:
    state = _sample_state()
    attach_state(state)
    client = TestClient(app)

    response = client.get("/events/latest", params={"limit": 1})
    payload = response.json()

    assert response.status_code == 200
    assert len(payload["events"]) == 1
    assert payload["events"][0]["event_id"] == "def"
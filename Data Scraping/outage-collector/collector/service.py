"""FastAPI service surface and runtime state helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException

from .datatypes import DiscoveryTemplate, NormalizedEvent, PollCycleStats, ResolvedConfig

app = FastAPI(title="Outage Collector", version="0.1.0")


@dataclass
class ServiceState:
    """Holds the latest polling snapshot for API consumers."""

    last_stats: Optional[PollCycleStats] = None
    last_events: List[NormalizedEvent] = field(default_factory=list)
    last_exports: Dict[str, str] = field(default_factory=dict)
    last_error: Optional[str] = None
    config: Optional[ResolvedConfig] = None
    last_template: Optional[DiscoveryTemplate] = None

    def record_success(
        self,
        stats: PollCycleStats,
        events: List[NormalizedEvent],
        exports: Dict[str, str],
    ) -> None:
        self.last_stats = stats
        self.last_events = list(events)
        self.last_exports = dict(exports)
        self.last_error = None

    def record_error(self, message: str) -> None:
        self.last_error = message

    def set_config(self, config: ResolvedConfig) -> None:
        self.config = config

    def record_template(self, template: DiscoveryTemplate) -> None:
        self.last_template = template


def attach_state(state: ServiceState) -> None:
    """Attach the runtime state to the FastAPI app."""

    app.state.runtime = state


def get_state() -> Optional[ServiceState]:
    """Return the attached runtime state if available."""

    return getattr(app.state, "runtime", None)


def _require_state() -> ServiceState:
    state = get_state()
    if state is None:
        raise HTTPException(status_code=503, detail="Service state not initialised")
    return state


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    """Return service health, including the last polling status."""

    state = get_state()
    payload: Dict[str, object] = {"status": "initializing"}

    if state and state.last_stats:
        payload.update(
            {
                "status": "ok",
                "last_poll_at": state.last_stats.polled_at.isoformat(),
                "events_seen": state.last_stats.events_seen,
                "events_emitted": state.last_stats.events_emitted,
                "errors": state.last_stats.errors,
            }
        )
    if state and state.last_error:
        payload["status"] = "degraded"
        payload["error"] = state.last_error

    return payload


@app.get("/stats")
async def stats() -> dict[str, object]:
    """Return the most recent polling statistics and export destinations."""

    state = _require_state()
    if not state.last_stats:
        raise HTTPException(status_code=503, detail="Polling has not completed yet")

    return {
        "stats": state.last_stats.model_dump(mode="json"),
        "exports": state.last_exports,
        "error": state.last_error,
    }


@app.get("/events/latest")
async def latest_events(limit: int = 100) -> dict[str, object]:
    """Return the most recent normalized events up to the requested limit."""

    state = _require_state()
    events = state.last_events[-limit:] if limit > 0 else []
    return {"events": [event.model_dump(mode="json") for event in events]}


@app.get("/config")
async def config_snapshot() -> dict[str, object]:
    """Return a redacted view of the active configuration and discovery template."""

    state = _require_state()
    payload: Dict[str, object] = {}

    if state.config:
        payload["config"] = state.config.redacted_dict()
    if state.last_template:
        payload["template"] = state.last_template.redacted_dict()

    if not payload:
        raise HTTPException(status_code=503, detail="Configuration not yet initialised")

    return payload

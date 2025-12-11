"""HTTP polling orchestration for the outage collector."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import httpx

from .datatypes import DiscoveryTemplate, PollCycleStats, RawPayload, ResolvedConfig
from .filters import expand_bbox, filter_in_bbox, filter_in_radius
from .discovery import bootstrap, invalidate_cache
from .logging_utils import get_logger

logger = get_logger("collector.poller")

_DEFAULT_TIMEOUT_S = 30.0


class PollerError(RuntimeError):
    """Base error raised by the polling workflow."""


class PollerAuthError(PollerError):
    """Raised when the feed returns an authentication failure."""


class PollerDecodeError(PollerError):
    """Raised when the outage feed payload cannot be decoded."""


class PollerShapeError(PollerError):
    """Raised when the outage feed payload lacks expected event collections."""


class PollerEndpointError(PollerError):
    """Raised when the outage feed endpoint responds with an unexpected status."""


@dataclass(slots=True)
class PollCycleResult:
    """Return payload for a polling cycle."""

    template: DiscoveryTemplate
    payload: RawPayload
    events: List[Dict[str, Any]]
    stats: PollCycleStats
    rebootstrap_performed: bool


async def poll_once(
    template: DiscoveryTemplate,
    *,
    jurisdiction: Optional[str] = None,
    extra_query: Optional[Mapping[str, Any]] = None,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: Optional[float] = None,
) -> RawPayload:
    """Perform a single HTTP request against the outage feed and return the raw payload."""

    owns_client = False
    request_timeout = timeout_s or _DEFAULT_TIMEOUT_S
    http_client = client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=request_timeout)
        owns_client = True

    params = _prepare_query_params(template.query)
    if extra_query:
        params = _merge_query_params(params, extra_query)
    if jurisdiction:
        params = _ensure_jurisdiction(params, jurisdiction)

    request_kwargs: Dict[str, Any] = {
        "method": template.method or "GET",
        "url": template.url,
        "headers": template.headers or {},
        "params": params,
    }

    body = template.body
    if body is not None:
        if isinstance(body, (dict, list)):
            request_kwargs["json"] = body
        elif isinstance(body, (bytes, bytearray)):
            request_kwargs["content"] = body
        else:
            request_kwargs["content"] = str(body)

    try:
        response = await http_client.request(timeout=request_timeout, **request_kwargs)
    except httpx.HTTPError as exc:  # pragma: no cover - propagated to caller
        if owns_client:
            await http_client.aclose()
        raise PollerError("Failed to perform outage feed request") from exc

    if response.status_code in {401, 403}:
        if owns_client:
            await http_client.aclose()
        raise PollerAuthError(f"Received {response.status_code} from outage feed")

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if owns_client:
            await http_client.aclose()
        raise PollerEndpointError(f"Outage feed returned HTTP {exc.response.status_code}") from exc

    try:
        parsed = response.json()
    except json.JSONDecodeError as exc:
        if owns_client:
            await http_client.aclose()
        raise PollerDecodeError("Unable to decode outage feed response as JSON") from exc

    normalised: Dict[str, Any]
    if isinstance(parsed, dict):
        normalised = parsed
    elif isinstance(parsed, list):
        normalised = {"data": parsed}
    else:
        normalised = {"data": parsed}

    payload = RawPayload(request=template, response=normalised)

    if owns_client:
        await http_client.aclose()

    return payload


def select_events(
    raw_events: Iterable[Dict[str, Any]],
    *,
    config: Optional[ResolvedConfig] = None,
    max_events: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Filter raw events using configuration policies and apply the max events cap."""

    events = list(raw_events)
    if config:
        bbox = config.preset.bbox.as_tuple()
        if config.padding_km:
            bbox = expand_bbox(bbox, config.padding_km, config.preset.center.lat)
        viewport_polygon = None
        if config.viewport_only:
            viewport_polygon = [(pt.lat, pt.lng) for pt in config.preset.polygon]

        events = filter_in_bbox(
            events,
            bbox,
            policy=config.bbox_policy,
            viewport_polygon=viewport_polygon,
        )
        if config.radius_km:
            centre = (config.preset.center.lat, config.preset.center.lng)
            events = filter_in_radius(events, centre, config.radius_km)

    if max_events is not None and max_events >= 0:
        events = events[:max_events]
    return events


async def run_poll_cycle(
    config: ResolvedConfig,
    *,
    client: Optional[httpx.AsyncClient] = None,
    bootstrapper: Optional[Callable[[ResolvedConfig, bool], Awaitable[DiscoveryTemplate]]] = None,
) -> PollCycleResult:
    """Execute a full polling cycle including optional re-bootstrap handling."""

    start = perf_counter()

    bootstrap_fn: Callable[[ResolvedConfig, bool], Awaitable[DiscoveryTemplate]]
    if bootstrapper is None:

        async def default_bootstrap(cfg: ResolvedConfig, force: bool) -> DiscoveryTemplate:
            return await bootstrap(cfg, force_refresh=force)

        bootstrap_fn = default_bootstrap
    else:
        bootstrap_fn = bootstrapper

    template = await bootstrap_fn(config, False)

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S)
    errors = 0
    rebootstrap = False

    try:
        attempts = 0
        while True:
            attempts += 1
            try:
                raw_payload = await poll_once(
                    template,
                    jurisdiction=config.jurisdiction,
                    extra_query=config.query_params,
                    client=http_client,
                    timeout_s=_DEFAULT_TIMEOUT_S,
                )
                events_seen = _extract_events(raw_payload.response)
                break
            except PollerAuthError as auth_error:
                if not config.rebootstrap_on_401 or attempts >= 2:
                    raise
                errors += 1
                rebootstrap = True
                invalidate_cache(config.bootstrap_cache_path)
                template = await bootstrap_fn(config, True)
                continue
            except (PollerDecodeError, PollerShapeError) as exc:
                if attempts >= 2:
                    raise
                errors += 1
                rebootstrap = True
                invalidate_cache(config.bootstrap_cache_path)
                template = await bootstrap_fn(config, True)
                continue
            except PollerEndpointError as exc:
                if attempts >= 2:
                    raise
                errors += 1
                rebootstrap = True
                invalidate_cache(config.bootstrap_cache_path)
                template = await bootstrap_fn(config, True)
                continue

        events_selected = select_events(
            events_seen,
            config=config,
            max_events=config.max_events_per_cycle,
        )
    finally:
        if owns_client:
            await http_client.aclose()

    stats = PollCycleStats(
        polled_at=datetime.now(tz=timezone.utc),
        events_seen=len(events_seen),
        events_emitted=len(events_selected),
        errors=errors,
        duration_s=perf_counter() - start,
    )

    logger.info(
        "poll_cycle_completed",
        extra={
            "event": "poll_cycle_completed",
            "rebootstrap": rebootstrap,
            "events_seen": stats.events_seen,
            "events_emitted": stats.events_emitted,
            "errors": stats.errors,
            "duration_s": round(stats.duration_s, 3),
        },
    )

    return PollCycleResult(
        template=template,
        payload=raw_payload,
        events=events_selected,
        stats=stats,
        rebootstrap_performed=rebootstrap,
    )


def _prepare_query_params(query: Optional[Mapping[str, Any]]) -> List[Tuple[str, str]]:
    """Normalise template query parameters into a list of tuples."""

    if not query:
        return []

    flattened: List[Tuple[str, str]] = []
    for key, value in query.items():
        if isinstance(value, (list, tuple, set)):
            flattened.extend((key, str(item)) for item in value)
        else:
            flattened.append((key, str(value)))
    return flattened


def _merge_query_params(
    params: Sequence[Tuple[str, str]],
    overrides: Mapping[str, Any],
) -> List[Tuple[str, str]]:
    """Apply override query parameters, replacing existing keys when provided."""

    merged = [(key, value) for key, value in params]
    if not overrides:
        return merged

    override_keys = {str(key).lower() for key in overrides.keys()}
    merged = [(key, value) for key, value in merged if key.lower() not in override_keys]
    merged.extend(_prepare_query_params(dict(overrides)))
    return merged


def _ensure_jurisdiction(
    params: Sequence[Tuple[str, str]],
    jurisdiction: str,
) -> List[Tuple[str, str]]:
    """Ensure the jurisdiction value is present in the query parameters."""

    updated: List[Tuple[str, str]] = []
    has_key = False
    for key, value in params:
        lowered = key.lower()
        if lowered == "jurisdictions":
            has_key = True
            parts = [part for part in value.split(",") if part]
            if jurisdiction not in parts:
                parts.append(jurisdiction)
            updated.append((key, ",".join(parts)))
        elif lowered == "jurisdiction":
            has_key = True
            updated.append((key, jurisdiction))
        else:
            updated.append((key, value))

    if not has_key:
        updated.append(("jurisdiction", jurisdiction))
    return updated


def _extract_events(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Best-effort extraction of outage events from a response payload."""

    candidates: Iterable[Any]
    source_key: Optional[str] = None
    if "events" in response and isinstance(response["events"], list):
        candidates = response["events"]
        source_key = "events"
    elif "outages" in response and isinstance(response["outages"], list):
        candidates = response["outages"]
        source_key = "outages"
    elif "features" in response and isinstance(response["features"], list):
        candidates = response["features"]
        source_key = "features"
    elif "data" in response and isinstance(response["data"], list):
        candidates = response["data"]
        source_key = "data"
    else:
        values = [value for value in response.values() if isinstance(value, list)]
        if values:
            candidates = values[0]
            source_key = "__dynamic__"
        else:
            candidates = []
            source_key = None

    if source_key is None:
        raise PollerShapeError("Outage payload did not include any list-like collections")

    normalised: List[Dict[str, Any]] = []
    for item in candidates:
        if isinstance(item, dict):
            normalised.append(_flatten_feature(item))
        else:
            normalised.append({"value": item})
    return normalised


def _flatten_feature(feature: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten common ArcGIS-style feature payloads."""

    attributes = feature.get("attributes") if isinstance(feature, dict) else None
    geometry = feature.get("geometry") if isinstance(feature, dict) else None

    if isinstance(attributes, dict):
        merged = dict(attributes)
        if isinstance(geometry, dict):
            merged.setdefault("geometry", geometry)
        return merged

    return dict(feature)

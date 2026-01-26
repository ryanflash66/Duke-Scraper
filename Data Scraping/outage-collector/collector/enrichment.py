"""Detail enrichment for normalized outage events."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable, List, Optional, Sequence
from urllib.parse import urlparse, urlunparse

import httpx

from .datatypes import DiscoveryTemplate, LatLng, NormalizedEvent, ResolvedConfig
from .logging_utils import get_logger

logger = get_logger("collector.enrichment")

_DETAIL_PATH = "/outage-maps/v1/outages/outage"
_DEFAULT_TIMEOUT_S = 15.0
_DEFAULT_CONCURRENCY = 10


async def enrich_events(
    events: Sequence[NormalizedEvent],
    *,
    config: ResolvedConfig,
    template: DiscoveryTemplate,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    concurrency: int = _DEFAULT_CONCURRENCY,
    detail_fetcher: Optional[Callable[[str], Awaitable[dict[str, Any]]]] = None,
) -> List[NormalizedEvent]:
    """Fetch detail payloads for each event and merge into normalized records."""

    if not events:
        return list(events)

    jurisdiction = config.jurisdiction
    if not jurisdiction:
        return list(events)

    http_client = client
    owns_client = False
    detail_url: Optional[str] = None

    if detail_fetcher is None:
        if http_client is None:
            http_client = httpx.AsyncClient(timeout=timeout_s)
            owns_client = True

        detail_url = _build_detail_url(template.url)
        headers = dict(template.headers or {})

        async def http_detail_fetcher(event_id: str) -> dict[str, Any]:
            if not event_id:
                return {}
            params = {"jurisdiction": jurisdiction, "sourceEventNumber": event_id}
            try:
                response = await http_client.get(detail_url, params=params, headers=headers)
                response.raise_for_status()
            except Exception as exc:  # pragma: no cover - network guardrail
                logger.warning(
                    "detail_fetch_failed",
                    extra={
                        "event": "detail_fetch_failed",
                        "event_id": event_id,
                        "error": str(exc),
                    },
                )
                return {}

            try:
                payload = response.json()
            except ValueError:
                logger.warning(
                    "detail_decode_failed",
                    extra={"event": "detail_decode_failed", "event_id": event_id},
                )
                return {}

            data = payload.get("data")
            if isinstance(data, dict):
                return data
            return {}

        detail_fetcher = http_detail_fetcher

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def enrich_one(event: NormalizedEvent) -> NormalizedEvent:
        async with semaphore:
            try:
                detail = await detail_fetcher(event.event_id)
            except Exception as exc:  # pragma: no cover - defensive catch
                logger.warning(
                    "detail_fetcher_exception",
                    extra={
                        "event": "detail_fetcher_exception",
                        "event_id": event.event_id,
                        "error": str(exc),
                    },
                )
                return event

        if not detail:
            return event

        updated = _apply_detail(event, detail)
        return updated or event

    try:
        enriched = await asyncio.gather(*(enrich_one(event) for event in events))
    finally:
        if owns_client and http_client is not None:
            await http_client.aclose()

    return list(enriched)


def _build_detail_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(
        parsed._replace(path=_DETAIL_PATH, params="", query="", fragment="")
    )


def _apply_detail(event: NormalizedEvent, detail: dict[str, Any]) -> Optional[NormalizedEvent]:
    update: dict[str, Any] = {}

    status = detail.get("crewStatTxt") or detail.get("status")
    if status:
        update["status_text"] = str(status)

    cause_code = detail.get("outageCause")
    if cause_code:
        update["cause_code"] = str(cause_code)

    cause_description = detail.get("causeDescription")
    if cause_description:
        update["cause_description"] = str(cause_description)

    restoration = _parse_timestamp(
        detail.get("estimatedRestorationTime") or detail.get("etrOverride")
    )
    if restoration:
        update["estimated_restoration_at"] = restoration

    first_reported = _parse_timestamp(
        detail.get("startTime") or detail.get("outageCreationTime")
    )
    if first_reported:
        update["first_reported_at"] = first_reported

    customers = detail.get("customersAffectedSum") or detail.get("maxCustomersAffectedNumber")
    if customers is not None:
        try:
            update["customers_affected"] = int(customers)
        except (TypeError, ValueError):
            pass

    polygon = detail.get("trfPolygonXyLoc")
    if isinstance(polygon, Iterable):
        points: List[LatLng] = []
        for candidate in polygon:
            lat = candidate.get("lat") if isinstance(candidate, dict) else None
            lng = candidate.get("lng") if isinstance(candidate, dict) else None
            if lat is None or lng is None:
                continue
            try:
                points.append(LatLng(lat=float(lat), lng=float(lng)))
            except Exception:  # pragma: no cover - validation guard
                continue
        if points:
            update["polygon"] = points

    if not update:
        return None

    return event.model_copy(update=update)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None

    if isinstance(value, datetime):
        return value

    if isinstance(value, (int, float)):
        # assume epoch seconds
        try:
            return datetime.fromtimestamp(float(value))
        except (TypeError, ValueError, OSError):
            return None

    if isinstance(value, str):
        try:
            iso = value.replace("Z", "+00:00")
            return datetime.fromisoformat(iso)
        except ValueError:
            return None

    return None
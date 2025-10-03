"""Scheduler wiring for recurring outage polling."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .datatypes import DiscoveryTemplate, ExportFormat, ResolvedConfig
from .logging_utils import get_logger
from .normalizer import normalize_records
from .poller import run_poll_cycle
from .service import ServiceState
from .storage import write_csv, write_geojson
from .enrichment import enrich_events

logger = get_logger("collector.scheduler")


async def poll_and_export(
    config: ResolvedConfig,
    state: ServiceState,
    *,
    client: Optional[httpx.AsyncClient] = None,
    bootstrapper: Optional[Callable[[ResolvedConfig, bool], Awaitable[DiscoveryTemplate]]] = None,
) -> None:
    """Execute a polling cycle, normalize results, and persist exports."""

    try:
        result = await run_poll_cycle(config, client=client, bootstrapper=bootstrapper)
        normalized = normalize_records(result.events, config.jurisdiction)
        if normalized:
            normalized = await enrich_events(
                normalized,
                config=config,
                template=result.template,
            )
        state.record_template(result.template)

        exports: Dict[str, str] = {}
        if config.export_format in {ExportFormat.CSV, ExportFormat.BOTH}:
            csv_path = write_csv(normalized, config.output_dir)
            exports[ExportFormat.CSV.value] = str(csv_path)
        if config.export_format in {ExportFormat.GEOJSON, ExportFormat.BOTH}:
            geo_path = write_geojson(normalized, config.output_dir)
            exports[ExportFormat.GEOJSON.value] = str(geo_path)

        state.record_success(result.stats, normalized, exports)

        logger.info(
            "poll_cycle_success",
            extra={
                "event": "poll_cycle_success",
                "events": len(normalized),
                "exports": exports,
            },
        )
    except Exception as exc:  # pragma: no cover - logged for observability
        logger.exception(
            "poll_cycle_failure",
            extra={"event": "poll_cycle_failure", "error": str(exc)},
        )
        state.record_error(str(exc))
        return


def build_scheduler(config: ResolvedConfig, state: ServiceState) -> AsyncIOScheduler:
    """Create an AsyncIO scheduler configured with the polling job."""

    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        poll_and_export,
        trigger="interval",
        seconds=config.poll_interval_s,
        kwargs={"config": config, "state": state},
        next_run_time=datetime.now(tz=timezone.utc),
        id="polling_cycle",
        coalesce=True,
        max_instances=1,
    )

    return scheduler

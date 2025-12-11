"""Entry point for running the outage collector package."""

from __future__ import annotations

import asyncio
from typing import Optional, Sequence

import uvicorn

from .config import resolve_runtime_config
from .datatypes import ResolvedConfig
from .logging_utils import configure_logging, get_logger, log_config_snapshot
from .scheduler import build_scheduler
from .service import ServiceState, app as fastapi_app, attach_state


async def _run_application(config: ResolvedConfig) -> None:
    configure_logging(config.log_level)
    log_config_snapshot(config)
    logger = get_logger("collector.runtime")

    state = ServiceState()
    state.set_config(config)
    attach_state(state)

    scheduler = build_scheduler(config, state)
    scheduler.start()
    logger.info(
        "scheduler_started",
        extra={"event": "scheduler_started", "interval_s": config.poll_interval_s},
    )

    server_config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=8000,
        loop="asyncio",
        log_level=config.log_level.lower(),
    )
    server = uvicorn.Server(server_config)

    try:
        await server.serve()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("runtime_exception", extra={"event": "runtime_exception", "error": str(exc)})
        raise
    finally:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped", extra={"event": "scheduler_stopped"})


def main(argv: Optional[Sequence[str]] = None) -> None:
    """CLI entry point."""

    # resolve_runtime_config reads from argv if provided, so route through there first
    config, _ = resolve_runtime_config(argv)
    asyncio.run(_run_application(config))


if __name__ == "__main__":  # pragma: no cover
    main()

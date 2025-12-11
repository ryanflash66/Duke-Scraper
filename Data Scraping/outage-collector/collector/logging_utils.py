"""Structured logging helpers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.config import dictConfig
from pathlib import Path
from typing import Any, Dict, Set

from .datatypes import ResolvedConfig

STANDARD_LOG_RECORD_KEYS: Set[str] = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "process",
    "processName",
}


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, set):
        return list(value)
    return str(value)


class JsonLogFormatter(logging.Formatter):
    """Minimal JSON formatter for structured logs."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        message = record.getMessage()
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": message,
        }

        for key, value in record.__dict__.items():
            if key in STANDARD_LOG_RECORD_KEYS or key == "message":
                continue
            payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=_json_default, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Configure application-wide structured logging."""

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": "collector.logging_utils.JsonLogFormatter"}},
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                }
            },
            "root": {
                "handlers": ["default"],
                "level": level.upper(),
            },
        }
    )


def get_logger(name: str = "collector") -> logging.Logger:
    """Return a namespaced logger."""

    return logging.getLogger(name)


_logged_config_digests: Set[str] = set()


def log_config_snapshot(config: ResolvedConfig) -> None:
    """Emit the resolved configuration once at startup."""

    digest = json.dumps(config.redacted_dict(), sort_keys=True)
    if digest in _logged_config_digests:  # pragma: no cover - guardrail
        return
    _logged_config_digests.add(digest)

    logger = get_logger("collector.config")
    logger.info(
        "resolved_config",
        extra={
            "event": "resolved_config",
            "config": config.redacted_dict(),
        },
    )

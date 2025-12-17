"""Playwright-based discovery of Duke Energy outage feed endpoints."""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from playwright.async_api import (  # type: ignore[import-untyped]
    Browser,
    Page,
    Playwright,
    Request,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)
from pydantic import BaseModel, ValidationError

from .datatypes import DiscoveryTemplate, ResolvedConfig
from .logging_utils import get_logger

logger = get_logger("collector.discovery")

# Public outage map entry points to attempt when discovery_url is not provided.
DEFAULT_START_URLS: tuple[str, ...] = (
    "https://outagemap.duke-energy.com/#/current-outages/ncsc",
    "https://outagemap.duke-energy.com/#/current-outages",
    "https://outagemap.duke-energy.com/#/current",
    "https://outagemap.duke-energy.com/#/",
)

# Accepted URL fragments for outage data feeds.
_ACCEPT_PATH_SUBSTRINGS: tuple[str, ...] = (
    "/outage-maps/",
    "/outages",
    "/resources/data/",
)

_ACCEPT_HOST_SUBSTRINGS: tuple[str, ...] = (
    "prod.apigee.duke-energy.app",
    "outagemap.duke-energy.com",
)

_REJECT_HOST_SUBSTRINGS: tuple[str, ...] = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "googlesyndication.com",
    "analytics.google.com",
)

# Reject common static assets and configuration endpoints.
_REJECT_SUBSTRINGS: tuple[str, ...] = (
    "/config/",
    "/tiles/",
    "/tile/",
    "/static/",
    "/bf",
    "/rb_",
    "dyna",
    "rum",
    "beacon",
)

_REJECT_SUFFIXES: tuple[str, ...] = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".map",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
)

_ACCEPT_RESOURCE_TYPES: frozenset[str] = frozenset({"xhr", "fetch"})

_ALLOWED_HEADER_NAMES: frozenset[str] = frozenset(
    {
        "accept",
        "accept-language",
        "referer",
        "origin",
        "user-agent",
        "authorization",
        "x-api-key",
        "content-type",
    }
)

_OUTAGE_COLLECTION_KEYS: frozenset[str] = frozenset({"data", "outages", "events", "features"})

_HEADER_STRIP_PREFIXES: tuple[str, ...] = (
    ":",
    "sec-",
    "x-dt-",
    "x-ray",
    "x-newrelic",
)

_HEADER_STRIP_NAMES: frozenset[str] = frozenset(
    {
        "cookie",
        "cookies",
        "dnt",
        "upgrade-insecure-requests",
        "priority",
        "accept-encoding",
        "content-length",
        "x-requested-with",
    }
)

_OUTAGE_KEY_CANDIDATES: frozenset[str] = frozenset(
    {
        "sourceeventnumber",
        "customersaffectednumber",
        "devicelatitudelocation",
        "devicelongitudelocation",
        "convexhull",
        "outagecause",
        "eventid",
        "latitude",
        "longitude",
    }
)

_MIN_BODY_BYTES = 2 * 1024

_CANDIDATE_DECISION_DELAY_S = 0.75


@dataclass(slots=True)
class _CandidateRecord:
    template: DiscoveryTemplate
    url: str
    host: str
    path: str
    status: int
    content_type: str
    body_len: int
    outage_keys: set[str]
    has_jurisdiction: bool
    jurisdiction_match: bool
    score: tuple[int, int, int, int, int, int]
    seen_order: int


class DiscoveryError(RuntimeError):
    """Raised when discovery cannot find a suitable endpoint."""


class DiscoveryCacheEntry(BaseModel):
    """Serialized record of a previously successful discovery run."""

    template: DiscoveryTemplate
    retrieved_at: datetime
    source_urls: list[str]


@dataclass(slots=True)
class DiscoveryContext:
    """Holds runtime state for a discovery attempt."""

    start_urls: Sequence[str]
    timeout_s: int
    cache_path: Path
    jurisdiction: str | None = None


def determine_start_urls(config: ResolvedConfig, overrides: Optional[Sequence[str]] = None) -> list[str]:
    """Resolve the list of URLs Playwright should visit for discovery."""

    if overrides:
        return [str(url) for url in overrides if url]
    if config.discovery_url:
        return [config.discovery_url]
    return list(DEFAULT_START_URLS)


def _assess_candidate(url: str) -> tuple[bool, str | None]:
    """Return (is_candidate, rejection_reason)."""

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    lowered = url.lower()
    for fragment in _REJECT_SUBSTRINGS:
        if fragment in lowered:
            return False, f"reject_substring:{fragment}"
    for suffix in _REJECT_SUFFIXES:
        if lowered.endswith(suffix):
            return False, f"reject_suffix:{suffix}"
    for bad_host in _REJECT_HOST_SUBSTRINGS:
        if bad_host in host:
            return False, f"reject_host:{bad_host}"
    if not any(allowed in host for allowed in _ACCEPT_HOST_SUBSTRINGS):
        return False, "host_not_whitelisted"
    if not any(fragment in path for fragment in _ACCEPT_PATH_SUBSTRINGS):
        return False, "path_missing_keywords"
    return True, None


def url_is_candidate(url: str) -> bool:
    """Return True if the request URL matches outage feed heuristics."""

    accepted, _ = _assess_candidate(url)
    return accepted


def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a reduced header set safe for replay."""

    cleaned: dict[str, str] = {}
    for name, value in headers.items():
        lname = name.lower()
        if any(lname.startswith(prefix) for prefix in _HEADER_STRIP_PREFIXES):
            continue
        if lname in _HEADER_STRIP_NAMES:
            continue
        if lname not in _ALLOWED_HEADER_NAMES and not lname.startswith("authorization"):
            continue
        if not value:
            continue
        cleaned[name] = value
    return cleaned


def _collect_outage_keys(payload: object, limit: int = 1000) -> set[str]:
    """Traverse payload looking for known outage keys."""

    detected: set[str] = set()

    def _walk(node: object, remaining: list[int]) -> None:
        if remaining[0] <= 0 or not _OUTAGE_KEY_CANDIDATES - detected:
            return
        if isinstance(node, dict):
            lowered = {str(key).lower() for key in node.keys()}
            matches = lowered & _OUTAGE_KEY_CANDIDATES
            if matches:
                detected.update(matches)
            remaining[0] -= 1
            for value in node.values():
                _walk(value, remaining)
        elif isinstance(node, list):
            for item in node:
                if remaining[0] <= 0 or not _OUTAGE_KEY_CANDIDATES - detected:
                    break
                _walk(item, remaining)

    _walk(payload, [limit])
    return detected


def _find_outage_collection(payload: object) -> tuple[str, list[dict[str, object]]] | None:
    """Locate the primary outage collection within a payload."""

    stack: list[object] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                if lowered in _OUTAGE_COLLECTION_KEYS and isinstance(value, list):
                    dict_items = [item for item in value if isinstance(item, dict)]
                    if dict_items:
                        return lowered, dict_items
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    stack.append(item)
    return None


def extract_query_params(url: str) -> dict[str, list[str]]:
    """Parse multi-value query parameters from a URL."""

    parsed = urlparse(url)
    return {key: values for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}


def _extract_jurisdiction_tokens(query_params: dict[str, list[str]]) -> set[str]:
    """Return normalized jurisdiction values from query parameters."""

    tokens: set[str] = set()
    for key, values in query_params.items():
        if key.lower() not in {"jurisdiction", "jurisdictions"}:
            continue
        for raw_value in values:
            for piece in re.split(r",|;", raw_value):
                cleaned = piece.strip().lower()
                if cleaned:
                    tokens.add(cleaned)
    return tokens


def _template_from_override(url: str) -> DiscoveryTemplate:
    """Construct a basic template when an override URL is provided."""

    return DiscoveryTemplate(
        url=url,
        method="GET",
        headers={"accept": "application/json"},
        query=extract_query_params(url),
        body=None,
    )


def load_cached_template(cache_path: Path, start_urls: Sequence[str]) -> Optional[DiscoveryTemplate]:
    """Load a previously cached discovery template if it matches the active start URLs."""

    if not cache_path.exists():
        return None

    try:
        payload = cache_path.read_text(encoding="utf-8")
        entry = DiscoveryCacheEntry.model_validate_json(payload)
    except (OSError, ValidationError, json.JSONDecodeError):
        logger.warning(
            "discovery_cache_corrupt",
            extra={"event": "discovery_cache_corrupt", "cache_path": str(cache_path)},
        )
        return None

    expected = sorted(str(url) for url in start_urls)
    if sorted(entry.source_urls) != expected:
        logger.info(
            "discovery_cache_mismatch",
            extra={
                "event": "discovery_cache_mismatch",
                "cache_path": str(cache_path),
                "stored_sources": entry.source_urls,
                "requested_sources": expected,
            },
        )
        return None

    logger.info(
        "discovery_cache_hit",
        extra={"event": "discovery_cache_hit", "cache_path": str(cache_path)},
    )
    if not url_is_candidate(entry.template.url):
        logger.info(
            "discovery_cache_rejected",
            extra={
                "event": "discovery_cache_rejected",
                "cache_path": str(cache_path),
                "reason": "candidate_invalid",
                "url": entry.template.url,
            },
        )
        return None
    return entry.template


def save_cached_template(cache_path: Path, template: DiscoveryTemplate, start_urls: Sequence[str]) -> None:
    """Persist a discovery template to disk for reuse."""

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    entry = DiscoveryCacheEntry(
        template=template,
        retrieved_at=datetime.now(tz=timezone.utc),
        source_urls=[str(url) for url in start_urls],
    )
    cache_path.write_text(entry.model_dump_json(indent=2), encoding="utf-8")


def invalidate_cache(cache_path: Path) -> None:
    """Remove the cached discovery template if it exists."""

    try:
        cache_path.unlink()
    except FileNotFoundError:  # pragma: no cover - safe guard
        return


async def bootstrap(config: ResolvedConfig, force_refresh: bool = False) -> DiscoveryTemplate:
    """Obtain a discovery template, using the cache when possible."""

    if config.discovery_override_url:
        logger.info(
            "discovery_override_active",
            extra={
                "event": "discovery_override_active",
                "url": config.discovery_override_url,
            },
        )
        return _template_from_override(config.discovery_override_url)

    start_urls = determine_start_urls(config)
    logger.debug(
        "discovery_bootstrap_begin",
        extra={"event": "discovery_bootstrap_begin", "start_urls": start_urls, "force": force_refresh},
    )

    if not force_refresh:
        cached = load_cached_template(config.bootstrap_cache_path, start_urls)
        if cached:
            return cached

    template = await _discover_with_playwright(
        DiscoveryContext(
            start_urls=start_urls,
            timeout_s=config.playwright_timeout_s,
            cache_path=config.bootstrap_cache_path,
            jurisdiction=config.jurisdiction,
        )
    )

    save_cached_template(config.bootstrap_cache_path, template, start_urls)
    return template


@asynccontextmanager
async def _launch_browser() -> AsyncIterator[tuple[Playwright, Browser]]:
    """Async context manager that yields a ready Chromium browser."""

    async with async_playwright() as playwright:
        headless_env = os.environ.get("COLLECTOR_PLAYWRIGHT_HEADLESS")
        headless = True
        if headless_env is not None:
            headless = headless_env.lower() not in {"0", "false", "no"}
        logger.debug(
            "discovery_launch_browser",
            extra={
                "event": "discovery_launch_browser",
                "headless": headless,
            },
        )
        browser = await playwright.chromium.launch(headless=headless)
        try:
            yield playwright, browser
        finally:
            await browser.close()


async def _discover_with_playwright(context: DiscoveryContext) -> DiscoveryTemplate:
    """Use Playwright to observe network traffic and capture a template."""

    async with _launch_browser() as (_, browser):
        playwright_context = await browser.new_context()
        page = await playwright_context.new_page()
        tracker = _RequestTracker(expected_jurisdiction=context.jurisdiction)
        page.on("requestfinished", tracker.on_request_finished)
        page.on("requestfailed", tracker.on_request_failed)

        for url in context.start_urls:
            try:
                await page.goto(url, wait_until="networkidle", timeout=context.timeout_s * 1000)
                await _trigger_feed(page)
            except PlaywrightTimeoutError:
                logger.warning(
                    "discovery_navigation_timeout",
                    extra={"event": "discovery_navigation_timeout", "url": url, "timeout_s": context.timeout_s},
                )
                continue
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.warning(
                    "discovery_navigation_error",
                    extra={"event": "discovery_navigation_error", "url": url, "error": str(exc)},
                )
                continue

            try:
                template = await tracker.wait_for_template(context.timeout_s)
                logger.info(
                    "discovery_resolved",
                    extra={
                        "event": "discovery_resolved",
                        "url": template.url,
                        "method": template.method,
                        "headers": template.headers,
                        "query": template.query,
                    },
                )
                await playwright_context.close()
                return template
            except DiscoveryError:
                continue

        await playwright_context.close()
        raise DiscoveryError("Failed to discover outage feed endpoint within timeout")


async def _trigger_feed(page: Page) -> None:
    """Interact with the outage map UI to encourage the data feed to load."""

    async def _ensure_dep_selected() -> None:
        selectors = (
            "role=link[name=\"Duke Energy Progress\"]",
            "role=button[name=\"Duke Energy Progress\"]",
            "text=Duke Energy Progress",
            "role=link[name=\"View outage map\"]",
            "text=View Outage Map",
        )
        for selector in selectors:
            try:
                element = await page.wait_for_selector(selector, timeout=2000, state="visible")
                if not element:
                    continue
                await element.click()
                await page.wait_for_timeout(500)
                logger.debug(
                    "discovery_trigger_dep_selected",
                    extra={"event": "discovery_trigger_dep_selected", "selector": selector},
                )
                return
            except PlaywrightTimeoutError:
                continue
            except Exception as exc:  # pragma: no cover - best effort UI click
                logger.debug(
                    "discovery_trigger_dep_failed",
                    extra={
                        "event": "discovery_trigger_dep_failed",
                        "selector": selector,
                        "error": str(exc),
                    },
                )
        logger.debug(
            "discovery_trigger_dep_not_found",
            extra={"event": "discovery_trigger_dep_not_found"},
        )

    await _ensure_dep_selected()

    try:
        await page.wait_for_timeout(500)
        logger.debug(
            "discovery_trigger_begin",
            extra={"event": "discovery_trigger_begin"},
        )
    except Exception:  # pragma: no cover - best effort timing control
        pass

    tab_selectors = (
        "role=tab[name=\"List\"]",
        "role=tab[name=\"Table\"]",
        "text=List",
        "text=Table",
        "button:has-text(\"List\")",
        "button:has-text(\"Table\")",
        "[aria-label=\"List\"]",
    )
    for selector in tab_selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0:
                await locator.click(delay=50, timeout=1500)
                await page.wait_for_timeout(250)
                logger.debug(
                    "discovery_trigger_tab_click",
                    extra={"event": "discovery_trigger_tab_click", "selector": selector},
                )
                break
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue

    try:
        search_locator = page.locator("input[placeholder*=Search]").first
        if await search_locator.count() > 0:
            await search_locator.click(timeout=1500)
            await search_locator.fill("Raleigh", timeout=1500)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(500)
            logger.debug(
                "discovery_trigger_search",
                extra={"event": "discovery_trigger_search"},
            )
    except PlaywrightTimeoutError:
        pass
    except Exception:
        pass

    try:
        await page.mouse.move(400, 400)
        await page.mouse.down()
        await page.mouse.move(440, 360, steps=5)
        await page.mouse.up()
        await page.wait_for_timeout(200)
        await page.mouse.wheel(0, -200)
        logger.debug(
            "discovery_trigger_pan_zoom",
            extra={"event": "discovery_trigger_pan_zoom"},
        )
    except Exception:
        logger.debug(
            "discovery_trigger_pan_zoom_failed",
            extra={"event": "discovery_trigger_pan_zoom_failed"},
        )


class _RequestTracker:
    """Captures the first qualifying network request."""

    def __init__(self, expected_jurisdiction: str | None = None) -> None:
        self._event = asyncio.Event()
        self._template: Optional[DiscoveryTemplate] = None
        self._lock = asyncio.Lock()
        self._best_candidate: Optional[_CandidateRecord] = None
        self._decision_task: Optional[asyncio.Task[None]] = None
        self._seen_counter = 0
        self._expected_jurisdiction = (expected_jurisdiction or "").strip().lower() or None

    def on_request_finished(self, request: Request) -> None:
        if self._event.is_set():
            return
        asyncio.create_task(self._evaluate_request(request))

    def on_request_failed(self, request: Request) -> None:
        if self._event.is_set():
            return
        if not url_is_candidate(request.url):
            return
        logger.debug(
            "discovery_request_failed",
            extra={"event": "discovery_request_failed", "url": request.url},
        )

    async def wait_for_template(self, timeout_s: int) -> DiscoveryTemplate:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise DiscoveryError("Timed out waiting for outage feed request") from exc

        if not self._template:
            raise DiscoveryError("Candidate request was not captured")
        return self._template

    async def _finalize_after_delay(self) -> None:
        await asyncio.sleep(_CANDIDATE_DECISION_DELAY_S)
        async with self._lock:
            if self._event.is_set() or not self._best_candidate:
                if self._best_candidate and not self._template:
                    self._template = self._best_candidate.template
                    self._event.set()
                return
            self._template = self._best_candidate.template
            self._event.set()

    async def _evaluate_request(self, request: Request) -> None:
        if request.resource_type not in _ACCEPT_RESOURCE_TYPES:
            return
        accepted, reason = _assess_candidate(request.url)
        if not accepted:
            logger.debug(
                "discovery_candidate_rejected",
                extra={
                    "event": "discovery_candidate_rejected",
                    "url": request.url,
                    "reason": reason,
                    "resource_type": request.resource_type,
                },
            )
            return

        response: Optional[Response] = await request.response()
        if not response:
            return
        status = response.status
        if status >= 400:
            logger.debug(
                "discovery_request_rejected",
                extra={
                    "event": "discovery_request_rejected",
                    "url": request.url,
                    "status": status,
                    "reason": "http_error",
                },
            )
            return

        try:
            response_bytes = await response.body()
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.debug(
                "discovery_request_rejected",
                extra={
                    "event": "discovery_request_rejected",
                    "url": request.url,
                    "reason": "response_body_error",
                    "error": str(exc),
                },
            )
            return

        body_len = len(response_bytes) if response_bytes else 0
        if body_len < _MIN_BODY_BYTES:
            logger.debug(
                "discovery_request_rejected",
                extra={
                    "event": "discovery_request_rejected",
                    "url": request.url,
                    "reason": "body_too_small",
                    "body_len": body_len,
                },
            )
            return

        content_type = (response.headers or {}).get("content-type", "").lower()
        if "application/json" not in content_type:
            logger.debug(
                "discovery_request_rejected",
                extra={
                    "event": "discovery_request_rejected",
                    "url": request.url,
                    "reason": "content_type",
                    "content_type": content_type,
                },
            )
            return

        try:
            payload = json.loads(response_bytes)
        except json.JSONDecodeError:
            logger.debug(
                "discovery_request_rejected",
                extra={
                    "event": "discovery_request_rejected",
                    "url": request.url,
                    "reason": "json_decode_error",
                },
            )
            return

        outage_collection = _find_outage_collection(payload)
        if not outage_collection:
            logger.debug(
                "discovery_request_rejected",
                extra={
                    "event": "discovery_request_rejected",
                    "url": request.url,
                    "reason": "missing_outage_collection",
                },
            )
            return

        collection_name, collection_items = outage_collection

        outage_keys = _collect_outage_keys(collection_items)
        if not outage_keys:
            logger.debug(
                "discovery_request_rejected",
                extra={
                    "event": "discovery_request_rejected",
                    "url": request.url,
                    "reason": "missing_outage_keys",
                },
            )
            return

        headers = await request.all_headers()
        sanitized_headers = _sanitize_headers(headers)
        template = await _build_template_from_request(request, sanitized_headers)

        parsed = urlparse(request.url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        query_params = extract_query_params(request.url)
        jurisdiction_tokens = _extract_jurisdiction_tokens(query_params)
        has_jurisdiction = bool(jurisdiction_tokens)
        jurisdiction_match = bool(
            self._expected_jurisdiction and self._expected_jurisdiction in jurisdiction_tokens
        )

        async with self._lock:
            self._seen_counter += 1
            score = (
                1 if "prod.apigee.duke-energy.app" in host else 0,
                1 if "/outages" in path else 0,
                1 if jurisdiction_match else 0,
                1 if has_jurisdiction else 0,
                body_len,
                -self._seen_counter,
            )
            candidate = _CandidateRecord(
                template=template,
                url=request.url,
                host=host,
                path=parsed.path,
                status=status,
                content_type=content_type,
                body_len=body_len,
                outage_keys=outage_keys,
                has_jurisdiction=has_jurisdiction,
                jurisdiction_match=jurisdiction_match,
                score=score,
                seen_order=self._seen_counter,
            )

            logger.info(
                "discovery_candidate_evaluated",
                extra={
                    "event": "discovery_candidate_evaluated",
                    "url": candidate.url,
                    "method": request.method,
                    "status": candidate.status,
                    "content_type": candidate.content_type,
                    "body_len": candidate.body_len,
                    "headers": template.headers,
                    "collection": collection_name,
                    "jurisdiction_present": candidate.has_jurisdiction,
                    "jurisdiction_match": candidate.jurisdiction_match,
                    "score": list(candidate.score),
                },
            )

            replaced = False
            if self._best_candidate is None or candidate.score > self._best_candidate.score:
                self._best_candidate = candidate
                replaced = True
                logger.info(
                    "discovery_candidate_selected",
                    extra={
                        "event": "discovery_candidate_selected",
                        "url": candidate.url,
                        "host": candidate.host,
                        "path": candidate.path,
                        "status": candidate.status,
                        "content_type": candidate.content_type,
                        "body_len": candidate.body_len,
                        "outage_keys": sorted(candidate.outage_keys)[:5],
                        "jurisdiction_present": candidate.has_jurisdiction,
                        "jurisdiction_match": candidate.jurisdiction_match,
                        "headers": template.headers,
                        "collection": collection_name,
                        "score": list(candidate.score),
                    },
                )
            else:
                logger.debug(
                    "discovery_candidate_ranked_lower",
                    extra={
                        "event": "discovery_candidate_ranked_lower",
                        "url": candidate.url,
                        "score": list(candidate.score),
                    },
                )

            if replaced and self._event.is_set():
                self._template = candidate.template

            if self._decision_task is None or self._decision_task.done():
                self._decision_task = asyncio.create_task(self._finalize_after_delay())


async def _build_template_from_request(
    request: Request,
    headers: dict[str, str],
) -> DiscoveryTemplate:
    """Construct a DiscoveryTemplate from a Playwright request."""

    body: Optional[object]
    try:
        body = request.post_data_json
    except Exception:  # pragma: no cover - fallback path
        body = request.post_data

    template = DiscoveryTemplate(
        url=request.url,
        method=request.method,
        headers=headers,
        query=extract_query_params(request.url),
        body=body,
    )
    return template

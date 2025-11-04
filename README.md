# Duke Scraper Toolkit

## Overview

The Duke Scraper Toolkit combines an automated outage collection service with
energy resilience analysis notebooks used by the City of Enfield power project.
The `outage-collector` package discovers Duke Energy outage feeds with
Playwright, schedules recurring polls, normalizes events, and exposes an
operations FastAPI surface while persisting CSV/GeoJSON exports.【F:outage-collector/README.md†L1-L37】【F:outage-collector/collector/__main__.py†L1-L49】【F:outage-collector/collector/scheduler.py†L1-L85】【F:outage-collector/collector/storage.py†L1-L117】【F:outage-collector/collector/service.py†L1-L129】
The `power-usage` folder contains sensitivity studies and an optimization demo
for distributed solar + storage microgrids that underpin planning discussions
around critical load support.【F:power-usage/Power_usage_summary.py†L1-L106】【F:power-usage/nrel_api_demo.py†L1-L200】

## Repository structure

| Path | Description |
| ---- | ----------- |
| `outage-collector/` | Python package that handles discovery, polling, filtering, and export of Duke outage events together with a FastAPI operations plane.【F:outage-collector/README.md†L1-L37】 |
| `power-usage/` | Analysis scripts for PV/battery sensitivity sweeps and an NREL PVWatts-backed dispatch optimizer to explore microgrid performance.【F:power-usage/Power_usage_summary.py†L1-L106】【F:power-usage/nrel_api_demo.py†L1-L200】 |
| `requirements.txt` | Consolidated dependency pin set used during local development and CI for both components (Python 3.11+).【F:requirements.txt†L1-L57】【F:outage-collector/pyproject.toml†L6-L36】 |

## Getting started

1. **Create a virtual environment** targeting Python 3.11 or newer as required by
   the outage collector package.【F:outage-collector/pyproject.toml†L6-L36】【F:outage-collector/README.md†L11-L16】
2. **Install dependencies** from the pinned requirements file and download the
   Playwright browsers once per machine.【F:outage-collector/README.md†L18-L23】【F:requirements.txt†L1-L57】
3. **Enter the outage collector directory** when you want to run or develop the
   service and use the helper PowerShell script (or invoke the module with
   `python -m outage_collector`) to launch it.【F:outage-collector/README.md†L25-L37】【F:outage-collector/collector/__main__.py†L52-L61】
4. **Run the FastAPI operations plane** and polling loop; the helper script
   defaults to the Raleigh preset, writes exports to `outage-collector/data/`,
   and serves on port 8000.【F:outage-collector/README.md†L31-L37】【F:outage-collector/collector/scheduler.py†L22-L85】

> **Tip:** Playwright needs system dependencies for headless Chromium/Firefox on
> Linux. Consult the [Playwright installation docs](https://playwright.dev)
> if the bootstrap step reports missing libraries.

## Outage collector service

### Configuration model

Runtime configuration blends CLI flags, `.env` overrides, and place presets. The
collector ships sensible defaults for polling interval, export format, bounding
boxes, logging levels, and discovery cache locations, while allowing overrides
for jurisdiction, radius padding, viewport filtering, and manual discovery
endpoints.【F:outage-collector/collector/config.py†L14-L181】 Presets live in
`presets/places.yml`; copy the Raleigh entry and adjust geometry plus optional
`query_params` for new territories before launching with `--place <name>`.【F:outage-collector/README.md†L39-L47】【F:outage-collector/presets/places.yml†L1-L20】

### Discovery and polling pipeline

During startup the service resolves configuration, attaches runtime state, and
initialises a recurring APScheduler job that polls the outage feed at the
requested interval.【F:outage-collector/collector/__main__.py†L17-L49】【F:outage-collector/collector/scheduler.py†L69-L85】
Playwright-driven discovery captures authenticated request templates for Duke's
outage APIs, caching headers, query parameters, and jurisdiction tokens for use
by the poller.【F:outage-collector/collector/discovery.py†L23-L120】 Each poll
performs robust HTTP requests, merges preset query parameters, gracefully
retries on authentication failures by re-running discovery, and filters events
using bounding boxes, optional radius limits, and viewport polygons before
handing them to downstream normalization.【F:outage-collector/collector/poller.py†L20-L237】 Normalized events are enriched, exported as CSV/GeoJSON, and recorded in the
service state for API consumers and observability.【F:outage-collector/collector/scheduler.py†L22-L60】【F:outage-collector/collector/storage.py†L14-L109】【F:outage-collector/collector/service.py†L15-L45】

### Operations API

The FastAPI app exposes health, stats, latest events, and configuration snapshots
backed by the in-memory `ServiceState`. These endpoints provide structured data
for dashboards or alerting while surfacing the most recent exports and poll
errors when present.【F:outage-collector/collector/service.py†L12-L129】 Start the
service and visit `http://localhost:8000/docs` to explore the OpenAPI UI.

## Power usage analyses

The `power-usage` scripts offer quick-turn exploration of microgrid scenarios:

- `Power_usage_summary.py` builds deterministic PV and stochastic load profiles,
  runs a greedy dispatch heuristic across PV, battery, and critical load sweeps,
  and writes a CSV summary for comparison.【F:power-usage/Power_usage_summary.py†L1-L106】
- `nrel_api_demo.py` optionally downloads PV generation from the NREL PVWatts
  API, formulates a convex optimization in CVXPY to minimise unmet load, and can
  fall back to synthetic PV/load profiles for exploratory analysis.【F:power-usage/nrel_api_demo.py†L1-L200】

Install the shared requirements, execute the scripts with Python, and inspect
the generated CSV outputs or plots to assess microgrid performance assumptions.
Set the `NREL_API_KEY` environment variable when you want live PVWatts data.【F:power-usage/nrel_api_demo.py†L39-L117】

## Testing and quality checks

Run the outage collector test suite with `pytest` from inside the
`outage-collector` directory once your virtual environment is active.【F:outage-collector/README.md†L49-L55】 The
project also includes a development extras group with formatting and linting
tools (Black, Ruff, pytest) that can be installed via `pip install
.[dev]`.【F:outage-collector/pyproject.toml†L23-L37】


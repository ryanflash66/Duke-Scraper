# Duke Scraper Toolkit

## Overview

The Duke Scraper Toolkit combines an automated outage collection service with
energy resilience analysis notebooks used by the City of Enfield power project.
The `Data Scraping/outage-collector` package discovers Duke Energy outage feeds with
Playwright, schedules recurring polls, normalizes events, and exposes an
operations FastAPI surface while persisting CSV/GeoJSON exports. The
`Data Scraping/power-usage` folder contains sensitivity studies and an
optimization demo for distributed solar + storage microgrids that underpin
planning discussions around critical load support.

## Repository structure

| Path | Description |
| ---- | ----------- |
| `Data Scraping/outage-collector/` | Python package that handles discovery, polling, filtering, and export of Duke outage events together with a FastAPI operations plane. |
| `Data Scraping/power-usage/` | Analysis scripts for PV/battery sensitivity sweeps and an NREL PVWatts-backed dispatch optimizer to explore microgrid performance. |
| `Prediction Models/` | Notebooks, training scripts, and evaluation artifacts for the Enfield load forecasting models. |
| `requirements.txt` | Consolidated dependency pin set used during local development and CI for both components (Python 3.11+). |

## Getting started

1. **Create a virtual environment** targeting Python 3.11 or newer as required by
   the outage collector package.
2. **Install dependencies** from the pinned requirements file and download the
   Playwright browsers once per machine:
   ```powershell
   pip install -r requirements.txt
   python -m playwright install
   ```
3. **Enter the outage collector directory** when you want to run or develop the
   service. From the repo root:
   ```powershell
   Set-Location ".\Data Scraping\outage-collector"
   ```
   Use the helper PowerShell script (or invoke the module with `python -m collector` —
   the module name is `collector` even though the project folder is `outage-collector`)
   to launch it.
4. **Run the FastAPI operations plane** and polling loop; the helper script
   defaults to the Raleigh preset, writes exports to `data/`, and serves on port 8000.

> **Tip:** Playwright needs system dependencies for headless Chromium/Firefox on
> Linux. Consult the [Playwright installation docs](https://playwright.dev)
> if the bootstrap step reports missing libraries.

## Outage collector service

### Configuration model

Runtime configuration blends CLI flags, `.env` overrides, and place presets. The
collector ships sensible defaults for polling interval, export format, bounding
boxes, logging levels, and discovery cache locations, while allowing overrides
for jurisdiction, radius padding, viewport filtering, and manual discovery
endpoints. Presets live in `presets/places.yml`; copy the Raleigh entry and
adjust geometry plus optional `query_params` for new territories before
launching with `--place <name>`.

### Discovery and polling pipeline

During startup the service resolves configuration, attaches runtime state, and
initialises a recurring APScheduler job that polls the outage feed at the
requested interval. Playwright-driven discovery captures authenticated request
templates for Duke's outage APIs, caching headers, query parameters, and
jurisdiction tokens for use by the poller. Each poll performs robust HTTP
requests, merges preset query parameters, gracefully retries on authentication
failures by re-running discovery, and filters events using bounding boxes,
optional radius limits, and viewport polygons before handing them to downstream
normalization. Normalized events are enriched, exported as CSV/GeoJSON, and
recorded in the service state for API consumers and observability.

### Operations API

The FastAPI app exposes health, stats, latest events, and configuration snapshots
backed by the in-memory `ServiceState`. These endpoints provide structured data
for dashboards or alerting while surfacing the most recent exports and poll
errors when present. Start the service and visit `http://localhost:8000/docs` to
explore the OpenAPI UI.

## Power usage analyses

The `Data Scraping/power-usage` scripts offer quick-turn exploration of microgrid
scenarios:

- `Power_usage_summary.py` builds deterministic PV and stochastic load profiles,
  runs a greedy dispatch heuristic across PV, battery, and critical load sweeps,
  and writes a CSV summary for comparison.
- `nrel_api_demo.py` optionally downloads PV generation from the NREL PVWatts
  API, formulates a convex optimization in CVXPY to minimise unmet load, and can
  fall back to synthetic PV/load profiles for exploratory analysis.

Install the shared requirements, execute the scripts with Python, and inspect
the generated CSV outputs or plots to assess microgrid performance assumptions.
Set the `NREL_API_KEY` environment variable when you want live PVWatts data.

## Testing and quality checks

Run the outage collector test suite with `pytest` from inside the
`Data Scraping/outage-collector` directory once your virtual environment is active:

```powershell
Set-Location ".\Data Scraping\outage-collector"
pytest
```

The project also includes a development extras group with formatting and linting
tools (Black, Ruff, pytest) that can be installed via `pip install .[dev]` inside
the outage collector directory.

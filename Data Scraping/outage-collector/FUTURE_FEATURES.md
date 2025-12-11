# Future Features Roadmap

This document collects the forward-looking work that extends the outage collector beyond the current enrichment release. Items are grouped by theme, and each entry highlights the intended outcome, key design notes, and open questions or dependencies.

## Multi-place data orchestration

- **Coordinated polling for multiple service territories**  
  Outcome: capture outages for a list of Duke service areas (e.g., Raleigh, Charlotte, Greenville) within a single run.  
  Implementation notes:

  - Reuse the existing `collector` CLI but accept a manifest of places (`presets/` JSON or a YAML schedule) so APScheduler can loop through them.
  - Serial execution is fine initially; parallelism can wait until Playwright concurrency limits are validated.  
    Open questions: how frequently can we poll each territory without tripping Duke's rate limits?

- **Shared output layout per place**  
  Outcome: keep each place's CSV/GeoJSON in a predictable folder structure so downstream jobs can ingest them easily.  
  Implementation notes:
  - Adopt `data/<place>/<yyyy-mm-dd>/` as the canonical layout.
  - Ensure the CLI and scheduler create folders on demand and cleanly log the paths.
  - Document the layout in `RUNBOOK.md` once finalized.

## Excel aggregation deliverable

- **Workbook exporter after each polling cycle**  
  Outcome: one `.xlsx` workbook combining the latest normalized outages for all polled places, with individual worksheets and a summary tab.  
  Implementation notes:

  - Preferred tooling: `openpyxl` (lightweight) or `pandas` if tabular transformations grow. (User Remark: Use pandas)
  - Summary tab should include totals by cause code, customer counts, and the most recent restoration estimates per place.
  - Trigger the exporter from the scheduler immediately after CSV writes succeed.  
    Open questions: where should the workbook live (`data/aggregates/` vs. `reports/`), and do we need historical versions or just the latest snapshot?

- **Validation hooks**  
  Outcome: confidence that the workbook stays in sync with CSV/GeoJSON exports.  
  Implementation notes:
  - Add unit tests around the exporter to confirm sheet naming, required columns, and numeric aggregations.
  - Consider a quick smoke test that opens the workbook and verifies row counts for a tiny fixture dataset.

## Data retention and archival

- **Rolling archive rotation**  
  Outcome: limit hot storage footprint while preserving historical snapshots.  
  Implementation notes:

  - Nightly job moves yesterdays folders into `archive/<yyyy>/<mm>/<dd>.zip`.
  - Keep the latest `N` days (configurable) unarchived for quick access.  
    Dependencies: ensure Excel exporter reads only from the active (non-archived) folders.

- **Metadata manifest**  
  Outcome: quick traceability for where source data originated and when it was captured.  
  Implementation notes:
  - Generate a `manifest.json` alongside each archive bundle containing poll window, cause code coverage, and record counts.
  - Update `RUNBOOK.md` with restore procedures that rely on the manifest.

## Operational visibility

- **FastAPI ops plane**  
  Outcome: surface health endpoints (last poll, next scheduled run, row counts) to operators.  
  Implementation notes:

  - Extend the existing FastAPI skeleton to expose read-only metrics.
  - Wire APScheduler events to push status updates into a lightweight store (SQLite or in-memory cache).

- **Alerting and dashboards**  
  Outcome: early warning when polls fail or outage volumes spike unexpectedly.  
  Implementation notes:
  - Introduce structured logging fields (`place`, `cause_code`, `customer_count`) for ingestion into tooling such as Azure Monitor or Grafana.
  - Define threshold-based alerts (e.g., no outages captured for a place during prime hours) once baseline data is available.

## Documentation follow-ons

- Update `RUNBOOK.md` once the folder layout and archival process are finalized.
- Provide end-user guidance in `README.md` for invoking the Excel exporter and understanding the aggregated workbook.
- Capture any new environment variables (e.g., archive retention days, workbook output path) in `.env.example`.

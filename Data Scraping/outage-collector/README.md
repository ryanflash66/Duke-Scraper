# Outage Collector

> Automated service that discovers and polls Duke Energy outage feeds, filters to Raleigh, and ships CSV/GeoJSON exports with an operational FastAPI plane.

## Status

🚧 Work in progress. Implementation is being developed incrementally.

## Quick Start

1. Create and activate a virtual environment (PowerShell example):

   ```powershell
   python -m venv pwroutage
   .\pwroutage\Scripts\Activate.ps1
   ```

2. Install dependencies and Playwright browsers:

   ```powershell
   pip install -r requirements.txt
   python -m playwright install
   ```

3. Move into the project directory (from the repo root):

   ```powershell
   Set-Location ".\Data Scraping\outage-collector"
   ```

4. Launch the collector via the helper script (override parameters as needed):

   ```powershell
   .\run_collector.ps1
   ```

   The script defaults to the Raleigh preset, writes CSV and GeoJSON to `data/`, and starts the FastAPI ops plane on port 8000. Pass flags such as `-Place charlotte -Format csv -LogLevel INFO` to customise a run or append extra arguments (e.g. `--poll-interval-s 60`).

## Place presets and query parameters

Place-specific configuration lives in `presets/places.yml`. Each preset now supports an optional `query_params` block, allowing custom query string values (e.g., additional filters) to be merged into the polling request while the service still enforces the correct jurisdiction token.

To add a new territory:

1. Copy the existing Raleigh entry and adjust the geometry metadata.
2. Add/modify `query_params` entries as needed (values can be scalars or lists).
3. Invoke the collector with `--place <name>` to load your new preset.

## Testing

Run the test suite (from the repository root) after making changes:

```powershell
Set-Location ".\Data Scraping\outage-collector"
..\pwroutage\Scripts\python.exe -m pytest
```

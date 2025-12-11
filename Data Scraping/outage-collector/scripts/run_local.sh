#!/usr/bin/env bash
set -euo pipefail

# TODO: flesh out local run orchestration
python -m playwright install || true
uvicorn collector.service:app --reload --port 8080

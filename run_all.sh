#!/usr/bin/env bash
# AML-GraphAlert — run the full pipeline end-to-end.
# Usage: bash run_all.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

echo "Running pipeline..."
.venv/bin/python -m src.run_pipeline

echo ""
echo "Running tests..."
.venv/bin/python -m pytest tests/ -q

echo ""
echo "Done. See outputs/ (alerts_ranked.csv, metrics.json, suspicious_subgraph.html)."

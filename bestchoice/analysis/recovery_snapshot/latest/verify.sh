#!/usr/bin/env bash
set -euo pipefail
cd '/Users/dp/Documents/M/stock/bestchoice'
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check

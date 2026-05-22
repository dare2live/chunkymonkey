#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== BestChoice recovery checkpoint ==="
python scripts/workflow_checkpoint.py --brief

echo
echo "=== External GCP watch ==="
echo "Skipped: BestChoice recovery is local-only unless the user explicitly authorizes GCP in the current conversation."

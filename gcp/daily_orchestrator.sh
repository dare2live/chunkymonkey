#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-YOUR_PROJECT_ID}"
BUCKET="${BUCKET:-YOUR_BUCKET_NAME}"
PREFIX="${PREFIX:-chunkymonkey}"
REGION="${REGION:-us-central1}"
MAX_JOBS="${MAX_JOBS:-1}"
MAX_SUBMIT_JOBS="${MAX_SUBMIT_JOBS:-50}"
BATCH_ID="${BATCH_ID:-daily_$(date -u +%Y%m%dT%H%M%SZ)}"

export PROJECT_ID BUCKET PREFIX REGION MAX_SUBMIT_JOBS

echo "Uploading current local DuckDB snapshot"
SNAPSHOT_ID="${BATCH_ID}" \
PROJECT_ID="${PROJECT_ID}" \
BUCKET="${BUCKET}" \
PREFIX="${PREFIX}" \
gcp/sync_data_to_gcs.sh

echo "Generate Batch job configs"
python gcp/generate_jobs.py \
  --config gcp/experiment_config.yaml \
  --batch-id "${BATCH_ID}" \
  --max-jobs "${MAX_JOBS}"

echo "Submit Batch jobs"
gcp/submit_jobs.sh "${BATCH_ID}"

echo "Daily orchestration submitted: ${BATCH_ID}"
echo "Pull results later with:"
echo "python gcp/pull_results_to_duckdb.py --results-uri gs://${BUCKET}/${PREFIX}/results/${BATCH_ID} --db data/smartmoney.duckdb"

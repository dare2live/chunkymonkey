# ChunkyMonkey GCP Batch Orchestration

This directory contains a strict-PIT Cloud Batch template for heavyweight experiments.

## Recommended Architecture

Use Cloud Batch plus GCS for experiment bursts, with the Mac mini remaining the daily orchestrator. Do not keep a standing experiment cluster. Each job downloads immutable DuckDB snapshots from GCS to VM local ephemeral disk, runs one isolated experiment, uploads result files, and exits.

## Required Placeholders

Replace these values before running:

- `YOUR_PROJECT_ID`
- `YOUR_BUCKET_NAME`
- `BILLING_ACCOUNT_ID`
- `YOUR_EMAIL@example.com`

## One Job Smoke Test

```bash
cd /Users/dp/Documents/M/stock/chunkymonkey

python3 -m venv .venv-gcp
source .venv-gcp/bin/activate
pip install -r gcp/requirements-gcp.txt

export PROJECT_ID=YOUR_PROJECT_ID
export BUCKET=YOUR_BUCKET_NAME
export PREFIX=chunkymonkey
export REGION=us-central1

gcp/sync_data_to_gcs.sh

python gcp/generate_jobs.py \
  --config gcp/experiment_config.yaml \
  --batch-id smoke_$(date -u +%Y%m%dT%H%M%SZ) \
  --max-jobs 1
```

Then submit the generated batch id:

```bash
export BATCH_ID=PASTE_BATCH_ID
gcp/submit_jobs.sh "${BATCH_ID}"
```

## Pull Results

```bash
python gcp/pull_results_to_duckdb.py \
  --results-uri gs://YOUR_BUCKET_NAME/chunkymonkey/results/PASTE_BATCH_ID \
  --db data/smartmoney.duckdb
```

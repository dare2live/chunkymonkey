# ChunkyMonkey GCP

Two paths depending on goal:

| 路径 | Setup 时间 | 并行支持 | 适合 |
|---|---|---|---|
| **A. SSH 单 VM (setup_ssh_vm.sh)** | 30 min | no | 单 Optuna 加速 (当前急需) |
| **B. Cloud Batch + GCS (setup_all.sh)** | 1-2h | yes (N parallel) | 多 experiment 并行探索 (用户 vision) |

## Path A: SSH + Single VM (no Docker, simpler)

```bash
# Prerequisite (用户一次):
brew install --cask google-cloud-sdk
gcloud auth login
# Enable billing for project (GCP Console 1-click)

# 一键 setup VM:
gcp/setup_ssh_vm.sh PROJECT_ID [BUCKET_NAME] [REGION]

# Script 会:
#  1. 验证 gcloud auth
#  2. enable Compute + Storage APIs
#  3. 创建 GCE VM (n2-standard-32, 32 vCPU, 128GB RAM, spot)
#  4. (optional) 创建 GCS bucket
#  5. 打印 next steps (upload data + SSH + run Optuna)

# 跑完 Optuna 后:
gcloud compute instances stop chunkymonkey-optuna --zone us-central1-a
# 或 delete (省钱):
gcloud compute instances delete chunkymonkey-optuna --zone us-central1-a
```

成本估算: spot n2-standard-32 ~$0.45/h × 6h ≈ **$3** + storage $0.5/月

## Path B: Cloud Batch + GCS (Docker, parallel exploration)

### Prerequisite (Mac local, 一次性)

```bash
# 1. Install Docker Desktop for Mac (used to build cloud image)
brew install --cask docker
open -a Docker  # Start Docker

# 2. Install gcloud CLI
brew install --cask google-cloud-sdk

# 3. Login + select project
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

### One-shot Setup

After prerequisites + enable billing for project (GUI 1-click), run:

```bash
gcp/setup_all.sh PROJECT_ID BUCKET_NAME REGION EMAIL
```

例:
```bash
gcp/setup_all.sh chunkymonkey-2026 chunkymonkey-dp-2026 us-central1 dp@example.com
```

This single script does steps 1-6: verify auth, enable APIs, create GCS bucket + Artifact Registry + service account + IAM roles, replace all placeholders. Output tells you steps 7-10 (Docker build, data upload, smoke job, pull results).

---

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

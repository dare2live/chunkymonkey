#!/usr/bin/env bash
# Simplified GCP setup: one big VM, no Docker, no Batch.
#
# Usage:
#   gcp/setup_ssh_vm.sh PROJECT_ID [BUCKET_NAME] [REGION]
#
# Defaults:
#   BUCKET_NAME: chunkymonkey-data (or pass explicitly)
#   REGION: us-central1
#
# What this does:
#   1. Verify gcloud auth + set project
#   2. Enable Compute + Storage APIs (no Batch/Artifact/IAM SA needed)
#   3. Create GCE VM: n2-standard-32 (32 vCPU, 128GB RAM, 100GB disk, spot pricing)
#   4. (optional) Create GCS bucket if not exists
#   5. Show how to upload data + SSH

set -euo pipefail

source "$(cd "$(dirname "$0")/.." && pwd)/scripts/lib/gcp_guard.sh"
require_gcp_explicit_ok "gcp/setup_ssh_vm.sh"

if [ $# -lt 1 ]; then
    echo "Usage: $0 PROJECT_ID [BUCKET_NAME] [REGION]"
    echo
    echo "Example:"
    echo "  $0 my-project-id chunkymonkey-data us-central1"
    exit 1
fi

PROJECT_ID="$1"
BUCKET_NAME="${2:-chunkymonkey-data-$(date +%Y%m%d)}"
REGION="${3:-us-central1}"
ZONE="${REGION}-a"
VM_NAME="chunkymonkey-optuna"
MACHINE_TYPE="n2-standard-32"  # 32 vCPU, 128GB RAM
DISK_SIZE="100GB"

echo "================================================"
echo "Simplified GCP setup (SSH + venv, no Docker)"
echo "  PROJECT_ID: ${PROJECT_ID}"
echo "  BUCKET:     gs://${BUCKET_NAME}"
echo "  REGION/ZONE: ${REGION} / ${ZONE}"
echo "  VM:         ${VM_NAME} (${MACHINE_TYPE}, ${DISK_SIZE} disk, spot)"
echo "================================================"
echo

cd "$(dirname "$0")/.."

# Step 1: verify auth
CURRENT_USER=$(gcloud config get-value account 2>/dev/null || echo "")
if [ -z "${CURRENT_USER}" ]; then
    echo "ERROR: gcloud not authenticated. Run: gcloud auth login"
    exit 2
fi
echo "[1/5] gcloud user: ${CURRENT_USER}"
gcloud config set project "${PROJECT_ID}"

# Step 2: enable APIs
echo
echo "[2/5] Enable Compute + Storage APIs"
gcloud services enable compute.googleapis.com storage.googleapis.com --project "${PROJECT_ID}" --quiet

# Step 3: create VM (spot for cost saving)
echo
echo "[3/5] Create GCE VM ${VM_NAME} ..."
if gcloud compute instances describe "${VM_NAME}" --zone "${ZONE}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    echo "  VM exists, skipping create"
else
    gcloud compute instances create "${VM_NAME}" \
        --zone="${ZONE}" \
        --machine-type="${MACHINE_TYPE}" \
        --image-family=debian-12 --image-project=debian-cloud \
        --boot-disk-size="${DISK_SIZE}" \
        --provisioning-model=SPOT \
        --instance-termination-action=STOP \
        --no-restart-on-failure \
        --metadata=enable-oslogin=TRUE \
        --quiet
    echo "  VM created (SPOT)"
fi

# Step 4: optional GCS bucket
echo
echo "[4/5] Create GCS bucket gs://${BUCKET_NAME} (for data upload, optional)"
if gsutil ls "gs://${BUCKET_NAME}" >/dev/null 2>&1; then
    echo "  bucket exists, skipping"
else
    gsutil mb -p "${PROJECT_ID}" -l "${REGION}" "gs://${BUCKET_NAME}"
    echo "  bucket created"
fi

# Step 5: next steps
echo
echo "[5/5] Next steps (用户跑 OR 我接管):"
echo
echo "  # Upload 24GB data to GCS (~30 min):"
echo "  gsutil -m cp data/smartmoney.duckdb gs://${BUCKET_NAME}/"
echo "  gsutil -m cp data/alpha158.duckdb gs://${BUCKET_NAME}/"
echo "  gsutil -m cp data/market.duckdb gs://${BUCKET_NAME}/"
echo
echo "  # SSH into VM:"
echo "  gcloud compute ssh ${VM_NAME} --zone ${ZONE}"
echo
echo "  # On VM (setup Python env):"
echo "  sudo apt-get update && sudo apt-get install -y python3-pip python3-venv git"
echo "  git clone <repo-url> chunkymonkey && cd chunkymonkey"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r backend/requirements.txt"
echo
echo "  # Download data:"
echo "  mkdir -p data"
echo "  gsutil -m cp gs://${BUCKET_NAME}/*.duckdb data/"
echo
echo "  # Run Optuna v4 (32 cores, should be ~5-8h):"
echo "  PYTHONPATH=backend python backend/scripts/run_p0b_lightgbm_optuna_v4.py \\"
echo "      --label fwd_cost_after_20d --n-trials 50 --full \\"
echo "      --start-date 2024-01-01 --end-date 2026-04-13 --min-train-months 12 \\"
echo "      --feature-panel mart_p0a_feature_label_panel_v4"
echo
echo "  # When done, download results back:"
echo "  gsutil cp data/smartmoney.duckdb gs://${BUCKET_NAME}/smartmoney_v4_done.duckdb"
echo
echo "  # SHUT DOWN VM when done (省钱):"
echo "  gcloud compute instances stop ${VM_NAME} --zone ${ZONE}"
echo "  # 或 delete:"
echo "  gcloud compute instances delete ${VM_NAME} --zone ${ZONE}"
echo
echo "Cost estimate: spot n2-standard-32 ~\$0.45/h × 6h = ~\$3 + storage \$0.5/month"

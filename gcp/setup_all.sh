#!/usr/bin/env bash
# One-shot GCP setup for ChunkyMonkey Batch + GCS (Codex round 22).
#
# Usage:
#   gcp/setup_all.sh PROJECT_ID BUCKET_NAME REGION EMAIL
#
# Example:
#   gcp/setup_all.sh chunkymonkey-2026 chunkymonkey-dp-2026 us-central1 dp@example.com
#
# Prerequisite:
#   1. gcloud auth login (user-side, browser)
#   2. gcloud config set project <PROJECT_ID>
#   3. enable billing for project (GCP Console GUI 1-click)
#
# What this script does:
#   1. Verify gcloud auth + project
#   2. Enable required APIs (Cloud Batch / Compute / Artifact Registry / IAM / Storage)
#   3. Create GCS bucket
#   4. Create Artifact Registry Docker repo
#   5. Create service account + grant IAM roles
#   6. Update gcp/experiment_config.yaml + gcp/sync_data_to_gcs.sh + gcp/submit_jobs.sh placeholders
#   7. Report next steps (Docker build + sync data + smoke job)

set -euo pipefail

source "$(cd "$(dirname "$0")/.." && pwd)/scripts/lib/gcp_guard.sh"
require_gcp_explicit_ok "gcp/setup_all.sh"

if [ $# -lt 4 ]; then
    echo "Usage: $0 PROJECT_ID BUCKET_NAME REGION EMAIL"
    echo
    echo "Args:"
    echo "  PROJECT_ID    GCP project ID (gcloud config get-value project)"
    echo "  BUCKET_NAME   GCS bucket name (must be globally unique, e.g. chunkymonkey-dp-2026)"
    echo "  REGION        GCE region (recommended: us-central1)"
    echo "  EMAIL         Your GCP account email"
    exit 1
fi

PROJECT_ID="$1"
BUCKET_NAME="$2"
REGION="$3"
EMAIL="$4"
PREFIX="chunkymonkey"
REPO_NAME="chunkymonkey"
SA_NAME="chunkymonkey-batch"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

cd "$(dirname "$0")/.."

echo "================================================"
echo "ChunkyMonkey GCP setup"
echo "  PROJECT_ID: ${PROJECT_ID}"
echo "  BUCKET:     gs://${BUCKET_NAME}"
echo "  REGION:     ${REGION}"
echo "  EMAIL:      ${EMAIL}"
echo "  SA:         ${SA_EMAIL}"
echo "================================================"
echo

# Step 1: verify gcloud auth
echo "[1/7] Verify gcloud auth + project ..."
CURRENT_USER=$(gcloud config get-value account 2>/dev/null || echo "")
if [ -z "${CURRENT_USER}" ]; then
    echo "ERROR: gcloud not authenticated. Run: gcloud auth login"
    exit 2
fi
echo "  current user: ${CURRENT_USER}"
gcloud config set project "${PROJECT_ID}"
echo "  project set: ${PROJECT_ID}"
echo

# Step 2: enable APIs
echo "[2/7] Enable required APIs ..."
APIS=(
    batch.googleapis.com
    compute.googleapis.com
    artifactregistry.googleapis.com
    iam.googleapis.com
    storage.googleapis.com
    cloudbuild.googleapis.com
    logging.googleapis.com
)
for api in "${APIS[@]}"; do
    echo "  enabling ${api} ..."
    gcloud services enable "${api}" --project "${PROJECT_ID}" --quiet
done
echo "  all APIs enabled"
echo

# Step 3: create GCS bucket
echo "[3/7] Create GCS bucket gs://${BUCKET_NAME} ..."
if gsutil ls "gs://${BUCKET_NAME}" >/dev/null 2>&1; then
    echo "  bucket already exists, skip"
else
    gsutil mb -p "${PROJECT_ID}" -l "${REGION}" "gs://${BUCKET_NAME}"
    # versioning on for snapshot immutability
    gsutil versioning set on "gs://${BUCKET_NAME}"
    echo "  bucket created + versioning on"
fi
echo

# Step 4: create Artifact Registry Docker repo
echo "[4/7] Create Artifact Registry ${REPO_NAME} ..."
if gcloud artifacts repositories describe "${REPO_NAME}" \
        --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    echo "  repo already exists, skip"
else
    gcloud artifacts repositories create "${REPO_NAME}" \
        --repository-format=docker \
        --location="${REGION}" \
        --project="${PROJECT_ID}" \
        --quiet
    echo "  repo created"
fi
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
echo

# Step 5: create service account + grant roles
echo "[5/7] Create service account ${SA_EMAIL} ..."
if gcloud iam service-accounts describe "${SA_EMAIL}" \
        --project "${PROJECT_ID}" >/dev/null 2>&1; then
    echo "  service account exists, skip create"
else
    gcloud iam service-accounts create "${SA_NAME}" \
        --display-name="ChunkyMonkey Batch Runner" \
        --project="${PROJECT_ID}" \
        --quiet
fi
ROLES=(
    roles/batch.jobsEditor
    roles/storage.objectAdmin
    roles/artifactregistry.reader
    roles/logging.logWriter
    roles/compute.instanceAdmin.v1
    roles/iam.serviceAccountUser
)
for role in "${ROLES[@]}"; do
    echo "  grant ${role} ..."
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${role}" \
        --quiet >/dev/null
done
echo "  IAM bindings done"
echo

# Step 6: update placeholders in scripts/config
echo "[6/7] Update placeholders in gcp/* ..."
PLACEHOLDERS=(
    "YOUR_PROJECT_ID:${PROJECT_ID}"
    "YOUR_BUCKET_NAME:${BUCKET_NAME}"
    "YOUR_EMAIL@example.com:${EMAIL}"
)
for f in gcp/experiment_config.yaml gcp/sync_data_to_gcs.sh gcp/submit_jobs.sh gcp/daily_orchestrator.sh; do
    [ -f "$f" ] || continue
    for kv in "${PLACEHOLDERS[@]}"; do
        old="${kv%%:*}"
        new="${kv##*:}"
        sed -i.bak "s|${old}|${new}|g" "$f"
        rm -f "${f}.bak"
    done
done
echo "  placeholders replaced"
echo

# Step 7: report
echo "[7/7] Setup complete. Next steps:"
echo
echo "  1. Build + push Docker image (~20-30 min):"
echo "       cd $(pwd)"
echo "       docker build -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/chunkymonkey-gcp:latest -f gcp/Dockerfile ."
echo "       docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/chunkymonkey-gcp:latest"
echo
echo "  2. Upload 24GB data to GCS (~30 min - 2h depending on upload speed):"
echo "       gcp/sync_data_to_gcs.sh"
echo
echo "  3. Generate smoke job + submit:"
echo "       python gcp/generate_jobs.py --config gcp/experiment_config.yaml --batch-id smoke_$(date -u +%Y%m%dT%H%M%SZ) --max-jobs 1"
echo "       gcp/submit_jobs.sh <BATCH_ID>"
echo
echo "  4. Pull results back to local:"
echo "       python gcp/pull_results_to_duckdb.py --bucket gs://${BUCKET_NAME}/${PREFIX}/results"
echo
echo "All values used:"
echo "  PROJECT_ID=${PROJECT_ID}"
echo "  BUCKET=gs://${BUCKET_NAME}"
echo "  REGION=${REGION}"
echo "  SA=${SA_EMAIL}"
echo "  IMAGE=${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/chunkymonkey-gcp:latest"

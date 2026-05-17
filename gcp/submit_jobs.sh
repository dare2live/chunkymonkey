#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: PROJECT_ID=YOUR_PROJECT_ID BUCKET=YOUR_BUCKET_NAME gcp/submit_jobs.sh BATCH_ID" >&2
  exit 2
fi

BATCH_ID="$1"
PROJECT_ID="${PROJECT_ID:-YOUR_PROJECT_ID}"
BUCKET="${BUCKET:-YOUR_BUCKET_NAME}"
PREFIX="${PREFIX:-chunkymonkey}"
REGION="${REGION:-us-central1}"
JOBS_DIR="${JOBS_DIR:-gcp/jobs/${BATCH_ID}}"
MAX_SUBMIT_JOBS="${MAX_SUBMIT_JOBS:-50}"
SUBMIT_SLEEP_SECONDS="${SUBMIT_SLEEP_SECONDS:-1}"

if [[ ! -d "${JOBS_DIR}" ]]; then
  echo "Missing jobs directory: ${JOBS_DIR}" >&2
  exit 2
fi

job_count="$(find "${JOBS_DIR}" -name '*.batch.json' -type f | wc -l | tr -d ' ')"
if [[ "${job_count}" -eq 0 ]]; then
  echo "No .batch.json files found in ${JOBS_DIR}" >&2
  exit 2
fi
if [[ "${job_count}" -gt "${MAX_SUBMIT_JOBS}" ]]; then
  echo "Refusing to submit ${job_count} jobs because MAX_SUBMIT_JOBS=${MAX_SUBMIT_JOBS}" >&2
  exit 3
fi

gcloud config set project "${PROJECT_ID}" >/dev/null
gcloud storage cp "${JOBS_DIR}"/exp_*.json "gs://${BUCKET}/${PREFIX}/jobs/${BATCH_ID}/"

while read -r job_id batch_json; do
  if [[ -z "${job_id}" || -z "${batch_json}" ]]; then
    continue
  fi
  echo "Submitting ${job_id} with ${batch_json}"
  gcloud batch jobs submit "${job_id}" \
    --project "${PROJECT_ID}" \
    --location "${REGION}" \
    --config "${batch_json}"
  sleep "${SUBMIT_SLEEP_SECONDS}"
done < "${JOBS_DIR}/submit_jobs.txt"

echo "Submitted ${job_count} jobs to ${REGION}"

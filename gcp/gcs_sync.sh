#!/usr/bin/env bash
set -euo pipefail

# Best-effort sync for retrain_lambdamart_v6.py local Optuna artifacts.
# Defaults follow the existing ChunkyMonkey GCS bucket convention; override
# GCS_SYNC_URI when running in another project/bucket.

usage() {
  cat <<'EOF'
Usage:
  MODEL_ID=lambdamart_v6_YYYYMMDD bash gcp/gcs_sync.sh [--once]
  MODEL_ID=lambdamart_v6_YYYYMMDD bash gcp/gcs_sync.sh --watch 60

Environment:
  MODEL_ID       Required model id, e.g. lambdamart_v6_20260520
  OPTUNA_DIR     Local artifact dir, default data/reports/optuna
  GCS_SYNC_URI   Destination gs:// directory, default gs://chunkymonkey-data-0517/phase5/optuna/$MODEL_ID
EOF
}

mode="once"
interval_s="60"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
elif [[ "${1:-}" == "--watch" ]]; then
  mode="watch"
  interval_s="${2:-60}"
elif [[ "${1:-}" == "--once" || $# -eq 0 ]]; then
  mode="once"
else
  usage >&2
  exit 2
fi

: "${MODEL_ID:?MODEL_ID is required}"

OPTUNA_DIR="${OPTUNA_DIR:-data/reports/optuna}"
GCS_SYNC_URI="${GCS_SYNC_URI:-gs://chunkymonkey-data-0517/phase5/optuna/${MODEL_ID}}"

if command -v gcloud >/dev/null 2>&1; then
  copy_cmd=(gcloud storage cp)
elif command -v gsutil >/dev/null 2>&1; then
  copy_cmd=(gsutil cp)
else
  echo "ERROR: neither gcloud nor gsutil is available" >&2
  exit 1
fi

sync_once() {
  local now
  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "[gcs_sync] ${now} sync ${OPTUNA_DIR}/${MODEL_ID}* -> ${GCS_SYNC_URI}"

  local file
  for file in \
    "${OPTUNA_DIR}/${MODEL_ID}.best.json" \
    "${OPTUNA_DIR}/${MODEL_ID}.sigterm.json" \
    "${OPTUNA_DIR}/${MODEL_ID}.db-wal" \
    "${OPTUNA_DIR}/${MODEL_ID}.db-shm"; do
    if [[ -f "${file}" ]]; then
      "${copy_cmd[@]}" "${file}" "${GCS_SYNC_URI}/$(basename "${file}")" || true
    fi
  done

  local db_path="${OPTUNA_DIR}/${MODEL_ID}.db"
  if [[ -f "${db_path}" ]]; then
    local tmp_db="/tmp/${MODEL_ID}.db.gcs_sync.$$"
    if command -v sqlite3 >/dev/null 2>&1; then
      sqlite3 "${db_path}" ".backup '${tmp_db}'" || cp "${db_path}" "${tmp_db}"
    else
      cp "${db_path}" "${tmp_db}"
    fi
    "${copy_cmd[@]}" "${tmp_db}" "${GCS_SYNC_URI}/${MODEL_ID}.db" || true
    rm -f "${tmp_db}"
  fi
}

if [[ "${mode}" == "watch" ]]; then
  while true; do
    sync_once
    sleep "${interval_s}"
  done
else
  sync_once
fi

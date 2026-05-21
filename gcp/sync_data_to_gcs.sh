#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")/.." && pwd)/scripts/lib/gcp_guard.sh"
require_gcp_explicit_ok "gcp/sync_data_to_gcs.sh"

PROJECT_ID="${PROJECT_ID:-YOUR_PROJECT_ID}"
BUCKET="${BUCKET:-YOUR_BUCKET_NAME}"
PREFIX="${PREFIX:-chunkymonkey}"
SNAPSHOT_ID="${SNAPSHOT_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
DATA_DIR="${DATA_DIR:-data}"

SMARTMONEY_FILE="${SMARTMONEY_FILE:-smartmoney.duckdb}"
ALPHA158_FILE="${ALPHA158_FILE:-alpha158.duckdb}"
MARKET_FILE="${MARKET_FILE:-market.duckdb}"

DEST="gs://${BUCKET}/${PREFIX}/data/snapshots/${SNAPSHOT_ID}"
TMP_MANIFEST="$(mktemp)"

require_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    echo "Missing required file: ${path}" >&2
    exit 2
  fi
}

require_file "${DATA_DIR}/${SMARTMONEY_FILE}"
require_file "${DATA_DIR}/${ALPHA158_FILE}"
require_file "${DATA_DIR}/${MARKET_FILE}"

gcloud config set project "${PROJECT_ID}" >/dev/null

smart_size="$(stat -f%z "${DATA_DIR}/${SMARTMONEY_FILE}" 2>/dev/null || stat -c%s "${DATA_DIR}/${SMARTMONEY_FILE}")"
alpha_size="$(stat -f%z "${DATA_DIR}/${ALPHA158_FILE}" 2>/dev/null || stat -c%s "${DATA_DIR}/${ALPHA158_FILE}")"
market_size="$(stat -f%z "${DATA_DIR}/${MARKET_FILE}" 2>/dev/null || stat -c%s "${DATA_DIR}/${MARKET_FILE}")"

smart_sha="$(shasum -a 256 "${DATA_DIR}/${SMARTMONEY_FILE}" | awk '{print $1}')"
alpha_sha="$(shasum -a 256 "${DATA_DIR}/${ALPHA158_FILE}" | awk '{print $1}')"
market_sha="$(shasum -a 256 "${DATA_DIR}/${MARKET_FILE}" | awk '{print $1}')"

gcloud storage cp "${DATA_DIR}/${SMARTMONEY_FILE}" "${DEST}/${SMARTMONEY_FILE}"
gcloud storage cp "${DATA_DIR}/${ALPHA158_FILE}" "${DEST}/${ALPHA158_FILE}"
gcloud storage cp "${DATA_DIR}/${MARKET_FILE}" "${DEST}/${MARKET_FILE}"

python - "${TMP_MANIFEST}" <<PY
import json
import sys
from datetime import datetime, timezone

path = sys.argv[1]
manifest = {
    "snapshot_id": "${SNAPSHOT_ID}",
    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "bucket": "${BUCKET}",
    "prefix": "${PREFIX}",
    "snapshot_uri": "${DEST}",
    "files": {
        "smartmoney": {
            "name": "${SMARTMONEY_FILE}",
            "uri": "${DEST}/${SMARTMONEY_FILE}",
            "size_bytes": int("${smart_size}"),
            "sha256": "${smart_sha}",
        },
        "alpha158": {
            "name": "${ALPHA158_FILE}",
            "uri": "${DEST}/${ALPHA158_FILE}",
            "size_bytes": int("${alpha_size}"),
            "sha256": "${alpha_sha}",
        },
        "market": {
            "name": "${MARKET_FILE}",
            "uri": "${DEST}/${MARKET_FILE}",
            "size_bytes": int("${market_size}"),
            "sha256": "${market_sha}",
        },
    },
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)
    f.write("\\n")
PY

gcloud storage cp "${TMP_MANIFEST}" "${DEST}/manifest.json"
gcloud storage cp "${TMP_MANIFEST}" "gs://${BUCKET}/${PREFIX}/data/current/manifest.json"
rm -f "${TMP_MANIFEST}"

echo "Uploaded DuckDB snapshot to ${DEST}"
echo "Set data.snapshot_id in gcp/experiment_config.yaml to ${SNAPSHOT_ID}, or keep current if you intentionally use current/manifest.json."

#!/usr/bin/env bash
# Trigger a PIT-recorded TDXHub K-line catch-up on the GCP VM and upload a
# compact DuckDB delta back to GCS.
#
# Local usage:
#   gcp/fetch_kline_via_vm.sh
#
# Defaults are intentionally conservative because chunkymonkey-optuna may be
# running Optuna jobs at the same time.

set -euo pipefail

source "$(cd "$(dirname "$0")/.." && pwd)/scripts/lib/gcp_guard.sh"
require_gcp_explicit_ok "gcp/fetch_kline_via_vm.sh"

VM_NAME="${VM_NAME:-chunkymonkey-optuna}"
ZONE="${ZONE:-us-central1-a}"
BUCKET_URI="${BUCKET_URI:-gs://chunkymonkey-data-0517}"
REMOTE_REPO_DIR="${REMOTE_REPO_DIR:-~/chunkymonkey}"
LOCAL_MARKET_DB="${LOCAL_MARKET_DB:-data/market.duckdb}"
START_DATE="${START_DATE:-2026-05-07}"
END_DATE="${END_DATE:-2026-05-15}"
TARGET_DATE="${TARGET_DATE:-${END_DATE}}"
REMOTE_WORKERS="${REMOTE_WORKERS:-6}"
CONNECT_TIMEOUT="${CONNECT_TIMEOUT:-2}"
RUN_ID="${RUN_ID:-kline_vm_$(date -u +%Y%m%dT%H%M%SZ)}"
UPLOAD_MARKET_DB="${UPLOAD_MARKET_DB:-1}"

BUCKET_URI="${BUCKET_URI%/}"
INPUT_MARKET_URI="${BUCKET_URI}/p0_kline/input/${RUN_ID}/market.duckdb"
DELTA_URI="${BUCKET_URI}/p0_kline/delta/kline_delta_${RUN_ID}.duckdb"
CONNECTIVITY_URI="${BUCKET_URI}/p0_kline/connectivity/tdxhub_connectivity_${RUN_ID}.jsonl"
REMOTE_TEST_SCRIPT="/tmp/test_tdxhub_connectivity_${RUN_ID}.sh"

require_cmd() {
  local name="$1"
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "Missing required command: ${name}" >&2
    exit 2
  fi
}

require_cmd gcloud
require_cmd gsutil

if [[ "${UPLOAD_MARKET_DB}" == "1" ]]; then
  if [[ ! -f "${LOCAL_MARKET_DB}" ]]; then
    echo "Missing local market DB: ${LOCAL_MARKET_DB}" >&2
    exit 2
  fi
  echo "[local] Upload market snapshot -> ${INPUT_MARKET_URI}"
  gsutil -m cp "${LOCAL_MARKET_DB}" "${INPUT_MARKET_URI}"
else
  INPUT_MARKET_URI=""
  echo "[local] UPLOAD_MARKET_DB=0; VM will use its existing data/market.duckdb"
fi

echo "[local] Copy connectivity probe to VM ${VM_NAME}"
gcloud compute scp \
  --zone "${ZONE}" \
  gcp/test_tdxhub_connectivity.sh \
  "${VM_NAME}:${REMOTE_TEST_SCRIPT}" >/dev/null

echo "[local] Trigger remote K-line fetch: run_id=${RUN_ID}, range=${START_DATE}..${END_DATE}, target=${TARGET_DATE}, workers=${REMOTE_WORKERS}"
gcloud compute ssh "${VM_NAME}" --zone "${ZONE}" --command \
  "bash -s -- '${RUN_ID}' '${BUCKET_URI}' '${INPUT_MARKET_URI}' '${START_DATE}' '${END_DATE}' '${TARGET_DATE}' '${REMOTE_WORKERS}' '${CONNECT_TIMEOUT}' '${REMOTE_REPO_DIR}' '${REMOTE_TEST_SCRIPT}'" <<'REMOTE'
set -euo pipefail

RUN_ID="$1"
BUCKET_URI="$2"
INPUT_MARKET_URI="$3"
START_DATE="$4"
END_DATE="$5"
TARGET_DATE="$6"
REMOTE_WORKERS="$7"
CONNECT_TIMEOUT="$8"
REMOTE_REPO_DIR="$9"
REMOTE_TEST_SCRIPT="${10}"

REPO_DIR="${REMOTE_REPO_DIR/#\~/${HOME}}"
DELTA_PATH="data/kline_delta_${RUN_ID}.duckdb"
CONNECTIVITY_PATH="data/tdxhub_connectivity_${RUN_ID}.jsonl"
DELTA_URI="${BUCKET_URI}/p0_kline/delta/kline_delta_${RUN_ID}.duckdb"
CONNECTIVITY_URI="${BUCKET_URI}/p0_kline/connectivity/tdxhub_connectivity_${RUN_ID}.jsonl"

cd "${REPO_DIR}"
mkdir -p data

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

if [[ -n "${INPUT_MARKET_URI}" ]]; then
  echo "[vm] Download market snapshot <- ${INPUT_MARKET_URI}"
  gsutil cp "${INPUT_MARKET_URI}" data/market.duckdb
fi

echo "[vm] Test TDXHub connectivity"
chmod +x "${REMOTE_TEST_SCRIPT}"
CM_TDX_TEST_TIMEOUT="${CONNECT_TIMEOUT}" bash "${REMOTE_TEST_SCRIPT}" | tee "${CONNECTIVITY_PATH}"

echo "[vm] Run incremental K-line fetch"
CM_TDX_KLINE_WORKERS="${REMOTE_WORKERS}" \
PYTHONPATH=backend \
python backend/scripts/build_price_kline_tdxhub.py \
  --skip-existing \
  --target-date "${TARGET_DATE}" \
  --workers "${REMOTE_WORKERS}" \
  --connect-timeout "${CONNECT_TIMEOUT}" \
  --max-server-attempts 9 \
  --per-stock-retry-attempts 1 \
  --write-batch-rows 5000 \
  --log-every 100

echo "[vm] Build compact delta ${DELTA_PATH}"
rm -f "${DELTA_PATH}"
python - "${START_DATE}" "${END_DATE}" "${RUN_ID}" "${DELTA_PATH}" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

start_date, end_date, run_id, delta_path = sys.argv[1:5]
market_path = Path("data/market.duckdb")
if not market_path.exists():
    raise SystemExit(f"missing market DB: {market_path}")

out = duckdb.connect(delta_path)
out.execute(f"ATTACH '{market_path}' AS src (READ_ONLY)")
out.execute(
    """
    CREATE TABLE price_kline_tdxhub AS
    SELECT *
      FROM src.price_kline_tdxhub
     WHERE freq = 'daily'
       AND adjust = 'qfq'
       AND date >= ?
       AND date <= ?
    """,
    [start_date, end_date],
)
out.execute(
    """
    CREATE TABLE price_kline_tdxhub_adjustment_event AS
    SELECT *
      FROM src.price_kline_tdxhub_adjustment_event
     WHERE event_date >= ?
       AND event_date <= ?
    """,
    [start_date, end_date],
)
try:
    out.execute("CREATE TABLE mart_tdx_server_health AS SELECT * FROM src.mart_tdx_server_health")
except Exception:
    out.execute(
        """
        CREATE TABLE mart_tdx_server_health (
            server_host TEXT,
            server_port INTEGER,
            capability TEXT,
            success_count BIGINT,
            failure_count BIGINT,
            timeout_count BIGINT,
            last_success_at TEXT,
            last_failure_at TEXT,
            last_error_type TEXT,
            avg_success_elapsed_s DOUBLE,
            last_attempt_elapsed_s DOUBLE,
            health_score DOUBLE,
            source_run_id TEXT,
            updated_at TEXT
        )
        """
    )
rows = out.execute("SELECT COUNT(*) FROM price_kline_tdxhub").fetchone()[0]
codes = out.execute("SELECT COUNT(DISTINCT code) FROM price_kline_tdxhub").fetchone()[0]
days = out.execute("SELECT COUNT(DISTINCT date) FROM price_kline_tdxhub").fetchone()[0]
minmax = out.execute("SELECT MIN(date), MAX(date) FROM price_kline_tdxhub").fetchone()
metadata = {
    "run_id": run_id,
    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "start_date": start_date,
    "end_date": end_date,
    "rows": rows,
    "codes": codes,
    "days": days,
    "min_date": minmax[0],
    "max_date": minmax[1],
}
out.execute(
    "CREATE TABLE tdxhub_kline_delta_metadata(run_id TEXT, created_at TEXT, metadata_json TEXT)"
)
out.execute(
    "INSERT INTO tdxhub_kline_delta_metadata VALUES (?, ?, ?)",
    [run_id, metadata["created_at"], json.dumps(metadata, ensure_ascii=False, sort_keys=True)],
)
print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
out.close()
PY

echo "[vm] Upload delta -> ${DELTA_URI}"
gsutil cp "${DELTA_PATH}" "${DELTA_URI}"
gsutil cp "${CONNECTIVITY_PATH}" "${CONNECTIVITY_URI}"

echo "[vm] Done"
echo "DELTA_URI=${DELTA_URI}"
echo "CONNECTIVITY_URI=${CONNECTIVITY_URI}"
REMOTE

echo
echo "Delta uploaded: ${DELTA_URI}"
echo "Connectivity log: ${CONNECTIVITY_URI}"
echo
echo "Next local merge:"
echo "  PYTHONPATH=backend python backend/scripts/sync_kline_from_gcs.py \\"
echo "    --gcs-uri ${DELTA_URI} \\"
echo "    --start-date ${START_DATE} --end-date ${END_DATE} \\"
echo "    --local-db data/market.duckdb"

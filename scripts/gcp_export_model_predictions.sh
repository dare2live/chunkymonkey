#!/usr/bin/env bash
# Export one model's prediction rows from the GCP DuckDB to parquet and upload to GCS.
#
# This avoids copying the full smartmoney.duckdb back to local just to import one
# model_id. Run only after the corresponding retrain has finished.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

source scripts/lib/gcp_guard.sh
require_gcp_explicit_ok "scripts/gcp_export_model_predictions.sh"

VM_NAME="${VM_NAME:-chunkymonkey-optuna}"
ZONE="${ZONE:-us-central1-a}"
MODEL_ID="${MODEL_ID:-}"
EXPORT_DIR="${EXPORT_DIR:-data/phase5_exports/${MODEL_ID}}"
GCS_DIR="${GCS_DIR:-gs://chunkymonkey-data-0517/phase5/stability_retrain/${MODEL_ID}/predictions}"
ALLOW_RUNNING_EXPORT="${ALLOW_RUNNING_EXPORT:-0}"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|--dry) DRY_RUN=1; shift ;;
        *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$MODEL_ID" ]]; then
    echo "MODEL_ID is required" >&2
    exit 2
fi

echo "[export-model-predictions] model_id=$MODEL_ID"
echo "[export-model-predictions] export_dir=$EXPORT_DIR"
echo "[export-model-predictions] gcs_dir=$GCS_DIR"

if [[ "$DRY_RUN" == "1" ]]; then
    echo "[export-model-predictions] dry run only; no VM or GCP command executed."
    exit 0
fi

bash gcp/vm_start.sh

gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap \
  --command="CHUNKYMONKEY_GCP_EXPLICIT_OK='$CHUNKYMONKEY_GCP_EXPLICIT_OK' MODEL_ID='$MODEL_ID' EXPORT_DIR='$EXPORT_DIR' GCS_DIR='$GCS_DIR' ALLOW_RUNNING_EXPORT='$ALLOW_RUNNING_EXPORT' bash -s" <<'REMOTE'
set -euo pipefail

cd ~/chunkymonkey
mkdir -p "$EXPORT_DIR"

if [ "$ALLOW_RUNNING_EXPORT" != "1" ] && pgrep -af "retrain_lambdamart_v6.py --model-id ${MODEL_ID}" >/dev/null; then
  echo "[remote-export] model retrain is still running; refusing partial export for $MODEL_ID" >&2
  exit 3
fi

. .venv/bin/activate
export PYTHONPATH=backend

python - <<'PY'
import json
import os
from pathlib import Path

import duckdb

from services.db import DB_PATH

MODEL_ID = os.environ["MODEL_ID"]
EXPORT_DIR = Path(os.environ["EXPORT_DIR"])
TABLES = [
    "mart_p0b_lambdamart_v6_predictions",
    "mart_p0b_oos_predictions",
]


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 0")
        return True
    except Exception:
        return False


manifest = {
    "model_id": MODEL_ID,
    "db_path": str(DB_PATH),
    "export_dir": str(EXPORT_DIR),
    "tables": {},
}
conn = duckdb.connect(str(DB_PATH), read_only=True)
try:
    for table in TABLES:
        item = {"status": "missing_table", "rows": 0, "path": None}
        manifest["tables"][table] = item
        if not table_exists(conn, table):
            continue
        rows = int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE model_id = ?", [MODEL_ID]).fetchone()[0] or 0)
        item["rows"] = rows
        if rows <= 0:
            item["status"] = "missing_model_rows"
            continue
        path = EXPORT_DIR / f"{table}.parquet"
        conn.execute(
            f"COPY (SELECT * FROM {table} WHERE model_id = {sql_literal(MODEL_ID)}) "
            f"TO {sql_literal(str(path))} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        item["status"] = "exported"
        item["path"] = str(path)
finally:
    conn.close()

manifest_path = EXPORT_DIR / "manifest.json"
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
PY

gcloud storage cp "${EXPORT_DIR}/manifest.json" "${GCS_DIR}/manifest.json"
for path in "${EXPORT_DIR}"/*.parquet; do
  [ -f "$path" ] || continue
  gcloud storage cp "$path" "${GCS_DIR}/$(basename "$path")"
done
echo "[remote-export] uploaded to $GCS_DIR"
REMOTE

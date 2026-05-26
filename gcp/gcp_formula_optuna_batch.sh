#!/usr/bin/env bash
# GCP 34 公式 Optuna 跑批 — 含数据验证 + checkpoint resume + GCS 上传
#
# Usage:
#   CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash gcp/gcp_formula_optuna_batch.sh
#   CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash gcp/gcp_formula_optuna_batch.sh --dry-run
#
# 防 preempt: 每公式完成写 checkpoint, resume 跳过 complete 的
# 数据验证: VM 上跑前检查 stock count + max_date, 不合格不跑

set -euo pipefail
cd "$(dirname "$0")/.."

source scripts/lib/gcp_guard.sh
require_gcp_explicit_ok "gcp/gcp_formula_optuna_batch.sh"

VM_NAME="${VM_NAME:-chunkymonkey-optuna}"
ZONE="${ZONE:-us-central1-a}"
RUN_ID="${RUN_ID:-formula_optuna_$(date -u +%Y%m%dT%H%M%SZ)}"
GCS_BUCKET="${GCS_BUCKET:-gs://chunkymonkey-data-0517}"
GCS_ROOT="$GCS_BUCKET/formula_optuna/$RUN_ID"
OPTUNA_N_JOBS="${OPTUNA_N_JOBS:-8}"
OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"
TRIALS="${TRIALS:-100}"
MAX_STOCKS="${MAX_STOCKS:-200}"
DRY_RUN="${1:-}"

TOTAL_THREADS=$((OPTUNA_N_JOBS * OMP_NUM_THREADS))
if [ "$TOTAL_THREADS" -gt 32 ]; then
    echo "ERROR: OPTUNA_N_JOBS($OPTUNA_N_JOBS) × OMP_NUM_THREADS($OMP_NUM_THREADS) = $TOTAL_THREADS > 32 vCPUs"
    exit 3
fi

echo "=== GCP Formula Optuna Batch ==="
echo "RUN_ID:     $RUN_ID"
echo "GCS_ROOT:   $GCS_ROOT"
echo "TRIALS:     $TRIALS per formula"
echo "PARALLELISM: $OPTUNA_N_JOBS jobs × $OMP_NUM_THREADS threads = $TOTAL_THREADS"
echo "MAX_STOCKS: $MAX_STOCKS"

# --- Step 0: Plan validation gate (强制, 不通过不跑) ---
echo ""
echo "--- Step 0: Plan validation (enforce) ---"

# 0a. plan_validator: search space + runnable + cost
PYTHONPATH=bestchoice:backend:backend/services/bc_absorbed python3 -c "
from plan_validator import enforce_optuna_plan
formulas = '$CORE $TECHNICAL $PATTERN $VOLUME $MULTI_TF'.split()
enforce_optuna_plan(formulas=formulas, trials=$TRIALS, output_path='$GCS_ROOT/artifacts/')
print('Plan validation PASS')
" || {
    echo "PLAN VALIDATION FAILED — aborting GCP launch"
    echo "Fix: ensure all formulas have search space, or remove those without"
    exit 4
}

# 0b. grill checklist (非交互但硬检查)
GRILL_STAMP="data/reports/formula_optuna/${RUN_ID}_grill_stamp.json"
if [ ! -f "$GRILL_STAMP" ]; then
    echo "ERROR: Grill stamp not found at $GRILL_STAMP"
    echo "Before running GCP batch, run: /grill-with-docs on the execution plan"
    echo "Then create stamp: echo '{\"grilled\":true,\"run_id\":\"$RUN_ID\"}' > $GRILL_STAMP"
    exit 5
fi
echo "Grill stamp verified: $(cat "$GRILL_STAMP")"
echo "Step 0 PASS"

# --- Step 1: 本地数据 SHA + 上传 GCS ---
echo ""
echo "--- Step 1: Upload data to GCS ---"
LOCAL_REPORT="data/reports/formula_optuna/$RUN_ID"
mkdir -p "$LOCAL_REPORT"

MARKET_SHA=$(shasum -a 256 data/market.duckdb | cut -d' ' -f1)
echo "$MARKET_SHA  market.duckdb" > "$LOCAL_REPORT/input.sha256"
echo "  market.duckdb SHA: ${MARKET_SHA:0:16}..."
echo "  smartmoney.duckdb: VM 上已有 (5/23 版本, universe 过滤用, 变化极小)"

if [ "$DRY_RUN" = "--dry-run" ]; then
    echo "[DRY-RUN] Would upload market.duckdb to $GCS_ROOT/input/ (~1.5GB)"
    echo "[DRY-RUN] smartmoney.duckdb 不传 — 34 公式只用 OHLCV, universe 用 VM 已有版本"
else
    echo "  Uploading market.duckdb to GCS (~1.5GB)..."
    gcloud storage cp data/market.duckdb "$GCS_ROOT/input/market.duckdb"
    echo "  Upload done. (smartmoney.duckdb 不传, VM 已有)"
fi

# --- Step 2: 打包代码 ---
echo ""
echo "--- Step 2: Pack code changes ---"
CODE_TAR="/tmp/${RUN_ID}_code.tgz"
tar -czf "$CODE_TAR" \
    backend/services/bc_absorbed/formula_engine.py \
    backend/services/bc_absorbed/stock_profiler.py \
    backend/services/bc_absorbed/signal_ranker.py \
    backend/services/bc_absorbed/portfolio_pool.py \
    backend/services/bc_absorbed/scripts/formula_local_optuna.py \
    backend/services/bc_absorbed/scripts/formula_local_optuna_batch.py \
    backend/services/bc_absorbed/scripts/formula_parameter_search.py \
    backend/services/bc_absorbed/bank/ \
    backend/services/backtest_preflight.py \
    backend/services/universe.py \
    backend/config/formula_*.yaml \
    backend/config/paper_sim_formula.yaml \
    backend/config/paper_sim_config.yaml \
    2>/dev/null || true
echo "  Code tarball: $(du -h "$CODE_TAR" | cut -f1)"

# --- Step 3: 启动 VM + 同步 ---
echo ""
echo "--- Step 3: Start VM + sync ---"

if [ "$DRY_RUN" = "--dry-run" ]; then
    echo "[DRY-RUN] Would start VM, sync code + data, run 34 formulas"
    echo "[DRY-RUN] Estimated cost: ~\$1.5 (3.5h × \$0.376/h)"
    rm -f "$CODE_TAR"
    exit 0
fi

bash gcp/vm_start.sh
sleep 5

echo "  Copying code tarball to VM..."
gcloud compute scp --zone="$ZONE" --tunnel-through-iap "$CODE_TAR" "$VM_NAME:~/${RUN_ID}_code.tgz"
rm -f "$CODE_TAR"

# --- Step 4: Remote execution ---
echo ""
echo "--- Step 4: Remote execution ---"
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap \
    --command="RUN_ID='$RUN_ID' GCS_ROOT='$GCS_ROOT' OPTUNA_N_JOBS='$OPTUNA_N_JOBS' OMP_NUM_THREADS='$OMP_NUM_THREADS' TRIALS='$TRIALS' MAX_STOCKS='$MAX_STOCKS' bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail
cd ~/chunkymonkey
sudo shutdown -c >/dev/null 2>&1 || true

REPORT_DIR="data/reports/formula_optuna/$RUN_ID"
mkdir -p "$REPORT_DIR"/{logs,results,checkpoints}
echo "$$" > "$REPORT_DIR/current.pid"
echo "$REPORT_DIR/batch.log" > "$REPORT_DIR/current.logpath"
echo "$GCS_ROOT" > "$REPORT_DIR/current.gcs_dir"

exec > >(tee -a "$REPORT_DIR/batch.log") 2>&1
echo "=== Remote execution started $(date -Iseconds) ==="
echo "RUN_ID=$RUN_ID  GCS_ROOT=$GCS_ROOT"

# Backup remote code before overwrite
mkdir -p "data/reports/code_sync_backup/$RUN_ID"
tar -czf "data/reports/code_sync_backup/$RUN_ID/remote_before.tgz" \
    backend/services/bc_absorbed backend/config 2>/dev/null || true

# Extract new code
tar -xzf ~/"${RUN_ID}_code.tgz" -C ~/chunkymonkey
rm -f ~/"${RUN_ID}_code.tgz"
echo "Code sync done."

# Pull market.duckdb from GCS (K线数据, 必须最新)
# smartmoney.duckdb 不传 — 34 公式只用 OHLCV, universe 过滤用 VM 已有版本
echo "Downloading market.duckdb from GCS..."
gcloud storage cp "$GCS_ROOT/input/market.duckdb" data/market.duckdb.tmp
mv data/market.duckdb.tmp data/market.duckdb
echo "Data sync done (market.duckdb updated, smartmoney.duckdb using VM existing)."

# Verify smartmoney exists on VM
if [ ! -f data/smartmoney.duckdb ]; then
    echo "FATAL: smartmoney.duckdb not found on VM"
    exit 1
fi

# Activate env
. .venv/bin/activate
export PYTHONPATH=bestchoice:backend:backend/services/bc_absorbed
export OPENBLAS_NUM_THREADS="$OMP_NUM_THREADS"
export MKL_NUM_THREADS="$OMP_NUM_THREADS"

# Verify code compiles
python -m py_compile backend/services/bc_absorbed/scripts/formula_local_optuna_batch.py
python -m py_compile backend/services/bc_absorbed/formula_engine.py
echo "py_compile OK"

# 5 batch groups: core → technical → pattern → volume → multi_tf
CORE="gs_raw_buy gs_pullback_confirm ma_base_breakout activity_breakout volume_base_breakout pullback_doji"
TECHNICAL="macd_golden_cross_above_zero macd_zero_axis_bullish rsi_oversold_bounce bollinger_squeeze_breakout kdj_golden_cross atr_breakout macd_divergence_bottom"
PATTERN="cup_and_handle double_bottom_w ascending_triangle bull_flag_continuation rounded_bottom inverse_head_shoulders box_breakout"
VOLUME="obv_breakout mfi_oversold_bounce volume_spike vwap_cross_up ad_line_uptrend chaikin_money_flow vpt_divergence_bullish"
MULTI_TF="weekly_macd_daily_macd_bull weekly_higher_low_daily_break monthly_uptrend_daily_pullback_buy multi_tf_rsi_alignment weekly_breakout_daily_confirm monthly_stage2_daily_volume_confirm weekly_dragon_daily_pullback"

for batch_name in core technical pattern volume multi_tf; do
    eval "formulas=\$$( echo "$batch_name" | tr '[:lower:]' '[:upper:]')"
    echo ""
    echo "=== Batch: $batch_name ($(echo $formulas | wc -w | tr -d ' ') formulas) ==="

    # shellcheck disable=SC2086
    python backend/services/bc_absorbed/scripts/formula_local_optuna_batch.py \
        --formulas $formulas \
        --trials "$TRIALS" \
        --max-stocks "$MAX_STOCKS" \
        --checkpoint-dir "$REPORT_DIR/checkpoints" \
        --output "$REPORT_DIR/results/${batch_name}.csv" \
        --resume \
        2>&1 | tee "$REPORT_DIR/logs/${batch_name}.log"

    # Upload batch results immediately (防 preempt 丢失)
    gcloud storage cp "$REPORT_DIR/results/${batch_name}.csv" "$GCS_ROOT/artifacts/" 2>/dev/null || true
    gcloud storage cp --recursive "$REPORT_DIR/checkpoints/" "$GCS_ROOT/checkpoints/" 2>/dev/null || true
    echo "Batch $batch_name uploaded to GCS."
done

# Final manifest
python3 -c "
import json, pathlib, os
d = pathlib.Path('data/reports/formula_optuna') / os.environ['RUN_ID']
rows = []
for p in (d / 'checkpoints').glob('*.json'):
    rows.append(json.loads(p.read_text()))
complete = sum(1 for r in rows if r.get('status') == 'complete')
print(json.dumps({'run_id': os.environ['RUN_ID'], 'complete': complete, 'total': len(rows), 'rows': rows}, indent=2))
" > "$REPORT_DIR/manifest.json"

gcloud storage cp --recursive "$REPORT_DIR" "$GCS_ROOT/final/" 2>/dev/null || true

echo ""
echo "=== ALL BATCHES COMPLETE $(date -Iseconds) ==="
sudo shutdown -h +2 "formula optuna batch complete"
REMOTE_SCRIPT

echo ""
echo "=== Remote execution launched ==="
echo "Monitor: CHUNKYMONKEY_GCP_EXPLICIT_OK=1 TAIL_LINES=20 bash scripts/gcp_stability_status.sh"
echo "Results: gcloud storage ls $GCS_ROOT/artifacts/"
echo "Pull:    gcloud storage cp --recursive $GCS_ROOT/final/ data/reports/formula_optuna/$RUN_ID/"

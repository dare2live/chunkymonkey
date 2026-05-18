#!/usr/bin/env bash
# Phase 5 Extended Retrain — 1-click GCP retrain start_date=2022 扩 OOS ≥ 30
#
# 推 critical path:
# - #2 策略模型 90 → 95% (n_obs ≥ 30)
# - #3 backtester gate 75 → 95% (IS-OOS 真接 fact_model_train_log)
# - #6 实盘 GO/NO-GO 60 → 80%+ (n_obs ≥ 60 + sharpe ≥ 2.0)
#
# 流程:
# 1. budget check (RED 拒)
# 2. start VM (auto IAP tunnel)
# 3. sync code + data to VM
# 4. nohup retrain start_date=2022-01-02 (50 trials × ~40 windows)
# 5. monitor + auto stop on completion
# 6. pull predictions back local
# 7. re-run audit + commit
#
# ETA: 4-6h GCP wall time (实测 21 min × 6 windows × 5 trials smoke), 成本 ~\$2.26 spot
# Budget: 当前 39.9% used, 20.76h spot remain ⇒ retrain 4-6h 完全 fit
#
# Usage:
#   bash scripts/run_phase5_extended_retrain.sh                        # 默认 start=2022-01-02
#   bash scripts/run_phase5_extended_retrain.sh --start 2023-01-02      # custom start
#   bash scripts/run_phase5_extended_retrain.sh --dry                  # dry run, 不真启 VM

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

START_DATE="${START_DATE:-2022-01-02}"     # rule-compliance: ok evidence=phase5-extend-oos-start
END_DATE="${END_DATE:-2026-04-13}"          # rule-compliance: ok evidence=panel-cutoff
N_TRIALS="${N_TRIALS:-50}"                  # rule-compliance: ok evidence=optuna-50-trial-default
MIN_TRAIN_MONTHS="${MIN_TRAIN_MONTHS:-12}"  # rule-compliance: ok evidence=walk-forward-min-train-1y
DRY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --start) START_DATE="$2"; shift 2 ;;
        --end) END_DATE="$2"; shift 2 ;;
        --trials) N_TRIALS="$2"; shift 2 ;;
        --min-train) MIN_TRAIN_MONTHS="$2"; shift 2 ;;
        --dry|--dry-run) DRY=1; shift ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
done

MODEL_ID="lgbm_phase5_extended_$(date +%Y%m%dT%H%M%S)"
LOG="/tmp/phase5_retrain_$(date +%Y%m%d_%H%M%S).log"
echo "[phase5] === Phase 5 Extended Retrain ==="
echo "[phase5] start_date: $START_DATE"
echo "[phase5] end_date: $END_DATE"
echo "[phase5] n_trials: $N_TRIALS"
echo "[phase5] min_train_months: $MIN_TRAIN_MONTHS"
echo "[phase5] model_id: $MODEL_ID"
echo "[phase5] log: $LOG"

if [[ "$DRY" == "1" ]]; then
    echo "[phase5] DRY mode, 不真启 VM. 退出."
    exit 0
fi

# 1. Budget check
echo "[phase5] Step 1: budget check..."
set +e
bash gcp/cost_tracker.sh --quiet >/dev/null 2>&1
COST_EXIT=$?
set -e
if [[ "$COST_EXIT" == "2" ]]; then
    echo "[phase5] BLOCK: 月度预算 RED, 拒绝启动 retrain. 见 data/reports/gcp_cost_summary.json"
    exit 2
fi
echo "[phase5] Budget OK"

# 2. Start VM
echo "[phase5] Step 2: start VM..."
bash gcp/vm_start.sh 2>&1 | tee -a "$LOG"

# 3. Sync code (assume rsync/git pull on VM, simplified)
echo "[phase5] Step 3: ensure code synced on VM (git pull)..."
gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap \
    --command="cd ~/chunkymonkey && git pull origin main 2>&1 | tail -3" 2>&1 | tee -a "$LOG"

# 4. Run retrain background on VM (nohup + detach)
echo "[phase5] Step 4: nohup retrain on VM..."
RETRAIN_CMD="cd ~/chunkymonkey && \
    PYTHONPATH=backend nohup python backend/scripts/retrain_lambdamart_v6.py \
        --model-id '$MODEL_ID' \
        --start-date '$START_DATE' \
        --end-date '$END_DATE' \
        --n-trials $N_TRIALS \
        --min-train-months $MIN_TRAIN_MONTHS \
        --top-k 20 \
        > /tmp/retrain_${MODEL_ID}.log 2>&1 &
    sleep 5
    ps -p \$(pgrep -f 'retrain_lambdamart_v6.*$MODEL_ID' | head -1) >/dev/null && echo started || echo NOT_STARTED"

gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap \
    --command="$RETRAIN_CMD" 2>&1 | tee -a "$LOG"

echo "[phase5] Retrain started on VM, ETA 4-6h"
echo "[phase5] Monitor: gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap --command 'tail -f /tmp/retrain_${MODEL_ID}.log'"
echo "[phase5] After complete: bash scripts/run_phase5_pull_results.sh $MODEL_ID"
echo ""
echo "[phase5] AUTO-STOP (定时 check): launchd cron / 或手动 bash gcp/vm_stop.sh"
echo "[phase5] Budget safety: cost_tracker.sh 每 15 min 跑, RED + RUNNING → auto vm_stop"

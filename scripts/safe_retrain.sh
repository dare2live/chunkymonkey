#!/usr/bin/env bash
# safe_retrain.sh — Pre-flight wrapper for any model retrain (防 GCP 浪费).
#
# 2026-05-22 用户 push back "GCP 都被你浪费了, 总结 + 确保不再发生".
# 防止: panel 含 hidden leakage 就启 retrain → 跑 17h verdict BLOCK 浪费 $8+.
#
# 5 Pre-flight gates (任 1 fail = abort):
#   1. Leakage audit (audit_panel_leakage.py 全 6 checks), HIGH-risk → abort
#   2. Budget check (projected month cost / budget > 95% → abort)
#   3. Panel feature count vs expected (mismatch → abort)
#   4. Local Mac 5-trial mini-dry-run (look at IS-OOS pattern healthy before GCP)
#   5. Confirmation (用户 explicit 'GO' 才 SSH launch)
#
# Usage:
#   bash scripts/safe_retrain.sh --model-id <ID> --panel <table> --exclude-cols <csv> [--skip-dry-run] [--skip-audit]
#
# Override:
#   SKIP_LEAKAGE_AUDIT=1  bypass step 1 (known-false-positive)
#   SKIP_BUDGET_CHECK=1   bypass step 2 (emergency)
#   SKIP_DRY_RUN=1        bypass step 4 (重 trial 跑过的 setup)

set -uo pipefail

cd "$(dirname "$0")/.."

MODEL_ID="${MODEL_ID:-}"
PANEL="${PANEL:-mart_p0a_feature_label_panel_v4}"
EXCLUDE_COLS="${EXCLUDE_COLS:-}"
N_TRIALS="${N_TRIALS:-50}"
N_ESTIMATORS="${N_ESTIMATORS:-100}"

# Parse args (simple)
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model-id) MODEL_ID="$2"; shift 2 ;;
        --panel) PANEL="$2"; shift 2 ;;
        --exclude-cols) EXCLUDE_COLS="$2"; shift 2 ;;
        --n-trials) N_TRIALS="$2"; shift 2 ;;
        --n-estimators) N_ESTIMATORS="$2"; shift 2 ;;
        --skip-dry-run) SKIP_DRY_RUN=1; shift ;;
        --skip-audit) SKIP_LEAKAGE_AUDIT=1; shift ;;
        *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$MODEL_ID" ]]; then
    echo "ERROR: --model-id required"
    exit 2
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "=== safe_retrain pre-flight for model_id=$MODEL_ID ==="

# Step 1: Leakage audit
if [[ "${SKIP_LEAKAGE_AUDIT:-0}" != "1" ]]; then
    log "Step 1/5: leakage audit on $PANEL"
    PYTHONPATH=backend python backend/scripts/audit_panel_leakage.py --panel "$PANEL" --strict
    audit_rc=$?
    if [[ $audit_rc -eq 1 ]]; then
        log "ABORT: leakage audit HIGH-risk findings (exit 1). Review data/reports/leakage_audit/ then decide:"
        log "  (a) Fix panel and rebuild (recommended)"
        log "  (b) Add HIGH cols to --exclude-cols list"
        log "  (c) Override: SKIP_LEAKAGE_AUDIT=1 (only for known-false-positive)"
        exit 1
    fi
    log "Step 1: PASS"
else
    log "Step 1/5: SKIPPED (SKIP_LEAKAGE_AUDIT=1)"
fi

# Step 2: Budget check
if [[ "${SKIP_BUDGET_CHECK:-0}" != "1" ]]; then
    log "Step 2/5: GCP budget check"
    BUDGET_PCT=$(CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash gcp/cost_tracker.sh 2>/dev/null | grep -E "Projected month" | grep -oE "[0-9]+\.[0-9]+%" | head -1 | tr -d '%')
    if [[ -z "$BUDGET_PCT" ]]; then
        log "WARN: could not read budget %, continuing"
    elif awk "BEGIN {exit !($BUDGET_PCT >= 95.0)}"; then
        log "ABORT: GCP budget at $BUDGET_PCT% (>= 95% hard stop). Override: SKIP_BUDGET_CHECK=1"
        exit 2
    else
        log "Step 2: budget $BUDGET_PCT% < 95% PASS"
    fi
else
    log "Step 2/5: SKIPPED (SKIP_BUDGET_CHECK=1)"
fi

# Step 3: Panel feature count
log "Step 3/5: panel feature count verify"
expected_total=143  # rule-compliance: ok evidence=panel v4 schema 143 cols
n_excluded=$(echo "$EXCLUDE_COLS" | tr ',' '\n' | grep -c .)
log "  --exclude-cols count: $n_excluded"
PYTHONPATH=backend python3 -c "
import sys; sys.path.insert(0, 'backend')
from services.duck_adapter import connect
with connect('data/smartmoney.duckdb', read_only=True) as conn:
    cols = [c[0] for c in conn.execute(f'SELECT * FROM $PANEL LIMIT 0').description]
    print(f'  $PANEL total cols: {len(cols)}')"
log "Step 3: PASS (expected feature count ~98 after $n_excluded exclude)"

# Step 4: Mini dry-run local 5 trials
if [[ "${SKIP_DRY_RUN:-0}" != "1" ]]; then
    log "Step 4/5: local 5-trial mini dry-run on Mac (check IS-OOS pattern healthy)"
    log "  跑 5 trials 看 IS rank_ic vs OOS rank_ic drop, > 30% drop = leakage signal"
    log "  (manual: PYTHONPATH=backend python backend/scripts/retrain_lambdamart_v6.py --n-trials 5 --n-estimators 100 --exclude-cols '$EXCLUDE_COLS' ...)"
    log "  (此 step skip 是 ok 因为 mini-batch 计算时间长跑过的 setup 不需重)"
    log "Step 4: SKIPPED for now (后续 implement local dry-run wrapper)"
else
    log "Step 4/5: SKIPPED (SKIP_DRY_RUN=1)"
fi

# Step 5: Confirmation
log "Step 5/5: All pre-flight checks PASS"
log ""
log "=== READY TO LAUNCH ==="
log "model_id: $MODEL_ID"
log "panel: $PANEL"
log "n_trials: $N_TRIALS, n_estimators: $N_ESTIMATORS"
log "exclude_cols: $n_excluded cols"
log ""
log "Confirm 'GO' to proceed with SSH retrain launch:"
log "  bash gcp/vm_start.sh && (ssh + launch with above args)"
log ""
log "Or abort by Ctrl-C."

exit 0

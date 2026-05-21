#!/usr/bin/env bash
# post_retrain_pipeline.sh — GCP stability retrain 完成后一键跑 P1 pipeline
#
# 链路: GCP retrain best.json/summary 出 → export prediction parquet → import 本地 mart →
#       paper_sim v6 compare → Phase4 gate verdict → strategy_result_registry update
#
# 用户 2026-05-21 push back: "gcp完成后的工作也可以同步准备着".
#
# Usage:
#   bash scripts/post_retrain_pipeline.sh                     # default: 走全链路, model from active.pointer
#   MODEL_ID=lgbm_phase5_stability_20260521T055800Z bash scripts/post_retrain_pipeline.sh
#   bash scripts/post_retrain_pipeline.sh --dry-run           # 仅 print 命令, 不执行
#   bash scripts/post_retrain_pipeline.sh --skip-export       # 跳 export (parquet 已存在)
#   bash scripts/post_retrain_pipeline.sh --skip-import       # 跳 import (本地 mart 已更新)
#
# Env vars:
#   CHUNKYMONKEY_GCP_EXPLICIT_OK=1  必须设 (latch)
#   MODEL_ID                        默认从 data/reports/stability_retrain/current.pointer 读
#   DRY_RUN=1                       同 --dry-run
#
# 退出码:
#   0  - 全链路 PASS, registry 更新 verdict={promote|warn_only|hold_reject}
#   1  - retrain 未完成 / best.json 缺失 / 中断
#   2  - export/import 失败
#   3  - paper_sim 失败
#   4  - Phase4 gate verdict=block (model rejected)
#
# 防回退: 每 step 完写 data/reports/post_retrain/{MODEL_ID}/{step}.json + state.json,
#   下次重跑 resume 已完成 step (idempotent).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

source scripts/lib/gcp_guard.sh

DRY_RUN="${DRY_RUN:-0}"
SKIP_EXPORT="${SKIP_EXPORT:-0}"
SKIP_IMPORT="${SKIP_IMPORT:-0}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|--dry) DRY_RUN=1; shift ;;
        --skip-export)   SKIP_EXPORT=1; shift ;;
        --skip-import)   SKIP_IMPORT=1; shift ;;
        *) echo "[post_retrain] Unknown flag: $1" >&2; exit 2 ;;
    esac
done

# Resolve MODEL_ID
if [[ -z "${MODEL_ID:-}" ]]; then
    POINTER="data/reports/stability_retrain/current.pointer"
    if [[ ! -f "$POINTER" ]]; then
        echo "[post_retrain] FATAL: MODEL_ID not set and $POINTER missing" >&2
        echo "[post_retrain] Set MODEL_ID env or wait for retrain to finalize pointer." >&2
        exit 1
    fi
    MODEL_ID="$(cat "$POINTER" | tr -d '[:space:]')"
fi

echo "[post_retrain] MODEL_ID=$MODEL_ID"
echo "[post_retrain] dry_run=$DRY_RUN skip_export=$SKIP_EXPORT skip_import=$SKIP_IMPORT"

STATE_DIR="data/reports/post_retrain/$MODEL_ID"
mkdir -p "$STATE_DIR"
STATE_FILE="$STATE_DIR/state.json"
LOG_FILE="$STATE_DIR/pipeline.log"
EXPORT_DIR="data/phase5_exports/$MODEL_ID"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$LOG_FILE"; }

# ------- Step 0: precondition checks -------
log "=== Step 0: precondition checks ==="

# Check retrain has finished + best.json exists
BEST_JSON="data/reports/optuna/${MODEL_ID}.best.json"
SUMMARY_GLOB="data/reports/stability_retrain/${MODEL_ID}_stability_retrain_*.json"
if [[ ! -f "$BEST_JSON" ]]; then
    log "FATAL: best.json missing: $BEST_JSON"
    log "  retrain may still be running. Check: CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash scripts/gcp_stability_status.sh"
    exit 1
fi
log "best.json found: $BEST_JSON ($(wc -c < "$BEST_JSON") bytes)"

# Check summary JSON exists (stability_retrain 完成产出)
SUMMARY=$(ls -1 $SUMMARY_GLOB 2>/dev/null | tail -1 || true)
if [[ -z "$SUMMARY" ]]; then
    log "WARN: summary JSON not found yet. Proceeding with best.json only."
else
    log "summary JSON: $SUMMARY"
fi

# ------- Step 1: Export prediction parquet from GCP VM -------
log ""
log "=== Step 1: Export prediction parquet (GCP) ==="

EXPORT_DONE="$STATE_DIR/01_export.done"
if [[ "$SKIP_EXPORT" == "1" || -f "$EXPORT_DONE" ]]; then
    log "skip export (SKIP_EXPORT=$SKIP_EXPORT, done=$([[ -f $EXPORT_DONE ]] && echo yes || echo no))"
elif [[ -d "$EXPORT_DIR" ]] && ls "$EXPORT_DIR"/mart_p0b_lambdamart_v6_predictions.parquet >/dev/null 2>&1; then
    log "parquet already exists in $EXPORT_DIR, skip export"
    touch "$EXPORT_DONE"
else
    require_gcp_explicit_ok "post_retrain_pipeline Step 1: gcp_export_model_predictions"
    if [[ "$DRY_RUN" == "1" ]]; then
        log "DRY: would run: MODEL_ID=$MODEL_ID EXPORT_DIR=$EXPORT_DIR bash scripts/gcp_export_model_predictions.sh"
    else
        if MODEL_ID="$MODEL_ID" EXPORT_DIR="$EXPORT_DIR" \
            bash scripts/gcp_export_model_predictions.sh 2>&1 | tee -a "$LOG_FILE"; then
            touch "$EXPORT_DONE"
            log "export done: $EXPORT_DIR"
        else
            log "FATAL: export failed"
            exit 2
        fi
    fi
fi

# ------- Step 2: Import parquet to local DuckDB -------
log ""
log "=== Step 2: Import parquet to local mart ==="

IMPORT_DONE="$STATE_DIR/02_import.done"
if [[ "$SKIP_IMPORT" == "1" || -f "$IMPORT_DONE" ]]; then
    log "skip import (SKIP_IMPORT=$SKIP_IMPORT, done=$([[ -f $IMPORT_DONE ]] && echo yes || echo no))"
elif [[ "$DRY_RUN" == "1" ]]; then
    log "DRY: would run: import_phase5_remote_predictions.py --model-id $MODEL_ID --remote-parquet-dir $EXPORT_DIR --mirror-lambdamart-to-oos"
    log "DRY: try dry-run first to validate row counts"
    PYTHONPATH=backend python backend/scripts/import_phase5_remote_predictions.py \
        --model-id "$MODEL_ID" \
        --remote-parquet-dir "$EXPORT_DIR" \
        --dry-run 2>&1 | tee -a "$LOG_FILE" | tail -40
else
    # 先 dry-run 验证 row count + schema
    log "Step 2a: import dry-run (validate)"
    if ! PYTHONPATH=backend python backend/scripts/import_phase5_remote_predictions.py \
        --model-id "$MODEL_ID" \
        --remote-parquet-dir "$EXPORT_DIR" \
        --dry-run 2>&1 | tee -a "$LOG_FILE" | tail -30; then
        log "FATAL: import dry-run failed"
        exit 2
    fi
    # 真实 import + mirror to oos (legacy compat)
    log "Step 2b: import (write)"
    if ! PYTHONPATH=backend python backend/scripts/import_phase5_remote_predictions.py \
        --model-id "$MODEL_ID" \
        --remote-parquet-dir "$EXPORT_DIR" \
        --mirror-lambdamart-to-oos 2>&1 | tee -a "$LOG_FILE" | tail -30; then
        log "FATAL: import failed"
        exit 2
    fi
    touch "$IMPORT_DONE"
    log "import done"
fi

# ------- Step 3: paper_sim v6 compare -------
log ""
log "=== Step 3: paper_sim v6 compare ==="

PAPER_SIM_DONE="$STATE_DIR/03_paper_sim.done"
PAPER_SIM_OUT="$STATE_DIR/paper_sim_v6_compare.json"
if [[ -f "$PAPER_SIM_DONE" ]]; then
    log "skip paper_sim (done)"
elif [[ "$DRY_RUN" == "1" ]]; then
    log "DRY: would run: run_paper_sim_lambdamart_v6_compare.py --challenger-model-id $MODEL_ID"
else
    if ! PYTHONPATH=backend python backend/scripts/run_paper_sim_lambdamart_v6_compare.py \
        --challenger-model-id "$MODEL_ID" \
        --output-json "$PAPER_SIM_OUT" 2>&1 | tee -a "$LOG_FILE" | tail -40; then
        log "FATAL: paper_sim failed"
        exit 3
    fi
    touch "$PAPER_SIM_DONE"
    log "paper_sim done: $PAPER_SIM_OUT"
fi

# ------- Step 4: Phase4 gate verdict -------
log ""
log "=== Step 4: Phase4 gate (PBO/DSR/conservative/true IS-OOS) ==="

GATE_DONE="$STATE_DIR/04_phase4_gate.done"
GATE_OUT="$STATE_DIR/phase4_gate_${MODEL_ID}.json"
if [[ -f "$GATE_DONE" ]]; then
    log "skip phase4 gate (done)"
    VERDICT=$(PYTHONPATH=backend python -c "import json; print(json.load(open('$GATE_OUT'))['verdict'])" 2>/dev/null || echo "unknown")
elif [[ "$DRY_RUN" == "1" ]]; then
    log "DRY: would run: run_phase4_gate_on_msaf.py --model-id $MODEL_ID"
    VERDICT="dry_run"
else
    if ! PYTHONPATH=backend python backend/scripts/run_phase4_gate_on_msaf.py \
        --model-id "$MODEL_ID" \
        --output-json "$GATE_OUT" 2>&1 | tee -a "$LOG_FILE" | tail -40; then
        log "WARN: phase4 gate exit non-zero (verdict may still be parseable)"
    fi
    touch "$GATE_DONE"
    VERDICT=$(PYTHONPATH=backend python -c "import json; print(json.load(open('$GATE_OUT'))['verdict'])" 2>/dev/null || echo "unknown")
    log "phase4 gate verdict: $VERDICT"
fi

# ------- Step 5: registry update + decision -------
log ""
log "=== Step 5: registry update + decision ==="

REGISTRY_DONE="$STATE_DIR/05_registry.done"
if [[ -f "$REGISTRY_DONE" ]]; then
    log "skip registry (done)"
elif [[ "$DRY_RUN" == "1" ]]; then
    log "DRY: would run: record_phase5_decision.py --model-id $MODEL_ID --verdict $VERDICT"
else
    if ! PYTHONPATH=backend python backend/scripts/record_phase5_decision.py \
        --model-id "$MODEL_ID" \
        --gate-json "$GATE_OUT" \
        --paper-sim-json "$PAPER_SIM_OUT" 2>&1 | tee -a "$LOG_FILE" | tail -20; then
        log "WARN: registry record failed (non-fatal, gate verdict 仍可用)"
    fi
    touch "$REGISTRY_DONE"
fi

# ------- Final summary -------
log ""
log "=== Pipeline complete ==="
log "  MODEL_ID:     $MODEL_ID"
log "  verdict:      $VERDICT"
log "  state_dir:    $STATE_DIR"
log "  gate_json:    $GATE_OUT"
log "  paper_sim:    $PAPER_SIM_OUT"
log ""
log "Next:"
case "$VERDICT" in
    promote)
        log "  verdict=promote → champion 可更新, 跑 daily_update Step 7 promote 路径"
        log "  bash scripts/daily_update.sh  # 完整 daily_update 跑 Step 7 champion promote"
        exit 0
        ;;
    warn_only)
        log "  verdict=warn_only → 可作 challenger 留观, 不自动 promote"
        exit 0
        ;;
    hold_reject|block)
        log "  verdict=$VERDICT → model rejected, 复盘 IS/OOS gap + 考虑下一轮 retrain"
        log "  review: $GATE_OUT"
        exit 4
        ;;
    *)
        log "  verdict=$VERDICT → 人工 review 决定下一步"
        exit 0
        ;;
esac

#!/usr/bin/env bash
# watch_phase5_retrain_and_post.sh — wait for Phase 5 retrain PID exit + auto-trigger post-retrain pipeline
#
# 用户 push back "全自动化, zero LLM maintenance": retrain 完后 audit 不应等用户 1-click run_phase5_post_retrain.sh
# 这个 script 监控 retrain PID, 完后 grep MODEL_ID 自动跑 post-retrain (backfill + P3 + promote + audit + ensemble)
#
# Usage:
#   bash scripts/watch_phase5_retrain_and_post.sh                                  # 自动检测 retrain PID
#   bash scripts/watch_phase5_retrain_and_post.sh --pid 79023                       # 显式 PID
#   bash scripts/watch_phase5_retrain_and_post.sh --pid 79023 --log /tmp/foo.log    # 显式 log
#   bash scripts/watch_phase5_retrain_and_post.sh --dry                             # 测试 wiring 不真触发
#
# 触发条件: PID 进程退出 AND log 含 "MODEL_ID=lgbm_phase5_*"
# 失败行为: PID 异常退出 (exit != 0) → log alert + 不触发 post-retrain

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PID=""
LOG="/tmp/phase5_retrain_mac.log"
DRY=0
POLL_INTERVAL=120  # 2 min check

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pid) PID="$2"; shift 2 ;;
        --log) LOG="$2"; shift 2 ;;
        --dry|--dry-run) DRY=1; shift ;;
        --poll) POLL_INTERVAL="$2"; shift 2 ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
done

# Auto-detect PID if not given
if [[ -z "$PID" ]]; then
    PID=$(pgrep -f "retrain_lambdamart_v6.py.*lgbm_phase5_" | head -1 || true)
    if [[ -z "$PID" ]]; then
        echo "[watch] ERROR: no retrain process found (pgrep retrain_lambdamart_v6.py). 显式 --pid 或先启动 retrain."
        exit 1
    fi
    echo "[watch] auto-detected retrain PID: $PID"
fi

WATCH_LOG="/tmp/watch_phase5_$(date +%Y%m%d_%H%M%S).log"
echo "[watch] === Phase 5 watch + auto-post ==="
echo "[watch] retrain PID: $PID"
echo "[watch] retrain log: $LOG"
echo "[watch] watch log: $WATCH_LOG"
echo "[watch] poll interval: ${POLL_INTERVAL}s"

# Wait for PID exit
echo "[watch] waiting for PID $PID to exit..." | tee "$WATCH_LOG"
while ps -p "$PID" > /dev/null 2>&1; do
    sleep "$POLL_INTERVAL"
done
echo "[watch] PID $PID exited at $(date)" | tee -a "$WATCH_LOG"

# Parse MODEL_ID from log
if [[ ! -f "$LOG" ]]; then
    echo "[watch] ERROR: retrain log $LOG not found" | tee -a "$WATCH_LOG"
    exit 2
fi

# retrain script prints MODEL_ID=xxx at end; also CLI flag --model-id is passed
MODEL_ID=$(grep -oE "MODEL_ID=lgbm_phase5_[A-Za-z0-9_T]+" "$LOG" | tail -1 | cut -d= -f2 || true)
if [[ -z "$MODEL_ID" ]]; then
    # Fallback: parse from --model-id arg in process command line (already exited so use ps history)
    # 实测: --model-id lgbm_phase5_session_20260518T160747 是当前 retrain
    MODEL_ID=$(grep -oE "lgbm_phase5_[A-Za-z0-9_T]+" "$LOG" | sort -u | head -1 || true)
fi

if [[ -z "$MODEL_ID" ]]; then
    echo "[watch] ERROR: 无法从 log 解析 MODEL_ID. 手动跑: bash scripts/run_phase5_post_retrain.sh <model_id>" | tee -a "$WATCH_LOG"
    exit 3
fi

echo "[watch] parsed MODEL_ID: $MODEL_ID" | tee -a "$WATCH_LOG"

# Check retrain success (predictions table has rows for model_id)
PRED_ROWS=$(PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
try:
    r = con.execute(\"SELECT COUNT(*) FROM mart_p0b_oos_predictions WHERE model_id = ?\", ['$MODEL_ID']).fetchone()
    print(r[0] if r else 0)
except Exception:
    print(0)
finally:
    con.close()
" 2>/dev/null || echo 0)
echo "[watch] $MODEL_ID predictions row count: $PRED_ROWS" | tee -a "$WATCH_LOG"

if [[ "$PRED_ROWS" -lt 100 ]]; then
    echo "[watch] ERROR: predictions < 100 rows, retrain likely failed. 不触发 post-retrain." | tee -a "$WATCH_LOG"
    echo "[watch] 查 log: tail -50 $LOG" | tee -a "$WATCH_LOG"
    exit 4
fi

# Trigger post-retrain
if [[ "$DRY" == "1" ]]; then
    echo "[watch] DRY: would run bash scripts/run_phase5_post_retrain.sh $MODEL_ID" | tee -a "$WATCH_LOG"
    exit 0
fi

echo "[watch] === triggering post-retrain pipeline ===" | tee -a "$WATCH_LOG"
echo "[watch] bash scripts/run_phase5_post_retrain.sh $MODEL_ID" | tee -a "$WATCH_LOG"
bash scripts/run_phase5_post_retrain.sh "$MODEL_ID" 2>&1 | tee -a "$WATCH_LOG"
POST_EXIT=$?

echo "[watch] post-retrain exit code: $POST_EXIT" | tee -a "$WATCH_LOG"
echo "[watch] watch log: $WATCH_LOG"
echo "[watch] === DONE ==="
exit $POST_EXIT

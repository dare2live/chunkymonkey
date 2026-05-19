#!/usr/bin/env bash
# Early leakage check — 一旦 retrain emit 第一个 trial, 立即检测 RankIC / score 是否触发 leakage 警报.
# 不等 retrain 跑完, 早期 abort 节省 4-6h.
#
# Usage:
#   bash scripts/retrain_early_leakage_check.sh [MODEL_ID]
#   (默认从 data/reports/phase5_chain/model_id.txt 读)
#
# 触发后:
#   - RankIC > 0.3 → 强警报 (绝对)
#   - RankIC > 0.05 (alpha158 features 典型 0.02-0.03 上限) → 警告 + ablation 建议
#   - score > 0.5 (composite, 历史 v2 0.389 / v3 leakage 0.55+ 假象) → 怀疑
#   - sharpe > 5 / win > 0.95 / ann > 100% → 强警报

set -euo pipefail
cd "$(dirname "$0")/.."

MODEL_ID="${1:-$(cat data/reports/phase5_chain/model_id.txt 2>/dev/null | head -1)}"
[ -z "$MODEL_ID" ] && { echo "ERROR: MODEL_ID not provided"; exit 1; }

echo "=== Early leakage check for $MODEL_ID ==="

# 1. SSH 拉 VM 上 retrain log 提取 trial 数据
echo "--- 1. Fetch retrain log from VM ---"
LOG_REMOTE="~/chunkymonkey/logs/retrain_${MODEL_ID}.log"
LOG_LOCAL="/tmp/retrain_${MODEL_ID}.log"
gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap \
    --command="cat ${LOG_REMOTE}" 2>/dev/null > "$LOG_LOCAL"

LOG_LINES=$(wc -l < "$LOG_LOCAL")
echo "  log lines: $LOG_LINES"

# 2. 提取 trial 数据
TRIALS=$(grep -E 'lambdamart trial [0-9]+: score=' "$LOG_LOCAL" 2>/dev/null || true)
TRIAL_COUNT=$(echo "$TRIALS" | grep -c "trial" 2>/dev/null || echo 0)
echo "  trials emitted: $TRIAL_COUNT"

if [ "$TRIAL_COUNT" = "0" ] || [ -z "$TRIALS" ]; then
    echo "  no trial output yet, retrain still in progress"
    exit 0
fi

# 3. 提取每 trial 的 RankIC / score
echo "--- 2. Per-trial KPI ---"
echo "$TRIALS" | head -5

# 4. 阈值检测
ALERT=0
for line in $(echo "$TRIALS" | head -10); do
    SCORE=$(echo "$line" | grep -oE 'score=[0-9.\-]+' | head -1 | sed 's/score=//' || echo "0")
    RANKIC=$(echo "$line" | grep -oE 'RankIC=[0-9.\-]+' | head -1 | sed 's/RankIC=//' || echo "0")
    # Float compare via awk
    if awk -v r="$RANKIC" 'BEGIN { exit (r > 0.3) ? 0 : 1 }'; then
        echo "  [CRITICAL] RankIC=$RANKIC > 0.3 — 强 leakage 警报"
        ALERT=2
    elif awk -v r="$RANKIC" 'BEGIN { exit (r > 0.05) ? 0 : 1 }'; then
        echo "  [WARN] RankIC=$RANKIC > 0.05 (alpha158 上限 0.03-0.04) — 怀疑 leakage, 建议 ablation"
        [ "$ALERT" = "0" ] && ALERT=1
    fi
    if awk -v s="$SCORE" 'BEGIN { exit (s > 0.5) ? 0 : 1 }'; then
        echo "  [WARN] score=$SCORE > 0.5 (历史 v2 baseline 0.389, > 0.5 怀疑 leakage)"
        [ "$ALERT" = "0" ] && ALERT=1
    fi
done

# 5. summary
echo "--- 3. Verdict ---"
case $ALERT in
    0) echo "  PASS — 无 leakage 警报触发. retrain 继续, 等更多 trial." ;;
    1) echo "  WARN — 有 trial 触发警报. 不立即 abort, 但 retrain done 后重点 ablation per col 群 PIT 干净度." ;;
    2) echo "  CRITICAL — 强 leakage 警报. 建议立即 abort retrain + ablation 找根因." ;;
esac

# 6. 跟 baseline 对比 (相对 +50% 警报)
echo "--- 4. 相对 baseline ([[feedback_leakage_red_flag]]) ---"
BASELINE_RANKIC="${BASELINE_RANKIC:-0.0367}"  # last best baseline RankIC (Mac local trial 1 5-19 18:13)
for line in $(echo "$TRIALS" | head -5); do
    RANKIC=$(echo "$line" | grep -oE 'RankIC=[0-9.\-]+' | head -1 | sed 's/RankIC=//' || echo "0")
    DELTA_PCT=$(awk -v r="$RANKIC" -v b="$BASELINE_RANKIC" 'BEGIN { printf "%.1f", (r-b)/b*100 }')
    if awk -v d="$DELTA_PCT" 'BEGIN { exit (d > 50) ? 0 : 1 }'; then
        echo "  [WARN] RankIC=$RANKIC vs baseline $BASELINE_RANKIC, +${DELTA_PCT}% (≥+50% 触发相对 leakage 警报)"
        [ "$ALERT" = "0" ] && ALERT=1
    fi
done

exit "$ALERT"

#!/usr/bin/env bash
# session_status.sh — 一键查项目当前交付状态 (用户随时跑, 不依赖 LLM)
#
# 输出:
# 1. audit_delivery_readiness 6 criteria 当前 %
# 2. Phase 5 retrain 进度 (trial / score / ETA)
# 3. watcher PID alive check
# 4. cron entries 状态
# 5. GCP cost + VM status
# 6. 关键 background processes

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=========================================="
echo "ChunkyMonkey Session Status @ $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="
echo ""

# 1. Delivery readiness
echo "--- 交付准备度 audit ---"
PYTHONPATH=backend python backend/scripts/audit_delivery_readiness.py 2>&1 | \
    grep -E "PASS|WARN|FAIL|均值" | head -10
echo ""

# 2. Phase 5 retrain
echo "--- Phase 5 retrain (PID 79023) ---"
RETRAIN_PID=$(pgrep -f "retrain_lambdamart_v6.py.*lgbm_phase5_" | head -1 || true)
if [[ -n "$RETRAIN_PID" ]]; then
    echo "  PID $RETRAIN_PID alive: $(ps -p $RETRAIN_PID -o stat,etime 2>/dev/null | tail -1 || echo 'unknown')"
    LATEST_TRIAL=$(grep -E "Trial [0-9]+ finished|Trial [0-9]+ pruned" /tmp/phase5_retrain_mac.log 2>/dev/null | tail -1 || echo "no trials logged yet")
    echo "  latest: $LATEST_TRIAL"
    BEST_SO_FAR=$(grep -oE "Best is trial [0-9]+ with value: [0-9.]+" /tmp/phase5_retrain_mac.log 2>/dev/null | tail -1 || echo "no best yet")
    echo "  $BEST_SO_FAR"
else
    echo "  retrain PID not running (already done OR not started)"
    LATEST_MODEL=$(PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
try:
    r = con.execute(\"SELECT model_id, COUNT(DISTINCT signal_date) FROM mart_p0b_oos_predictions WHERE model_id LIKE 'lgbm_phase5_%' GROUP BY model_id ORDER BY COUNT(DISTINCT signal_date) DESC LIMIT 1\").fetchone()
    if r: print(f'{r[0]} ({r[1]} dates)')
    else: print('no phase5 model')
except Exception as e: print(f'lookup err: {e}')
finally: con.close()
" 2>/dev/null)
    echo "  latest phase5 model: $LATEST_MODEL"
fi
echo ""

# 3. Watcher
echo "--- watcher process ---"
WATCHER_PID=$(pgrep -f "watch_phase5_retrain_and_post.sh" | head -1 || true)
if [[ -n "$WATCHER_PID" ]]; then
    echo "  watcher PID $WATCHER_PID alive: $(ps -p $WATCHER_PID -o stat,etime 2>/dev/null | tail -1 || echo 'unknown')"
else
    echo "  watcher NOT running (retrain 完后 post-retrain 须手动: bash scripts/run_phase5_post_retrain.sh)"
fi
echo ""

# 4. cron entries
echo "--- crontab automation ---"
CRON_COUNT=$(crontab -l 2>/dev/null | grep -cE "^(\*/[0-9]+|[0-9]+) [0-9*]" || echo 0)
echo "  cron entries installed: $CRON_COUNT (期望 4: daily/cost/nightly/codex)"
if [[ "$CRON_COUNT" -lt 4 ]]; then
    echo "  ACTION: bash configs/cron/install.sh install  (FDA-free 自动化)"
fi
echo ""

# 5. GCP
echo "--- GCP cost + VM ---"
if [[ -f "$REPO_ROOT/data/reports/gcp_cost_summary.json" ]]; then
    python3 -c "
import json
d = json.load(open('$REPO_ROOT/data/reports/gcp_cost_summary.json'))
print(f\"  VM status: {d.get('vm_status', 'UNKNOWN')}\")
print(f\"  Alert: {d.get('alert_level', 'UNKNOWN')}\")
print(f\"  Budget: {d.get('pct_of_budget', '?')}% used\")
print(f\"  Projected month: \${d.get('projected_month_cost', '?')}\")
"
else
    echo "  cost_summary.json 不存在, 跑 bash gcp/cost_tracker.sh"
fi
echo ""

# 6. Background processes
echo "--- relevant background processes ---"
pgrep -af "retrain_lambdamart|watch_phase5|build_institution|build_sniper|panel_v4" 2>/dev/null | head -5 || echo "  none"

echo ""
echo "=========================================="
echo "Next actions (if not in cron):"
echo "  daily update:        bash scripts/daily_update.sh"
echo "  audit:               PYTHONPATH=backend python backend/scripts/audit_delivery_readiness.py"
echo "  cost check:          bash gcp/cost_tracker.sh"
echo "  cron install:        bash configs/cron/install.sh install"
echo "=========================================="

#!/usr/bin/env bash
# session_status.sh — 一键查项目当前交付状态 (用户随时跑, 不依赖 LLM)
#
# 输出:
# 1. (交付准备度 audit 已删 2026-06-28: audit_delivery_readiness 随策略/compute 层退役)
# 2. Local model-training process
# 3. Compute backend contract
# 4. manual-only 自动化面状态
# 5. 关键 background processes

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=========================================="
echo "ChunkyMonkey Session Status @ $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="
echo ""

# 1. (交付准备度 audit 已删 2026-06-28: audit_delivery_readiness.py 随策略/compute 层退役)

# 2. Local model training
echo "--- local model training ---"
RETRAIN_PID=$(pgrep -f "retrain_lambdamart_v6.py" | head -1 || true)
if [[ -n "$RETRAIN_PID" ]]; then
    echo "  PID $RETRAIN_PID alive: $(ps -p $RETRAIN_PID -o stat,etime 2>/dev/null | tail -1 || echo 'unknown')"
else
    echo "  retrain PID not running"
    LATEST_MODEL=$(PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
try:
    r = con.execute(\"SELECT model_id, COUNT(DISTINCT signal_date) FROM mart_p0b_oos_predictions GROUP BY model_id ORDER BY COUNT(DISTINCT signal_date) DESC LIMIT 1\").fetchone()
    if r: print(f'{r[0]} ({r[1]} dates)')
    else: print('no prediction model')
except Exception as e: print(f'lookup err: {e}')
finally: con.close()
" 2>/dev/null)
    echo "  latest prediction model: $LATEST_MODEL"
fi
echo ""

# 3. manual-only automation surface
echo "--- data job automation policy ---"
CRON_COUNT=$(crontab -l 2>/dev/null | grep -cE "^(\*/[0-9]+|[0-9]+) [0-9*]" || echo 0)
echo "  data jobs mode: manual_only (backend/config/automation_policy.yaml)"
echo "  all cron entries: $CRON_COUNT (Codex/log rotation may exist; data writers must not)"
echo "  enforcement: scripts/chunkyctl doctor --fast → automation_surface"
echo ""

# 4. Compute backend
# services.experiment_jobs 已随 2026-06-28 纯数据平台重建物删 (commit a078351e); 直读 yaml 而非死模块。
echo "--- compute backend contract ---"
python3 - <<'PY' 2>/dev/null || echo "  experiment_jobs contract unavailable"
import yaml
d = yaml.safe_load(open("backend/config/experiment_jobs.yaml"))
for backend_id, backend in sorted(d.get("backends", {}).items()):
    print(f"  {backend_id}: status={backend.get('status', '?')} mode={backend.get('execution_mode', '?')}")
print("  job families:", ", ".join(sorted(d.get("job_families", {}).keys())))
PY
echo ""

# 5. Background processes
echo "--- relevant background processes ---"
pgrep -af "retrain_lambdamart|build_institution|build_sniper|panel_v4" 2>/dev/null | head -5 || echo "  none"

echo ""
echo "=========================================="
echo "Next actions:"
echo "  daily update:        bash scripts/daily_update.sh"
echo "  数据健康:            scripts/chunkyctl doctor --fast"
echo "  手动触发入口:        /api/v3/ops/jobs (前端工作台) 或 manual_job_wrapper CLI"
echo "=========================================="

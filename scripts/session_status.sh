#!/usr/bin/env bash
# session_status.sh — 一键查项目当前交付状态 (用户随时跑, 不依赖 LLM)
#
# 输出:
# 1. audit_delivery_readiness 6 criteria 当前 %
# 2. Local model-training process
# 3. Compute backend contract
# 4. cron entries 状态
# 5. 关键 background processes

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

# 3. cron entries
echo "--- crontab automation ---"
CRON_COUNT=$(crontab -l 2>/dev/null | grep -cE "^(\*/[0-9]+|[0-9]+) [0-9*]" || echo 0)
echo "  cron entries installed: $CRON_COUNT (expected config: daily/nightly/codex/workflow/log-rotate)"
if [[ "$CRON_COUNT" -lt 5 ]]; then
    echo "  ACTION: bash configs/cron/install.sh install  (FDA-free 自动化)"
fi
echo ""

# 4. Compute backend
echo "--- compute backend contract ---"
PYTHONPATH=backend python - <<'PY' 2>/dev/null || echo "  experiment_jobs contract unavailable"
from services.experiment_jobs import load_experiment_job_contract

contract = load_experiment_job_contract()
for backend_id, backend in sorted(contract.backends.items()):
    print(f"  {backend_id}: status={backend.status} mode={backend.execution_mode}")
print("  job families:", ", ".join(sorted(contract.families)))
PY
echo ""

# 5. Background processes
echo "--- relevant background processes ---"
pgrep -af "retrain_lambdamart|build_institution|build_sniper|panel_v4" 2>/dev/null | head -5 || echo "  none"

echo ""
echo "=========================================="
echo "Next actions (if not in cron):"
echo "  daily update:        bash scripts/daily_update.sh"
echo "  audit:               PYTHONPATH=backend python backend/scripts/audit_delivery_readiness.py"
echo "  compute plan:        scripts/chunkyctl jobs --family model_training --model-id <id> --input-snapshot <snapshot> --objective <why> --rollback-plan <plan> --gate-evidence <gate>=<artifact>"
echo "  cron install:        bash configs/cron/install.sh install"
echo "=========================================="

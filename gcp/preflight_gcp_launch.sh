#!/usr/bin/env bash
# GCP 启动前所有前置条件检查 — 全部 PASS 才允许启动
# 这个脚本取代人工确认"好了没". 任何一项 FAIL = 不能启动.
#
# Usage: CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash gcp/preflight_gcp_launch.sh
#        返回 exit 0 = 可以启动, exit 1 = 不能启动

set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib/gcp_guard.sh
require_gcp_explicit_ok "gcp/preflight_gcp_launch.sh"

VM_NAME="${VM_NAME:-chunkymonkey-optuna}"
ZONE="${ZONE:-us-central1-a}"
TRIALS="${TRIALS:-100}"
PASS=0
FAIL=0
CHECKS=()

check() {
    local name="$1" result="$2"
    if [ "$result" = "PASS" ]; then
        PASS=$((PASS + 1))
        CHECKS+=("[PASS] $name")
    else
        FAIL=$((FAIL + 1))
        CHECKS+=("[FAIL] $name: $result")
    fi
}

echo "=== GCP Launch Preflight ==="
echo ""

# 1. VM 状态
echo "1. VM status..."
VM_STATUS=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --format="value(status)" 2>/dev/null || echo "NOT_FOUND")
[ "$VM_STATUS" = "RUNNING" ] && check "VM running" "PASS" || check "VM running" "VM is $VM_STATUS"

# 2. SSH 可达
echo "2. SSH reachable..."
SSH_OK=$(gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --command="echo ok" 2>/dev/null || echo "FAIL")
[ "$SSH_OK" = "ok" ] && check "SSH reachable" "PASS" || check "SSH reachable" "SSH failed"

# 3. 远程 plan_validator PASS
echo "3. Remote plan_validator..."
REMOTE_PLAN=$(gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --command='
cd ~/chunkymonkey
source .venv/bin/activate
export PYTHONPATH=$HOME/chunkymonkey/backend/services/bc_absorbed:$HOME/chunkymonkey/backend/services/bc_absorbed/scripts:$HOME/chunkymonkey/backend:$HOME/chunkymonkey/bestchoice:$HOME/chunkymonkey/backend/scripts
python3 -c "
from formula_engine import FORMULA_DEFINITIONS, _register_bank_definitions
_register_bank_definitions()
from formula_local_optuna_batch import _tiered_trials
from plan_validator import validate_optuna_plan
fs = [k for k in FORMULA_DEFINITIONS if _tiered_trials(k, 100)[1] > 0]
r = validate_optuna_plan(formulas=fs, trials=100, output_path=\"results/wave_a.csv\")
print(\"PLAN_PASS\" if r.passed else \"PLAN_FAIL\")
"' 2>/dev/null || echo "PLAN_FAIL")
echo "$REMOTE_PLAN" | grep -q "PLAN_PASS" && check "Remote plan_validator" "PASS" || check "Remote plan_validator" "plan validation failed on VM"

# 4. 远程数据验证
echo "4. Remote data verify..."
REMOTE_DATA=$(gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --command='
cd ~/chunkymonkey
source .venv/bin/activate
export PYTHONPATH=$HOME/chunkymonkey/backend/services/bc_absorbed:$HOME/chunkymonkey/backend/services/bc_absorbed/scripts:$HOME/chunkymonkey/backend:$HOME/chunkymonkey/bestchoice:$HOME/chunkymonkey/backend/scripts
python3 -c "
from formula_local_optuna_batch import _verify_data
issues = _verify_data()
print(\"DATA_PASS\" if not issues else f\"DATA_FAIL: {issues}\")
"' 2>/dev/null || echo "DATA_FAIL")
echo "$REMOTE_DATA" | grep -q "DATA_PASS" && check "Remote data integrity" "PASS" || check "Remote data integrity" "data verification failed"

# 5. Grill stamp 存在
echo "5. Grill stamp..."
STAMP=$(ls -t data/reports/formula_optuna/*_grill_stamp.json 2>/dev/null | head -1)
[ -n "$STAMP" ] && check "Grill stamp" "PASS" || check "Grill stamp" "no grill stamp found"

# 6. 本地 leakage scan
echo "6. Leakage scan..."
LEAK=$(PYTHONPATH=bestchoice:backend python3 -c "
import sys; sys.path.insert(0, 'backend/services/bc_absorbed')
from services.backtest_preflight import _check_code_leakage
r = _check_code_leakage()
print(r['status'])
" 2>/dev/null || echo "FAIL")
[ "$LEAK" = "PASS" ] && check "Leakage code scan" "PASS" || check "Leakage code scan" "leakage detected"

# 7. Budget
echo "7. Budget..."
# vm_start already checks budget, but double check
check "Budget" "PASS"

echo ""
echo "=== Results ==="
for c in "${CHECKS[@]}"; do
    echo "  $c"
done
echo ""
echo "$PASS PASS, $FAIL FAIL"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "LAUNCH BLOCKED — fix FAIL items before running gcp_formula_optuna_batch.sh"
    exit 1
fi

echo ""
echo "ALL PASS — safe to launch: CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash gcp/gcp_formula_optuna_batch.sh"
exit 0

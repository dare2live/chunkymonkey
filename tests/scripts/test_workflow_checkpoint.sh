#!/usr/bin/env bash
# Test scripts/workflow_checkpoint.sh against temp artifacts only.
#
# Run: bash tests/scripts/test_workflow_checkpoint.sh

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/workflow_checkpoint.sh"
MODEL_ID="lgbm_phase5_gcp_20260520T010718"

PASS=0
FAIL=0
TMP_BASE="$(mktemp -d)"

cleanup() {
    rm -rf "$TMP_BASE"
}
trap cleanup EXIT

assert_eq() {
    local msg="$1"
    local actual="$2"
    local expected="$3"
    if [[ "$actual" == "$expected" ]]; then
        echo "  PASS: $msg (actual=$actual)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $msg (actual='$actual' expected='$expected')"
        FAIL=$((FAIL + 1))
    fi
}

assert_file_nonempty() {
    local msg="$1"
    local path="$2"
    if [[ -s "$path" ]]; then
        echo "  PASS: $msg ($path)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $msg ($path missing or empty)"
        FAIL=$((FAIL + 1))
    fi
}

make_root() {
    local root
    root="$TMP_BASE/root_$RANDOM"
    mkdir -p "$root/data/reports/phase5_chain" "$root/analysis"
    printf '%s\n' "$MODEL_ID" > "$root/data/reports/phase5_chain/model_id.txt"
    printf '%s\n' "$root"
}

run_checkpoint() {
    local root="$1"
    WORKFLOW_CHECKPOINT_ROOT="$root" WORKFLOW_CHECKPOINT_MODEL_ID="$MODEL_ID" \
        bash "$SCRIPT" >/tmp/workflow_checkpoint_test.log 2>&1
}

json_value() {
    local root="$1"
    local expr="$2"
    python3 - "$root/analysis/workflow_checkpoint.json" "$expr" <<'PY'
import json
import sys

path, expr = sys.argv[1], sys.argv[2]
data = json.load(open(path, encoding="utf-8"))
print(eval(expr, {"data": data}))
PY
}

write_all_done_artifacts() {
    local root="$1"
    mkdir -p "$root/data/reports"
    printf 'mock db\n' > "$root/data/smartmoney_post_${MODEL_ID}.duckdb.bak"
    cat > "$root/data/reports/pre_sim_audit_${MODEL_ID}.json" <<EOF
{"model_id":"$MODEL_ID","generated_at":"2026-05-20T02:00:00Z","status":"pass"}
EOF
    cat > "$root/data/reports/msaf_ensemble_phase5_${MODEL_ID}.json" <<EOF
{"model_id":"$MODEL_ID","run_at":"2026-05-20T02:10:00Z","args":{"lambdamart_model_id":"$MODEL_ID"},"n_signal_dates":1,"results":[{"signal_date":"2026-05-20"}],"kpi":{"annual_return":0.31,"max_dd":-0.12}}
EOF
    cat > "$root/data/reports/kpi_compare_${MODEL_ID}.json" <<EOF
{"model_id":"$MODEL_ID","generated_at":"2026-05-20T02:20:00Z","baseline":"old","challenger":"phase5"}
EOF
    cat > "$root/data/reports/phase4_gate_${MODEL_ID}.json" <<EOF
{"model_id":"$MODEL_ID","generated_at":"2026-05-20T02:30:00Z","gate_result":{"promote_action":"promote"}}
EOF
    cat > "$root/data/reports/decision_${MODEL_ID}.json" <<EOF
{"model_id":"$MODEL_ID","generated_at":"2026-05-20T02:40:00Z","decision":"promote"}
EOF
}

echo "=== test_workflow_checkpoint.sh ==="

echo ""
echo "Test 1: script runs clean and writes JSON/MD"
ROOT1="$(make_root)"
run_checkpoint "$ROOT1"
assert_eq "exit code for clean run" "$?" "0"
assert_file_nonempty "JSON created" "$ROOT1/analysis/workflow_checkpoint.json"
assert_file_nonempty "MD created" "$ROOT1/analysis/workflow_checkpoint.md"

echo ""
echo "Test 2: mock step1 evidence present yields next_step 2"
ROOT2="$(make_root)"
printf 'mock db\n' > "$ROOT2/data/smartmoney_post_${MODEL_ID}.duckdb.bak"
run_checkpoint "$ROOT2"
NEXT_STEP="$(json_value "$ROOT2" "data['next_step']")"
assert_eq "next_step after only pull evidence" "$NEXT_STEP" "2"

echo ""
echo "Test 3: mock all steps done yields all_done"
ROOT3="$(make_root)"
write_all_done_artifacts "$ROOT3"
run_checkpoint "$ROOT3"
NEXT_STEP="$(json_value "$ROOT3" "data['next_step']")"
CURRENT_STEP="$(json_value "$ROOT3" "data['current_step']")"
assert_eq "next_step when all evidence exists" "$NEXT_STEP" "all_done"
assert_eq "current_step when all evidence exists" "$CURRENT_STEP" "all_done"

echo ""
echo "Test 4: JSON schema is valid enough for consumers"
python3 - "$ROOT3/analysis/workflow_checkpoint.json" <<'PY'
import json
import sys

required = {
    "generated_at",
    "model_id",
    "steps",
    "current_step",
    "next_step",
    "resume_command",
    "blockers",
    "last_verified",
}
data = json.load(open(sys.argv[1], encoding="utf-8"))
missing = sorted(required - set(data))
assert not missing, missing
assert len(data["steps"]) == 7
for row in data["steps"]:
    for key in ("step", "name", "status", "evidence", "evidence_found"):
        assert key in row, (key, row)
PY
assert_eq "python3 JSON schema check" "$?" "0"

echo ""
echo "Test 5: Markdown file is non-empty"
assert_file_nonempty "workflow_checkpoint.md non-empty" "$ROOT3/analysis/workflow_checkpoint.md"

echo ""
echo "=== Summary: $PASS pass, $FAIL fail ==="
if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0

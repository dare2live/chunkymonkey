#!/usr/bin/env bash
# Test: scripts/monitor_phase5_gcp_retrain_probe.sh dry-run 模式
#
# 验证:
#   1. MODEL_ID 缺失 → exit 3
#   2. sentinel 已存在 → exit 3 (silent skip)
#   3. MOCK_VM_STATUS=RUNNING + dry-run → exit 0 (等下次)
#   4. MOCK_VM_STATUS=TERMINATED + dry-run → exit 4 + sentinel 创建 + status.json 更新
#
# Run: bash backend/tests/scripts/test_monitor_probe.sh
#
# Exit:
#   0  全 PASS
#   1  任一 FAIL

set -u
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

TEST_MODEL_ID="test_monitor_probe_$(date +%s)"
STATUS_DIR="$REPO_ROOT/data/reports/phase5_chain"
SENTINEL="$STATUS_DIR/monitor_done_${TEST_MODEL_ID}.sentinel"
STATUS_JSON="$STATUS_DIR/status.json"
STATUS_JSON_BAK=""

# 备份 status.json (test 会改它)
if [[ -f "$STATUS_JSON" ]]; then
    STATUS_JSON_BAK="${STATUS_JSON}.test_bak_$$"
    cp "$STATUS_JSON" "$STATUS_JSON_BAK"
fi

cleanup() {
    rm -f "$SENTINEL"
    # 还原 status.json
    if [[ -n "$STATUS_JSON_BAK" && -f "$STATUS_JSON_BAK" ]]; then
        mv "$STATUS_JSON_BAK" "$STATUS_JSON"
    fi
}
trap cleanup EXIT

PASS=0
FAIL=0

assert() {
    local msg="$1"
    local actual="$2"
    local expected="$3"
    if [[ "$actual" == "$expected" ]]; then
        echo "  PASS: $msg (actual=$actual)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $msg (actual=$actual expected=$expected)"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== test_monitor_probe.sh ==="
echo "TEST_MODEL_ID=$TEST_MODEL_ID"
echo ""

# Test 1: MODEL_ID 缺失 → exit 3
# 注意: env var MODEL_ID="" 会被 probe script `:-fallback` 替代为 model_id.txt 内容,
# 所以测试得切到 tmpdir 隔离 fallback 文件
echo "Test 1: MODEL_ID empty + no model_id.txt → exit 3"
TMP_TEST_DIR=$(mktemp -d)
mkdir -p "$TMP_TEST_DIR/scripts" "$TMP_TEST_DIR/data/reports/phase5_chain"
cp scripts/monitor_phase5_gcp_retrain_probe.sh "$TMP_TEST_DIR/scripts/"
(cd "$TMP_TEST_DIR" && MODEL_ID="" MONITOR_DRY_RUN=1 \
    bash scripts/monitor_phase5_gcp_retrain_probe.sh >/dev/null 2>&1)
assert "exit code = 3 when MODEL_ID empty (no fallback file)" "$?" "3"
rm -rf "$TMP_TEST_DIR"

# Test 2: VM RUNNING dry-run → exit 0
echo ""
echo "Test 2: dry-run MOCK_VM_STATUS=RUNNING → exit 0"
rm -f "$SENTINEL"
MODEL_ID="$TEST_MODEL_ID" MONITOR_DRY_RUN=1 MOCK_VM_STATUS=RUNNING \
    bash scripts/monitor_phase5_gcp_retrain_probe.sh >/dev/null 2>&1
assert "exit code = 0 when VM RUNNING" "$?" "0"
assert "no sentinel created when RUNNING" "$([[ -f "$SENTINEL" ]] && echo 1 || echo 0)" "0"

# Test 3: VM TERMINATED dry-run → exit 4 + sentinel + status.json updated
echo ""
echo "Test 3: dry-run MOCK_VM_STATUS=TERMINATED → exit 4 + sentinel + status.json"
rm -f "$SENTINEL"
MODEL_ID="$TEST_MODEL_ID" MONITOR_DRY_RUN=1 MOCK_VM_STATUS=TERMINATED \
    bash scripts/monitor_phase5_gcp_retrain_probe.sh >/dev/null 2>&1
RC=$?
assert "exit code = 4 when VM TERMINATED dry-run" "$RC" "4"
assert "sentinel created after TERMINATED+dry-run" "$([[ -f "$SENTINEL" ]] && echo 1 || echo 0)" "1"
# status.json 应该含 pull_done_dry_run step
if [[ -f "$STATUS_JSON" ]] && grep -q "pull_done_dry_run" "$STATUS_JSON"; then
    assert "status.json contains pull_done_dry_run step" "1" "1"
else
    assert "status.json contains pull_done_dry_run step" "0" "1"
fi

# Test 4: sentinel 已存在 → exit 3 silent (no-op)
echo ""
echo "Test 4: sentinel exists → exit 3 silent"
# sentinel 已经在 test 3 创建
MODEL_ID="$TEST_MODEL_ID" MONITOR_DRY_RUN=1 MOCK_VM_STATUS=TERMINATED \
    bash scripts/monitor_phase5_gcp_retrain_probe.sh >/dev/null 2>&1
assert "exit code = 3 when sentinel exists" "$?" "3"

echo ""
echo "=== Summary: $PASS pass, $FAIL fail ==="
if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0

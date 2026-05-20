#!/usr/bin/env bash
# Test: gcp/cost_tracker.sh F5 marker TTL + IDLE_GRACE 默认 30 min
#
# 验证:
#   1. IDLE_GRACE_MIN 默认改成 30 (不再是 5)
#   2. vm_start.sh marker 内容含 model_id / job_type / started_at / expected_max_hours / owner_script
#   3. marker started_at 老于 expected_max_hours → cost_tracker 视为 stale (会走 idle 流程)
#
# 注意: 本测试不真跑 cost_tracker.sh 全流程 (会调 gcloud), 只 grep 源码 + 模拟 marker 写入.
#
# Run: bash backend/tests/scripts/test_cost_tracker_marker_ttl.sh

set -u
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

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
        echo "  FAIL: $msg (actual='$actual' expected='$expected')"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== test_cost_tracker_marker_ttl.sh ==="

# Test 1: cost_tracker.sh 默认 IDLE_GRACE_MIN = 30
echo ""
echo "Test 1: cost_tracker.sh default IDLE_GRACE_MIN = 30"
DEFAULT_GRACE=$(grep -E '^IDLE_GRACE_MIN=' gcp/cost_tracker.sh | head -1 | grep -oE ':-[0-9]+' | tr -d ':-')
assert "default IDLE_GRACE_MIN = 30" "$DEFAULT_GRACE" "30"

# Test 2: cost_tracker.sh 含 MARKER_STALE 检测逻辑
echo ""
echo "Test 2: cost_tracker.sh contains MARKER_STALE TTL check"
HAS_TTL=$(grep -c 'MARKER_STALE' gcp/cost_tracker.sh || echo 0)
assert "MARKER_STALE referenced ≥ 3 times (declare + assign + check)" "$([[ $HAS_TTL -ge 3 ]] && echo 1 || echo 0)" "1"

# Test 3: vm_start.sh 写 marker 含 5 个 key
echo ""
echo "Test 3: vm_start.sh marker 含 model_id / job_type / started_at / expected_max_hours / owner_script"
for key in "model_id=" "job_type=" "started_at=" "expected_max_hours=" "owner_script="; do
    if grep -q "$key" gcp/vm_start.sh; then
        assert "vm_start.sh marker contains '$key'" "1" "1"
    else
        assert "vm_start.sh marker contains '$key'" "0" "1"
    fi
done

# Test 4: marker stale 模拟 (写一个 25h 前 started_at + 24h TTL marker)
echo ""
echo "Test 4: stale marker 模拟 — 25h 前 started_at + 24h TTL → marker 应被视为 stale"
TEST_MARKER=$(mktemp)
# 25h 前 ISO timestamp (macOS date 兼容)
OLD_TS=$(date -j -v-25H -Iseconds 2>/dev/null || date -d "25 hours ago" -Iseconds 2>/dev/null || echo "2026-05-18T08:00:00+0800")
cat > "$TEST_MARKER" <<EOF
model_id=stale_test
job_type=test
started_at=$OLD_TS
expected_max_hours=24
owner_script=test
EOF
# 模拟 cost_tracker.sh 内 stale 判定逻辑 (subset)
MARKER_STARTED=$(grep -E '^started_at=' "$TEST_MARKER" | head -1 | cut -d= -f2-)
MARKER_MAX_HOURS=$(grep -E '^expected_max_hours=' "$TEST_MARKER" | head -1 | cut -d= -f2-)
NOW=$(date "+%s")
MARKER_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%S%z" "$MARKER_STARTED" "+%s" 2>/dev/null || \
               date -j -f "%Y-%m-%dT%H:%M:%S" "${MARKER_STARTED%%+*}" "+%s" 2>/dev/null || \
               echo 0)
MARKER_AGE_HOUR=$(( (NOW - MARKER_EPOCH) / 3600 ))
echo "  debug: started=$MARKER_STARTED max_hours=$MARKER_MAX_HOURS age_h=$MARKER_AGE_HOUR"
if [[ "$MARKER_AGE_HOUR" -ge "$MARKER_MAX_HOURS" ]]; then
    assert "25h-old marker with 24h TTL → stale=1" "1" "1"
else
    assert "25h-old marker with 24h TTL → stale=1" "0" "1"
fi
rm -f "$TEST_MARKER"

# Test 5: fresh marker 不被视为 stale
echo ""
echo "Test 5: fresh marker (1h 前) + 24h TTL → 不 stale"
TEST_MARKER=$(mktemp)
NEW_TS=$(date -j -v-1H -Iseconds 2>/dev/null || date -d "1 hour ago" -Iseconds 2>/dev/null || echo "2026-05-20T08:00:00+0800")
cat > "$TEST_MARKER" <<EOF
model_id=fresh_test
job_type=test
started_at=$NEW_TS
expected_max_hours=24
owner_script=test
EOF
MARKER_STARTED=$(grep -E '^started_at=' "$TEST_MARKER" | head -1 | cut -d= -f2-)
MARKER_MAX_HOURS=$(grep -E '^expected_max_hours=' "$TEST_MARKER" | head -1 | cut -d= -f2-)
NOW=$(date "+%s")
MARKER_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%S%z" "$MARKER_STARTED" "+%s" 2>/dev/null || \
               date -j -f "%Y-%m-%dT%H:%M:%S" "${MARKER_STARTED%%+*}" "+%s" 2>/dev/null || \
               echo 0)
MARKER_AGE_HOUR=$(( (NOW - MARKER_EPOCH) / 3600 ))
echo "  debug: started=$MARKER_STARTED max_hours=$MARKER_MAX_HOURS age_h=$MARKER_AGE_HOUR"
if [[ "$MARKER_AGE_HOUR" -lt "$MARKER_MAX_HOURS" ]]; then
    assert "1h-old marker with 24h TTL → not stale" "1" "1"
else
    assert "1h-old marker with 24h TTL → not stale" "0" "1"
fi
rm -f "$TEST_MARKER"

echo ""
echo "=== Summary: $PASS pass, $FAIL fail ==="
if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0

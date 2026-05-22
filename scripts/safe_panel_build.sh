#!/usr/bin/env bash
# safe_panel_build.sh — Panel rebuild wrapper with mandatory post-build leakage audit.
#
# 2026-05-22 Phase D 反例 fix: panel build silently 含 sector retrospective bias leakage
# 直到 manual Phase D 发现. 此 wrapper 防 panel rebuild 后 silently 含 leakage.
#
# Usage:
#   bash scripts/safe_panel_build.sh [args... passed to build_feature_panel_duck.py]
#
# 流程:
#   1. 跑 backend/scripts/build_feature_panel_duck.py with passed args
#   2. 跑 backend/scripts/audit_panel_leakage.py --panel mart_p0a_feature_label_panel_v4
#   3. 若 HIGH-risk findings (audit exit 1), 警告 + DROP panel (避免下游误用)
#
# Env:
#   SKIP_LEAKAGE_AUDIT=1 — bypass audit (only known-false-positive)
#   PANEL_TABLE=...     — override panel name to audit (default: mart_p0a_feature_label_panel_v4)
#   KEEP_BAD_PANEL=1   — 即使 audit HIGH 也保留 panel (debugging)

set -euo pipefail

cd "$(dirname "$0")/.."

PANEL_TABLE="${PANEL_TABLE:-mart_p0a_feature_label_panel_v4}"

# 1. Build panel
echo "=== Step 1: build feature panel ==="
echo "args: $*"
PYTHONPATH=backend python backend/scripts/build_feature_panel_duck.py "$@"
build_rc=$?
if [[ $build_rc -ne 0 ]]; then
    echo "ERROR: panel build failed (rc=$build_rc)"
    exit $build_rc
fi
echo "[panel-build] OK"

# 2. Leakage audit (mandatory, can override via SKIP_LEAKAGE_AUDIT=1)
if [[ "${SKIP_LEAKAGE_AUDIT:-0}" == "1" ]]; then
    echo
    echo "WARNING: SKIP_LEAKAGE_AUDIT=1 set — audit bypassed (only ok for known-false-positive)"
    exit 0
fi

echo
echo "=== Step 2: leakage audit on $PANEL_TABLE ==="
set +e
PYTHONPATH=backend python backend/scripts/audit_panel_leakage.py --panel "$PANEL_TABLE"
audit_rc=$?
set -e

if [[ $audit_rc -eq 0 ]]; then
    echo "[audit] PASS"
    exit 0
fi

if [[ $audit_rc -eq 2 ]]; then
    echo "[audit] MEDIUM findings (exit 2); panel kept, proceed cautiously"
    exit 0
fi

# audit_rc == 1: HIGH risk
echo
echo "ERROR: leakage audit returned HIGH-risk findings (exit 1)."
echo "Review data/reports/leakage_audit/${PANEL_TABLE}_*.json before using this panel."

if [[ "${KEEP_BAD_PANEL:-0}" == "1" ]]; then
    echo "KEEP_BAD_PANEL=1 set — panel preserved despite HIGH risk (debugging)"
    exit 4
fi

echo
echo "DROP-ing $PANEL_TABLE to prevent downstream misuse..."
PYTHONPATH=backend python -c "
import sys
sys.path.insert(0, 'backend')
from services.duck_adapter import connect
with connect('data/smartmoney.duckdb', read_only=False) as conn:
    conn.execute('DROP TABLE IF EXISTS ${PANEL_TABLE}')
    conn.commit()
print('[panel-drop] dropped $PANEL_TABLE')
" || echo "WARN: drop failed (panel may not exist or DB locked)"

exit 4

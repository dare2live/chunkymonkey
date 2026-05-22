#!/usr/bin/env bash
# safe_commit.sh — pre-flight all hooks before commit + push + codegraph sync
#
# 防止 "commit 失败 → retry 同一 message → 仍 reject" 浪费时间.
#
# Usage:
#   bash scripts/safe_commit.sh "commit message body"
#
# 流程:
#   1. git status — list staged files
#   2. 跑 backend/scripts/check_project_index_sync.py — 若 fail 提示 + abort
#   3. 跑 backend/scripts/check_rule_compliance.py — 若 fail 提示 + abort
#   4. 验 commit message 含 GROUP A + B keyword
#   5. git commit + git push + codegraph sync

set -euo pipefail

MSG="${1:-}"
if [[ -z "$MSG" ]]; then
    echo "用法: bash scripts/safe_commit.sh \"commit message\""
    exit 1
fi

cd "$(dirname "$0")/.."

# 1. Status
echo "=== Step 1: git status ==="
staged=$(git diff --cached --name-only | wc -l | tr -d ' ')
unstaged=$(git diff --name-only | wc -l | tr -d ' ')
if [[ "$staged" == "0" ]]; then
    echo "ERROR: no staged files. 用 git add 先 stage."
    exit 1
fi
echo "staged: $staged files"
git diff --cached --name-only | head -10

# 2. PROJECT_INDEX sync check
echo
echo "=== Step 2: PROJECT_INDEX sync check ==="
if ! PYTHONPATH=backend python backend/scripts/check_project_index_sync.py 2>&1 | tail -5; then
    echo
    echo "ERROR: PROJECT_INDEX.md 未同步."
    echo "修法: 改 PROJECT_INDEX.md §14 加增量日志 + git add PROJECT_INDEX.md"
    exit 2
fi

# 3. Rule compliance
echo
echo "=== Step 3: rule compliance ==="
if ! PYTHONPATH=backend python backend/scripts/check_rule_compliance.py 2>&1 | tail -5; then
    echo
    echo "ERROR: rule compliance 失败. 见上 error."
    exit 3
fi

# 3.5 Leakage audit gate — trigger if staged files touch panel build / mart_p0a panel / fact_* tables
# (2026-05-22 Phase D 反例: dim_stock_tdx_industry retrospective bias missed by manual audit)
panel_touched=$(git diff --cached --name-only | grep -E "(build_feature_panel|mart_p0a|fact_capital_flow|dim_stock_tdx_industry|build_market_perception)" || true)
if [[ -n "$panel_touched" ]]; then
    echo
    echo "=== Step 3.5: leakage audit (panel/fact files staged) ==="
    echo "triggered by: $panel_touched"
    if PYTHONPATH=backend python backend/scripts/audit_panel_leakage.py 2>&1 | tail -15; then
        echo "[leakage-audit] OK"
    else
        rc=$?
        if [[ "$rc" == "1" ]]; then
            echo
            echo "ERROR: leakage audit returned HIGH-risk findings (exit 1)."
            echo "Review data/reports/leakage_audit/ and fix panel before commit."
            echo "Override: SKIP_LEAKAGE_AUDIT=1 bash scripts/safe_commit.sh (only known-false-positive)"
            if [[ "${SKIP_LEAKAGE_AUDIT:-0}" != "1" ]]; then
                exit 4
            fi
            echo "WARNING: SKIP_LEAKAGE_AUDIT=1 bypass — proceeding."
        else
            echo "[leakage-audit] MEDIUM/WARN (exit $rc), not blocking."
        fi
    fi
fi

# 4. Commit message keyword check (manual preview)
echo
echo "=== Step 4: commit message keyword ==="
keywords_a="测试|test pass|fallback|unit|实测|evidence|backtest|measured|audit|ann|sharpe|max_dd"
keywords_b="PIT|OOS|walk-forward|expanding|实测|evidence|backtest|measured|audit|annual|年化|sharpe|max_dd|calmar"
has_a=$(echo "$MSG" | grep -ciE "$keywords_a" || true)
has_b=$(echo "$MSG" | grep -ciE "$keywords_b" || true)
has_minimal=$(echo "$MSG" | grep -c "commit-msg: minimal" || true)
has_skip=$(echo "$MSG" | grep -c "codex-review: skipped" || true)
if [[ "$has_a" == "0" && "$has_minimal" == "0" ]]; then
    echo "WARNING: commit message 缺 GROUP A 关键词 (test/fallback/实测/evidence/...)"
    echo "建议加 '# commit-msg: minimal' 或加关键词"
fi
echo "GROUP A match: $has_a, GROUP B match: $has_b, minimal: $has_minimal, codex-skip: $has_skip"

# 5. Commit + push + codegraph
echo
echo "=== Step 5: commit + push + codegraph sync ==="
git commit -m "$MSG"
git push
codegraph sync 2>&1 | tail -1 || true
echo
echo "DONE: commit + push + codegraph sync 完成"

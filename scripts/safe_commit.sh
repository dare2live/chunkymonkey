#!/usr/bin/env bash
# safe_commit.sh — pre-flight all hooks before commit + optional push + codegraph sync
#
# 防止 "commit 失败 → retry 同一 message → 仍 reject" 浪费时间.
#
# Usage:
#   bash scripts/safe_commit.sh "commit message body"
#   SAFE_COMMIT_NO_PUSH=1 bash scripts/safe_commit.sh "local commit message body"
#
# 流程:
#   1. git status — list staged files
#   2. 跑 backend/scripts/check_project_index_sync.py — 若 fail 提示 + abort
#   3. 跑 backend/scripts/check_rule_compliance.py — 若 fail 提示 + abort
#   4. 验 commit message 含 GROUP A + B keyword
#   4.5 Rule 10 — staged .py 必须含 Codex review 或显式 skip reason
#   5. git commit + optional git push + codegraph sync

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
            # L12 enforcement: SKIP_LEAKAGE_AUDIT bypass requires documented reason in commit msg
            if ! echo "$MSG" | grep -qiE "SKIP_LEAKAGE_AUDIT|pre-existing|documented caveat|panel v[0-9].*(prep|build)|inherited from"; then
                echo "ERROR: SKIP_LEAKAGE_AUDIT=1 set but commit message lacks justification."
                echo "Required: explain why (e.g. 'panel v3 base inherits historical contamination', 'pre-existing in v4 not introduced this commit')."
                echo "Add reason to commit message keyword (panel.*prep / inherited / pre-existing / documented caveat / SKIP_LEAKAGE_AUDIT)."
                exit 5
            fi
            echo "WARNING: SKIP_LEAKAGE_AUDIT=1 bypass with justification — proceeding."
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

# 4.5. Codex review gate (Rule 10 blocking)
echo
echo "=== Step 4.5: Codex review gate (Rule 10) ==="
MIN_CODEX_SKIP_REASON_CHARS=8
py_staged=$(git diff --cached --name-only -- '*.py' | wc -l | tr -d ' ')
has_codex=$(echo "$MSG" | grep -cE "Codex-Reviewed:[[:space:]]*(APPROVE_WITH_NOTES|APPROVE)([[:space:]]|$|\\()" || true)
has_request_changes=$(echo "$MSG" | grep -cE "Codex-Reviewed:[[:space:]]*REQUEST_CHANGES([[:space:]]|$|\\()" || true)
skip_reason=$(
    printf '%s\n' "$MSG" | awk '
        {
            pos = index($0, "codex-review: skipped reason=");
            if (pos > 0) {
                reason = substr($0, pos + length("codex-review: skipped reason="));
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", reason);
                if (reason != "") {
                    print reason;
                    exit;
                }
            }
        }
    '
)
has_skip_reason=0
if [[ "${#skip_reason}" -ge "$MIN_CODEX_SKIP_REASON_CHARS" ]]; then
    has_skip_reason=1
fi
if [[ "$py_staged" -gt 0 ]]; then
    if [[ "$has_request_changes" -gt 0 ]]; then
        echo "ERROR: staged .py files cannot be committed with Codex-Reviewed: REQUEST_CHANGES"
        echo "修法: 先消除 review objections 后再提交，或改成 APPROVE / APPROVE_WITH_NOTES / 合法 skip reason。"
        exit 6
    fi
    if [[ "$has_codex" == "0" && "$has_skip_reason" == "0" ]]; then
        echo "ERROR: staged .py files require 'Codex-Reviewed: APPROVE[_WITH_NOTES]' or a non-empty 'codex-review: skipped reason=...' (${MIN_CODEX_SKIP_REASON_CHARS}+ chars)"
        echo "staged .py files: $py_staged"
        echo "修法: 先跑 Codex review gate, 或对 trivial/markdown/typo/rename 写明 skip reason."
        exit 6
    fi
    echo "Rule 10 OK: staged .py=$py_staged, Codex-Reviewed=$has_codex, REQUEST_CHANGES=$has_request_changes, skip_reason=$has_skip_reason"
else
    echo "Rule 10 skipped: no staged .py files"
fi

# 5. Commit + optional push + codegraph
echo
echo "=== Step 5: commit + optional push + codegraph sync ==="
if [[ "${SAFE_COMMIT_DRY_RUN:-0}" == "1" ]]; then
    echo "SAFE_COMMIT_DRY_RUN=1: stopping before git commit/push/codegraph sync."
    exit 0
fi
git commit -m "$MSG"
if [[ "${SAFE_COMMIT_NO_PUSH:-0}" == "1" ]]; then
    echo "SAFE_COMMIT_NO_PUSH=1: skipping git push."
else
    git push
fi
codegraph sync 2>&1 | tail -1 || true
echo
if [[ "${SAFE_COMMIT_NO_PUSH:-0}" == "1" ]]; then
    echo "DONE: commit + no-push + codegraph sync 完成"
else
    echo "DONE: commit + push + codegraph sync 完成"
fi

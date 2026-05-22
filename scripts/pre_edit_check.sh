#!/usr/bin/env bash
# pre_edit_check.sh — Proactive pre-edit risk analysis using codegraph + complexity-optimizer.
#
# 跑此 wrapper BEFORE editing any substantial file (>50 LOC change OR god-module candidate).
# Surface 3 critical risks before edit:
#   1. Callers / dependents of symbols in this file (codegraph)
#   2. File LOC + already-present HIGH complexity hotspots (complexity-optimizer)
#   3. Related code paths (codegraph context for the topic/symbol)
#
# 2026-05-22 触发: 用户 push back '能否利用 codegraph + complexity 在改代码之前避免问题呢'
#
# Usage:
#   bash scripts/pre_edit_check.sh <file_path>
#   bash scripts/pre_edit_check.sh <topic_or_symbol> --topic
#
# Examples:
#   bash scripts/pre_edit_check.sh backend/scripts/retrain_lambdamart_v6.py
#   bash scripts/pre_edit_check.sh "panel sector features" --topic
#
# Exit code: 0 always (advisory, never block). User reviews output before edit.

set -uo pipefail

cd "$(dirname "$0")/.."

TARGET="${1:-}"
MODE="file"
if [[ "${2:-}" == "--topic" ]]; then
    MODE="topic"
fi

if [[ -z "$TARGET" ]]; then
    echo "用法: bash scripts/pre_edit_check.sh <file_path>"
    echo "      bash scripts/pre_edit_check.sh <topic> --topic"
    exit 1
fi

echo "============================================================"
echo "PRE-EDIT RISK CHECK — $TARGET (mode=$MODE)"
echo "============================================================"
echo

if [[ "$MODE" == "file" ]]; then
    if [[ ! -f "$TARGET" ]]; then
        echo "WARN: file not found locally, will only query symbols"
    else
        # 1. LOC + structure
        LOC=$(wc -l < "$TARGET" 2>/dev/null | tr -d ' ')
        echo "## 1. File metrics"
        echo "  LOC: $LOC"
        if [[ "$LOC" -gt 1000 ]]; then
            echo "  WARN: file > 1000 LOC = god-module candidate. Consider refactor before adding logic."
        elif [[ "$LOC" -gt 500 ]]; then
            echo "  NOTE: file 500-1000 LOC. Monitor growth."
        fi
        echo
    fi

    # 2. Codegraph: find symbols defined in this file + their callers
    SYMBOL_BASE=$(basename "$TARGET" .py)
    SYMBOL_BASE=$(basename "$SYMBOL_BASE" .sh)
    echo "## 2. Codegraph callers / dependents (query: '$SYMBOL_BASE')"
    codegraph query "$SYMBOL_BASE" 2>&1 | head -30 | sed 's/^/  /'
    echo
fi

if [[ "$MODE" == "topic" ]]; then
    echo "## Codegraph context (topic: '$TARGET')"
    codegraph context "$TARGET" 2>&1 | head -50 | sed 's/^/  /'
    echo
fi

# 3. Complexity scan — only on file mode
if [[ "$MODE" == "file" && -f "$TARGET" ]]; then
    echo "## 3. Complexity hotspots in $TARGET"
    SCAN_OUTPUT=$(python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py "$(pwd)" --format markdown 2>&1)
    # Show 2 lines before+at every Location line that matches target file
    HOTSPOTS=$(echo "$SCAN_OUTPUT" | grep -B 1 -F "$TARGET")
    if [[ -n "$HOTSPOTS" ]]; then
        echo "$HOTSPOTS" | sed 's/^/  /'
    else
        echo "  no HIGH hotspots in this file"
    fi
    echo
fi

echo "============================================================"
echo "[pre-edit-check] DONE. Review above before editing."
echo "  - 若 god-module + 加 logic → 考虑 refactor 先"
echo "  - 若 callers 多 → 改 signature 必查 test + 调用方"
echo "  - 若有 HIGH hotspot → 避免在同 function 加 nested loop / sort-in-loop"
echo "============================================================"

exit 0

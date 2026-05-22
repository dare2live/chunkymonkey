#!/usr/bin/env bash
# pre_edit_check_lite.sh — Smart lite pre-edit check (PreToolUse Edit|Write hook).
#
# 2026-05-22 用户决策 A: project-level Claude Code hook in .claude/settings.json
# 配 PreToolUse Edit|Write matcher → 自动跑此 lite 版本.
#
# Input: JSON via stdin (Claude Code hook contract, same as py_compile_check.sh).
#        Extracts tool_input.file_path with jq.
# Output: systemMessage JSON via jq if findings (only when LOC > 1000 OR HIGH hotspot count > 0).
#        Silent on small files / docs / non-chunkymonkey path.
#
# Design:
#   - skip non-chunkymonkey path (silent in other projects)
#   - skip small/doc files (md/json/txt/log/yaml/csv/parquet/duckdb/etc)
#   - skip files < 200 LOC
#   - output via systemMessage (not stdout) so Claude sees it as 1 system note
#   - never block (exit 0 always, even on error)

set -u

input=$(cat)
TARGET=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# Manual invoke fallback: also accept arg
if [[ -z "$TARGET" ]]; then
    TARGET="${1:-}"
fi

# Quick exit on missing arg or non-file
[[ -z "$TARGET" ]] && exit 0
[[ ! -f "$TARGET" ]] && exit 0

# Skip if not in chunkymonkey path (project-level hook may fire on any file Claude touches)
case "$TARGET" in
    */chunkymonkey/*|chunkymonkey/*) ;;  # OK in chunkymonkey
    *)
        # Check if PWD is chunkymonkey
        case "$PWD" in
            */chunkymonkey*) ;;
            *) exit 0 ;;  # silent skip
        esac
        ;;
esac

# Skip doc / config / small files
case "$TARGET" in
    *.md|*.json|*.txt|*.log|*.yaml|*.yml|*.lock|*.toml|*.csv|*.parquet|*.duckdb|*.db|*.ipynb)
        exit 0
        ;;
esac

# Skip if small file
LOC=$(wc -l < "$TARGET" 2>/dev/null | tr -d ' ')
[[ -z "$LOC" || "$LOC" -lt 200 ]] && exit 0

# cd to project root if hook ran from elsewhere
PROJECT_ROOT="/Users/dp/Documents/M/stock/chunkymonkey"
cd "$PROJECT_ROOT" 2>/dev/null || exit 0

# Gather findings (only emit systemMessage if LOC > 1000 OR HIGH hotspot > 0 OR many callers)
findings=()

# LOC warn
if [[ "$LOC" -gt 1000 ]]; then
    findings+=("$TARGET: ${LOC} LOC (god-module warn, consider refactor before adding logic)")
elif [[ "$LOC" -gt 500 ]]; then
    findings+=("$TARGET: ${LOC} LOC (large, monitor growth)")
fi

# Codegraph callers (strip ANSI then filter import lines, top 2)
SYMBOL=$(basename "$TARGET" | sed 's/\.[^.]*$//')
callers=$(codegraph query "$SYMBOL" 2>/dev/null | sed -E "s/\x1b\[[0-9;]*[a-zA-Z]//g" | grep -E "^\s*import\s+" | head -2)
if [[ -n "$callers" ]]; then
    n_callers=$(echo "$callers" | grep -c "import")
    findings+=("callers (top $n_callers): $(echo "$callers" | head -1 | xargs)")
fi

# HIGH hotspots (cached, refresh if missing/old/empty)
SCAN_CACHE="/tmp/cm_complexity_scan_cache.txt"
SCAN_AGE=99999
if [[ -f "$SCAN_CACHE" ]]; then
    SCAN_AGE=$(( $(date +%s) - $(stat -f %m "$SCAN_CACHE" 2>/dev/null || echo 0) ))
fi
if [[ ! -s "$SCAN_CACHE" || "$SCAN_AGE" -gt 300 ]]; then
    python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py "$PROJECT_ROOT" --format markdown > "$SCAN_CACHE" 2>/dev/null
fi
n_high=$(grep -c -F "Location: \`$TARGET" "$SCAN_CACHE" 2>/dev/null)
n_high=${n_high:-0}
if [[ "$n_high" -gt 0 ]]; then
    findings+=("$n_high HIGH complexity hotspot(s) already in this file (avoid adding nested loop / sort-in-loop)")
fi

# Emit systemMessage if any findings (uses jq pattern like py_compile_check.sh)
if [[ ${#findings[@]} -gt 0 ]]; then
    msg="[pre-edit-check] $TARGET"$'\n  - '
    msg+=$(printf '%s\n  - ' "${findings[@]}" | sed '$d')
    jq -nc --arg msg "$msg" '{systemMessage: $msg}' 2>/dev/null
fi

exit 0

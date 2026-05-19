#!/usr/bin/env bash
# agents_status.sh — Codex + Claude background agent 主动 lifecycle 检查
#
# 用户 push back 2026-05-19: "建一个 agent 管理机制, 让 agents 主动报告完成情况, 你确认后
# 关闭它们然后起新的 agent 开始其他任务". 固化 CLAUDE.md Rule 10.6.
#
# 输出: 所有 running Codex task + idle time + recent completed tasks + actionable next.
#
# 配套 codex_monitor.sh (~/.codex_monitor/, launchd 每 15 min auto-cancel idle > 30 min).
#
# Usage:
#   bash scripts/agents_status.sh              # 列当前状态
#   bash scripts/agents_status.sh --watch      # watch mode (每 60s refresh)

set -euo pipefail

export PATH="/opt/homebrew/bin:/opt/homebrew/opt/python@3.13/libexec/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

COMPANION="/Users/dp/.claude/plugins/cache/openai-codex/codex/1.0.4/scripts/codex-companion.mjs"
NODE_BIN="${NODE_BIN:-/opt/homebrew/bin/node}"
WATCH=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --watch) WATCH=1; shift ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
done

print_status() {
    echo "==================================================="
    echo "  Agents Status @ $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "==================================================="
    if [[ ! -f "$COMPANION" ]]; then
        echo "Codex companion not found: $COMPANION"
        return 0
    fi

    "$NODE_BIN" "$COMPANION" status --json 2>/dev/null | python3 -c "
import json, sys
from datetime import datetime, timezone

data = json.load(sys.stdin)
running = data.get('running', [])
latest = data.get('latestFinished') or {}
recent = data.get('recent') or []

now = datetime.now(timezone.utc)
print()
print(f'Running tasks: {len(running)}')
for t in running:
    updated = datetime.fromisoformat(t['updatedAt'].replace('Z', '+00:00'))
    idle_min = (now - updated).total_seconds() / 60
    alarm = '⚠ STUCK' if idle_min > 30 else ('警告' if idle_min > 15 else 'OK')
    print(f'  [{alarm}] {t[\"id\"]} elapsed={t[\"elapsed\"]} idle={idle_min:.0f}min')
    summary = (t.get('summary') or '').strip()[:100]
    if summary:
        print(f'    summary: {summary}')
    preview = t.get('progressPreview') or []
    if preview:
        last_preview = preview[-1][:140]
        print(f'    last preview: {last_preview}')

if latest:
    print()
    print(f'Latest finished: {latest.get(\"id\", \"none\")}')
    if latest.get('status'):
        print(f'  status: {latest.get(\"status\")}')
    if latest.get('finishedAt'):
        finished = datetime.fromisoformat(latest['finishedAt'].replace('Z', '+00:00'))
        ago_min = (now - finished).total_seconds() / 60
        print(f'  finished: {ago_min:.0f} min ago')

if recent:
    print()
    print(f'Recent completed (last {len(recent)}):')
    for t in recent[:5]:
        print(f'  - {t.get(\"id\", \"?\")}: {t.get(\"status\", \"?\")} ({(t.get(\"summary\") or \"\").strip()[:60]})')

print()
print(f'needsReview: {data.get(\"needsReview\", False)}')
"
}

if [[ "$WATCH" == "1" ]]; then
    while true; do
        clear
        print_status
        echo
        echo "(Ctrl-C to exit, refresh every 60s)"
        sleep 60
    done
else
    print_status
fi

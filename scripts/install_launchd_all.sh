#!/usr/bin/env bash
# install_launchd_all.sh — 一键安装所有 ChunkyMonkey launchd 任务
#
# 用户终极交付标准 #4: "一切不再需要大模型维护, 用户每天跑数据更新就全自动化"
#
# 安装 3 个 launchd jobs:
# 1. codex-monitor — 每 15 min auto-cancel idle Codex (current Codex local-ops policy)
# 2. nightly-data-audit — 每天 2 AM 数据治理 audit (configs/launchd 已有)
# 3. daily-update — 每个交易日 17:00 全自动 update + paper_sim + 报告

set -euo pipefail

PROJECT_ROOT="/Users/dp/Documents/M/stock/chunkymonkey"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"

mkdir -p "$LAUNCH_AGENTS"

PLISTS=(
    "com.chunkymonkey.codex-monitor.plist"
    "com.chunkymonkey.daily-update.plist"
)

# Optional: nightly-data-audit
if [[ -f "$PROJECT_ROOT/configs/launchd/com.chunkymonkey.nightly-data-audit.plist" ]]; then
    PLISTS+=("com.chunkymonkey.nightly-data-audit.plist")
fi

echo "=== ChunkyMonkey launchd install ==="
for plist in "${PLISTS[@]}"; do
    src="$PROJECT_ROOT/configs/launchd/$plist"
    dst="$LAUNCH_AGENTS/$plist"
    if [[ ! -f "$src" ]]; then
        echo "SKIP: $src not found"
        continue
    fi
    echo "Install: $plist"
    cp "$src" "$dst"
    # Unload first (in case already loaded)
    launchctl unload "$dst" 2>/dev/null || true
    launchctl load "$dst"
    echo "  loaded"
done

echo
echo "=== Verify ==="
for plist in "${PLISTS[@]}"; do
    label="${plist%.plist}"
    if launchctl list | grep -q "$label"; then
        echo "  [OK] $label"
    else
        echo "  [MISSING] $label"
    fi
done

echo
echo "=== Uninstall later ==="
echo "  for plist in ${PLISTS[*]}; do"
echo "    launchctl unload \$HOME/Library/LaunchAgents/\$plist"
echo "    rm \$HOME/Library/LaunchAgents/\$plist"
echo "  done"

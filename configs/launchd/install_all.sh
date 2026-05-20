#!/usr/bin/env bash
# install_all.sh — 一键安装所有 launchd agents (用户 push back "全自动化 zero LLM maintenance")
#
# 解决: plist 文件存在但未加载 → audit 报 100% 但 cron 实际未跑
# 用法: bash configs/launchd/install_all.sh [install|uninstall|status]
#
# macOS Full Disk Access 注意 (2026-05-18 实测发现):
# launchd 用户 agent 跑 ~/Documents/ 下 script 会 'exit 126 Operation not permitted'.
# 解决之一:
#   (a) System Preferences → Privacy & Security → Full Disk Access
#       添加 /bin/bash 或 /opt/homebrew/bin/bash 给 Full Disk Access 权限
#   (b) 或移 repo 到非 Documents/ 路径 (~/code/chunkymonkey)
#   (c) 或用 crontab -e (cron daemon 路径权限不同, 但功能弱)
# 验证: 跑 bash configs/launchd/install_all.sh status, exit 126 = 权限问题, 0 = 正常.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLISTS=(
    "com.chunkymonkey.daily-update"
    "com.chunkymonkey.gcp-cost-tracker"
    "com.chunkymonkey.nightly-data-audit"
    "com.chunkymonkey.codex-monitor"
    "com.chunkymonkey.phase5-monitor"
)

ACTION="${1:-install}"

status() {
    echo "=== launchd agents status ==="
    fda_warn=0
    for label in "${PLISTS[@]}"; do
        plist_src="$REPO_ROOT/configs/launchd/${label}.plist"
        plist_dst="$LAUNCH_AGENTS/${label}.plist"
        if [[ ! -f "$plist_src" ]]; then
            echo "  $label: source plist 缺失 ($plist_src)"
            continue
        fi
        if [[ ! -f "$plist_dst" ]]; then
            echo "  $label: NOT INSTALLED (run: bash $0 install)"
            continue
        fi
        loaded=$(launchctl list 2>/dev/null | awk -v l="$label" '$3==l{print "loaded pid="$1" exit="$2}')
        if [[ -z "$loaded" ]]; then
            echo "  $label: installed file but NOT LOADED"
        else
            echo "  $label: $loaded"
            if [[ "$loaded" == *"exit=126"* ]]; then
                fda_warn=1
            fi
        fi
    done
    if [[ "$fda_warn" == "1" ]]; then
        echo ""
        echo "WARN: 有 agent 报 exit=126 (Operation not permitted) — macOS Full Disk Access 未授权."
        echo "      解决: System Preferences → Privacy & Security → Full Disk Access"
        echo "      添加 /bin/bash (或 \$(which bash)) + 重启 → 重跑 bash $0 status"
    fi
}

install_agents() {
    mkdir -p "$LAUNCH_AGENTS"
    for label in "${PLISTS[@]}"; do
        plist_src="$REPO_ROOT/configs/launchd/${label}.plist"
        plist_dst="$LAUNCH_AGENTS/${label}.plist"
        if [[ ! -f "$plist_src" ]]; then
            echo "SKIP $label: source missing"
            continue
        fi
        # Copy (overwrite to refresh paths if repo moved)
        cp -f "$plist_src" "$plist_dst"
        # Unload first (idempotent — no-op if not loaded)
        launchctl unload "$plist_dst" 2>/dev/null || true
        # Load
        if launchctl load "$plist_dst" 2>&1; then
            echo "  OK $label installed + loaded"
        else
            echo "  FAIL $label load failed (check $plist_dst syntax)"
        fi
    done
    echo ""
    status
}

uninstall_agents() {
    for label in "${PLISTS[@]}"; do
        plist_dst="$LAUNCH_AGENTS/${label}.plist"
        if [[ -f "$plist_dst" ]]; then
            launchctl unload "$plist_dst" 2>/dev/null || true
            rm -f "$plist_dst"
            echo "  removed $label"
        fi
    done
}

case "$ACTION" in
    install) install_agents ;;
    uninstall|--uninstall) uninstall_agents ;;
    status|--status) status ;;
    *) echo "Usage: $0 [install|uninstall|status]"; exit 1 ;;
esac

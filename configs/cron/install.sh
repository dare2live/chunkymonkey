#!/usr/bin/env bash
# install.sh — 一键安装 cron 自动化 (绕开 launchd FDA 阻塞)
#
# 用户 push back: launchd 跑 ~/Documents/ 下 script 需 macOS FDA 1 次手工授权
# cron daemon 不需 FDA, 真零依赖手工 (符合用户 'zero LLM maintenance' 标准)
#
# Usage:
#   bash configs/cron/install.sh [install|status|uninstall]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CRONTAB_SRC="$REPO_ROOT/configs/cron/crontab.txt"

ACTION="${1:-install}"

backup() {
    BACKUP="/tmp/cron_backup_$(date +%Y%m%d_%H%M%S).txt"
    crontab -l > "$BACKUP" 2>/dev/null || echo "" > "$BACKUP"
    echo "  backup saved: $BACKUP"
}

status() {
    echo "=== current crontab ==="
    if crontab -l 2>/dev/null | grep -q "chunkymonkey\|$REPO_ROOT"; then
        crontab -l 2>/dev/null | grep -E "chunkymonkey|$REPO_ROOT" || true
        echo ""
        echo "INSTALLED: chunkymonkey cron entries 存在"
        echo ""
        echo "=== recent activity ==="
        for log in /tmp/chunkymonkey_daily_update.log /tmp/nightly_data_audit.log /tmp/codex_monitor.log; do
            if [[ -f "$log" ]]; then
                echo "  $log: $(wc -l <"$log" | tr -d ' ') lines, last modified $(stat -f '%Sm' "$log" 2>/dev/null || stat -c '%y' "$log")"
            fi
        done
    else
        echo "NOT INSTALLED. 跑 bash $0 install"
    fi
}

install_cron() {
    if [[ ! -f "$CRONTAB_SRC" ]]; then
        echo "ERROR: $CRONTAB_SRC 不存在"
        exit 1
    fi
    backup
    # Merge: keep existing non-chunkymonkey entries, replace chunkymonkey entries
    (crontab -l 2>/dev/null | grep -v "chunkymonkey\|$REPO_ROOT" || true; cat "$CRONTAB_SRC") | crontab -
    echo "  installed crontab from $CRONTAB_SRC"
    echo ""
    status
}

uninstall_cron() {
    backup
    crontab -l 2>/dev/null | grep -v "chunkymonkey\|$REPO_ROOT" | crontab - || true
    echo "  removed chunkymonkey cron entries"
}

case "$ACTION" in
    install) install_cron ;;
    uninstall|--uninstall) uninstall_cron ;;
    status|--status) status ;;
    *) echo "Usage: $0 [install|status|uninstall]"; exit 1 ;;
esac

#!/bin/bash
# M8.9 launchd 安装/卸载/状态脚本
# 用法:
#   ./install_launchd.sh install   # 加载 plist 并启用
#   ./install_launchd.sh uninstall # 卸载
#   ./install_launchd.sh status    # 查看状态
#   ./install_launchd.sh kick      # 立即手动触发一次 (调试用)

set -eu

PLIST_NAME="cn.local.chunky-monkey.daily"
SRC_PLIST="$(cd "$(dirname "$0")" && pwd)/${PLIST_NAME}.plist"
DST_PLIST="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

case "${1:-status}" in
    install)
        if [[ ! -f "$SRC_PLIST" ]]; then
            echo "ERROR: 找不到 $SRC_PLIST"
            exit 1
        fi
        mkdir -p "$HOME/Library/LaunchAgents"
        mkdir -p "$HOME/Library/Logs/chunky-monkey"
        cp "$SRC_PLIST" "$DST_PLIST"
        # 卸载旧的 (如果存在), 忽略错误
        launchctl bootout "gui/$(id -u)/$PLIST_NAME" 2>/dev/null || true
        # 加载新的
        launchctl bootstrap "gui/$(id -u)" "$DST_PLIST"
        launchctl enable "gui/$(id -u)/$PLIST_NAME"
        echo "OK: $PLIST_NAME 已安装并启用"
        echo "下一次触发时间: 周一-周五 17:30"
        echo "立即手动触发: $0 kick"
        ;;
    uninstall)
        launchctl bootout "gui/$(id -u)/$PLIST_NAME" 2>/dev/null || true
        rm -f "$DST_PLIST"
        echo "OK: $PLIST_NAME 已卸载"
        ;;
    status)
        echo "=== plist 路径 ==="
        ls -la "$DST_PLIST" 2>/dev/null || echo "(未安装)"
        echo
        echo "=== launchctl 状态 ==="
        launchctl print "gui/$(id -u)/$PLIST_NAME" 2>&1 | head -40 || echo "(未加载)"
        echo
        echo "=== 最近日志 ==="
        ls -lt "$HOME/Library/Logs/chunky-monkey/" 2>/dev/null | head -5 || echo "(无日志)"
        ;;
    kick)
        echo "立即触发一次..."
        launchctl kickstart -p "gui/$(id -u)/$PLIST_NAME"
        echo "已触发, 查看日志: tail -f ~/Library/Logs/chunky-monkey/daily-$(date +%Y-%m-%d).log"
        ;;
    *)
        echo "用法: $0 {install|uninstall|status|kick}"
        exit 1
        ;;
esac

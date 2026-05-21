#!/usr/bin/env bash
# install_resilience — 1 命令装齐 session resilience 全部组件.
#
# 装的:
#   1. SessionStart hook in ~/.claude/settings.json (claude 启动 auto-inject SESSION_HANDOFF.md)
#   2. Cron: */5 * * * * session_snapshot.sh + */10 * * * * workflow_checkpoint.sh
#   3. launchd plist: com.chunkymonkey.phase5-monitor (5min probe, VM TERMINATED 自动 pull)
#
# Usage:
#   bash scripts/install_resilience.sh             # install all
#   bash scripts/install_resilience.sh --uninstall # remove all
#   bash scripts/install_resilience.sh --status    # check install state
#
# 设计原则: idempotent (重装不重复), uninstall clean (恢复原状)

set -e
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

MODE="${1:-install}"

if [ "$MODE" = "--status" ]; then
    echo "=== Resilience install status ==="
    # 1. SessionStart hook
    if grep -q "session_start_handoff" ~/.claude/settings.json 2>/dev/null; then
        echo "  [OK]   SessionStart hook (~/.claude/settings.json)"
    else
        echo "  [MISS] SessionStart hook"
    fi
    # 2. Cron
    if crontab -l 2>/dev/null | grep -q "session_snapshot"; then
        echo "  [OK]   cron session_snapshot 5min"
    else
        echo "  [MISS] cron session_snapshot"
    fi
    if crontab -l 2>/dev/null | grep -q "workflow_checkpoint"; then
        echo "  [OK]   cron workflow_checkpoint 10min"
    else
        echo "  [MISS] cron workflow_checkpoint"
    fi
    cron_blocked=0
    for log in /tmp/session_snapshot.log /tmp/workflow_checkpoint.log; do
        if [[ -f "$log" ]] && tail -20 "$log" 2>/dev/null | grep -qi "Operation not permitted"; then
            echo "  [FAIL] cron runtime blocked: $log has Operation not permitted"
            cron_blocked=1
        fi
    done
    if [[ "$cron_blocked" == "1" ]]; then
        echo "         ACTION: 手动恢复先跑 bash scripts/cm_resume.sh;"
        echo "                 长期修复需给 cron/bash Full Disk Access 或把 repo 移出 Documents."
    fi
    # 3. launchd
    if launchctl list 2>/dev/null | grep -q "phase5-monitor"; then
        echo "  [OK]   launchd phase5-monitor (5min probe)"
    else
        echo "  [MISS] launchd phase5-monitor"
    fi
    exit 0
fi

if [ "$MODE" = "--uninstall" ]; then
    echo "=== Uninstall resilience ==="
    # 1. cron
    crontab -l 2>/dev/null | grep -v "session_snapshot\|workflow_checkpoint" | crontab - 2>/dev/null || true
    echo "  cron entries removed"
    # 2. launchd
    if launchctl list 2>/dev/null | grep -q "phase5-monitor"; then
        launchctl unload ~/Library/LaunchAgents/com.chunkymonkey.phase5-monitor.plist 2>/dev/null || true
        rm -f ~/Library/LaunchAgents/com.chunkymonkey.phase5-monitor.plist
        echo "  launchd unloaded"
    fi
    echo "  (SessionStart hook in ~/.claude/settings.json kept — manual remove if needed)"
    exit 0
fi

echo "=== Install resilience ==="

# 1. SessionStart hook check (we don't auto-edit ~/.claude/settings.json — user 已 install commit edc2bce5)
if grep -q "session_start_handoff" ~/.claude/settings.json 2>/dev/null; then
    echo "[1/3] SessionStart hook 已配置 (skip)"
else
    echo "[1/3] WARN: SessionStart hook 未配置. Manual:"
    echo "      edit ~/.claude/settings.json hooks.SessionStart 加 bash ~/.claude/hooks/session_start_handoff.sh"
fi

# 2. Install cron (5min snapshot + 10min workflow if script exists)
echo "[2/3] Install cron entries..."
CRON_TMP=$(mktemp)
crontab -l 2>/dev/null | grep -v "session_snapshot\|workflow_checkpoint" > "$CRON_TMP" || true
echo "*/5 * * * * cd $REPO_ROOT && bash scripts/session_snapshot.sh > /tmp/session_snapshot.log 2>&1" >> "$CRON_TMP"
if [ -x "scripts/workflow_checkpoint.sh" ]; then
    echo "*/10 * * * * cd $REPO_ROOT && bash scripts/workflow_checkpoint.sh > /tmp/workflow_checkpoint.log 2>&1" >> "$CRON_TMP"
fi
crontab "$CRON_TMP"
rm -f "$CRON_TMP"
echo "      cron installed"
crontab -l | grep -E "session_snapshot|workflow_checkpoint" | sed 's/^/        /'

# 3. Install launchd plist for phase5-monitor probe (5min)
echo "[3/3] Install launchd phase5-monitor..."
if [ -f "configs/launchd/com.chunkymonkey.phase5-monitor.plist" ]; then
    cp configs/launchd/com.chunkymonkey.phase5-monitor.plist ~/Library/LaunchAgents/
    launchctl unload ~/Library/LaunchAgents/com.chunkymonkey.phase5-monitor.plist 2>/dev/null || true
    launchctl load ~/Library/LaunchAgents/com.chunkymonkey.phase5-monitor.plist
    echo "      launchd loaded (5min auto probe)"
else
    echo "      SKIP (configs/launchd/com.chunkymonkey.phase5-monitor.plist 不存在)"
fi

echo ""
echo "=== Install done. Verify ==="
bash "$0" --status
echo ""
echo "中断恢复用法:"
echo "  1. Mac 重启 / terminal 崩 后, 启 terminal"
echo "  2. cd $REPO_ROOT"
echo "  3. bash scripts/cm_resume.sh    # 看当前 state"
echo "  4. claude                        # SessionStart hook 自动 inject handoff"

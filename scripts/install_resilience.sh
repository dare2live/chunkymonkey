#!/usr/bin/env bash
# install_resilience — legacy 自动恢复组件管理.
#
# Codex app/CLI 当前默认不再安装 SessionStart handoff auto-inject 或 cron
# snapshot 自动刷新，避免 stale handoff 在新会话中被静默加载。推荐恢复路径是:
#   bash scripts/cm_resume.sh
#   然后在新 Codex 会话中按 docs/chunkyctl_session_quickstart.md 启动。
#
# legacy opt-in 装的:
#   1. Cron: */5 * * * * session_snapshot.sh + */10 * * * * workflow_checkpoint.sh
#   2. launchd plist: com.chunkymonkey.phase5-monitor (5min probe, VM TERMINATED 自动 pull)
#
# Usage:
#   bash scripts/install_resilience.sh             # no-op status; does not install legacy automation
#   CHUNKYMONKEY_ENABLE_LEGACY_AUTOMATION=1 bash scripts/install_resilience.sh
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
    # 1. Codex SessionStart hook
    if grep -q "session_start_handoff" ~/.codex/hooks.json 2>/dev/null; then
        echo "  [WARN] Codex SessionStart handoff hook enabled (legacy auto-inject)"
    else
        echo "  [OK]   Codex SessionStart handoff hook disabled"
    fi
    # 2. Cron
    CRONTAB_TEXT="$(crontab -l 2>/dev/null || true)"
    cron_snapshot_enabled=0
    cron_workflow_enabled=0
    if printf '%s\n' "$CRONTAB_TEXT" | grep -q "session_snapshot"; then
        cron_snapshot_enabled=1
        echo "  [WARN] cron session_snapshot 5min enabled (legacy auto-update)"
    else
        echo "  [OK]   cron session_snapshot disabled"
    fi
    if printf '%s\n' "$CRONTAB_TEXT" | grep -q "workflow_checkpoint"; then
        cron_workflow_enabled=1
        echo "  [WARN] cron workflow_checkpoint 10min enabled (legacy auto-update)"
    else
        echo "  [OK]   cron workflow_checkpoint disabled"
    fi
    cron_blocked=0
    if [[ "$cron_snapshot_enabled" == "1" && -f /tmp/session_snapshot.log ]] \
        && tail -20 /tmp/session_snapshot.log 2>/dev/null | grep -qi "Operation not permitted"; then
            echo "  [FAIL] cron runtime blocked: /tmp/session_snapshot.log has Operation not permitted"
            cron_blocked=1
    fi
    if [[ "$cron_workflow_enabled" == "1" && -f /tmp/workflow_checkpoint.log ]] \
        && tail -20 /tmp/workflow_checkpoint.log 2>/dev/null | grep -qi "Operation not permitted"; then
            echo "  [FAIL] cron runtime blocked: /tmp/workflow_checkpoint.log has Operation not permitted"
            cron_blocked=1
    fi
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
    echo "  (Codex SessionStart hook is managed in ~/.codex/hooks.json; verify with --status)"
    exit 0
fi

if [[ "${CHUNKYMONKEY_ENABLE_LEGACY_AUTOMATION:-0}" != "1" ]]; then
    echo "=== Legacy automation install disabled by default ==="
    echo "Codex app/CLI should use manual resume:"
    echo "  bash scripts/cm_resume.sh"
    echo "To intentionally restore legacy cron/launchd automation, rerun with:"
    echo "  CHUNKYMONKEY_ENABLE_LEGACY_AUTOMATION=1 bash scripts/install_resilience.sh"
    echo ""
    bash "$0" --status
    exit 0
fi

echo "=== Install resilience ==="

# 1. Install cron (5min snapshot + 10min workflow if script exists)
echo "[1/2] Install legacy cron entries..."
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

# 2. Install launchd plist for phase5-monitor probe (5min)
echo "[2/2] Install launchd phase5-monitor..."
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
echo "  4. 新 Codex 会话按 docs/chunkyctl_session_quickstart.md 启动"

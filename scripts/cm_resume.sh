#!/usr/bin/env bash
# cm_resume — 中断后 1 命令恢复入口.
#
# 用户在 terminal 跑这个, 输出:
#   1. 刷新 SESSION_HANDOFF.md (调 session_snapshot.sh)
#   2. 刷新 analysis/workflow_checkpoint.md (调 workflow_checkpoint.sh if exists)
#   3. 打印简短 "粘贴进 Codex 的 prompt"
#
# 用户中断后流程 (推荐):
#   $ cd /Users/dp/Documents/M/stock/chunkymonkey
#   $ bash scripts/cm_resume.sh          # 1 命令出 prompt
#   新 Codex 会话输入脚本输出的推荐 prompt.
#
# 如果需要显式恢复，用户 copy 输出的 prompt 粘进 Codex:
#   "中断恢复. 看 SESSION_HANDOFF.md + analysis/workflow_checkpoint.md, 按 next_action 继续."

set -e
cd "$(dirname "$0")/.."

echo "============================================================"
echo "  ChunkyMonkey resume helper"
echo "============================================================"
echo ""

# 1. Refresh session snapshot
if [ -x "scripts/session_snapshot.sh" ]; then
    echo "[1/3] Refresh SESSION_HANDOFF.md..."
    bash scripts/session_snapshot.sh > /dev/null 2>&1 || true
    echo "      done"
fi

# 2. Refresh workflow checkpoint (if Codex aca4146c deliver)
if [ -x "scripts/workflow_checkpoint.sh" ]; then
    echo "[2/3] Refresh analysis/workflow_checkpoint.md..."
    bash scripts/workflow_checkpoint.sh > /dev/null 2>&1 || true
    echo "      done"
fi

# 3. Extract key state for prompt
MODEL_ID=$(cat data/reports/phase5_chain/model_id.txt 2>/dev/null | head -1)
NEXT_ACTION=$(python3 -c "import json; d=json.load(open('data/reports/session_snapshot.json')); print(d.get('next_action','?'))" 2>/dev/null || echo "?")
VM_STATUS=$(python3 -c "import json; d=json.load(open('data/reports/session_snapshot.json')); print(d['retrain']['vm_status'])" 2>/dev/null || echo "?")
F2_BEST=$(python3 -c "import json; d=json.load(open('data/reports/session_snapshot.json')); print(d['retrain']['f2_best_value'])" 2>/dev/null || echo "")
COMMITS_24H=$(python3 -c "import json; d=json.load(open('data/reports/session_snapshot.json')); print(d['git']['commits_24h'])" 2>/dev/null || echo "?")
CODEX_RUN=$(python3 -c "import json; d=json.load(open('data/reports/session_snapshot.json')); print(d['background']['codex_running'])" 2>/dev/null || echo "?")

echo ""
echo "============================================================"
echo "  当前状态 (auto-detected)"
echo "============================================================"
echo "  retrain model_id: $MODEL_ID"
echo "  VM 状态:          $VM_STATUS"
[ -n "$F2_BEST" ] && [ "$F2_BEST" != "?" ] && echo "  F2 best value:    $F2_BEST"
echo "  Codex running:    $CODEX_RUN"
echo "  24h commits:      $COMMITS_24H"
echo "  Next action:      $NEXT_ACTION"
echo ""
echo "============================================================"
echo "  用户怎么继续"
echo "============================================================"
echo ""
echo "  推荐:"
echo "    请按照 docs/chunkyctl_session_quickstart.md 接手本项目，先完成启动检查，再看 SESSION_HANDOFF.md 的 next_action。"
echo ""
echo "  简短恢复:"
echo "    继续, 看 SESSION_HANDOFF.md 和 analysis/workflow_checkpoint.md, 按 next_action 推进"
echo ""
echo "  方案 C (复杂多步流程衔接, workflow_checkpoint 可用时):"
echo "    用户输入:  从 analysis/workflow_checkpoint.md 推断当前 pipeline step, 按 next_recovery_command 继续"
echo ""
echo "============================================================"
echo "  Resilience 配置 verify"
echo "============================================================"
# Verify Codex SessionStart hook disabled
if grep -q "session_start_handoff" ~/.codex/hooks.json 2>/dev/null; then
    echo "  [WARN] Codex SessionStart handoff hook 仍启用 — 可能自动注入 stale handoff"
else
    echo "  [OK]   Codex SessionStart handoff auto-inject 未启用"
fi
# Verify cron snapshot disabled by default
if crontab -l 2>/dev/null | grep -q "session_snapshot"; then
    echo "  [WARN] cron snapshot 已 install (legacy auto-update)"
    if [[ -f /tmp/session_snapshot.log ]] && tail -20 /tmp/session_snapshot.log 2>/dev/null | grep -qi "Operation not permitted"; then
        echo "  [FAIL] cron snapshot runtime blocked: /tmp/session_snapshot.log has Operation not permitted"
        echo "         本次 cm_resume 已手动刷新; 长期修复需给 cron/bash Full Disk Access 或把 repo 移出 Documents."
    fi
else
    echo "  [OK]   cron snapshot 未启用; handoff 只按需手动刷新"
fi
# Verify launchd monitor probe
if launchctl list 2>/dev/null | grep -q "phase5-monitor"; then
    echo "  [OK]   launchd monitor 已 active"
else
    echo "  [WARN] launchd monitor 未 active — VM TERMINATED 不会 auto pull"
    echo "         install: bash configs/launchd/install_all.sh install"
fi
echo ""
echo "============================================================"
echo "  下一步建议"
echo "============================================================"
echo "  $NEXT_ACTION"
echo ""

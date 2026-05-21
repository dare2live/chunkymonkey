#!/usr/bin/env bash
# Session snapshot — Mac 重启 / terminal 崩 / Claude session 中断后无缝衔接的核心.
#
# 输出:
#   - data/reports/session_snapshot.json (machine-readable, cron auto-update)
#   - SESSION_HANDOFF.md (human + Claude-readable, 含 "next action" 建议)
#   - references analysis/workflow_checkpoint.md for business pipeline status
#
# 跑法:
#   bash scripts/session_snapshot.sh                # 1 命令更新
#   bash scripts/session_snapshot.sh                # 默认跳过 GCP query (offline)
#   CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash scripts/session_snapshot.sh --with-gcp
#
# Cron 集成 (configs/cron/install.sh):
#   */5 * * * * cd /Users/dp/Documents/M/stock/chunkymonkey && bash scripts/session_snapshot.sh > /tmp/session_snapshot.log 2>&1
#
# SessionStart hook 集成 (~/.claude/settings.json):
#   {"type":"command","command":"cat /Users/dp/Documents/M/stock/chunkymonkey/SESSION_HANDOFF.md"}
#
# 设计原则: 0 stale (cron 持续 refresh), 0 manual (不要求用户 paste context),
#            auto-discover (in-flight retrain / pending agents / 未 commit changes)

set -e
cd "$(dirname "$0")/.."

NO_GCP=1
if [[ "${1:-}" == "--with-gcp" ]]; then
    if [[ "${CHUNKYMONKEY_GCP_EXPLICIT_OK:-0}" != "1" ]]; then
        echo "BLOCK: GCP controlled-use requires CHUNKYMONKEY_GCP_EXPLICIT_OK=1" >&2
        exit 3
    fi
    NO_GCP=0
fi
[[ "${1:-}" == "--no-gcp" ]] && NO_GCP=1

SNAPSHOT_JSON=data/reports/session_snapshot.json
HANDOFF_MD=SESSION_HANDOFF.md
mkdir -p data/reports

NOW=$(date '+%Y-%m-%d %H:%M:%S %Z')
NOW_EPOCH=$(date +%s)

# ============ 1. Git state ============
GIT_BRANCH=$(git branch --show-current 2>/dev/null || echo "?")
GIT_HEAD=$(git log --oneline -1 2>/dev/null || echo "?")
GIT_HEAD_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
COMMITS_24H=$(git log --oneline --since '24 hours ago' 2>/dev/null | wc -l | tr -d ' ')
UNCOMMITTED=$(git status --short 2>/dev/null | wc -l | tr -d ' ')
RECENT_COMMITS=$(git log --oneline -10 2>/dev/null)

# ============ 2. Retrain in-flight ============
RETRAIN_MODEL_ID=$(cat data/reports/phase5_chain/model_id.txt 2>/dev/null | head -1)
RETRAIN_STATUS_JSON=$(cat data/reports/phase5_chain/status.json 2>/dev/null || echo '{}')

# Check VM status (GCP) — skip if --no-gcp
VM_STATUS="?"
VM_START=""
VM_STOP=""
if [[ "$NO_GCP" == "0" ]]; then
    VM_INFO=$(gcloud compute instances describe chunkymonkey-optuna --zone=us-central1-a \
              --format='value(status,lastStartTimestamp,lastStopTimestamp)' 2>/dev/null || echo "ERROR	?	?")
    VM_STATUS=$(echo "$VM_INFO" | cut -f1)
    VM_START=$(echo "$VM_INFO" | cut -f2)
    VM_STOP=$(echo "$VM_INFO" | cut -f3)
fi

# F2 checkpoint best params (latest)
F2_CHECKPOINT=""
F2_BEST_VALUE=""
F2_BEST_TRIAL=""
F2_UPDATED=""
if [[ -n "$RETRAIN_MODEL_ID" ]] && [[ -f "data/reports/optuna/${RETRAIN_MODEL_ID}.best.json" ]]; then
    F2_CHECKPOINT="data/reports/optuna/${RETRAIN_MODEL_ID}.best.json"
    F2_BEST_VALUE=$(python3 -c "import json; d=json.load(open('$F2_CHECKPOINT')); print(d.get('best_value', '?'))" 2>/dev/null || echo "?")
    F2_BEST_TRIAL=$(python3 -c "import json; d=json.load(open('$F2_CHECKPOINT')); print(d.get('best_trial_number', '?'))" 2>/dev/null || echo "?")
    F2_UPDATED=$(python3 -c "import json; d=json.load(open('$F2_CHECKPOINT')); print(d.get('updated_at', '?'))" 2>/dev/null || echo "?")
fi

# ============ 3. Background processes ============
MONITOR_PID=$(pgrep -f "monitor_phase5_gcp_retrain" | head -1 || echo "")
MONITOR_STATUS="dead"
if [[ -n "$MONITOR_PID" ]]; then
    MONITOR_ELAPSED=$(ps -p "$MONITOR_PID" -o etime --no-headers 2>/dev/null | tr -d ' ' || echo "?")
    MONITOR_STATUS="alive PID=$MONITOR_PID elapsed=$MONITOR_ELAPSED"
fi

# Codex companion threads
CODEX_RUNNING=0
CODEX_INFO=""
if command -v node >/dev/null 2>&1 && [[ -f /Users/dp/.claude/plugins/cache/openai-codex/codex/1.0.4/scripts/codex-companion.mjs ]]; then
    CODEX_INFO=$(node /Users/dp/.claude/plugins/cache/openai-codex/codex/1.0.4/scripts/codex-companion.mjs status --json 2>/dev/null | \
                 python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    running = d.get('running', [])
    print(f'{len(running)}')
    for r in running:
        print(f'  - {r[\"id\"]} elapsed={r[\"elapsed\"]}')
except: print('0')
" 2>/dev/null || echo "0")
    CODEX_RUNNING=$(echo "$CODEX_INFO" | head -1)
fi

# ============ 4. GCP cost ============
GCP_COST_PCT="?"
GCP_REMAINING_HOURS="?"
if [[ -f data/reports/gcp_cost_summary.json ]]; then
    GCP_COST_PCT=$(python3 -c "import json; d=json.load(open('data/reports/gcp_cost_summary.json')); print(f\"{d.get('pct_of_budget',0):.1f}\")" 2>/dev/null || echo "?")
    GCP_REMAINING_HOURS=$(python3 -c "import json; d=json.load(open('data/reports/gcp_cost_summary.json')); print(f\"{d.get('remaining_hours_at_spot',0):.1f}\")" 2>/dev/null || echo "?")
fi

# ============ 5. Compute next action ============
NEXT_ACTION=""
if [[ "$VM_STATUS" == "RUNNING" ]] && [[ "$MONITOR_STATUS" =~ ^dead ]]; then
    NEXT_ACTION="VM RUNNING 但 monitor 死了 — restart: bash scripts/monitor_phase5_gcp_retrain.sh"
elif [[ "$VM_STATUS" == "TERMINATED" ]] && [[ -n "$RETRAIN_MODEL_ID" ]] && [[ -z "$F2_BEST_VALUE" || "$F2_BEST_VALUE" == "?" ]]; then
    NEXT_ACTION="VM TERMINATED + 无 F2 checkpoint — 检查 retrain 是否完成: SSH 看 logs/retrain_${RETRAIN_MODEL_ID}.log"
elif [[ "$VM_STATUS" == "TERMINATED" ]] && [[ -n "$F2_BEST_VALUE" ]]; then
    NEXT_ACTION="VM TERMINATED + F2 checkpoint best_value=$F2_BEST_VALUE — pull predictions OR resume retrain"
elif [[ "$UNCOMMITTED" != "0" ]]; then
    NEXT_ACTION="$UNCOMMITTED uncommitted files — git status 看 + bash scripts/safe_commit.sh"
else
    NEXT_ACTION="background autonomy active, wait retrain progress"
fi

# ============ 6. Write JSON ============
cat > "$SNAPSHOT_JSON" <<EOF
{
  "snapshot_at": "$NOW",
  "git": {
    "branch": "$GIT_BRANCH",
    "head_hash": "$GIT_HEAD_HASH",
    "head_msg": $(printf '%s' "$GIT_HEAD" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))"),
    "commits_24h": $COMMITS_24H,
    "uncommitted_files": $UNCOMMITTED
  },
  "retrain": {
    "model_id": "$RETRAIN_MODEL_ID",
    "vm_status": "$VM_STATUS",
    "vm_last_start": "$VM_START",
    "vm_last_stop": "$VM_STOP",
    "f2_checkpoint_path": "$F2_CHECKPOINT",
    "f2_best_value": "$F2_BEST_VALUE",
    "f2_best_trial": "$F2_BEST_TRIAL",
    "f2_updated_at": "$F2_UPDATED"
  },
  "background": {
    "monitor": "$MONITOR_STATUS",
    "codex_running": $CODEX_RUNNING
  },
  "gcp_cost": {
    "pct_of_budget": "$GCP_COST_PCT",
    "remaining_hours_spot": "$GCP_REMAINING_HOURS"
  },
  "next_action": $(printf '%s' "$NEXT_ACTION" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))")
}
EOF

# ============ 7. Write SESSION_HANDOFF.md ============
cat > "$HANDOFF_MD" <<EOF
# SESSION HANDOFF — Auto-updated by cron

> 此文件由 \`scripts/session_snapshot.sh\` 每 5 min cron 自动更新.
> Claude session start 时 read 此文件即可无缝衔接, 不需要用户 paste context.
> Mac 重启 / terminal 崩 后, 启动 Claude → 自动 read → 立即知道当前状态 + next action.
> 业务 pipeline 进度另见 \`analysis/workflow_checkpoint.md\` (pull/audit/paper_sim/KPI/gate/decision).

## 中断恢复用法 (用户必读)

### 1. Mac 重启 / terminal 崩 后:
\`\`\`
cd /Users/dp/Documents/M/stock/chunkymonkey
bash scripts/cm_resume.sh          # 1 命令出当前 state + prompt 模板
claude                              # SessionStart hook 自动 inject 本 handoff
\`\`\`

### 2. 用户输入哪句话给 Claude:
- **方案 A** (SessionStart hook 配好, 推荐): 不用输入, hook 自动 inject 本 handoff, Claude 看到立即继续 next_action
- **方案 B** (hook fail / 想显式 trigger): 输入 \`继续, 看 SESSION_HANDOFF.md 按 next_action 推进\`
- **方案 C** (复杂多步流程): 输入 \`从 analysis/workflow_checkpoint.md 推断当前 pipeline step, 按 next_recovery_command 继续\`

### 3. 一次性 install 全部 resilience:
\`\`\`
bash scripts/install_resilience.sh   # SessionStart hook + cron + launchd 全装
bash scripts/install_resilience.sh --status   # check 装好没
\`\`\`

**Snapshot 时间**: $NOW

## 主线 retrain 状态

| 项 | 值 |
|---|---|
| Model ID | \`$RETRAIN_MODEL_ID\` |
| VM 状态 | $VM_STATUS |
| VM 上次启动 | $VM_START |
| VM 上次停止 | $VM_STOP |
| F2 checkpoint best_value | $F2_BEST_VALUE |
| F2 checkpoint best_trial | $F2_BEST_TRIAL |
| F2 updated_at | $F2_UPDATED |
| F2 path | \`$F2_CHECKPOINT\` |

## 后台 process

| 项 | 状态 |
|---|---|
| Local monitor | $MONITOR_STATUS |
| Codex companion threads | $CODEX_RUNNING running |

$CODEX_INFO

## GCP 成本

| 项 | 值 |
|---|---|
| 月预算用 | ${GCP_COST_PCT}% |
| 剩余 spot 小时 | $GCP_REMAINING_HOURS h |

## Git 状态

| 项 | 值 |
|---|---|
| Branch | $GIT_BRANCH |
| HEAD | \`$GIT_HEAD\` |
| 最近 24h commits | $COMMITS_24H |
| 未 commit 文件 | $UNCOMMITTED |

### 最近 10 commits

\`\`\`
$RECENT_COMMITS
\`\`\`

## NEXT ACTION (auto-computed)

**$NEXT_ACTION**

## Resilience 配置 (verified)

| 机制 | 状态 |
|---|---|
| F1 Optuna SQLite storage | deployed (\`sqlite:///data/reports/optuna/\$MODEL_ID.db\` resume on preempt) |
| F2 per-trial checkpoint | deployed (\`data/reports/optuna/\$MODEL_ID.best.json\` atomic write) |
| nohup + setsid + disown | retrain detached, SSH 断不影响 |
| monitor MAX_DURATION_HOURS=24 | Mac sleep proof |
| cron session_snapshot.sh | 5min auto update, 不依赖 Claude session 活 |
| SessionStart hook (~/.claude/settings.json) | 启动时 auto-read SESSION_HANDOFF.md |
| Stop hook session_rule_audit | 防 multi-agent / continuous-mode 违规 |

## 一旦中断如何无缝衔接

1. **Mac 重启 / terminal 崩 后**: 启动 terminal → \`cd /Users/dp/Documents/M/stock/chunkymonkey\` → 启动 \`claude\`
2. Claude SessionStart hook 自动 cat \`SESSION_HANDOFF.md\` 注入 context
3. Claude 看到: 当前 retrain model_id / local artifacts / next action
4. Claude 按 NEXT ACTION 执行本地工作 (audit / compare / commit / etc)
5. 用户 0 需要 paste 长 summary

GCP controlled-use (2026-05-21 用户澄清):
- 可用于大计算、寻优、长 replay、主项目与 BestChoice 综合寻优。
- 启动前说明 scope、wall time/成本、输入快照、输出路径、artifact 保存与 stop/rollback。
- 脚本层仍要求 \`CHUNKYMONKEY_GCP_EXPLICIT_OK=1\`, 防误触。
EOF

echo "[session_snapshot] updated $SNAPSHOT_JSON + $HANDOFF_MD @ $NOW"
echo "[session_snapshot] next_action: $NEXT_ACTION"

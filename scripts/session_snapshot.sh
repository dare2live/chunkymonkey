#!/usr/bin/env bash
# Session snapshot — Mac 重启 / terminal 崩 / Codex session 中断后的手动恢复快照.
#
# 输出:
#   - data/reports/session_snapshot.json (machine-readable)
#   - SESSION_HANDOFF.md (human + Codex-readable, 含 "next action" 建议)
#   - references analysis/workflow_checkpoint.md only when a pipeline is active
#
# 跑法:
#   bash scripts/session_snapshot.sh                # 1 命令更新
#
# Codex app/CLI 启动集成:
#   不再通过 cron 或 SessionStart hook 自动注入，避免旧 handoff 在新会话里被当作当前事实。
#   新会话按 docs/chunkyctl_session_quickstart.md 启动；需要刷新时手动跑 scripts/cm_resume.sh。
#
# 设计原则: live refresh on demand,
#            auto-discover (in-flight retrain / pending agents / 未 commit changes)

set -e
cd "$(dirname "$0")/.."

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
UNCOMMITTED=$(git status --short 2>/dev/null | awk '
{
  path = substr($0, 4);
  if (path != "SESSION_HANDOFF.md" &&
      path != "data/reports/session_snapshot.json" &&
      path != "analysis/workflow_checkpoint.md" &&
      path != "analysis/workflow_checkpoint.json") {
    count++;
  }
}
END { print count + 0 }
' )
RECENT_COMMITS=$(git log --oneline -10 2>/dev/null)

# ============ 2. Retrain artifact ============
# Fallback chain: current pointer > latest optuna best.json mtime.
RETRAIN_MODEL_ID=$(cat data/reports/stability_retrain/current.pointer 2>/dev/null | tr -d '[:space:]')
if [[ -z "$RETRAIN_MODEL_ID" ]]; then
    # latest best.json in optuna dir, derive model_id from filename
    LATEST_BEST=$(ls -t data/reports/optuna/*.best.json 2>/dev/null | head -1)
    if [[ -n "$LATEST_BEST" ]]; then
        RETRAIN_MODEL_ID=$(basename "$LATEST_BEST" .best.json)
    fi
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

# ============ 4. Compute backend contract ============
COMPUTE_BACKENDS=$(
PYTHONPATH=backend python - 2>/dev/null <<'PY' || echo "unavailable"
from services.experiment_jobs import load_experiment_job_contract

contract = load_experiment_job_contract()
print(", ".join(f"{k}:{v.status}" for k, v in sorted(contract.backends.items())))
PY
)

# ============ 5. Compute next action ============
NEXT_ACTION=""
if [[ "$UNCOMMITTED" != "0" ]]; then
    NEXT_ACTION="$UNCOMMITTED uncommitted files — git status 看 + bash scripts/safe_commit.sh"
else
    NEXT_ACTION="run startup checks first — scripts/chunkyctl doctor --fast; prioritize data_health blocking_yellow, then stage-opt structural blocker / need_027 blocked-gap triage"
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
    "f2_checkpoint_path": "$F2_CHECKPOINT",
    "f2_best_value": "$F2_BEST_VALUE",
    "f2_best_trial": "$F2_BEST_TRIAL",
    "f2_updated_at": "$F2_UPDATED"
  },
  "background": {
    "codex_running": $CODEX_RUNNING
  },
  "compute_backend": {
    "backends": $(printf '%s' "$COMPUTE_BACKENDS" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))")
  },
  "next_action": $(printf '%s' "$NEXT_ACTION" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))")
}
EOF

# ============ 7. Write SESSION_HANDOFF.md ============
cat > "$HANDOFF_MD" <<EOF
# SESSION HANDOFF — Manual resume snapshot

> 此文件由 \`scripts/session_snapshot.sh\` / \`scripts/cm_resume.sh\` 按需手动刷新.
> Codex app/CLI 不再通过 cron 或 SessionStart hook 自动注入本 handoff，避免 stale state 被静默加载.
> 新会话应先按 \`docs/chunkyctl_session_quickstart.md\` 做启动检查，再把本文件当 context-only 状态快照.
> 当前计划看薄入口 \`goal.md\`; 已完成证据查 \`analysis/project_state_ledger.md\`.
> \`analysis/workflow_checkpoint.md\` 只在其声明 active pipeline 时参与恢复。

## 中断恢复用法 (用户必读)

### 1. Mac 重启 / terminal 崩 后:
\`\`\`
cd /Users/dp/Documents/M/stock/chunkymonkey
bash scripts/cm_resume.sh          # 1 命令出当前 state + prompt 模板
\`\`\`

### 2. 新 Codex 会话输入哪句话:
- **推荐**: \`请按照 docs/chunkyctl_session_quickstart.md 接手本项目，先完成启动检查，再看 goal.md 和 live gates。\`
- **简短恢复**: \`继续，看 goal.md、SESSION_HANDOFF.md 和 doctor 输出，按当前 P0 推进。\`
- **复杂 pipeline**: 仅当 \`analysis/workflow_checkpoint.md\` 声明 active pipeline 时，按其中 next command 继续。

### 3. 自动注入状态:
\`\`\`
bash scripts/install_resilience.sh --status
\`\`\`
默认不再安装 cron snapshot / SessionStart auto-inject；如需恢复旧自动化，必须显式设置脚本里的 legacy opt-in。

**Snapshot 时间**: $NOW

## 主线状态

| 项 | 值 |
|---|---|
| Model ID | \`$RETRAIN_MODEL_ID\` |
| F2 checkpoint best_value | $F2_BEST_VALUE |
| F2 checkpoint best_trial | $F2_BEST_TRIAL |
| F2 updated_at | $F2_UPDATED |
| F2 path | \`$F2_CHECKPOINT\` |

## 后台 process

| 项 | 状态 |
|---|---|
| Codex companion threads | $CODEX_RUNNING running |

$CODEX_INFO

## Compute backend

| 项 | 值 |
|---|---|
| Backends | $COMPUTE_BACKENDS |
| Job plan | \`scripts/chunkyctl jobs --family model_training --model-id <id> --input-snapshot <snapshot> --objective <why> --rollback-plan <plan> --gate-evidence <gate>=<artifact>\` |

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
| manual session_snapshot.sh | active; run via \`bash scripts/cm_resume.sh\` |
| cron session_snapshot.sh | disabled by default for Codex app/CLI |
| SessionStart handoff auto-inject | disabled by default for Codex app/CLI |
| Stop hook session_rule_audit | 防 multi-agent / continuous-mode 违规 |

## 一旦中断如何无缝衔接

1. **Mac 重启 / terminal 崩 后**: 启动 terminal → \`cd /Users/dp/Documents/M/stock/chunkymonkey\`
2. 运行 \`bash scripts/cm_resume.sh\` 刷新本 handoff 和 snapshot
3. 新 Codex 会话输入: \`请按照 docs/chunkyctl_session_quickstart.md 接手本项目，先完成启动检查，再看 goal.md 和 live gates。\`
4. Codex 先跑 live checks，再按 NEXT ACTION 执行本地工作 (audit / compare / commit / etc)
EOF

echo "[session_snapshot] updated $SNAPSHOT_JSON + $HANDOFF_MD @ $NOW"
echo "[session_snapshot] next_action: $NEXT_ACTION"

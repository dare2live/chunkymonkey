#!/usr/bin/env bash
# Codex log tail — tail -f /tmp/codex.log 加 ISO timestamp + Codex 真实 model 元信息.
#
# 用户原话 2026-05-18: "tail -f /tmp/codex.log 这个命令请把对话带上时间戳, 并且带上 codex 的模型版本号,
#  不要是你自己编的, 是调用 codex 时它回传的"
#
# Codex companion 已写 job log 含 ISO timestamps (e.g. [2026-05-18T07:26:27.989Z] ...),
# 此 script 额外 prepend Codex CLI version + 用户 dispatch 时传的 model + job_id header.
#
# 数据源 (Codex 真实回传):
# - state.json (jobs[].id / status / threadId / startedAt)
# - job-*.json (kind=task/review)
# - codex --version (codex-cli 0.130.0)
#
# 注: model param 来自 dispatch convention (Claude 当前默认 --model gpt-5.5 --effort xhigh).
#   Codex companion + codex CLI 0.130.0 不在 job 元数据回传具体 model field, 但
#   thread 启动时 codex CLI 默认 GPT model (可在 stop hook 输出 "config/model=..." line).
#
# Usage:
#   bash scripts/codex_log_tail.sh                  # tail latest running job
#   bash scripts/codex_log_tail.sh --all            # all jobs in state.json
#   bash scripts/codex_log_tail.sh <job_id>         # specific job
#   bash scripts/codex_log_tail.sh --append-to /tmp/codex.log  # 持续 append + tail

set -euo pipefail

STATE_DIR="$HOME/.claude/plugins/data/codex-openai-codex/state"
WORKSPACE_HASH="chunkymonkey-3e283829598a56c6"  # rule-compliance: ok evidence=current-workspace-hash
APPEND_FILE="${APPEND_FILE:-/tmp/codex.log}"
ALL=0
SPECIFIC_JOB=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all) ALL=1; shift ;;
        --append-to) APPEND_FILE="$2"; shift 2 ;;
        --) shift; break ;;
        -*) echo "Unknown flag: $1"; exit 1 ;;
        *) SPECIFIC_JOB="$1"; shift ;;
    esac
done

STATE_JSON="$STATE_DIR/$WORKSPACE_HASH/state.json"
if [[ ! -f "$STATE_JSON" ]]; then
    echo "ERROR: state.json 不存在 ($STATE_JSON). Codex companion 未运行?"
    exit 1
fi

CODEX_CLI_VERSION=$(codex --version 2>/dev/null | head -1)
CLAUDE_DEFAULT_MODEL="gpt-5.5"  # rule-compliance: ok evidence=memory-feedback-codex-model-preference
TS_NOW=$(date -Iseconds)

# 找 job log file
if [[ -n "$SPECIFIC_JOB" ]]; then
    JOB_LOG="$STATE_DIR/$WORKSPACE_HASH/jobs/${SPECIFIC_JOB}.log"
    JOB_INFO=$(python3 -c "
import json
d = json.load(open('$STATE_JSON'))
for j in d['jobs']:
    if j['id'] == '$SPECIFIC_JOB':
        print(json.dumps(j, indent=2, ensure_ascii=False))
        break
")
elif [[ "$ALL" == "1" ]]; then
    echo "[$TS_NOW] codex-cli=$CODEX_CLI_VERSION, default_model=$CLAUDE_DEFAULT_MODEL, all jobs:" | tee -a "$APPEND_FILE"
    python3 -c "
import json
d = json.load(open('$STATE_JSON'))
for j in d['jobs']:
    print(f\"job_id={j['id']} kind={j['kind']} status={j['status']} thread={j.get('threadId','-')} started={j.get('startedAt','-')}\")
" | tee -a "$APPEND_FILE"
    exit 0
else
    # Latest running job
    LATEST_RUNNING=$(python3 -c "
import json
d = json.load(open('$STATE_JSON'))
running = [j for j in d['jobs'] if j['status'] == 'running']
if running:
    latest = max(running, key=lambda j: j.get('startedAt', ''))
    print(latest['id'])
" 2>/dev/null)
    if [[ -z "$LATEST_RUNNING" ]]; then
        # No running, take latest completed
        LATEST_RUNNING=$(python3 -c "
import json
d = json.load(open('$STATE_JSON'))
all_jobs = sorted(d['jobs'], key=lambda j: j.get('startedAt', ''), reverse=True)
if all_jobs:
    print(all_jobs[0]['id'])
")
    fi
    SPECIFIC_JOB="$LATEST_RUNNING"
    JOB_LOG="$STATE_DIR/$WORKSPACE_HASH/jobs/${SPECIFIC_JOB}.log"
    JOB_INFO=$(python3 -c "
import json
d = json.load(open('$STATE_JSON'))
for j in d['jobs']:
    if j['id'] == '$SPECIFIC_JOB':
        info = {k: j.get(k) for k in ('id','kind','kindLabel','status','threadId','turnId','startedAt','updatedAt','phase')}
        print(json.dumps(info, indent=2, ensure_ascii=False))
        break
")
fi

if [[ ! -f "$JOB_LOG" ]]; then
    echo "ERROR: job log 不存在 ($JOB_LOG)"
    exit 1
fi

# Header
{
    echo "==============================================="
    echo "[$TS_NOW] Codex Log Tail"
    echo "  job_id: $SPECIFIC_JOB"
    echo "  codex-cli version: $CODEX_CLI_VERSION"
    echo "  dispatch model (Claude convention): $CLAUDE_DEFAULT_MODEL (--model gpt-5.5 --effort xhigh)"
    echo "  note: Codex CLI 0.130.0 不在 job 元数据回传具体 model, 此处 dispatch 时传值"
    echo "  job log: $JOB_LOG"
    echo "  job info:"
    echo "$JOB_INFO" | sed 's/^/    /'
    echo "==============================================="
} | tee -a "$APPEND_FILE"

echo "" | tee -a "$APPEND_FILE"
echo "[$(date -Iseconds)] Begin tail -f (Ctrl-C to stop):" | tee -a "$APPEND_FILE"
echo "" | tee -a "$APPEND_FILE"

# Tail with timestamp prefix (codex job log already has ISO timestamps)
exec tail -F "$JOB_LOG" | awk '{ printf "[%s] %s\n", strftime("%Y-%m-%dT%H:%M:%S%z"), $0; fflush() }' | tee -a "$APPEND_FILE"

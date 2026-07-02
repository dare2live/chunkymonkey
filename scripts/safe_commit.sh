#!/usr/bin/env bash
# safe_commit.sh — pre-flight all hooks before commit + optional push + codegraph sync
#
# 防止 "commit 失败 → retry 同一 message → 仍 reject" 浪费时间.
#
# Usage:
#   bash scripts/safe_commit.sh "commit message body"
#   SAFE_COMMIT_NO_PUSH=1 bash scripts/safe_commit.sh "local commit message body"
#
# 流程:
#   1. git status — list staged files
#   2. 跑 backend/scripts/check_project_index_sync.py — 若 fail 提示 + abort
#   3. 跑 backend/scripts/check_rule_compliance.py — 若 fail 提示 + abort
#   4. 验 commit message 含 GROUP A + B keyword
#   4.5 Rule 10 — staged .py 必须含 Codex review 或显式 skip reason
#   5. git commit + optional git push + codegraph sync

set -euo pipefail

MSG="${1:-}"
if [[ -z "$MSG" ]]; then
    echo "用法: bash scripts/safe_commit.sh \"commit message\""
    exit 1
fi

cd "$(dirname "$0")/.."

# 1. Status
echo "=== Step 1: git status ==="
staged=$(git diff --cached --name-only | wc -l | tr -d ' ')
unstaged=$(git diff --name-only | wc -l | tr -d ' ')
if [[ "$staged" == "0" ]]; then
    echo "ERROR: no staged files. 用 git add 先 stage."
    exit 1
fi
echo "staged: $staged files"
git diff --cached --name-only | head -10

# 2. PROJECT_INDEX sync check
echo
echo "=== Step 2: PROJECT_INDEX sync check ==="
if ! PYTHONPATH=backend python backend/scripts/check_project_index_sync.py 2>&1 | tail -5; then
    echo
    echo "ERROR: PROJECT_INDEX.md 未同步."
    echo "修法: 更新 PROJECT_INDEX.md 对应活索引节 (历史叙事写 ledger, 不进 INDEX changelog) + git add PROJECT_INDEX.md"
    exit 2
fi

# 2.6 Feature map refresh — 机器派生视图保鲜 (漂移才写盘, 同 commit 带上)
# alert_level=optional: 生成失败不挡 commit, 但必须可见 (Platform Runtime Contract)
echo
echo "=== Step 2.6: feature map refresh ==="
if PYTHONPATH=backend python backend/scripts/build_feature_map.py --quiet 2>/dev/null; then
    if [[ -n "$(git status --porcelain -- FEATURE_MAP.md)" ]]; then
        git add FEATURE_MAP.md
        echo "[feature-map] 漂移 → 已重生成并 stage 进本次 commit"
    else
        echo "[feature-map] fresh"
    fi
else
    echo "WARNING: [feature-map] 生成失败 (optional 级, 不挡 commit) — 跑 scripts/chunkyctl map 查原因"
fi

# 3. Rule compliance
echo
echo "=== Step 3: rule compliance ==="
if ! PYTHONPATH=backend python backend/scripts/check_rule_compliance.py 2>&1 | tail -5; then
    echo
    echo "ERROR: rule compliance 失败. 见上 error."
    exit 3
fi

# (Step 3.5 leakage audit gate 已删 2026-07-02: 触发实体 build_feature_panel/mart_p0a/
#   fact_capital_flow/dim_stock_tdx_industry/build_market_perception 全已随重建物删 + audit_panel_leakage.py
#   不存在 -f 永假 = 双重死块。真金白银泄漏强制在转正门 record_verdict + CI, 详 ledger)


# 3.6 消费方泄漏闸 已退役 2026-06-28 (残留清理批1c): leakage_consumers.yaml + leakage_probe.py 已删
#   (特征面板/策略 serving 层退役, 消费方不存在)。真金白银泄漏强制移到转正门 record_verdict(confirmed_by_owner=1
#   须带 leakage-clean 证据) + CI server-side, 不在本地 commit gate (mio §7: enforcement 沉到提交者够不到处)。

# 3.7 散落死闸 (G3 门#1, owner=docs/conditional_alpha_program.md §4): staged 的 experiment_*.py
# 必须走 harness/留档层 (phaseD_signal_eval 或 experiment_store), 禁裸跑无留档 → 进 experiment_store
# 唯一真相源, 防"一脚本一实验丢 analysis/*.json"漂移 (根因审计 H1/H2)。
exp_staged=$(git diff --cached --name-only | grep -E "^backend/scripts/experiment_.*\.py$" || true)
if [[ -n "$exp_staged" && -f backend/scripts/check_experiment_harness.py ]]; then
    echo
    echo "=== Step 3.7: experiment harness gate (experiment_*.py staged) ==="
    if PYTHONPATH=backend python backend/scripts/check_experiment_harness.py --staged 2>&1 | tail -15; then
        echo "[experiment-harness] PASS"
    else
        echo
        echo "ERROR: 散落死闸 — staged experiment 脚本裸跑无留档 (不走 phaseD_signal_eval / 不写 experiment_store)。"
        echo "正解: 实验走 services/phaseD_signal_eval.evaluate_signal (唯一 harness) 或 import experiment_store 留档; 不丢 analysis/*.json。"
        echo "真豁免 (非 alpha 实验) = check_experiment_harness.py EXEMPT 显式登记 + 理由, 不 --no-verify 绕。"
        exit 4
    fi
fi

# 3.8 沙盒隔离门 (实验室产物只留实验室, 2026-06-21 立; 4+次隔离失守根治):
# C1 backend引用sandbox(FAIL) / C2 控制面嵌未promote(confirmed_by_owner=0)实验结果(WARN) / C3 探索runner漏主脚本(FAIL)。
echo
echo "=== Step 3.8: sandbox isolation gate ==="
if PYTHONPATH=backend python backend/scripts/check_sandbox_isolation.py 2>&1 | tail -12; then
    echo "[sandbox-isolation] PASS"
else
    echo
    echo "ERROR: 沙盒隔离门 — 测试产物漏进主项目 (C1 backend引用sandbox / C3 探索runner漏主脚本)。"
    echo "正解: 探索弧产物全留 sandbox; promotion 是方法确认后单独步骤 (真edge confirmed_by_owner=1 才进主项目)。"
    echo "误报修 check_sandbox_isolation.py 本身, 不 --no-verify 绕。"
    exit 5
fi

# 3.9 SERVE 读层 P1 门 (数据模块顶层设计 §10 P1 gate 落地, 2026-06-22):
# D1 dossier 0内联裸查 / D2 0自写asof / D3 preflight接线 / D4 entity声明链齐全 / D5 L2-bypass关闭。
echo
echo "=== Step 3.9: SERVE read-layer P1 doors ==="
if PYTHONPATH=backend python backend/scripts/check_serve_read_layer.py; then
    echo "[serve-read-layer] PASS"
else
    echo
    echo "ERROR: SERVE 读层 P1 门红 — consumer 内联裸查/自写asof, 或 entity 声明链断, 或实验runner漏进backend。"
    echo "正解: consumer 取数走 services.data_access.DataAccess; PIT asof 只在 asof_gate; 实验入 sandbox。"
    echo "误报修 check_serve_read_layer.py 本身, 不 --no-verify 绕。"
    exit 5
fi

# 3.95 交易日历强制使用门 (2026-06-22 P1 升硬门, 第零条规定与 universe 同档执法):
# 拦内联绕过交易日历真相源 (wall-clock 当最新/SQL CURRENT_DATE 上界锚); 合法日历天窗口加 evidence 注释。
echo
echo "=== Step 3.95: calendar-usage gate (交易日历强制使用) ==="
if PYTHONPATH=backend python backend/scripts/check_calendar_usage.py --staged --strict; then
    echo "[calendar-usage] PASS"
else
    echo
    echo "ERROR: 交易日历强制使用门红 — 内联 wall-clock(date.today/datetime.now)当最新交易日 或 SQL <=CURRENT_DATE 上界锚。"
    echo "正解: services.calendar.latest_closed_or_raise (交易日历真相源); 合法日历天窗口加 # rule-compliance: ok evidence=。"
    echo "误报修 check_calendar_usage.py 本身, 不 --no-verify 绕。"
    exit 5
fi

# 3.96 血缘漂移门 (M5-T2, 2026-06-26): 结构文件 (registry/schema/data_layers/lineage模块) staged 时,
# 提醒 data/lineage/graph.json 是否随现实漂移。**T2=informational WARN 非 block** (consume 边随任何
# 引用表的文件漂移, block 会高摩擦; 硬闸排到 T4 转正门/CI, mio §7 本地 hook=快反馈非强制)。
STAGED_FOR_LINEAGE=$(git diff --cached --name-only | grep -E 'config/(sync_registry|data_access|data_layers|database_manifest|feature_registry)\.yaml|services/lineage/|scripts/(build_|lineage_cli|check_lineage)' || true)
if [[ -n "$STAGED_FOR_LINEAGE" ]]; then
    echo
    echo "=== Step 3.96: lineage drift (结构文件 staged, informational) ==="
    if PYTHONPATH=backend python backend/scripts/check_lineage_drift.py; then
        :
    else
        echo "[lineage-drift] WARN (非 block): 血缘图与现实漂移 — 跑 chunkyctl lineage build 重生并提交 data/lineage/graph.json"
        echo "  (T2 informational; 硬闸=T4 转正门; 删/迁表前用 chunkyctl lineage impact <table> 自动 fan-in)"
    fi
fi

# 3.97 死引用硬门 (2026-06-28 根因根治): 删模块/表/文件后引用方必须同步清。
# 根因: 之前每波清理删"供给侧"漏"需求侧", 验收够不到孤儿脚本/懒import/guarded垫片/config死路径
# → 残留静默累积。本门 import-services + dead-services-ref + config-dead-path 机械堵死。**硬闸**。
echo
echo "=== Step 3.97: dead-references gate (死引用根治硬门) ==="
if PYTHONPATH=backend python backend/scripts/check_dead_references.py > /tmp/cm_deadref.out 2>&1; then
    grep -E "^\[dead-references\] PASS" /tmp/cm_deadref.out || echo "[dead-references] PASS"
else
    echo
    echo "ERROR: 死引用门红 — 删了模块/表/文件但引用方未清 (孤儿脚本/死import/config引死路径):"
    grep -E "✗|FAIL:" /tmp/cm_deadref.out | head -40
    echo "正解: 删引用方 / repoint 现存 / 引用方也是残留则一并删。误报修 check_dead_references.py 本身, 不 --no-verify 绕。"
    exit 5
fi

# 4. Commit message keyword check (manual preview)
echo
echo "=== Step 4: commit message keyword ==="
keywords_a="测试|test pass|fallback|unit|实测|evidence|backtest|measured|audit|ann|sharpe|max_dd"
keywords_b="PIT|OOS|walk-forward|expanding|实测|evidence|backtest|measured|audit|annual|年化|sharpe|max_dd|calmar"
has_a=$(echo "$MSG" | grep -ciE "$keywords_a" || true)
has_b=$(echo "$MSG" | grep -ciE "$keywords_b" || true)
has_minimal=$(echo "$MSG" | grep -c "commit-msg: minimal" || true)
has_skip=$(echo "$MSG" | grep -c "codex-review: skipped" || true)
if [[ "$has_a" == "0" && "$has_minimal" == "0" ]]; then
    echo "WARNING: commit message 缺 GROUP A 关键词 (test/fallback/实测/evidence/...)"
    echo "建议加 '# commit-msg: minimal' 或加关键词"
fi
echo "GROUP A match: $has_a, GROUP B match: $has_b, minimal: $has_minimal, codex-skip: $has_skip"

# 4.5. Codex review gate (Rule 10 blocking)
echo
echo "=== Step 4.5: Codex review gate (Rule 10) ==="
MIN_CODEX_SKIP_REASON_CHARS=8
py_staged=$(git diff --cached --name-only -- '*.py' | wc -l | tr -d ' ')
has_codex=$(echo "$MSG" | grep -cE "Codex-Reviewed:[[:space:]]*(APPROVE_WITH_NOTES|APPROVE)([[:space:]]|$|\\()" || true)
has_request_changes=$(echo "$MSG" | grep -cE "Codex-Reviewed:[[:space:]]*REQUEST_CHANGES([[:space:]]|$|\\()" || true)
skip_reason=$(
    printf '%s\n' "$MSG" | awk '
        {
            pos = index($0, "codex-review: skipped reason=");
            if (pos > 0) {
                reason = substr($0, pos + length("codex-review: skipped reason="));
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", reason);
                if (reason != "") {
                    print reason;
                    exit;
                }
            }
        }
    '
)
has_skip_reason=0
if [[ "${#skip_reason}" -ge "$MIN_CODEX_SKIP_REASON_CHARS" ]]; then
    has_skip_reason=1
fi
if [[ "$py_staged" -gt 0 ]]; then
    # 2026-06-12 用户决议: Codex review 强制解除 — 改为信息性提示, 不阻塞。
    # 质量闸保留: 单测 + self-check 5 项 + 重大改动对抗复审 (workflow)。
    echo "Rule 10 (informational): staged .py=$py_staged, Codex-Reviewed=$has_codex, skip_reason=$has_skip_reason"
else
    echo "Rule 10 skipped: no staged .py files"
fi

# 5. Commit + optional push + codegraph
echo
echo "=== Step 5: commit + optional push + codegraph sync ==="
if [[ "${SAFE_COMMIT_DRY_RUN:-0}" == "1" ]]; then
    echo "SAFE_COMMIT_DRY_RUN=1: stopping before git commit/push/codegraph sync."
    exit 0
fi
git commit -m "$MSG"
if [[ "${SAFE_COMMIT_NO_PUSH:-0}" == "1" ]]; then
    echo "SAFE_COMMIT_NO_PUSH=1: skipping git push."
else
    git push
fi
codegraph sync 2>&1 | tail -1 || true
echo
if [[ "${SAFE_COMMIT_NO_PUSH:-0}" == "1" ]]; then
    echo "DONE: commit + no-push + codegraph sync 完成"
else
    echo "DONE: commit + push + codegraph sync 完成"
fi

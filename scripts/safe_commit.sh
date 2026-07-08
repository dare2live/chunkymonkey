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

# 3.9 SERVE 读层门 (数据模块顶层设计 §10 P1 gate 落地, 2026-06-22; 2026-07-08 系统性收口):
# D1 全量非成员消费者内联裸查(data_module_members.yaml 区分 builder vs 消费者, 替代原只扫
# dossier.py 的伪绿门, 见 analysis/serve_read_layer_gate_consolidation_20260708.md) /
# D2 preflight接线 / D3 entity声明链齐全 / D4 L2-bypass关闭。
echo
echo "=== Step 3.9: SERVE read-layer doors ==="
if PYTHONPATH=backend python backend/scripts/check_serve_read_layer.py; then
    echo "[serve-read-layer] PASS"
else
    echo
    echo "ERROR: SERVE 读层门红 — 非成员消费者内联裸查, 或 entity 声明链断, 或实验runner漏进backend。"
    echo "正解: 消费者取数走 services.data_access.DataAccess; 加工builder登记进 data_module_members.yaml; 实验入 sandbox。"
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
STAGED_FOR_LINEAGE=$(git diff --cached --name-only | grep -E 'config/(sync_registry|data_access|data_layers|database_manifest)\.yaml|services/lineage/|scripts/(build_|lineage_cli|check_lineage)' || true)
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

# Step 3.98: grain 唯一性门 (R1 根因1: grain 声明错误在良性期抓, 防批内去重升级成静默销毁;
#   --strict 默认关 → 跑批写锁期库不可达优雅跳过不阻塞 commit; 豁免带到期日, 过期自动恢复 FAIL)
echo
echo "=== Step 3.98: grain-uniqueness gate (grain 持续审计门) ==="
if PYTHONPATH=backend python backend/scripts/check_grain_uniqueness.py \
     --exempt mart_sector_pulse_daily:20260710 > /tmp/cm_grain.out 2>&1; then
    tail -1 /tmp/cm_grain.out
else
    echo
    echo "ERROR: grain 门红 — 某表按声明 grain 有重复组 (grain 声明不足或 MERGE 幂等破坏):"
    grep -E "FAIL|dup" /tmp/cm_grain.out | head -20
    echo "正解: 核 grain 是否漏列 (report_rc/block_trade 反例) → 修 registry grain + 重拉自清。误报修脚本本身。"
    exit 5
fi

# Step 3.99: continuity-integrity gate (2026-07-06 全面数据审计根因根治 —
#   check_continuity_integrity.py 自 07-05 加入起就一直未接入 safe_commit/CI, 审计当场抓到
#   这正是"新增治理脚本未同批注册"模式在本 session 活生生重演; 非 strict 默认库不可达优雅
#   跳过不阻塞 commit, 只有真 FAIL[calendar_gaps/cross_section/group_freshness/
#   declared_vs_actual/static_staleness]才 abort)
echo
echo "=== Step 3.99: continuity-integrity gate (数据连续性常驻审查) ==="
if PYTHONPATH=backend python backend/scripts/check_continuity_integrity.py > /tmp/cm_continuity.out 2>&1; then
    tail -1 /tmp/cm_continuity.out
else
    echo
    echo "ERROR: continuity-integrity 门红 — 某域出现日历缺口/横截面骤降/分组断流/声明-实测漂移/静态域断流:"
    grep -E "^\[fail\]" /tmp/cm_continuity.out | head -20
    echo "正解: 核实是真数据缺口(重拉补) 还是 registry 声明漂移(改 data_start/加 known_empty_days)。误报修脚本本身, 不 --no-verify 绕。"
    exit 5
fi

# Step 3.991/992: 2026-07-06 全面数据审计根因根治 — 治理脚本覆盖率盲区之二: 18 个
# check_*.py 里 5 个此前完全未接入任何机制(safe_commit/CI/git hooks 都没有)。逐个实测:
# check_config_refs/check_doc_drift 现在都能跑通且真 PASS, 检查内容仍有价值(层词汇/文档
# 死引用一致性) → 接成硬闸(与其余静态一致性门同级别)。
echo
echo "=== Step 3.991: config-refs gate (data_access/data_layers 层词汇一致性) ==="
if PYTHONPATH=backend python backend/scripts/check_config_refs.py > /tmp/cm_configrefs.out 2>&1; then
    tail -2 /tmp/cm_configrefs.out
else
    echo
    echo "ERROR: config-refs 门红 — data_access entity 引用的 layer 词汇在 data_layers.yaml 里没有定义:"
    cat /tmp/cm_configrefs.out
    echo "正解: 补 data_layers.yaml 声明 / 改 data_access.yaml 用现存层名。误报修脚本本身, 不 --no-verify 绕。"
    exit 5
fi

echo
echo "=== Step 3.992: doc-drift gate (11 活文档死引用) ==="
if PYTHONPATH=backend python backend/scripts/check_doc_drift.py > /tmp/cm_docdrift.out 2>&1; then
    tail -2 /tmp/cm_docdrift.out
else
    echo
    echo "ERROR: doc-drift 门红 — 活文档(活索引/AGENTS/docs)引用了已删文件路径:"
    cat /tmp/cm_docdrift.out
    echo "正解: 改文档指向现存路径 / 标 deprecated 头注豁免。误报修脚本本身, 不 --no-verify 绕。"
    exit 5
fi

# Step 3.993/994: check_doc_governance / check_legacy_flow_integrity 都能跑通, 但暂不适合
# 硬闸——check_doc_governance 当前 21 条已知 WARN 待清 (脚本自身设计就是"先 WARN, 清完 backlog
# 再翻 FAIL", 现在硬挡会拦下与本次改动无关的历史 backlog); check_legacy_flow_integrity 的
# C1 子检查因架构变迁已空转 PASS (查无可查, 见 P2 记录), 整体判断力打折。两者都先接成
# informational (不 block), 曝光但不拦, 待 backlog 清完/C1 修复后再考虑升硬闸。
echo
echo "=== Step 3.993: doc-governance (informational, 21 条已知 backlog 待清) ==="
PYTHONPATH=backend python backend/scripts/check_doc_governance.py 2>&1 | tail -3

echo
echo "=== Step 3.994: legacy-flow-integrity (informational, C1 子检查架构变迁后空转) ==="
PYTHONPATH=backend python backend/scripts/check_legacy_flow_integrity.py > /tmp/cm_legacyflow.out 2>&1
python3 -c "
import json
try:
    d = json.load(open('/tmp/cm_legacyflow.out'))
    checks = d.get('checks', {}) if isinstance(d, dict) else {}
    print('overall=' + str(d.get('overall')), {k: v.get('verdict') for k, v in checks.items()})
except Exception:
    print(open('/tmp/cm_legacyflow.out').read()[-300:])
"

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

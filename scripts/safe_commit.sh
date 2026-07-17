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
#   4.5 Rule 10 — 高风险 staged 文件必须有 canonical Codex APPROVE verdict
#   5. git commit + optional git push + codegraph sync

set -euo pipefail

cd "$(dirname "$0")/.."

# python 解析器 (2026-07-10 根治, mythos "env PATH 双前提陷阱"第N例实锤): 机器重启后裸 `python`
# 从 PATH 消失(homebrew 无 python 非版本化符号链) + PATH 顺序变化使 python3 解析到系统 3.9(无
# duckdb/yaml) → 全部治理门 command-not-found 假红。优先项目 venv(全部依赖保证在), 沙箱/无 venv
# 环境回退 python3/python(测试 stub 门只用 stdlib, 系统 python3 足够)。
if [[ -x ".venv/bin/python3" ]]; then
    PY="$(pwd)/.venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
else
    PY="$(command -v python)"
fi

MSG="${1:-}"
if [[ -z "$MSG" ]]; then
    echo "用法: bash scripts/safe_commit.sh \"commit message\""
    exit 1
fi

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

# Export the exact Git index once and reuse it for every static ownership/map gate.
# The live worktree may contain another intentionally unstaged slice; it must neither
# feed a false green nor contaminate generated artifacts for this commit.
STAGED_INDEX_DIR=$(mktemp -d /tmp/chunkymonkey-staged-index.XXXXXX)
trap 'rm -rf "$STAGED_INDEX_DIR"' EXIT
git checkout-index --all --prefix="$STAGED_INDEX_DIR/"
STAGED_BACKEND="$STAGED_INDEX_DIR/backend"

# Give staged-snapshot checks a real tracked-file inventory without borrowing the
# parent worktree. Production DuckDB files are ignored, so expose them read-only
# through symlinks for static import/table/lineage checks that need catalog truth.
git -C "$STAGED_INDEX_DIR" init -q
git -C "$STAGED_INDEX_DIR" add -f -A
mkdir -p "$STAGED_INDEX_DIR/data"
for db in "$(pwd)"/data/*.duckdb; do
    [[ -e "$db" ]] || continue
    ln -s "$db" "$STAGED_INDEX_DIR/data/$(basename "$db")"
done

# 2. PROJECT_INDEX sync check
echo
echo "=== Step 2: PROJECT_INDEX sync check ==="
if [[ ! -f "$STAGED_BACKEND/scripts/check_project_index_sync.py" ]]; then
    echo "ERROR: staged snapshot 缺 check_project_index_sync.py，拒绝用 worktree 版本代验。"
    exit 2
fi
if ! PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_project_index_sync.py" 2>&1 | tail -5; then
    echo
    echo "ERROR: PROJECT_INDEX.md 未同步."
    echo "修法: 更新 PROJECT_INDEX.md 对应活索引节 (历史叙事写 ledger, 不进 INDEX changelog) + git add PROJECT_INDEX.md"
    exit 2
fi

# 2.6 Feature map gate — 从 Git index 导出 staged snapshot，在隔离目录重建 CodeGraph 后验真。
# 禁止 safe_commit 自动改写或 stage 当前工作树，避免未提交的其他 dirty slice 混入派生地图。
echo
echo "=== Step 2.6: feature map gate (staged snapshot, read-only) ==="
if [[ -f "$STAGED_BACKEND/scripts/build_feature_map.py" ]]; then
    if ! codegraph init "$STAGED_INDEX_DIR" > "$STAGED_INDEX_DIR/codegraph-sync.out" 2>&1; then
        tail -20 "$STAGED_INDEX_DIR/codegraph-sync.out"
        echo "ERROR: staged snapshot CodeGraph 初始化失败，无法验证 FEATURE_MAP。"
        exit 2
    fi
    if ! codegraph sync "$STAGED_INDEX_DIR" >> "$STAGED_INDEX_DIR/codegraph-sync.out" 2>&1; then
        tail -20 "$STAGED_INDEX_DIR/codegraph-sync.out"
        echo "ERROR: staged snapshot CodeGraph 构建失败，无法验证 FEATURE_MAP。"
        exit 2
    fi
    if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/build_feature_map.py" --check --quiet; then
        echo "[feature-map] staged snapshot fresh; current worktree was not modified"
    else
        echo "ERROR: staged FEATURE_MAP.md 与 staged source snapshot 不一致。"
        echo "正解: 在隔离 staged snapshot 上重生、review 后显式 stage；不得提交当前 dirty-tree 派生物。"
        exit 2
    fi
else
    echo "ERROR: staged snapshot 缺 build_feature_map.py；生成地图门不得静默跳过。"
    exit 2
fi

# 2.7 Moth gate — Moth 0.3.0 resolves profile repo_path relative to process cwd.
# Always enter the exported snapshot before invoking it; passing an absolute --repo
# from the parent checkout can otherwise make assertions/CodeGraph inspect dirty main.
echo
echo "=== Step 2.7: Moth gates (exact staged snapshot) ==="
if [[ -f "$STAGED_INDEX_DIR/.moth/profile.yaml" ]]; then
    if ! command -v moth >/dev/null 2>&1; then
        echo "ERROR: staged snapshot 声明 Moth profile，但 moth CLI 不可用。"
        exit 2
    fi
    if [[ -d "$(pwd)/.venv" && ! -e "$STAGED_INDEX_DIR/.venv" ]]; then
        ln -s "$(pwd)/.venv" "$STAGED_INDEX_DIR/.venv"
    fi
    if ! (cd "$STAGED_INDEX_DIR" && moth assert --repo .); then
        echo "ERROR: staged snapshot Moth assertions 未闭合。"
        exit 2
    fi
    if ! (cd "$STAGED_INDEX_DIR" && moth coupling --repo .); then
        echo "ERROR: staged snapshot Moth coupling 未闭合。"
        exit 2
    fi
    echo "[moth] staged snapshot PASS"
else
    echo "[moth] no staged profile; not applicable to this fixture/repository"
fi

# 3. Rule compliance
echo
echo "=== Step 3: rule compliance ==="
if [[ ! -f "$STAGED_BACKEND/scripts/check_rule_compliance.py" ]]; then
    echo "ERROR: staged snapshot 缺 check_rule_compliance.py，拒绝用 worktree 版本代验。"
    exit 3
fi
if ! PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_rule_compliance.py" 2>&1 | tail -5; then
    echo
    echo "ERROR: rule compliance 失败. 见上 error."
    exit 3
fi

# (Step 3.5 旧 leakage audit gate 已删 2026-07-02: 触发实体与 verifier 同时退役。
#   safe_commit 不因此声称策略泄漏已验证；策略发布必须另走 docs/strategy_validation_contract.md。)


# 3.6 旧消费方 leakage gate 已随当时的特征/策略 serving 层退役。
#   当前尚无 StrategyRelease，因此本地提交门不冒充 PIT/发布证书。

# 3.7 散落死闸已退役；当前 gate owner=docs/engineering_governance.md + live scripts/tests：
#   check_experiment_harness.py 本体 + 被守护的 harness 层(phaseD_signal_eval.py/experiment_store.py)
#   + 当时的 conditional-alpha owner 文档均已随 2026-06-28 reset 删除。原有
#   -f 存在性守卫已优雅跳过多日(非报错), 但触发条件(backend/scripts/experiment_*.py 被 staged)
#   本身也已被 check_serve_read_layer.py D4(feature-from-l2)硬性禁止(该目录不许有 experiment_*.py
#   文件存在), 双重确认这块永不会再触发, 整块死代码物删而非继续留守卫。
#   当前策略研究/发布边界 owner=docs/strategy_validation_contract.md。

# 3.8 沙盒隔离门 (实验室产物只留实验室, 2026-06-21 立; 4+次隔离失守根治):
# C1 backend引用sandbox(FAIL) / C2 控制面嵌未promote(confirmed_by_owner=0)实验结果(WARN) / C3 探索runner漏主脚本(FAIL)。
echo
echo "=== Step 3.8: sandbox isolation gate ==="
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_sandbox_isolation.py" 2>&1 | tail -12; then
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
# dossier.py 的伪绿门；red→green 证据已固化在对应 tests) /
# D2 preflight接线 / D3 entity声明链齐全 / D4 L2-bypass关闭。
echo
echo "=== Step 3.9: SERVE read-layer doors ==="
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_serve_read_layer.py"; then
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
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_calendar_usage.py" --strict; then
    echo "[calendar-usage] PASS"
else
    echo
    echo "ERROR: 交易日历强制使用门红 — 内联 wall-clock(date.today/datetime.now)当最新交易日 或 SQL <=CURRENT_DATE 上界锚。"
    echo "正解: services.calendar.latest_closed_or_raise (交易日历真相源); 合法日历天窗口加 # rule-compliance: ok evidence=。"
    echo "误报修 check_calendar_usage.py 本身, 不 --no-verify 绕。"
    exit 5
fi

# 3.96 血缘漂移门: 每次提交都用同一 staged snapshot + live read-only catalogs 重建对账。
# 普通 consumer 删除同样会改变 consume edge，不能靠结构文件 regex 猜触发面。
echo
echo "=== Step 3.96: lineage drift (staged snapshot, blocking) ==="
if [[ ! -f "$STAGED_BACKEND/scripts/check_lineage_drift.py" ]]; then
    echo "ERROR: staged snapshot 缺 check_lineage_drift.py。"
    exit 5
fi
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_lineage_drift.py"; then
    echo "[lineage-drift] staged snapshot PASS"
else
    echo "ERROR: staged 血缘图与 staged source/config/live catalog 漂移。"
    echo "正解: 从 exact staged snapshot 重生 data/lineage/graph.json 后显式 stage。"
    exit 5
fi

# 3.97 死引用硬门 (2026-06-28 根因根治): 删模块/表/文件后引用方必须同步清。
# 根因: 之前每波清理删"供给侧"漏"需求侧", 验收够不到孤儿脚本/懒import/guarded垫片/config死路径
# → 残留静默累积。本门 import-services + dead-services-ref + config-dead-path 机械堵死。**硬闸**。
echo
echo "=== Step 3.97: dead-references gate (死引用根治硬门) ==="
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_dead_references.py" > /tmp/cm_deadref.out 2>&1; then
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
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_grain_uniqueness.py" \
     --exempt mart_sector_pulse_daily:20260710 > /tmp/cm_grain.out 2>&1; then
    tail -1 /tmp/cm_grain.out
else
    echo
    echo "ERROR: grain 门红 — 某表按声明 grain 有重复组 (grain 声明不足或 MERGE 幂等破坏):"
    grep -E "FAIL|dup" /tmp/cm_grain.out | head -20
    echo "正解: 核 grain 是否漏列 (report_rc/block_trade 反例) → 修 registry grain + 重拉自清。误报修脚本本身。"
    exit 5
fi

# Step 3.99: continuity-integrity evidence (2026-07-06 全面数据审计根因根治 —
#   check_continuity_integrity.py 自 07-05 加入起就一直未接入 safe_commit/CI, 审计当场抓到
#   这正是"新增治理脚本未同批注册"模式在本 session 活生生重演; 非 strict 默认库不可达优雅
#   跳过。2026-07-17 分离 code fitness 与 mutable data readiness：provider/DB 的 live FAIL
#   必须原样曝光并继续阻断 daily pipeline/消费 readiness，但不能让修复该故障的代码也无法提交。
#   静态 verifier/测试本身仍由前述 exact-index gates + Rule 10 阻断。
echo
echo "=== Step 3.99: continuity-integrity evidence (live data readiness) ==="
CONTINUITY_JSON="$STAGED_INDEX_DIR/.continuity.json"
CONTINUITY_ERR="$STAGED_INDEX_DIR/.continuity.err"
set +e
PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_continuity_integrity.py" \
    --json > "$CONTINUITY_JSON" 2> "$CONTINUITY_ERR"
continuity_rc=$?
set -e

# A live data FAIL may coexist with code fitness, but the verifier itself must be trustworthy:
# parseable JSON, coherent counts/overall, and an exit code that agrees with the report.
if ! continuity_state=$("$PY" - "$CONTINUITY_JSON" "$continuity_rc" <<'PY'
import json
import sys
from collections import Counter

path, raw_rc = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    overall = payload["overall"]
    latest_expected = payload["latest_expected"]
    checks = payload["checks"]
    counts = payload["summary"]["counts"]
    if not isinstance(payload, dict) or overall not in {"PASS", "WARN", "FAIL"}:
        raise ValueError("invalid overall/checks")
    if not isinstance(checks, list) or not isinstance(counts, dict):
        raise ValueError("invalid checks/counts")
    if not isinstance(latest_expected, str) or len(latest_expected) != 8 or not latest_expected.isdigit():
        raise ValueError(f"invalid latest_expected={latest_expected!r}")
    if not checks:
        raise ValueError("empty checks cannot establish readiness")

    actual = Counter()
    for index, row in enumerate(checks):
        if not isinstance(row, dict):
            raise ValueError(f"check[{index}] is not an object")
        status = row.get("status")
        for field in ("check", "domain", "table", "detail"):
            if not isinstance(row.get(field), str):
                raise ValueError(f"check[{index}] missing string {field}")
        category = (
            "fail" if isinstance(status, str) and status.startswith("fail")
            else "warn" if isinstance(status, str) and status.startswith("warn")
            else "pass" if status == "pass"
            else "db_unreachable" if status == "db_unreachable"
            else "skipped" if isinstance(status, str) and status.startswith("skipped")
            else None
        )
        if category is None:
            raise ValueError(f"check[{index}] invalid status={status!r}")
        actual[category] += 1

    allowed_categories = {"pass", "warn", "fail", "skipped", "db_unreachable"}
    unknown_categories = set(counts) - allowed_categories
    if unknown_categories:
        raise ValueError(f"unknown count categories={sorted(unknown_categories)}")
    declared = {}
    for category in sorted(allowed_categories):
        value = counts.get(category, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"invalid count {category}={value!r}")
        declared[category] = value
        if value != actual[category]:
            raise ValueError(
                f"summary/check mismatch {category}: declared={value} actual={actual[category]}"
            )
    fail_count = declared["fail"]
    warn_count = declared["warn"]
    skipped_count = declared["skipped"]
    db_unreachable = declared["db_unreachable"]
    expected_overall = "FAIL" if fail_count else "WARN" if warn_count else "PASS"
    if overall != expected_overall:
        raise ValueError(f"overall/count mismatch: {overall} vs {expected_overall}")
    expected_rc = 1 if overall == "FAIL" else 0
    if int(raw_rc) != expected_rc:
        raise ValueError(f"exit/report mismatch: rc={raw_rc} overall={overall}")
except Exception as exc:
    print(f"invalid:{type(exc).__name__}:{exc}", file=sys.stderr)
    raise SystemExit(1)

if overall == "FAIL":
    state = "BLOCKED"
elif db_unreachable or skipped_count:
    state = "UNVERIFIED"
elif overall == "WARN":
    state = "DEGRADED"
else:
    state = "READY"
print(
    f"{state}|{overall}|{declared['pass']}|{fail_count}|{warn_count}|"
    f"{skipped_count}|{db_unreachable}"
)
PY
); then
    echo "CONTINUITY VERIFIER ERROR — malformed/missing report or exit/report contradiction."
    cat "$CONTINUITY_ERR" 2>/dev/null || true
    head -20 "$CONTINUITY_JSON" 2>/dev/null || true
    exit 5
fi

IFS='|' read -r continuity_class continuity_overall continuity_pass continuity_fail \
    continuity_warn continuity_skipped continuity_db <<< "$continuity_state"
echo "continuity-integrity: overall=$continuity_overall pass=$continuity_pass fail=$continuity_fail warn=$continuity_warn skipped=$continuity_skipped db_unreachable=$continuity_db"

print_continuity_nonpass() {
    "$PY" - "$CONTINUITY_JSON" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
for item in [row for row in payload["checks"] if row["status"] != "pass"][:20]:
    print(f"[{item['status']}] {item['check']} {item['domain']} {item['table']}: {item['detail']}")
PY
}

case "$continuity_class" in
    BLOCKED )
        echo "LIVE DATA READINESS: BLOCKED — non-blocking for code commit"
        print_continuity_nonpass
        echo "code commit continues; COMMIT ALLOWED != TIER0 DATA READY."
        echo "修复/重拉后须单独重跑 continuity；禁止调阈值、墓碑或伪数据洗绿。"
        ;;
    UNVERIFIED )
        echo "LIVE DATA READINESS: UNVERIFIED — skipped/DB-unreachable checks prevent a readiness claim."
        print_continuity_nonpass
        echo "COMMIT ALLOWED != TIER0 DATA READY."
        ;;
    DEGRADED )
        echo "LIVE DATA READINESS: DEGRADED — continuity warnings remain."
        print_continuity_nonpass
        echo "COMMIT ALLOWED != TIER0 DATA READY."
        ;;
    READY )
        echo "LIVE DATA READINESS: READY"
        ;;
    * )
        echo "CONTINUITY VERIFIER ERROR — unknown classification: $continuity_state"
        exit 5
        ;;
esac

# Step 3.991/992: 2026-07-06 全面数据审计根因根治 — 治理脚本覆盖率盲区之二: 18 个
# check_*.py 里 5 个此前完全未接入任何机制(safe_commit/CI/git hooks 都没有)。逐个实测:
# check_config_refs/check_doc_drift 现在都能跑通且真 PASS, 检查内容仍有价值(层词汇/文档
# 死引用一致性) → 接成硬闸(与其余静态一致性门同级别)。
echo
echo "=== Step 3.991: config-refs gate (data_access/data_layers 层词汇一致性) ==="
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_config_refs.py" > /tmp/cm_configrefs.out 2>&1; then
    tail -2 /tmp/cm_configrefs.out
else
    echo
    echo "ERROR: config-refs 门红 — data_access entity 引用的 layer 词汇在 data_layers.yaml 里没有定义:"
    cat /tmp/cm_configrefs.out
    echo "正解: 补 data_layers.yaml 声明 / 改 data_access.yaml 用现存层名。误报修脚本本身, 不 --no-verify 绕。"
    exit 5
fi

echo
echo "=== Step 3.992: doc-drift gate (live docs + generated map + source/config owners) ==="
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_doc_drift.py" > /tmp/cm_docdrift.out 2>&1; then
    tail -2 /tmp/cm_docdrift.out
else
    echo
    echo "ERROR: doc-drift 门红 — 活文档(活索引/AGENTS/docs)引用了已删文件路径:"
    cat /tmp/cm_docdrift.out
    echo "正解: 改文档指向现存路径 / 标 deprecated 头注豁免。误报修脚本本身, 不 --no-verify 绕。"
    exit 5
fi

# Step 3.993: 活文档治理硬闸。WARN 也是未闭合状态，不能随 commit 传播。
echo
echo "=== Step 3.993: doc-governance (fails=0, warns=0) ==="
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_doc_governance.py" > /tmp/cm_docgov.out 2>&1; then
    tail -3 /tmp/cm_docgov.out
else
    cat /tmp/cm_docgov.out
    echo
    echo "ERROR: doc-governance 门红 — FAIL/WARN 均不得提交。"
    echo "正解: 修 owner、退役 CLI、幽灵链接或文档生命周期；不放宽 gate。"
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

# 4.5. Codex review gate (Rule 10 blocking; one policy owner shared with commit-msg hook)
echo
echo "=== Step 4.5: Codex review gate (Rule 10) ==="
REVIEW_MSG_FILE="$STAGED_INDEX_DIR/COMMIT_EDITMSG"
printf '%s\n' "$MSG" > "$REVIEW_MSG_FILE"
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_codex_review.py" "$REVIEW_MSG_FILE"; then
    echo "Rule 10 PASS: canonical staged check_codex_review gate"
else
    echo "ERROR: Rule 10 blocking review gate failed."
    exit 6
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
if ! codegraph sync 2>&1 | tail -1; then
    echo
    echo "ERROR: commit 已创建，但 CodeGraph sync 失败；交付状态为 PARTIAL。"
    echo "恢复命令: codegraph sync ."
    exit 7
fi
echo
if [[ "${SAFE_COMMIT_NO_PUSH:-0}" == "1" ]]; then
    echo "DONE: commit + no-push + codegraph sync 完成"
else
    echo "DONE: commit + push + codegraph sync 完成"
fi

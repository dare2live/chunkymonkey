#!/usr/bin/env bash
# safe_commit.sh — pre-flight hooks before commit + optional push + codegraph sync
#
# 防止 "commit 失败 → retry 同一 message → 仍 reject" 浪费时间.
#
# Usage:
#   bash scripts/safe_commit.sh "commit message body"
#   SAFE_COMMIT_NO_PUSH=1 bash scripts/safe_commit.sh "local commit message body"
#
# WP1 tiered DoD (machine-classified; agent cannot self-downgrade):
#   L1 docs/analysis/sandbox → light doc gates
#   L2 tests/routers/frontend → narrow static gates + Rule 10
#   L3 writer/PIT/schema/config/deletion/unknown → full gates (unchanged)
# Classifier owner: backend/scripts/classify_commit_tier.py +
# backend/config/commit_tiers.yaml. Missing/bad classifier → L3 fail-closed.
#
# 门分布 (正交于 tier; goal.md「治理体系重构」P1, owner backend/config/governance_gates.yaml):
#   diff_correctness → 阻断 (这次 diff 本身错, 受害时刻就是 commit)
#   system_health    → commit 路径不跑, 归 scripts/daily_update.sh 运行时自检
#                      (查的是 live 数据与 config 生效性, 与本次 diff 无关)
#   scaffold         → warn-only, 批量修 `scripts/chunkyctl scaffold-fix`
#                      (坏掉只让下一个接手的人多花时间, 不让系统出错)
# 策略文件不可读 → 全部门按 diff_correctness 阻断 (fail-closed, 等于旧行为)。
#
# 流程:
#   1. git status — list staged files
#   1.5 classify_commit_tier → COMMIT_TIER + gate allowlist
#   2+  per-tier gate subset (L3 = all current gates)
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

# 1.5 Commit tier classification (fail-closed → L3 full gates)
echo
echo "=== Step 1.5: commit tier classification ==="
COMMIT_TIER="L3"
COMMIT_TIER_GATES="staged_worktree_parity project_index_sync feature_map moth rule_compliance ci_pytest sandbox_isolation serve_read_layer calendar_usage population_contract lineage_drift dead_references grain_uniqueness continuity no_emoji config_refs doc_drift doc_governance doc_runtime_state commit_msg rule10"
if [[ -f "backend/scripts/classify_commit_tier.py" && -f "backend/config/commit_tiers.yaml" ]]; then
    if COMMIT_TIER_JSON=$(PYTHONPATH=backend "$PY" backend/scripts/classify_commit_tier.py 2>/tmp/cm_tier_err.out); then
        if parsed=$("$PY" -c '
import json, sys
d = json.loads(sys.argv[1])
tier = d.get("tier")
gates = d.get("gates")
if tier not in {"L1", "L2", "L3"} or not isinstance(gates, list) or not gates:
    raise SystemExit(1)
print(tier)
print(" ".join(str(g) for g in gates))
' "$COMMIT_TIER_JSON" 2>/dev/null); then
            COMMIT_TIER=$(printf '%s\n' "$parsed" | sed -n '1p')
            COMMIT_TIER_GATES=$(printf '%s\n' "$parsed" | sed -n '2p')
            echo "[commit-tier] $COMMIT_TIER gates=[$COMMIT_TIER_GATES]"
            echo "$COMMIT_TIER_JSON" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print("[commit-tier] reasons:", "; ".join(d.get("reasons") or [])[:240])'
        else
            echo "WARNING: classify_commit_tier JSON unparseable → L3 fail-closed"
            cat /tmp/cm_tier_err.out 2>/dev/null || true
        fi
    else
        echo "WARNING: classify_commit_tier failed → L3 fail-closed"
        cat /tmp/cm_tier_err.out 2>/dev/null || true
    fi
else
    echo "WARNING: classify_commit_tier.py or commit_tiers.yaml missing → L3 fail-closed"
fi

# ── 1.6 门分布策略 (goal.md「治理体系重构」P1; owner backend/config/governance_gates.yaml) ──
# 三组按「谁受害、何时受害」切:
#   diff_correctness = 这次 diff 本身错   → commit 阻断
#   system_health    = 数据/策略/钱受害   → commit 不跑, 归 daily_update 运行时自检
#   scaffold         = 下一个开发者受害   → warn-only + `scripts/chunkyctl scaffold-fix`
# tier 剪枝 (L1/L2/L3) 是正交的另一维, owner 仍是 commit_tiers.yaml, 两者都满足才跑。
# fail-closed: 策略不可读 → 两个名单都空 → 所有门按 diff_correctness 阻断 (= 旧行为)。
GATES_SCAFFOLD="$(PYTHONPATH=backend "$PY" backend/scripts/gate_policy.py --names scaffold 2>/dev/null || true)"
GATES_SYSTEM_HEALTH="$(PYTHONPATH=backend "$PY" backend/scripts/gate_policy.py --names system_health 2>/dev/null || true)"
if [[ -z "$GATES_SCAFFOLD" || -z "$GATES_SYSTEM_HEALTH" ]]; then
    echo "WARNING: 门分布策略不可用 → 全部门按 diff_correctness 阻断 (fail-closed)"
    GATES_SCAFFOLD=""
    GATES_SYSTEM_HEALTH=""
else
    echo "[gate-policy] scaffold=warn-only:[$GATES_SCAFFOLD]"
    echo "[gate-policy] system_health→daily_update:[$GATES_SYSTEM_HEALTH]"
fi
SCAFFOLD_WARNED=""

gate_group() {
    case " $GATES_SCAFFOLD " in *" $1 "*) echo scaffold; return 0 ;; esac
    case " $GATES_SYSTEM_HEALTH " in *" $1 "*) echo system_health; return 0 ;; esac
    echo diff_correctness
}

gate_enabled() {
    local g="$1"
    # system_health 组在 commit 路径不跑 —— 它查的是 live 数据与 config 生效性,
    # 与本次 diff 无关; 受害时刻在运行时, 门已归位 daily_update。
    case " $GATES_SYSTEM_HEALTH " in
        *" $g "*) return 1 ;;
    esac
    case " $COMMIT_TIER_GATES " in
        *" $g "*) return 0 ;;
        *) return 1 ;;
    esac
}

# 门失败的统一出口: 分组决定后果 (阻断 vs 记 warn 续跑)。不许在门体内直接 exit,
# 否则「后果由分组定义」就退化成散落在 700 行里的硬编码。
gate_fail() {
    local g="$1" code="$2"
    if [[ "$(gate_group "$g")" == "scaffold" ]]; then
        echo "WARN-ONLY [$g]: 脚手架门未闭合 — 不阻断提交 (受害者=下一个开发者, 非本次 diff)。"
        echo "  批量修: scripts/chunkyctl scaffold-fix"
        SCAFFOLD_WARNED="$SCAFFOLD_WARNED $g"
        return 0
    fi
    exit "$code"
}

# 2. PROJECT_INDEX sync check
echo
echo "=== Step 2: PROJECT_INDEX sync check ==="
if gate_enabled project_index_sync; then
if [[ ! -f "$STAGED_BACKEND/scripts/check_project_index_sync.py" ]]; then
    echo "ERROR: staged snapshot 缺 check_project_index_sync.py，拒绝用 worktree 版本代验。"
    gate_fail project_index_sync 2
elif ! PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_project_index_sync.py" 2>&1 | tail -5; then
    echo
    echo "ERROR: PROJECT_INDEX.md 未同步."
    echo "修法: 更新 PROJECT_INDEX.md 对应活索引节 (历史叙事写 ledger, 不进 INDEX changelog) + git add PROJECT_INDEX.md"
    gate_fail project_index_sync 2
fi
else
    echo "[commit-tier] skip project_index_sync (tier=$COMMIT_TIER)"
fi

# 2.6 Feature map gate — 从 Git index 导出 staged snapshot，在隔离目录重建 CodeGraph 后验真。
# 禁止 safe_commit 自动改写或 stage 当前工作树，避免未提交的其他 dirty slice 混入派生地图。
echo
echo "=== Step 2.6: feature map gate (staged snapshot, read-only) ==="
if gate_enabled feature_map; then
if [[ -f "$STAGED_BACKEND/scripts/build_feature_map.py" ]]; then
    if ! codegraph init "$STAGED_INDEX_DIR" > "$STAGED_INDEX_DIR/codegraph-sync.out" 2>&1; then
        tail -20 "$STAGED_INDEX_DIR/codegraph-sync.out"
        echo "ERROR: staged snapshot CodeGraph 初始化失败，无法验证 FEATURE_MAP。"
        gate_fail feature_map 2
    elif ! codegraph sync "$STAGED_INDEX_DIR" >> "$STAGED_INDEX_DIR/codegraph-sync.out" 2>&1; then
        tail -20 "$STAGED_INDEX_DIR/codegraph-sync.out"
        echo "ERROR: staged snapshot CodeGraph 构建失败，无法验证 FEATURE_MAP。"
        gate_fail feature_map 2
    elif PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/build_feature_map.py" --check --quiet; then
        echo "[feature-map] staged snapshot fresh; current worktree was not modified"
    else
        echo "ERROR: staged FEATURE_MAP.md 与 staged source snapshot 不一致。"
        echo "正解: 在隔离 staged snapshot 上重生、review 后显式 stage；不得提交当前 dirty-tree 派生物。"
        gate_fail feature_map 2
    fi
else
    echo "ERROR: staged snapshot 缺 build_feature_map.py；生成地图门不得静默跳过。"
    gate_fail feature_map 2
fi
else
    echo "[commit-tier] skip feature_map (tier=$COMMIT_TIER)"
fi

# 2.7 Moth gate — Moth 0.3.0 resolves profile repo_path relative to process cwd.
# Always enter the exported snapshot before invoking it; passing an absolute --repo
# from the parent checkout can otherwise make assertions/CodeGraph inspect dirty main.
echo
echo "=== Step 2.7: Moth gates (exact staged snapshot) ==="
if gate_enabled moth; then
if [[ -f "$STAGED_INDEX_DIR/.moth/profile.yaml" ]]; then
    if ! command -v moth >/dev/null 2>&1; then
        echo "ERROR: staged snapshot 声明 Moth profile，但 moth CLI 不可用。"
        gate_fail moth 2
    else
        if [[ -d "$(pwd)/.venv" && ! -e "$STAGED_INDEX_DIR/.venv" ]]; then
            ln -s "$(pwd)/.venv" "$STAGED_INDEX_DIR/.venv"
        fi
        # 两项必须**各自独立**跑, 不能用 elif 串起来: P1 之前 gate_fail 是 exit, assert 挂了
        # 就整个终止, 短路无害; 降成 warn-only 后 gate_fail 返回 0, elif 会让 coupling 在
        # assert 一挂时**永远不再执行** —— 耦合检查连同它的发现一起消失且无任何信号。
        # (2026-08-11 B3 实测发现的检测面回退; 由 test_moth_gate_runs_coupling_even_when_assert_fails 锁定)
        MOTH_OK=1
        if ! (cd "$STAGED_INDEX_DIR" && moth assert --repo .); then
            echo "ERROR: staged snapshot Moth assertions 未闭合。"
            MOTH_OK=0
        fi
        if ! (cd "$STAGED_INDEX_DIR" && moth coupling --repo .); then
            echo "ERROR: staged snapshot Moth coupling 未闭合。"
            MOTH_OK=0
        fi
        if [[ "$MOTH_OK" == "1" ]]; then
            echo "[moth] staged snapshot PASS"
        else
            gate_fail moth 2
        fi
    fi
else
    echo "[moth] no staged profile; not applicable to this fixture/repository"
fi
else
    echo "[commit-tier] skip moth (tier=$COMMIT_TIER)"
fi

# 2.9 Staged/worktree parity —— 必须排在**所有**读 worktree 的门之前。
#
# 为什么存在: ci_pytest 门刻意拿 live worktree 跑测试(见 Step 3.4 注释: 测试需要 repo 的
# pytest.ini 与 fixture)。这意味着**它对「工作树 ≠ 索引」结构性失明** —— 跑的是你手上的
# 版本, 提交的是索引里的版本。已经咬过两次(2026-08-11 `26b1d6901` 的 history_cli 声明落空、
# 同日 `92f98f6e5` 带着已在本地修好的红测试上线, CI 红)。两次都是同一个动作:
# `git add` 之后又编辑, 然后提交。
#
# 范围**只限 .py**, 这是刻意收窄的: feature_map / lineage / moth / doc_* 这些门都读
# STAGED_INDEX_DIR, 对漂移天然免疫(且有用例专门验证它们在漂移下仍judge索引版本);
# **只有 ci_pytest 会被骗**, 而它骗人的介质就是 pytest 真正导入并执行的 .py。
# 把门开到所有文件会误伤 `git add -p` 这类正当分批提交, 也会让上述用例无法构造场景。
#
# 不变量: 每个 staged 的 .py, 其**索引内容 == 工作树内容**。
echo
echo "=== Step 2.9: staged/worktree parity ==="
if gate_enabled staged_worktree_parity; then
    STAGED_FILES="$(git diff --cached --name-only -- '*.py')"
    DIRTY_FILES="$(git diff --name-only -- '*.py')"
    DRIFTED=""
    if [[ -n "$STAGED_FILES" && -n "$DIRTY_FILES" ]]; then
        DRIFTED="$(comm -12 <(printf '%s\n' "$STAGED_FILES" | sort -u) \
                            <(printf '%s\n' "$DIRTY_FILES" | sort -u))"
    fi
    if [[ -n "$DRIFTED" ]]; then
        echo "ERROR: 以下 .py 已 staged, 但工作树里还有未 staged 的改动 —— ci_pytest 跑的是工作树版本, 你测的和你要提交的不是同一份:"
        printf '  %s\n' $DRIFTED
        echo "  修法: \`git add <上列文件>\` 后重跑; 若那些改动**不该**进本次提交, 先 stash 它们。"
        gate_fail staged_worktree_parity 2
    else
        echo "[staged-worktree-parity] PASS (staged 内容 == 工作树内容)"
    fi
else
    echo "[commit-tier] skip staged_worktree_parity (tier=$COMMIT_TIER)"
fi

# 3. Rule compliance
echo
echo "=== Step 3: rule compliance ==="
if gate_enabled rule_compliance; then
if [[ ! -f "$STAGED_BACKEND/scripts/check_rule_compliance.py" ]]; then
    echo "ERROR: staged snapshot 缺 check_rule_compliance.py，拒绝用 worktree 版本代验。"
    gate_fail rule_compliance 3
elif ! PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_rule_compliance.py" 2>&1 | tail -5; then
    echo
    echo "ERROR: rule compliance 失败. 见上 error."
    gate_fail rule_compliance 3
fi
else
    echo "[commit-tier] skip rule_compliance (tier=$COMMIT_TIER)"
fi

# 3.35 Always-on: ci_pytest_surface classification drift.
# Binding (2026-07-21×3 + 2026-07-20×2 CI red): public CI always runs
# test_ci_pytest_surface_drift, but L1 docs commits skipped ci_pytest locally —
# so an unclassified test_*.py left on main kept every subsequent push red until
# someone fixed the yaml. Cheap always-on check closes that loop for all tiers.
echo
echo "=== Step 3.35: CI pytest surface classification (always-on) ==="
if [[ ! -f "backend/tests/scripts/test_ci_pytest_surface_drift.py" ]]; then
    echo "ERROR: missing backend/tests/scripts/test_ci_pytest_surface_drift.py"
    exit 3
fi
if ! (
    PYTHONPATH=backend "$PY" -m pytest \
        backend/tests/scripts/test_ci_pytest_surface_drift.py \
        -p no:cacheprovider --tb=line -q
); then
    echo
    echo "ERROR: ci_pytest_surface drift — tracked test_*.py 未登记到 blocking/nightly/optional。"
    echo "正解: 把新测试写入 backend/config/ci_pytest_surface.yaml 对应列表 (optional 须带 reason)。"
    echo "此门对 L1 也强制: public CI 不论 commit tier 都会跑 drift test。"
    exit 3
fi
echo "[ci-surface-drift] PASS (always-on; L1 included)"

# 3.4 Offline CI pytest surface (L2/L3 only; same blocking list as public CI).
# Owner: backend/config/ci_pytest_surface.yaml + backend/scripts/run_ci_pytest.py.
# Binding finding (2026-07-20): safe_commit previously ran zero pytest locally, so
# stale assertions only exploded on public CI. This gate closes that gap without
# changing accept/PIT/cutover semantics. L1 docs commits skip full blocking suite
# (surface classification still runs in 3.35 above).
# Gate redesign #1 (2026-07-21): L2/L3 + CI run `--tier blocking` only
# (nightly_paths stay async; do not expand to full 985 here).
echo
echo "=== Step 3.4: CI pytest surface (L2/L3 blocking) ==="
if gate_enabled ci_pytest; then
if [[ ! -f "$STAGED_BACKEND/scripts/run_ci_pytest.py" ]]; then
    echo "ERROR: staged snapshot 缺 run_ci_pytest.py；L2/L3 不得跳过 CI 同面 pytest。"
    gate_fail ci_pytest 3
elif [[ ! -f "$STAGED_BACKEND/config/ci_pytest_surface.yaml" ]]; then
    echo "ERROR: staged snapshot 缺 ci_pytest_surface.yaml；L2/L3 不得跳过 CI 同面 pytest。"
    gate_fail ci_pytest 3
# Run against the live worktree (tests need repo pytest.ini + fixtures). Paths
# come only from the staged SSOT yaml via the staged runner — never a hand list.
elif ! (
    CHUNKYMONKEY_REPO="$(pwd)" \
    CI_PYTEST_SURFACE="$STAGED_BACKEND/config/ci_pytest_surface.yaml" \
    PYTHONPATH=backend "$PY" "$STAGED_BACKEND/scripts/run_ci_pytest.py" \
        --tier blocking -p no:cacheprovider --tb=line -q
); then
    echo
    echo "ERROR: CI pytest blocking surface 红 — 本地 L2/L3 必须与 public CI 同面绿。"
    echo "正解: 修失败测试, 或把路径从 blocking_paths 移到 nightly_paths / ci_test_optional(带 reason)。"
    echo "勿 --no-verify 绕; 勿改 accept/PIT/cutover 门去洗绿。"
    gate_fail ci_pytest 3
else
    echo "[ci-pytest] PASS (blocking surface = .github/workflows/ci.yml)"
fi
else
    echo "[commit-tier] skip ci_pytest (tier=$COMMIT_TIER)"
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
if gate_enabled sandbox_isolation; then
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_sandbox_isolation.py" 2>&1 | tail -12; then
    echo "[sandbox-isolation] PASS"
else
    echo
    echo "ERROR: 沙盒隔离门 — 测试产物漏进主项目 (C1 backend引用sandbox / C3 探索runner漏主脚本)。"
    echo "正解: 探索弧产物全留 sandbox; promotion 是方法确认后单独步骤 (真edge confirmed_by_owner=1 才进主项目)。"
    echo "误报修 check_sandbox_isolation.py 本身, 不 --no-verify 绕。"
    gate_fail sandbox_isolation 5
fi
else
    echo "[commit-tier] skip sandbox_isolation (tier=$COMMIT_TIER)"
fi

# 3.9 SERVE 读层门 (数据模块顶层设计 §10 P1 gate 落地, 2026-06-22; 2026-07-08 系统性收口):
# D1 全量非成员消费者内联裸查(data_module_members.yaml 区分 builder vs 消费者, 替代原只扫
# dossier.py 的伪绿门；red→green 证据已固化在对应 tests) /
# D2 preflight接线 / D3 entity声明链齐全 / D4 L2-bypass关闭。
echo
echo "=== Step 3.9: SERVE read-layer doors ==="
if gate_enabled serve_read_layer; then
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_serve_read_layer.py"; then
    echo "[serve-read-layer] PASS"
else
    echo
    echo "ERROR: SERVE 读层门红 — 非成员消费者内联裸查, 或 entity 声明链断, 或实验runner漏进backend。"
    echo "正解: 消费者取数走 services.data_access.DataAccess; 加工builder登记进 data_module_members.yaml; 实验入 sandbox。"
    echo "误报修 check_serve_read_layer.py 本身, 不 --no-verify 绕。"
    gate_fail serve_read_layer 5
fi
else
    echo "[commit-tier] skip serve_read_layer (tier=$COMMIT_TIER)"
fi

# 3.95 交易日历强制使用门 (2026-06-22 P1 升硬门, 第零条规定与 universe 同档执法):
# 拦内联绕过交易日历真相源 (wall-clock 当最新/SQL CURRENT_DATE 上界锚); 合法日历天窗口加 evidence 注释。
echo
echo "=== Step 3.95: calendar-usage gate (交易日历强制使用) ==="
if gate_enabled calendar_usage; then
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_calendar_usage.py" --strict; then
    echo "[calendar-usage] PASS"
else
    echo
    echo "ERROR: 交易日历强制使用门红 — 内联 wall-clock(date.today/datetime.now)当最新交易日 或 SQL <=CURRENT_DATE 上界锚。"
    echo "正解: services.calendar.latest_closed_or_raise (交易日历真相源); 合法日历天窗口加 # rule-compliance: ok evidence=。"
    echo "误报修 check_calendar_usage.py 本身, 不 --no-verify 绕。"
    gate_fail calendar_usage 5
fi
else
    echo "[commit-tier] skip calendar_usage (tier=$COMMIT_TIER)"
fi

# 3.955 population contract gate.  This is intentionally static: it proves the
# staged registry/policy/binder contract and keeps live accepted-data readiness
# separate (continuity/doctor may remain BLOCKED without blocking a code commit).
echo
echo "=== Step 3.955: population-contract gate (staged snapshot) ==="
if gate_enabled population_contract; then
POPULATION_JSON="$STAGED_INDEX_DIR/.population-contract.json"
if ! PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_universe_filter.py" \
    --format json > "$POPULATION_JSON"; then
    cat "$POPULATION_JSON" 2>/dev/null || true
    echo "ERROR: staged population contract gate failed."
    gate_fail population_contract 5
fi
if ! "$PY" - "$POPULATION_JSON" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
if report.get("verdict") != "PASS":
    raise SystemExit("population contract verdict is not PASS")
if report.get("live_readiness") not in {"NOT_EVALUATED", "BLOCKED", "DEGRADED", "READY"}:
    raise SystemExit("population live_readiness is missing or invalid")
if not isinstance(report.get("formal_dataset_count"), int) or report["formal_dataset_count"] <= 0:
    raise SystemExit("population formal dataset inventory is empty")
print(
    "[population-contract] staged PASS; "
    f"live_readiness={report['live_readiness']} (not upgraded by code commit)"
)
PY
then
    gate_fail population_contract 5
fi
else
    echo "[commit-tier] skip population_contract (tier=$COMMIT_TIER)"
fi

# 3.96 血缘漂移门: 每次提交都用同一 staged snapshot + live read-only catalogs 重建对账。
# 普通 consumer 删除同样会改变 consume edge，不能靠结构文件 regex 猜触发面。
echo
echo "=== Step 3.96: lineage drift (staged snapshot, blocking) ==="
if gate_enabled lineage_drift; then
if [[ ! -f "$STAGED_BACKEND/scripts/check_lineage_drift.py" ]]; then
    echo "ERROR: staged snapshot 缺 check_lineage_drift.py。"
    gate_fail lineage_drift 5
elif PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_lineage_drift.py"; then
    echo "[lineage-drift] staged snapshot PASS"
else
    echo "ERROR: staged 血缘图与 staged source/config/live catalog 漂移。"
    echo "正解: 从 exact staged snapshot 重生 data/lineage/graph.json 后显式 stage。"
    gate_fail lineage_drift 5
fi
else
    echo "[commit-tier] skip lineage_drift (tier=$COMMIT_TIER)"
fi

# 3.97 死引用硬门 (2026-06-28 根因根治): 删模块/表/文件后引用方必须同步清。
# 根因: 之前每波清理删"供给侧"漏"需求侧", 验收够不到孤儿脚本/懒import/guarded垫片/config死路径
# → 残留静默累积。本门 import-services + dead-services-ref + config-dead-path 机械堵死。**硬闸**。
echo
echo "=== Step 3.97: dead-references gate (死引用根治硬门) ==="
if gate_enabled dead_references; then
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_dead_references.py" > /tmp/cm_deadref.out 2>&1; then
    grep -E "^\[dead-references\] PASS" /tmp/cm_deadref.out || echo "[dead-references] PASS"
else
    echo
    echo "ERROR: 死引用门红 — 删了模块/表/文件但引用方未清 (孤儿脚本/死import/config引死路径):"
    grep -E "✗|FAIL:" /tmp/cm_deadref.out | head -40
    echo "正解: 删引用方 / repoint 现存 / 引用方也是残留则一并删。误报修 check_dead_references.py 本身, 不 --no-verify 绕。"
    gate_fail dead_references 5
fi
else
    echo "[commit-tier] skip dead_references (tier=$COMMIT_TIER)"
fi

# Step 3.98: grain 唯一性门 (R1 根因1: grain 声明错误在良性期抓, 防批内去重升级成静默销毁;
#   --strict 默认关 → 跑批写锁期库不可达优雅跳过不阻塞 commit; 豁免带到期日, 过期自动恢复 FAIL)
echo
echo "=== Step 3.98: grain-uniqueness gate (grain 持续审计门) ==="
if gate_enabled grain_uniqueness; then
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_grain_uniqueness.py" \
     --exempt mart_sector_pulse_daily:20260710 > /tmp/cm_grain.out 2>&1; then
    tail -1 /tmp/cm_grain.out
else
    echo
    echo "ERROR: grain 门红 — 某表按声明 grain 有重复组 (grain 声明不足或 MERGE 幂等破坏):"
    grep -E "FAIL|dup" /tmp/cm_grain.out | head -20
    echo "正解: 核 grain 是否漏列 (report_rc/block_trade 反例) → 修 registry grain + 重拉自清。误报修脚本本身。"
    gate_fail grain_uniqueness 5
fi
else
    echo "[gate] skip grain_uniqueness — system_health 组已归位 daily_update 运行时自检 (或 tier=$COMMIT_TIER 剪枝)"
fi

# Step 3.99: continuity-integrity evidence (2026-07-06 全面数据审计根因根治 —
#   check_continuity_integrity.py 自 07-05 加入起就一直未接入 safe_commit/CI, 审计当场抓到
#   这正是"新增治理脚本未同批注册"模式在本 session 活生生重演; 非 strict 默认库不可达优雅
#   跳过。2026-07-17 分离 code fitness 与 mutable data readiness：provider/DB 的 live FAIL
#   必须原样曝光并继续阻断 daily pipeline/消费 readiness，但不能让修复该故障的代码也无法提交。
#   静态 verifier/测试本身仍由前述 exact-index gates + Rule 10 阻断。
echo
echo "=== Step 3.99: continuity-integrity evidence (live data readiness) ==="
# 本块自 2026-08-11 P1 起只在 **fail-closed 路径**可达 (continuity 属 system_health 组,
# gate_enabled 常规下直接返回 false)。刻意保留而非删除: 门策略坏掉时它必须还在, 而那正是
# 最需要「验证器自身可信」的时刻 —— 下面那段 JSON/计数/退出码自洽校验守的就是这个。
# 2026-08-11 独立审查 finding #5 记录了这块的可达性, 结论是设计如此, 非死代码。
if gate_enabled continuity; then
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
            else "observe" if isinstance(status, str) and status.startswith("observe_")
            else "db_unreachable" if status == "db_unreachable"
            else "skipped" if isinstance(status, str) and status.startswith("skipped")
            else None
        )
        if category is None:
            raise ValueError(f"check[{index}] invalid status={status!r}")
        actual[category] += 1

    # observe_* = frozen-domain honesty (parallel SLA FROZEN_STALE_OBSERVED); not FAIL/WARN.
    allowed_categories = {"pass", "warn", "fail", "observe", "skipped", "db_unreachable"}
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
    # observe does not drive overall (recorded honesty only).
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
    gate_fail continuity 5
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
        gate_fail continuity 5
        ;;
esac
else
    echo "[gate] skip continuity — system_health 组已归位 daily_update 运行时自检 (或 tier=$COMMIT_TIER 剪枝)"
fi

# Step 3.991/992: 2026-07-06 全面数据审计根因根治 — 治理脚本覆盖率盲区之二: 18 个
# check_*.py 里 5 个此前完全未接入任何机制(safe_commit/CI/git hooks 都没有)。逐个实测:
# check_config_refs/check_doc_drift 现在都能跑通且真 PASS, 检查内容仍有价值(层词汇/文档
# 死引用一致性) → 接成硬闸(与其余静态一致性门同级别)。
echo
echo "=== Step 3.991: config-refs gate (data_access/data_layers 层词汇一致性) ==="
if gate_enabled config_refs; then
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_config_refs.py" > /tmp/cm_configrefs.out 2>&1; then
    tail -2 /tmp/cm_configrefs.out
else
    echo
    echo "ERROR: config-refs 门红 — data_access entity 引用的 layer 词汇在 data_layers.yaml 里没有定义:"
    cat /tmp/cm_configrefs.out
    echo "正解: 补 data_layers.yaml 声明 / 改 data_access.yaml 用现存层名。误报修脚本本身, 不 --no-verify 绕。"
    gate_fail config_refs 5
fi
else
    echo "[commit-tier] skip config_refs (tier=$COMMIT_TIER)"
fi

echo
echo "=== Step 3.992: doc-drift gate (live docs + generated map + source/config owners) ==="
if gate_enabled doc_drift; then
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_doc_drift.py" > /tmp/cm_docdrift.out 2>&1; then
    tail -2 /tmp/cm_docdrift.out
else
    echo
    echo "ERROR: doc-drift 门红 — 活文档(活索引/AGENTS/docs)引用了已删文件路径:"
    cat /tmp/cm_docdrift.out
    echo "正解: 改文档指向现存路径 / 标 deprecated 头注豁免。误报修脚本本身, 不 --no-verify 绕。"
    gate_fail doc_drift 5
fi
else
    echo "[commit-tier] skip doc_drift (tier=$COMMIT_TIER)"
fi

# Step 3.993: 活文档治理硬闸。WARN 也是未闭合状态，不能随 commit 传播。
echo
echo "=== Step 3.993: doc-governance (fails=0, warns=0) ==="
if gate_enabled doc_governance; then
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_doc_governance.py" > /tmp/cm_docgov.out 2>&1; then
    tail -3 /tmp/cm_docgov.out
else
    cat /tmp/cm_docgov.out
    echo
    echo "ERROR: doc-governance 门红 — FAIL/WARN 均未闭合。"
    echo "正解: 修 owner、退役 CLI、幽灵链接或文档生命周期；不放宽 gate。"
    gate_fail doc_governance 5
fi
else
    echo "[commit-tier] skip doc_governance (tier=$COMMIT_TIER)"
fi

# Step 3.9935: emoji 硬门 (owner 全项目偏好; 2026-08-11 登记进门表 —— 此前只在 git hook 里,
# safe_commit 路径不查, 两条执法路径给出不同结果)。
echo
echo "=== Step 3.9935: no-emoji gate ==="
if gate_enabled no_emoji; then
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_no_emoji.py" > /tmp/cm_emoji.out 2>&1; then
    tail -1 /tmp/cm_emoji.out
else
    cat /tmp/cm_emoji.out
    echo "ERROR: staged 内容含 emoji (owner 全项目禁用)。"
    gate_fail no_emoji 5
fi
else
    echo "[commit-tier] skip no_emoji (tier=$COMMIT_TIER)"
fi

# Step 3.994: 人工文档写死运行时状态的对账门 (goal.md P2.1)。
# 唯一查**语义时效**的门 —— doc_drift/doc_governance 只查悬空链接与代码路径, 于是
# 「两份手写文档互相矛盾且同时落后两周」能在它们全绿时发生 (2026-08-10 实证)。
echo
echo "=== Step 3.994: doc-runtime-state (文档写死运行时状态对账) ==="
if gate_enabled doc_runtime_state; then
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_doc_runtime_state.py" > /tmp/cm_docstate.out 2>&1; then
    head -2 /tmp/cm_docstate.out
else
    cat /tmp/cm_docstate.out
    echo
    echo "ERROR: 活文档里有未声明的运行时状态 (写死的前沿/计数, 或已失效的豁免)。"
    echo "正解: 改成指向 \`scripts/chunkyctl status\`; 确属常量则在 backend/config/doc_runtime_state.yaml 写明理由。"
    gate_fail doc_runtime_state 5
fi
else
    echo "[commit-tier] skip doc_runtime_state (tier=$COMMIT_TIER)"
fi

# 4. Commit message keyword check (manual preview)
echo
echo "=== Step 4: commit message 结构自检 (Q/Fix/Evidence/Residual) ==="
if gate_enabled commit_msg; then
# 与 commit-msg hook 共用同一实现, 不在 shell 里维护第二份关键词表 ——
# 旧版这里有一张 `sharpe|calmar|max_dd` 词表, 与 hook 各写一份且都会烂。
MSG_FILE="$STAGED_INDEX_DIR/COMMIT_MSG_STRUCT"
printf '%s\n' "$MSG" > "$MSG_FILE"
PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_commit_message.py" "$MSG_FILE" || true
else
    echo "[commit-tier] skip commit_msg (tier=$COMMIT_TIER)"
fi

# 4.5. Codex review gate (Rule 10 blocking; one policy owner shared with commit-msg hook)
echo
echo "=== Step 4.5: Codex review gate (Rule 10) ==="
if gate_enabled rule10; then
REVIEW_MSG_FILE="$STAGED_INDEX_DIR/COMMIT_EDITMSG"
printf '%s\n' "$MSG" > "$REVIEW_MSG_FILE"
if PYTHONPATH="$STAGED_BACKEND" "$PY" "$STAGED_BACKEND/scripts/check_codex_review.py" "$REVIEW_MSG_FILE"; then
    echo "Rule 10 PASS: canonical staged check_codex_review gate"
else
    echo "ERROR: Rule 10 blocking review gate failed."
    gate_fail rule10 6
fi
else
    echo "[commit-tier] skip rule10 (tier=$COMMIT_TIER)"
fi

# 4.9 脚手架汇总 — warn-only 不等于「可以永远忽略」。一次性列全 + 指到批量修入口,
# 否则 warn 会被逐条无视, 分组就成了放水而不是重新分布。
echo
echo "=== Step 4.9: scaffold 汇总 (warn-only) ==="
if [[ -n "${SCAFFOLD_WARNED// /}" ]]; then
    echo "本次未闭合的脚手架门:${SCAFFOLD_WARNED}"
    echo "  不阻断提交 — 受害者是下一个接手的人, 不是数据, 也不是本次 diff。"
    echo "  批量收口: scripts/chunkyctl scaffold-fix   (能重生的重生, 只能报的报清单)"
else
    echo "[scaffold] 全部闭合"
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

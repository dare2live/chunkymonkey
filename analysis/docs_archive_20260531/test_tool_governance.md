# Test Tool Governance

Date: 2026-05-27
Status: active / controller loop 10 closed with full selected-artifact registry coverage and root micro-batch 13 realdb isolation

## Conclusion

需要建立测试工具治理，但先做轻量治理面，不先做重系统。

| 问题 | 决策 | 原因 |
|---|---|---|
| 是否需要测试工具模块 | YES, 但从 registry + audit script 起步 | 当前测试数量大、架构变化快，过期测试会制造假安全感 |
| 是否需要实时调整工具状态 | YES, 但状态由审计结果驱动，不让脚本静默改事实 | 防止测试工具自己变成另一个不受控真相源 |
| 是否需要测试工具审计工具 | YES | 架构重构完成前，必须知道哪些测试仍在证明旧 universe/PIT/DB 假设 |
| 是否现在删除旧测试 | NO blanket delete; YES after evidence | 删除前必须有替代覆盖、无引用证据或明确隔离理由；一旦证明确实可删，必须直接删除，不用注释、隐藏 marker、改名或空壳假删除 |

## Current State

| Audit scope | Verdict | Meaning |
|---|---:|---|
| `--scope test_tool_health_audit` | PASS: 0 FAIL / 0 WARN / 4 selected / 1 selected test artifact registered | 审计器自身可作为本轮机制变更证据 |
| `--scope __definitely_missing_scope_for_audit__` | FAIL, exit 1 | 显式 scope 选中 0 文件会阻断，避免拼错 scope 后误报 PASS |
| full default audit | PASS: 0 FAIL / 0 WARN / 365 selected / 365 registered / 0 unregistered / 100% registry coverage | 全量 selected test artifact 已有 owner/status/evidence；这只证明测试工具治理覆盖，不证明业务数据、策略收益或真实库 route smoke 已执行 |
| high-risk registry batch 1 | PASS: 0 FAIL / 0 WARN / 64 selected / 64 registered | `backtest` / `paper_sim` / `optimization` / `pipeline` / `data_governance` 已有 owner/status/evidence |
| registry batch 2 | PASS: 0 FAIL / 0 WARN / 31 selected / 31 registered; collect-only 185 tests | `buy_signal` / `candle_pattern` / `features` / `integration` / `labels` / `ml_ranking` / `portfolio` 已有 scoped owner/status/evidence |
| registry batch 3 | PASS: 0 FAIL / 0 WARN / 34 selected / 34 registered; collect-only 209 tests | `sentiment` / `services/market_perception` / `trading_config` / split `strategies` / `services/paper_sim` / `fixtures` / `portfolio_walk_forward` / `services/notification` 已有 scoped owner/status/evidence |
| root registry batch 4 | PASS: 0 FAIL / 0 WARN / 29 selected / 29 registered; collect-only 910 tests | audit/gate/root-cause + backtest/paper/strategy-result root files 已有 scoped owner/status/evidence |
| root registry batch 5 | PASS: 0 FAIL / 0 WARN / 39 selected / 39 registered; collect-only 241 tests | data-source/ingestion/freshness/lineage/profile/model/formula root files 已有 scoped owner/status/evidence |
| blended recommendation scoped audit | PASS: 0 FAIL / 0 WARN / 2 selected / 2 registered; pytest 5 passed | 已拆成 duck_mem selection contract 与 isolated mocked endpoint contract，不再依赖生产 DB 行数 |
| root micro-batch 6 | PASS: 0 FAIL / 0 WARN / 4 selected / 4 registered; pytest 10 passed / 1 warning | 事件、删除治理、常量、daily-update shell root files 已有 scoped owner/status/evidence |
| root micro-batch 7 | PASS: 0 FAIL / 0 WARN / 11 selected / 11 registered; pytest 45 passed / 4 warnings | static data、data health、DB、dependency、drift、ETF root files 已有 scoped owner/status/evidence |
| root micro-batch 8 | PASS: 0 FAIL / 0 WARN / 11 selected / 11 registered; pytest 51 passed / 1 warning | holding top-k、event engine、external attention、feature join/fillna、holdings、artifact import、industry PIT root files 已有 scoped owner/status/evidence；`test_industry_pit.py` 已用 `cleanup_scan_root=tmp_path` 隔离全局 cleanup policy，保持 production 默认扫描真实 workspace |
| root micro-batch 9 | PASS: 0 FAIL / 0 WARN / 7 selected / 7 registered; pytest 29 passed / 2 warnings | institution profile/read-model、API contract、L2 metrics、scoring engine/read、survey sync root files 已有 scoped owner/status/evidence；`test_institution_contract.py` 已移除默认真实 DB skip，`test_institution_survey_client.py` 已固定 calendar `as_of_date`，避免默认测试隐式读真实 calendar |
| root micro-batch 10 | PASS: 0 FAIL / 0 WARN / 12 selected / 12 registered; pytest 76 passed / 1 deselected; perf opt-in 1 passed / 3 deselected | LambdaMART v6 fast-path/perf、model artifact lifecycle/wrapper、neutralize、paper-engine core/storage/benchmark root files 已有 scoped owner/status/evidence；`test_lambdamart_v6_perf.py` 仅 timing 用例标 `perf`，默认快测保留 PIT fast-path 合同且排除 timing |
| root micro-batch 11 | PASS: 0 FAIL / 0 WARN / 20 selected / 20 registered; pytest 137 passed / 6 warnings | API route visibility、Phase0 daily controller、stock-picture component、pipeline/preflight/pricing、primitive seeds、QFII、recommendation-output GC、Phase5 decision、return-engine pricing、scoring composite root files 已有 scoped owner/status/evidence；`test_market_routes.py` 已改成内存连接 + audit snapshot monkeypatch，默认快测不再隐式读真实 DB |
| root micro-batch 12a | PASS: 0 FAIL / 0 WARN / 11 selected / 11 registered; pytest 51 passed / 29 warnings | source policy/watermark、updater step budget、screening API/read、selection lifecycle、scoring grade helper root files 已有 scoped owner/status/evidence；warning 主要为 `source_watermarks.py` 的 `datetime.utcnow()` 和 FastAPI `on_event` 历史债 |
| root micro-batch 12b/12c | PASS: 0 FAIL / 0 WARN / 14 selected / 14 registered; pytest 114 passed / 3 warnings | research three-pieces、LambdaMART v6 retrain、daily top-k、feature ablation、feature-group ablation、sector momentum、signals route/v2、stock horizon/scoring/trends/watchlist/turtle、storage retention root files 已有 scoped owner/status/evidence；`test_run_feature_ablation.py` 已通过 `with_alpha158=False` 隔离本地 `data/alpha158.duckdb` 隐式真实库 attach |
| root micro-batch 13a | PASS: 0 FAIL / 0 WARN / 9 selected / 9 registered; pytest 89 passed / 2 warnings | updater completeness/connectivity/sync/execution/institution/launcher/plan/status/N+1 root tests 已有 scoped owner/status/evidence；只证明管家 route/helper/fixture mechanics，不证明生产 freshness、真实 DAG 完成、外部源可用或性能证据 |
| root micro-batch 13b | PASS: 0 FAIL / 0 WARN / 14 selected / 14 registered; default pytest 76 passed / 63 deselected / 15 warnings; realdb collect-only 63/66 collected | system/strategy/ta_lib/tdx/trade_plan/train_multidim/v3/utils/conftest/xdxr root artifacts 已登记；`system_routes` 和 `v3_*` route smoke 标为 `realdb` opt-in，默认快测不再执行真实库 route；collect-only 不是业务/数据 evidence |

## First Principles

| Fundamental truth | Implication |
|---|---|
| 测试不是目标，可信证据才是目标 | 不能因为测试绿就宣称架构安全；测试也要被审计 |
| 当前系统服务真金白银候选 | 过期测试比缺测试更危险，因为它会掩盖 universe/PIT/freshness 问题 |
| 架构正在从旧 pipeline 迁到 truth-source/gate 驱动 | 测试必须跟着 truth source 更新，否则会继续保护旧行为 |
| 工具状态本身也是治理数据 | 状态要有 owner、reason、replacement、last_verified，不靠对话记忆 |

## Occam Decision

先做三件事，不先引入数据库表或复杂 dashboard:

1. `backend/config/test_tool_registry.yaml`
   - 记录测试工具/测试组的 owner、scope、runner、status、replacement、last_verified、risk。
2. `backend/scripts/audit_test_tool_health.py`
   - 只读审计 registry 与当前文件系统/pytest marker/禁用模式是否一致。
3. `data/reports/test_tool_health_latest.json`
   - 作为审计输出；后续需要长期趋势时再物化到 DuckDB 治理表。

## Registry Shape

建议最小字段:

| 字段 | 含义 |
|---|---|
| `id` | 稳定测试工具或测试组 id |
| `paths` | 测试文件、shell test、fixture 或 helper 路径 |
| `owner_module` | 归属模块，例如 `universe`, `updater`, `paper_sim`, `data_audit` |
| `scope` | `unit`, `contract`, `integration`, `pipeline`, `fixture`, `realdb`, `perf`, `network`, `gcp` |
| `runner` | 默认 pytest、opt-in pytest、shell、manual、CI-only |
| `status` | `active`, `needs_refactor`, `quarantined`, `deprecated`, `delete_candidate` |
| `evidence_level` | `trusted_current`, `trusted_with_scope`, `legacy_guard`, `quarantined`, `invalid` |
| `truth_source` | 该测试应保护的真相源，例如 K 线、calendar、`universe_rules.yaml` |
| `risk_reason` | 为什么可能过期或为什么必须保留 |
| `replacement` | 替代测试/工具，未知则 `unknown` |
| `last_verified` | 最近一次人工/脚本确认日期 |

`fixture` scope 只允许登记共享测试夹具或 helper。它不是独立业务证据，必须通过调用它的
具体 test scope 才能支持某个 contract。

## Audit Checks

## Pre-Test Validity Gate

任何测试运行前，先判断“这个测试工具今天还适不适合证明当前代码”。测试绿只能说明
被测试的前提成立；如果前提已经过期，绿灯就是误导。

在 `audit_test_tool_health.py` 实现前，使用手工 preflight:

| Check | Required answer |
|---|---|
| target | 本次将运行的 exact command 和测试文件/测试组是什么 |
| scope | 是否属于默认快测，还是 `realdb/perf/network/gcp/slow` opt-in |
| truth source | 测试保护的真相源是否仍正确: K 线、calendar、`universe_rules.yaml`、source coverage、lineage |
| fixture | fixture 是否仍符合当前架构；`dim_active_a_stock` 不能证明 active universe |
| DB engine | 是否使用 DuckDB/`duck_mem()`；SQLite 只允许 Optuna storage 等有证据例外 |
| evidence status | 测试是否把 proxy/warn-only/stale/in-sample 当生产证据 |
| mocking | 是否 mock 掉了本次真正需要验证的 gate、truth source 或 DB 行为 |

允许的退役/敏感 token 测试例外必须显式写成 `audit-fixture`，只用于测试审计器能否抓到违规。
这类例外不能被复用为业务 fixture，也不能证明旧 truth-source 合法。

脚本实现后，默认命令为:

```bash
PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope <scope-or-path>
```

FAIL 时不继续跑业务测试，除非当前任务就是修复该测试工具本身。

当前脚本硬规则:

| Rule | Severity |
|---|---:|
| 显式 `--scope` 选中 0 个存在文件 | FAIL |
| 默认 pytest 路径里的 `needs_refactor/quarantined/deprecated/delete_candidate` 或 `legacy_guard/quarantined/invalid` 测试 | FAIL |
| opt-in marker (`realdb/perf/network/gcp/slow`) 与 registry scope/runner 不一致 | FAIL |
| 全量 selected test artifacts 未登记 owner | WARN (future drift; 当前 0 个) |
| 默认测试文件混入真实库/生产环境 endpoint smoke 且无 mock 或 opt-in marker | FAIL |
| registry policy 与 `pytest.ini` 默认 testpaths / excluded markers 不一致 | FAIL |

### Test Evidence Levels

| Level | 可用于 | 不可用于 |
|---|---|---|
| `trusted_current` | 架构/业务验收证据 | 无 |
| `trusted_with_scope` | 当前明确 scope 内证据 | 推广成全局通过 |
| `legacy_guard` | 防旧行为回归 | 证明新架构正确 |
| `quarantined` | 人工参考 | 默认 gate / commit verdict |
| `invalid` | 只能作为清理对象 | 任何通过声明 |

P0 检查:

| Check | FAIL 条件 |
|---|---|
| registry coverage | registry 路径不存在，或关键测试组没有 registry |
| marker correctness | `realdb/perf/network/gcp/slow` 测试没有显式 marker 或 runner |
| registered tool state | 默认 pytest gate 中出现 non-current status/evidence |
| hidden realdb smoke | 默认测试文件依赖 FastAPI app/真实 DB 状态、生产行数或外部环境，却没有 mock 或 `realdb` opt-in |
| default runner drift | 默认 pytest 跑到了真实库/网络/长跑测试，或关键 contract 从默认集消失 |
| legacy DB drift | 新测试绕过 `duck_mem()` 使用旧内存 DB 替身，除 Optuna SQLite storage fixture 外 |
| universe fixture drift | `dim_active_a_stock` fixture 被用来证明 active universe，而非 code->name/cache/schema |
| PIT/freshness drift | 测试使用 future data、固定历史 end date、warn-only/proxy 当生产证据 |

P1 检查:

| Check | WARN 条件 |
|---|---|
| root test drift | root `tests/` 或 shell tests 不在默认 runner 且无 owner |
| selected registry coverage | selected test artifacts 未登记 owner/status/evidence；当前全量为 0 个 |
| stale name drift | 文件名/注释仍指向已退役 pipeline、旧表、旧前端 surface |
| over-mocking | 测试 mock 掉核心 truth-source/gate，无法证明真实 contract |
| duplicate coverage | 多个测试保护同一旧行为，新架构 contract 反而无测试 |

## Status Transitions

| From | To | Required evidence |
|---|---|---|
| `active` | `needs_refactor` | 审计发现旧 truth-source、旧 fixture、runner/marker 不匹配 |
| `needs_refactor` | `active` | 已补 contract 或 fixture，targeted tests pass |
| `needs_refactor` | `quarantined` | 暂时有历史价值但不适合默认 gate |
| `quarantined` | `deprecated` | 已有替代覆盖，保留仅为历史说明 |
| `deprecated` | `delete_candidate` | `rg` 无引用，替代测试 pass，文档不再依赖 |
| `delete_candidate` | deleted | CodeGraph query/context + `rg` 无 active consumer + replacement/lineage 证据 + targeted tests/audits + controller review + `git diff --check` |

如果 non-current 状态的测试仍在 `pytest.ini` 默认 testpaths 内，审计器必须 FAIL；要么修成
`active/trusted_*`，要么移出默认 gate/改为显式 opt-in runner。

## Integration Points

| 位置 | 行为 |
|---|---|
| `pytest.ini` | 默认保持快而可信，`realdb/perf/network/gcp` opt-in |
| `scripts/safe_commit.sh` | Rule 10 已 blocking staged `.py` review trailer；`safe_commit_rule10_tests` 用临时 git repo 覆盖空 skip reason、REQUEST_CHANGES、APPROVE_WITH_NOTES 和有效 skip reason |
| `goal.md` / handoff | 只记录当前 FAIL/WARN 和下一步，不堆完整 registry |
| `docs/implementation_plan.md` | 记录阶段、优先级、验收标准 |
| CodeGraph + complexity | 审计器本身如果改 `.py`，必须成对执行 |

## Controller Feedback Loop

测试工具治理必须闭环，不允许审计报告停在 JSON 里:

| Step | Owner | Output |
|---:|---|---|
| 1 | `audit_test_tool_health.py` | 输出 `verdict`、`findings`、`controller_feedback` |
| 2 | controller | 评审每条 FAIL/WARN 是真问题、已知例外、误报还是需要机制调整 |
| 3 | registry | 真问题写入 `status` / `evidence_level` / `risk_reason` / `replacement` |
| 4 | mechanism | 误报只通过更精确规则修正，不靠静默 ignore |
| 5 | docs/handoff | 当前 FAIL/WARN 数字和下一批任务进入 `goal.md` / handoff |
| 6 | tests | 只有 pre-test audit 通过或 WARN 被明确 scope 后，测试结果才能作为证据 |

### Root-Files Triage Order

`backend/tests/<root-files>` 是审计器故意保留的大桶，不允许直接登记成一个 registry
owner。controller 必须先按 filename、import owner、truth source 和证据语义拆组:

| 顺序 | 优先级 | root-file 组 | 拆分原则 |
|---:|---|---|---|
| 1 | P0/P1 | audit/gate/root-cause tests | 先登记 delivery/readiness/PIT/leakage/freshness/survivorship 等门禁，FAIL/WARN 语义不能混成普通 unit |
| 2 | P1 | backtest / paper_sim / strategy-result root tests | 区分 toy fixture、paper_sim contract、Phase4/PBO/DSR evidence；不得把 in-sample 或 proxy 测试当 production claim |
| 3 | P1 | data-source / ingestion / freshness root tests | 按 tdxhub/akshare/aif10/miaoxiang/source coverage owner 拆；资金流、K 线、calendar、lineage 分别登记 |
| 4 | P1/P2 | candidate / recommendation / feature root tests | 按 candidate builder、recommendation selector、feature panel、model input contract 拆；PIT/freshness 状态必须写进 risk |
| 5 | P1 | institution profile / scoring / survey root tests | 默认测试不得隐式读真实 DB/calendar；fixture 指标只能证明 read-model/scoring/sync mechanics，不证明机构画像或跟随 alpha |
| 6 | P1 | model lifecycle / paper_engine / lambdamart perf root tests | `perf` 必须 opt-in；toy paper-engine/math fixtures 不能当 paper_sim KPI、forward Sharpe 或 production promotion 证据 |
| 7 | P2 | API / constants / compatibility / misc root tests | 只在前六组清完后处理，避免把低风险兼容测试抢到门禁前面 |

每组完成后先跑 scoped audit，再跑 full audit。full audit WARN 只能在 controller 明确说明
“剩余 WARN 属于未处理 root-file registry backlog”后，才允许对应 scoped tests 作为局部证据。

2026-05-27 首轮闭环:

| Audit conclusion | Controller verdict | Feedback applied |
|---|---|---|
| 自审发现 `test_audit_test_tool_health.py` 含 `dim_active_a_stock` | 真阳性但属于审计器自测 fixture | 新增 `audit-fixture` 规则和 registry 说明 |
| 全量发现 16 个 `dim_active_a_stock` fixture WARN + 1 个 root `tests/` WARN | 混合结论：多数是合法 cache/name/data-sync/scoped universe contract，少量是 stale test/comment，1 个整文件测试工具实际失败 | registry 拆成 universe/name-cache/data-sync/feature-panel/root entries；`test_v3_picture.py` stale 注释移除；root `.sh` 纳入审计 |
| `test_build_feature_panel_duck.py` 仍断言旧 `dim_active_a_stock` default path | 测试过期，不是代码应回退 | 测试改为验证当前 default A-share prefix filter，PIT 模式仍验证 `dim_all_ever_listed` date-aware contract |
| `backend/tests/test_global_data_quality.py` 旧状态 23 passed / 13 failed | 真测试工具债；根因是 cleanup policy 默认扫真实 `WORKSPACE_ROOT`，把历史 archive/backup artifact 混入局部 DQ 用例 | `record_global_data_quality_gate()` 新增显式 `cleanup_scan_root`，生产默认仍扫真实 workspace；测试传 `tmp_path`，global DQ 升级为 `global_data_quality_contract_tests` active |
| 全量审计一度收敛为 0 FAIL / 0 WARN / 360 selected / 12 registry tools | controller 复核判定该 PASS 太窄：只覆盖少数 registry 条目，不能代表 360 个 selected artifacts 都有 owner | 机制已硬化为 full WARN: 312 selected artifacts 未登记；显式空 scope、默认 gate 中 non-current 测试、marker/scope drift 均 FAIL |
| 第一批 high-risk registry backfill | 64 个 selected artifacts 已登记，scoped audit PASS | 新增 `backtest_contract_tests`、`paper_sim_contract_tests`、`optimization_contract_tests`、`pipeline_contract_tests`、`data_governance_contract_tests`；当前全量数字见 Current State |
| 第二批 registry backfill | 31 个 selected artifacts 已登记，scoped audit PASS，collect-only 185 tests | 新增 `buy_signal_contract_tests`、`candle_pattern_contract_tests`、`feature_contract_tests`、`integration_contract_tests`、`label_contract_tests`、`ml_ranking_contract_tests`、`portfolio_contract_tests`；full audit 降为 217 unregistered / 39.72% coverage |
| 第三批 registry backfill | 34 个 selected artifacts 已登记，scoped audit PASS，collect-only 209 tests | 新增 `sentiment_contract_tests`、`market_perception_service_contract_tests`、`trading_config_contract_tests`、拆分后的 `strategy_*` entries、`paper_sim_service_contract_tests`、`test_fixture_helpers`、`portfolio_walk_forward_contract_tests`、`notification_contract_tests`；full audit 降为 183 unregistered / 49.17% coverage |
| agent 复核第三批 | `strategies` 不应目录级 bulk-register；`fixture` scope 需文档承认 | controller 已拆 `strategies` 为 regime/ensemble/institution-follow/sniper 四个 owner；`fixture` scope 只作证据支撑 |
| root registry batch 4 | 29 个 selected artifacts 已登记，scoped audit PASS，collect-only 910 tests | 采纳 Hubble/Dalton 结论：audit/gate/root-cause 与 backtest/paper/strategy-result root files 拆 owner，不把 proxy/warn-only/in-sample 当生产证据；full audit 降为 154 unregistered / 57.22% coverage |
| root registry batch 5 | 39 个 selected artifacts 已登记，scoped audit PASS，collect-only 241 tests | 采纳 Sartre 的安全子集：data-source/ingestion/freshness/lineage/profile/model/formula root files 登记为 `trusted_with_scope`；`tdx_source` 保留 P1 拆分债 |
| blended recommendation P0 detected | `test_blended_recommendation.py` 同文件混合 duck_mem contract 与真实 DB endpoint smoke | 当时先登记为临时 blocker entry，让全量 audit FAIL 而不是隐藏在 WARN；随后已拆分清除 |
| blended recommendation P0 cleared | 旧 endpoint smoke 已从 `test_blended_recommendation.py` 拆出，新增 `test_blended_recommendation_endpoint.py` 使用 isolated router app + monkeypatched DuckDB connection | registry 改为 `blended_recommendation_contract_tests` 与 `blended_recommendation_endpoint_contract_tests`，scoped audit PASS，pytest 5 passed；full audit 从 FAIL 回到 WARN |
| candidate/data-deprecation owner split | `test_candidate_feature_pipeline.py` 同文件混合 candidate feature/PIT/Optuna mechanics 与 data-asset deprecation metadata writer | 已拆出 `test_data_deprecation.py`，registry 新增 `candidate_feature_pipeline_root_tests` 与 `data_deprecation_registry_root_tests`；scoped audit PASS，pytest 2 passed / 9 deprecation warnings；full audit 降为 113 unregistered |
| root micro-batch 6 | 4 个 root selected artifacts 已按独立 owner 登记，scoped audit PASS，pytest 10 passed / 1 warning | 新增 `executive_trade_events_contract_tests`、`candidate_feature_set_gc_contract_tests`、`etf_constants_contract_tests`、`daily_update_model_refresh_shell_tests`；candidate GC 明确不能替代真实删除前的 CodeGraph/rg/lineage 审查；full audit 降为 109 unregistered |
| root micro-batch 7 | 11 个 root selected artifacts 已按 7 个 owner 登记，scoped audit PASS，pytest 45 passed / 4 warnings | 新增 static data contract、data health snapshot、DB core、DB health/financial、dependency guards、ML drift、ETF local strategy/DB entries；这些测试只证明本地 contract，不证明生产 DB/freshness/ETF strategy/model promotion |
| root micro-batch 8 | 11 个 root selected artifacts 已按 11 个 owner 登记，scoped audit PASS，pytest 51 passed / 1 warning | 新增 holding top-k eval、institution event notice lineage、event simulator、external attention、smart sync plan、feature join v5 leakage guard、fillna policy、holdings read model、model train-log import、Phase5 remote prediction import、industry PIT entries；`test_industry_pit.py` 旧用例被 workspace cleanup blocker 污染，已改为传 `cleanup_scan_root=tmp_path`，不改变生产默认 cleanup 扫描 |
| root micro-batch 9 | 7 个 institution root selected artifacts 已按 6 个 owner 登记，scoped audit PASS，pytest 29 passed / 2 warnings | 采纳 Ohm 只读审计：`test_institution_contract.py` 默认真实 DB skip 已改成 monkeypatched route contract；`test_institution_survey_client.py` 固定 `_latest_closed()` 避免隐式真实 calendar；full audit 降为 80 unregistered / 77.90% coverage |
| root micro-batch 10 | 12 个 model/paper_engine/lambdamart root selected artifacts 已按 7 个 owner 登记，scoped audit PASS，默认 pytest 76 passed / 1 deselected，perf opt-in 1 passed / 3 deselected | 采纳 Boyle 只读审计：`test_lambdamart_v6_perf.py` timing 用例已标 `perf`，registry 同时登记 fast-path contract 与 perf opt-in；model artifact deletion 明确不能授权生产删除；paper_engine toy fixtures 明确不能当 production KPI |
| root micro-batch 11 | 20 个 API/picture/pipeline/pricing/QFII/GC/Phase5/return/scoring root selected artifacts 已按 11 个 owner 登记，scoped audit PASS，pytest 137 passed / 6 warnings | `test_market_routes.py` 从真实 DB route smoke 改成内存连接 + audit snapshot monkeypatch；pipeline/recommendation GC 测试只证明 in-memory 删除 mechanics，不授权生产删除；picture/scoring hardcoded fixture 只作 component contract，不作生产画像/alpha 证据；full audit 降为 48 unregistered / 86.74% coverage |
| root micro-batch 12a | 11 个 source/screening/selection/scoring root selected artifacts 已按 5 个 owner 登记，scoped audit PASS，pytest 51 passed / 29 warnings | 这批只证明 source policy/watermark、step budget、screening read/API、selection lifecycle 和 scoring grade helper 的本地 contract；`datetime.utcnow()` warnings 作为后续小债，不阻断 registry owner 收敛；full audit 降为 37 unregistered / 89.78% coverage |
| root micro-batch 12b/12c | 14 个 research/retrain/topk/ablation/sector/signals/stock/storage root selected artifacts 已按 14 个 owner 登记，scoped audit PASS，pytest 114 passed / 3 warnings | 采纳 Wegener/Kepler 只读审计：`test_run_feature_ablation.py` 默认测试存在隐式 attach 3.75GB `data/alpha158.duckdb` 风险，已给 loader 增加 `with_alpha158` 参数并在测试中关闭；feature ablation/CYQ/stock/signals/storage 测试只证明本地 contract，不证明生产 alpha/画像/删除授权；当时 full audit 降为 23 unregistered / 93.65% coverage，后续 13a/13b 已清零 |
| root micro-batch 13a | 9 个 updater root selected artifacts 已按 owner 登记，scoped audit PASS，pytest 89 passed / 2 warnings | 采纳 Turing 只读审计：updater 测试可作为默认 fast，但只证明管家 route/helper/fixture mechanics；`test_updater_n_plus_one_fix.py` 的复制 SQL/over-mocking 风险写入 registry，不能当生产性能证据；full audit 进入最后 14 个 root artifact |
| root micro-batch 13b + Rule 10 | 14 个剩余 root artifacts 已按 owner/realdb scope 登记，scoped audit PASS，默认 pytest 76 passed / 63 deselected / 15 warnings，realdb collect-only 63/66 collected；Rule 10 行为测试已登记 | 采纳 Linnaeus 只读审计：`test_system_routes.py` 移除 module import 阶段生产 `main` 加载；`system_routes` 与 `v3_*` route smoke 标 `realdb` opt-in；`test_v3_meta.py` 一处漏标已补；当前 full audit 达到 0 FAIL / 0 WARN / 365 registered / 100% coverage |
| controller feedback 太粗 | 旧报告只给单条 registry backfill 建议，不能直接派 agents | `audit_test_tool_health.py` 已新增 `unregistered_selected_slices` 与 `controller_feedback.next_task_slices`；当前 selected-artifact backlog 为 0，后续用于发现新增/漂移测试 |

## Implementation Order

| 顺序 | 优先级 | 动作 | 验收 |
|---:|---|---|---|
| 1 | P0 | 生成只读 inventory: 统计默认 pytest、opt-in marker、root/shell 游离测试、legacy fixture 候选 | 形成 registry 初稿，不删文件 |
| 2 | P0 | 新增 `test_tool_registry.yaml`，先覆盖 gate/contract/updater/universe/paper_sim 关键测试组 | YAML 可人工审计，字段完整 |
| 3 | P0/P1 | 新增 `audit_test_tool_health.py`，只读输出 JSON/Markdown | 能检出缺 marker、旧 DB 替身、`dim_active_a_stock` fixture 风险 |
| 4 | P1 | 第一批 refactor/promote/quarantine 候选处理 | 已完成首轮分类: 17 WARN 收敛为 0 WARN；global DQ 36 passed；DQ+recommendation universe 39 passed；active scoped pytest 146 passed / 13 warnings；root shell 8 passed |
| 5 | P1 | registry backfill: 先覆盖 backtest/paper_sim/optimization/pipeline/data_governance 等关键测试组 | 第一至第五批 + root micro-batch 6/7/8/9/10/11/12a/12b/12c/13a/13b 已完成；full audit 从 312 unregistered 收敛到 0，当前 0 FAIL / 0 WARN / 100% coverage；每批 owner/status/evidence 明确 |
| 6 | P1 | controller 反馈机制: 未登记 selected artifacts 自动按目录切片 | `controller_feedback.next_task_slices` 已把目录级 backlog 收敛完；root-files 必须继续按 owner 拆分，不允许盲目 bulk-register |
| 6.1 | P0 | 拆 `test_blended_recommendation.py` | 已完成：分离 duck_mem selection contract 与 mocked endpoint contract；scoped audit PASS，pytest 5 passed，full audit 回到 WARN |
| 6.2 | P1 | 拆 `test_candidate_feature_pipeline.py` 混 owner | 已完成：candidate feature pipeline 与 data-deprecation registry 分文件分 owner；scoped audit PASS，pytest 2 passed / 9 warnings，full audit 为 0 FAIL / 1 WARN / 113 unregistered |
| 6.3 | P1 | root micro-batch 6 owner 登记 | 已完成：4 个 root 测试按事件、删除治理、常量、daily-update shell 分 owner；scoped audit PASS，pytest 10 passed / 1 warning，full audit 为 0 FAIL / 1 WARN / 109 unregistered |
| 6.4 | P1 | root micro-batch 7 owner 登记 | 已完成：11 个 root 测试按 static data、data health、DB、dependency、drift、ETF 分 owner；scoped audit PASS，pytest 45 passed / 4 warnings，full audit 为 0 FAIL / 1 WARN / 98 unregistered |
| 6.5 | P1 | root micro-batch 8 owner 登记 | 已完成：11 个 root 测试按 holding/event/external attention/feature/holdings/import/industry PIT 分 owner；scoped audit PASS，pytest 51 passed / 1 warning，full audit 为 0 FAIL / 1 WARN / 87 unregistered |
| 6.6 | P1 | root micro-batch 9 owner 登记 | 已完成：7 个 institution root 测试按 profile/read-model、API contract、L2 metrics、scoring、survey client 分 owner；scoped audit PASS，pytest 29 passed / 2 warnings，full audit 为 0 FAIL / 1 WARN / 80 unregistered |
| 6.7 | P1 | root micro-batch 10 owner 登记 | 已完成：12 个 model artifact / neutralize / paper_engine / lambdamart root 测试按 7 个 owner 登记；`test_lambdamart_v6_perf.py` timing 用例已标 `perf`；scoped audit PASS，默认 pytest 76 passed / 1 deselected，perf opt-in 1 passed / 3 deselected，full audit 为 0 FAIL / 1 WARN / 68 unregistered |
| 6.8 | P1 | root micro-batch 11 owner 登记 | 已完成：20 个 root 测试按 API、Phase0 daily、picture component、pipeline/preflight/pricing、primitive seeds、QFII、recommendation-output GC、Phase5 decision、return-engine pricing、scoring composite 分 owner；`test_market_routes.py` 已隔离真实 DB；scoped audit PASS，pytest 137 passed / 6 warnings，full audit 为 0 FAIL / 1 WARN / 48 unregistered |
| 6.9 | P1 | root micro-batch 12 owner 登记 | 已完成 12a/12b/12c：12a 的 11 个 source/screening/selection/scoring root 测试 scoped audit PASS，pytest 51 passed / 29 warnings；12b/12c 的 14 个 research/retrain/topk/ablation/sector/signals/stock/storage root 测试 scoped audit PASS，pytest 114 passed / 3 warnings；`test_run_feature_ablation.py` 已隔离 `alpha158.duckdb` 隐式真实库 |
| 6.10 | P1 | root micro-batch 13 owner 登记、realdb 隔离与 Rule 10 行为测试 | 已完成 13a/13b：updater 9 个 root tests scoped audit PASS，pytest 89 passed / 2 warnings；剩余 14 个 system/strategy/tdx/v3/utils/conftest/xdxr root artifacts scoped audit PASS，默认 pytest 76 passed / 63 deselected / 15 warnings，realdb collect-only 63/66 collected；Rule 10 行为测试已登记；当前 full audit 为 0 FAIL / 0 WARN / 365 selected / 365 registered / 100% coverage |
| 7 | P1/P2 | 稳定后接入 commit/session gate | 不影响快速开发，不误杀合法 opt-in 测试 |

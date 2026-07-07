# 全仓库死代码普查 (2026-07-07)

## 背景

用户在完成 `raw_aif10_peer_valuation` 退役收尾的 3 项顺带发现清理后提出疑虑: "我怎么感觉残留删不净了呢, 一直在删除残留"。根因诊断: 2026-06-28 "纯数据平台重建" (git rm ~245 策略/serving 文件) 是一次白名单裁剪式大手术, 只删了被明确判定该删的文件本体, 没有系统性验证"这些文件留下的引用/连接组织(connective tissue)"是否也一并清干净; `check_dead_references.py` 只抓**断引用**(import 一个不存在的模块 → 立刻报错), 抓不到**可达但从未被调用的孤儿文件**(import 链完整、语法正确, 但没有任何入口路径能走到它)。这是两类不同的死代码, 前者有自动化门, 后者没有。

用户拍板"一次性全仓库死代码普查"(而非继续零散被动发现), 用 **entry-point reachability 分析**方法: 先枚举全部真实入口点(main.py 5 个挂载路由 / daily_update.sh 调用链 / safe_commit.sh ~10 个 check_*.py / CI whitelist / moth claims.yaml / pytest 默认收集), 再反向追踪哪些代码从未被这些入口触达。

100-agent workflow 产出 13 簇候选, 随后用 codegraph(精确 AST 调用图) + moth(text-match fan-in, 对普通词文件名不可靠, 已用 codegraph 交叉验证发现假阳性) 逐簇验证, 用户批准后按簇执行、每簇跑全量测试 + moth assert + codegraph sync 收口。

## 方法论笔记 (留给后续同类普查参考)

- **codegraph 比 moth 更可信**: `moth coupling --impact industry.py` 报 104 文件命中, 实为文本子串误配(`v_sw_industry_pit`/`sw_industry` 等标识符含 "industry" 子串, 或注释里的中文"行业"), 逐函数核 `codegraph callers` 确认真实 0 调用方。moth 对常见词文件名(constants/industry 等)不可靠, 需 codegraph 复核。
- **pytest 默认收集 ≠ 活测试**: `pytest.mark.realdb` 标记的测试文件默认被排除(pytest.ini), 需 `-m realdb` 强制跑才能看真实 pass/fail, 用于甄别"看似是测试实则测已删模块"的死测试。
- **fixture 目录不能一刀切**: 15 个"空壳测试目录"候选中 `backend/tests/fixtures/` 实为 55 个真实 tracked 的 `domain_samples/*.json`(被 `test_market_pulse.py` 消费), 从删除批次中排除, 只删真正只剩 `__init__.py` 的 14 个目录。

## 已执行簇 (随本文档持续追加)

### 簇11 — 15 个空壳测试目录 → 实删 14 个 (2026-07-07)
`backend/tests/{backtest,buy_signal,candle_pattern,features,labels,ml_ranking,optimization,paper_sim,perf,portfolio,portfolio_walk_forward,routers,sentiment,trading_config}/` 仅剩 `__init__.py`(git-tracked 内容为空), 对应功能已随 2026-06-28 重建整体退役, 无任何测试收集。`fixtures/` 因含 55 个真实 fixture 数据被排除。

### 簇3 — 7 个死 service 文件 (2026-07-07)
`services/{api_cache,api_schemas,constants,pipeline_lock,pipeline_performance_policy,ui_labels,industry}.py` 逐一用 `codegraph callers` 核每个 public 函数, 确认全部 0 外部调用方(均为 2026-06-28 重建后失去所有消费方的孤儿工具模块)。连带删除 `config/pipeline_performance_policy.yaml`(`pipeline_performance_policy.py` 是其唯一 reader, 内容只引用已删的 builder 脚本)。

### 簇4 — 2 个死一次性脚本 (2026-07-07)
`scripts/{migrate_reference_db,cleanup_holder_dup}.py` 均为已完成的一次性历史修复脚本(§9 reference 库迁移 / 239 条 holder 重复行清理), `migrate_reference_db.py` 早已在多个 commit 里被标注 "DEPRECATED 禁止执行"。codegraph 确认 0 调用方。同步从 `config/duckdb_connect_policy.yaml` 的 `allowed_raw_connect_paths` 白名单移除对应条目(改为注释存档)。**未动** `check_strategy_validation_integrity.py`(报告曾候选, 复核确认仍被 grill 流程实际消费, 保留)。

### 簇8 — experiment_jobs.yaml 3 个死表条目 + 顺带发现 (2026-07-07)
`backtest_validation`/`model_training` job family 的 `artifact_contracts` 引用 3 张当前不存在的表(`mart_paper_sim_kpi`/`fact_model_train_log`/`mart_p0b_oos_predictions`, 策略/模型层待 edge 重建)。**未删除**这两个 job_family 整体结构(判断: 它们是未来重建的 schema 契约声明, `build_experiment_store.py` 自述"执行器待重写", 删掉等于烧掉设计文档), 只在 3 个死表行加注说明现状。

**顺带根治发现**: `scripts/session_status.sh`/`session_snapshot.sh` 的 "compute backend contract" 段落 `from services.experiment_jobs import load_experiment_job_contract` — 该模块随 2026-06-28 重建(commit `a078351e`)物删, 但两个 session housekeeping 脚本从未同步更新, 且用 `2>/dev/null || echo "unavailable"` 静默吞掉了 `ModuleNotFoundError`, 表现为 SESSION_HANDOFF.md 长期显示 `Backends: unavailable`(而非报错让人发现)。这正是用户"残留删不净"疑虑的一个实例: 一次删除(experiment_jobs.py)留下两处下游孤儿引用(session 脚本), 因静默 fallback 而不触发任何告警。**修**: 两脚本改为直接用 PyYAML 读 `experiment_jobs.yaml` 的 `backends`/`job_families` 字段(不依赖已删的 Python loader 类), 实跑验证 `Backends: local:active, modal:active` 正确显示。

**衍生发现(留给簇10 一并处理, 未在本簇动手)**: 排查过程中发现 `bash scripts/chunkyctl jobs ...` 本身也是死路径 — `backend/scripts/chunkyctl.py` 只实现了 `doctor` 子命令 + 5 个显式 `_RETIRED` 子命令(worktree/docs/preflight/audit/data-status), `jobs` 两者都不在, argparse 会静默走到 `parser.print_help()` 后返回 0(非崩溃, 但也不执行任何跑批调度) — 与簇10 `data-status` 死路径同源, 一并在簇10 处理。

### 簇9 — feature_registry.yaml 删除 (2026-07-07)
`backend/config/feature_registry.yaml`(297行, model_input_excluded + 各特征组声明)全仓库 grep 确认 0 个 Python `open()`/`yaml.safe_load` 消费方(唯二命中 `check_lineage_drift.py` 和 `scripts/safe_commit.sh` 都只是把文件名列进"这类文件变了要重生 lineage"的触发正则/docstring, 非真读取), 也未登记进 `data_layers.yaml`/`data_module_members.yaml`。git log 溯源: 其消费方(L2 特征 panel builder)已分两批物删——2026-06-28 `0320a505`(U1 L2 特征 panel 退役)+ 2026-06-28 `a078351e`(纯数据平台重建)。git rm 整文件, 同步清 `safe_commit.sh` 触发正则和 `check_lineage_drift.py` docstring 里的过期提及。

### 簇10 — chunkyctl 死路径修复 (2026-07-07)
两个独立但同源的死路径, 根因都是 2026-06-28 重建把 `chunkyctl.py` 从 1660 行降到"最小重建版"(只留 `doctor` + `_RETIRED` 优雅提示列表)时漏记: **(1) `jobs`** — 旧版 `chunkyctl.py` 曾完整实现过 `run_jobs()`(读 `services.experiment_jobs.load_experiment_job_contract`), 该 loader 随重建物删后 `jobs` 子命令既未重实现也未补进 `_RETIRED`, 实测裸 argparse 报 `invalid choice: 'jobs'`(exit=2), 比其余 5 个已退役命令的统一 JSON 提示更不友好; 补进 `_RETIRED` 元组即可复用既有优雅路径。**(2)`data-status`** — `data-status` 其实**已经**在 `_RETIRED` 里(`chunkyctl.py data-status` 本身工作正常), 真正的 bug 在 `scripts/chunkyctl` shell wrapper: 它对 `data-status` 走了一条独立分支, 直接 `python backend/scripts/data_migration_status.py`(该脚本本体已随更早一批清理物删, 只剩空目录), 完全绕过了 `chunkyctl.py` 已实现的退役提示, 实测直接 `FileNotFoundError` 崩溃(exit=2)。**修**: wrapper 的 `data-status)` 分支改路由到 `chunkyctl.py data-status "$@"`(与 `jobs` 分支的既有写法一致)。两处修完实测均返回 `{"status": "retired", ...}` JSON, exit=0。**未做**: 未重新实现 `jobs`/`data-status` 真实功能(计算任务契约/tushare迁移状态), 因为其依赖(`services.experiment_jobs`/`services.compute.modal_adapter`/`data_migration_status.py`)本身随策略/编排层退役, 重建这些不在本次死代码清理范围内, 是 edge 重建阶段的事。

## 待执行簇 (占位, 完成后追加小节)

- 簇5: 测试文件清理(test_v3_selection.py/test_perf_p1_trade_date.py 删, test_system_routes.py/test_real_data_consistency.py 部分修)
- 簇7: kline_source.py vs pipeline/clean.py 内联清洗逻辑重复性厘清
- 簇12: main.py `/v3` `/legacy` 断链路由修复
- 簇1+2: 旧 vanilla JS 前端整体删除 + main.py 死代码 + 15 个 contract 测试
- 簇13: PROJECT_INDEX.md §1 架构描述最终一致性收口(待簇1/2/12 完成后一次性做)

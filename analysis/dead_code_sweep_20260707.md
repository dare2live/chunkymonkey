# 全仓库死代码普查 (2026-07-07)

## 背景

用户在完成 `raw_aif10_peer_valuation` 退役收尾的 3 项顺带发现清理后提出疑虑: "我怎么感觉残留删不净了呢, 一直在删除残留"。根因诊断: 2026-06-28 "纯数据平台重建" (git rm ~245 策略/serving 文件) 是一次白名单裁剪式大手术, 只删了被明确判定该删的文件本体, 没有系统性验证"这些文件留下的引用/连接组织(connective tissue)"是否也一并清干净; `check_dead_references.py` 只抓**断引用**(import 一个不存在的模块 → 立刻报错), 抓不到**可达但从未被调用的孤儿文件**(import 链完整、语法正确, 但没有任何入口路径能走到它)。这是两类不同的死代码, 前者有自动化门, 后者没有。

用户拍板"一次性全仓库死代码普查"(而非继续零散被动发现), 用 **entry-point reachability 分析**方法: 先枚举全部真实入口点(main.py 5 个挂载路由 / daily_update.sh 调用链 / safe_commit.sh ~10 个 check_*.py / CI whitelist / moth claims.yaml / pytest 默认收集), 再反向追踪哪些代码从未被这些入口触达。

100-agent workflow 产出 13 簇候选, 随后用 codegraph(精确 AST 调用图) + moth(text-match fan-in, 对普通词文件名不可靠, 已用 codegraph 交叉验证发现假阳性) 逐簇验证, 用户批准后按簇执行、每簇跑全量测试 + moth assert + codegraph sync 收口。

## 方法论笔记 (留给后续同类普查参考)

- **codegraph 比 moth 更可信**: `moth coupling --impact industry.py` 报 104 文件命中, 实为文本子串误配(`v_sw_industry_pit`/`sw_industry` 等标识符含 "industry" 子串, 或注释里的中文"行业"), 逐函数核 `codegraph callers` 确认真实 0 调用方。moth 对常见词文件名(constants/industry 等)不可靠, 需 codegraph 复核。
- **pytest 默认收集 ≠ 活测试**: `pytest.mark.realdb` 标记的测试文件默认被排除(pytest.ini), 需 `-m realdb` 强制跑才能看真实 pass/fail, 用于甄别"看似是测试实则测已删模块"的死测试。
- **fixture 目录不能一刀切**: 15 个"空壳测试目录"候选中 `backend/tests/fixtures/` 实为 52 个真实 tracked 的 `domain_samples/*.json`(被 `test_market_pulse.py` 消费, `git ls-files` 精确核实), 从删除批次中排除, 只删真正只剩 `__init__.py` 的 14 个目录。

## 已执行簇 (随本文档持续追加)

### 簇11 — 15 个空壳测试目录 → 实删 14 个 (2026-07-07)
`backend/tests/{backtest,buy_signal,candle_pattern,features,labels,ml_ranking,optimization,paper_sim,perf,portfolio,portfolio_walk_forward,routers,sentiment,trading_config}/` 仅剩 `__init__.py`(git-tracked 内容为空), 对应功能已随 2026-06-28 重建整体退役, 无任何测试收集。`fixtures/` 因含 52 个真实 fixture 数据被排除(`git ls-files` 精确核实, 早期估算"约55个"有约5.5%偏差, 2026-07-08 独立核实批修正)。

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

### 簇7 — kline_source.py 厘清结果: 非重复实现, 是完全孤立的死代码对 (2026-07-07)
报告原候选问题是"kline_source.py 是否与 pipeline/clean.py 内联清洗逻辑重复", 逐层核实后结论不是"重复"而是"孤立死代码": `pipeline/clean.py` 本身只有40行, 无任何内联清洗逻辑, 只是调度两个既有环节(`build_price_kline_qfq_tushare.py` SQL CTAS 脚本 + `data_audit` post-sync 审计), 两者根本不重叠。真问题在 `kline_source.py` 自身——它是 tdxhub/eastmoney/akshare 多源并存时代的通用行清洗/归一化层(`clean_price_row`/`normalize_price_rows`/`aggregate_monthly_from_daily`, 含"governance v1: volume unit = lots"等 tdxhub 专属注释), `codegraph query` + 全仓库 grep 确认**唯一 Python 导入方是它自己的单测 `test_kline_cleaning.py`**(测的还是已退役的 tdxhub 手/股换算场景), 现行唯一活跃的 qfq 构建器 `build_price_kline_qfq_tushare.py` 只用 `services.duck_adapter` 做纯 SQL 变换, 完全不经过这层 Python 逐行清洗。进一步核实发现 `kline_source.py` 依赖的 `services/data_processing_monitor.py`(`ProcessingToolStats` 类 + `mart_data_processing_tool_run`/`mart_data_processing_tool_issue` 两表 DDL)**唯一消费方就是 kline_source.py 自己**, 且这两张表在真实 `smartmoney.duckdb` 里**从未被创建过**(`SELECT COUNT(*)` 报 Catalog Error 表不存在) —— 双重确认整条链自始至终没被真正启用过, 是 tushare 整合后被架空的完整子系统, 非"和 clean.py 功能重叠"。**执行**: git rm 三件套(`kline_source.py`/`data_processing_monitor.py`/`test_kline_cleaning.py`) + `schema_versions.py` MART_VERSIONS 去两条死表登记 + PROJECT_INDEX.md 治理/杂项行去除引用。全量测试608→603 passed(减5为参数化目录扫描测试随文件数变化, 非回归, 无新增failure)。

### 簇5+12 — 测试文件清理 + 路由断链修复 (联动执行, 2026-07-07)
两簇实为同一根因的不同表现, 一并处理。

**簇12 根因**: `main.py` 的 `/v3`+`/legacy` 路由自 2026-06-28 重建后一直 `RedirectResponse(url="/api/dossier/view")`, 而 `/api/dossier/view` 本身随重建已被 git rm(策略/serving routers 整批退役), 用户访问这两个旧路径会先看到 307 再撞 404, 是断链而非"已妥善退役"。当前唯一活前端是 `/app/`(edge React, 2026-07-02 挂载, `/` 根路由已在 2026-07-03 修对)。**修**: `/v3`+`/legacy` 四个路径改直接返回 410 Gone JSON(`{"error": "legacy_retired", "redirect": "/app/"}`), 不再走会撞死链的 307。

**簇5 实测结果**(逐文件强制用 `-m realdb`/`CM_REALDB=1` 跑出真 pass/fail, 不采信报告原始猜测):
- `test_v3_selection.py`: 12/12 FAIL(测 `/api/v3/selection/*` 已退役路由, `KeyError: 'data'`)。git rm。
- `test_perf_p1_trade_date.py`(`backend/tests/scripts/`): **未删, 与报告原判断不同**。实测显示该测试标 `perf`+`slow`(CI默认排除) 且对目标表 `mart_p0b_oos_predictions` 缺失有 `pytest.skip()` 优雅降级(非 FAIL), 强制跑验证结果是 SKIPPED 不是 FAIL。该表正是簇8判定"未来 edge 重建 schema 契约, 不删"的同一张表——按同一逻辑, 这个已正确 skip-gate 的 perf 测试也不该删, 表重建后会自动激活。
- `test_system_routes.py`: 实测3 FAIL/3 PASS。`test_root_redirects_to_v3`(断言 location 含"v3") + `test_legacy_returns_410_gone`(断言 redirect 字段含"/v3") 都是给旧架构写的, 随簇12的 main.py 修复一并更新断言为 `/app`; `test_workbench_storage_route_defaults_to_persisted_read_model` 测 `routers.workbench`(2026-06-28 随策略层整批 git rm, 非待重建的边缘表, 是永久退役), import 直接 ImportError, 删除。
- `test_real_data_consistency.py`(`backend/tests/realdb/`): 实测(`CM_REALDB=1`)3 FAIL/1 PASS。`test_real_tdxhub_kline_reaches_latest_completed_trade_date`+`test_real_fallback_rows_do_not_overlap_existing_primary_keys` 查 `price_kline_tdxhub`(CatalogException, tdxhub 早随更早批次全删物删)——删。`test_real_trading_calendar_is_available_and_has_completed_trade_date` 是**真连错库 bug**(非退役问题): 该测试连 `smartmoney.duckdb` 查 `dim_trading_calendar`, 但该表实际活在 `reference.duckdb`(§9拆库迁移后), 实测 `reference.duckdb` 里表存在且行数(5343)/起始日期(2005-01-04)与测试断言完全吻合——证明断言本身是对的, 只是连错库。改连 `reference.duckdb` 后 2/2 PASS。

全量测试608→603 passed(净删4测试文件+2用例, 无新增failure), main.py import + 路由行为实测验证。

## CI 事故记录 (2026-07-07)

批1(cf4280c4)删除 `services/api_cache.py` 时漏了一处引用: `.github/workflows/ci.yml` 的
"Smoke import" 步骤是硬编码在 YAML 里的裸 `python -c` 脚本(非 pytest 收集, 不在
`check_dead_references.py` 扫描面内), 其中 `from services.api_cache import cached,
get_cache_stats` 引用了已删模块。`check_dead_references` gate 只扫 `.py` 文件的 import
语句, 扫不到 YAML 内嵌 python 字符串里的引用, 导致批1~批4(5个commit)连续 CI 红而未被
本地/门禁察觉, 直到用户报"CI大量报错"才发现。**修**: 删除该失效 import 行(commit
bcac60eb), 本地逐条复现 CI 全部4个 step 验证后确认转绿。**待评估**: 是否要给
`check_dead_references.py` 加第6道扫描面(`.github/workflows/*.yml` 内嵌 python 脚本),
本次未做(单次事故未构成"频繁"立法门槛, 按 mio 协议#7 "频繁问题才立法"暂不扩展, 留观察)。

### 簇1+2 — 旧 vanilla JS 前端整体删除 (2026-07-08)
`index.html` + `assets/js/` 全部26个JS文件(app.js/workbench-view.js/stock-view.js等一串互相耦合的旧前端, 此前调研阶段就已确认"不是孤立UI死重, 是一整套更大的老vanilla-JS workbench前端") + `main.py` 对应死代码(`render_index_html()`/`build_index_asset_version()`/`INDEX_ASSET_FILES`/`INDEX_ASSET_VERSION_PATTERN`, 实测确认无任何路由调用`render_index_html()`, `HTMLResponse`导入0使用, `hashlib`/`re`两个仅供此死代码使用的顶层import一并清) + `/assets` static mount。当前唯一活前端已是2026-07-02起重建的 edge React(`frontend/`, 挂`/app`), 根路由`/`已在2026-07-03修复指向它, `/v3`+`/legacy`已在簇12改410 Gone指向它——旧vanilla前端彻底失去唯一入口(此前用户能访问到它的路径已全部指向别处), 确认是纯死代码非"待重建参考对象"。

**连带发现**: `backend/tests/contract/` 目录下15个契约测试(`test_workbench_frontend_contract.py`等)全部测试这套旧前端的DOM结构/入口注册关系, 随前端删除一并git rm, 目录变空后一并移除(无`__init__.py`残留, 与pytest收集无关)。

**执行**: git rm index.html + 整个assets/目录(29个文件, 含1个css) + 15个contract测试 + main.py删约50行死代码。全量测试608(簇5后)→561 passed(净减47个测试用例, 全部是被删文件自身的测试, 0新增failure)。

### 簇13 — PROJECT_INDEX.md §1 架构描述自相矛盾收口 (2026-07-08)
本节此前长期停留在2026-06-28"纯数据平台定型"的快照状态(流程图末尾写`[edge 待重建 — 唯一消费方缺位]`), 与同一文档§3代码地图早已存在的"edge 件(2026-07-02 起)"institution_profile/paper_portfolio描述互相矛盾——这正是"簇13"这个名字最初想修的自相矛盾。**执行**: 流程图末尾改为准确描述当前edge重建现状(3 service + 5 routers + React前端); §3 routers/前端行同步更新(3 live→5 live, 旧vanilla前端行改为"已物删"描述); 顺带修正reference库dim计数(4→2, all_ever_listed/listing_status已在2026-07-07早些时候的commit整表退役, 本表此前未同步)。

## 独立核实批 (2026-07-08, 用户"对照方案查看完成情况")

13簇执行完毕后, 用户要求对照方案独立核实完成情况——不采信执行过程中的自我总结。16-agent workflow(13个逐簇核实 + 3个全新残留扫描/全量测试/CI状态)亲自跑命令核实, 结论: **11/13簇PASS无出入, 2簇PARTIAL有真实遗留**:

1. **簇11 fixture数量口径不符**: summary声称"约55个", `git ls-files` 精确核实实为52个(偏差约5.5%)。清理动作本身(删14个空壳目录、保留fixtures/)属实, 只是数字表述不准。**已修**: 更正 PROJECT_INDEX.md + 本文档两处"55个"为精确的"52个"。
2. **簇13 PROJECT_INDEX.md文档内部自相矛盾未彻底清**: §1架构流程图(line 188)仍残留"reference 4 dim", 与同文件§2表格(line 214)"2 dim"及changelog自述"4→2"互相矛盾——这正是簇13本该修的"自相矛盾"类问题, 但只改了表格文字未同步改流程图。**已修**: 流程图改为"reference 2 dim"。
3. **簇4 轻微残留(非阻断)**: `backend/scripts/check_universe_filter.py:38` 的 `EXEMPT_FILES` 白名单仍保留一条指向已删除的 `migrate_reference_db.py` 的豁免条目, 因目标文件不存在, 该条目从未被实际触达(纯集合成员, 不影响linter功能), 但属于历史豁免规则未同步清理。**已修**: 移除该条目, 重跑linter确认仍 CLEAN(1117文件检查)。

其余11簇(1+2/3/5/7/8/9/10/12/CI事故)核实agent均报告"无出入", 机械门三道(dead-references/doc-drift/moth)全绿, 全量测试561 passed 0 failed, git工作区在核实前无未提交残留, CI最新状态success。

**教训**: 即使全程走"跑测试+门禁+全量验证"流程, 长链条多批次执行仍会在文档层面(非代码/数据层面)留下人工总结的细节偏差和跨commit未同步的矛盾点——机械门(dead-references/doc-drift/moth)检查的是**当前代码状态**, 不检查"文档内部数字/表述前后一致性"这类问题, 独立核实(尤其是不采信自我总结、亲自重新跑命令)在长任务收尾阶段仍有不可替代的价值。

## 收官统计

13簇死代码普查执行完毕(2026-07-07~08, 分5批commit + 1次独立核实修正批)。中途遭遇1次CI事故(见上节)已修复。全量测试从执行前的608 passed收敛到561 passed(净删约50个测试用例, 全部是被删死代码自身的测试, 全程0新增回归)。独立核实批发现并修正2处真实文档遗留(fixture计数/架构图自相矛盾)+1处轻微残留(已删脚本的linter豁免条目)。

# PROJECT_INDEX.md — Chunky Monkey v2 项目地图 (context-only briefing)

> 用于防止对话压缩 / context 丢失导致重复发现项目结构 / 误解数据资产.
> 内容是**项目地图**, 不是规则 — Codex 规则在 `AGENTS.md`; 当前阶段计划在薄入口 `goal.md`; 历史状态/已完成证据在 `analysis/project_state_ledger.md`; `SESSION_HANDOFF.md` 是生成恢复快照; durable contract 在 `docs/README.md` 指向的 active docs; `CLAUDE.md` 是 legacy Claude-specific history.
> 2026-06-05 起，旧 GCP / GCS / phase5 monitor / cost tracker 条目只作历史证据，不是可恢复执行面。当前长任务/花钱任务必须走 `backend/config/experiment_jobs.yaml` + `scripts/chunkyctl jobs`，`local` active，`modal` planned/blocked。
>
> **目标**: 新接手 (无论 Claude 还是人) 读完此文档**不用看代码 / 不用查 DB** 就能理解:
> 项目业务 / 架构 / 技术路线 / 数据资产 / 当前进度 / 已知坑 / 常用操作.

最后更新: **2026-06-06** (TuShare no-persist exact-flow probe wiring + need_027 probe diagnostics hardening + storage retention owner/consumer policy contract + data-source capability router contract + need_027 candidate validation metadata + provider-neutral experiment job contract + execution-surface audit + retired GCP execution surface removal + architect-controller skill install + verify-verifier rule + Moth complexity path normalization + local complexity baseline refresh + data-health dry-run read-only fix + Moth evidence path sync + design-review preflight machine gate + Moth registry instruction-source sync + after-close data refresh + controller-agent preflight hard gate + retention dry-run inventory + storage payload cap recalibration + DB manifest attach policy + DB boundary static gate + holder replay safety + Codex instruction-source boundary + DuckDB capacity audit + need_027 exact-flow probe gate + stage-opt supply/readiness/schema contract + stage-opt signal-date K-line coverage evidence + stage-opt source-aware density diagnostics + stage-opt source freshness/window diagnostics + iFinD MCP research-only routing recheck).

## [INDEX] 最近增量 (只留 7 天, 历史在 analysis/project_index_changelog_archive_20260611.md + ledger)

- **2026-06-12 Fable-5 复查降级期 (Opus) 工作 — 抓 2 实质问题 + 1 立案**: (1) **概念事件字段方向反 (CRITICAL)**: dc_member 实测 ts_code=BK*.DC 概念板块/con_code=股票, build_concept_events 原码反向会把 5521 只股票当概念; raw+snapshot 双路径改正 + 真实形态防回退测试 (fixture 抽象命名与实现一致地错 = "测试 pass ≠ 产物没说谎"再添一例); post-fix-audit 表 0 行无残留。(2) **need_027 验收文档引用错**: "29 终败 resolved" 实为上午轮 12:14 旧记录; 下午轮 (15:10-18:43, 829批/29终败) 的 _record_outcome **静默未生效** (watermark/queue 全停在 12:14 但 log 正常) — 立案 task #14 诊断 (疑 smartmoney 写锁期 get_conn 行为 + run_domain ok 宽松/record 严格双标); 29 日缺口由 drain 日历 gap 兜底 (不依赖 queue 的设计价值实证)。(3) 其余复查项核验通过: calendar_gate allowlist / modal 三源 / data-status 分类 / fetch_irm_qa。
- **2026-06-11 数据迁移状态仪表盘 (data-status) + 宪法 v2 状态落实**: `backend/scripts/data_migration_status.py` (入口 `scripts/chunkyctl data-status`) — registry 驱动读 watermark+failure_queue+日历, 一眼看清每域 OK/STALE/NEVER_SYNCED/未决失败 (把"数据基建做好了么"从手敲 SQL 变可执行答案; 真相源唯一, NEVER_SYNCED 不臆造已落库)。日历新鲜度用 canonical latest_completed_trade_date (VARCHAR/未来日 bug 修正)。当前态: 21 注册域 / 7 已落库 (moneyflow 4.22M/stk_limit 5.76M/dc_member 2.71M 等, 总 12.9M 行) / 14 待回填 (chain4/5 在跑+W-B 待注册) / 2 open 失败 (moneyflow_ind_dc/limit_cpt_list, 下次 drain 自动). 3 单测. **宪法 v2 状态落实** (Stop hook 抓 stale): goal.md "草案待确认"→"已生效", docs/PROJECT_CONSTITUTION.md 即 live v2, 草案加"已采纳"头注。
- **2026-06-11 互动易/e互动 问答直抓落地 (产业链 L2 关系边, irm_qa premium 无权限的替代)**: `backend/scripts/fetch_irm_qa.py` — 深交所 `POST irm.cninfo.com.cn/newircs/index/search` (JSON, updateDate 降序流, epoch-ms 字段) + 上证 `POST sns.sseinfo.com/ajax/feeds.do` (HTML 片段, ask_ico/answer_ico 块, 相对时间还原分钟精度) 双源全局流收割 + 本地按代码过滤; 落 `data/concept_snapshots/irm_qa/irm_qa_<YYYYMM>.parquet` (按提问月分文件, 幂等 merge 原子覆盖, 字段 code/question/answer/q_time/a_time/source/fetched_at)。**实测坑**: SZ 服务端 per-code 过滤参数全失效 (stockCode/keyWord/"code,secid" 组合均 totalRecord=0) → 全局流+本地过滤反而对多代码列表请求更少。礼貌限频 2s/请求。首跑实测: sz 27页/565行 + sh 40页/772行 (--days 1, 0 malformed), 19 单测; 真数据抽样核验 (真投资者问答, 唯一 except=httpx.TransportError 有界重试无吞错)。消费方: 链谱边弱监督 (Serenity W5)。**注**: 此条推翻同日早些"互动易未实证"判断 (newircs/index/search 实为浅层 JSON)。
- **2026-06-11 测试红 triage + 本会话 calendar_gate 回归修复**: 全量 triage (3471 测试 99.1% pass): A 环境性 4 (缺 rg) / B 前端 widget 5 (WidgetFormatUtils 导入链) / C 真 bug 0 / D 本会话 2 (已修). 修本会话 2 处 calendar_gate: sync_runner drain today 排除 + build_feature_map 时间戳加 Phase ψ.5 allowlist (stash 实证 21→19 确属本会话). modal 转正对齐 (用户"该用就用": yaml/test/goal 三源收敛, active=可派发非必派发, dry_run 默认 True 防误花). **backlog**: 19 个 HEAD 预存 calendar_gate 红 (bestchoice/ensemble/v7 脚本 wall-clock end_date) 归 P2, 需逐文件判 legit-violation vs false-positive.
- **2026-06-11 概念增删事件流 detector (fact_concept_event)**: `build_concept_events.py` 相邻交易日成分集合 diff → concept_born/dead/member_add/drop 四事件; 真相源 = 概念成分快照本身 (raw_tushare_dc_member 历史回填=reconstructed / parquet 自养=observed), 纯集合运算无中间表; event_date=后一快照日 (变更首次可观测 = PIT 锚), as_of_mode 显式区分 PIT 强弱; 增量 watermark 续传。6 单测 (增删/诞生消失/event_date PIT/增量过滤/as_of 标注/单日空). 用途 = 题材生命周期标记 + 链谱边弱监督 (Serenity W5). 待 dc_member 历史落库 (chain2 回填中) 跑 measured 实验。互动易 L2 关系边: ~~接口未实证暂不进生产~~ → 2026-06-11 晚已实证落地 `fetch_irm_qa.py` (newircs/index/search 实为浅层 JSON, 见本节顶部条目)。
- **2026-06-11 深夜批: runner 分页 + doctor 告警巡检 + 前端急救三件套**: (1) `sync_runner._fetch_paged` offset 分页 (registry `page_limit` 字段驱动; 中间页败=整批 None 防部分写伪完整; 50 页防御上限) → top_inst 注册落地 (1000/页, chain4 队列); stk_surv 实测 trade_date 参数 0 行 — 参数语义未核证暂缓 (0 行不下结论纪律)。(2) `chunkyctl doctor` 新增 `alert_flags` 节: /tmp/chunkymonkey_ALERT_*.flag 巡检入 doctor report (约定变代码), 2 单测。(3) **前端急救三件套** (毕业自 mock 静默回退 CRITICAL): `frontend_config.yaml`+`GET /api/v3/config` (阈值唯一真相源, 拉不到=中性灰不许内置回退) / fetchJson 失败台账 `SOURCE_STATUS`+页面降级 badge+dataReady 带 degraded / stability 硬编码 0.80 改真 null 显 '—'; 5 个 jsx 样板迁移 (picks 0.58+红黄绿/stock-view 0.5/lab stability)。preview 实测: config API 200 + 68 源台账全 ok + 0 console error + 页面正常渲染。剩余硬编码阈值 (lab 0.6/0.65 装饰色, stock-view.js cutoffs) 列下批。前端入口 = `/v3/Chunky Monkey v3.html` (uvicorn main:app 8000; preview 配置 .claude/launch.json 本地不入 git)。
- **2026-06-11 数据平台 P0 第一批: 失败分级 + 日历 gap drain (对抗复审 12 条, 9 修 3 排期)**: (1) `daily_update.sh` 29 处吞错点 (26 处 `|| log WARN` + tdxhub sync_exit 丢弃 + SLA 倒挂 + [gate] 漏网) 全部接 `step_degraded` — 写 `/tmp/chunkymonkey_ALERT_daily_update_degraded.flag` + 链尾汇总 + osascript 通知, 链首清 flag。(2) `sync_runner.drain_domain` + `--drain` CLI: **日历 gap 扫描取代队列重放** (failure_queue 按错误类型聚合不存日期; 真相源 = trade_cal + raw 表本身, 终败/漏跑/历史空洞三类一网打尽); 完整日口径 = 行数 >= min_rows (防 vendor 截断批被当完整, 复审 HIGH), 截断最新优先, today 锚定 Asia/Shanghai, 非按日域 fallback 增量 run_domain (防 drain-only 接线静默停更, 复审 HIGH)。(3) daily_update Step 2.95 接线: drain 即增量 (昨日数据 17:00 落库恰对齐 JOIN t-1)。证据: 16 单测 passed + 实弹撞写锁显式 error + exit 1。(4) **SLA 防线 registry 驱动** (`update_watermark_sla.py`): sync:* 条目从 sync_registry.yaml 自动生成 (注册即入防线), 新增 4 个显式状态 NO_PROBE_RULE/NO_QUERY_MAPPING/DB_LOCKED_UNVERIFIED/NEVER_SYNCED 消灭"查不到=静默OK"; 实弹 dry-run 当场抓到 lhb_daily stale 13d + xdxr stale 17d (分红季!) 两条真腐烂 + 3 个映射盲区域。剩余排期: dead_dates 降频 / doctor flag 检查。(5) **旧源退役批 6 域注册+排队回填** (用户决策"能在 tushare 获取就从 tushare 获取"): top_list(龙虎榜2005起)/report_rc(盈预)/ths_hot(热榜)/moneyflow_hsgt(北向)/dividend(分红除权对照)/adj_factor(复权因子盘前9:20当日可用) — chain4 排 chain3 后自动接力。**实测抓住 3 个截断陷阱**: top_inst 单日 1000 行整=隐性单页上限 (暂缓等分页), ths_hot 全量 2000 整=多榜合计截断 (fixed_params 锁热股单榜 444 行完整) + 同日多 rank_time 快照 grain 必须加 rank_time, dc_hot 参数未核证暂缓。runner 新增 date_param 通用支持 (dividend 用 ex_date / report_rc 用 report_date); allow_empty 域 gap 不可判定 → drain_inapplicable 走 watermark 增量 (顺手闭环 suspend_d 不收敛)。20 单测 passed。**主源定位纠偏 (用户原话二次强调)**: tushare 主源不是兜底 — 复权链方向 = adj_factor/dividend **转正**, tdxhub xdxr 降备援交叉验证, 旧源故障的响应是加速切换不是修旧路径; 权威表述移入 CLAUDE.md §4.3, 禁"兜底/对照"措辞。**第三次递进 → 全量默认 tushare**: 存量 44 表 (tdxhub 12/aif10 9/akshare 22/停用 1) 全部迁移对象, 地图+波次 = `analysis/tushare_full_migration_map_20260611.md` (W-B 16 接口 catalog 核证积分全够; 真例外仅 F10 文本类与本地 CYQ; 估值分位改 daily_basic 自算更 PIT); K 线三件套 daily+daily_basic+adj_factor 已注册, chain5 排队 (daily_basic 的 float_share/free_share = CYQ 流通股本输入替代本地推算); data_product_contract 旧条款 "no global primary" 已改写。
- **2026-06-11 功能地图工具 (FEATURE_MAP.md 机器事实层)**: `backend/scripts/build_feature_map.py` (入口 `scripts/chunkyctl map`, `--check` 漂移门) 从 4 真相源机器枚举: chunkyctl 子命令/launchd plist/@router 路由/sync_registry 域/**产表→writer 映射 (单 writer 契约执法视图: 302 表中 121 张多 writer)**/codegraph 依赖热点 (双过滤抗按名解析假边: 唯一定义名 + caller 实际 import 目标模块 — 实测剔除 Path.resolve 272 条假边)。保鲜: safe_commit Step 2.6 漂移才重生成并同 commit (时间戳行不算漂移, 防噪音 commit); 生成失败 optional 级不挡 commit 但可见。**职责切分红线: FEATURE_MAP=机器可枚举事实, PROJECT_INDEX=人工判断层 (坑/权重/状态), 互不重复**。6 单测 + chunkyctl 39 回归 = 45 passed, 生成 ~1s。
- **2026-06-11 Serenity 方法论锻造 + TuShare 模块化 catalog + 金股验证**: (1) @aleabitoreddit 全量 6463 贴文 (镜像站后端 API 直拉) 提炼方法论全集 (B1-B12 信念/W1-W3 工作流/证据三档, 全带原话日期) + 集成设计 (拆四块: chain-research skill / industry_chain.yaml 链谱维表 / 解禁质押减持 veto / chain_diffusion 第 8 引擎条件激活; **明确反对建第四套书与照搬选股逻辑**; 链谱后视污染 freeze-date forward-only 红线) + 不可迁移批判 — analysis/serenity_20260611/。(2) `backend/scripts/build_tushare_catalog.py` 生成 `backend/config/tushare_api_catalog.json` (239 接口结构化: 积分/限频/字段/起始, probed_* 增量回写设计) = sync_registry 上游字典, 镜像更新可重跑。(3) 券商金股 measured (29 月): 共识>=3 月均超额 +1.41%/累计 +69% vs 市场 +18%, 但小桶噪音 (月中位 8 只) — 定位候选池增强因子非独立策略, 归 v7-F4 与 report_rc 同族 W5 设计 — analysis/broker_gold_validation_20260611.md。
- **2026-06-11 产业链 L1 数据底座: fina_mainbz 主营构成接入 + by_ts_code 批模式**: Serenity A股化三层方案 L1 — `fina_mainbz` (按产品分项收入/利润/成本, 立讯 002475 实测 4 产品项) 注册进 sync_registry, 首期 20251231 年报全市场 ~5300 股排 chain3; sync_runner 新增 `by_ts_code` 模式 (股票清单真相源=K线近45日活跃 code, 非 dim 表) + `fixed_params` 透传。**PIT 教科书陷阱已写进 registry**: end_date 是报告期非披露日, 可用时点必须 JOIN ann_date/disclosure_date。用途: 概念标签→收入加权产业链节点 (蹭概念 <5% vs 真卡点 >50%, 一个 JOIN 算纯度)。互动易 irm_qa_sz/sh 实测 premium 无权限 → 走交易所公开接口直抓 (L2 关系边); vip 全市场版无权限且重复访问触发网关风控 357s 封禁 → 拉黑 (权限错立即放弃, 重复打 = 风控)。
- **2026-06-11 sync_registry 范式落地 + 首个生产域回填 (Task 1 writer/watermark/failure_queue 三 gate)**: 架构稿 §3 实施 — `backend/config/sync_registry.yaml` (一条目=一域, 7 个 W1 域注册: moneyflow/limit_list_d/stk_limit/stock_st/suspend_d/moneyflow_mkt_dc/trade_cal, 每条带 pit_anchor/available_after/data_start/freshness_sla/min_rows 机器可读契约) + `services/data_sources/sync_runner.py` (通用同步器: 交易日历切批 by_trade_date/by_date_range/full_refresh, 0行退避重试, MERGE on grain 幂等, 列演进, 复用 source_watermarks 服务) + `data/tushare_raw.duckdb` 新库 (manifest 注册, 与主库写锁解耦 — DB 管理决策: 新域不进 34GB 单体)。adapter 加 `fetch_raw(api_name)` sync 专用入口 (raw 镜像不加工)。首跑实测: moneyflow_mkt_dc 全史 762 行落库 + watermark 写入 + 7 单测 green (幂等/0行重试/列演进/grain 缺失防御)。**实战坑 2 个**: (1) data_start 必须按"单日参数口径"实测, 范围查询会掩盖真实起点 (mkt_dc 单日 20230103 空, 范围查却有) (2) 部分接口 trade_date 单日参数语义失效只认 start/end → by_date_range 模式。
- **2026-06-11 K线 denormal 停牌行 + audit 两处口径修正 (audit 链转绿)**: nightly audit critical 双拆解 — (1) **真 bug**: tdx 协议对停牌日写 float32 denormal (~5.9e-39) 而非 0, denormal>0 绕过 tradability `volume<=0` 停牌检查 = 停牌股被判可交易 (实测 137 行/44 股/2026-04-23 起, 06-04 仍在产生)。三层修复: 写入路径 `_denormal_to_zero` 清洗 + 存量 137 行 UPDATE 归零 + `is_suspended` 物理下限 1.0 (不足 1 手=停牌) 防御, 防回退测试 3 case。(2) **audit 误报两处**: vwap/close ratio 检查未乘 factor, 近期除权股固有偏离 1/factor 假突破 1.5 阈值 (实测 002245 1.508→1.015), 已乘 factor 对齐 + 停牌行排除; fwd_cost_after 1.0 阈值误判真实牛市连板为坏数据 (实证 300085 自 2024-09-13 八个精确 20.0% 连板 10日+453%, 无复权断层), 改为物理复利极限 block (1.3^n-1)/1.0 观察线 warn, warn 不再走非零 exit (防告警疲劳淹没真 critical)。验证: audit ok/ok/warn + launchd 跑 OK flag 清除 + 84 tests passed。防回退: 探活/检查类阈值必须先问"物理上可能吗", 真实市场极值≠数据错误。
- **2026-06-11 tdxhub 断流根因修复 + 数据源主力反转决策**: 根因不是服务器全死, 是 **Surge 代理在本地接管全部 TCP** (实证: 物理不可达地址 192.0.2.1 任意端口 connect 0.00s "成功") — connect 永远秒成功骗过 server 选择, 真实数据链路只有部分服务器活 (协议层握手实测 9/35 可达, DEFAULT 5 台全活但运行时用的 health 排序池全是死 IP)。修复: 应用层握手扫描出可达池 → `CM_TDX_SERVERS` env (iter_tdx_servers 最高优先级) 写入 daily-update launchd plist。**防回退: 代理环境下 TCP connect 测试无意义, server 探活必须协议层握手**。同日用户决策: **数据源主力反转 — tushare 主力 / tdxhub 备用 / miaoxiang 第三 / akshare 最后** (理由: 真 alpha 来自新接入数据域且全在 tushare; tdxhub 屡断)。probe 239 接口终版: **171 ok / 19 no_permission / 37 error / 12 empty**。
- **2026-06-11 Fable-5 复查修正降级期 3 个真问题 (模型曾回落 Opus, verifier 也降级)**: (1) **PIT 泄漏补漏**: `build_stock_formula_buy_signal_daily.load_today_rows` 原修复只改了 picture as-of, 但寻优统计仍 JOIN legacy in-sample 表 `mart_per_stock_strategy_optimal` (sharpe/win_rate 经 6 因子 score→tier 流入 live selector) — 组间 scope 缝隙; 改读 `mart_per_stock_stage_strategy_optimal_pit` 的 cutoff<=signal_date as-of + oos_*, 防回退测试断言不 JOIN legacy + 未来 cutoff 不可见。(2) **modal 烧钱隐患**: `modal_adapter.submit_job` dry_run 默认 False + modal SDK 本地已装 → 完整 plan 忘传 dry_run 即触发付费调用; 改 dry_run 默认 **True** (fail-safe, 显式 dry_run=False 才 spawn) + modal>=1.5.0 入 requirements (此前装了未声明) + 防回退测试。(3) **§4 unknown 当 0 偷偷参与**: `northbound_alpha` stale 返回 0.0 被 compose 当合格类稀释 composite/虚增 n_classes_eligible; 改返回 score=NaN 让 compose 完全排除该类, compose 级防回退测试断言 n_eligible 不含 + composite 不稀释。受影响 79 passed。
- **2026-06-11 体检 confirmed 系统性修复 (修复工厂 7 组并行+对抗验证)**: (1) **PIT-live-fallback CRITICAL**: `build_daily_position_recommendations.py` 候选 SQL 删除 legacy in-sample 表 `mart_per_stock_strategy_optimal` (24442 keys 0 oos_*, 22741 只在 legacy = 全程 in-sample 假数据驱动 live T+1 仓位), scoring 只读 oos_*, `oos_sharpe IS NOT NULL` 守门, PIT 无覆盖即 score=unknown。(2) **PIT-fitness-snapshot**: `rebuild_stage_formula_fitness.py` + `build_stock_formula_buy_signal_daily.py` 把 `snapshot_date=MAX(...)` latest-snapshot 改 as-of (`snapshot_date<=决策日`), 历史日不注入未来画像。(3) **hsgt-stale-guard**: `northbound_alpha.py`+`build_institution_score_daily.py` 对 `fact_hsgt_daily` (停 2024-08) 加 staleness guard (阈值走 `institution_alpha.yaml`), 超期因子置 unknown 不静默用 2 年前数据。(4) **security**: `prediction_outcome.py` model_id SQL 注入改参数化+白名单; `main.py`+`start.command` 默认绑 127.0.0.1 (env 可切 0.0.0.0)+CORS 收敛 loopback。(5) **daily-update-wiring**: `daily_update.sh` 补三件套增量 rebuild。(6) **champion+plan_validator**: `formula_local_optuna_batch.py` 修 plan gate no-op (enforce_optuna_plan 缩进 bug 致零调用); `champion_registry.yaml`+reader 单一真相源; `registry.py` 成本读 paper_sim_config 收敛双源 (印花税 10→5bps); 收尾: `slippage.py` TradingCostConfig 改 **default-free** (旧默认 stamp=10bps 是减半前失真值, 裸构造=第二真相源已禁), 防回退测试断言裸构造 TypeError + yaml 派生 round_trip=27.282bps (与 cost_after/v5 panel 日志 0.002728 交叉一致)。(7) **modal-adapter**: `services/compute/modal_adapter.py` 复用 local plan gate, `experiment_jobs.yaml` modal planned→active ($30/mo, ~/.modal.toml), test_experiment_jobs 3 断言同步新现实。每组带防回退测试, 受影响 suite 实测 140 passed。
- **2026-06-11 行业分类区分度 measured 决策**: 申万 vs 通达信 ANOVA eta² (40 月度截面, forward 20d, shuffle baseline 扣类目数效应): 申万L2 净区分度 0.137 (稳定性 2.04) > 通达信L2 0.118 > 申万L1 0.110 > 通达信L3 0.068 > 通达信L1 0.070。结论: 申万L2 为主行业口径 (区分度+稳定双第一; L2>L1 甜点; 通达信L3 过细=过拟合)。当前快照验证 (两口径同偏差, 相对排序可靠); 生产需从 TuShare index_member_all 拉历史成分 PIT 化 (申万退役真因=只有快照无历史)。证据 analysis/industry_discrimination_*.json。
- **2026-06-11 TuShare vendor-gateway 接入 + need_027 gate PASS**: `.venv` + tushare 1.4.29; adapter `_pro_api()` 支持 `TUSHARE_HTTP_URL` 代理网关 (token/URL 只在 .env, 不进 git); probe `TUSHARE_TOKEN_ENV_VARS` 改 import adapter 真相源; `_source_preflight` find_spec 加防护; 3 个防回退测试; live gate PASS (tushare 3/3, selected_source=tushare); 代理有间歇空响应, writer 必须 0 行当失败重试; 剩余 production gates: pit_key/freshness_sla/writer/watermark/failure_queue。
- **2026-06-11 v5 panel PIT 防线显式化 (CRITICAL)**: v3 panel 表加宽后 (含 inst_path_a latest-snapshot 泄漏列, v3 DDL 自注 "latest, NOT PIT"), v5 的 `v3.*` 透传通道失去"v5 表无此列"的被动阻挡; `feature_join_v5.py` 现已显式 `v3.* EXCLUDE (inst_quality_wavg/max, inst_total_holding_ratio, inst_holder_cnt, top_inst_holding_ratio)` 兑现注释承诺, 防回退测试断言 5 列必须在 EXCLUDE 清单。v5 表误加的 5 列已 DROP (0 值写入)。遗留: v3 表自身泄漏列的物理清理在 implementation_plan P2。
- **2026-06-11 调度层 cron→launchd 迁移 + 失败告警**: cron 无 FDA 静默失败 (K线断流 4+ 交易日); python3.13 (有 FDA) 做 plist 入口 spawn bash 全链通; `scripts/launchd_job_wrapper.py` 失败写 ALERT flag + macOS 通知; crontab 两条退役。防回退: plist 入口不许改回 bash; 定时任务必须走 wrapper。

## 30 秒速览 — 这是什么项目

**Chunky Monkey v2** = A 股**自动选股 + 实盘模拟**系统. 用户(私人投资者)用它筛 5 只股票 / 月度轮换.

**用户目标 (硬指标, 一切优先级以此为锚)**:
- 年化 ≥ **+30%**
- max_drawdown ≥ **-20%**
- 超额 vs HS300 > 0

**数据基础**: 6,618 股 A 股 K 线 (2022-01 起) + 70K+ 财报 + 35K 机构事件 + 53K 龙虎榜 + 68K 高管增减持 + 大盘 regime + 4 阶段技术形态分类.

**架构主线 (alpha pipeline)**:
```
原始数据 → 公式信号 + PIT 因子 → Optuna 调参 (walk-forward) → mart 表
       → paper_sim selector (按 ensemble score 排名)
       → simulate_trade (T+1 入场, 含 tx_cost + 涨跌停)
       → NAV 曲线 → KPI 验证 (6 类 20+ 指标)
```

**当前最强发现** (实测严格 walk-forward OOS, 7.5h 跑批; 2026-06-11 E0 双口径收敛:
本区历史数字与 §10 实表矛盾, **统一以 §10 表为唯一口径**, 三评委一致点名该矛盾已污染下游引用):
- `reversal_1m_deep × stage=1`: avg OOS sharpe **+0.392** / win **58.1%** (§10 表, 地基旗舰)
- `reversal_1m_mild × stage=1.5`: avg OOS sharpe **+0.342** / win **51.9%** (§10 表, 次选)
- 整体 momentum 公式 (MACD/turtle/dynamic_ma) **全失效** (OOS sharpe ~0 或负)

**距离用户目标**: 单股 OOS sharpe 0.32 → 5 股组合 + 月度轮换 paper_sim 真实期望约 **+15-25% 年化** (推算未实测). 缺 **+5-15pp** 才达 +30% 标准.

**下一步**: 引入更多 alpha 源 (机构跟随主 alpha PIT 重建 / case-based 历史相似 / 板块强度) — 见 §11 "16 项遗漏审计".

## 维护责任 (Rule 9.5 沉淀)

**每次完成一个 phase / commit / 数据 backfill 后, 都要更新本文档**. 具体 checkpoints:
- 新加数据表 → 加进 §2 (数据资产)
- 新加 service 模块 / script 入口 → 加进 §3-4
- 新加 yaml config → 加进 §6
- 解决了已知坑 → §8 标 [PASS] + 短说明
- 跑出新 OOS 数据 → 加进 §10
- 踩了新坑 → §11 + CLAUDE.md Rule 9
- 加 §14 增量日志 (本 session 做了啥)

不维护 = 下次 session 又要重新摸索 = 用户最大抱怨

---

## 0. 用户终极目标 (锚)

> "短期内资产最大幅度增值不缩水"

3 个 PASS 标准:
1. 年化 ≥ +30%
2. max_dd ≥ -20%
3. 超额 vs HS300 > 0

基线: 2023-01-03 起, 100 万初始, HS300 benchmark.

---

## Pipeline 数据流图 (端到端架构)

```
┌──────────────────────────────────────────────────────────────────────┐
│ 0. 原始数据层 (data sources)                                         │
│   - akshare (K 线 / 财报 / 龙虎榜)  - tdxhub (qfq 复权 K 线)         │
│   - aif10 (估值 / 一致预期)         - tdx F10 (机构持仓)             │
│   - 内部模拟器 (event_simulator)                                     │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 1. raw_ 层 (smartmoney.duckdb): 70K 财报 / 53K 龙虎榜 / 35K 机构事件  │
│    market.duckdb: 6M K 线 / 158K xdxr 事件                           │
└──────────────────────────────────────────────────────────────────────┘
        │ sync (POST /api/inst/update/smart) — 含 watermark
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 2. fact_ 层 (PIT 时序事实表):                                        │
│    - fact_stock_technical_stage (2.4M, Stan Weinstein 4 stage)       │
│    - fact_signal_context (3.3M, vol_r20/price_pos/drawdown_60d/stage)│
│    - fact_technical_trigger (公式信号触发, 含 strength)              │
│    - fact_risk_factors (4.8M, Phase ψ.β.1 PIT mom/sharpe/vol)        │
│    - fact_financial_pit_daily (3.7M, Phase ψ.β.2 PE/PB/ROE/yoy)      │
│    - fact_capital_flow_pit_daily (858K, Phase ψ.β.3 lhb/exec/holder) │
│    - fact_regime_state (775, 大盘 bull/bear/sideways)                │
└──────────────────────────────────────────────────────────────────────┘
        │ Optuna 调参 (R1 walk-forward, expanding_monthly / train_end_forward)
        │ governance 守门 (sharpe>5/win>0.95/avg>0.5 reject)
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 3. mart_ 业务层 (调参 / 寻优结果):                                   │
│    - mart_per_formula_stage_optimal (426 OOS 行,                     │
│         per formula × stage × train_end_date, 最强 setup ↓)          │
│    - mart_per_stock_stage_strategy_optimal (per-stock × stage 旧表)  │
│    - mart_formula_horizon_evidence (per formula × hp 全市场)         │
│    - mart_stock_trend (主 alpha 88 列, 但 ⚠ latest 快照无 PIT)       │
│    - fact_optuna_governance_log (reject 审计)                        │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 4. paper_sim selector (3 mode):                                      │
│    - "backtest" 单公式排名 (按 mart_per_formula_stage.oos_sharpe)    │
│    - "ensemble" 10 alpha zscore 加权 + regime gate (Phase ψ.β.4)     │
│    - "production" 走 mart_daily_position_recommendation (实盘)        │
│    选 top 5 + 流动性过滤 (vol_60d ≤ 40% / amount_20d ≥ 5000万)       │
└──────────────────────────────────────────────────────────────────────┘
        │ T+1 VWAP 入场
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 5. simulate_trade (services/backtest/realistic_engine.py):           │
│    - T+1 入场 (buy_offset=1, 一字涨停延迟 1 次)                      │
│    - 5 出场触发: stop_loss > target_arm > trailing > hp_expired      │
│         > stage_deterioration                                        │
│    - 含 tx_cost (佣金 0.025% + 印花税 0.05% + 滑点 0.1%)              │
│    - 含涨跌停 reject_buy (一字涨停不买) / 退市暂停过滤                │
└──────────────────────────────────────────────────────────────────────┘
        │ 每日 NAV 更新, swap 决策, 跨日 trailing arm
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 6. paper_sim 输出 + KPI:                                             │
│    - fact_paper_sim_nav (NAV 时序)                                   │
│    - fact_paper_sim_position (持仓快照)                              │
│    - fact_paper_sim_trade (BUY/SELL/SWAP_OUT/SWAP_IN)                │
│    - mart_paper_sim_kpi (6 类 KPI: A 用户标准 / B anti-churn         │
│         / C robustness / D ablation / E sensitivity / F reality)     │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼ 决策: 6 类 KPI 全过 → 上线 / 一类不过 → 不上线
┌──────────────────────────────────────────────────────────────────────┐
│ 7. 实盘上线 (待 — 还没满足用户 +30%/-20%/超额 HS300)                  │
└──────────────────────────────────────────────────────────────────────┘
```

## 1. 三个 DuckDB 数据库

| DB | 路径 | 用途 |
|---|---|---|
| `smartmoney.duckdb` | `data/smartmoney.duckdb` | 业务主库 (mart_* / fact_* / raw_* / dim_*) |
| `market.duckdb` | `data/market.duckdb` | K 线 + 行情 (`v_price_kline_qfq`) |
| `etf.duckdb` | `data/etf.duckdb` | ETF 专用 |

**约束** (AGENTS.md / engineering governance DuckDB 段):
- 永远走 `services.duck_adapter.connect` / `services.db.get_conn`
- 单写锁, 一次 ATTACH, 不要直接 `duckdb.connect()`
- raw `duckdb.connect` 允许清单现在 config-owned (`backend/config/duckdb_connect_policy.yaml`) 用于跟踪历史 call sites；新增生产 raw connect line 由 `backend/scripts/check_rule_compliance.py` 默认阻断，确需例外必须有同行/上一行 evidence 注释并进入 review。
- 新增 `data/*.duckdb` / `.duckdb` 文件名字面量默认阻断；DB 路径应进入 `backend/config/database_manifest.yaml` 或专属 config。

---

## 2. 数据资产 — 6 大维度 (完整盘点)

> ⚠ Claude 容易误以为"项目主要数据是 K 线". 错. 6 大维度全有.

### 2.1 大盘 / 指数

| 表 / 字段 | 数据量 | freshness | 用途 |
|---|---|---|---|
| `v_price_kline_qfq` (market.duckdb) 含指数 K 线 | 5.97M 行 / 6,618 股 / 2022-01 → 2026-05 | 实时 | 指数代码: `000300` 沪深300 / `000905` 中证500 / `000852` 中证1000 / `000016` 上证50 |
| `fact_regime_state` | 775 行 / 2023-02 → 2026-04 [PASS] | 历史可用 | trade_date / regime_id / regime_label (bull/bear/sideways) / regime_prob_json / transition_signal |
| `dim_market_segment` | dim 表 | 静态 | 市场分段 |

### 2.2 行业 / 板块

| 表 | 数据量 | freshness | 用途 |
|---|---|---|---|
| `dim_stock_sw_industry` | dim | 静态 | 申万行业映射 |
| `dim_stock_tdx_industry_history` | dim history | PIT | 通达信行业 PIT 映射 |
| `fact_stock_industry_context` | 个股行业上下文 | 取决于跑批 | 衔接 sector_momentum 到个股 |
| **`mart_sector_momentum`** | **⚠ 只 41 行 / 2026-04-17 → 2026-05-13** | ⚠ **没历史, 不能历史回测** | sector_name/code/level, ma20/60, macd, momentum_score, return_1m/3m/6m/12m, excess_1m |
| `mart_industry_pit_quality` | ? | PIT | 行业质量 |
| `mart_stock_industry_pit` | ? | PIT | 个股行业 PIT 评分 |
| `mart_institution_industry_stat` | ? | — | 机构 × 行业统计 |
| `research_inst_industry_performance` | 6,564 行 | — | 机构 × 行业 win_rate_10d/30d/60d/120d, avg_gain_10d/30d/60d/120d |

### 2.3 机构跟随 (项目主 alpha, **权重 0.40**)

| 表 | 内容 |
|---|---|
| **`mart_stock_trend` (主 alpha, 88 列)** | inst_count_t0/t1/t2 / inst_cap_t0/t1/t2 / inst_trend / cap_trend / latest_events / external_attention_signal / **stock_gate** / turtle_setup_state |
| `fact_institution_follow_backtest` | cohort × params Grid 回测 (**已 train/holdout 切分** — split='train'/'holdout', cohort_scheme='institution_L2_pit_20240930') |
| `fact_institution_event` / `fact_jgdy_event` | 机构调研事件 |
| `mart_institution_industry_stat` | 行业级机构统计 |

### 2.4 基本面 / 质量

| 表 | 内容 |
|---|---|
| **`fact_stock_archetype` (22K 行 / 53 列)** | snapshot_date / **net_profit_positive_8q** / **operating_cashflow_positive_8q** / revenue_yoy_positive_4q / profit_yoy_positive_4q / eps_yoy_positive_4q / **high_quality_hits** / growth_hits / cycle_flags |
| `fact_financial_derived` / `fact_fundamental_quarterly` | 财务衍生 / 季度 |
| `fact_stock_fundamental_stage_daily` | 基本面阶段 daily |
| `fact_stock_quality_features` | 质量特征 |
| `raw_aif10_financial_history` / `raw_gpcw_detail` / `raw_tdx_gpcw_wide` | 财务原始 |
| `raw_aif10_valuation_quantile.percentile_fifty` | 估值 10Y 分位 (strategy_ensemble 在用) |
| `raw_aif10_forecast_consensus.compre_rating_num` | 一致预期评分 (strategy_ensemble 在用) |
| `raw_aif10_peer_valuation` | 同业估值 |

### 2.5 资金流 / 事件

| 表 | 内容 |
|---|---|
| `fact_hsgt_daily` | 北向资金 daily |
| `raw_lhb_daily` / `fact_lhb_event` | 龙虎榜 |
| `raw_fund_flow_daily` | 主力资金流 daily |
| `fact_executive_trade_event` | 高管增减持 |
| `fact_shareholder_trade` / `fact_shareholder_trade_tdx_b` | 股东交易 |
| `fact_holder_event` / `fact_top10_holder_period` / `fact_holder_count_period` | 持股人结构 |
| `fact_dzjy_event` | 大宗交易 |
| `raw_capital_*` (allotment/dividend/repurchase/unlock) | 配股/分红/回购/解禁 |
| `raw_institution_surveys` | 机构调研 raw |
| `raw_qfii_holding_quarterly` | QFII 季度持仓 |

### 2.6 技术 / 形态 / 信号

| 表 | 内容 |
|---|---|
| **`fact_signal_context`** | stock × date / vol_r20 / amt_r20 / amount_20d_avg / price_pos_60d / price_pos_120d / drawdown_60d / **technical_stage** (1/1.5/2/3/4) / built_at |
| **`fact_stock_technical_stage`** | Stan Weinstein 4 stage (1=底部 / 1.5=突破中 / 2=上升 / 3=顶部 / 4=下跌) |
| `fact_stock_stage_features` | 阶段特征 |
| `fact_stock_turtle_features` | 海龟特征 |
| **`fact_technical_trigger`** | 公式信号触发 (stock × date × formula_id × variant × strength × state × reason_codes_json) |
| `fact_stock_archetype` (53 列) | 形态原型 (跟基本面共用此表) |
| `fact_setup_snapshot` | ⚠ **0 行 / 未启用** |

### 2.7 Phase ψ 治理 / 调参产物

| 表 | 用途 |
|---|---|
| **`mart_per_stock_stage_strategy_optimal`** | per-stock × variant × stage Optuna 寻优 (Phase ψ R1 后含 OOS 列, 但稀疏信号下大量 governance reject) |
| **`mart_per_formula_stage_optimal`** (Phase ψ.α B) | per-formula × stage × train_end_date 严格 walk-forward 寻优 (反转因子用此表) |
| `mart_formula_horizon_evidence` | per (formula × hp) 全市场合并真实历史涨跌 (无 Optuna 调参, 最干净) |
| `mart_stage_formula_fitness` | cohort fitness (fund × tech × formula × hp) |
| `mart_stock_formula_optuna_v2` | 旧 per-stock × formula × hp 全宇宙 (337K 行) |
| `fact_optuna_governance_log` | Phase ψ governance reject 审计 |
| `mart_market_perception_daily` | Market Perception P1 daily snapshot: regime_score / breadth_state / volatility_state / sentiment_phase, PIT cutoff and built_at |

---

## 3. Service 模块 (231 个 .py 文件, 21 个子包)

### 3.1 调参 / 寻优 (Phase ψ)

| 模块 | 文件 | 作用 |
|---|---|---|
| `services/optimization/` | config.py | yaml loader (governance/walk_forward/search_space/composite/constraints/execution/output) |
| | governance.py | enforce_pre_optimize / enforce_pre_insert (50≤n_trials≤500, sharpe ≤ 5, win ≤ 0.95) |
| | walk_forward.py | split_dispatch (none/holdout/expanding/expanding_monthly/**train_end_forward**) + assert_no_temporal_leak + list_month_ends |
| | oos_aggregator.py | aggregate_oos_metrics (multi-window OOS trades 合并) |
| | composite.py | CompositeWeights.from_config() (7 个权重 ∑=1.0) |
| | constraints.py | HardConstraints (max_dd, streak, worst_loss, min_traded) |
| | objectives.py | 8 个 metric (sharpe/calmar/sortino/pain/ulcer/tail/stability/cvar) |
| | ddl.py | mart_per_stock_*_optimal / mart_per_formula_stage_optimal / fact_optuna_governance_log DDL |
| `services/backtest/` | optimize.py | optimize_stock_strategy (R1 expanding_monthly 主流程) |
| | realistic_engine.py | simulate_trade (T+1 入场, intraday stop/target, 含 tx_cost) |
| | search_space.py | 5 维 SearchSpace.from_config() (hp/stop/target/trailing/buy_offset) |
| | objective.py | make_objective Optuna 目标函数工厂 |
| | filters.py | is_index_code 等 |

### 3.2 公式 (formula_engine, 4+3 = 7 公式)

| 公式 | 文件 | 类型 |
|---|---|---|
| macd_golden_cross | macd_golden_cross.py | 动量 (DIF 上穿 DEA, variant=above/below_zero, **裸金叉无量能**) |
| turtle_breakout_20/55 | turtle_breakout.py | 动量 (突破 + **量能 > MA20 × 1.3**) |
| dynamic_ma_iterative_cross | dynamic_ma_iterative.py | 动量 (用户 MQL, 4 均线 + 加权重心 + **1 轮迭代过滤假突破**) |
| **reversal_1m_mild** (Phase ψ.α) | reversal_short_term.py | **反转** (20 日跌 4-15% + 60 日低波 + 量比正常) |
| **reversal_1m_deep** (Phase ψ.α) | reversal_short_term.py | **反转** (20 日跌 10-30%) — **主 alpha (sharpe 1.1 horizon / 0.39 walk-forward)** |
| **reversal_1w** (Phase ψ.α) | reversal_short_term.py | **反转** (5 日跌 2-10%) |
| technical_stage (4 stage) | technical_stage.py | classify_technical_stage(closes, volumes) — Stan Weinstein |

### 3.3 多 Alpha Ensemble (strategy_ensemble.py)

**5 alpha 源 + 加权综合** (paper_sim 目前**没用**, 这是设计意图):

| Alpha | weight | 数据源 | 类别 |
|---|---|---|---|
| **institution_follow** | **0.40** | `mart_stock_trend.action_score` | 资金流 (主 alpha) |
| valuation_pct_low | 0.20 | `raw_aif10_valuation_quantile.percentile_fifty` | 基本面价值 |
| forecast_consensus | 0.15 | `raw_aif10_forecast_consensus.compre_rating_num` | sell-side analyst |
| momentum_120d | 0.10 | `fact_risk_factors.mom_120d` | 技术 |
| risk_adjusted_sharpe | 0.15 | `fact_risk_factors.sharpe_60d` | 风险调整 |

### 3.4 Paper Sim v2 (Phase ψ)

| 模块 | 作用 |
|---|---|
| `services/paper_sim/config.py` | yaml loader (portfolio / selection / exit / swap / tx_cost / risk / validation / data) |
| | selector.py | backtest mode 查 mart_per_formula_stage_optimal (Phase ψ.α B), 0 selection leakage; **Phase ψ.β.5 L2**: ensemble mode 可按 vol_60d 缩放 stop/target/trailing per-stock (`_vol_aware_params`, config flag `selection.vol_aware.enabled`); **Phase ψ.γ.2 L3**: ensemble mode 可 JOIN mart_per_stock_stage_strategy_optimal (24K 行 9-dim OOS) 用 per-stock × stage params 覆盖 default (`_load_per_stock_stage_optimal`, config flag `selection.per_stock_stage.enabled`). 优先级: per_stock_stage > vol_aware > default_holding. |
| | driver.py | walk-forward 主循环 + VWAP 成交 + swap 决策 |
| | exit_rules.py | 5 触发优先级 (stop > target_arm > trailing > hp_expired > stage_deterioration) |
| | swap_rules.py | compute_fulfillment / candidate_can_close_gap / evaluate_swap |
| | sizer.py | wilson_kelly position sizing |
| | tx_cost.py | 佣金 + 印花税 + 滑点 |
| | reporter.py | 6 类 KPI (A 用户标准 / B anti-churn / C robustness / D ablation / E sensitivity / F reality_check) |
| | ddl.py | 4 张 paper_sim 专表 (nav / position / trade / kpi) |

### 3.5 候选 / 推荐 / 选股

| 模块 | 作用 |
|---|---|
| `services/buy_signal/` | classify_tier + factor_aggregator + scoring + reasoning + configs + ddl — **6 因子综合 score, 输出 mart_stock_formula_buy_signal_daily** |
| `services/selection/` | logger / outcome / feedback / summary — 选股事件追踪 |
| `services/portfolio_walk_forward/` | metrics.py (CAGR / sharpe / max_dd / calmar / monthly_win_rate), liquidity, ... |
| `services/portfolio_sizer/` | profiles.py 不同风格 sizing |
| `services/trade_plan/builder.py` | 交易计划生成 |
| `services/candle_pattern/` | features (6 维 + 1 突破强度) / evaluator / search_space (4 维 Optuna 阈值) |
| `services/market_perception/` | Market Perception P1: `compute_regime_for_date/range`, PIT-strict market context features written to `mart_market_perception_daily` |

### 3.6 机构 / 行业 / 阶段

| 模块 | 作用 |
|---|---|
| `services/institution_l2_metrics.py` | institution_l2_score_cte (train_best/holdout pair CTE) |
| `services/institution_read.py` / `institution_scoring_read.py` / `institution_write.py` | 机构数据 R/W |
| `services/industry_context_engine.py` | sector_momentum 衔接到个股 fact_stock_industry_context |
| `services/industry.py` / `industry_pit.py` / `industry_overview_read.py` | 行业 PIT + UI 读取 |
| `services/stock_stage_engine.py` | 阶段特征中间事实层 |
| `services/stock_turtle_engine.py` | 海龟形态特征 |

### 3.7 数据源 / 客户端 / sync

| 模块 | 作用 |
|---|---|
| `services/data_sources/` | base / clients_registry / data_routes / fallback / registry — 数据源中央 |
| `services/akshare_client.py` / `tdx_*_client.py` / `block_client.py` / `capital_client.py` / `lhb_client.py` / `xdxr_client.py` / etc. | 各种数据源 client |
| `services/kline_source.py` / `market_db.py` | K 线源 + market DB 入口 |
| `services/duck_adapter.py` / `db.py` / `db_health.py` | DuckDB 安全包装 |
| `services/source_watermarks.py` / `source_policy.py` | sync watermark + policy |

### 3.8 其他

- `services/sentiment/` — **情绪因子框架** (factor_registry + bin_assigner + window_calculator + survey_builder). 未集成到主选股
- `services/external_attention.py` — 关注度因子 (`external_attention_score` 已写入 mart_stock_trend)
- `services/event_simulator.py` / `event_engine.py` — 事件模拟引擎 (用于机构跟随 backtest)
- `services/shareholder_plan_*` (3 文件) — 股东计划相关 alpha
- `services/feature_registry.py` / `feature_labels.py` / `feature_retention.py` — 特征工程
- `services/data_lineage/` — 数据血缘
- `services/ml_lifecycle/` — drift / registry
- `services/etf_*` — ETF 子系统 (独立, 不影响个股 alpha)
- `services/trading_config/` — 真实执行模型 (buy_pricing / sell_pricing / slippage / filters / execution_model)

---

## 4. Scripts 入口 (135 个)

> 机器枚举的完整入口/产表/依赖清单 → `FEATURE_MAP.md` (`scripts/chunkyctl map` 重生成,
> 勿手改)。本节只保留人工策展 (哪些重要/怎么用/坑在哪), 计数以 FEATURE_MAP 为准。

按主题分组:

| 主题 | 数量 | 例子 |
|---|---|---|
| `build_*` | 49 | build_formula_signals_history, build_signal_context, build_stock_formula_buy_signal_daily, build_daily_position_recommendations, build_picture_daily, build_stage_formula_fitness, build_architecture_inventory |
| `formula_*` | 1 | **formula_limit_up_pullback.py** (涨停回调十字星选股, S/A/B 三档, YAML 配置 `config/formula_limit_up_pullback.yaml`) |
| `run_*` | 17 | run_paper_sim_v2 (我们主用), run_follow_backtest (机构跟随), run_optuna_*, run_portfolio_mvp |
| `validate_*` | 10 | validate_exclusion_rules 等 |
| `audit_*` | 5 | **audit_end_to_end.py** (23 项检查) |
| `backfill_*` | 5 | 各种回填 |
| `optimize_*` | 4 | **optimize_per_stock_stage_strategy.py** (Phase ψ R1), **optimize_per_formula_stage.py** (Phase ψ.α B), **optimize_ensemble_full.py** (Phase ψ.γ.1, **20 维 ensemble Optuna**: 13 alpha weights + 2 regime + 3 sigma + hp + max_vol, constrained sharpe, holdout train/test, mart_ensemble_optimal 入库) |
| `rebuild_*` | 2 | rebuild_stage_formula_fitness |
| `replay_*` | 2 | replay_paper_history_signflip |
| `evaluate_*` / `train_*` | 4+2 | 各种评估 + 训练 |
| `cron_*` | — | cron_daily.py (HTTP wrapper for sync) |

### 4.1 主流水线 (顺序严格)

```
1. optimize_per_stock_stage_strategy.py    Optuna 9-dim per (stock × variant × stage)  ~16 min
   或 optimize_per_formula_stage.py        Phase ψ.α B 全局 walk-forward          ~28 min
2. rebuild_stage_formula_fitness.py        fitness 聚合                          ~1s
3. build_stock_formula_buy_signal_daily    buy_signal × technical_trigger        快
4. build_daily_position_recommendations    最终推荐 + 价格                       快
5. audit_end_to_end.py                     23 项检查 (0 FAIL 才算通过)           ~1 min
6. portfolio_backtest.py / run_paper_sim_v2.py   walk-forward NAV + KPI         30 min
```

---

## 5. Routers / API (17 个)

| Router | 主功能 |
|---|---|
| `routers/recommendation.py` | 选股推荐 API |
| `routers/screening.py` | 筛选 |
| `routers/signals.py` | 信号 |
| `routers/institution.py` | 机构数据 |
| `routers/market.py` | 行情 |
| `routers/etf.py` | ETF |
| `routers/updater.py` | sync 入口 (POST /api/inst/update/smart) |
| `routers/workbench.py` | 工作台 |
| `routers/strategy_preset.py` | 策略预设 |
| `routers/v3_*` | v3 系列 (meta / paper / picture / portfolio_builder / selection / views) |

---

## 6. Config 文件 (yaml)

| 文件 | 控制什么 |
|---|---|
| `backend/config/optuna_config.yaml` | Optuna 治理 (Phase ψ Rule 7/8) — governance/walk_forward/search_space/composite/constraints/execution/output |
| `backend/config/paper_sim_config.yaml` | Paper Sim v2 hyperparam |
| `backend/config/paper_sim_momentum.yaml` / `paper_sim_reversal.yaml` / `paper_sim_reversal_deep_only.yaml` | Phase ψ.α ablation 切换 |
| `backend/config/paper_sim_ensemble.yaml` | **Phase ψ.β.4** ensemble 模式 (13 alpha + regime + vol_aware + per_stock_stage) |
| `backend/config/field_dictionary.yaml` | **Phase ψ.γ.dict.1** 字段字典 (3 DB × 12 核心表 × 100+ 字段 + 单位 + PIT key + outlier cap + JOIN 模板) — 防 VWAP unit bug 类故障 |
| `backend/config/recommendation_universe.yaml` | 选股宇宙 |
| `backend/config/pipeline_performance_policy.yaml` | step budget 预算 |
| `backend/config/data_sources.yaml` | 数据源 |
| `backend/config/storage_retention.yaml` | 保留期 |
| `backend/config/pricing_label_policy.yaml` | 定价标签 |
| `backend/config/feature_registry.yaml` | 特征注册 |
| `backend/config/model_search.yaml` | 模型搜索 |
| `backend/config/tdx_data_need_coverage.yaml` | TDX 数据需求/source priority/迁移建议 catalog，供 `audit_tdx_data_need_coverage.py` 物化到治理表 |

---

## 常用命令 cheatsheet (复制即可跑)

### 安装 (新人首次)
```bash
git clone https://github.com/dare2live/chunkymonkey.git
cd chunkymonkey
python3 -m venv venv && source venv/bin/activate
pip install -r backend/requirements.txt
pip install pre-commit && pre-commit install   # 强制 PROJECT_INDEX 同步检查
```

### 数据 backfill (从空开始)
```bash
# 1. 技术阶段 (Stan Weinstein 4 stage)
PYTHONPATH=backend python backend/scripts/build_stage_formula_fitness.py --start 2022-09-01

# 2. signal_context (vol/amt/price_pos + technical_stage)
PYTHONPATH=backend python backend/scripts/build_signal_context.py --start 2023-09-01

# 3. 公式信号历史 (含反转 3 公式)
PYTHONPATH=backend python backend/scripts/build_formula_signals_history.py

# 4. PIT 因子 (Phase ψ.β.1/2/3)
PYTHONPATH=backend python backend/scripts/backfill_risk_factors_history.py
PYTHONPATH=backend python backend/scripts/backfill_financial_pit.py
PYTHONPATH=backend python backend/scripts/backfill_capital_flow_pit.py
```

### Optuna 跑批
```bash
# per-formula × stage 全局 walk-forward (推荐)
PYTHONPATH=backend python backend/scripts/optimize_per_formula_stage.py \
    --formula reversal_1m_mild reversal_1m_deep reversal_1w \
              macd_golden_cross turtle_breakout_20 turtle_breakout_55 \
              dynamic_ma_iterative_cross
# 时长: ~7.5h (1260 任务), 输出 mart_per_formula_stage_optimal 426 行
```

### paper_sim 跑批 (4 套 ablation)
```bash
# A. baseline (no swap, 老 momentum 公式)
PYTHONPATH=backend python backend/scripts/run_paper_sim_v2.py --variant baseline

# B. 反转单 alpha (最强 setup)
PYTHONPATH=backend python backend/scripts/run_paper_sim_v2.py \
    --config-path backend/config/paper_sim_reversal.yaml --ablation

# C. momentum 单 alpha
PYTHONPATH=backend python backend/scripts/run_paper_sim_v2.py \
    --config-path backend/config/paper_sim_momentum.yaml --ablation

# D. ensemble 10 alpha 综合 (主战)
PYTHONPATH=backend python backend/scripts/run_paper_sim_v2.py \
    --config-path backend/config/paper_sim_ensemble.yaml --ablation
# 时长: 各 ~30-60 min
```

### 数据查询 (常用诊断)
```bash
# 查 mart 表最强 setup
duckdb data/smartmoney.duckdb -c "
SELECT formula_id, stage_filter, COUNT(*) AS n,
       ROUND(AVG(oos_sharpe),3) AS avg_sh,
       ROUND(AVG(oos_win_rate)*100,1) AS win
  FROM mart_per_formula_stage_optimal
 GROUP BY 1, 2 ORDER BY avg_sh DESC LIMIT 10"

# 查 PIT 数据 freshness
duckdb data/smartmoney.duckdb -c "
SELECT 'risk_factors' AS t, MIN(calc_date), MAX(calc_date), COUNT(*) FROM fact_risk_factors
UNION SELECT 'financial', MIN(trade_date), MAX(trade_date), COUNT(*) FROM fact_financial_pit_daily
UNION SELECT 'capital_flow', MIN(trade_date), MAX(trade_date), COUNT(*) FROM fact_capital_flow_pit_daily
UNION SELECT 'signal_context', MIN(date), MAX(date), COUNT(*) FROM fact_signal_context"
```

### 测试 / 验证
```bash
# 全部单测 (paper_sim + optuna + backtest + ...)
cd backend && PYTHONPATH=. pytest tests/ -q

# 仅 Optuna 治理测试
cd backend && PYTHONPATH=. pytest tests/optimization -q   # 83 tests

# 跑 audit (23 项检查)
PYTHONPATH=backend python backend/scripts/audit_end_to_end.py
```

### Pre-commit 测试 (避免 hook reject)
```bash
# 改完代码后 staged
git add backend/services/your_file.py

# 测 hook (会告诉你需不需要改 PROJECT_INDEX)
python3 backend/scripts/check_project_index_sync.py; echo "exit=$?"

# 如果 exit=1 → 改 PROJECT_INDEX.md 加进 §14, 然后 git add PROJECT_INDEX.md
# 如果 exit=0 → 可以 commit
```

## 7. CLAUDE.md 规则栈 (现 9 条)

```
Rule 1: Think Before Coding         — 列假设, 不确定就问, push back
Rule 2: Simplicity First            — 最少代码, 不 speculative
Rule 3: Surgical Changes            — 只改必须改的
Rule 4: Goal-Driven Execution       — 定义成功, 循环验证
Rule 5: Root Cause Over Patches     — 不打补丁, 找根因
Rule 6: Measured, Not Estimated     — 不估算, 必须实测
Rule 7: Anti-Look-Ahead / Leakage   — 普适, 时间维度诚实
Rule 8: Optuna 治理                 — Rule 7 在调参层落地, config-driven
Rule 9: 真金白银 / 第一性原理       — 用户视角严苛门槛
```

---

## 8. 已知坑 / 未启用 / 需要修

| 项 | 状态 |
|---|---|
| **vendor rank 字段 = 分页伪 rank** | [陷阱-永久] `moneyflow_ind_dc.rank` 是每 50 行循环的分页序号 (三评委独立复现 vs 自算全量 rank spearman 仅 0.07-0.084)。**一切 vendor rank/序号类字段必须自算全量截面 rank**, 禁止直接当因子 (E9 纪律件, 2026-06-11) |
| `mart_sector_momentum` 只 41 行 (2026-04 起) | [BLOCKED] 没历史回测能力, **需 rebuild 全期** |
| `fact_setup_snapshot` 0 行 | [BLOCKED] 未启用 |
| **paper_sim 选股 走 strategy_ensemble** | [PASS] Phase ψ.β.4: ensemble mode + `paper_sim_ensemble.yaml` 10 alpha |
| **5 alpha 主源数据 PIT 时序** | [PASS] β.1 fact_risk_factors / β.2 fact_financial_pit_daily / β.3 fact_capital_flow_pit_daily backfill 完成 (跨 2023-01 → 2026-05) |
| **fact_institution_event 主 alpha** | ⚠ 只 1 年 (2025-04 起), 无法做 800 天 backfill — β.3 改用 lhb+exec+holder 替代 |
| **mart_stock_trend.action_score (机构跟随主 alpha)** | [BLOCKED] 仍是 latest 快照 — 未做 PIT 重建 (依赖 fact_institution_event 1 年限制) |
| **aif10 估值/一致预期** | [BLOCKED] 全 latest 快照, 无 PIT, β.2 改用 fact_financial_derived 替代 |
| **case-based / k-NN 历史相似回测** | [BLOCKED] 未建. 数据基础已有 (fact_signal_context + archetype) |
| **`fact_regime_state` 在 paper_sim** | [PASS] Phase ψ.β.4: ensemble selector regime_gate (bear 0.3x / sideways 0.7x / bull 1.0x) |
| sentiment/ 包未集成 | ⚠ 8 文件框架, 未对接 |
| 大盘指数 K 线 在 paper_sim 当 benchmark | [PASS] 已用作 excess vs HS300 |
| **fact_signal_context 早期数据缺** | [PASS] Phase ψ.β.4.5 backfill 完成 (2024-03 起, 66% valid_stage) |
| **fact_stock_technical_stage 早期缺** | [PASS] Phase ψ.β.4.5 backfill 完成 (2023-09-12 起, 2.4M 行) |
| **mart_per_formula_stage_optimal train_end 范围** | ⏳ 正在重跑 (1260 任务, 5 worker, 含 7 公式 × stage × 35 train_end) |
| **Optuna 跑批 8h 慢** | [PASS] Phase ψ.β.perf 修 hotspot: _idx O(1) cache + backtest_signals_with_trades 避免重跑 simulate_trade. 重跑预估 3-4h |
| `fact_stock_archetype` (基本面质量) 只 2026-04 几天 | ⚠ 未 backfill 历史 (待后续 audit) |
| `fact_financial_derived.revenue_yoy` 对部分股 (如 000001) null | ⚠ derived 表本身 sparse, 不影响其他股 |

---

## 9. 关键术语速查

| 术语 | 含义 |
|---|---|
| **IS** | In-Sample, 调参用的数据 |
| **OOS** | Out-of-Sample, 调参后**没看过**的数据上的表现 (实盘只能 OOS) |
| **R1** | 严格 walk-forward — 用户指定标准 |
| **expanding_monthly** | R1 严格模式: 每月底切, 累积 train + 当月 OOS |
| **train_end_forward** | Phase ψ.α B: train < d, test = [d, d+forward_days], 写多行支持 paper_sim point-in-time 选 |
| **leakage** (selection) | t 时选股用了 t+ 才能算的指标 (例 mart.sharpe 全期合并) |
| **leakage** (look-ahead) | 特征用了未来 K 线 |
| **CAGR** | (final/initial)^(252/n_days) - 1 — 复利年化 (不是单笔 × N) |
| **technical_stage** | 1=底部 / 1.5=突破中 / 2=上升 / 3=顶部 / 4=下跌 (Stan Weinstein) |
| **mart_** | 业务表 (报表 / 聚合) |
| **fact_** | 事实表 (实际发生) |
| **raw_** | 原始数据源 |
| **dim_** | 维度表 (静态 / 缓变) |

---

## 10. 已实测数据点 (Phase ψ.α 跑出的诚实 OOS)

### 反转因子 (B 严格 walk-forward, 34 个月窗 avg):

| formula × stage | avg OOS sharpe | avg win | avg single ret | max sharpe |
|---|---|---|---|---|
| reversal_1w × stage=3 | +0.393 | 50.4% | +3.94% | +1.255 |
| reversal_1m_deep × stage=3 | +0.393 | 51.6% | +5.49% | +0.898 |
| **reversal_1m_deep × stage=1** (底部深跌反转) | **+0.392** | **58.1%** | **+5.22%** | +0.905 |
| reversal_1m_deep × stage=4 | +0.356 | 46.2% | +4.77% | +0.889 |
| reversal_1m_mild × stage=1.5 | +0.342 | 51.9% | +4.49% | +1.372 |
| ... 9 行 ... | | | | |
| reversal_1w × stage=1 | -0.171 | 34.9% | +2.61% | +0.612 |

### Momentum 公式 (per-stock × stage R1, sparser):
全 12 组合 OOS sharpe 全负 (-0.02 ~ -0.63), avg win ≈ 39% — **per-stock 粒度不适合**, 应该改 per-formula 全局重测.

### Horizon Evidence (无 Optuna, 最干净, per formula × hp):
- reversal_1m_deep × 20d: win 61.8% / sharpe **+1.10** (但**这是合并跨全期, 不是 forward OOS**)

---

## 11. 我 (Claude) 容易踩的坑 (Rule 9.5 沉淀)

| 坑 | 教训 |
|---|---|
| "项目主要数据是 K 线" | **全错**. 6 大数据维度都有. 下结论前先 grep 所有 fact_/mart_/raw_ 表 |
| "momentum 公式失效 → 项目无 alpha" | 错. 项目还有机构跟随 (0.40 主 alpha) + 估值 + 一致预期 + 情绪 + 行业 + 大盘 regime |
| "MACD 是裸的" | 错. 跑 Optuna 时叠加 4 维 K 线形态过滤, 不是裸金叉 |
| "上升趋势 (stage=2) 反转完全无效" | 错. 是**粗糙公式**判 stage=2 回调失败, stage=2 回调本身是合理买点, 需要更精细 |
| "估算 2 min 跑完" → 实际 28 min | Rule 9.5: 不实测就估算 = 失败. 估时间也要小样本先测 |
| **paper_sim selector 用 mart_per_stock_*_optimal sharpe 排名** | 这是 selection leakage. 修正: walk-forward selector (Phase ψ.α B 已修, 但只对 reversal). 整体业务应走 ensemble |
| "对话压缩后还在用旧 context" | 修正: 每次启动**先读这个文档 + CLAUDE.md** |

---

## 11.5 已知遗漏 / 待办清单 (按 ROI 优先级)

> 这是用户反复 push back 后系统 audit 的结果. 每项含: 用户期望 / 现状 / 优先级 / 估时.
> Claude 应该在每个 phase 结束自动 review 这个列表, 不让任何一项静默 drop.

### P0 — 必修 (影响主目标达成)

| # | 项 | 用户期望 | 现状 | 估时 |
|---|---|---|---|---|
| 1 | **数据 sync 同步** | 数据更新到最新交易日 | `mart_data_source_watermark` 停在 2026-05-06, 其他 2026-05-13. 没主动跑 sync | 1 h |
| 2 | **goal.md 维护** | Phase ψ.β 系列进度记录在 goal.md | goal.md 没动过 Phase ψ.β 内容 | 1 h |
| 4 | **mart_sector_momentum 历史 backfill** | 板块强度可历史回测 | 只 41 行 (2026-04 起), 板块 alpha 不可用 | 半天 |
| 11 | **swap 策略最终评估** | 反转 setup 下 swap 是否需要? | swap_v1 跑 -44% 后中断, 反转下没验证 | paper_sim ablation 一部分 |

### P1 — 高 ROI (alpha 增强)

| # | 项 | 用户期望 | 现状 | 估时 |
|---|---|---|---|---|
| 5 | **mart_stock_trend.action_score PIT 重建** | 机构跟随主 alpha (0.40 权重) 历史可用 | β.3 改方向用 lhb/exec/holder 替代; 主 action_score 还是 latest 快照 | 3-5 天 (受 fact_institution_event 只 1 年限制) |
| 6 | **case-based / k-NN 历史相似回测** | "结合历史相似形态胜率" 选股 | 列为 R-γ, 未开工. 数据基础 fact_signal_context + archetype 已有 | 1-2 周 |
| 10 | **大盘 regime gate paper_sim 验证** | regime 择时是否生效? | yaml 配置加了但 paper_sim 还没验证 (反转 ablation 没用 ensemble mode) | paper_sim ablation 一部分 |

### P2 — 中 ROI (alpha 拓展)

| # | 项 | 现状 | 估时 |
|---|---|---|---|
| 3 | fact_stock_archetype 历史 backfill | 只 2026-04 几天 | 半天 |
| 7 | sentiment/ 关注度 alpha 集成 | 8 文件框架, 未对接 | 1 天 |
| 8 | 量价相关因子 (vol-price correlation) | 调研提过, 未建 | 半天 |
| 9 | fact_financial_derived.revenue_yoy sparse | 部分股 null (如 000001 银行) | 修 derived 表本身, 半天 |

### P3 — 工程 / 审计

| # | 项 | 现状 | 估时 |
|---|---|---|---|
| 12 | swap_uplift_estimate vs 反事实验证 | Phase ψ Batch 4c todo | 半天 |
| 13 | qfq 复权 PIT leakage | "业界接受不修", 但 Rule 9.1 严格说要处理 | 1-2 天 |
| 14 | 行业分类 PIT 系统验证 | 没核 SQL 用 history 还是 latest | 半天 |
| 15 | codex 分支整理 | 保留作 backup (用户原话), 不删 | 0 |
| 16 | dev 手册 / goal.md / PROJECT_INDEX 职责划分 | 没明文, 内容可能冗余 | 半天 |
| 17 | **283 历史 Rule violations 渐进清理** (Phase ψ.γ.discipline 扫出) | Rule 5 silent except 138 / Rule 7 date 112 / stock 22 / Rule 6 alpha weight 6 (strategy_ensemble.py) / threshold 3 / sigma 1 / multiplier 1. 多数 Rule 5 可能合理 (best-effort cleanup), Rule 6 6 个是 strategy_ensemble.py 真违规需要 yaml-back. | 1-2 天 (按 rule 分批清理 + 误判加 evidence 注释) |

### 处理原则

- 每跑完一个 phase / commit 后, **检查这个列表是否有项可以划掉**
- 新踩坑 / 新 audit 发现的项加进来
- 不静默 drop — 即使 "暂不修" 也要写明理由
- P0 不修, 用户目标基本不可能达成

## Performance Profile (跑批时间预期)

| 任务 | 数据量 | 实测时长 | 备注 |
|---|---|---|---|
| build_signal_context backfill | 3.3M K 线 → 2.7M context | **5.7 min** | calc 1 min + 写库 4.7 min |
| build_stage_formula_fitness (含 technical_stage) | 5.2M K 线 → 2.4M stage | **4 min** | classify 22s + 写库 3.5 min |
| backfill_risk_factors_history | 5.5M K 线 → 4.8M risk PIT | **12 min** | SQL 窗口 8.6s + 写库 11.5 min |
| backfill_financial_pit | 70K 财报 + K 线 → 3.7M PIT | **10 min** | ASOF JOIN 4s + 写库 10 min |
| backfill_capital_flow_pit | 53K lhb + 68K exec + holder → 858K | **2.4 min** | SQL 3s + 写库 2 min |
| optimize_per_formula_stage (反转 3 公式) | 455 任务 × 100 trials | **28 min** | 8 workers |
| **optimize_per_formula_stage (全 7 公式)** | **1260 任务 × 100 trials** | **7.5 h** ⚠ | 后期 5 worker tail (用户问"卡了吗") |
| paper_sim_v2 walk-forward 单 variant 800 天 | 4-5K 候选 / 天 | 30 min | swap_v1 含 |
| paper_sim_v2 ablation (baseline + swap_v1) | 2 variants × 800 天 | 60 min | |

### 已修 hotspot (Phase ψ.β.perf, commit 192bcb4d)

| Hotspot | 修法 | 预期加速 |
|---|---|---|
| `realistic_engine._idx` linear search | 加 `_BAR_DATE_IDX_CACHE` dict cache | **2-5×** |
| `objective.py` + `optimize.py` 重跑 simulate_trade | 新增 `backtest_signals_with_trades` 返回 (summary, trades) | **1.5-2×** |
| `objective.py` 自己做 linear search | 改用 `_idx` (含 dict cache) | **1.2-1.5×** |

**预期重跑 1260 任务 Optuna 从 7.5h 降到 ~3h**.

### 已知尚未优化

| 项 | 影响 |
|---|---|
| `dynamic_ma_iterative` 公式原始 10 轮迭代 / 当前默认 2 轮 Python loop | 慢公式之一, 可 numpy 向量化 → 3-5× |
| backfill 写库阶段 (单事务 INSERT) | 平均 150 us/row, 4.8M 行 11 min. COPY FROM Parquet 可 5-10× |
| Optuna pool tail effect (5 worker idle / 2 worker 慢任务) | 改 chunksize 或调度策略, 拉平 worker 负载 |

## 12. 当前 Phase / 进度

| Phase | 内容 | 状态 |
|---|---|---|
| Phase β-η+++++++ | 前期工作 (公式 / Optuna / fitness / sizer / etc.) | 大量已完成, 见 goal.md |
| **Phase ψ** | Optuna 治理 + R1 + Rule 7/8 + paper_sim VWAP 修正 | [PASS] commit `34e83d75` (main + codex) |
| **Phase ψ.α** | 反转因子 + per-formula 全局 + B 严格 walk-forward + Rule 9 + PROJECT_INDEX | [PASS] commit `545cb3d9` (feature/reversal-factor) |
| **Phase ψ.β.1** | fact_risk_factors PIT backfill (4.8M 行 / 6,567 股 / 810 天) | [PASS] commit `5a3b5ea8` |
| **Phase ψ.β.2** | fact_financial_pit_daily PIT (3.69M 行) — PE/PB/ROE/yoy/inst_holding_pct | [PASS] commit `baf815b6` (β.2+β.3) |
| **Phase ψ.β.3** | fact_capital_flow_pit_daily (858K 行) — lhb/exec/holder PIT | [PASS] commit `baf815b6` |
| **Phase ψ.β.4** | paper_sim ensemble selector + 10 alpha yaml + regime_gate | [PASS] commit `1af98eca` |
| **Phase ψ.β.4.5** | backfill fact_stock_technical_stage + fact_signal_context 历史 | [PASS] 数据已落, 待 commit |
| **Phase ψ.β.4.6** | ensemble quality_filter (vol_60d / allowed_stages) | [PASS] commit `192bcb4d` |
| **Phase ψ.β.perf** | hotspot fix: _idx O(1) cache + backtest_signals_with_trades | [PASS] commit `192bcb4d`, 161 测过 |
| **Phase ψ.β.5** (in-progress) | optimize_per_formula 重跑 7 公式 × 35 train_end = 1260 任务 | ⏳ 5 worker 67% CPU, 1000/1260 |
| **Phase ψ.β.6** (next) | paper_sim ablation 完整 800 天 (reversal / momentum / ensemble) | ⏸ 等 ψ.β.5 |
| **Phase ψ.β.7** (next) | audit + 修 残留漏洞 (mart_stock_trend PIT / sector_momentum 全期 / case-based 等) | ⏸ |

git 状态 (commit chain):
```
main:                       34e83d75  (Phase ψ Optuna 治理)
feature/reversal-factor:    192bcb4d  (head, 含 β.1-β.4.6 + perf, 6 commits ahead)
  ← 192bcb4d  Phase ψ.β.perf
  ← 1af98eca  Phase ψ.β.4 ensemble selector
  ← baf815b6  Phase ψ.β.2+β.3 financial + capital_flow PIT
  ← 5a3b5ea8  Phase ψ.β.1 risk_factors PIT
  ← 545cb3d9  Phase ψ.α reversal + Rule 9 + PROJECT_INDEX
  ← 34e83d75  Phase ψ
```

worktree 残留: `/Users/dp/.codex/worktrees/a980/stock` 链接到外部 `/Users/dp/Documents/M/stock/.git`, 不归本项目处理.

---

## 13. 写本文档的源数据 (供刷新)

```sql
-- 项目自己维护的架构 inventory (smartmoney.duckdb)
SELECT * FROM mart_architecture_inventory_summary ORDER BY built_at DESC LIMIT 1;
SELECT * FROM mart_architecture_inventory_asset WHERE run_id = ?;
SELECT * FROM mart_data_health;
SELECT * FROM mart_data_source_watermark;
```

或运行 `backend/scripts/build_architecture_inventory.py` 自动重生成.

---

## 14. Session 增量更新日志 (已归档)

> 246 条历史增量已移至 `analysis/project_index_changelog_archive_20260611.md` (2026-06-11 文档治理)。
> 新增历史叙事写 `analysis/project_state_ledger.md`; 本文件只维护上方活索引与最近 7 天增量。

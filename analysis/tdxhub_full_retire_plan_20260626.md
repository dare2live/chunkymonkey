# 通达信(tdxhub/tdx F10)全删迁移计划 — 对抗验证版 (2026-06-26)

> owner=本文 + workflow wjk2g1yl7 (15 agent, 7 单元 fan-in 审计 + 对抗验证)。
> 缘起: 用户 2026-06-26 "通达信数据源全删, 用妙想源找替代"。
> 政策 (CLAUDE §4.3): tushare 优先 / 妙想(aif10) 补 F10 缺口 / 都没有则删数据。
> **状态: 决策门控 — 2 单元需用户拍板 (不可逆数据损失/与已有决议冲突), 3 单元需先 code-fix, 2 单元干净。**

## 替代映射总表

| # | 单元 | 替代源 | 就绪 | verdict | 关键 |
|---|------|--------|------|---------|------|
| 1 | 增减持意向 `fact_shareholder_plan_tdx_f10` | **无等价** | — | [BLOCK] block_needs_user | tushare/aif10 都是"实际成交"非"意向"; 物删=永久丢信号 + **与已落地"归档冻结保留"决议冲突** |
| 2 | 户数 `raw_tdx_f10_holder_count_history`+`fact_holder_count_period` | tushare stk_holdernumber | [OK] 更全更鲜 | [BLOCK] block_needs_user | 仅缺 **1997-2017 深史**(tushare 2018+); 0 live 消费方; 物删=丢深史 |
| 3 | 十大股东 raw `raw_tdx_f10_holder_research` | 派生已 100% aif10 | [OK] | [SAFE] safe | already_migrated, 删 raw 不动派生 |
| 4 | 财务 gpcw 簇 (5表) | tushare income/fina_indicator/forecast/balancesheet | [PARTIAL] 部分 | [REVISE] revise | **balancesheet 未拉** → contract_to_revenue 2020Q3后断源 |
| 5 | F10 元数据 `mart_tdx_f10_capability_matrix`+`mart_tdx_gpcw_file_manifest` | 删(无等价) | — | [REVISE] revise | 共享 writer 耦合 fact_fundamental_quarterly(域外L1表) |
| 6 | xdxr 热备 `price_kline_tdxhub_adjustment_event`(735行) | tushare adj_factor(qfq已吸收) | [OK] | [SAFE] safe | 真冗余 (前提: 先 neuter server_health) |
| 7 | server健康 `mart_tdx_server_health` | 删(运维元数据) | — | [REVISE] revise | **僵尸表**: 存活 builder 的 CREATE IF NOT EXISTS 会复活 |

## 对抗验证翻案 (原审说安全实则不安全 — 最高优先)

- **[BLOCK] 翻案-A 僵尸表复活 (单元7)**: `build_price_kline_tdxhub.py`(M3 明确保留的 xdxr 热备唯一 writer) 每次 adjustment_event 跑 (pull_adjust=None 唯一不抛异常模式) 都 load/record `mart_tdx_server_health`; `tdx_source.py:331 ensure_*`=CREATE IF NOT EXISTS → 物删后自动重建灌新行 (§4.5 僵尸表)。**必须先 neuter server_health 触点 + 删 DDL**, 否则删不掉。
- **[BLOCK] 翻案-B 越界掐写路径 (单元5)**: `build_fundamental_quarterly.py` 是 scoped-out 的 `fact_fundamental_quarterly`(60528行 L1_foundation, feature_registry 引用)的唯一 writer。删它=静默掐死域外 L1 基础表写路径。**物删 manifest 表不需删整脚本**。
- **[REVISE] 翻案-C orphan 测试崩 CI (单元5)**: `test_build_fundamental_quarterly.py:8` import + `test_tool_registry.yaml:1569/1573` 引用 → 删 writer 后 pytest collection ImportError 崩。须同删测试+注册表两行。
- **[REVISE] 翻案-D 漏第二消费点 (单元4)**: fan-in 漏 `macd_optuna_backtest.py:121 LEFT JOIN dim_financial_latest`(非死脚本, main.py:760 注册 optuna 入口)。repoint 须与 `compute.py:3041` 同等纳入。

## 分阶段执行 (可逆优先, 物删最后)

**Phase A (可逆, 无物删)**:
- A1 财务(单元4): backfill raw_tushare_balancesheet 含 contract_liab 至2026 → 重写 financial_client.calc_financial_derived 改 tushare → 重算 dim_financial_latest → repoint 两 JOIN(compute.py:3041 + macd_optuna:121)
- A2 server健康(单元7, 单元6/7物删硬前置): neuter build_price_kline_tdxhub.py 的 server_health load/record(L1235/1409/1417) + 删 tdx_source ensure_*/DDL + 前端冒烟降级
- A3 元数据(单元5): 解耦 — 保留 build_fundamental_quarterly.py(fact writer), 只去 manifest 写; 不删脚本

**Phase B (验收)**: 替代覆盖核(对日历/universe非tdx) + 全 fan-in 重审 0 残留(复查翻案-A/B/D) + pytest collection 不崩 + contract_to_revenue 非全NULL + dry-run 验 server_health 不复活

**Phase C (物删, 全 escalate)**: db_lifecycle_delete + deletion_record + db_compact。顺序: 单元3/6(干净, A2后) → 单元4(A1后) → 单元5/7(A2/A3后) → 单元1/2(用户决策后)

## 用户决策 (2026-06-26 已拍板)

- **D1 增减持意向 (单元1)**: [OK] **物删** (用户 2026-06-26 拍板, 知情接受永久丢"增减持意向"信号 + 覆盖早先"归档冻结保留"决议)。执行时须同步更正 PROJECT_INDEX 2026-06-24 "保留唯一数据不物删" 注记为 "用户 2026-06-26 改物删"。
- **D2 户数深史 (单元2)**: [OK] **物删** (用户 2026-06-26 拍板, 知情接受丢 1997-2017 深史; 户数全走 tushare stk_holdernumber 2018+)。

→ **结论: 7 单元全部授权物删** (含 D1/D2 不可逆损失)。执行按 Phase A(可逆 code-fix)→ B(验收)→ C(物删 escalate-已授权) 顺序。

## 执行就绪状态 + 节奏 (2026-06-26)

载荷事实已验: fact_holder_count_period(277560行)=deprecated tdx 时代户数表, 无活 builder, 0 live 消费方 = 删除候选确认。
全 4 smartmoney 表 fan-in 已扫: 都有 schema_core+schema_migrations DDL (**不清=僵尸重建**, §4.5) + config 声明。

**执行复杂度** (须逐单元谨慎, 非快批): (a) DDL 拆除 (schema_core/schema_migrations 精确删表块不碰他表) + config 清 (feature_registry 7声明/data_layers/sync_registry/retired/seed) + 验门绿; (b) 单元6/7 = xdxr/server 子系统退役 (撤 preflight SLA门 + 退役 build_price_kline_tdxhub.py builder + 删 tdx_source ensure/DDL); (c) 单元4/5 = 财务簇需 A1 (balachesheet backfill + financial_client 重写 + repoint 2 JOIN, 真金白银路径)。

**执行单元 checklist** (每单元: 清全 fan-in → 删 DDL → 物删 db_lifecycle_delete+deletion_record → 验门 → commit):
- [x] 单元1 增减持 (DONE 2026-06-26 Batch2): feature_registry 删7声明 + seed/retired/data_layers/sync_registry 清 + schema DDL 删 + 物删 + PROJECT_INDEX 更正
- [x] 单元2 户数 (DONE 2026-06-26 Batch1): data_layers/storage_retention/panel_manifest/coverage 清 + schema DDL 删 + 物删 raw_tdx_f10_holder_count_history + fact_holder_count_period
- [x] 单元3 十大股东raw (DONE 2026-06-26 Batch1): data_layers/storage_retention/seed/audit/data_routes 清 (派生 fact_top10_holder_period KEEP 100% aif10 不动) + schema DDL 删 + 物删 raw_tdx_f10_holder_research
- [ ] 单元6 xdxr热备: 撤 update_watermark_sla xdxr SLA(65-68) + 退役 build_price_kline_tdxhub.py + 物删 price_kline_tdxhub_adjustment_event。**[2026-06-26 纠缠发现, 待聚焦解]** `PRICE_KLINE_TDXHUB_DDL`(含adjustment_event)被 market_schema:158 schema-init executescript + market_db:22/market_read:6,161 导入导出 + 3 test fixture(mini_market:40/test_market_db_canonical_kline:52/test_kline_write_calendar_lint:138) + write-lint 编织; = ~7 文件 K线/xdxr 测试基础设施解耦, 非单删表。**[2026-06-27 纠正] 关联的 4 个 tdxhub K线测试当前在 main HEAD 上红着 (失败于 price_kline_qfq_tushare does not exist), 不在任何 worktree (git worktree list 实测无 worktree); 旧文档误写"独立 worktree"=transient session机制误当持久状态, 已纠正 (见 memory feedback-no-transient-mechanics-in-durable-docs)。单元6 须先收口这 4 测试。**
- [ ] 单元7 server健康: workbench 读有 `_relation_exists` 守卫(降级) + seed_dim:172 freshness + tdx_source server_health 函数(288-491 仅builder调, builder退役后死码) + 物删 mart_tdx_server_health。与单元6 同 builder 子系统一起做。
- [ ] 单元4 财务簇 [**2026-06-26 scope 确认: 真金白银源迁移非删, 7+活消费方, 需聚焦专做**]: `financial_client.calc_financial_derived`(:1554) 读 `SELECT * FROM raw_gpcw_financial`(22769行)→ 派生 → 写 `dim_financial_latest`(**7 活 service 消费方**)+ fact_financial_derived(4 service)+ fact_fundamental_quarterly(L1, 6消费)。**迁移 spec**: 重写 calc_financial_derived 读 tushare — roe/debt/margin/ocf/yoy 等派生比率 ← `raw_tushare_fina_indicator`(95659行 max20260331, 直接含派生); revenue/profit ← `raw_tushare_income`(71359行); forecast ← `raw_tushare_forecast`(17722)。**缺口**: `raw_tushare_balancesheet`/cashflow 不存在(advrecv 仅到2020Q3)→ `contract_to_revenue`(contract_liab/revenue) 断源, 须先**网络回填 tushare balancesheet** 或弃该字段。repoint compute.py:3041 + macd_optuna:121(翻案-D)。验收: 7 消费方 schema/值不破 + re-derive 正确。→ 全绿后物删 gpcw 5表。
- [ ] 单元5 F10元数据 [与单元4 耦合, 共享 build_fundamental_quarterly writer]: 解耦 build_fundamental_quarterly (保 fact_fundamental_quarterly writer, 只去 manifest 写) + 删 orphan test/registry + 物删 mart_tdx_f10_capability_matrix + mart_tdx_gpcw_file_manifest 2 mart 表
- [ ] **通达信客户端整体退役步 (最终)**: tdxhub.py/tdx_source.py/tdx_affair_client.py/workbench_tdx_*.py 客户端适配器 wholesale 退役 (全数据删后, 非 piecemeal)

## 本 session 执行进度 (2026-06-26)
- **DONE: 单元1/2/3 (4表物删)** — fact_shareholder_plan_tdx_f10 + raw_tdx_f10_holder_research + raw_tdx_f10_holder_count_history + fact_holder_count_period; 全 fan-in 清(schema DDL/feature_registry/config/audit/seed); schema-init smoke + data_layer/config_refs/moth 45/0/0 全验。commit: 7125422d(B1) / 5f33746d(B2)。
- **剩: 单元6/7 (xdxr/server, ~7文件K线测试基础设施纠缠) + 单元4/5 (财务, 网络backfill+真金白银重写)** — 均需聚焦执行, 非降级期末尾快做。

## 单元4 财务迁移深度发现 (2026-06-26 值比对 prep, 用户选"先值比对"救出真复杂度)

> 用户选"回填balancesheet保留 contract_to_revenue + 先做 gpcw-vs-tushare 值比对"。值比对 prep 阶段实测发现:**财务迁移不是字段重映射, 是数据模型重设计**。

1. **balancesheet API 要 ts_code (不支持 ann_date 批量)** → 域改 `by_ts_code`(同 fina_indicator), 已注册 backfill 2020+ 进行中(PID后台, ~40min)。实弹核证茅台 contract_liab 2026Q1=30.27亿存在。
2. **fina_indicator 仅 2023+** (gpcw 财务链 2020-12-31~): tushare 派生指标源覆盖比 gpcw 短 ~3年 → 迁移须先把 fina_indicator backfill 扩到 2020 (fixed_params.start_date 20230101→20200101 + 重拉), 否则丢 2020-2022 财务。
3. **gpcw 是快照模型, tushare 是周期模型** [核心]: gpcw report_date='2026-04-07'/'2026-04-01'(F10 页抓取/快照日, 非季末); tushare income/fina_indicator/balancesheet 是 period 模型(一行/ts_code×end_date季末)。`calc_financial_derived` 的 snapshot-to-snapshot YoY 逻辑 ≠ tushare period-to-period。**重写不是 repoint, 是 derivation 链重设计**(snapshot→period; 输出 dim_financial_latest=每股最新财务仍可从 tushare MAX(end_date) 派生, 但派生逻辑全改)。
4. **单位差**: fina_indicator roe 是 %(25.0), gpcw 计算的是分数(0.25) → 值比对/迁移须归一。

**=> 单元4 是真金白银财务链【重设计】(snapshot→period 模型 + 2源 by_ts_code backfill 到2020 + derivation 重写 + 值比对 + 验7消费方), 一个聚焦工程项目, 非降级期能收口。本 session 已做: 注册 balancesheet + 实弹核证 contract_liab + 启动 backfill + 文档化设计约束。**

## 单元4 值比对结果 (2026-06-26 用户坚持"先值比对", 救出真金白银级发现)

**balancesheet 回填 DONE**: 117084行/4987股/2016-2026/contract_liab非空111226 (by_ts_code; 实弹核证茅台2026Q1=30.27亿)。

**fina_indicator 扩2020 受阻**: 实弹发现 fina_indicator API 现【完全不返 update_flag 列】(2023窄窗也不返), grain=[ts_code,end_date,update_flag]依赖它 → 任何sync报'缺grain列'。既有表95659行有update_flag(曾返)=API/tinyshare漂移。**= 独立 live bug**(fina_indicator 现无法sync), 修=grain改[ts_code,end_date,ann_date]或容忍缺update_flag, 是careful前置任务。已回退扩窗config到20230101。

**值比对 (dim_financial_latest gpcw派生 vs fina_indicator最新期, 5201重叠股)**:
| 指标 | corr | 中位\|差\| | 判定 |
|---|---|---|---|
| net_margin | 0.016 | 0.007 | 表面小差但茅台spot-check匹配(50.3%vs50.5%) |
| debt_ratio | 0.750 | 0.053 | 中等接近 (茅台15.6%vs16.4%) |
| **gross_margin** | **-0.109** | 0.53 | **gpcw错!茅台gpcw=8.7% vs fina=91.2%(真实~91%高端白酒)** |
| roe/yoy | — | — | 期不匹配混淆(gpcw快照H1 vs fina周期年报) |

**真金白银级发现 (spot-check 茅台坐实)**: gpcw `revenue` 字段虚高~15x(茅台显示1.28万亿/实际~1700亿)→ gross_profit/revenue烂; net_margin因分子分母同虚高巧合正确。**=> 当前 gpcw 财务打分用的 gross_margin 是错的; tushare fina_indicator 正确。迁移反而修复数据质量, 但会显著改变scoring/screening输出(向正确)。**

**单元4 redesign 的真复杂度 (值比对揭示)**: (a) 不能盲信任一源 — gpcw 有质量问题, 须以 tushare(fina_indicator+balancesheet) 为准; (b) fina_indicator update_flag 漂移须先修 grain; (c) 快照→周期模型重设计; (d) 迁移会改财务打分值(scoring/screening), 须 escalate 用户知情(值会变但更准)。**这是聚焦工程项目 + 需用户确认"接受财务打分按更准的tushare值变化"。**

## 单元4 shadow 验证结果 (2026-06-26 workflow wydf17fu8 + controller 亲核)

**已建 dim_financial_latest_shadow (5202行, tushare派生, 不碰 live)**。controller 亲核茅台 600519:
- **shadow gross_margin=0.8976(89.76%) ✓ 对** (vs live gpcw 0.0871 错) → 迁移**修复 gpcw 数据质量坐实**。net_margin 0.5222 对。
- 全表: gross_margin shadow中位0.242(合理) vs live中位0.758(gpcw garbage), corr -0.109 = live错非shadow错。

**shadow 暴露 1 个待修 subtlety (promote 前必解)**:
- **roe 等累计指标周期基准不一致**: fina_indicator "最新期" roe 是季报【累计】值 (茅台2026Q1=10.57%), 多数股最新期是近季 → roe 中位仅0.010(1%) 偏低/不可比。**修: 用 roe_yearly/TTM 或统一取年报期 (end_date like '%1231'), 不裸用最新季累计**。同类: 任何"累计自年初"的指标 (revenue/profit 绝对值) 跨 Q1/年报不可比。比率类 (gross_margin/net_margin/debt_ratio) 不受影响 (比率跨期可比)。

## 单元4 对抗验证过的字段映射 (workflow wydf17fu8, 新session 直接用不必重跑)

> dim_financial_latest 17 列 → tushare 源 (茅台600519实测对账 + 全市场range校验)。**陷阱列已对抗验证标红**。

| dim 列 | tushare 源.列 | 单位转换 | 陷阱/注 |
|---|---|---|---|
| roe | fina_indicator.roe | **/100** | 季报累计口径(茅台Q1=10.57→0.106 vs FY=34.46) — **promote前考虑用roe_yearly统一年化** |
| debt_ratio | fina_indicator.debt_to_assets | /100 | 茅台0.121(FY0.164) |
| current_ratio | fina_indicator.current_ratio | **不除!** | 倍数非%(茅台7.06, /100=0.07错) |
| **gross_margin** | fina_indicator.**grossprofit_margin** | /100 | **绝不用 `gross_margin` 列(=毛利【金额】陷阱,同gpcw错)! 用 grossprofit_margin(毛利率%) 茅台89.76→0.898** |
| net_margin | fina_indicator.netprofit_margin | /100 | 茅台0.522 |
| revenue_yoy | fina_indicator.tr_yoy | /100 | 内置比手算稳 |
| profit_yoy | fina_indicator.netprofit_yoy | /100 | |
| ocf_to_profit | fina_indicator.ocf_to_profit | /100 | 茅台0.717 |
| **contract_to_revenue** | balancesheet.contract_liab / income.revenue | 金额/金额 | **共同最新期 INTERSECT(茅台bs有20260331但income最新20251231→取20251231); TRY_CAST(contract_liab是VARCHAR); 分母income.revenue非total_revenue** |
| holder_count | stk_holdernumber.holder_num | TRY_CAST(VARCHAR) | **每(ts_code,end_date)最多3重复行须dedup(ann_date DESC)** |
| holder_count_change_pct | computed (相邻两期) | 分数 | **dedup后再算lag(否则重复同期当上期→假0)** |
| float_shares | daily_basic.float_share | ***10000**(万股→股) | 取MAX(trade_date) |
| total_shares | daily_basic.total_share | ***10000** | |
| latest_report_date | fina MAX(end_date) | YYYYMMDD→**YYYY-MM-DD** | 消费方字符串比较须带横杠 |
| history_rows | COUNT(DISTINCT end_date) | 计数 | 非COUNT(*)(有重复) |
| stock_code/updated_at | meta | substr6位 / now().isoformat | |

**期逻辑**: fina取每股最新期(end_date DESC, ann_date DESC去重); balancesheet+income取两表共同最新期(INTERSECT, 各自ann_date DESC去重); stk_holdernumber/daily_basic独立取最新。**PIT注**: 此dim=latest-snapshot(沿用gpcw口径), 非历史面板; 未来PIT化须ann_date<=t过滤。**实测**: fina_indicator表实际覆盖20191231~20260331(22期, 比config window 2023+多), 故迁移历史覆盖比担心的好。

## 单元4 promote 路线 (新session 接手, shadow 已建+验证)

> **状态 (2026-06-26 更新): rewrite + roe修复 + contract BLOCKER修复 + 单测 + 对抗验证(workflow wuxnownvm) 全 DONE → 卡在 escalate 用户确认 promote (步3)**。
> 不可逆物删 gpcw 在最后, 须 用户确认财务打分值变化后。详见下方"本session promote执行进度"。

1. **修 roe 周期基准** (上述 subtlety): shadow 派生改累计指标用 roe_yearly/统一年报期; 重建 shadow 重验。
2. **全列验** (controller): shadow vs live 逐列, 比率类应接近(除gpcw错的gross_margin), 累计类修后接近; 茅台/几只龙头 spot-check。
3. **promote**: rewrite financial_client.calc_financial_derived 读 tushare (按 shadow 验证过的映射) 替 raw_gpcw_financial; 重新生成 live dim_financial_latest + fact_financial_derived。**escalate 用户**: 财务打分值会变(向正确, gross_margin修复) — 确认接受。
4. **验 3 消费方**: scoring.py(用 roe/debt/gross/ocf/contract rank, 财务子分会变)/screening(仅float_shares)/stock_stage; schema保留+run不破+财务分合理。注: scoring.py 还有 _ak后缀(akshare另源)+yoy_4q特征表多源, 本次只迁 dim_financial_latest(gpcw部分)。
5. **物删 gpcw 簇** (escalate): raw_gpcw_detail/financial/wide + dim_tdx_gpcw_field/_semantic (单元4) + 单元5 mart_tdx_f10_*; 同 DDL/config/fan-in 清 (同 Batch1/2 模式)。
6. **fina_indicator update_flag grain bug** (独立live bug, 单独修): API不返update_flag → grain改[ts_code,end_date,ann_date]或sync_runner容忍; daily sync freshness 需它。用现有2023+数据可先做上述迁移。
7. shadow 表 dim_financial_latest_shadow 是验证产物, promote 后可 DROP。

## 单元4 本session promote 执行进度 (2026-06-26, model: Opus 4.8)

**代码 DONE (financial_client.py calc_financial_derived 重写)**:
- 签名 `calc_financial_derived(conn, *, attach=True, write_suffix='')`; 快照→周期模型 SQL 化 (替旧逐行Python);
  ATTACH tushare_raw READ_ONLY (manifest path_for); 源全按(ts_code,end_date)去重(ann_date/update_flag/built_at DESC);
  写 fact_financial_derived(周期历史,74192行) + dim_financial_latest(最新快照,5202行)。
- **两处真金白银修复**: roe←roe_yearly(年化, 非季报累计); gross_margin←grossprofit_margin(毛利率%, 非gross_margin金额陷阱)。
- fact 的 float/total_shares/holder_count_change_pct 留 NULL (无消费方读fact这几列; dim才填)。
- **2 新单测** (test_financial_client.py): 守全映射+两修复+去重+单位+INTERSECT+FY-restriction+shadow隔离。12测试全过。

**对抗验证 DONE (workflow wuxnownvm, 3 lens + 综合)**:
- Lens A 独立重导 12 股×15列 = **0 mismatch** (茅台+银行+地产+亏损+IPO+保险全对)。
- Lens B 消费方 (scoring/screening/stock_stage) 原样SELECT跑shadow **零破坏** (schema全兼容5202行0报错); 财务分向正确移 (gross garbage 0.755→0.242; roe极值崩坏修)。
- **Lens C 对抗抓 1 BLOCKER (我自己漏, 茅台落FY口径掩盖)**: contract_to_revenue 期间口径混合 — contract_liab时点÷累计YTD营收, 多数股落Q1(3个月)→虚高4.5-6.8x跨股不可比, scoring绝对门33%股按财季运气压0分。

**BLOCKER 已修 (FY-restriction)**: contract 分子分母锁最新年报期(end_date 1231) → 分母恒12个月可比。验证 (measured):
- 茅台 0.047 **不变** (本就落FY); 601628 70.93→**10.36** / 601601 27.44→**5.67** / 601319 8.39→**1.85** (与验证者独立FY值逐股精确吻合);
- contract>0.20压0分 33%→**10.5%** (剩余=真高合同负债保险/地产, 正确)。fact非FY期contract=NULL(诚实)。

**可接受限制 (综合裁决, 不阻塞 promote)**: 亏损股ocf NULL(97%为net_margin<0,语义正确) / 银行无毛利率(101股raw即NULL) /
roe_yearly Q1年化季节失真(PIT约束下最好年度代理,median差仅3.32pp) / **holder_count_change_pct环比窗口不等(MEDIUM, 1753股非季末披露当上期 → defer builder侧季末过滤, 权重小±0.5~1.5)** / 微利股极值(rank沉底)。

**PROMOTE DONE (2026-06-26, 用户选"Promote+立即物删gpcw簇")**: calc_financial_derived(conn) 无suffix 已覆盖 live —
dim 5204→5202 / fact 23691→74192; 茅台全列正确(roe 0.4227/gross 0.898/contract 0.047); gross_margin count>1.0=0(garbage清);
3消费方(scoring/screening/stock_stage) live SELECT 跑通5202行0报错; watermark MAX(report_date)=2026-03-31。**live 财务数据已是正确 tushare。**

**物删 gpcw 簇 fan-in 审计 (mio铁律11) → 发现远超财务迁移范围, 物删受阻 (诚实标, 未盲删)**:
财务迁移只迁了 dim_financial_latest(calc_financial_derived)。gpcw raw 表还有**非财务 live 消费方**未迁:
| gpcw 表 | 非财务消费方 | 删除前置 |
|---|---|---|
| raw_gpcw_financial | calc已迁✓; **sync_financial_data 仍写它** + ensure_tables(:100) DDL + audit/db_health/data_routes/clients_registry/data_layers | 退役gpcw sync + 删ensure_tables DDL(防僵尸重建) + 清5引用 |
| **raw_gpcw_detail** | **signals_v2 LIVE**(router HTTP; `_load_gpcw_feature_maps` 取 holder_count[D1]+forecast_profit_yoy_mid[D3], 经 data_access financial_gpcw entity) + dead builder | **硬阻塞: 须先迁 signals_v2 2特征→tushare(holder_count→stk_holdernumber / forecast→raw_tushare_forecast)**; data_access.yaml:281 注释自承"M4切tushare改一行"但实际2特征异源非一行 |
| raw_tdx_gpcw_wide / dim_tdx_gpcw_field / dim_tdx_gpcw_field_semantic | **build_tdx_gpcw_auto_features = dead流水线**(0 代码调用点, 仅手动/孤儿) + profile script + schema_core(:DDL) | 退役dead builder + 删schema_core DDL + 清config(storage_retention/seed/tdx_data_need_coverage) |
| mart_tdx_f10_capability_matrix | workbench_data_source_read(`_table_exists`守卫优雅降级) + schema_versions | 低风险, 删后降级 |
| mart_tdx_gpcw_file_manifest | build_fundamental_quarterly(翻案-B: 同写L1 fact_fundamental_quarterly 60528行) + seed + tdx_affair_client | 单元5解耦: 保fact writer去manifest写 + 删orphan test/registry |

**=> "物删 gpcw 簇" = 通达信客户端整体退役步(plan最终步), 一个独立多消费方退役工程**。

**gpcw退役 Stage1 DONE (2026-06-26)**: 切 signals_v2 gpcw 依赖 (_load_gpcw_feature_maps 返空 → D1/D3 过滤器 no-op; 删 data_access financial_gpcw entity); signals_v2 78测试全过。**raw_gpcw_detail 的唯一 live 消费方已解除**。用户决议"信号重做不用旧的", D1/D3 不迁 tushare 直接退。

**sync 真相 (measured, 定论)**: `sync_financial_data`(gpcw sina/akshare sync) **全 backend 0 caller** (pipeline/acquire 财务 sync 走 `_sync_registry_drain` registry-driven tushare, 不调 gpcw sync); tdx_affair_client/build_tdx_gpcw_auto_features/build_fundamental_quarterly 均 0 daily caller。**gpcw 摄取流水线全 DEAD, gpcw 7表=冻结 legacy (无 active 写, 不再 sync)**。

**剩余 = 物删 + wholesale 代码退役 (focused session 级, 跨 ~8 live-shared 文件, 不可逆; 安全已确认但规模大不宜 session 尾赶)**:
1. raw_gpcw_financial: DDL 在 ensure_tables(live calc 共用) + dead sync body export FIN_HISTORY_*/summarize_history_gap_state **被 audit.py import** + 10 sync 测试依赖 → 须连 sync body wholesale 删 + 改 audit import + 删10测试。
2. raw_gpcw_detail/wide/dim_tdx_gpcw_field/_semantic: DDL 在 schema_core(须删防僵尸) + tdx_affair_client(dead) + build脚本(dead)。
3. mart_tdx_f10_capability_matrix(workbench _table_exists 优雅降级) + mart_tdx_gpcw_file_manifest(build_fundamental_quarterly 同写 **fact_fundamental_quarterly L1 feature_registry引用须保** → 单元5 解耦)。
4. 清 ~15 config (data_layers/clients_registry/data_routes/storage_retention/seed/tdx_data_need_coverage/field_dictionary/schema_versions/audit/db_health/data_module_members)。
5. db_lifecycle_delete 物删 7表 + deletion_record + gates绿 + 对抗验证。
6. wholesale 退役: tdx_affair_client.py + build_tdx_gpcw_auto_features.py + profile_tdx_gpcw_fields.py + financial_client sync body。

**状态 (2026-06-27 用户决"现在careful增量删完", 已完整收口)**: 财务迁移(correctness)+promote上线 + gpcw 簇 7表物删 全 DONE。
- Stage1: 切 signals_v2 gpcw 依赖(D1/D3 no-op)。
- Stage2: 物删 3 dead文件(tdx_affair_client/build_tdx_gpcw_auto_features/profile) + schema_core/ensure_tables gpcw DDL移除(防僵尸) + financial_client sync body标RETIRED(保_bootstrap/_parse_*) + db_health gpcw清 + 测试收口(financial留calc/db_health repoint fact_top10) + clients_registry/data_routes/data_module_members清。
- Stage3: 清 data_layers(7声明)/storage_retention/seed/schema_versions + 单元5 build_fundamental_quarterly标RETIRED(保 fact_fundamental_quarterly L1 冻结) + **db_lifecycle_delete 物删7表** (archive parquet+deletion_record lifecycle_gpcw_retire_20260627) + DROP shadow。
验收: 0 gpcw残留 + data_layer_audit PASS(87表/0stale) + config_refs PASS + moth PASS 45/0/0 + schema-init无僵尸 + 测试绿(1预存在mart失败非本次)。**gpcw=GONE**。
**低风险 follow-up (非阻塞, 不在 session 尾做)**: (a) financial_client dead sync body + build_fundamental_quarterly 整段代码物理移除(已标RETIRED, dead 0caller 不zombie, 须保 _bootstrap[ensure_tables依赖]/_parse_float[lhb/qfii/aif10/capital共享]); (b) tdx_data_need_coverage/field_dictionary gpcw 软引用清(gates绿不阻塞); (c) db_compact 缩盘回收 ~156k 行。

## 要丢的不可再生数据 (诚实标)
- 增减持意向信号 (无任何源可重建)
- 户数 1997-2017 深史 (tushare 仅 2018+)

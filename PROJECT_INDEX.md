# PROJECT_INDEX.md — Chunky Monkey v2 项目地图 (context-only briefing)

> 用于防止对话压缩 / context 丢失导致重复发现项目结构 / 误解数据资产.
> 内容是**项目地图**, 不是规则 — Codex 规则在 `AGENTS.md`; 当前阶段计划在薄入口 `goal.md`; 历史状态/已完成证据在 `analysis/project_state_ledger.md`; `SESSION_HANDOFF.md` 是生成恢复快照; durable contract 在 `docs/README.md` 指向的 active docs; `CLAUDE.md` 是 legacy Claude-specific history.
> **文档保鲜 (2026-06-20)**: 全面审计后修死引用 (reset 删的 audit_*/build_* 脚本 → 改指现 gate moth coupling/check_doc_drift + 当前 build_rally_*/feature_panel 管道); `docs/implementation_plan.md` + `docs/chip_distribution_cyq_spec.md` 标 **deprecated** (留历史参考, 勿当现行命令源); `engineering_governance`/`chunkyctl_session_quickstart` 死引用改指现 gate/管道 + 陈旧头注。**机器化根治 (用户"保持最新避免污染")**: `check_doc_drift.py` **扩展扫全活文档** (活索引+AGENTS+docs/ 13 档, 非仅活索引 — 故原漏报 AGENTS/docs 死引用); lookbehind 防 mid-path 假阳性(bestchoice/scripts) + 整档 deprecated 头注豁免 + 行级 retired/历史叙事/模板豁免; 6 单测; moth `doc-drift` 断言守 (commit 前拦未来死引用)。现 PASS 悬空=0/13档, 2 deprecated 跳过。
> 2026-06-05 起，旧 GCP / GCS / phase5 monitor / cost tracker 条目只作历史证据，不是可恢复执行面。当前长任务/花钱任务必须走 `backend/config/experiment_jobs.yaml` + `scripts/chunkyctl jobs`，`local` active，`modal` active(端到端验证 2026-06-20)。
> **2026-06-20 D 按阶段因子矩阵 重定向 (用户纠偏; 前期买点 detour 全删)**: 前期 D-step 起涨点买点判别偏离计划 §0 诚实先验 (买点=secondary, 主攻=鱼身延续+鱼尾出场+仓位)，已全删。**正确路径 (用户确认)**: 在 `fact_rally_stage`(起涨/主升/顶部) 上逐阶段验因子, 优先级 **鱼尾出场 > 鱼身延续 > 鱼头买点**; 判据=**stage 窗内条件化持有(持到信号反转非固定调仓)→含成本绝对收益, IC 仅快筛非 AUC**; 鱼身=是否续持, 鱼尾=何时卖。先 CYQ 出货预警(鱼尾,0代码,cyq_perf 单峰→多峰)+多头排列/资金净入(鱼身)。owner=`data_validation_backtest_plan_20260619.md` §2.2-2.3。
>
> **目标**: 新接手 (无论 Claude 还是人) 读完此文档**不用看代码 / 不用查 DB** 就能理解:
> 项目业务 / 架构 / 技术路线 / 数据资产 / 当前进度 / 已知坑 / 常用操作.

最后更新: **2026-06-06** (TuShare no-persist exact-flow probe wiring + need_027 probe diagnostics hardening + storage retention owner/consumer policy contract + data-source capability router contract + need_027 candidate validation metadata + provider-neutral experiment job contract + execution-surface audit + retired GCP execution surface removal + architect-controller skill install + verify-verifier rule + Moth complexity path normalization + local complexity baseline refresh + data-health dry-run read-only fix + Moth evidence path sync + design-review preflight machine gate + Moth registry instruction-source sync + after-close data refresh + controller-agent preflight hard gate + retention dry-run inventory + storage payload cap recalibration + DB manifest attach policy + DB boundary static gate + holder replay safety + Codex instruction-source boundary + DuckDB capacity audit + need_027 exact-flow probe gate + stage-opt supply/readiness/schema contract + stage-opt signal-date K-line coverage evidence + stage-opt source-aware density diagnostics + stage-opt source freshness/window diagnostics + iFinD MCP research-only routing recheck).

## [INDEX] 最近增量 (只留 7 天, 历史在 analysis/project_index_changelog_archive_20260611.md + ledger)

- **2026-06-17 P0 数据域注册 (tushare 选股潜力研究后)**: 单日实弹核证后注册 5 域进 sync_registry (口径对齐项目 行业=申万/概念=东财, 禁同花顺第三套): sw_daily(申万行业日线 by_trade_date) / share_float(解禁 by_trade_date date_param=ann_date, float_date前瞻PIT) / stk_factor_pro(261技术因子 by_ts_code, 不支持单日全量) / stk_holdernumber(户数 by_ts_code)。**by_ts_code 取码改走 services.universe 单一真相源**(白名单60/00/30/68+非ST+非退市, 实测4969码0排除股漏入; 原内联tdxhub+前缀=第二套定义漏ST已退役)。namechange 撤销(退市/ST导向违排除列表, ST由stock_st覆盖)。拉取走 sync_runner --domain <d> --backfill。研究 owner=analysis/tushare_alpha_potential_research_20260617.md。
- **2026-06-17 全库清排除股 + universe 写入门 (用户: 排除列表硬真相源, 北交所/新三板/老三板)**: (1) **一次性 purge** (DB侧, 验剩排除=0): 个股级表删非白名单前缀(60/00/30/68外)行 ~4.9M (tushare_raw 4.33M 含 daily/cyq_perf/adj_factor/stk_limit/moneyflow_dc/top_inst + dc_member 按 con_code 745k / market 295k / smartmoney 41k / feature_store 220k)。**假阳性避开 (mythos§14)**: dc_index/dc_member(ts_code)/moneyflow_ind_dc/sw_daily/index_* 的"100%排除"是指数/概念/行业代码(BK/801/399)非个股, 未删。(2) **写入门防回潮**: `sync_runner._write_batch` 加 universe 写入门 (`spec.universe_filter=true` 写前丢非白名单前缀行, dc_member 用 `universe_filter_col: con_code`); 26 个 stock-level 域加 `universe_filter: true`, 指数/概念/日历域不设(防误删399/801); by_trade_date 域重拉全市场不再加回北交所。实测 share_float 拉取丢53北交所行/落库0排除前缀。(3) **ST/退市不整删 (PIT)**: ST 可摘帽/退市股有交易期合法历史, 整删=丢合法史+生存者偏差 → 由 universe.assert_universe_clean + is_st_on PIT 消费侧排除。
- **2026-06-19 universe 身份真相源切 tushare stock_basic (退役 akshare dim_active 前缀猜)**: 根因实证 — 旧 `get_active_universe` = K线90d活性 ∩ 前缀(00/30/60/68) − ST, **不与真股清单交集** → 双向 bug: 漏入指数 benchmark 000300(沪深300, 与00前缀共号段)+ 漏掉真股(001393 等 8 只, 旧 akshare 快照 stale 24天)。`dim_active_a_stock` 旧由 **akshare** `stock_info_a_code_name`(bare码 + `_market_from_code` 前缀猜市场)建。修: (1) 注册 `stock_basic` 域 (tushare, full_refresh, list_status=L, **不设 universe_filter**=身份真相源本身, raw 保全市场); (2) `security_master.refresh_active_a_stock_master` 改读 raw_tushare_stock_basic 重建 dim (stock_code=symbol, market=ts_code后缀权威SH/SZ, 排北交所market='北交所', source='tushare_stock_basic'), 删 akshare 调用 + `_market_from_code`/`_disable_proxy_env`/os import 孤儿; (3) `get_active_universe` 加**身份交集** (K线 ∩ dim_active_a_stock 真股清单 − ST), 前缀降 defense-in-depth。实测: dim 5201(akshare)→5208(tushare, +8真股/-1退市000638\*ST万方06-03退市), universe 000300出局/4978干净码/0非白名单漏入; test_universe 16 passed (+1 防回退: 指数不在真股清单必剔)。19 消费者只 ingest_holders_tdxhub.py 读 market 列(仍SH/SZ)零改。全盘点见 analysis/non_tushare_source_inventory_20260619.md (非tushare源 akshare22/tdxhub18/aif10 13, M2-M4 逐簇双轨退役)。
- **2026-06-19 非tushare孤儿表退役 (逐表对抗验证 wf_39200ec2, 11表→SAFE_TO_DROP 6/KEEP_MIGRATE 5)**: 验证抓住 aif10 valuation_quantile(3消费者 v3_picture)/peer_valuation/price_kline(4消费者 regime/return) 是 **LIVE** → 不可 bulk-drop (mythos§14)。已物删 4 表 (0 live消费者): **fact_orderbook_snapshot**(market 100, 污染残留)· **raw_fund_flow_daily**(86k, 被 tushare moneyflow 替代)· **raw_aif10_holder_count**(742k, 转 tushare stk_holdernumber)· **raw_aif10_financial_history**(5713, 探针孤儿)。aif10 shared writer 删2留3 (aif10_capability_client + updater DAG 5文件精细手术, 3 KEEP capability/DAG完好)。fact_hsgt_daily(2767, build_akshare_panel 删 build_hsgt_daily 留其余5表 + institution_alpha northbound块退役)。fact_financial_indicator_ak+dim_financial_indicator_latest+sync_state(git rm dedicated writer financial_indicator_client + financial_client caller改stub; scoring/audit try/except安全降级dormant层保留)。**[OK] 6/6 SAFE_TO_DROP 全退役 (~750k行)**, data_layer_audit PASS(87表)/moth fail=0。剩 KEEP_MIGRATE_FIRST 5表(aif10 valuation/peer/forecast live + dividend_summary + price_kline)走 M3/M4 双轨先迁后删。退役日志 owner=盘点doc §3.5。

- **2026-06-17 清验证墓地 + 恢复干净地基 + universe 升交易日历级真相源** (用户决议): (1) **清理** (不可逆, 已确认): 删旧 LGBM/ensemble 验证墓地 196M (data/reports/optuna 153M + v7_retrain + msaf_ensemble_*/phase4_gate_* + data/optuna 公式工厂 studies + multidim_models) + 分层孤儿 (calc.duckdb 0B 未声明 + concept_snapshots 8.7M + portfolio_backtest_nav.csv); wipe experiment_store 探索裁决 (25+10+9 行); 保 live infra (daily_*/data_audit/leakage_audit/底座6库)。(2) **恢复干净地基**: data_layers L2/L3/L4 声明为空 (06-14 reset 已清, 无模型层残留); drop 旧 GT 两表 (rally 43202 含北交所 3.1%+ST污染突破def / macd 280324)。(3) **universe 升交易日历级硬真相源** (`services/universe.py` 单一计算点): 加 `assert_universe_clean()` 硬验证器 (前缀级, 排除股进任何 GT/回测/选股 = raise `UniverseContaminationError`) + PIT ST 日历 `load_st_calendar/is_st_on` (raw_tushare_stock_st, 历史 t 真相源) + `classify_exclusion` (北交所/三板/ETF taxonomy 进 universe_rules.yaml); kline 源切 tushare。**三道门**: 代码门 `check_universe_filter.py` 拦内联白名单前缀绕过 (污染根) / 数据门 moth `rally-gt/macd-gt-universe-clean` (GT 0 排除股) + `universe-hard-gate-present` / 运行时门 builder 调 assert_universe_clean。(4) **结构型 GT 重建** (新 D1 锚): `build_rally_ground_truth.py` (用户图样型 长底+多头排列+平滑+底→顶>60%, 漏斗21687→9070主升浪/4347股, universe硬门PASS) + `build_macd_episode_ground_truth.py` (金叉峰值>30%, 311291 episode/5197股); 删超期 experiment_zhushenglang_swing_def + experiment_macd_episode_scan。test_universe 15 passed (5 新硬门防回归)。002484(江海)实测命中主升浪 = 定义验证 (具体 forward 收益属探索期数字, 不入索引)。

- **2026-06-16 重启 (清探索污染 + 立方法论 owner)**: 用户决议清掉本轮无锁方案的 alpha 探索污染重新开始。**精准删除** (保数据底座+基础设施改进): DB 清 experiment_store 留档行 / drop feature_store L2 探索面板(8.17M)/缓存+0行表; 删本轮 16 个 alpha 探索 runner + episode 引擎 + 49 个 analysis 验证结果 json + 11 探索设计稿 + 探索方法论 doc + 4 探索 config + consumer_alpha family + 6 探索 moth 断言。**保留**: sync 限流修复 / tushare catalog(241接口) / mio 收编 / G2-G3 治理 / 全数据底座(raw/dim/K线/财报/行业/serving)。**立权威方法论** `docs/alpha_discovery_methodology.md` (用户口述监督式范式: 裸K线扫主升浪>60% / MACD episode>30% = ground truth → 入场点 PIT 因子逐层叠 → 分层 → train≤2025-06/OOS→2026-06 → Modal; 高积分高价值因子优先 hk_hold/stk_holdertrade/moneyflow_dc 等)。cyq 实测与 tushare qfq 同复权坐标可用(C0 FAIL=审计比错基准非数据错), 本地 2023+ 用 2018 需回填。 **耦合检查工具** `moth coupling` (引擎全局子命令) (用户: 删除暴露 表↔代码↔配置↔DB↔文档↔测试 耦合): --impact <name> 删前看 fan-in 爆炸半径 / 默认扫孤儿引用 (pytest --co 真实 collection 崩 + moth 文件悬空) → moth 断言 coupling-no-orphan-refs。CI 修复: 删 experiment 脚本漏删的 2 孤儿测试 (collection 崩根因)。方法论并入 MASTER §5 (docs 11→10)。 CI 第3处: ci.yml 硬编码测试清单/family 断言悬空 (修+耦合工具 T5)。CI 第4处: 误删 formula_search_spaces/candidates config (被保留优化层 plan_validator/features 消费, 非探索) → 恢复; consumer_alpha_matrix/phaseD_search_space 真探索仍删。本地全量 CI offline 91/91 passed。


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

**当前最强发现**: 无 — 2026-06-16/17 地基-reset + 清验证墓地后, 所有 reset 前/污染期 alpha 结论 (reversal sharpe / 二次突破超额 / 鱼身 / lgbm 模型 / frontier) 已作废清除。当前态 = **unknown**, 以 `goal.md` Active Priority Board 为准 (CLAUDE §4.2: 不引用文档旧数字, doctor --fast 实测为准)。

**下一步**: 监督式 episode-first 结果倒推 — 结构型主升浪 GT (已重建, 底→顶>60%+长底+多头排列+平滑, universe 硬门 clean) → 逐数据 alpha 验证 (因子对起涨/持仓/出场判别力) → train≤2025-06 / OOS→2026-06 → 含成本 paper_sim → KPI。

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

> 权威清单 = `backend/config/database_manifest.yaml` (含 retention_class 生命周期分类, 见 db_management_design §13)。
| DB | 路径 | 用途 | retention_class |
|---|---|---|---|
| `smartmoney.duckdb` | `data/smartmoney.duckdb` | **2.5G / 85表** (2026-06-14 地基-reset: 删整个模型/特征/寻优层144表, 只留基础数据+纯K线中间+档案展示+治理; 26.6→2.5G; 参数寻优重做; 退役实验知识→config/experiments/retired_experiments.yaml) | production_control(地基) |
| `market.duckdb` | `data/market.duckdb` | K 线 + 行情 (`v_price_kline_qfq`) | canonical_source |
| `tushare_raw.duckdb` | `data/tushare_raw.duckdb` | TuShare raw 镜像 (raw_tushare_*), sync_runner 独占写, 写锁隔离 | canonical_source (mirror) |
| `alpha158.duckdb` | (planned, 旧panel 2026-06-14 删) | qlib Alpha158 K线因子库; 旧 panel(418万行/3.5G, PIT不可信)删, 验证Alpha158时干净重算+pit_guard核证 (manifest planned; daily_update Step2c重建循环已切) | rebuildable_feature |
| `etf.duckdb` | `data/etf.duckdb` | ETF 专用 | governed_source |
| `experiment_store.duckdb` | active (S0 建, 执行器接入) | alpha 验证实验输出 (verdict/IC scan/lineage/pit_audit), 与 live 隔离; 写入器=experiment_consumer_alpha_validation.py | transient_experiment |
| `data/scratch/*.duckdb` | (约定) | 测试/探索一次性库, 用完即删, gitignore | disposable_scratch |

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
| `v_price_kline_qfq` (market.duckdb) 含指数 K 线 | 5.97M 行 / 6,618 股 / 2022-01 → 2026-05 | 实时 | tdxhub 备援源视图; 只 2022+ 且 2022-12-30 复权 glitch; 回测主源已切 ↓ |
| `price_kline_qfq_tushare` (market.duckdb) **回测前复权主源** | 856万行 / 5755 股 / **2019-01 → 2026-06** | build_price_kline_qfq_tushare.py | 2026-06-15 §4.3 消费链切换: raw_tushare_daily×adj_factor 前复权(rebased, 单位对齐tdxhub); 与tdxhub重叠期收益对账 avg 0.03%一致(max差=tdxhub 2022-12-30 glitch, tushare正确); load_kline 已 repoint; 解锁2020+多regime回测; data_layers=L1k |
| `fact_feature_panel` (**feature_store.duckdb** L2) **LIVE** | **8,173,577 行 / 5427股 / 2019-01-30~2026-06-12** | build_feature_panel.py (services.data_loaders + formula_engine 5因子; pit_guard 物化门) | 2026-06-19 A0 重建物化: mom_60/reversal_20/vol_20/mf_trend_20/roe_dt_asof PIT 宽表。**对抗验证**: universe 0违规 / 0 NaN-inf / 独立重算 600519 1784日 0泄漏 / **entry点JOIN覆盖正负样本100%**(命中点 mom100%/mf83%/roe45%)。roe/mom 尾值留 D 阶段 winsorize; data_layers=L2_feature live |
| `fact_segment_panel` (**feature_store.duckdb** L2) **形态/分层面板** | (重建中) | build_segment_panel.py (+config segment_panel.yaml) | 直读 price_kline_qfq_tushare 复用 classify_technical_stage 物化 PIT 形态轴: stage(Weinstein5态)+range_pos+dif/dea/macd_hist/macd_above_zero+board; forward 不入表(防 outcome-as-feature); Arrow批插。判别力结论=unknown(逐数据 alpha 验证重做, 见 goal.md); data_layers=L2_feature |
| `fact_rally_ground_truth` (**smartmoney.duckdb** L1) **D1 主升浪 ground truth 标签y** | **9,070 主升浪 / 4,347股 / 2019+** | build_rally_ground_truth.py (services.universe 硬门 clean) | 2026-06-17 结构型重建(用户图样型, episode-first D1 锚): 底→顶>60% + 长底 + 多头排列(MA5>10>20>30>60) + 平滑(途中max_dd>-30%), 排北交所/ST/退市(assert_universe_clean); event_date=底(bottom_date PIT锚, 特征<=t/label后验); 下游 D2-D4 因子判别力=unknown(逐数据 alpha 验证待跑); data_layers=L1_foundation |
| `fact_macd_episode_ground_truth` (**smartmoney.duckdb** L1) **D1 MACD金叉峰值 ground truth** | **311,291 episode / 5,197股** | build_macd_episode_ground_truth.py (services.universe 硬门 clean) | 2026-06-17 重建(用户口径: 金叉=买点, 卖点=金叉后波峰探索): 金叉峰值>30%=is_win; peak_gain_pct/peak_offset_days/max_dd_pct; 出场规则判别力=unknown(逐数据验证); data_layers=L1_foundation |
| `fact_rally_entry_pit` (**smartmoney.duckdb** L1) **GT entry-PIT 侧 (标签拆)** | **9,070 episode / 4,347股 / fwd_complete 90.5%** | build_rally_entry_pit.py (+契约 config/rally_gt_columns.yaml + 守卫 services/rally_labels.py) | 2026-06-19 A0#c: 从 GT 剥出 entry-PIT 侧防 outcome 当训练 X=leakage。entry_signal_date=bottom_date(PIT锚, JOIN fact_feature_panel 键) + base_days(唯一 PIT 入场特征) + **fwd_complete**(bottom+250交易日是否<=数据边缘2026-06-12, False=右删失全在2025/2026); **outcome 列(gain/peak/dd/bull_aligned)不入此表**留 GT 表禁做X (rally_labels.assert_no_outcome_leakage 守门, 单测 red→green)。陷阱: bull_aligned 拉升期测=forward 非入场态; data_layers=L1_foundation |
| `fact_rally_entry_negative` (**smartmoney.duckdb** L1) **GT hard-negative 对照组** | **35,198 / 4,846股 / pos:neg≈1:3.9** | build_rally_negatives.py (+共享原语 services/rally_detect.py) | 2026-06-19 A0#d: 结果倒推判别器对照组。框架=**hard-negative**(holding PIT-setup恒定隔离涨不涨信号, 非全市场随机): 同结构 pivot-low + 长底(base>=40, 与正样本同 PIT setup) + fwd_complete + **未涨**(forward gain<60%) + purge 同股正样本±250根。无锁生成(K线 market + GT/日历 smartmoney, 不碰 tushare_raw); ST 留消费侧 PIT 硬门(is_st_on)。验证: 0正负重叠/0北交所/0 outcome列/base_days min40。下游 UNION fact_rally_entry_pit(y=1)训练; data_layers=L1_foundation |
| `fact_rally_episode_strata` (**smartmoney.duckdb** L1) **episode PIT 分层** | **9,070 episode** | build_rally_episode_strata.py | 2026-06-20 C#48: episode 按 PIT 维分层(可live conditioning, 非outcome): **申万sector** as-of join index_member_all(in/out_date PIT, is_new Y+N避§4.5 latest-snapshot)覆盖99% + **市值** daily_basic 底日total_mv 覆盖100%(daily_basic 回补2019对齐 K线/GT, data_start 20190102)→微/小/中/大盘桶 + **base_days** 短/中/长底桶。分布: 小盘+微盘60%+(主升浪小盘主导), 机械/化工/电子/医药板块聚集。form/gain=outcome留GT join不入表; data_layers=L1_foundation |
| `fact_rally_stage` (**smartmoney.duckdb** L1) **episode 阶段切分(鱼头/鱼身/鱼尾)** | **1,507,894 行 / 9070 episode** | build_rally_stage.py (+config rally_stage.yaml, +单测 test_rally_stage) | 2026-06-20 C#48 step2 (用户核心缺口"没研究鱼头鱼尾"): 每 episode [底,峰] per-date 切 **起涨/主升/顶部**, progress=(close-底)/(峰-底) 首次跨阈(launch_end0.30/main_end0.85, pre-reg config)划连续时间段(单调防pullback错标)。分布 主升50%/起涨42%/**顶部仅8%**(底后慢启动+近峰快冲顶=入场窗宽/出场窗窄, 契合出场最重要)。stage=POST-HOC(依赖peak)分析用非live; 跨库 join feature_panel 三阶段100%命中→D 按stage查PIT因子; data_layers=L1_foundation |
| `raw_tushare_moneyflow` (tushare_raw) | **738万行 / 5620股 / 2020-01-02→2026-06-12 [DONE]** | sync_runner --domain moneyflow --backfill | 2026-06-15 用户"拉齐2020"回补完成: data_start 20220104→20200101 + min_rows 4000→3000(2020 universe~3740股); .venv/bin/python + source .env (env PATH双前提) |
| `raw_tushare_moneyflow_dc` (tushare_raw) **东财个股资金流** | **384万行 / 6219股 / 2023-09→2026-06 / 665日 [DONE]** | sync_runner --domain moneyflow_dc --backfill | 2026-06-16 用户"全拉初评有用数据": net_amount/net_amount_rate 东财口径(补 order-size moneyflow); 实测起点~2023-10(东财个股近年才有, data_start 20230901, 前置~20空日 ok:false 但真数据完整); rate=分钟级150/200无日上限 |
| `raw_tushare_index_member_all` (tushare_raw) **申万行业 PIT** + `v_sw_industry_pit` 视图 | **7787行 (5847当前Y + 1940历史剔除N) / out_date填1940 / 同股多区间1609** | sync_runner --domain index_member_all(_hist) + build_sw_industry_view.py | 2026-06-15/16 **行业迁移 S1+S2**: S1 原只拉 is_new='Y' → out_date 100% NULL = latest-snapshot leakage; 加 `index_member_all_hist` 域 (is_new='N' 补真 PIT 区间)。**S2** 建 `v_sw_industry_pit` as-of 视图。**S3** [DONE 2026-06-16] live serving 切申万: build_sw_industry_view.py 加建 smartmoney `dim_stock_sw_industry` 当前快照(5847股, tdx_l* 列名=位置别名/值申万, L1_foundation); industry.py INDUSTRY_TABLE→dim_stock_sw_industry; signals_v2 7 处 JOIN 走 {INDUSTRY_TABLE} 常量(no-hardcode); resolve_industry ref_date 缺陷标注(serving=当前, as-of走视图)。验证 59测试pass/moth32/load_industry_map返申万。**S4** [DONE] 删 STALE 孤儿 mart_stock_industry_pit+quality (4消费者全guard降级/移声明/residue0)。**S6** 初次双轨: 申万5847>通达信5624股, taxonomy不同非系统错位(迁移sound)。**S7** 通达信降tdxhub热备不物删(§4.3)。**迁移功能完成** (serving+探索+KPI全申万PIT)。剩跟进: S6完整1周/S8 index_classify/申万readiness面板重建 (owner=analysis/industry_migration_tdx_to_sw_20260615.md; 06-11 ANOVA 已定 申万L2 主口径)。taxonomy 桶 13→31 历史不可比 (§4.5) |
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
| `raw_aif10_valuation_quantile.percentile_fifty` | 估值 10Y 分位 (aif10 源, task#37 待迁/退役; 原 strategy_ensemble 消费者已退役 2026-06-19) |
| `raw_aif10_forecast_consensus.compre_rating_num` | 一致预期评分 (aif10 源, task#37 待迁/退役; 原 strategy_ensemble 消费者已退役 2026-06-19) |
| `raw_aif10_peer_valuation` | 同业估值 |
| **`raw_tushare_forecast`** (业绩预告, 2026-06-14 接入) | **PEAD 预期差事件因子** (alpha 验证程序 S1 第一个基本面接口): type(预增/预减/扭亏/首亏) + p_change_min/max(净利变动幅度) + net_profit_min/max + ann_date(PIT 锚, 早于正式财报). grain=[ts_code,end_date,ann_date]; 实测 17042 行 (2023-2026) |
| **`raw_tushare_income`** (正式利润表, 2026-06-14 接入) | 96 列全套利润表 (total_revenue/revenue/oper_cost/各费用/operate_profit/n_income/ebit/ebitda...) = 质量/成长因子料 (PEAD 后段慢信号). grain=[ts_code,end_date,f_ann_date,update_flag] (uf=0原始/1订正双推送), PIT 锚 f_ann_date 取 uf=1; by_trade_date date_param=ann_date; 实测 4月 10578 行/5305 股. **express/fina_indicator 已注册** (express=express_vip by_period [sync_runner 加 by_period 分支+单测]; fina_indicator=by_ts_code 2023-2026窗口避100条截断), 回填排队 income 后 (单写锁) |
| **`raw_tushare_balancesheet_advrecv`** (预收账款/合同负债, 2026-06-16 注册) | 用户提议"预收账款"前瞻需求因子: adv_receipts + contract_liab (2020 后迁入) + total_assets. PIT 锚 ann_date; by_period (V0 取每期最新修订). **当前落库 7 期非连续止 2020Q3 = 不可用** (allow_empty=true 旧配置静默吃间歇空响应 + 配额墙截断双因); 已配 allow_empty=false + min_rows_per_batch=1000, 待配额恢复重拉连续季报. debate 裁决档C: 修源前禁入 panel |

### 2.5 资金流 / 事件

| 表 | 内容 |
|---|---|
| `fact_hsgt_daily` | 北向资金 daily |
| `raw_lhb_daily` / `fact_lhb_event` | 龙虎榜 |
| `fact_executive_trade_event` | 高管增减持 |
| `fact_shareholder_trade` / `fact_shareholder_trade_tdx_b` | 股东交易 |
| `fact_holder_event` / `fact_top10_holder_period` / `fact_holder_count_period` | 持股人结构 |
| `fact_dzjy_event` | 大宗交易 (旧源) |
| **`raw_tushare_block_trade`** (大宗交易, 2026-06-16 注册) | 用户提议: 机构折价/大单方向, stage 内 alpha 增强候选 (moneyflow 抓不到的机构维度). grain=[ts_code,trade_date,price,vol] (同股同日多笔全留), PIT 锚 trade_date (盘后披露, 决策用 t-1); by_trade_date 2023+. **表未建** (配额墙), 配置就绪待拉. debate 裁决档B: 做事件 confirmation 不做连续因子 |
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
| | ddl.py | mart_per_stock_*_optimal / mart_per_formula_stage_optimal / fact_optuna_governance_log DDL; log_governance_violations(**manage_txn**: False=与业务表同事务原子提交防 orphan governance, 06-14 D0 发现) |
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
| **reversal_1m_deep** (Phase ψ.α) | reversal_short_term.py | **反转** (20 日跌 10-30%) — 验证结论=unknown (reset 清, 以 goal.md 为准) |
| **reversal_1w** (Phase ψ.α) | reversal_short_term.py | **反转** (5 日跌 2-10%) |
| technical_stage (4 stage) | technical_stage.py | classify_technical_stage(closes, volumes) — Stan Weinstein |

### 3.4 Paper Sim v2 (Phase ψ)

| 模块 | 作用 |
|---|---|
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
| `services/data_loaders.py` | **feature_panel 物化输入层** (2026-06-19 A0: 从已删 experiment_* 移进 services, 可注入 conn 可测): `load_kline`(L1k market price_kline_qfq_tushare)/`load_moneyflow`(L0 raw_tushare_moneyflow, net+total_flow)/`load_quality_reports`(L0 raw_tushare_fina_indicator, ann_date ISO PIT 锚)/`in_active_universe`(services.universe config 驱动替内联前缀)。分层契约: build 唯一 L0-read 点, 探索只读物化后 panel (L2-bypass lesson) |

### 3.7 数据源 / 客户端 / sync

| 模块 | 作用 |
|---|---|
| `services/data_sources/` | base / clients_registry / data_routes / fallback / registry — 数据源中央。**tushare 代理网关 2026-06-17 切 tinyshare** (旧 jiaoch.site 反刷量墙弃用): `sources/tushare.py:_pro_api` = `import tinyshare as ts; ts.set_token(授权码); ts.pro_api()` (tinyshare 自带网关, 去 _DataApi__http_url monkeypatch); 授权码进 gitignored .env (TUSHARE_TOKEN); 旧网关解封 stk_surv 机构调研(实测 316 行/日)。sync_registry 已注册 stk_surv 域。**限流(tinyshare): 单接口 120次/分, 多接口 200次/分, 并发 2**(旧 tushare 150/200)。**强制 = config 驱动主动节流**(2026-06-19): 限额在 `sync_registry.yaml defaults.rate_limit`, `sync_runner._RateLimiter` 读 config 每次 fetch_raw 前滑窗节流(撞墙前先睡, no-hardcode 改限额只动 yaml); 瞬态限流措辞退避 `_is_transient_ratelimit`→transient_backoff 作兜底, 真当日墙 `_is_quota_wall` 才停链。**socket 超时根治 hung** (2026-06-19, defaults.fetch_timeout_seconds=120; run_domain `socket.setdefaulttimeout`; 反例: stk_factor_pro 重试 hung 在无超时 socket 71min)。**by_ts_code 断点续拉** `--resume` (`_existing_ts_codes` 跳 target 已有 ts_code, 省重拉)。owner doc=`sources/tushare.py` docstring |
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
| `build_*` | 49 | build_formula_signals_history, build_signal_context, build_stock_formula_buy_signal_daily, build_daily_position_recommendations, build_picture_daily, build_architecture_inventory |
| `formula_*` | 1 | **formula_limit_up_pullback.py** (涨停回调十字星选股, S/A/B 三档, YAML 配置 `config/formula_limit_up_pullback.yaml`) |
| `run_*` | 17 | run_follow_backtest (机构跟随), run_optuna_*, run_portfolio_mvp |
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
| `backend/config/field_dictionary.yaml` | **Phase ψ.γ.dict.1** 字段字典 (3 DB × 12 核心表 × 100+ 字段 + 单位 + PIT key + outlier cap + JOIN 模板) — 防 VWAP unit bug 类故障 |
| `backend/config/recommendation_universe.yaml` | 选股宇宙 |
| `backend/config/db_partition_tiers.yaml` | **DB 多库分区 tier** (源/特征/服务/实验) + 原子写簇 (关联性检查); 驱动 `backend/scripts/db_partition_migrate.py` (保真迁移引擎: 原 DDL 含 PK + INSERT SELECT, 非 CTAS; dry-run 默认 + 前后验证[行数/EXCEPT/约束/索引] + 绝不 DROP 源; D1a experiment_store 25 表迁验 PASS [暂缓 repoint, live 耦合重]; **D2-minimal feature_store 2 表 fact_feature_panel+validation 迁验 PASS** [解决 build_feature_panel vs daily_update 写锁竞争, repoint 待定]) — owner=analysis/db_management_design_20260614.md |
| `backend/scripts/db_compact.py` | **整库保真缩盘** (删行后回收盘): ATTACH-copy 逐表原 DDL 含 PK + INSERT + 重建索引 + 视图按定义重建 (依赖容忍重试), **绝不 CTAS** (避 06-12 约束 315→1); dry-run 默认; 验证前 DETACH src (information_schema/约束/索引跨 attach 库会双计) + 逐表行数对账全等才换名, 旧库留 `_precompact_bak`。2026-06-14 实测 smartmoney 26.6G→17.5G (-34%, 333表/4视图/821约束/333索引全等) — owner=db_management_design §13.4 |
| `backend/scripts/db_dead_table_audit.py` | **死表守门** (0行 AND 0字面引用才判死, 保守防误删); 大表过时判定走 lifecycle 分析非本工具 — owner=db_management_design §12 |
| `backend/scripts/db_lifecycle_delete.py` | **生命周期删除执行器** (可复用): 读删除 manifest, 4 道闸 — (1) live守护 word-boundary grep daily_update脚本集+serving/ensemble/routers, 命中REFUSE (`--force` 跳过用于有意删 live 层如地基-reset); (2) action=archive 先 COPY parquet 再删 (drop 则不归档); (3) mart_data_deletion_record 留痕; (4) 残留扫描悬挂视图 + view 处理 + 周期 CHECKPOINT 防 catalog stale。dry-run默认。2026-06-14 地基-reset 删 144 表/视图 — owner=db_management_design §13.6 |
| `backend/config/data_layers.yaml` + `backend/scripts/data_layer_audit.py` + `backend/services/schema_layer_filter.py` | **数据层级框架** (2026-06-14 地基-reset 后立, owner=docs/data_management_framework.md): 8层声明式注册表(L0_source/L1_foundation/L1k_kline/display/infra/L2_feature/L3_model/L4_experiment), 144表全声明layer=单一真相源根治"层级隐式→反复推导+耦合"; audit `--check` 未声明=FAIL强制新表声明; schema_layer_filter 让 schema-init 只建活层表(梳理"删表后启动空重建"recreation loop, 接 schema_core/marts/migrations); moth 断言 data-layer-integrity/minimal-module-main-routers/no-new-godfile 自动执法。**2026-06-15 扩 feature_store 纳管**: audit `_live_tables` 从只扫 smartmoney → 扫 MANAGED_DBS=(smartmoney,feature_store), 否则 L2 分区(fact_feature_panel)静默不受层级执法; fact_feature_panel 声明 L2_feature, L2 层 status partial_rebuild |
| `backend/scripts/check_legacy_flow_integrity.py` | **老流程污染防回潮 gate** (2026-06-14 工具化 reset 6 教训, owner=framework §6): C1 daily_update 无缺失脚本调用(删层必删caller, 防静默degraded假活)/C2 无 wiped 表孤儿引用(238处实测)/C3 append-only(*_history/*_snapshot)必 storage_retention 声明(防无界膨胀=DB巨大根因)。覆盖 schema_layer_filter 之外的污染面(daily_update/散落DDL/config)。moth `legacy-flow-no-pollution` 守; **重构验收 gate**: 重构前红=问题实锤, 老daily_update退役+清孤儿+加retention 转绿。进度 (owner=analysis/refactor_execution_plan_20260614.md): 2026-06-14 A2 完成→**C3 append_only_retention PASS** (3表 retention 声明: dim_stock_tdx_industry_history/raw_profit_forecast_snapshot_daily/raw_tdx_industry_file_snapshot); A3d gate 精度修 (grep 加 -w 词边界防 substring 假阳性[fact_shareholder_plan 误匹配活表 _tdx_f10] + -I/--exclude-dir 跳 __pycache__ 二进制, 238→179); A3a 删 2 真孤儿 config (model_search/champion_registry)→C2 残 149; **A1 daily_update 重写 855→457 行 (删 Step4-8 model/paper_sim/champion + 19 缺失脚本调用, 保留 sync/L1k macd + 加 retention plan/data-health report; DRY 实跑通过)→C1 PASS**; A3b 退役 7 死 serving router (v3_market_perception bundled fallback / recommendation / institution / screening / v3_meta / v3_views / v3_perception_legacy + main.py 注册, app import OK 124 routes)→**C2 149→70**; A3c schema_versions 删 23 wiped version 条目 (版本注册表非 DDL; import/summary 验证 220→195) + 7 config 18 处 wiped ref 加 @archived 标记 (gate 认可豁免; 表均核实 wiped+DB 不存在; yaml 全 valid)→**C2 70→29**; 余 updater_* 死 feature 步骤(29, 子系统被 data_sources/etf live import 故外科清非整体退役) 待。A5 bloat: 删 phase5.duckdb 57M+phase5_exports 101M 死 model 工件 + manifest 去 phase5 分区; archive/ 3.4G reset 回滚网保留待重建 KPI 验证后用户定。**2026-06-15 C2 gate 修(重建表识别)**: `_live_tables`(复用 data_layer_audit, managed-DB live 集) 排除已重建为 live 的 wiped 层表 — fact_feature_panel 重建后 layer 仍 L2 但已 live, 不再误判孤儿(否则其 manifest/config 引用刷爆 stale 41>29); C2 stale 28<=29 ratchet PASS |
| `backend/scripts/check_strategy_validation_integrity.py` | **策略验证完整性 gate** (2026-06-15 P0 制度先行, 8-lens 对抗复审根因 R1/R2 + 判断法典 C-WinReturn 反哺; owner=docs/strategy_validation_contract.md 判断法典节): 4 检查 — anomaly_symmetric(C-R1: experiment_harness 有 tradability_verdict 对称门)/promotion_needs_money(C-R1: record_verdict 拒无含成本证据转正)/kpi_joint_codex(C-WinReturn: kpi_verdict 联合年化+max_dd+胜率×盈亏比)/engine_execution_aware(C-R2: 单一引擎含涨跌停/非对称成本/容量/T+1 open)。验证器纪律 mythos §13: 引擎检查取单文件全维满足非多文件并集 (防旧 portfolio_backtest 残留 marker 污染)。**P0 gate=P1 引擎验收尺**: engine 检查在引擎重建前 FAIL=预期红色规格。moth `validation-r1-symmetric-gate`/`validation-promotion-needs-money`/`validation-winreturn-codex` 守 |
| `backend/scripts/audit_panel_leakage.py` + `backend/config/leakage_consumers.yaml` | **泄漏审计 + 消费者注册表** (2026-06-15 post-reset 去硬编码): audit_panel_leakage 原硬编码 default 目标 (mart_p0a_v4[wiped]/build_feature_panel_duck.py[已删]) → **改 config 驱动** (读 leakage_consumers.yaml `audit_panels`, 空=PASS 无幻影审计; CLI `--panel` 仍可显式覆盖), 修"能不硬编码就不硬编码"违纪 + 解 Step3.5 幻影 BLOCK。leakage_consumers post-reset 对账: `consumers: []`(3 历史消费者脚本+面板 reset 全删)/`audit_panels: []`(旧SQL面板已wipe; fact_feature_panel 是 Python builder+code/date schema, SQL-JOIN 审计不适用)。experiment-discipline moth 门加识别 phaseD_signal_eval.evaluate_signal(共享 harness 满足留档+anomaly)+check_split_discipline(leakage门)。待: dim_stock_tdx_industry 非PIT(通达信)→tushare申万PIT 行业迁移 |
| `backend/services/sandbox_guard.py` + `scripts/sandbox.sh` + `sandbox/` | **探索沙盒边界硬门** (2026-06-17 用户根治探索散进主代码; owner=sandbox/README.md + engineering_governance §Exploration Sandbox): enable_sandbox_guard() monkeypatch duckdb.connect 挡 rw 开主6库 raise SandboxBoundaryError (审计实测沙脚本曾裸连写 market 库) + read_only_main(只读正路) + sandbox_scratch(per-exp); sandbox.sh new/wipe/check (probe 模板带 guard); sandbox/ gitignored 用完删, 唯一跨删存活=experiment_store verdict. moth exploration-isolated-in-sandbox/sandbox-boundary-guard-present 守; test_sandbox_guard 5 测 |
| `backend/scripts/check_sandbox_isolation.py` | **沙盒隔离门 — 实验室产物只留实验室** (2026-06-21 立, 4+次隔离失守根治: sandbox脚本隔离了但产物[主库表/builder/控制面KPI/裁决]在方法确认前 promote 进主项目=污染): C1 backend引用sandbox(FAIL) / C2 控制面文档嵌未promote(confirmed_by_owner=0)实验结果(WARN) / C3 探索runner漏主脚本(FAIL)。wired into sandbox.sh check + safe_commit Step 3.8; test_check_sandbox_isolation 3测(含C2 red→green); promotion纪律 owner=sandbox/README.md |
| `docs/stock_dossier_master_design.md` | **股票档案系统 顶层设计 (立法 owner; 2026-06-21 用户"顶层设计统筹规划")**: 给任一股多维度可解读档案(形态/板块概念/资金/筹码/…可无限叠加), 是主升浪猎手的**认识论地基**(看懂股→选股)。核心抽象=**维度解读器协议**(interpret/series/compare/screen+config, 加维度=加模块+config 不动框架); 创世层(感知/判断/谄媚死)+判断法典种子(J1人话参数/J2边界耦合同步/J3默认列表+趋势线/J4维度互不耦合); **后续维度数据底座全已在库**(申万PIT v_sw_industry_pit/东财概念dc_member/moneyflow/cyq_perf); 前端=FastAPI+HTML(趋势线非K线+板块对比叠加+交互调参+before/after叠加)。6阶段roadmap(P1形态成熟[声明式人话config+边界耦合]→P2前端→P3/4/5板块/资金/筹码→P6接回选股fact_rally_stage)。Verdict=PROCEED |
| `backend/services/technical_states/` + `config/technical_states.yaml` | **技术形态识别工具 (形态地基, 后续因子叠加的基础)** (2026-06-21 promote, 经 sandbox state_quantify v1/P0/P1/v3 验证: 全宇宙5421股各状态/子态语义准确+OOS稳+100%覆盖): 给任一股任一时点(日/周/月)识别技术状态+子态+量化特征。**9主态 (D1 下跌侧修复 2026-06-21, probe_v4_descent 重调 软可分0.816)**: 低位横盘/放量突破/上升通道/缩量上涨/**中继平台(新, 填pctile0.475-0.717 GAP, 8.4%)**/高位滞涨/下跌通道(改纯缩量阴跌)/**放量下跌(新, 用户点名缺态, 位置子态 高位出货/低位见底SC/中位延续)**/缩量回踩(瞬时近死态0.01%, 升势回踩=前序态依赖→D4上下文层接管暂占位)。**各自细分**(上升通道→温和/震荡/加速; 放量下跌→位置消歧)。**D2 子态全config驱动**(消除_sub_state硬编码, 评审critical): config 子态规则段(声明式{则,条件:[{指标,大于/小于:阈值名}]})+修饰指标段, classifier 通用evaluator按序匹配, 加/改子态=改config不动代码。**D3 A股涨停修正**(limits.py + config涨停段, 评审critical): 封涨停=缩量被vol_ratio误判'无量假突破'方向反 → raw_tushare_stk_limit硬真相源(up/down_limit已编码板块tier±10/20%不必分板路由)→ 涨停日设需求proxy量比使放量突破不误判 + is_up_limit/is_one_word描述标志 + 涨停突破子态(配config子态规则, classifier零改动); dossier.load_limits接入interpret_stock日线enrich; 实测000513涨停突破子态触发。**D4 上下文层(context.py 两遍架构, 评审决策点1)**: 缩量回踩瞬时死态(0.007%)本质=前序态依赖→pass-2用前序态(as-of≤t-1)refine: 前序窗口主导属升势态+当前mild回调→缩量回踩复活(真实股200股 8.53%); 标 prior_trend(升/平/跌)供位置消歧; refined_dominant=context_state或瞬时dominant。PIT三时点契约(decision_date=t用前序≤t-1+当前t无未来; 边界事件trigger/confirm分离立契约待D5)。dossier interpret_stock apply_context日线; test_context_pit_no_lookahead+test_context_revives_pullback。**D5a 单日K线形态(candles.py)**: 单根开高低收几何→命名(十字星/大阳大阴/锤子/上吊/倒锤/流星/纺锤/一字板); **位置消歧**(锤子=下跌末看多 vs 上吊=上涨末看空 同形, 用prior_trend区分)+ **A股一字板特判**(排假十字星); config单日形态阈值; dossier today_candle; 定位=短期构件非独立alpha。**D5b 命名形态(patterns.py)**: 态序列模板匹配(老鸭头=上升通道→缩量回踩→中继平台→放量突破出水; 圆弧底突破; 顶部派发转跌), 派生纯函数标签(不进软隶属/零独立参数), **PIT三时点(完成bar命中不回贴历史鸭头段)**, provenance主观性分级(中文实战/西方Bulkowski); config命名形态段加形态=改config不动代码; dossier recent_patterns; 实测000027老鸭头+圆弧底突破。**模块化收口**: __init__ 清晰公共API(__all__)+模块架构docstring(features/classifier/coupling/limits/context/candles/patterns 各单一职责)。**D6 前端**(dossier_view): 9态+前序趋势+单日K线+命名形态+RS badge 展示, 浏览器实测截图。**RS 相对强度维度(rs.py, 评审HIGH盲点)**: Mansfield RS 个股 vs 大盘(HS300 000300.SH 真相源 raw_tushare_index_daily)= 强于/弱于/同步大盘, 直服超额HS300>0 KPI(防纳入大盘普涨弱势齐涨股); 正交置信度维度不改7态; PIT(RS只用≤t); config RS段(基准/窗口可换中证500等); dossier.load_benchmark+rs字段; 实测600519强于大盘/000027弱于大盘。横截面RS rank(IBD)留选股层。test_relative_strength。**资金维度③(capital.py)**: moneyflow主力净额20日累计趋势(主力净流入/流出)+ daily_basic换手率自身分位(换手活跃/低迷); config资金段。**筹码维度④(chips.py)**: cyq_perf winner_rate获利盘(0-100量纲)+ 集中度((cost95-cost5)/cost50, 单峰集中/多峰分散)+ 价位(收盘vs weight_avg=获利/套牢)+ 获利盘20日变化(鱼尾派发预警); config筹码段。dossier load_capital/load_cyq + capital/chips字段 + 前端多维度卡; 实测600519主力净流出/低获利盘惜售 vs 000513主力净流入/单峰集中。**模块10个**(+capital+chips), DRY _iso helper。test_capital_and_chip_signals。**主力意图+量价背离(capital.capital_intent+zhuli_intent, 替代旧mingan_flow伪暗盘, 2026-06-21裁决)**: 旧TDX暗盘公式(X_8路径权重×中小单)=伪维度已砍(详见上"暗盘伪维度裁决"); 新设计=明盘(主力大单净net_amount)×价格 量价背离代理, 6象限主力意图(config主力意图段, 明盘×价格方向: 洗盘低吸/缩量阴跌/诱空吸筹建仓/拉高派发诱多/主力推升看多/主力做空出逃) + 量价背离标签(隐性承接/隐性派发/量价一致)。真暗盘需L2逐笔(need_027 order-flow BLOCKED无源)。dossier mingan字段+前端动向行(标日度近似)。test_capital_intent+test_zhuli_intent_minga_price(量价背离6象限)。**模块11个**。**主力资金口径裁决(reconcile wf_e6a0e9e8, 真金白银)**: tushare net_mf_amount **不是主力净额**(=厂商净主动流vol×VWAP, 跟中小单/动量, 与大单主力档常反向corr镜像); 真主力净额=capital.mainforce_net(elg+lg大单净)=东财dc.net_amount同构念(corr0.961)。修: capital_signals+mingan明盘统一走mainforce_net单一真相源(600519实测单日同源9891万); data_loaders docstring纠错(net_mf谎称lg+elg). **2层架构(用户)**: 基础层=价格形态/第二层=量价资金筹码(确认形态)。暗盘=日度粗近似(非真L2 L2_AMO).**同花顺截图核对(15股06-18, 用户暗盘追踪官方账号)**: tushare按供应商区分确认(moneyflow标准/moneyflow_dc东财/moneyflow_ths同花顺); tushare net_mf==moneyflow_ths(同源); 三套主力净额(net_mf/大单净/东财)+暗盘追踪明盘全不同口径(暗盘追踪=L2专有, 天孚ths0.45 vs 截图明盘7.22亿, 日度源复现不了)。主力资金**统一东财单一源 moneyflow_dc.net_amount**(用户决: 与项目概念=东财同源, flow-vendor=membership-vendor红线口径自洽; net_amount≡buy_elg+buy_lg大单净, buy_*为净额万元, 2023-09起)。**暗盘伪维度裁决(2026-06-21 measured, sandbox/mingan_redesign, 用户两轮质疑后双向对抗)**: 同花顺暗盘=L2逐笔专有, 日度moneyflow任何口径不可近似(净额零和镜像→中小单净≡−大单净54%随机; gross买入96%是假象=同花顺只发流入票选择偏差+中小单买入恒正平凡一致, 排序spearman0.283不相关, 全市场分位0/25无判别)。砍伪暗盘(旧mingan_flow X_8×中小单), 资金维度改 **明盘(主力大单净, 89%对齐同花顺明盘)×价格 量价背离代理**: capital_intent+zhuli_intent 6象限(洗盘低吸/缩量阴跌/诱空吸筹建仓/拉高派发诱多/主力推升看多/主力做空出逃, config主力意图段, 明盘×价格方向), 量价背离(隐性承接/隐性派发/量价一致)诚实代理非伪造暗盘金额; 三因子分离(明盘/背离/意图独立)。真暗盘需L2源(need_027 BLOCKED)。弃net_mf/tushare桶+双源flag(单源不需交叉, 去flag顺带消numpy.bool route500)。板块概念行业资金=东财moneyflow_ind_dc(在库,概念+行业, 与个股dc+概念dc_member全东财自洽)。东财数据经tushare API拉(网关聚合东财/同花顺/自有三套)。暗盘14截图标定: **路径权重X_8主驱动**(纯X_8 corr0.830, 换桶0.80几乎不变), 现公式0.806近最优, 误差2.6亿幅度不可复现, 诚实=暗盘方向≈当日K线路径函数与价格部分冗余。iFinD MCP无暗盘(模糊匹配主力净流入), 其主力净流入≠暗盘追踪明盘(同花顺两产品口径不通).**声明式人话 config (P1, J1)**: 状态**公式结构本身进 config**(状态: 人话条件列表 {指标,判断:高于/低于/平缓,阈值:人话单位如"均线斜率高于6.55%",锐度}), 加/改状态=改config不动代码; classifier.py 只持通用 evaluator(解释条件列表成 sigmoid 软门取积)+软隶属度+子态+多TF。**软隶属度**(softmax 温度, 一股可部分属多态+报熵)替 argmax(覆盖70→100%); **多时间框架**(日主+周/月确认 mtf_aligned, 窗口按TF缩放, _asof bisect); PIT(特征≤t, 量基准排当日)。**边界耦合 resolver (P1, J2; coupling.py)**: config 边界耦合 段声明状态间边界关联(上升退出↔下跌进入互补对称/低位<高位价格分位互斥/放量>缩量量比中性带), `apply_coupling` 调一个→镜像同步+人话变化说明, `with_overrides` 产 effective config 供 before/after 重分类, `list_tunables` 枚举可调边界(前端滑块来源)。API: compute/classify_bar/classify_series/classify_stock/classify_multi_timeframe/apply_coupling/with_overrides/list_tunables。test_technical_states 8测(PIT无前瞻+声明式evaluator语义+J2耦合镜像/override/枚举)。真实股600800回归100%covered/7态合理/多TF186天一致; J2 demo放宽斜率阈→上升通道180→296天。待: per-TF tune(现周月用日线参数=近似)+物化fact_stock_technical_state+FSM破位门 |
| `backend/services/dossier.py` + `backend/routers/dossier.py` + `routers/static/dossier_view.html` | **股票档案前端视图 (P2; 维度①form 解读器 + FastAPI + 自包含HTML)** (2026-06-21, owner=docs/stock_dossier_master_design.md): form 维度解读器 (interpret_stock 单股多TF解读+趋势线+可调参数 / screen_pattern 列符合形态的股票 / trend_series 趋势线非K线按主态着色) + 路由 `/api/dossier/{stock,screen,tunables,view}` (注册 main.py) + 自包含 HTML 档案视图。**实现用户J3/J5/J2/前端要求**: 多TF解读卡(日/周/月状态+人话描述+子态) / 趋势线非K线(SVG分段着色 绿升红跌灰横盘) / 形态筛选列符合股票+mini趋势线 / 滑块调参(⇄标耦合) / 边界耦合同步+人话变化说明 / before-after叠加(实线浅+虚线亮)。J5诚实报冲突: 600800 日跌/周回踩/月升=多框矛盾并列不藏。**默认值/恢复/全体对比 (2026-06-21 用户要求+扩展)**: 默认值=config当前值(探索v2, 前端显版本+每滑块标默认), 恢复默认全部 + 点数值复位单项(resetOne) + 偏离默认高亮计数; 修改vs默认 单股趋势叠加(实线浅默认/虚线亮调整后)。**扩展覆盖盲点** (compare_distribution + /compare): 调参真实影响在**全体层面**非单股 — 扫200股双config分类报每形态股票数Δ+翻转股票+盲点提醒banner(Δ暴涨=阈值松涌入垃圾股); 实证 放宽上升通道斜率→镜像下跌通道松→下跌通道+11/低位横盘-10。性能: compare/screen只载近600日(非全史)scan200约10s。**浏览器实测**(preview)截图证。test_dossier 4测。待: 多套Optuna预设(用户要求, 进行中)+板块/资金/筹码维度卡(P3/4/5) |
| `backend/scripts/build_experiment_store.py` + `data/experiment_store.duckdb` | **S0 实验台留档基建** (alpha验证程序, owner=alpha_validation_program_spec §8): 隔离 L4 库 (与 live 写锁/数据隔离防污染) 4 留档表 — fact_experiment_verdict(verdict/prereg_hash/judges) / fact_consumer_alpha_ic_scan(data_snapshot×consumer×metric PIT as-of) / pipeline_artifact_lineage(input/output hash 防回溯泄漏) / experiment_pit_audit_log(每步PIT校验); manifest active。**实验三段纪律固化 (2026-06-15 用户)**: `services/experiment_store.py` 共享留档写入器 (每实验 import+调 record_ic_cells/record_verdict/record_pit_check/record_artifact, 路径走 manifest, 防散落JSON) + `services/experiment_harness.py` (leakage_gate 事前 pit_guard 行为门不过BLOCK / anomaly_verdict 事后 §4.2 红线标 pending_ablation 不直接用/弃) + moth `experiment-discipline-tooled` 强制每个算 OOS IC 的 experiment_*.py 三段全走 (缺任一 FAIL); 5 实验全 retrofit, IC cells 留档 16→101。**R1/R2 制度化加固 (2026-06-15 P0)**: experiment_harness 加 `tradability_verdict` (C-R1 对称门: IC>0 但含成本净收益≤0→IC_POSITIVE_BUT_UNTRADABLE, 补 anomaly 单边盲点) + `kpi_verdict` (C-WinReturn 联合门: 年化 AND max_dd AND 月胜率 AND 胜率×盈亏比期望, 胜率=诊断量); experiment_store.record_verdict 加 C-R1 转正 guard (`confirmed_by_owner=1` 无含成本证据 raise). **C-LEAK 转正门 + leakage 门去自批 (2026-06-15 用户拷问"自批skip=门是摆设")**: record_verdict 加 `_has_leakage_clean` guard (confirmed_by_owner=1 须带 leakage-clean 证据[judges 含 leakage_gate/pit_audit 显 clean] 否则 C-LEAK BLOCK — commit-skip 够不到的转正门强制) + phaseD_signal_eval 把 gate 带入 judges; safe_commit Step3.5/3.6 **移除 SKIP_LEAKAGE_AUDIT 自批逃生**改硬 exit (误报=修 verifier 非 skip, verifier-only commit 不触发门=无死锁); 防御纵深=commit硬门+转正门+CI(终极). moth `validation-promotion-needs-leakage-clean`/`leakage-gate-no-self-bypass`; red→green 测试 (money但无leakage→C-LEAK raise). **P2 阶梯 R1 加固 (2026-06-15)**: experiment_harness 加 `block_bootstrap_return_null` (N1 armory: 含成本持有期收益块自助 -> P(累计<=0), 与 rank 显著性正交的绝对收益 null); Gate2 (experiment_ablation_gate2) 两级转正 (N3: REAL_EDGE->STAT_EDGE_CONFIRMED 排序统计显著非 money, confirmed_by_owner=0, money 转正须 tier2) + cohort/top-K 绝对 forward 报告 (N1); cell-scan (experiment_layered_segment_ic) 加 DSR 多重比较去偏 (N17: n_trials=实际cell数, n_eff=n_days/horizon 重叠校正 N15)。25 单测 (test_experiment_harness_codex + test_portfolio_execbacktest). owner=docs/strategy_validation_contract.md 判断法典 |
| `backend/scripts/experiment_consumer_alpha_validation.py` + `backend/config/experiments/consumer_alpha_matrix.yaml` | **S0 consumer_alpha 验证执行器** (config 驱动, reset 后重建; 复用对象 optimization/walk_forward runner 已删故新建非复活 god-dispatcher): 读 (数据x消费者) 矩阵 yaml (6 候选→7 cell, 映射铁律 event/fundamental/chip/infra→feature_ic, technical→formula_signal) + `experiment_jobs.yaml` `consumer_alpha_validation` family 契约 → gate-before-run (plan().blocked_reasons) → 枚举 cell → S0 dry 空矩阵 (不写假IC) → 写 verdict/lineage/pit_audit 留档 + verdict JSON 落 analysis/。死亡条款守: 矩阵轴走config(判断死, moth `consumer-alpha-axes-in-config-not-code`)/prereg_hash+`--check-prereg`(谄媚死)/PIT每步落档(泄漏死)/dry不造假(估计死)。IC计算留 S3。`backend/services/experiment_jobs.py` 契约loader 同恢复 (337L薄/纯yaml校验/误删, 修4处悬空import) |
| `backend/config/experiments/formula_candidates.yaml` + `l0_bare_kline_baseline_spec_20260614.md(已删·重启清理)` | **L0 裸K线基准 + 公式候选库** (用户 2026-06-14: 公式全保留为 config 备选省重建 + 裸K线寻优最佳OOS参数作基准 + **不要过拟合**): 9 公式索引 (全 ohlcv_only, 信号参数 yaml 全幸存, 评估器 macd live/其余 recoverable@639e0dfb~1), active 子集 4 (防过拟合池子小) / 其余 candidate 待解锁; L0 spec 定义=walk-forward OOS 寻优最佳参数标尺, 防过拟合第一约束 (OOS选参/DSR/pre-reg/限维度/诚实报弱, 复用幸存 optuna_config.yaml 治理; moth `optuna-require-walk-forward`/`optuna-realistic-sharpe-cap`/`l0-baseline-pool-bounded` 固化); 待重建 walk_forward OOS 引擎+治理层 (reset 删) |
| `backend/services/portfolio_walk_forward/oos_ic.py` | **L0 walk-forward OOS RankIC 核心** (Tier-1 引擎心脏, reset 删 runner 后重建): 纯函数无 DB 耦合 — forward_returns(PIT前向收益,只用未来不回看)/cross_sectional_ic(单日截面 spearman, numpy rank 不依赖 scipy, 样本<3→None)/expanding_monthly_windows(R1: min_train6月/forward1月/min_total12月)/oos_rank_ic(只用 OOS test 聚合日度IC→oos_rank_ic+ic_ir, embargo_days 切窗末跨界天[对抗审计修死闸], ic_ir 无偏 ddof=1, 无足够窗→None标unknown)。防过拟合: 选参只看 OOS 不看 train; unknown 不当 0。两层引擎共享窗口+标注原语。14 单测 (red→green PIT + 审计回归 embargo/完整窗) 入 CI |
| `backend/services/formula_engine/features.py` | **裸K线公式→连续PIT特征提取器** (L0 Tier-1): active 4 公式从核心机制派生连续特征 (MACD柱/MA距离/Donchian通道位置/反转), param 驱动读 formula_*.yaml; feature[i] 只用 bars[:i+1] (PIT); warmup→None。**2026-06-19 A0-1**: 加 3 主升浪 stage 因子 (feature_momentum 鱼身延续/feature_moneyflow_trend 资金确认/feature_asof_quality 财务as-of), 从已删 experiment_* 恢复进 services **消除 build_feature_panel→experiment 倒挂** (单测 test_stage_factors 5 passed PIT边界) |
| `backend/services/portfolio_walk_forward/pit_guard.py` | **PIT 行为门** (防泄露固化, 黄金标准前瞻检测): feature[i] 对追加未来 bar 不变否则=lookahead泄漏; 公式无关, 抓任何 rolling/EMA/未来引用 bug; red→green 测验它能抓植入泄漏 |
| `backend/scripts/experiment_l0_baseline.py` | **L0 裸K线基准驱动** (Tier-1 RankIC): v_price_kline_qfq→PIT特征→前向收益→walk-forward OOS RankIC→experiment_store (consumer_id=L0_baseline_<formula>)。**防泄露 3 门固化内联** (门1 PIT行为/门2 切分纪律 check_split_discipline/门3 异常红线 check_metric_anomaly, 任一失败BLOCK, moth `l0-leakage-gates-wired` 反孤儿守)。默认参数=测量; **`--search` 寻参模式** (#17 已实现): 经 plan_validator 闸+search_formula 网格寻 best-OOS-params+DSR, 写 L0_search_*。pre-reg d80e8ce 冻结+grill 后 RUN; **标尺=reversal +0.064 (lookback=20)**, 寻参佐证默认近最优 |
| `backend/services/portfolio_execbacktest.py` + `backend/config/backtest_execution.yaml` + `backend/tests/test_portfolio_execbacktest.py` | **Tier-2 execution-aware 回测引擎 (2026-06-15 P1 重建; 旧 portfolio_returnbacktest[clean但 R2 缺陷:close 无条件成交]已删, 旧 portfolio_backtest.py[5-07]退役标P2)**: 根因 R2 "信号!=可交易头寸"修复 — T+1 **open** 入场(非close, N14) + 涨停一字板剔篮/跌停顺延(N8/N12) + **非对称成本栈**(卖方印花, N13, config 镜像 paper_sim_momentum tx_cost) + 停牌冻结(缺价不剔篮不归零, N11) + 容量诊断(参与度 vs ADV + 大单溢价, N10, 不编造冲击系数守 measured) + **仓位 policy**(equal/rank/inverse_vol + 空槽留现金 = 连续 exposure 雏形, N4/N6); 联合 metrics(年化/max_dd/sharpe/calmar/月胜率/段胜率/盈亏比/正期望, C-WinReturn)。微结构真相源=backtest_execution.yaml(涨跌停镜像 universe_rules/dim_price_limit_rules, 成本镜像 paper_sim_momentum, 防双真相源)。14 单测手算证伪门(T+1 open/一字板剔篮/非对称成本/停牌冻结/容量/sizing/联合metrics/config加载/**trailing多窗**)。**trailing_metrics (2026-06-15 用户)**: 分 近3/6/12/18/24月/3年/5年/全期 窗口报 年化+月胜率+max_dd, 看策略趋势衰减 (全期均值掩盖: mf_trend 全期+2.53% 但近3m -27%/近24m +14.6% = 近期失效); harness evaluate_signal 自动打印趋势表+入 json。moth `validation-engine-execution-aware`/`validation-integrity-gate-green` 守; gate check_strategy_validation_integrity 4/4 PASS。P3 含成本裁决具体回测数字 reset 清(见 goal.md); 留机制: IC≠可交易(R1)/R2 四类摩擦(T+1 open/一字板剔篮/非对称成本/容量) |
| `backend/services/optimization/` (deflated_sharpe/plan_validator/formula_param_search) + `backend/config/experiments/formula_search_spaces.yaml` | **L0 寻参治理层** (reset 后最小重建, 只 L0 RankIC 寻参所需非复活策略机器): deflated_sharpe(Bailey-LdP DSR 多重比较去过拟合, stdlib 替 scipy=erf+Acklam) + plan_validator(搜索空间非空闸, 防 29/34 白跑反例, 空→raise) + formula_param_search(网格穷举寻参, 目标 OOS RankIC, 只读 OOS, DSR deflate, 受 plan_validator 闸); search_spaces 小网格(每公式3-9组合=防过拟合)。moth `l0-search-governance-wired` 反孤儿守。寻参 RUN=task#17 (pre-reg+grill, 大计算 Modal) |
| `backend/config/experiments/retired_experiments.yaml` | **退役实验知识库** (实验模块 config 子目录): 模型/寻优层删全表时把"用了什么(字段族/年限/工具/结论)"留这替代留全表 (用户 2026-06-14: challenger 只留摘要不留全表); 参数寻优重做的历史参照; 14 子系统 (公式工厂/p0a-p0b/multidim/synergy/drift/paper_sim/stage-opt/horizon/market_perception/特征搜索/research_chains 等) |
| `backend/config/pipeline_performance_policy.yaml` | step budget 预算 |
| `backend/config/data_sources.yaml` | 数据源 |
| `backend/config/storage_retention.yaml` | 保留期 |
| `backend/config/pricing_label_policy.yaml` | 定价标签 |
| `backend/config/feature_registry.yaml` | 特征注册 |
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

### 数据 backfill / Optuna / paper_sim 运行手册

> **2026-06-14 地基-reset 移除**: 模型/特征/寻优/paper_sim 层 (build_signal_context /
> backfill_risk_factors / optimize_per_formula_stage / run_paper_sim_v2 等) 已删, 参数寻优从零重做。
> 数据获取 (raw/dim 同步) 走 `sync_runner` (sync_registry.yaml); 重建路线见 `goal.md` 重建路线 +
> `alpha_validation_program_spec_20260614.md(已删·重启清理)`。地基同步: `scripts/daily_update.sh` (手动)。

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

# 地基模块测试 (db/层级/同步)
cd backend && PYTHONPATH=. pytest tests/test_db.py tests/scripts/test_db_compact.py tests/test_source_watermarks.py -q
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

## 10. 已实测数据点

> 2026-06-17 清验证墓地: reset 前/污染期所有 OOS 数字 (reversal sharpe / per-stock×stage 表 / momentum) 已作废清除 — 建于已删模型/寻优层 + 污染 universe。当前实测态 = **unknown**; 结构型主升浪 GT 已重建, 逐数据 alpha 验证待跑。以 `goal.md` + `scripts/chunkyctl doctor --fast` 实测为准, 不引用文档旧数字 (CLAUDE §4.2)。

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

## 11.5 待办 / 当前 Phase

> 2026-06-17: reset 前的待办清单 / Performance Profile 跑批时间 / Phase ψ 进度 (绑定已删 build_signal_context / risk_factors / optimize_per_formula / paper_sim_v2 流水线) 已清除。当前阶段板 + backlog 以 `goal.md` Active Priority Board 为唯一真相源; 完成项 / 历史证据在 `analysis/project_state_ledger.md`。

---

## 13. 写本文档的源数据 (供刷新)

```sql
-- 项目自己维护的架构 inventory (smartmoney.duckdb)
SELECT * FROM mart_architecture_inventory_summary ORDER BY built_at DESC LIMIT 1;
SELECT * FROM mart_architecture_inventory_asset WHERE run_id = ?;
SELECT * FROM mart_data_health;
SELECT * FROM mart_data_source_watermark;
```

---

## 14. Session 增量更新日志 (已归档)

> 246 条历史增量已移至 `analysis/project_index_changelog_archive_20260611.md` (2026-06-11 文档治理)。
> 新增历史叙事写 `analysis/project_state_ledger.md`; 本文件只维护上方活索引与最近 7 天增量。

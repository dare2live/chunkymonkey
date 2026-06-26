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
- [ ] 单元1 增减持: feature_registry 删7声明 + seed/retired/data_layers/sync_registry 清 + schema DDL 删 + 物删 + PROJECT_INDEX 更正
- [ ] 单元2 户数: data_layers/storage_retention/panel_manifest/coverage 清 + schema DDL 删 + 物删 raw_tdx_f10_holder_count_history + fact_holder_count_period
- [ ] 单元3 十大股东raw: data_layers/storage_retention/seed/audit/data_routes 清 (派生 fact_top10_holder_period KEEP 100% aif10 不动) + schema DDL 删 + 物删 raw_tdx_f10_holder_research
- [ ] 单元6 xdxr热备: 撤 update_watermark_sla SLA门 + 退役 build_price_kline_tdxhub.py + 物删 price_kline_tdxhub_adjustment_event
- [ ] 单元7 server健康: 删 tdx_source ensure/DDL (随单元6 builder 退役) + 前端冒烟降级 + 物删 mart_tdx_server_health
- [ ] 单元4 财务簇: A1 (balancesheet backfill + financial_client 重写 + repoint compute.py:3041 + macd_optuna:121) → 物删 gpcw 5表
- [ ] 单元5 F10元数据: 解耦 build_fundamental_quarterly (保 fact_fundamental_quarterly writer) + 删 orphan test/registry + 物删 2 mart 表

## 要丢的不可再生数据 (诚实标)
- 增减持意向信号 (无任何源可重建)
- 户数 1997-2017 深史 (tushare 仅 2018+)

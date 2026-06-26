# 东财 (DC) 全套迁移方案

> **[状态校正 2026-06-26 doc治理]** **核心决策已被推翻, 全文按废弃读**: 本文锁定"全项目单一数据供应商 = 东财(DC), 删申万+通达信+同花顺" — **2026-06-24 用户明确推翻**: 现行 = **tushare 唯一源** (不是东财), 东财 aif10 (datacenter) 仅作**正式例外**用于 holder 主源/估值分位/QFII/机构持仓明细 (实测 tushare 这几类滞后~4个月), 其余全部 tushare。详 `analysis/miaoxiang_aif10_source_decision_20260624.md` + CLAUDE §4.3。保留本文仅为溯源"曾考虑全局东财"的决策弧, 勿当现行方案执行。

> 状态: analysis 草稿 (主会话 review 后才执行; 控制面方案文档)
> 日期: 2026-06-23
> Owner 决策 (已锁): **全项目单一数据供应商 = 东财 (DC)**; 行业=东财(=申万对齐), 概念=东财, 个股/行业/概念资金流=东财。删申万(SW 行业部分)+通达信(TDX 全部残留)+同花顺(THS 评估后弃, 仅 ths_hot)。
> 真相源: tushare_api_catalog.json + sync_registry.yaml + ws3am0fjp 4路调研 + 2026-06-23 东财实测核证。
> 红线: §4.1 PIT / §4.2 异常高数字 / §4.3 单一源+删源不删数据双轨(铁律11) / §4.5 latest-snapshot+taxonomy切源 / mio真金白银。
> 前置实测裁决见 [[project-industry-moneyflow-vendor-verdict]]: 同花顺无PIT出局; 东财行业=申万(区分度打平0.116); 概念流预测力两家IC≈0。

---

## 第 0 节 · TL;DR

| 维度 | 裁决 |
|---|---|
| 东财在库 | **[OK] 全在库, 零 backfill** — dc_member/dc_index/moneyflow_dc/moneyflow_ind_dc 已同步 (相比同花顺需全量fetch, 东财迁移成本极低) |
| 行业 level 干净度 | **[OK]** 按申万对齐名映射 → L1=31/L2=127/L3=334 (= 申万 31/131/337, 4未匹配), 干净三级 |
| 行业/概念覆盖 | **[OK]** 5211股 ≥ 5203活跃股 (carry-forward, 仅缺1只科创次新), 覆盖完整 |
| 资金流一致性 | **[OK]** 板块净额=Σ成分股净额 (实测), 个股万元/板块亿元需换算 |
| **深史行业 PIT** | **[WARN] 需用户定** — 东财 dc_member PIT 仅 2025+ (行业2025-05/概念2025-01); 2025前历史归属只有申万index_member_all(2005+, 同套桶) |
| 整体 | **可行**: 当前画像+2025+资金流全干净; 唯一决策点=深史(2025前)行业PIT用申万兜底还是接受2025+ |

**一句话**: 东财全套干净可迁 (全在库零backfill, 行业=申万同套桶且level干净, 覆盖全, 资金流自洽)。唯一需用户拍板: rally GT episode (2018-2025) 的 sector as-of 特征需 2025前行业归属, 东财只 2025+ → **要不要保留申万 index_member_all 的深PIT视图(同套桶)专供深史回测**, 还是纯东财接受行业历史只到2025。

---

## 第 1 节 · 深史行业 PIT 决策点 (唯一需拍板)

**事实**: 东财 dc_member 逐日快照仅 2025-01(概念)/2025-05(行业)起 (实测 257/243天)。2025前无东财成员历史 (项目2025才开始snapshot dc_member)。申万 index_member_all 有 in_date/out_date 原生深PIT(2005+), 且东财行业=申万(86/99%同套桶)。

**影响面**: 仅"深史 PIT 历史归属"消费方受影响——主要是 `fact_rally_episode_strata` 的 sector as-of join (用 v_sw_industry_pit 把 2018-2025 episode 起涨点的当时行业归属算出). 当前快照消费方 (sector_momentum/scoring/institution_l2_metrics/stock_graph_read/industry_context) 读"当前"行业 dim, 不受 PIT 深度影响 (东财当前快照覆盖全)。

**两个选项**:
| 选项 | 做法 | 代价 |
|---|---|---|
| **(A) 东财全套 + 申万深PIT视图兜底 (推荐)** | 当前行业dim + 资金流 + 概念全切东财; **唯一保留 `v_sw_industry_pit`(申万 index_member_all 深PIT视图)专供 episode_strata 深史 as-of**。东财行业=申万同套桶, 故这不是混口径(同一套桶, 只是2025前用申万深PIT源, 2025+东财一致)。 | 保留 1 个申万深PIT视图 (不算全删申万); 工程上是"东财全套 + 深史PIT单点兜底" |
| **(B) 纯东财, 行业历史只到2025** | 全删申万; episode_strata 的 2025前 sector特征→无PIT→报废或标unknown | 2025前 episode 的行业/sector特征丢失; rally GT 大部分在2025前 → sector维度回测残缺 |

> **mio真金白银倾向 (供参考)**: rally GT episode 是项目北极星的训练真相, 2025前占大头。选(B)等于砍掉这批episode的sector维度。选(A)只多留一个申万深PIT视图(同套桶, 非混口径), 守住深史回测又基本单一源(东财)。**推荐(A)**, 但用户拍板。

---

## 第 2 节 · 迁移分阶段 (可逆优先, 删最后; 东财在库故无backfill阶段)

### 阶段 ① 物化东财行业/概念 dim (核心新建)
- **`dim_stock_dc_industry`** (当前快照, serving): 从 dc_index(idx_type='行业板块') JOIN dc_member 取当前成分 → 列 `tdx_l1/tdx_l1_name/tdx_l2/.../tdx_l3_name`(位置别名兼容消费方零字段改, 值=东财行业)。**level 推导 = 按申万对齐名映射**(board name ∈ 申万L1/L2/L3名集 → 定级, 实测干净31/127/334)。落 smartmoney.duckdb。
- **`dim_stock_dc_concept`** (当前快照): type=概念板块 → 成分 (多对多)。
- **`v_dc_industry_pit`** (as-of PIT 视图): dc_member 逐日快照 → as-of t 取 MAX(trade_date)<=t (同 dc_member 模式); **仅 2025+ 有效**, 2025前返NULL。
- 选项(A)下: **保留 `v_sw_industry_pit`** 专供 episode_strata 深史 (2025前) as-of; 标注 "同东财桶的深PIT源"。

### 阶段 ② repoint 消费方 (申万 dim → 东财 dim)
> 当前 = `dim_stock_sw_industry`(f523b4d7刚切申万)。这次再切 dim_stock_dc_industry (列名兼容→纯表名swap, 同 f523b4d7 手法)。
| 消费方 | 改法 |
|---|---|
| industry.py (INDUSTRY_TABLE) | → dim_stock_dc_industry |
| industry_context_engine / institution_l2_metrics / scoring / sector_momentum / stock_graph_read | 表名swap (6个, +6测试fixture) |
| updater_institution.py:421-477 build_industry_stat JOIN | → dim_stock_dc_industry (f523b4d7漏的第7个) |
| daily_update.sh Step2.96c | build_sw_industry_view → build_dc_industry_view (物化东财dim) |
| 资金流消费方 | 多数已是东财(moneyflow_dc/capital.py); 核 raw_tushare_moneyflow_dc/ind_dc 全消费方口径一致 |
| episode_strata sector as-of | 选(A): 留v_sw_industry_pit; 选(B): 切v_dc_industry_pit(2025+) |

### 阶段 ③ watermark/gate/moth
- source_watermarks + update_watermark_sla: industry_sw → industry_dc (指 dim_stock_dc_industry.updated_at)
- storage_retention: +dc dim/snapshot; 清 tdx 条目
- moth: 加 dc industry/concept claims; 删 sw/tdx 断言

### 阶段 ④ 删申万(行业部分)+通达信残留+同花顺 (双轨/物删纪律)
> 铁律11: 删前双轨核对(东财行业 vs 申万行业 逐股一致率, 已知86/99%) + schema-init重建路径同切 + deletion_record + 0 residue 验证。
- **申万**: 删 dim_stock_sw_industry + build_sw_industry_view.py; **选(A)保留 index_member_all + v_sw_industry_pit(深史兜底); 选(B)全删**。注: index_member_all 兼KPI超额基准用途, 删前确认。sw_daily(行业指数行情)→ 评估是否被sector消费, 是则留或换dc_index。
- **通达信全部残留** (ws3am0fjp审计 + 第3节checklist): _step_sync_industry* / tdx_industry_client / tdx_industry_names / clients_registry tdx条目 / data_routes / updater_connectivity / audit plan step / updater_plan DAG钉死 / schema_core DDL(487-517必删防重建循环) / schema_migrations索引 / block_client / 冻结表 dim_stock_tdx_industry*。
- **同花顺**: 仅 ths_hot 在库, 评估退役(题材情绪未用)。

---

## 第 3 节 · 无残留 checklist (验收闸, 来自 ws3am0fjp fan-in + 通达信残留审计)

### 3.1 表
- [ ] 新建: dim_stock_dc_industry / dim_stock_dc_concept / v_dc_industry_pit
- [ ] 物删通达信: dim_stock_tdx_industry / _history / raw_tdx_industry_file_snapshot / dim_stock_tdx_block / dim_tdx_block_catalog
- [ ] 物删申万行业: dim_stock_sw_industry (+ 选B: v_sw_industry_pit / index_member_all行业部分 / sw_daily)
- [ ] 评估: index_member_all (KPI基准双用途 — 删行业部分前确认基准链)

### 3.2 代码
- [ ] repoint: industry.py / industry_context_engine / institution_l2_metrics / scoring / sector_momentum / stock_graph_read (+6测试fixture)
- [ ] repoint: updater_institution.py:421-477,532 (build_industry_stat JOIN)
- [ ] 退役整模块: tdx_industry_client.py / tdx_industry_names.py / block_client.py / build_sw_industry_view.py
- [ ] 退役router步: updater_institution.py:308-415 / updater.py:92,404-483 (_step_sync_industry*)
- [ ] 新建: build_dc_industry_view.py
- [ ] 注释翻转: akshare_client.py:9 / data_deprecation.py / data_routes.py

### 3.3 注册表/配置
- [ ] sync_registry: 确认dc域齐(已在库); 删/退役 sw行业域 + tdx相关
- [ ] clients_registry.py:62-89: 删 tdx_industry_client + block_client
- [ ] data_routes.py:49-68: 删/改 误配路由
- [ ] schema_core.py:487-517: 删 TDX DDL (防schema-init复活=重建循环)
- [ ] schema_migrations.py:298-300,596: 删 TDX 索引+drop plan
- [ ] updater_plan.py + audit.py: 删 sync_industry/build_industry_stat STEPS/HARD_DEPS/smart-plan (解DAG钉死)
- [ ] daily_update.sh: Step2.96c build_sw → build_dc

### 3.4 域/watermark/retention
- [ ] source_watermarks + update_watermark_sla: industry_sw → industry_dc
- [ ] storage_retention: +dc; 删tdx(204-208,239-246)
- [ ] mart_data_source_watermark: 删sw/tdx stale水位行

### 3.5 doc/test/控制面
- [ ] PROJECT_INDEX 活索引 / FEATURE_MAP重生 / goal.md若真相源变
- [ ] 测试fixture 6+ 改dc
- [ ] §4.5 反例: 沉淀"东财PIT仅2025+, 深史用申万同套桶兜底" + "taxonomy第三次切换(SW→DC, 但同套桶不算切)"
- [ ] codegraph sync + complexity双扫 + moth全绿

### 3.6 双轨核对artifact
- [ ] analysis/非tushare源_双轨_20260623.md: (删申万时)东财行业 vs 申万行业 逐股一致率≥99% (已知86/99%, 定位差异股)
- [ ] deletion_record: 每张物删表

---

## 第 4 节 · 风险

| 风险 | 缓解 |
|---|---|
| 东财PIT仅2025+ | 选(A)留申万深PIT视图(同套桶)兜底深史; 选(B)接受 |
| index_member_all双用途(行业+KPI基准) | 删行业部分前确认基准链不依赖 |
| schema_core TDX DDL不删→重建循环 | DDL同删(3.3) |
| 资金流单位(个股万元/板块亿元) | 消费方换算归一 |
| f523b4d7刚切申万→现切东财=churn | 列名兼容, 纯表名swap, 低风险; 但需重跑6测试 |
| 通达信残留多 | 第3节checklist逐项, 不漏(避免上次残留教训) |

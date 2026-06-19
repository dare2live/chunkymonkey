# Tushare 10000积分 选股潜力研究 — 拉取优先级 (2026-06-17)

> 状态: live (数据拉取决策依据)。owner: 本文件。
> 方法: 6 类并行评估 241 接口中 A股选股相关者 (catalog backend/config/tushare_api_catalog.json 真实字段),
> 对主升浪逐阶段 (起涨鱼头/主升鱼身/顶部鱼尾 + 分层 + regime) 潜力打分。
> 诚实先验偏重: **出场/持仓/分层/资金确认/板块轮动 > 纯买点预测** (买点≈噪音上限)。
> pulled 标记已交叉核对 32 已拉表 (agent 曾误标 top_list 未拉, 实际已拉 — 已修)。

## 口径一致性铁律 (用户 2026-06-17 再强调, owner=CLAUDE §4 坑库)

**flow vendor 必须 = membership vendor; 申万只做中性化; 禁同花顺第三套** (口径混用=leakage)。项目现用口径:
- **行业/板块 = 申万 (SW)**: dim_stock_sw_industry / index_member_all / v_sw_industry_pit (已拉)。行业资金流 = **个股 moneyflow(已拉) 按申万成分聚合**, 不拉别家行业资金流表 (东财 moneyflow_ind_dc 的行业口径≠申万, 只可用于东财概念链, 不可当申万行业流)。sw_daily(申万行业指数量价) 与此口径自洽 → 仍 P0。
- **概念 = 东财 (DC)**: dc_member / dc_index / moneyflow_ind_dc(content_type=concept) (已拉)。概念资金流走**东财链自洽**, 不需新拉 (我原"概念资金真空区"判断错: 东财 moneyflow_ind_dc 已覆盖概念, 仅 2025+ 历史短是覆盖限制非真空)。
- **禁同花顺 (THS) 第三套**: moneyflow_ths / moneyflow_ind_ths / moneyflow_cnt_ths / ths_member / ths_index / ths_daily 一律 **不拉** (引入第三 vendor = 与申万/东财口径冲突 = leakage)。

→ 本研究原 P1 的 moneyflow_cnt_ths / ths_member / ths_index **撤销, 移入 SKIP** (违口径一致)。

## 先纠偏 (用户премise)

用户说"筹码胜率/券商盈利预测没拉" — 实查 **cyq_perf(筹码 winner_rate)2018+ + report_rc(券商研报)2018+ 都在库**。
真相 = **不是没拉, 是没评估没用上** (本对话已清的 B 类探索用过但裁决都是污染期, 已废)。本研究重新评估
全量潜力 + 找出真高价值未拉项。

## P0 — 立即拉 (高潜力 + 性价比, 5 项)

| 接口 | 积分 | 周期 | 服务阶段 | 为什么 P0 |
|---|---|---|---|---|
| **stk_factor_pro** | 5000 | 全历史 | 全阶段(鱼身/鱼尾最强) | 一接口=261 技术因子库 (MA/EMA 5-250 / MACD / RSI / KDJ / DMI(adx) / ATR / OBV / updays/topdays/lowdays 趋势计数, 三复权口径)。后复权直喂特征面板, 是主升浪鱼身延续+鱼尾出场最直接燃料, 口径统一省自算 |
| **sw_daily** | 5000 | 申万2021版长史 | 分层 + 板块轮动 + regime | 申万行业日线量价 → sector_momentum / 行业相对强弱。**个股主升必须板块共振** = 高价值正交层。已拉 index_member_all(成分) 配齐, taxonomy_version=2021 对齐 |
| **share_float** | 120 | 前瞻公告 | 顶部出场 + 分层风控 | 罕见**天然前瞻 PIT** (解禁日提前公告, 决策时点已知未来=0泄露)。量化未来供给冲击做出场/风控 gate, 120 积分极低成本, 正中"偏重出场"先验 |
| **stk_holdernumber** | 600 | 季度(ann_date) | 起涨(鱼头) + 分层 | 户数下降=筹码集中=主力吸筹经典前兆, 正交价量。ann_date PIT 干净, 低积分高性价比。弱点: 季度滞后 → 做慢变量分层非择时 |
| **namechange** | 低 | start/end_date 区间 | universe 清洗 | 带 PIT 名称区间 = 构建**历史 ST 日历**真相源, 直接服务 universe 硬门 (反例: ST 须 PIT 日历非 dim_active 当前名)。与已拉 stock_st 交叉验证 |

## P1 — 高价值 (拉, 21 项中主力)

| 接口 | 积分 | 服务阶段 | 价值 |
|---|---|---|---|
| **margin_detail** | 2000 | 主升确认 + 顶部预警 | 个股级两融余额, Δ融资=杠杆资金确认 (龙虎榜只上榜日有, 融资**天天有**), 主升延续/见顶判别, t-1 PIT |
| **kpl_list** | 5000 | 起涨 + 主升 + 顶部 | 开盘啦: 封单额/竞价强度/连板 lu_desc/炸板 status = 连板梯队溢价与瓦解最直接量化, 横截面分层强, 正交价量 |
| **hm_detail** | 10000 | 起涨 + 主升 + 顶部 | 游资席位标签 = top_inst 营业部之上的语义增强, 游资驱动主升专属解释力 |
| **stk_holdertrade** | 2000 | 顶部出场 + 主升背书 | 大股东/高管增减持: **高位减持=撤退**做出场负向 gate(真金白银), ann_date PIT。当过滤层 |
| **top10_floatholders** | 2000 | 分层 + 主升确认 | 流通前十大=真实可抛压筹码, 机构进驻变化中频信号, 配 cyq |
| 概念资金流/分层 | — | 分层 | **走东财链 (moneyflow_ind_dc + dc_member, 已拉)**, 不拉同花顺 (口径一致铁律); 概念历史仅 2025+ 是覆盖限制 |
| **disclosure_date** | 500 | 起涨 + PIT 工程 | 财报披露窗口(主升常在披露窗启动)+ 校准其他财报 PIT 时点, 低积分 |
| **daily_info** | 600 | regime | 全市场成交额/换手/PE = 市场情绪流动性 regime(主升需市场放量配合) |
| idx_factor_pro / stk_nineturn / stk_auction_c / hsgt_top10 / ccass_hold / research_report / stk_alert | 各异 | regime/出场/分层 | 次级: 多可自算替代或权限/历史受限, 见全表 |

## 拉取批次建议 (限流 tinyshare 单接口120/分,多200/分,并发2)

- **批1 (P0 核心, 优先)**: stk_factor_pro(全市场×全历史日频, 最大, 按交易日批) + sw_daily(行业指数, 小) + share_float + stk_holdernumber + namechange。
- **批2 (P1 资金/连板)**: margin_detail + kpl_list + hm_detail (概念资金走已拉东财链, 不拉同花顺)。
- **批3 (P1 股东/分层)**: stk_holdertrade + top10_floatholders + disclosure_date + daily_info (概念分层走已拉 dc_member 东财链)。

每接口走既有 `sync_registry.yaml` 范式注册 (api/grain/batch_mode/pit_anchor/data_start/min_rows) → 通用 sync_runner 拉; 落库前**单日实弹核证字段/grain/单页上限** (防静默截断, top_inst 1000 整反例); 0 行当失败重试。

## 诚实先验贯彻

拉的多数是**出场/持仓/分层/资金确认/风控**类 (share_float 解禁 / cyq 筹码 / margin 两融 / holdernumber 户数 / holdertrade 减持 / stk_alert 异动) — 不为纯买点预测拉。买点 secondary 用 stk_factor_pro 技术因子 + 概念资金, 但定位边际改善。拉完进 Phase 2 逐阶段因子验证 (sandbox 探索, 含成本 + CPCV)。

## SKIP (22, 不拉)

**同花顺第三套 (口径一致铁律, 禁)**: moneyflow_ths / moneyflow_ind_ths / moneyflow_cnt_ths / ths_member / ths_index / ths_daily — 引入第三 vendor 与申万(行业)/东财(概念)冲突=leakage; 概念资金/分层走已拉东财链。
其余 SKIP: stk_factor(被 pro 全覆盖) / weekly/monthly/stk_weekly_monthly(daily 可聚合) / stk_premarket(被 stk_limit+daily_basic 覆盖) / stk_shock(被 limit 覆盖) / 全部 us_*/hk_*/fut_*/fx_*/opt_*/cb_*/bond_*/fund_*/etf_*/黄金/票房 (非 A股选股范围)。

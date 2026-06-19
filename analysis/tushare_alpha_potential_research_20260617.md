# Tushare 10000积分 选股潜力研究 — 拉取优先级 (2026-06-17)

> 状态: live (数据拉取决策依据)。owner: 本文件。
> 方法: 6 类并行评估 241 接口中 A股选股相关者 (catalog backend/config/tushare_api_catalog.json 真实字段),
> 对主升浪逐阶段 (起涨鱼头/主升鱼身/顶部鱼尾 + 分层 + regime) 潜力打分。
> 诚实先验偏重: **出场/持仓/分层/资金确认/板块轮动 > 纯买点预测** (买点≈噪音上限)。
> pulled 标记已交叉核对 32 已拉表 (agent 曾误标 top_list 未拉, 实际已拉 — 已修)。

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
| **moneyflow_cnt_ths** | 6000 | 分层 + 起涨 | **概念资金流真空区**填补 (现 dc 是行业非概念, dc_member 概念仅2025+)。主升常概念催化, 概念净流入领先, PIT 干净 |
| **hm_detail** | 10000 | 起涨 + 主升 + 顶部 | 游资席位标签 = top_inst 营业部之上的语义增强, 游资驱动主升专属解释力 |
| **stk_holdertrade** | 2000 | 顶部出场 + 主升背书 | 大股东/高管增减持: **高位减持=撤退**做出场负向 gate(真金白银), ann_date PIT。当过滤层 |
| **ths_member + ths_index** | 6000 | 分层(概念归属) | 唯一带 in/out_date 的概念成分(非 latest 快照), 建 PIT 概念分层。**须核 out_date 填充率**防 index_member_all 式 75% NULL 坑 |
| **top10_floatholders** | 2000 | 分层 + 主升确认 | 流通前十大=真实可抛压筹码, 机构进驻变化中频信号, 配 cyq |
| **disclosure_date** | 500 | 起涨 + PIT 工程 | 财报披露窗口(主升常在披露窗启动)+ 校准其他财报 PIT 时点, 低积分 |
| **daily_info** | 600 | regime | 全市场成交额/换手/PE = 市场情绪流动性 regime(主升需市场放量配合) |
| idx_factor_pro / stk_nineturn / stk_auction_c / hsgt_top10 / ccass_hold / research_report / stk_alert | 各异 | regime/出场/分层 | 次级: 多可自算替代或权限/历史受限, 见全表 |

## 拉取批次建议 (限流 tinyshare 单接口120/分,多200/分,并发2)

- **批1 (P0 核心, 优先)**: stk_factor_pro(全市场×全历史日频, 最大, 按交易日批) + sw_daily(行业指数, 小) + share_float + stk_holdernumber + namechange。
- **批2 (P1 资金/连板)**: margin_detail + kpl_list + moneyflow_cnt_ths + hm_detail。
- **批3 (P1 股东/概念/分层)**: stk_holdertrade + top10_floatholders + ths_member + ths_index + disclosure_date + daily_info。

每接口走既有 `sync_registry.yaml` 范式注册 (api/grain/batch_mode/pit_anchor/data_start/min_rows) → 通用 sync_runner 拉; 落库前**单日实弹核证字段/grain/单页上限** (防静默截断, top_inst 1000 整反例); 0 行当失败重试。

## 诚实先验贯彻

拉的多数是**出场/持仓/分层/资金确认/风控**类 (share_float 解禁 / cyq 筹码 / margin 两融 / holdernumber 户数 / holdertrade 减持 / stk_alert 异动) — 不为纯买点预测拉。买点 secondary 用 stk_factor_pro 技术因子 + 概念资金, 但定位边际改善。拉完进 Phase 2 逐阶段因子验证 (sandbox 探索, 含成本 + CPCV)。

## SKIP (22, 不拉)

stk_factor(被 pro 全覆盖) / weekly/monthly/stk_weekly_monthly(daily 可聚合) / stk_premarket(被 stk_limit+daily_basic 覆盖) / stk_shock(被 limit 覆盖) / 及全部 us_*/hk_*/fut_*/fx_*/opt_*/cb_*/bond_*/fund_*/etf_*/黄金/票房 (非 A股选股范围)。

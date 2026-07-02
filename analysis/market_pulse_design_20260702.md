# 市场感知 (Market Pulse) — Follow the Money 架构设计 v1 (2026-07-02)

> owner: 主会话。状态: 设计待用户 review, 实现排 master plan **B4** (引擎) + **C4** (前端页)。
> 用户定调 (原话锚): "市场感知无非就是看钱在哪里从哪里流出流向哪里…从板块、行业、概念这种**分层后的
> 资金流向**及其相应的**涨停和跌停家数、涨跌家数**…感知出资金在哪里、从哪流出、流向哪、**哪里资金悄悄
> 的在流入、哪里悄悄在流出**"。借鉴 @aleabitoreddit sector-rotation 方法论 (11 ETF 周度 RS 排名→top3 关注
> /bottom3 拉黑→领涨板块内找 stage1 长基底→日线紧缩入场)。
> 旧 market_perception 模块 (复杂版) 已随 2026-06-28 重建整体退役 — 本设计从零按地基构建, 非复活。

## 0. 数据地基核证 (2026-07-02 实测 — **零新增数据源**)

| 原料 | 表 (已在库) | 供给 |
|---|---|---|
| 行业/概念资金流 | raw_tushare_moneyflow_ind_dc (**1076 个行业+概念板块**, 净额+超大/大/中/小单分档+rank, 2024-01+) | 钱从哪来往哪去 (分档=谁在买: 超大单≈机构/游资) |
| 概念板块行情+涨跌家数 | raw_tushare_dc_index (**up_num/down_num 现成** + pct_change + turnover + total_mv + leading 领涨股) | 板块内部广度 |
| 行业指数行情 | raw_tushare_sw_daily (申万 L1/L2, 90万行 2019+) | RS 相对强度计算 (A股版 11 ETF = 申万 31 L1) |
| 涨停/跌停/炸板 | raw_tushare_limit_list_d (U 5.0万 / D 1.3万 / Z 1.9万) × dim_stock_segment_daily (B1) | 分层涨跌停家数 (情绪温度分布) |
| 涨跌家数 (行业级) | raw_tushare_daily pct_chg × B1 分层表 聚合 | 申万行业广度 (dc_index 只有概念的) |
| 大盘资金 | raw_tushare_moneyflow_mkt_dc (1行/日) | 全市场水位 |
| 基准 | raw_tushare_index_daily 000300.SH | RS 分母 |

**vendor 自洽红线** (既有裁决): 资金流链全东财 (flow vendor = membership vendor, moneyflow_ind_dc × dc_member/dc_index);
RS 链全申万 (sw_daily × v_sw_industry_pit)。两链并列展示, **禁跨链混算** (东财流 ÷ 申万成分 = 口径杂交)。

## 1. 核心切分: 感知层 vs 信号层 (诚实前置)

| 层 | 内容 | 证据状态 | 处置 |
|---|---|---|---|
| **感知层 (本设计主体)** | 钱现在在哪/流向哪/悄悄动向 — 同步描述现状给**用户看** | 描述性事实, 无需预测力证明 | Type A 聚合, B4 引擎 + C4 页面, 直接做 |
| **信号层 (候选)** | RS 动量 top3 过滤器 (aleabitoreddit 主张) / 资金流领先性 | **既有裁决: 概念资金流预测力 IC≈0 (同步非领先)**; RS 行业动量有文献支持但本库未验 | 进 D 阶段消融验证 (D2 事件层旁挂 "板块 regime cell"), **验证过才进策略, 感知页不给买卖暗示** |

> aleabitoreddit 方法论的可借鉴内核拆解: ①sector rotation 为**第一过滤器** (= 我们的分层 cell 思想, B1 已备)
> ②RS 4/12 周排名 (信号层候选, D 验证) ③领涨板块内找 stage1 长基底 (= B2 形态识别 + D 主升浪的交集)
> ④警示信号 (板块 lower highs/龙头破位 → 感知层的"退潮预警"卡)。她的流程与 master plan D 阶段天然咬合 —
> **市场感知页 = 选股台的上游漏斗** (先看哪个板块有钱, 再进板块选股)。

## 2. B4 引擎 — mart_market_pulse_daily (Type A 聚合, M3 process 步)

**表 1: mart_sector_pulse_daily** (板块×日; 两链并列)
```
chain        'dc_concept' | 'sw_industry'          -- vendor 链标识 (禁混算)
sector_code  dc ts_code | sw 801xxx
sector_name
trade_date
pct_change   板块当日涨跌
net_amount   资金净流入 (dc 链; sw 链 NULL)
elg_amount   超大单净额 (机构/游资口径)
rank_flow    当日资金流排名
rs_4w        vs HS300 4周相对强度 (滚20交易日收益差)
rs_12w       vs HS300 12周相对强度 (滚60交易日)
rs_rank_4w   RS 排名 (aleabitoreddit top3/bottom3 的 A股版)
up_num / down_num       涨跌家数 (dc 现成; sw 由 daily×B1 聚合)
limit_up_n / limit_down_n / zha_ban_n   涨停/跌停/炸板家数 (limit_list_d×B1)
turnover_amt_share      成交额占全市场比
quiet_inflow_days       连续"悄悄流入"天数 (见下)
quiet_outflow_days      连续"悄悄流出"天数
```

**"悄悄流入/流出" 定义** (用户亮点, 阈值进 config/market_pulse.yaml):
`quiet_inflow = 板块 |pct_change| < quiet_px_band (默认 1%) AND net_amount > 0` 的连续天数
(价格没动但钱连续进 = 吸筹嫌疑; 反向=派发嫌疑)。确定性重排 → 仍 Type A。

**表 2: mart_market_pulse_daily** (全市场×日, 1行): 大盘净流入 / 全市场涨跌停家数 / 涨跌比 /
炸板率 (Z/(U+Z), 旧 regime 情绪口径复用) / 两链 top3-bottom3 板块快照 JSON。

工程: `services/market_pulse.py` (rebuild_all + build_latest 幂等, 挂 process 步 B1 之后) +
config/market_pulse.yaml (RS 窗口/quiet 阈值) + data_layers 声明 (display/L1) + roster 登记 + 单测。

## 3. C4 前端页 — 市场感知 (widget 独立小功能)

| 卡片 | 内容 | API |
|---|---|---|
| 资金热力图 | 板块×近20日 net_amount 热力 (dc 链), 点击下钻板块成分 | GET /api/v3/pulse/heatmap |
| RS 轮动排名 | 申万 31 L1 的 rs_4w/rs_12w 双窗排名 + 排名迁移箭头 (谁在升/降) | GET /api/v3/pulse/rotation |
| 悄悄流入/流出榜 | quiet_inflow_days 降序 + 累计净额 | GET /api/v3/pulse/quiet |
| 情绪温度 | 涨跌停/炸板率/涨跌比 时序 (全市场+分层) | GET /api/v3/pulse/sentiment |
| 退潮预警 | 前 top3 板块跌出 + 龙头股破位计数 (aleabitoreddit 警示信号) | GET /api/v3/pulse/warnings |

## 4. serenity (@aleabitoreddit) 资产定位

现存: `analysis/serenity_20260611/` 3 份 (METHODOLOGY_full / TRANSFERABILITY_critique / INTEGRATION_design,
80K 提炼版; 未见推文原始库)。方法论两部分:
- **sector rotation 流程** → 本设计 §1-§3 已吸收 (RS 排名/漏斗/警示信号)。
- **产业链上下游/关键瓶颈研究** → 定位"结构增强层" (钱沿产业链传导: 上游涨价→中游承压), 需产业链
  图谱数据 (dc 概念部分覆盖, 无严格上下游边) — **排后续**, 感知页 v1 不含; 若做, 先评估图谱数据源。

## 5. 落位 master plan

- **B4 市场感知引擎** (1-2天): 排 B2 形态识别之后 (B1 分层已备, 无阻塞可提前); 
- **C4 市场感知页**: C 线第 4 页 (档案/实盘模拟/工作台之后)。
- 信号层验证 (RS top3 过滤器是否提升 D 细分策略) = D2 消融的一个 lens, 不单独立项。

## 6. 待拍板
1. 感知/信号两层切分 (感知页只描述不暗示买卖, RS 过滤器进 D 验证) — 同意?
2. quiet_inflow 定义 (|pct|<1% 且净流入, 连续天数) — 同意/调整?
3. B4 排序: B2 之后 (默认) 或提前到 B2 之前 (无依赖冲突, 若你想先有感知面) — 选?

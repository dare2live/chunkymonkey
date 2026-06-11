# 流派 B 策略蓝图 — 回调增强 (Reversal-Plus): 给已验证的 reversal×stage 地基加三层确认

> 生成: 2026-06-11 | 作者: 策略研究员 (流派 B) | 状态: 蓝图 (未验证, 全部新增参数待 Optuna walk-forward)
> 地基实测证据: PROJECT_INDEX.md §10 (Phase ψ.α 严格 walk-forward, 34 个月窗, 7.5h 跑批)
> 数据侦察证据: /tmp/cm_checkup/domain_{筹码,资金流向,板块}*.json + samples/*.parquet (本会话实际 join 验证过 schema)
> 诚实声明: evidence_*.md / cross_*.md 不存在 (未生成), 本蓝图基于 domain 深挖 JSON + 全息图 + repo 实测资产写成。
> 所有"58%→65%"类目标 = 设计目标, 不是预测。每个确认层的真实 uplift 以 OOS ablation 实测为准。

---

## 0. 一页摘要

| 项 | 内容 |
|---|---|
| 地基 (已实测) | `reversal_1m_mild × stage=1.5`: avg OOS sharpe **+0.435** / win **58.5%**; `reversal_1m_deep × stage=1`: **+0.392** / win **58.1%** / 单笔 +5.22% (PROJECT_INDEX §10, 严格 walk-forward; 摘要区另有 deep×1 = +0.32/60.5% 的口径, 以 §10 表为准引用并注明) |
| 增量假设 | 反转信号的失败案例主要是"接刀"(下跌中继) 和"阴跌钝化"。用 **筹码套牢结构 (cyq_perf)** + **特大单洗盘/出货判别 (moneyflow)** + **板块资金流顺风 (moneyflow_ind_dc)** 三层确认过滤掉这两类失败 |
| 设计目标 | 胜率 58% → 62-65% (上限目标), 以"期望值账单"验收而非裸胜率: 信号量损失必须一起结算 |
| 新数据依赖 | moneyflow (2010 起, S 级, 2000 积分) + cyq_perf (2018 起, S 级) — 均已在 implementation_plan Phase 1 接入序前两位, **本流派不新增接入序, 只消费** |
| 最快落地 | V0 本周可跑: 用本地已有 `raw_fund_flow_daily` (akshare, 2025-08~2026-04, 8 个月) 对既有 reversal trigger 历史做条件分桶 — 0 新数据、0 API 调用 |
| 最大风险 | winner_rate 本质是 close 相对历史成本分布的位置函数, 与公式已有的 ret20 条件**先验共线** — 正交性检验是第一道生死关 (本会话 5 股 sample 探针未见单调性, 见 §6.0) |

---

## 1. 核心论点: 赚的是谁的钱 / 什么行为偏差

### 1.1 地基为什么赚钱 (已被项目实测支持的部分)

A 股短期反转效应是实测最强的截面 alpha 之一 (项目内: momentum 公式 12/12 组合 OOS sharpe 全负, reversal 族全员转正; 外部: 1 月反转中证全指 RankIC ~9%, 见 reversal_short_term.py 文档头)。行为来源:

1. **散户追涨杀跌的过度反应**: 20 日跌 4-30% 的非崩盘回调中, 相当部分是情绪性抛售 (止损盘/恐慌盘), 价格短期超调, 之后均值回归。卖给我们筹码的是"杀跌的散户", 把价格买回去的是回归的均值。
2. **低波过滤 (rel_std ≤ 8-10%) + 量比正常 (0.6-2.0)** 已经剔除了崩盘式下跌和恐慌末端, 这是地基 58% 胜率的来源 — 它买的是"有序回调", 不是"接飞刀"。
3. **stage gate (1/1.5 底部区) 有效**: 同一公式在 stage=4 (下降趋势) 胜率掉到 46.2%, 在 stage=1 是 58.1% — 位置结构本身已是第一层确认。

### 1.2 三层确认各自赚什么钱 (本蓝图的新增假设, 全部待验证)

| 确认层 | 行为偏差 | 区分的两类回调 | 数据 |
|---|---|---|---|
| L1 筹码/套牢盘 | **处置效应**: 套牢者回本即卖 (上方密集套牢区 = 抛压墙); 获利盘少 = 上方阻力小。回调到主力加权成本附近受支撑 = 主力不愿割自己 | "回调进成本支撑区" vs "跌破成本结构的趋势破坏" | cyq_perf (winner_rate + 5 成本分位) |
| L2 特大单资金流 | **信息不对称**: 回调期间特大单/大单持续净流入 = 知情资金在散户恐慌中吸筹 (洗盘); 净流出 = 主力借反弹派发 (出货)。散户单 (sm) 反向接盘是派发期典型微观结构 | "洗盘回调" vs "出货回调" | moneyflow (买卖两侧四档全拆分, 唯一可算主动买占比的口径) |
| L3 板块资金流顺风 | **A 股板块联动**: 个股反转若发生在板块资金净流出周期里, 是逆水行舟 (板块 beta 持续压制个股反弹); 板块资金仍在 = 顺风 | "板块内个股轮动回调" vs "全板块退潮" | moneyflow_ind_dc (rank 字段现成) + 成员表 PIT |

一句话: **地基用价格形态选出"可能的反转点", 三层确认回答同一个问题的三个侧面 — "这次下跌, 筹码到底去了谁手里"**。赚的钱 = 散户恐慌抛售 + 处置效应抛压结构 + 板块退潮期误判, 三类行为偏差的均值回归溢价。

### 1.3 为什么这是四流派里最快落地的

- 地基 OOS 数字已存在且干净 (项目唯一一组"真金白银验证过"的公式族)。
- 两个 S 级数据源 (moneyflow/cyq_perf) 已排在 implementation_plan Phase 1 接入序前两位, 不需要为本流派单独立项。
- 确认层是**过滤器**, 不改公式引擎、不改 paper_sim、不建新表族 — 架构上只是 D2 特征 + D3 信号 gate, 完全走 architecture_draft 六层契约的现有挂载范式。

---

## 2. 信号链 (每步: 数据表/接口 + PIT 锚 + 计算窗口)

```
S0 universe → S1 公式触发 (现有) → S2 筹码确认 → S3 资金流确认 → S4 板块顺风
   → S5 (盘后) regime/复核 gate → S6 T+1 开盘可执行性 → 入场
```

| 步 | 内容 | 数据表/接口 | PIT 锚 | 计算窗口 |
|---|---|---|---|---|
| S0 | universe 过滤 (见 §3) | `price_kline_tdxhub` (K 线真相源, qfq, 2022-01-04 起, 已补到 2026-06-10) + `universe_rules.yaml`; 生产替换静态规则: TuShare `stock_st`/`suspend_d` | K 线 date ≤ t; ST/停牌状态用 t 日已公告状态 | 90 日无交易=退市 (yaml 既有) |
| S1 | 公式触发: `reversal_1m_mild` / `reversal_1m_deep` × stage∈{1,1.5} (formula_reversal_short_term.yaml 现行参数) + `fact_stock_technical_stage` | `formula_engine/reversal_short_term.py` + `fact_stock_technical_stage` (本地, K 线派生) | `bars[:sig_i+1]`, 信号日 t 收盘价生成 | ret20 = close_t/close_{t-20}-1; rel_std 60d; vol_ratio = vol_t/vol_ma20 |
| S2 | 筹码确认特征 (L1): `wr` = winner_rate; `dist_wavg` = close_t / weight_avg − 1; `chip_spread` = (cost_85pct−cost_15pct)/cost_50pct; `wr_drop20` = wr − max(wr, 过去 20 行); `overhead_pressure` 代理 = close_t / cost_85pct | 新 `raw_tushare_cyq_perf` (cyq_perf, 2018 起, 18-19 点盘后更新) | **JOIN t-1 行** (盘后数据红线); 落库带 `built_at`, JOIN 恒带 `built_at <= t`; winner_rate 见过 100.05 > 100 (sample 实测) → ETL clamp [0,100] | 单行 t-1 + 20 日 rolling (全部 ≤ t-1 行) |
| S3 | 资金流确认特征 (L2): `elg_net_5d` = Σ_{t-5..t-1}(buy_elg+buy_lg−sell_elg−sell_lg)_amount / Σ amount; `elg_streak` = 连续净流入日数; `sm_elg_div` = z(小单净买) − z(特大单净买); `pv_div` = [5 日累计 net_mf>0] ∧ [5 日 pct_chg<0] (价跌钱进) | 新 `raw_tushare_moneyflow` (moneyflow, 2010 起, 盘后更新, 单位万元) | **JOIN t-1**; 字段语义陷阱: moneyflow_dc 的 `buy_elg_amount` 是净额、本表是买入额 — ETL 必须改名隔离 (domain JSON 警告) | 5/20 日窗口, 全部 ≤ t-1 |
| S4 | 板块顺风 (L3): `sector_rank` (moneyflow_ind_dc 现成 rank 字段) 的 t-1 值与 5 日均值; `sector_net_5d` 板块 5 日累计净额符号; veto: 板块连续净流出 ≥ N 日 | 新 `raw_tushare_moneyflow_ind_dc` (单位**元**, 与个股万元不同, ETL 归一) + 成员映射: 首选 `dc_member` (按日成分, 天然 as_of), 历史下限 unknown 需探底; 兜底口径 = 申万 L2 (`index_member_all` in_date/out_date 真 PIT + `sw_daily` 行情) | 板块流 JOIN t-1; **成员表 as_of t-1, 严禁拿最新成分回填历史** (dim_stock_tdx_block 静态快照 = 项目已有反例); `index_member_all` JOIN: in_date<=t AND (out_date IS NULL OR out_date>t) | rank: t-1 单日 + 5 日均; net: 5 日累计 |
| S5 | 盘后复核 gate (可选, 第二阶段): t 日盘后 (≥20:00, freshness 校验通过) 用 **t 日当天** 的 moneyflow/cyq 行复核 S2/S3 — 信号 t 收盘生成用 t-1 是保守锚, t 盘后数据在 t+1 开盘执行前已发布, 用于确认是 PIT 合法的 | 同 S2/S3 表的 t 日行 | 双阶段锚: 特征面板 = t-1 (统一口径); 盘后复核 = t 行 + `built_at` 在 t 日 20:00 后 + freshness PASS 才允许; **t 行缺失 → 跳过复核 (保持 t-1 决策), 禁止静默回退** | t 单日 |
| S6 | 可执行性: t+1 开盘价 vs 涨停价; 一字板/涨停开盘不可买; 停牌跳过 | `stk_limit` (涨跌停价真相源) + `suspend_d` + K 线 t+1 open | t+1 开盘时刻已知信息 (涨跌停价 t 日收盘后即定) | open_{t+1} ≥ limit_up × 0.98 → 标记不可成交, 弃单 |

**确认层组合逻辑** (gate 形式, 不是加权分): 每层输出 {enhance / neutral / veto} 三态。veto 直接弃; enhance 进入 selector 加分 (score 加成系数进 search space)。三层全部可独立开关 (ablation 必须逐层)。

---

## 3. Universe 与可执行性 (T+1 现实)

| 规则 | 实现 | 来源 |
|---|---|---|
| 板块白名单 | 60/00/30/68 前缀全量 (沪深主板+创业板+科创板) | `universe_rules.yaml` 既有; 反例防回退: 严禁 `max_stocks=200` 按 code 排序类截断 (CLAUDE.md §4.5), 运行时 `validate_loaded_stocks` 板块覆盖检查 |
| ST/*ST 排除 | 生产切到 `stock_st` 接口 (PIT 状态), 替代 name pattern (改名滞后风险) | implementation_plan Phase 1 接入序已含 |
| 退市/停牌 | K 线 90 日无交易 (真相源) + `suspend_d` 日级停牌 | universe_rules + Phase 1 |
| 流动性 | 20 日均成交额 ≥ 5000 万 (paper_sim 既有阈值); 大单冲击: 单笔买入 > 3% × ADV20 加 15bps 溢价 (tx_cost 既有) | `paper_sim_config.yaml` |
| 次新股 | 上市 < 60 日不参与 (cyq_perf 筹码分布在次新股上无意义 — 全是新筹码; universe_rules 中该项"记录未执行", **本策略必须执行**, 天数进 search space) | 新增, 理由: L1 层语义 |
| 涨停不可买 | T+1 open ≥ 涨停价×0.98 → 弃单; 涨停价用 `stk_limit` 不用 10%/20% 推算 (除权日/ST 5% 等边角) | paper_sim tradability 既有 + stk_limit 替换 |
| T+1 卖出 | 买入日不可卖, paper_sim 既有约束; min_holding ≥ 1 | paper_sim 既有 |
| 反转特有现实 | 反转信号日是阴线/十字星, **T+1 高开抢跑风险远低于突破策略** (买点天然在情绪低位) — 这是本流派相对突破族的执行优势; 但仍须用 T+1 open 口径结算全部回测数字 (全息图盲点 6) | 验证计划 §6 强制 |

---

## 4. 入场 / 出场 / 仓位 / 风控 — 参数全部进 Optuna search space

> 红线: 以下所有新增参数**不拍死任何默认值**, 全部声明进 `optuna_config.yaml` search_space 新增节, 走 `walk_forward.expanding_monthly` (R1)。跑批前 `plan_validator.enforce_optuna_plan()` 验证 search space 非空 (2026-05-26 29/34 公式白跑反例)。下方区间是 search 范围, 不是取值。

### 4.1 入场 (信号 + 三层确认)

| 参数 | search space | 说明 |
|---|---|---|
| formula_variant | {mild×1.5, deep×1, deep×3, mild+deep 并集} (categorical) | 地基 4 个已实测正 sharpe 组合 |
| L1.wr_max | uniform(20, 70) | 信号日 t-1 winner_rate 上限 (获利盘少=洗得干净); **也搜反向** wr_min ∈ (0,40) 对照, 不预设方向 |
| L1.dist_wavg_band | lo: uniform(-0.15, -0.02), hi: uniform(0.0, 0.10) | close 距主力加权成本的支撑区间 |
| L1.chip_spread_pctl_max | uniform(0.3, 0.9) | 筹码集中度 (相对自身 250 日分位) — 窄=吸筹完成 |
| L1.mode | {off, veto_only, enhance_only, both} | 层开关 (ablation 内建) |
| L2.elg_net_5d_min | uniform(-0.02, 0.03) | 特大+大单 5 日净流入占成交额比下限; <0 区间允许 = "流出不超过 X" 的弱版本 |
| L2.streak_min | int(0, 5) | 连续净流入日数 |
| L2.sm_elg_div_max | uniform(0.0, 2.0) | 散户接盘分歧 z 值上限 (veto 派发形态) |
| L2.mode | {off, veto_only, enhance_only, both} | 同上 |
| L3.sector_rank_max | int(10, 200) | t-1 板块净流入 rank 上限 (moneyflow_ind_dc 板块数 ~458) |
| L3.sector_outflow_veto_days | int(2, 8) | 板块连续净流出 ≥N 日则 veto |
| L3.mode | {off, veto_only, enhance_only, both} | 同上 |
| buy_offset | choices [1,2,3,5] (既有) | T+1 默认, 延迟确认入场一并搜 |
| score_boost_enhance | uniform(1.0, 1.5) | enhance 态对 selector score 的加成 |

### 4.2 出场 (复用既有 + 两个反转特有新参数)

| 参数 | search space | 说明 |
|---|---|---|
| stop_pct / target_pct / trailing_pct / hp | 既有 optuna_config 区间 ({-0.15,-0.03} / {0.05,0.30} / {0.01,0.08} / [5..90]) | 不动 |
| exit.wr_takeprofit | uniform(80, 98) ∪ {off} | 持仓股 winner_rate(t-1) 升破阈值 = 全员获利抛压临界 → 提前止盈 (cyq domain JSON 卖侧 gate 假设) |
| exit.elg_outflow_exit_days | int(2, 6) ∪ {off} | 持仓股连续 N 日特大单净流出 ∧ 收阴 → 提前退出 (出货判别用在退出端 — 全息图盲点 4: 主力数据强项在退出不在入场) |

### 4.3 仓位 / 风控 (复用 paper_sim v2, 不新建)

| 项 | 取值 | 来源 |
|---|---|---|
| 资金/持仓 | 100 万 / max 5 仓 / wilson_kelly sizing / min_cash 5% | paper_sim_config 既有 (max_positions sweep [3,5,7] 在 validation 节既有) |
| 组合集中度 | 同板块 (申万 L2 口径) 持仓 ≤ 2 只 (categorical {1,2,3} 进 space) — 反转信号在板块退潮日会成片出现, 不限制 = 5 仓同坑 (全息图盲点 5) | 新增 |
| 硬止损 | 累计 -25% 全清观望 5 日 (既有) | paper_sim risk 节 |
| regime gate | moneyflow_mkt_dc 主力净流入 20 日均值 + net_amount_rate 分位 → 仓位 scaling; 阈值走历史 sensitivity sweep (P2, 不阻塞主线; "regime 阈值拍脑袋"是 §4.5 反例) | implementation_plan Phase 4 既定 |
| 成本 | 全套既有 tx_cost (佣金 0.025%/印花 0.05%/滑点 8bps/大单溢价 15bps) | paper_sim_config 既有, TradingCostConfig 单一真相源 |

---

## 5. 数据需求清单 + 回填规模 + modal 跑批估算

### 5.1 接口清单 (按依赖序; 全部已在 implementation_plan Phase 1 序内或为其自然延伸)

| 优先 | 接口 | 回填范围 | 规模实测/推算 | 调用成本 (10000 积分) | 服务 |
|---|---|---|---|---|---|
| P0 | `moneyflow` | **2018-01 起** (8.5 年, 与 cyq 对齐; 2010 起全量为可选扩展) | 样本实测 5202 股/日 × ~2060 交易日 ≈ **10.7M 行** (推算) | 按 trade_date 1 call/日 (5202 行 < 单次 6000 上限, 样本实测) ≈ 2060 calls, 1 天配额内 | L2 入场确认 + 出货退出 |
| P0 | `cyq_perf` | 2018-01 起 (接口史起点) | ~5400 股 × ~2030 行/股 ≈ **11M 行** (推算) | ts_code 必选 → 按股循环 ~5400 calls (单股 1 call 拿全历史, 6000 行上限 > 2030); 日限 20 万次内 | L1 入场确认 + winner_rate 止盈 |
| P0 | `stk_limit` + `stock_st` + `suspend_d` | 2022-01 起 (K 线起点对齐) | 小 (推算 <8M 行合计) | 按 trade_date 循环 | S0/S6 可执行性真相源 |
| P1 | `moneyflow_ind_dc` | 历史下限 unknown → **接入第一步实测探底** | 样本实测 1021 板块·概念/日 (行业+概念+地域) | 单次 5000, 按日循环 | L3 板块顺风 |
| P1 | `dc_member` + `dc_index` | 下限 unknown 探底; **每日增量落盘今天就开始攒** (攒一天少一天, 历史买不到) | dc_index 样本 458 板块 | 6000 积分, 单次 5000 | L3 成员映射主口径 |
| P1 | `index_member_all` + `index_classify` + `sw_daily` | 全量 (in_date 最早 2003, 真 PIT 进出日) | 一次性, 小 | 2000-5000 积分 | L3 兜底口径 + 组合层同板块限制 (申万 L2 = 已实测最优区分度口径) |
| P2 | `moneyflow_mkt_dc` | 全历史 | 1 行/日, 极小 | 单次 3000 | regime gate |
| P2 | `moneyflow_dc` (2023-09 起) | 共识第二票 | ~118K 行/月 (样本实测 5 月≈11.8 万) | 按日循环 | L2 增强 (三口径共识), ablation 证明增益才长期 sync |
| 不接 | `cyq_chips` | — | 全量回填数十亿行不可行 (domain JSON 工程结论) | — | cyq_perf 分位已是低维摘要; perf ablation 无增量则 chips 永不接 |

**Writer 纪律** (need_027 5 项 required gate, 已 PASS 的契约延续): 0 行返回 = 失败重试 (TuShare 间歇空响应实测); watermark + failure_queue; 单位归一 (moneyflow 万元 / ind_dc 元 / ths 亿元); 字段改名隔离 (`buy_elg_amount` 双语义陷阱); winner_rate clamp [0,100]。

### 5.2 modal 跑批规模 (标注: **估算, 跑前必须小样本实测** — "估算 2min 实跑 28min" 是项目反例)

| 任务 | 基线锚点 (实测) | 本蓝图推算 | 备注 |
|---|---|---|---|
| 特征面板增量 (L1+L2 个股层) | 76 特征面板既有构建管线 | +12 列, 10.7M+11M 源行, 推算单机 <1h | 纯 SQL 窗口聚合 |
| 确认层 ablation (走 walk-forward R1) | reversal 族 34 月窗 7.5h (本地, 实测) | 4 公式组合 × 3 层 × {off/veto/enhance/both} 主对角 ≈ 基线的 3-5 倍 → **22-38h 本地等效 (推算)** | 先跑 1 公式 × 1 层小样本实测单窗耗时再排 modal |
| Optuna 阈值搜索 | min 50 / max 500 trials (governance 既有) | 每组合 ≤ 200 trials, 走 `services.optimization` 中央层 | modal $30/月额度内能否覆盖 = unknown, 小样本实测后算 |

modal 前置 (CLAUDE.md §9): reviewed adapter + artifact-manifest 契约未完成前**全部本地跑**; 本蓝图 V0-V1 阶段本地足够, 只有全量 ablation (V2) 才值得上 modal。跑前走 `chunkyctl jobs --family` + `plan_validator` + grill gate。

---

## 6. 验证计划

### 6.0 已做的侦察性探针 (本会话, 只读)

5 股 × 3 年 cyq_perf 深样本 (000001.SZ/002460.SZ/300750.SZ/600519.SH/688981.SH) 与本地 K 线 join (4144/4144 行全对齐, schema 验证通过)。在 1199 个"20 日跌 4-30%"回调日上, winner_rate(t-1) 分桶 vs 10 日 forward 收益**未见单调性** (胜率 45.4%-53.3% 无序)。
**结论: 仅证明数据可用、口径能对齐; 5 只大盘股样本对 alpha 假设零证据力 (初步, 需全量验证)。它同时是诚实警告: L1 层在大盘股上可能无效, 全量验证必须分市值桶看。**

### 6.1 阶段计划

| 阶段 | 内容 | 数据依赖 | 产出/验收 | 时点 |
|---|---|---|---|---|
| **V0 (MVP, 一周内)** | 用本地 `raw_fund_flow_daily` (akshare, 2025-08-21~2026-04-24 实测在库) 对既有 `fact_technical_trigger` reversal 信号同期截面做 L2 条件分桶: 特大单净流入 5 日符号/分位 × forward 5/10/20d 胜率与均值 | **0 新数据 0 API** | uplift 方向表 + 分市值桶; 注意: 8 个月窗口、akshare 口径 (不稳定源) → 只做方向性 go/no-go, 不做任何入库决策 | 本周 |
| V0.5 | moneyflow 2018+ 回填落库 (Phase 1 序位第一, writer gate 既定) → V0 同口径在 8.5 年全样本重跑 + 与 raw_fund_flow_daily 重叠期对账 (两源一致性) | moneyflow | L2 条件 uplift 全期表, 分年度 | 数据到后 2-3 天 |
| V1 | cyq_perf 回填 → L1 截面检验: (a) winner_rate/dist_wavg/chip_spread 对 forward ret 的 RankIC + 分桶单调性 (仅在 reversal 信号日子集上); (b) **正交性生死关**: 对 ret20/rel_std/股价位置回归取残差后的增量 RankIC — winner_rate 与 ret20 先验共线, 残差无增量 = L1 死刑, 不进 Optuna | cyq_perf | 增量 RankIC (诚实基线参照: 干净 PIT 下 0.0108-0.0203); 相对提升 ≥+50% 自动触发 pit-audit 5 步 | +1 周 |
| V2 | 全量 ablation: 4 公式组合 × 3 层逐层/组合开关, `walk_forward.expanding_monthly` (min_train 6m, 月窗 forward 1m, 2023-01~2026-05 ≈ 35 窗), Optuna 阈值搜索, selector 只读 `COALESCE(oos_*)` | + moneyflow_ind_dc/dc_member (L3; 若探底历史 < 2 年, L3 只做 veto 不做 enhance 且单独标注短窗) | 每层独立 OOS uplift 表 (Δ胜率/Δsharpe/Δ信号量 三列必须同表) | +2-3 周 |
| V3 | paper_sim 含成本组合口径: 三层最优配置 vs 地基, T+1 open 结算, 涨停不可买, 5 仓重叠 NAV; 写入 `fact_sim_run` (数字出口唯一) | 同上 | 年化/max_dd/月胜率分布/超额 HS300, 对 4 基准 | +1 周 |

### 6.2 基准对比 (4 基准, 缺一不可)

1. **地基本身** (reversal×stage 无确认层, 同期同口径重跑) — 主基准, 确认层的全部价值 = 对它的增量;
2. **random-entry + same-exit** (同 universe 随机抽样 + 相同退出规则) — 全息图盲点 3: 不跑它就不知道胜率里多少是入场的功劳;
3. **HS300 buy-hold**;
4. **等权全 universe 月调仓**。

### 6.3 预注册成败判据 (写在跑之前, 防事后挑数)

| 判据 | PASS | FAIL 处置 |
|---|---|---|
| 单层 OOS uplift | Δ胜率 ≥ +2pp **且** Δavg OOS sharpe ≥ +0.05 **且** 信号量保留 ≥ 50% | 任一不满足 → 该层降为 veto_only 重测; 仍 FAIL → 该层不进生产, 写入反例 |
| 期望值账单 (盲点 1) | 月信号数 × gate 通过率 × 单笔期望 × 仓位利用率 → 组合年化贡献不下降 | 胜率升但账单变差 → FAIL (胜率不是 KPI, 年化才是) |
| 泄漏红线 | 胜率 > 70% 或 sharpe 相对地基翻倍 (≥+100%) 或相对提升 ≥+50% → 自动 pit-audit + ablation 复审, **怀疑不兴奋** | 复审不过 → 数字作废 |
| 月度胜率分布 | walk-forward 月度胜率 ≥55% 的月份占比报告 (KPI 同构口径) | 仅报告, 不做 gate (组合层 V3 才验收) |

---

## 7. 北极星 KPI 贡献路径 + 失败模式

### 7.1 贡献路径 (期望值账单口径, 全部标注证据等级)

| 环节 | 数字 | 证据等级 |
|---|---|---|
| 地基单股 OOS | sharpe +0.39~0.435, win 58.1-58.5%, 单笔 +4.5~5.2% (hp 月级) | **实测** (34 月窗 walk-forward) |
| 地基组合推算 | 5 仓 + 月轮换 → 年化 +15~25% | **推算未实测** (PROJECT_INDEX 自标), V3 paper_sim 出真数 |
| 确认层目标 | 胜率 +3~6pp, 信号量 -30~50% | **设计目标, 未验证** |
| 净效应粗算 | 若 +3pp 胜率 × 单笔期望同步改善 (失败案例被过滤 → 平均亏损额下降), 且 5 仓仍喂得满 → 组合年化 +3~8pp (粗算); 若信号量砍过头 → 可能负贡献 | **粗算**, 以 V3 期望值账单为准 |
| 对 KPI 的定位 | 把"组合年化 15-25% (推算)"的下限抬高并把月胜率分布右移; 距 30% 的剩余缺口由其他流派/组合层补 — **流派 B 单独大概率不够到 30%, 这是诚实结论** | 自评 |
| max_dd 贡献 | 出货 veto + 板块退潮 veto + 同板块 ≤2 仓: 三者都直接作用于回撤尾部 (反转策略最大回撤来源 = 接刀 + 同板块共振), 期望对 max_dd ≥ -20% 的贡献可能大于对年化的贡献 | 定性, V3 实测 |

### 7.2 失败模式 (什么情况下这策略不工作)

| # | 失败模式 | 机制 | 预警/对策 |
|---|---|---|---|
| F1 | **L1 与价格形态共线** (最大概率失败点) | winner_rate ≈ f(close 在历史成交分布中的位置) ≈ f(ret20, 位置), 确认层只是重复地基条件 → 残差零增量 | V1 正交性生死关前置; 死刑就砍, 不留恋 (用户纪律: 不合格就是不合格) |
| F2 | gate 砍信号量 → 5 仓喂不满 | 胜率 +3pp 但月信号从 N 降到 N/3, 资金利用率掉 → 年化反降 | 期望值账单是一级验收; enhance_only 模式兜底 (不删信号只改排序) |
| F3 | 单边急熊 (2018 式) | 反转公式在系统性下跌里全是接刀; stage gate 和板块 veto 都基于历史平滑量, 急跌期滞后 | regime gate (P2) + 硬止损既有; 接受"熊市空仓机会成本" (策略收入现金流压力表, 盲点 8) |
| F4 | 强趋势牛市 (2024-09 式) | 回调买点少且浅, 跑输追涨; 月胜率高但超额 HS300 为负 | 4 基准里等权基准暴露此态; 组合层由突破族流派补 (见 §8) |
| F5 | 资金流口径漂移/断流 | akshare→TuShare 切换期双源不一致; TuShare 间歇空响应; 东财四档定义变更 | 重叠期对账 (V0.5 内建); writer 0 行=失败; 三口径共识 (P2) 把单口径噪声降权 |
| F6 | 板块层历史不足 | dc_member 探底若只有 1-2 年 → L3 回测窗口不足, 结论不稳 | 预案: L3 用申万 L2 (2003 起真 PIT) 的板块**行情动量**做顺风代理先行, 资金流口径等数据攒够再升级; L3 短窗结论一律标"初步" |
| F7 | 特大单数据被对倒/拆单污染 | 游资对倒制造"特大单净流入"假象 (尤其小盘题材股) | sm_elg_div 分歧特征部分免疫; 分市值桶 ablation; 流动性下限 5000 万既有 |
| F8 | 确认层过拟合 (参数 14 个新维度) | search space 大 + OOS 窗口 35 个 → 多重检验假阳性 | 每层 ≤4 参数、层先单独过 V1 截面关才进 V2; min 50 trials 治理既有; 预注册判据 §6.3 |

---

## 8. 与其他三流派的互补性自评

> 注: 其余三流派定稿未读到 (本会话只拿到流派 B 任务书), 按编排惯例 (A 突破/主升浪, C 题材/爆发接力, D 其他) 沿四个轴自评; 以最终编排为准。

| 轴 | 流派 B (本蓝图) | 互补关系 |
|---|---|---|
| 入场方向 | **左侧回调确认** (买在情绪低位, 阴线/十字星日) | 与右侧突破族 (A) 入场日天然错开 — 同一只股不会同日双触发, 组合层同日资金竞争小; B 的 veto 逻辑 (出货判别) 可直接复用为 A 的入场过滤器和**退出端预警** (全息图: 主力数据强项在退出) |
| 持有期/形态 | 5-30 日均值回归, 主场是 91% 的慢牛/中性形态 | A (60-180 日波段) 与 B 在同一只股上是"接力"而非竞争: B 的反转买点常是 A 主升浪的回调段; C (爆发型 9%) 用资金应隔离成小书 (全息图路线 3), B 不碰连板/打板 — L2 特大单逻辑在题材股上反而易被对倒污染 (F7), 正好把这块让给专门处理它的流派 |
| regime 表现 | 震荡市最强 (回调-修复循环密集); 强趋势牛跑输 (F4), 急熊接刀 (F3) | 突破族强趋势牛最强、震荡市假突破最多 — 两者 regime 收益曲线先验负相关, 合并 NAV 平滑月胜率分布 (KPI 月胜率 ≥55% 直接受益) |
| 数据基建 | 消费 moneyflow / cyq_perf / moneyflow_ind_dc / dc_member / 申万 L2 PIT 链 | **B 是这批数据的第一个生产消费者 = 替全项目把 PIT 锚/单位归一/字段陷阱踩平**; 筹码出货预警 (wr_takeprofit / elg_outflow_exit) 是全流派共用的退出端组件 — 即使 B 入场层全军覆没 (F1), 退出端组件仍可独立交付给其他流派 (这是本蓝图的残值下限) |

**一句话定位**: 流派 B 是"把已验证的 58% 地基做厚"的最短路径 + 全项目资金流/筹码数据域的开荒队; 它对 KPI 的贡献上限不高 (单独到不了 30% 年化), 但失败残值最高 (数据基建 + 退出组件 + 4 基准框架全部可复用), 且是唯一一个一周内就能用 0 新数据跑出第一张 go/no-go 表的流派。

---

## 附: 治理合规清单 (执行前自查)

- [ ] 跑批前: `chunkyctl preflight` + `plan_validator.enforce_optuna_plan()` (search space 非空) + grill gate
- [ ] 全部新阈值进 `optuna_config.yaml` / `formula_*.yaml`, 零 hardcode (写注释的数学常数除外)
- [ ] 特征 JOIN 一律 t-1 + `built_at <= t`; 盘后复核 gate 单独 freshness 校验, 缺数据跳过不回退
- [ ] selector / scoring 只读 `oos_*` 列; 入库走 `governance.enforce_pre_insert`
- [ ] 相对提升 ≥+50% / 胜率 >70% → pit-audit, 怀疑不兴奋
- [ ] 数字出口: 对外只引用含成本 paper_sim (fact_sim_run 行存在) 及以上口径
- [ ] V0 akshare 桥接结论只做方向性参考, 永不入生产决策

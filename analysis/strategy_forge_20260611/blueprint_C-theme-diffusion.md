# 流派 C 策略蓝图 — 题材扩散事件驱动 (theme-diffusion)

> 生成: 2026-06-11 | 作者: 流派 C 策略设计 agent (Claude)
> 输入: domain_*.json 9 域深挖 + user_vision_hologram.md + docs/implementation_plan.md + /tmp/cm_checkup/samples/ 深样本实证 + 现有 formula_*.yaml 资产
> 注: 任务指定的 evidence_*.md / cross_*.md 在 /tmp/cm_checkup/ 不存在 (生成时点未产出), 按"若无则跳过"处理; 本蓝图的实证部分由本 session 用深样本 + 本地 K 线自测补足, 全部标注样本量。
> 诚实声明: 本蓝图所有"初步"标记的数字来自 ≤60 交易日小样本, 仅证明管道可行性与方向排除, **不构成 alpha 证明**; 策略整体期望 = unknown, 待 MVP 验证。

---

## 0. 本 session 新增 measured 事实 (设计的地基)

| # | 事实 | 数据 | 含义 |
|---|---|---|---|
| M1 | `moneyflow_ind_dc.rank` 是**分页伪 rank**: 每 50 行循环 1-50 (494 概念/日, 每个 rank 值出现 ~10 次), 与自算 net_amount 截面 rank 的 spearman = **0.07** | 深样本 21 日 × 494 概念 | rank 跳变检测**必须自算全量 rank**, 直接用该字段 = 教科书级数据陷阱 |
| M2 | 自算 rank 跳变事件 (prev>100 → top10) = **7.4 次/日**, 但 top 事件被**风格/宽基/财报类伪概念**支配 (大盘股/科技风格/历史新高/2025年报预增/题材股) | 同上, 20 日 148 事件 | 必须建概念黑名单维表, 否则事件检测器输出垃圾 |
| M3 | **朴素扩散 = 负 alpha** (初步): 申万 L2 当日涨停数≥3 → 买板内全部未涨停成员 (t+1 open 入, t+5 close 出, 剔高开>7%): mean **-0.97%** / win 37.9%, 同期随机基线 -0.32% / 43.1% (n=79k/16.5k) | limit_list_d 60 日 (2026-03-13~06-10, 含一次崩盘段) × index_member_all PIT × 本地 K 线 | 无选择地追扩散 = 给先手接盘。事件检测本身不是 alpha, **候选排序与时机才是** |
| M4 | **新点火约束把负 alpha 拉回基线** (初步): 加"前 3 日板内涨停<2 (新点火) + 当日已响应 +0.5%~6% + 高开≤5% + 持有缩到 t+3": mean -0.26% / win 44.9% vs 基线 -0.33% / 43.6% (n=5.2k) | 同上 | 方向正确但 **L2 粒度太粗** (131 个板块): 题材扩散发生在概念粒度 (~490 概念/日), L2 粒度通道单独使用 ≈ 无 alpha |
| M5 | 情绪温度计量纲 (初步): 60 日内炸板率 median 0.241 (range 0.115-0.66), 连板高度 median 5 (3-8), 日涨停数 median 83.5 (28-1372) | limit_list_d 60 日 | regime gate 阈值的真实分布参考; 极值日 (U=1372/D=343) 证明样本含完整恐慌-修复周期 |

设计推论 (第一性): 这个策略的真相源是**概念粒度的成分隶属历史 (dc_member) + 涨停事实 (limit_list_d) + K 线**。L2 行业是确认层不是检测层; 资金流是排序特征不是入场理由。

---

## 1. 核心论点 — 赚的是谁的钱

**一句话**: A 股的涨停板制度 + T+1 制度把题材信息的价格发现人为截断: 龙头封死涨停后, 后知后觉的资金**买不到龙头**, 于是在次日把订单流溢出到同题材"第二梯队"。我们在题材点火日 (t) 盘后识别这股即将到来的溢出, 在 t+1 开盘买入溢出的承接标的, 在拥挤资金 (t+2~t+5 追高者) 进场时卖给他们。

| 要素 | 内容 |
|---|---|
| 行为偏差 | ① **锚定比价**: "龙头 20cm 封死, 同概念的它才涨 3%, 便宜" — 比价填谷是 A 股题材市最稳定的群体行为; ② **FOMO + 可得性**: 涨停潮上热搜/榜单 → 散户 t+1~t+2 集中下单, 但只能买得到没涨停的; ③ **制度性延迟**: T+1 + 涨停限制使信息扩散被拉长到 2-4 个交易日, 形成可预测的订单流时序 |
| 我们的对手盘 (卖给我们的) | 题材点火日不知情/恐高的第二梯队持有者 (在 t+1 开盘的温和高开里出货) |
| 接我们盘的 | t+2~t+5 进场的追高散户与后知后觉的跟风资金 |
| edge 为什么存在而未被套利干净 | 这是 A 股最卷的生态 (游资/打板量化都在做), 但绝大多数玩家**打龙头/打板**, 第二梯队低开低吸位是次优选择被相对冷落; 且策略容量小 (单题材第二梯队日承接量有限), 大资金做不了 — 适合 100 万级个人资金 |
| edge 衰减方式 | 题材进入第 3+ 日, 扩散末端是负和 (M3 实证: 不分时机追扩散 = -0.97%); 所以**只做点火日, 不做发酵日**, 持有 2-5 天强制离场 |
| 为什么不打板 | 用户 T+1 买不进痛点: 龙头涨停買不到; 打板需要盘中下单基础设施且是另一生态的红海。本策略全部决策在 t 盘后完成, t+1 开盘集合竞价/开盘价执行, 零盘中依赖 |

---

## 2. 信号链 (S0→S6, 每步: 数据源 + PIT 锚 + 计算窗口)

全链时序: **t 日盘后** (19:00 后, 各盘后表更新完毕) 跑检测与排序 → 产出 t+1 开盘买入清单 → **t+1 开盘** 按 gap 约束执行。

### S0 市场情绪 regime gate (做不做)

| 项 | 内容 |
|---|---|
| 数据 | `limit_list_d` (2020 起): 炸板率 #Z/(#U+#Z), 连板高度 max(limit_times), 晋级率 #(k+1板@t)/#(k板@t-1); `moneyflow_mkt_dc` (1 行/日): 大盘主力净流入 20 日均值/占比分位 |
| PIT 锚 | 两表均盘后更新 (具体时点文档未写): **回测主口径用 t 行 (t 盘后决策→t+1 执行), 敏感性口径用 t-1 行** (见 §6 PIT 双口径测试); 生产前实测更新时点 + `built_at` 守门 |
| 计算窗口 | 炸板率/连板高度: 当日值 + 5 日均值; 晋级率: t vs t-1; 大盘资金: 20 日滚动 |
| 输出 | regime ∈ {go, half, stop}: stop 日零新开仓 (M5 量纲: 炸板率 60 日 median 0.241, max 0.66 — 阈值进 Optuna, 不拍) |

### S1 题材点火检测 (三通道并联, 任一触发即候选事件)

| 通道 | 逻辑 | 数据 + PIT 锚 | 窗口 |
|---|---|---|---|
| D1 概念资金流 rank 跳变 | 概念 net_amount **自算**全量截面 rank (M1: 禁用原 rank 字段), prev_rank_5d_median > P_low 且 today_rank ≤ P_high; 概念黑名单过滤 (M2: 风格/宽基/财报/涨跌幅统计类伪概念, regex + 人工维表 `concept_blacklist.yaml`) | `moneyflow_ind_dc` (content_type=概念, ~490/日, 历史下限 unknown 需探底); 盘后更新明确 → t 行可用于 t+1 决策 | prev 用 5 日 median 防单日噪声; 跳变检测逐日 |
| D2 涨停传染 (主通道) | 概念内涨停家数 u_cnt(t) ≥ N_u **且** 新点火: max(u_cnt(t-1..t-3)) ≤ N_prev (M4: 该约束是把负 alpha 拉回正轨的关键); 联动 `limit_cpt_list` 的 rank/days 确认是否新晋主线 | `limit_list_d`(t, 盘后) × `dc_member`(**t-1 成分**, 概念归属日快照天然 as_of) + `limit_cpt_list`(t, 盘后, 8000 分) | 密度: 当日; 新点火回看 3 日 (回看窗口进 Optuna) |
| D3 概念指数动量确认 | 概念指数 20 日 RS rank 速度 (rank velocity) 上行 或 60 日新高放量; 申万 L2 RS 同向 = 加分 (确认层, 非检测层 — M4: L2 单独无 alpha) | `dc_daily` (2020 起, 概念指数行情) + `sw_daily`; 行情类 t 收盘后可得 → t 行可用 | RS: 20/60 日; 新高: 60 日 |

事件融合: 事件分 = w1·D1 + w2·D2 + w3·D3 (权重进 Optuna; D2 为必要条件还是加权项也作为离散搜索维)。事件频率预算: M2 实测 D1 原始 7.4 次/日, 黑名单 + D2 交叉后预期 1-3 次/日 (待 MVP 实测, 标 unknown)。

### S2 题材质量过滤 (这个题材值不值得做)

| 过滤 | 逻辑 | 数据 + PIT 锚 |
|---|---|---|
| 概念纯度 | 成分数 ∈ [N_min, N_max]: 太小 = 蹭概念噪声, 太大 = 泛题材 (如"融资融券标的") | `dc_member`(t-1) 成分计数 |
| 龙头封板质量 | 题材内最高板股: 封死 (open_times=0) > 烂板 (open_times≥3); 封单强度 fd_amount/float_mv 截面分位 | `limit_list_d`(t, 盘后) |
| 资金确认 | 概念 main_change/net_amount > 0 (D1 通道天然满足; D2/D3 触发的事件需此项把关) | `moneyflow_ind_dc`(t, 盘后) |
| 题材生命周期 | `limit_cpt_list.days` ≤ D_max (只做新晋, 不接力老主线 — M3 教训) | `limit_cpt_list`(t, 盘后) |

### S3 第二梯队候选构造与排序 (买谁)

候选池 = 触发概念的 `dc_member`(t-1) 成分, 减去:

| 剔除 | 数据 + PIT 锚 |
|---|---|
| 当日已涨停/曾涨停/跌停 (U/Z/D) | `limit_list_d`(t) + `stk_limit`(t, 8:40 盘前即有) 兜底 ST 板 (limit_list_d 不含 ST 统计) |
| ST/*ST | `stock_st`(t 日 9:20 名单, 2016 起; 更早用 namechange 重建) |
| 停牌 | `suspend_d` + K 线真相源 (无 bar = 不可交易) |
| 流动性: 20 日均成交额 < ADV_min | K 线 `price_kline_tdxhub` (t 及以前) |
| 上市 < 60 交易日 / 北交所 | `stock_basic` + 代码段规则 (`universe_rules.yaml`) |
| 当日涨幅透支: pct_chg(t) ∉ [resp_min, resp_max] | K 线(t)。M4 初步: [+0.5%, +6%] 区间有效 — **区间端点进 Optuna**, 不钉死 |

排序特征 (打分模型, 起步线性加权, 权重进 Optuna; 成熟后并入 LightGBM 排序):

| 特征 | 逻辑 | 数据 + PIT 锚 + 窗口 |
|---|---|---|
| 与龙头关联强度 | 共属概念数 (候选与当日龙头共同隶属的概念个数), 90 日收益相关性 | `dc_member`(t-1); K 线 90 日窗口 |
| 自身资金流 | 个股 (buy_elg+buy_lg−sell_elg−sell_lg)/amount, t 日值 + 5 日 rank | `moneyflow` (2010 起, 盘后) — t 行用于 t+1 决策 |
| 位置安全垫 | 距 60 日高点回撤分位 (不买已 extended 的); 获利盘 winner_rate 低分位 (抛压小) | K 线; `cyq_perf` (盘后 18-19 点, t 行) |
| 比价空间 | 候选市值/龙头市值, 候选累计涨幅/龙头累计涨幅 (10 日窗口) — "便宜的替身" | K 线 + `daily_basic` |
| 热度确认 (辅助) | dc_hot/ths_hot 上榜 = 加分但设上限 (热度太高 = 已扩散完) | `dc_hot`/`ths_hot`(t, 盘后快照, rank_time 字段核验时点) |

每题材取 top K_theme 只 (1-2, 进 Optuna), 全组合并集后按事件分 × 个股分排序取前 M。

### S4 入场执行 (t+1 开盘)

| 规则 | 内容 |
|---|---|
| 价格口径 | t+1 开盘价 (回测); 实盘 = 集合竞价挂单或开盘后限价 |
| gap 约束 | open(t+1)/close(t) − 1 > gap_max → 放弃该单 (M3/M4 实测用 5%/7% 截断; gap_max 进 Optuna)。高开过大 = 溢出已被抢跑, 追入是负期望 |
| 一字板/触板 | open == up_limit (来自 `stk_limit`, t+1 日 8:40 盘前可得) → reject_buy |
| 滑点 | 按成交额占 ADV 比例的滑点模型 (paper_sim v2 现有 tx_cost 框架, 不另造) |

### S5 出场 (T+1 现实: 最早 t+2 可卖)

| 规则 | 逻辑 | 数据 |
|---|---|---|
| 时间止损 (主) | 持有 H 日强制离场 (H ∈ 2-6, Optuna)。本策略赚扩散窗口的钱, 不恋战 — M3 实证扩散末端负和 | — |
| 题材退潮止损 | 概念指数跌破点火日收盘 × (1−θ) 或 概念内涨停数萎缩至 ≤1 → 次日开盘清出该题材全部持仓 | `dc_daily` + `limit_list_d` (盘后判, 次日执行) |
| 个股硬止损 | 收盘价较成本 −s% → 次日开盘卖 (s 进 Optuna; 跌停锁死按 paper_sim 现有 tradability 规则顺延并计成本) | K 线 + `stk_limit` |
| 止盈 | 触发涨停日不卖 (让利润跑), 次日按 trailing t_pct 回吐离场 | K 线 |

### S6 仓位与组合约束

| 规则 | 内容 |
|---|---|
| 单题材敞口 | 同一题材 ≤ K_theme 只 且 ≤ B_theme% 资金 (题材内个股联动 ≈ 1, 视作单注 — 全息图盲点 5) |
| 总敞口 | 本策略书 ≤ 20-30% 总资金 (双 book 结构, 见 §8); 单股 ≤ P_max% |
| 与主引擎互斥 | 若主升浪持仓已在该题材, 本策略不加仓同题材 (合并 NAV 层面控制) |

---

## 3. Universe 与可执行性

| 维度 | 规则 | 真相源 |
|---|---|---|
| 基础 universe | 全 A (含创业板/科创板, 板块涨停幅自动适配), 剔北交所 | K 线 + `universe_rules.yaml`; limit_up_pct 是 per-stock 属性运行时取 `stk_limit`, **不进 search space** (§4.5 反例: 参数作用域错配) |
| ST | 剔除 (5% 板扭曲扩散逻辑 + 退市风险) | `stock_st` (2016 起每日名单) |
| 停牌/退市 | K 线无 bar = 不可交易 (宪法真相源); `suspend_d` 作哨兵 | K 线 + `suspend_d` |
| 流动性 | ADV20 ≥ ADV_min (Optuna; 100 万资金单股 ≤ 20 万, 冲击成本要求 ADV ≥ 千万级) | K 线 amount |
| 涨停不可买 | t+1 open 触 up_limit → reject; 回测同规则 | `stk_limit` (8:40 盘前) |
| T+1 | 买入日不可卖; 出场信号盘后判、次日开盘执行; 跌停锁死顺延 | paper_sim v2 现有引擎 |
| 生存者偏差 | 含已退市股 (`index_member_all` 样本自带退市股; dc_member 按日成分天然含当时上市的全部) | PIT 成分 |

可执行性是本策略的**立身之本** (区别于打板): 所有买入标的定义上当日未涨停, t+1 开盘可成交概率高; gap 过滤再剔掉抢跑失败的单。MVP 必须输出"不可成交率 + gap 分布"作为验收件 (全息图盲点 6 的正面回应)。

---

## 4. 参数总表 — 全部进 Optuna search space (不拍死)

| 组 | 参数 | 范围 (search space) | 类型 |
|---|---|---|---|
| S0 regime | zha_rate_max | 0.20 – 0.50 | float |
| | lianban_height_min (低于此高度 = 冰点不做) | 2 – 5 | int |
| | mkt_inflow_gate (大盘主力 20 日净流入分位下限) | 0 – 0.4 | float |
| S1 检测 | N_u (概念涨停密度) | 2 – 6 | int |
| | N_prev (新点火回看上限) | 0 – 2 | int |
| | lookback_ignition (新点火回看日数) | 2 – 5 | int |
| | P_low / P_high (rank 跳变前/后阈值) | 80–300 / 5–30 | int |
| | w1,w2,w3 (通道权重) + D2_required (bool) | simplex / {0,1} | mixed |
| S2 质量 | N_min/N_max (概念成分数) | 5–30 / 80–300 | int |
| | D_max (题材已上榜天数上限) | 1 – 3 | int |
| | fd_quality_min (龙头封单分位) | 0 – 0.6 | float |
| S3 候选 | resp_min / resp_max (当日响应区间) | 0–2% / 4–8% | float |
| | adv_min | 1e7 – 1e8 元 | log-float |
| | K_theme (每题材只数) | 1 – 2 | int |
| | 排序特征权重 5 维 | simplex | float |
| S4 入场 | gap_max | 2% – 7% | float |
| S5 出场 | H (时间止损) | 2 – 6 日 | int |
| | s (硬止损) | 3% – 8% | float |
| | θ (题材退潮阈值) | 1% – 5% | float |
| | t_pct (涨停后 trailing) | 3% – 10% | float |
| S6 仓位 | B_theme / P_max / max_positions | 10–30% / 5–15% / 3–8 | mixed |

治理: 走 `services.optimization` 中央层 + `optuna_config.yaml`, walk_forward=expanding_monthly, selector 只读 `oos_*`; 跑前 `plan_validator.enforce_optuna_plan()` 验 search space 非空 (2026-05-26 反例)。维度 ~25, 首轮建议冻结 S6 + 通道权重 (用 D2-only), 先搜 12-15 维核心, 防 trial 数/维度比失衡。

---

## 5. 数据需求清单与回填规模

### 5.1 接入清单 (按依赖序)

| 优先 | 接口 | 用途 | 历史 | 回填量级估算 | 状态 |
|---|---|---|---|---|---|
| P0 | `dc_member` + `dc_index` (6000 分) | **概念成分 PIT, 全策略地基** | 下限 unknown (样本最早 2025-01), **第一动作 = 探底** | ~24.5k 行/日 (490 概念 × ~50 成员) ≈ 600 万行/年; 单次 5000 行 → ~1.2k 次调用/年份 | 未接 |
| P0 | `limit_list_d` (5000 分) | 涨停事实 + 情绪温度计 | 2020 起 (文档明确) | ~480 行/日 × ~1550 日 ≈ 75 万行, 一次性小表 | 未接 (Phase 1 序列已有 stk_limit/stock_st/suspend_d) |
| P0 | `moneyflow_ind_dc` (6000 分) | D1 通道 (概念资金流) | 下限 unknown, 探底 | ~1000 行/日, 极小 | 未接 |
| P0 | `stk_limit` / `stock_st` / `suspend_d` | 可执行性三件套 | 2019?/2016/unknown | 小表 | **已在 implementation_plan Phase 1 接入序** |
| P1 | `moneyflow` (2000 分) | 个股排序特征 | 2010 起 16 年 | ~120 万行/年 × 16 ≈ 1900 万行 | **need_027 进行中** (goal.md P1) |
| P1 | `dc_daily` (6000 分) | D3 通道 (概念指数) | 2020 起 (文档明确) | 490 × 1550 ≈ 76 万行 | 未接 |
| P1 | `limit_cpt_list` (8000 分) | 主线/生命周期 | 下限 unknown, 探底 | ~50 行/日, 极小 | 未接 |
| P1 | `moneyflow_mkt_dc` (120 分可试用) | regime 资金面 | 探底 | 1 行/日 | 未接, ROI 最高 |
| P2 | `cyq_perf` / `daily_basic` | 排序特征增强 | 2018/全历史 | 中等 | cyq 在 Phase 1 队列 |
| P2 | `ths_member` / `dc_concept_cons` / `kpl_concept_cons` | 口径交叉 + 产业链文本 | **无历史/2026-02 起** | 每日快照自养 | **今天就开始攒** (零成本, 全息图路线 2: 这种数据攒一天少一天) |

### 5.2 回测窗口的木桶约束 (诚实声明)

回测深度 = min(成分历史, 行情历史, K 线): K 线 (qfq) 现库 2022-01 起; limit_list_d/dc_daily 2020 起; **dc_member 历史下限 unknown — 它是木桶短板, 探底结果直接决定本策略能否严肃回测**。三种情形预案:

| dc_member 探底结果 | 决策 |
|---|---|
| ≥2022-01 (≥4 年) | 全速推进: 完整 walk-forward (扩窗月度, ~36+ OOS 折) |
| 2024 左右 (~2 年) | 降级推进: 折数少, 结论标"初步", 策略停在 candidate 状态边养数据边小仓验证 |
| 仅 2025+ (<1.5 年) | 不足以 walk-forward; 退路 = 用 `tdx_member` (880xxx, 与现库同源) 探底替代 + 概念层退化为"申万 L2 × limit_list_d.industry 东财行业"双粗粒度 (已知 L2 单独 ≈ 无 alpha, 需 industry 粒度实测), 同时每日攒 dc_member |

### 5.3 modal 跑批规模 (估算口径声明: 以下为 plan 输入, 跑前必须用 1 个实测 trial 校准, 不作为结论)

- 单 trial = 全期事件回放: 事件 ~1-3/日 × 候选 ~20-50/事件 × ~1000 交易日 ≈ 2-15 万 candidate-day 评估, 纯 pandas/duckdb 向量化预计单核分钟级 (参考: 本 session 60 日 ×1293 事件 ×79k 行回放 <1 分钟)
- Optuna 首轮 300 trials × walk-forward 折内重放 → 预计单机 (本地 8 核) 数小时量级; **不需要 modal** 即可完成首轮。modal 仅当进入 LightGBM 排序模型 + 多策略联合 ablation 阶段再评估, 且按 CLAUDE.md §9 需 reviewed adapter + experiment_jobs 契约
- 数据回填 API 成本: dc_member 探底 + 全量回填是大头 (~1.2k 次调用/年份数据); 按 vendor gateway 现行限频排程, 预计 1-2 天挂机完成 (writer 必须把 0 行当失败重试 — §4.3)

---

## 6. 验证计划

### 6.1 MVP (一周内, 全部本地可跑)

| 日 | 动作 | 产出/验收 |
|---|---|---|
| D1 | `dc_member`/`dc_index`/`moneyflow_ind_dc`/`limit_cpt_list` 历史**探底** (主 session 执行, 每接口二分查找最早可取日期); 同时启动 ths_member/dc_concept_cons 每日快照落盘 cron (走 launchd wrapper, §4.5 反例) | 四个接口的真实 data_start; 快照管道首日落盘成功 |
| D1-2 | `limit_list_d` 2020 起全量回填 + `stk_limit`/`stock_st` 接入 (Phase 1 既定) | 表落库 + data_audit PASS |
| D2-3 | **null 基线复算**: 把本 session 的 L2 粒度测试扩到 2022-2026 全窗口 (K 线可用全期), 含 M4 全部约束 + random-entry-same-exit 对照 | L2 粒度全期 expectancy 表 — 预期 ≈ 0 (M4 初步), 作为概念粒度必须显著超越的 null 基线 |
| D3-5 | 概念粒度版: 在 dc_member 已探到的历史区间跑 S1-S5 完整漏斗 (参数用表 4 范围中点, 不调优) | 漏斗统计: 事件/日 (黑名单后), 候选/事件, **不可成交率 + gap 分布** (盲点 6), 毛 expectancy |
| D5-7 | 四基准对照 + PIT 双口径敏感性 (盘后表用 t 行 vs t-1 行各跑一遍 — 量化"当晚可得性"值多少钱, 这是该策略最大的 PIT 风险点) + 首份诚实 readout | go/no-go 决策表 (见 6.3) |

### 6.2 正式验证 (MVP go 之后)

| 项 | 设计 |
|---|---|
| walk-forward | expanding_monthly (项目 R1 标准); train ≥12 个月起步; OOS 指标全走 `oos_*` 列约定 |
| 四基准 | HS300 / 等权 / 不换股 / **random-entry + same-exit** (同事件日同题材随机抽非候选成员, 同出场规则 — 全息图盲点 3: 这才是候选排序 alpha 的真对照) |
| 数字出口 | 只引用含成本 paper_sim replay (tx_cost + T+1 + 涨跌停 + 滑点); per-trade 口径数字一律不出门 (盲点 5) |
| 期望值账单 | 强制产出: 月事件数 × 入场率 × 单次 expectancy × 资金利用率 → 含成本年化区间, 与月胜率分布并列 (盲点 1) |
| 泄漏红线 | 概念成分一律 trade_date 快照 JOIN t-1, **严禁 ths_member 最新快照回测** (inst_path_a 反例同模式); RankIC>0.3/胜率>95%/相对基线 +50% → pit-audit 5 步复审; 黑名单维表版本化 (黑名单本身若用未来知识构建 = 软泄漏, 只允许按"概念类型"规则化定义, 不允许按"后来知道没涨"挑选) |
| 压力切片 | 2026-04 崩盘段 (样本内 D=343 极值日)、题材退潮日集中度压力测试 (盲点 5) |

### 6.3 go/no-go 决策门 (预先declared, 防事后挪门柱)

| 门 | 标准 |
|---|---|
| GO 继续投入 | 概念粒度 OOS expectancy (T+1 open 含成本) > random-entry 基线, 且差值在折间稳定同号 ≥70% 折; 不可成交率 <15% |
| 降级 (转特征) | expectancy 微正但不稳: 题材点火信号降级为主升浪引擎的"中观确认特征" (theme/LF 修复线, 全息图 B.4 #29 — 同一套数据, 不浪费) |
| NO-GO | gap 过滤后 expectancy ≤ 0: 承认扩散溢价已被抢跑干净, 数据资产 (概念 PIT/涨停事实/情绪温度计) 留给 regime gate 与其他流派, 策略线关闭 (§1.4 失败先承认) |

---

## 7. 北极星 KPI 贡献路径 + 失败模式

### 7.1 贡献路径 (机制账, 数字 = unknown 待 MVP)

| KPI | 路径 | 诚实备注 |
|---|---|---|
| 年化 ≥30% | 高频短持有 (2-5 日) × 月入场 10-30 次量级 (待实测) → 即便单次 expectancy 只有 1-2%, 复利贡献可观; **核心价值是填补主升浪引擎的空仓期资金利用率** (全息图盲点 1: 主引擎 0.7 次/月入场, 资金常年闲置) | 单次 expectancy 当前 unknown; M3/M4 初步显示朴素版无 alpha, 概念粒度是成败手 |
| 月胜率 ≥55% | 高频小赢结构天然平滑月度分布; 时间止损 + 题材退潮止损压尾部 | 60 日小样本 win 44.9% (未含候选排序与概念粒度) — 距离 55% 还有显著差距, 不许乐观 |
| max_dd ≥−20% | 本策略书 ≤20-30% 资金 + 单题材敞口帽 + regime stop 日零开仓; 题材退潮日强制清场 | 与主引擎的题材重叠是最大回撤耦合源, 必须合并 NAV 管 (§8) |
| 超额 HS300 >0 | 题材扩散收益与 HS300 低相关 (事件驱动, 中小市值为主) | 牛市题材期跑赢容易, 熊市看 regime gate 关停质量 |

### 7.2 失败模式 (什么时候这策略不工作)

| # | 失败模式 | 先兆/监控 | 应对 |
|---|---|---|---|
| F1 | **dc_member 历史太短** — 根本无法严肃回测 | 探底结果 <2 年 | §5.2 预案: 降级/退路/养数据 |
| F2 | **扩散溢价被抢跑** — 第二梯队 t+1 普遍高开超 gap_max, 可成交的都是没人要的 | 不可成交率上行 + 成交单 expectancy 转负 | 6.3 NO-GO; 或把入场推迟到 t+1 尾盘/t+2 回踩 (新假设需独立验证) |
| F3 | **无题材市/单边熊** (2018 式): 点火事件趋零 | 月事件数 <5 | 天然 gate (空仓是对的), 但本书收入归零 — 双 book 结构下可接受, 单策略生存不可接受 (本策略定位 = 卫星, 不是主粮) |
| F4 | **一日游市**: 题材切换过快, t+1 买入即是题材末日 | limit_cpt_list 新旧板块更替速度 + 持仓期题材退潮止损触发率飙升 | regime 加"题材持续性"维度; H 收缩; 最坏停做 |
| F5 | **风格概念污染** (M2 实证): 黑名单漏网 → 假事件吃掉资金 | 事件审计抽查: 每月人工复核 top 事件列表 | 黑名单维表 + 概念纯度过滤双保险 |
| F6 | **盘后数据当晚不可得** (PIT 风险): limit_list_d 等表实际更新晚于决策时点 | 生产前实测各表更新时点; PIT 双口径敏感性差值大 = 策略本质依赖不可靠的时间窗 | 若 t-1 口径下 edge 消失 → 策略不成立, NO-GO |
| F7 | **拥挤死亡**: 同生态量化太多, edge 半衰期短 | 滚动 6 个月 expectancy 衰减监控 | 接受策略寿命有限; 数据底座可复用, 沉没成本低 |
| F8 | 崩盘日连锁: 持仓题材集中遇 D=343 式极值日, 跌停卖不出 | regime stop + 单题材敞口帽 + 跌停顺延成本已计入 | 压力切片必测 (样本内有真实弹药) |

---

## 8. 与其他三流派互补性自评

> 声明: 其他三流派蓝图文件本 session 未读到 (不存在于 /tmp/cm_checkup/), 以下按任务描述的流派划分 + 全息图三路线推断, 标注假设。

| 对方流派 (假设) | 互补维度 | 重叠/冲突风险 | 协同建议 |
|---|---|---|---|
| A: 主升浪慢牛波段 (60-180 日, 全息图路线 1) | **时间尺度** (2-5 日 vs 季度级) 与**资金利用率** (填主引擎空仓期) 完全互补; 91%/9% 形态分布里, 本策略恰好做主引擎明确不碰的爆发型/情绪型 9% | 题材退潮日同向回撤: 主升浪持仓天然向主线题材集中 (V12 行业集中 56% 实证), 与本策略撞题材 | 双 book 合并 NAV 管总 max_dd (全息图路线 3); 同题材互斥规则 (§2 S6); 本策略的题材点火/退潮信号**反向输出**给主引擎做加减仓择时 |
| B: 资金流/筹码截面因子 (假设对应 implementation_plan Phase 2 资金流族+筹码族) | 共享数据底座 (moneyflow 16 年 / cyq_perf), 但用法正交: 对方做全市场日度截面 rank, 本策略做**板块聚合 + 事件跳变** | 个股 moneyflow 特征两边都用 → 模型层面有共线; 若对方也做"资金流 rank 动量", 候选重叠 | 数据接入一次两用 (need_027 已在跑); 特征注册制下共享 feature group, 各自 ablation 出增量; 候选重叠率作为月度监控指标 |
| D: 龙虎榜/游资爆发型 (假设: top_list/hm_detail/打板接力) | 同一生态的**不同位置**: 对方买龙头/上榜股 (高风险高赔率), 本策略明确不打板、买第二梯队 (低赔率高频) — 风格上是替代而非互补 | **全部流派中相关性最高**: 同一题材点火日两边同时触发, 等效双倍下注 | 若两派同时上线: 共用题材敞口预算 (theme budget 全局唯一); 或择一 — 按用户自家证据 (LHB 机构席位 lagging, precision 6%<base 9.5%) 本策略的"涨停事实+成分 PIT"路线证据面更干净 |
| 公共资产沉淀 (无论本策略成败) | ① 概念成分 PIT (dc_member) = 用户点名五个方向 (板块协同/概念协同/产业链/轮动/贴标签) 的共同地基; ② 情绪温度计 (炸板率/连板高度/晋级率) = 全项目 regime gate 的数据化替换 (§4.5 拍脑袋反例); ③ 涨停事实表 = formula_limit_up_pullback 的真相源升级 (现用价格推导有复权误差假涨停) | — | 这三件即使 6.3 走到 NO-GO 也全额保值 — 是本蓝图数据投资风险有限的根本原因 |

---

## 9. 治理挂载 (按 architecture_draft 六层 + implementation_plan 现实)

| 层 | 挂载物 |
|---|---|
| D1 | `sync_registry.yaml` 新条目: dc_member/dc_index/dc_daily/limit_list_d/moneyflow_ind_dc/limit_cpt_list (grain/pit_key/freshness SLA/0 行重试); 快照自养表 ths_member_snapshot/dc_concept_cons_snapshot |
| D2 | feature_registry 新 group: `theme_diffusion` (事件特征) + `theme_candidate_rank` (排序特征), 默认 production_ready=false 直到 ROI gate |
| D3 | 新 `formula_theme_diffusion.yaml` (本蓝图 §4 参数表全部入内, 非空 search space) — 注意当前 Pause Line: BestChoice 公式扩张被冻结, 本公式须等 universe/PIT/freshness/plan gates PASS 后才注册 |
| D5 | `strategies.yaml` 条目 `theme_diffusion_c` (status=candidate, base=paper_sim_config.yaml + overrides only) |
| D6 | 复用 paper_sim v2 全套 (exit_rules 需加"题材退潮"自定义 exit — 走现有 exit_rules 插件面, 不改 driver) |
| Gates | `backtest_preflight` 8 项 + `plan_validator` + pit-audit 5 步 + 跑批走 `chunkyctl jobs --family theme_diffusion --backend local` |

---

## 10. 一句话总结

赚涨停制度 + T+1 把题材扩散拉长成 2-4 天可预测订单流的钱; 地基是概念成分 PIT (dc_member, 第一动作 = 历史探底), 成败手是候选排序而非事件检测 (60 日小样本已证明: 无选择追扩散 = 负 alpha); 全部参数进 Optuna, 一周内 MVP 出 go/no-go, NO-GO 也沉淀三件全项目保值的数据资产。

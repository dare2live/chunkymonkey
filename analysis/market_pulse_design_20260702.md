# 市场感知 (Market Pulse) — Follow the Money 架构设计 v1 (2026-07-02)

> 生命周期：历史设计与实测证据（evidence-only）。现行市场感知边界由 `docs/MASTER_TOPLEVEL_DESIGN.md` 和 `docs/strategy_validation_contract.md` 拥有；实现状态与数据水位必须 live 重查。

> 当时状态: **B4 引擎已实现** (2026-07-02, services/market_pulse.py + 13 单测绿; 全量 rebuild 当时待跑); C4 前端页当时未建。
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
| **反哺因子层 (用户 2026-07-02 确认)** | pulse 指标族整体作为**板块上下文因子**: 个股 t 日特征 = 所在板块 (申万 L1 via B1 + 东财概念 via dc_member) 的 {资金流强度/RS 双窗/涨跌停广度/悄悄流入天数} as-of t | pulse 表本身 Type A PIT 干净, as-of join 无泄漏; 有效性未验 | 作为 D2 消融的独立一层 ("板块上下文层"), 与机构 episode 特征并列; 是否真反哺 = 消融说了算 |

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

**实现裁决 (2026-07-02 B4 实现定稿, 与上表字面的两处收敛)**:
- **rs_\* 列 = sw 链专属, dc 链恒 NULL** (vendor 红线保守读法; dc 概念 RS 若 C4 页面真需要再开, 用 dc_index.pct_change 自链算)。top_sectors_json 里 dc top/bottom 按 rank_flow (资金流) 排, sw 按 rs_rank。
- **turnover_amt_share = sw 链专属** (dc 源无成交额字段, 拒绝 turnover_rate×total_mv 估算 — measured not estimated)。
- rank_flow 由当日截面 net_amount DESC 重算 (源 rank 字段 = 分页伪 rank, 弃用)。
- 涨跌停计数: 源当日在场缺组 = 真 0; 源整日缺失 (2023 前) = NULL (不知道≠0)。

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

现存: ~~`analysis/serenity_20260611/`~~ **已被 `2d8f1dbb9`（2026-07-23 doc governance 删 62 份）删除，内容见 git history** 3 份 (METHODOLOGY_full / TRANSFERABILITY_critique / INTEGRATION_design,
80K 提炼版; 未见推文原始库)。方法论两部分:
- **sector rotation 流程** → 本设计 §1-§3 已吸收 (RS 排名/漏斗/警示信号)。
- **产业链上下游/关键瓶颈研究** → 定位"结构增强层" (钱沿产业链传导: 上游涨价→中游承压), 需产业链
  图谱数据 (dc 概念部分覆盖, 无严格上下游边) — **排后续**, 感知页 v1 不含; 若做, 先评估图谱数据源。

## 5. 落位 master plan

- **B4 市场感知引擎** (1-2天): 排 B2 形态识别之后 (B1 分层已备, 无阻塞可提前); 
- **C4 市场感知页**: C 线第 4 页 (档案/实盘模拟/工作台之后)。
- 信号层验证 (RS top3 过滤器是否提升 D 细分策略) = D2 消融的一个 lens, 不单独立项。
- **反哺因子层 (用户确认)**: mart_sector_pulse_daily 作 D2 "板块上下文层"特征源 — 主升浪起涨点是否更常发生在"资金流入+RS 上升+悄悄吸筹"的板块里, 消融验证; B4 引擎因此升为 D 阶段的前置依赖之一 (与 B1/B2 并列)。

## 6. 待拍板
1. 感知/信号两层切分 (感知页只描述不暗示买卖, RS 过滤器进 D 验证) — 同意?
2. quiet_inflow 定义 (|pct|<1% 且净流入, 连续天数) — 同意/调整?
3. B4 排序: B2 之后 (默认) 或提前到 B2 之前 (无依赖冲突, 若你想先有感知面) — 选?

---

## v2 增强设计 (2026-07-02 晚, 用户点名缺口 + tushare 全扫调研)

> 输入: 用户"没有概念热力图/没有板块龙头/涨跌数据入模了吗" + 调研 agent 241 接口全扫+20 次实弹核证。
> 入模澄清 (用户问): 感知层零建模是有意架构; 入模=D2 L1.5 板块上下文消融, 数据全备路径已排。

### v2 第一批 — 已在库零采集成本 (立即)
1. **content_type 透出** (缺口①): mart_sector_pulse_daily 加列, 行业/概念/地域分开; 前端热力图分 tab。
2. **龙头三件套** (缺口②): dc_index.leading/leading_code/leading_pct (涨幅龙头, NULL 率 0.2%) +
   moneyflow_ind_dc.buy_sm_amount_stock (资金龙头) 进板块行; 前端板块行显示双龙头。
3. **连板/情绪周期深挖**: limit_list_d 未用字段 limit_times/fd_amount/open_times/up_stat/first_time →
   全市场行加 最高板/n板家数分布/晋级率/秒板数/封单强度。**口径契约: limit_list_d 官方不含 ST**。
4. **limit_cpt_list 最强板块卡**: 已在库全没用 (2024+); 独立展示卡, 885xxx.TI 同花顺码**禁跨链 JOIN**。
5. **水位卡增强**: raw_tushare_margin (两融余额+日增, B3 刚回填) + index_dailybasic (大盘 pe/换手分位)。
6. dc_member × moneyflow_dc 链内聚合: 板块内个股流入宽度 (几成成分在流入, 抗龙头绑架); 成分下钻 API。
7. top_list/top_inst 日度聚合 (龙虎榜家数/机构净买) 进情绪卡。

### v2 第二批 — 新采集域 (实弹已核证, 按加源 SOP)
| 域 | 价值 | 史深 (实弹) | 档位 |
|---|---|---|---|
| **dc_daily** (东财板块 OHLC) | dc 链获得 RS 双窗能力 + 广度/龙头史深 2025→2020 | 20200102+, 1021 行/日 | 首选 |
| **kpl_list** (开盘啦) | 涨停原因文本/题材归因/连板状态 — 单表信息量最大 | 20200106+, 217 行/日 | 次选 |
| **hm_detail + hm_list** (游资) | 游资净买卖聚合=打板资金温度, v1 全无此维度 | ~2022-09+, 322 行/日 | 三 |
| **daily_info** (官方市场统计) | 沪深成交/换手/平均PE 官方口径 | 深史 | 四 |
| 缓: dc_hot (去THS化才需)/limit_step (limit_times 自算可覆盖)/dc_concept (接口 20260203 起太新) | | | |

### 不做 (调研否证, 留档防重查)
ths_* 全系 (vendor 红线) / ci_* 中信 (第三 taxonomy 桶不可比) / stk_account (实测 2019-02 停更) /
hsgt_top10 (2024-08 后 net/buy/sell 全 None) / slb_* (业务 2024-07 暂停) / fut_holding+cb_daily (整域配套成本>感知边际)。
**红线**: moneyflow_hsgt north_money **2024-08-19 语义断点** (净买入→成交额, 实测跳 13 倍) —
库内该表当流向消费=方向性错误, 只许当活跃度且跨断点禁拼接; 消费侧遇到必查此条。

---

## v3 设计 — 资金流形态分类学 + 全模块层级下钻 (2026-07-03, 用户定调)

> 用户定调 (原话要义): ①所有模块具备逐层展开基础能力 (L1→L2→…→个股) ②弃"悄悄"措辞, 用资金流入/
> 流出并**区分资金量** ③小色块只见近几天, 要能看一周/一个月 ④从目的出发深挖: 感知=感知钱去哪了 —
> **有大量涌入的、突然涌入的、静默累积流入的, 也有反过来流出的** — 感知加工到能辅助选股决策的程度。

### v3.1 资金流形态分类学 (flow_regime — 感知的核心加工件)

钱的流动 = 三个正交维度: **量级** (多少钱, 归一到板块流通市值可比) × **突然性** (对自身历史的异常度)
× **价格响应** (钱进来价格动没动)。派生量 (全部 config 化, Type A 无预测 claim):

```
flow_z       = 当日 net_amount 对自身近 120 日的 z-score        (突然性)
flow_streak  = 连续同号净流向天数                                (持续性)
cum_ratio    = 近 20 日累计净流入 / 板块流通市值 (%)              (量级, 跨板块可比)
px_cum       = 同 streak 窗口价格累计涨跌 (%)                    (价格响应)
```

**形态标签** (优先级序判定, 第一命中; 阈值全进 market_pulse.yaml):

| 标签 | 判定 (默认阈值) | 人话 | 选股语义 (D2 验证前只描述) |
|---|---|---|---|
| surge_in | flow_z >= 2.5 | **脉冲流入** | 事件驱动/题材引爆日 |
| accum_in_silent | streak >= 5 且 \|px_cum\| < 3% | **横盘累积流入** (原 quiet 语义) | 吸筹嫌疑 — 主升浪前的经典形态 |
| accum_in_driving | streak >= 5 且 px_cum >= 3% | **上行累积流入** | 趋势确认中 |
| surge_out / accum_out_silent / accum_out_driving | 镜像 | 脉冲流出 / 横盘累积流出 / 下行累积流出 | 退潮/出货形态 |
| neutral | 其余 | 无显著形态 | — |

落层: mart_sector_pulse_daily 加列 flow_z / flow_streak / cum_ratio_20d / flow_regime。
个股级 regime 不落全量表 (v3 下钻 API 实时算窗口 SQL, D2 需要特征化时再物化)。

### v3.2 全模块层级下钻 (基础能力, 数据全在库)

下钻链与数据源 (全部已核证在库):

```
sw 链:  L1 (31) → L2 (134) → L3 (346)  → 成分股
        RS/行情: sw_daily 588 码本就含 L1-L3 全级指数
        资金流:  个股 raw_tushare_moneyflow × v_sw_industry_pit (l1/l2/l3_code) GROUP BY 任意层
        成分:    index_member_all (PIT, is_new N 含历史)
dc 链:  行业/概念 (dc_index 自带 level 东财一/二/三级) → 成分股 (dc_member)
成分股层 (最后一层, 选股落点): 每股显示 近20日净流入/flow_regime(实时算)/form_name (B2 形态)/
        连板状态/是否龙头 → 点击跳机构档案页 (已有)
```

- 统一 API: GET /api/v3/pulse/drill?chain=&level=&code= — 返回下一层实体列表, 每行带该层
  pulse 指标 (净流入/RS/涨跌/flow_regime); 叶子层返回成分股带 form+flow+连板。
- mart_sector_pulse_daily 扩: sw 链补 L2/L3 行 (sector_code=801xxx L2/L3 码), 加 level 列
  (sw: L1|L2|L3; dc: 东财 level); RS 双窗对 L2/L3 同算 (sw_daily 有行情)。
- **vendor 红线不变**: sw 链下钻用申万成分/申万聚合, dc 链用东财成分/东财资金流, 禁跨。
- 前端: 热力图/轮动表/资金流向榜全部行点击 inline 展开下一层 (面包屑回退); 所有模块同一交互。

### v3.3 时间尺度 (方块串 → mini 温度条纹)

- 榜单行内嵌 **mini flow-stripe**: 近 60 日逐日净流入色带 (红入绿出, 白=平), GitHub contribution
  横向形态 — 一眼读出"这个板块的钱是怎么进来的" (脉冲? 匀速? 断续?), hover 显日值;
- 窗口切换全局化: 5D / 20D / 60D 聚合值 (累计净流入/RS/形态频次) 作用于榜单与热力图;
- 情绪温度条纹带 (v2 已做) 语义不变。

### v3.4 措辞与展示规范

- **前端措辞规范 (用户 2026-07-03 两次纠偏定调)**: 禁口语化/拟人化/戏剧化字样 ("悄悄""突然大量涌入"类),
  一律克制金融术语且对称成体系: 脉冲流入/流出 · 横盘累积流入/流出 · 上行累积流入/下行累积流出。榜单名 → **资金流向榜**
  (tab: 流入形态 / 流出形态), 每行显示 累计净额 (亿) + cum_ratio (占市值 %, 量级可比) + mini stripe;
- 数字优先: 净额一律带量级单位, 小数规范化; 红入绿出与全站 A 股语义一致。

### v3.5 目的穿透 (辅助选股的落点)

感知页 = 选股漏斗上游: 板块 flow_regime 榜 → 下钻到 L2/L3 找结构 → 叶子层个股
(form_name=低位横盘/突破 + accum_in_silent = 主升浪方法论的"长底吸筹"画像) → 跳档案页深看。
**flow_regime (板块级+个股级) 同时是 D2 L1.5 板块上下文/个股资金形态特征的候选** — 有效性
仍由消融裁决, 感知页零买卖暗示红线不变。

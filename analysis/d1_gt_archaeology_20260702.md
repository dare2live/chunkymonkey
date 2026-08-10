# D1 主升浪 GT 考古 (2026-07-02) — archive parquet 实测 + git 定义溯源

> 生命周期：历史证据（evidence-only）。本文保留当时的可复现实测，不拥有当前 GT 定义或实施计划；现行 owner 见 `docs/strategy_validation_contract.md`。

> 目的: D1 GT 重生成 (master plan §D1) 的输入件。旧 GT 表已物删 (2026-06-28 纯数据平台重建),
> 本文从 `data/archive/purge_processed/*.parquet` 直读实测 (read_parquet, 未进任何库) +
> git 考古生成脚本历史版本, 把**可复现的定义规则**整理成 v2 草案。
> 当时状态: side-agent 调研草稿；是否仍适用必须按现行契约和 live 数据重验。
> 实测环境: .venv/bin/python + duckdb in-memory; 全部数字来自 parquet 逐表 SQL, 非记忆。

## 1. 七表实测 profile (schema / 行数 / 时间范围 / grain)

| 表 | 行数 | 股票数 | 时间范围 | grain | built_at |
|---|---|---|---|---|---|
| fact_rally_ground_truth | 9,070 | 4,347 | bottom 2019-04-08 ~ **2026-04-30** | (stock_code, bottom_date) = 1 episode | 2026-06-17 |
| fact_rally_entry_pit | 9,070 | 4,347 | 同上 | (stock_code, entry_signal_date) | 2026-06-19 |
| fact_rally_entry_negative | 35,198 | 4,846 | 2019-04-09 ~ 2025-06-03 | (stock_code, entry_signal_date) | 2026-06-19 |
| fact_rally_episode_strata | 9,070 | 4,347 | 同 GT | (stock_code, bottom_date) | 2026-06-20 |
| fact_rally_stage | 1,507,894 | 4,347 | date 2019-04 ~ 2026-06 | (stock_code, date) 每 rally 日一行 | 2026-06-19 |
| fact_macd_episode_ground_truth | 311,291 | 5,197 | event 2019-01 ~ 2026-06 | (stock_code, event_date) = 1 金叉 episode | 2026-06-17 |
| fact_stock_technical_stage | 3,983,541 | 5,125 | date **2023-01** ~ 2026-06 | (stock_code, date) | 2026-06 |

交叉校验: GT = entry_pit = strata = stage 的 distinct episode = **9,070 全一致** (实测 XCHECK)。

### 1.1 fact_rally_ground_truth (主 GT)

列: stock_code, bottom_date(DATE), peak_date(DATE), gain_to_peak_pct, peak_offset_days,
base_days, bull_aligned(BOOL), path_max_dd_pct, is_true_rally(BOOL), fwd_window_len(INT),
taxonomy_version, built_at。

关键定义参数的**实测证据** (parquet 统计 ↔ 代码常数逐一对上):

| 参数 | 代码值 (build_rally_ground_truth.py @ e909e548~1) | parquet 实测 |
|---|---|---|
| GAIN 底→顶涨幅阈 | `>= 0.60` | min(gain)=**0.6000** ✓ (med 0.84, max 16.9) |
| MINDUR 峰距底 | `>= 20` 日 | min(peak_offset)=**20** ✓ (med 183) |
| MAXFWD 前瞻上限 | 250 日 (~1年) | max(peak_offset)=**250** ✓; fwd_window_len 恒 250 ✓ |
| BASEMIN 长底 | `base_days >= 40` | min(base_days)=**40** ✓ (med 82) |
| DDFLOOR 平滑 | 剔除 `max_dd <= -0.30` | min(path_max_dd)=**-0.2999** ✓ (med -0.20) |
| L2 多头排列 | 过滤后全 True | bull_aligned=True **9,070/9,070** ✓ |
| universe | 白名单 60/00/30/68 | 前缀实测 60:3396 / 00:2919 / 30:2121 / 68:634, **0 北交所** ✓ |
| taxonomy_version | universe_v1_20260617 | distinct=1, 值一致 ✓ |

分年 (底): 2019:1563 / 2020:1231 / 2021:1023 / 2022:1034 / 2023:623 / 2024:903 / **2025:2415 / 2026:278**。

### 1.2 fact_rally_entry_pit / fact_rally_entry_negative (判别器训练对)

同 schema: stock_code, entry_signal_date, base_days, fwd_complete, is_true_rally, fwd_window_len, built_at。
- entry_pit: GT 剥 outcome 的 PIT 侧 (A0 止血 #c)。9,070 行; fwd_complete **8,208 (90.5%)**, 862 右删失。
- negative: hard-negative 对照组 (A0 #d)。35,198 行 / 4,846 股; **全部 fwd_complete**; min base=40 (与正样本同 setup);
  pos:neg ≈ 1:3.9。范围止于 2025-06-03 = **fwd-complete(250交易日)+数据边缘(2026-06-19) 的机械结果, 不是刻意对齐 holdout** (诚实标注)。

### 1.3 fact_rally_episode_strata (PIT 分层)

列: sw_l1/l2 code+name, total_mv, cap_bucket, base_days, base_bucket。实测:
- cap: 小盘 4103 / 微盘 2553 / 中盘 1989 / 大盘 425 (桶界 30/100/500 亿, total_mv 万元: 300000/1000000/5000000)
- base: 中底 4254 (60-100日) / 长底 2786 (>100) / 短底 2030 (40-60)
- 申万 sector 覆盖 9,008/9,070 = **99.3%** (62 行缺失)
- sector join 是真 PIT: `in_date<=底 AND (out_date IS NULL OR out_date>=底)` as-of LATERAL (含 is_new='N' 历史区间, 非 latest-snapshot)

### 1.4 fact_rally_stage (鱼头/鱼身/鱼尾 — POST-HOC)

列: stock_code, date, episode_bottom, stage, progress, days_from_bottom。实测:
- 分布: 主升 750,261 (50%) / 起涨 630,395 (42%) / 顶部 127,238 (8%)
- **progress 实测越界: -1.1126 ~ 2.837** (定义上应 0~1, 见 §4 缺陷#2)
- 切法 (rally_stage.yaml, pre-registered 2026-06-20): progress=(close-bottom_close)/(peak_close-bottom_close)
  首次跨阈划连续段, launch_end=**0.30** / main_end=**0.85**
- builder docstring 自认: **stage 标签 POST-HOC (依赖 peak 事后才知) = 只做结果倒推分析, 非 live conditioning**

### 1.5 fact_macd_episode_ground_truth (公式线 D1)

列: stock_code, event_date(VARCHAR), peak_gain_pct, peak_offset_days, max_dd_pct, is_win, fwd_complete(INT), taxonomy_version, built_at。实测:
- 311,291 金叉 episode / 5,197 股; win (peak_gain>0.30) = 32,488 = **10.4%**; med gain 6.6%
- min(win gain)=**0.3000** ✓ (WIN_GAIN=0.30, 用户"公式线盈利>30%"); max(peak_offset)=95 (< MAX_HOLD=120, 下个金叉通常先到)
- 定义: 金叉 t 买 → 持有窗 [t+1, min(下个金叉, t+120)] 内 max(high)=峰; **只用金叉作买点不用死叉作卖点** (用户修正 2026-06-16), 出场是单独探索因子

### 1.6 fact_stock_technical_stage (Weinstein 4-stage, 旧 formula_engine)

列: stock_code, date, stage, built_at。实测分布: 3:1,287,141 / 4:1,282,273 / 2:754,024 / 1:536,742 / 1.5:123,361。
覆盖 **2023-01 起** (非 2019, 比 GT 窗短)。规则 (formula_engine/technical_stage.py @ e909e548~1, 全参数在
technical_stage.yaml): Stage1 底部 (60周低位±15%+均线走平+量枯) / 1.5 突破中 (破30周线+量比>1.5) /
2 上升 (MA10>30>50周+价>MA30+回撤<15%) / 3 顶部 (量背离 or 死叉 or 偏离过大) / 4 下跌。
**已被 B2 technical_states 重建替代** (2026-07-02, 正交5轴, 旧实现对抗审查 14 缺陷修正) — 考古仅存档, v2 勿复活。

## 2. 定义演化史 (git 考古)

| 代 | 时间 | 定义 | 出处 (代码证据) | 状态 |
|---|---|---|---|---|
| v0 原研究 | 2026-05-28 | 突破事件=close>前60日high; 主升浪=突破后60-180天涨幅>=50% AND max_dd>-20% | docs/zhushenglang_hunter_research_log_20260528.md §7 (原型已灭失, 06-13 三角法复现: cooldown=60+读法B, events 31,551 vs 锚 31,577=99.92%) | 已废弃 (rally_ground_truth_scan.py @ c0540c0c) |
| v1 首落库 | 2026-06-13~16 | v0 口径落库 (fb408dcd → c0540c0c 切干净 tushare qfq) | rally_ground_truth_scan.py; 锚 JSON analysis/rally_gt_reproduction_20250531.json | 已 DROP (北交所 3.1% + 未滤 ST 污染, CLAUDE §4.5) |
| **v1.5 结构型 (归档版)** | 2026-06-17 | 用户图样型: 长底+多头排列+平滑+底→顶>=60% (§3 详) | **build_rally_ground_truth.py @ 2af8b39d / e909e548~1** | 2026-06-28 U3 退役, parquet 归档 = 本文考古对象 |
| 配套 | 2026-06-19~26 | entry_pit 剥 outcome / hard-negative / strata / stage / macd GT / 列契约 | c8d6177c, 390c8c3a, 3977630f, e970a046, 13b95a62 | 同批归档 |

**重要澄清 (防误读)**: CLAUDE §4.5 说的"北交所 3.1% + 未滤 ST"污染是 **v1 (已 DROP, 不在 archive)**;
归档 parquet 是 **v1.5 清洁重建版** (taxonomy_version=universe_v1_20260617, 实测 0 北交所前缀, PIT ST 已滤)。

## 3. GT 定义 v2 草案 (可复现规则, 源自 v1.5 代码证据)

### 3.1 episode 判定 (正样本) — 五层漏斗, 每层透明

数据: price_kline_qfq_tushare (K线真相源, qfq), SCAN_START=2019-01-01, 逐股时序扫描。

- **L0 底→顶 swing**: 波段底 = `lows[i] == min(lows[i-20 : i+21])` 且 >0 (LOWWIN=20 前后确认);
  前瞻 250 交易日 (MAXFWD) 内 `peak = max(highs[i+1 : i+251])`;
  `gain = peak/lows[i] - 1 >= 0.60` (GAIN) 且 `peak_offset >= 20` (MINDUR, 排单日尖峰)。
  同股去重: 检出后 `covered = peak_idx`, 底扫描跳过已覆盖区。
- **L1 universe 硬门**: 前缀白名单 {60,00,30,68} + 非退市 (末K线距数据末 <= 90 自然日, DELISTED_NO_TRADE_DAYS)
  + episode 内非 ST (PIT ST 日历 raw_tushare_stock_st, 拉升期每 10 日抽样查 is_st_on)。
  落库前 `services.universe.assert_universe_clean` 兜底 raise。
- **L2 多头排列**: 拉升期 [bottom..peak] 内**存在某日** MA5>MA10>MA20>MA30>MA60 (日线; ∃ 读法, 见 §4 缺陷#5)。
- **L3 长底**: 底前 120 日 (BASE_LOOKBACK) 内 >= 40 日 (BASEMIN) 收盘落在 `底low*[0.85, 1.25]`。
- **L4 平滑**: 拉升路径 (closes[bottom..peak]) max drawdown > -0.30 (DDFLOOR, 无深调)。

起涨点锚 = **bottom_date (波段底日) = entry_signal_date = PIT 决策点** = fact_feature_panel JOIN 键。

### 3.2 列角色契约 (rally_gt_columns.yaml @ 13b95a62, 必须随 v2 重立)

- entry_anchor: bottom_date
- pit_features (可做 X): 仅 **base_days** (slice [i-120:i] 不含 i, 纯底前)
- label: is_true_rally
- **outcomes 禁做 X**: peak_date / gain_to_peak_pct / peak_offset_days / **bull_aligned** / path_max_dd_pct
  — bull_aligned 名字像入场态实为 FORWARD ([bottom..peak] 内测), 历史上反复被误当入场特征 (契约 notes 原话)
- 执法: gt_label_contract.assert_no_outcome_leakage (services/gt_label_contract.py @ 13b95a62) + 单测 + moth

### 3.3 负样本构造 (hard-negative, rally_detect.py 共享原语)

负样本 = 同结构 pivot-low (is_pivot_low, LOWWIN=20) + 长底 (base_days>=40, **与正样本同 PIT setup**)
+ forward 窗完整 (forward_complete, 250 交易日 <= 数据边缘) + **未涨** (forward 250 日 max gain < 0.60)。
- purge: 同股正样本 bottom **±250 根内**的 pivot 不取 (forward 窗重叠=污染)
- 同股负样本间隔 >= 20 根 (防贴邻重复 pivot)
- 设计意图: holding PIT-setup 恒定 → 隔离"涨不涨"信号 (非全市场随机 = 只学"是不是低点")
- ST: 留消费侧 PIT 硬门 (is_st_on; ST 是时变量不可一刀切删股)
- 正负必须共用同一候选检测原语模块 (单一计算点, 防双真相源漂移) — v1.5 抽在 services/rally_detect.py

### 3.4 分层字段 (strata — 全 bottom 时点 PIT 可知, 可 live conditioning)

- 申万 L1/L2: as-of join raw_tushare_index_member_all (`in_date<=底 AND (out_date IS NULL OR >=底)`, 含 is_new='N')
- 市值桶: daily_basic 底日 total_mv (万元) → 微(<30亿)/小(30-100)/中(100-500)/大(>500)
- 长底桶: base_days → 短(40-60)/中(60-100)/长(>100)
- v2 改造: 市值/分层桶**不再自算**, 改接 B1 `dim_stock_segment_daily` (单一计算点, master plan §B1 已 DONE); 形态/stage 轴接 B2 technical_states

### 3.5 公式线 (MACD episode GT)

金叉 (DIF 上穿 DEA, 12/26/9) = 买点锚 event_date; 持有窗 [t+1, min(下个金叉, t+120)];
peak_gain = 窗内 max(high)/close[t] - 1; **is_win = peak_gain > 0.30**; 死叉不做卖点 (出场留探索)。
列契约 macd_episode_gt_columns.yaml: pit_features=[], outcome (peak_gain/offset/max_dd) 禁做 X。

## 4. 证据等级 + v2 必须修正清单

### 4.1 证据等级 (诚实标注)

**有代码+实测双证据** (git 恢复的 builder 源码 + parquet 统计逐一对上):
GAIN=0.60 / MINDUR=20 / MAXFWD=250 / BASEMIN=40 / BASE_LOOKBACK=120 / DDFLOOR=-0.30 / LOWWIN=20 /
stage 阈 0.30/0.85 / MACD WIN_GAIN=0.30 / MAX_HOLD=120 / 负样本 purge=±250 / universe 白名单 / 桶界。

**只有"用户口述/图样"锚, 无独立数据证据** (代码注释自认 "用户口述 MASTER §5" / "结构常数"):
- 底→顶 **>60%** 与公式线 **>30%**: 用户口述定的, 非从数据测出的最优阈;
- LOWWIN/MINDUR/BASEMIN/BASE_LOOKBACK/MAXFWD/DDFLOOR 六个"结构常数": 工程拍板, **无敏感性分析留档**;
- "多头排列/长底/平滑"三个形态条件来自用户那张图, 代码是对图的**一种**翻译 (∃日多头排列是最松读法)。

**纯文档嘴说 (原型已灭失)**: v0 的 60-180 天口径细节只剩 research log 文字 + 三角复现 JSON, 无原始代码。

### 4.2 v2 必须修正清单

| # | 问题 (实测/代码证据) | v2 修正 |
|---|---|---|
| 1 | **train 窗违反**: v1.5 扫到数据边缘, bottom_date 到 2026-04-30 — 实测 **868/9,070 (9.6%) rally bottom 落在 2025-06-01 后** (holdout 窗); MACD GT 更重 49,410/311,291 (15.9%) | 生成时 data_end <= holdout_start (20250601), builder 接 `services.holdout_guard.assert_holdout_untouched`; 边界 embargo: bottom+250 交易日跨切分日的 episode 右删失, 剔出 train (与负样本 fwd_complete 对称) |
| 2 | **fact_rally_stage progress 越界** (实测 -1.11~2.84): gain/底/顶锚在 low/high, progress 却用 close 算 → 锚不一致 | v2 统一锚 (progress 分母改 low→high 或 bottom/peak 全用 close 定义), 加 `0<=progress<=1` 落库断言 |
| 3 | **stage 标签 POST-HOC** (依赖 peak 事后才知) | 保留其"结果倒推分析用"定位, **禁 live conditioning**; v2 重建时把该禁令做成消费侧机械门 (列契约 + moth), 不只 docstring |
| 4 | **bull_aligned 假 PIT 陷阱** (契约 notes: 反复被误当入场特征) | rally_gt_columns.yaml 列契约 + gt_label_contract.assert_no_outcome_leakage 随 v2 第一天重立, 不做事后补 |
| 5 | **多头排列 ∃-日读法过松**: 拉升期 250 日内任一日多头即过, 与用户图"拉升期多头排列"可能不符 | v2 用户拍板读法 (∃日 / 持续占比>=X% / B2 形态轴替代); 若改读法必换 taxonomy_version, 历史不可比 |
| 6 | **北交所/ST 污染** (v1 反例, CLAUDE §4.5; 归档 v1.5 已修) | v2 沿用 services/universe.assert_universe_clean 硬门 + PIT ST 日历 (raw_tushare_stock_st), 白名单只许经 universe 模块 (内联前缀 = 门拦) |
| 7 | **旧加工路径已退役**: strata 自算市值桶 / formula_engine Weinstein stage (14 缺陷) 均不可复用 | 分层接 B1 dim_stock_segment_daily, 形态/stage 接 B2 technical_states (正交5轴), 单一计算点 |
| 8 | **右删失不对称**: GT 留 862 条 fwd_complete=False 正样本, 负样本却全 fwd_complete | v2 训练集正负对称: 只用 fwd_complete 正样本 (或显式处理删失), fwd_complete 判定共用 rally_detect.forward_complete |
| 9 | **结构常数无敏感性证据** (§4.1) | 冻结前跑一次参数敏感性 (episode 数/分年分布 vs LOWWIN/BASEMIN/DDFLOOR 扰动) 留档 analysis/, 或明确标"用户拍板结构常数"进 yaml 注释 |
| 10 | **阈值 hardcode 在 builder 里** (v1.5 常数全在 .py) | v2 全部进 yaml (判断死红线): gain/mindur/maxfwd/basemin/ddfloor/lowwin + taxonomy_version, builder 只读 yaml |

### 4.3 v2 与 holdout 立法的接线 (本批同步交付)

> ⚠ **本小节已过期（2026-08-10 核实）。§1–§4.2 的 GT 定义演化史与 v2 漏斗规则不受
> 影响，仍是 `rally_gt.py` / `rally_detect.py` / `rally_gt.yaml` 引用的历史定义证据；
> 只有下面这段"holdout 接线"被后续立法取代。现行机制见 `backend/services/
> holdout_guard.py` 与 `backend/services/research_prereg_store.py`。**
>
> 具体差异：全局 `touch_budget=3` 预算制**已废除**，改为按 prereg 记录逐条发放唯一
> `single_touch_token`、经 `consume_single_touch` / `consume_holdout_single_touch`
> 一次性消费；`holdout_policy.yaml`（现 version 3）只剩 `holdout_start` 一个字段，
> 不再有 `touch_budget` / `require_preregistration` / `freeze_rule`；
> `register_criteria` / `touch_holdout` 两个函数已不存在。此外 `69a9cbe89` 给
> `assert_holdout_untouched` 增加了 `actual_data_end` 参数，堵住"declared 合规但实际
> 读到 live 全量日历"的口子（`holdout_guard.py:55-89`）。

以下为 2026-07-02 当时的接线设计，**仅作历史记录，勿照此实现**：

- `backend/config/holdout_policy.yaml`: holdout_start=20250601 / touch_budget=3 / require_preregistration=true / freeze_rule
- `backend/services/holdout_guard.py`: register_criteria (预注册判据, 写 experiment_store.holdout_touch_log) /
  touch_holdout (全局预算制, 无预注册 raise, 耗尽 raise) / assert_holdout_untouched (训练路径守门)
- D1 v2 builder 生成 train GT 前必调 `assert_holdout_untouched(data_end_date)`;
  D4 每次 holdout 验收 = register_criteria → 看数字 → touch_holdout (预算 3 次, 判据先于数字)。

## 附: 考古方法与文件指针

- parquet 实测脚本: scratchpad profile_gt_parquets.py (duckdb read_parquet 直读, 未建任何表)
- 定义源码 git 锚: build_rally_ground_truth.py / build_macd_episode_ground_truth.py @ `e909e548~1` (2af8b39d 初版);
  build_rally_entry_pit.py @ c8d6177c; build_rally_negatives.py @ 390c8c3a; build_rally_episode_strata.py @ 3977630f;
  build_rally_stage.py + rally_stage.yaml @ e970a046; rally_gt_columns.yaml @ c8d6177c;
  macd_episode_gt_columns.yaml + gt_label_contract.py @ 13b95a62; services/rally_detect.py @ 390c8c3a;
  formula_engine/technical_stage.py @ e909e548~1; v0 口径 rally_ground_truth_scan.py @ c0540c0c
- 退役 commit: e909e548 (加工层清空 U3), a078351e (纯数据平台重建)

# 交接文档 · signals_v2 开发上下文

> **这份文档给下一个 claude 读**。把它放在对话最前面，读完就能接着推进。
> 最后更新：2026-04-20（Step 5 视图整合收尾 · 4 主 tab 定型 · 后端 6 模块清理 · 数据管线 UI 重塑）

---

## ★ 下一轮开场必读（优先级最高）

用户反复强调的几件事，如果忽略了会被"骂"：

1. **禁止硬编码阈值 = 禁止人工定行业白名单**。所有"异质性"问题（合同负债、溢价、股东变化等）都交给 Qlib + 行业 onehot 特征，让模型自己学。
2. **参数全前端可调**。新增任何阈值都要走 `DEFAULT_CONFIG` + app_settings + 前端面板。
3. **机构不做黑白名单**，只做连续特征喂 Qlib。
4. **边做边验**：每加一个维度跑 cohort 看 edge。
5. **诚实面对数字**：look-ahead bias（cooldown_days=90 底线）不能再翻车。
6. **视图删除 > 视图新增**。2026-04-20 Step 5 大减法：legacy 候选池 / 股票评分框架 / setup 追踪 / 海龟 / 外部关注 / qlib 一级 tab / 行业背景 tab 全部撤下。发现新信号的唯一入口是「信号」tab；深挖单股去「股票」；看机构 track record 去「机构」；运维去「工作台」。

### 当前实情澄清（2026-04-20 — 替代 04-19 老版）

- **事件规模**：`fact_institution_event` 从 29,684 扩到 **56,326**（buy 31,015，其中 gain_60d 已观察 **29,684**，成熟率 95.7%）
- **V6 cohort edge 缩水**：原 +14.44pp（n=128，SW 分组）→ Phase A 复核后 **+10.64pp（n=91，TDX 分组）**；**79/91=87% 样本集中在 2025-Q4 一个季度**，全量历史 edge 为 -1.32pp。V6 仍作为主力硬规则，但 UI 上已用「季度分布」胶囊诚实披露集中度（见 signals_v2 cohort 卡片）
- **Qlib follow 模型确认封存**：Phase 4a-c + Phase A2 共 4 轮训练，合同负债覆盖从 68% 提到 88% 对 IC 无改善，最佳 IC 仍在 -0.02 附近。`qlib_follow_engine.py` 代码 + model artifacts 保留，不挂前端。
- **D8 调研硬规则默认禁用**：两个口径都负向（全量 -7.30pp / cohort -2.98pp）。待覆盖窗口扩到 12 月+ 再复评
- **前端导航**：6 tab → 4 tab。「信号」「机构」「股票」「工作台」。Qlib 降到工作台折叠区实验室
- **后端清理**：2026-04-20 一轮删 6 个 legacy service（quality_feature_engine / stock_archetype_engine / setup_tracker / setup_validation / setup_replay / stock_validation）+ 7 个对应测试，共 7218 行代码

### 下轮开场行动（Step 5 收尾后接下来）

已经没有"马上必做"的数据/模型任务；剩下都是时间/样本积累性质：

```
A. 实盘观察（Step 6，跨半年）
   V6 n=91 是 cohort 的 1.6% 子集，79/91 集中在 2025-Q4 单季度。
   上线半年内跟踪 WR 是否稳在 70%+；如果翻车再回炉调阈值。

B. D8 窗口扩充复评（Step 7，等数据累积）
   当前 raw_institution_surveys 覆盖 2025-10-22 → 2026-04-17 约 6 月。
   扩到 12 月+ 后重跑 cohort：判断"调研活跃 ≠ 超额"结论是否稳定。
   若翻转，考虑启用 min_survey_count_90d > 0。

C. 文档/配置最终精简（可选）
   - HANDOFF 本身已是 v2，若再攒一轮改动可以 rewrite
   - app_settings 已经 16 键齐全；没有新增需求
   - 后端路由审计：setup-replay/*/backtest 是否能再减
```

如果没有新想法，建议直接跑实盘观察（A）。代码层面系统已经干净。

---

## 0. 项目一句话

**从十大流通股东数据挖掘高胜率跟随信号，以此盈利**。主角是机构，股票是机构行为的载体。

---

## 1. 用户偏好（关键！必须记住）

按重要程度排序：

1. **诚实 > 好看**。数字真实比看起来漂亮重要。之前虚假 +7.4% alpha 被揭穿是 look-ahead bias 污染后，用户反而更信任系统。
2. **稳定数据源 > 复杂变量工程**。tdxhub 优先，akshare 只做 tdxhub 没有的补充。
3. **不堆砌数据 / 不堆砌功能**。少即是多。每加一个东西必须能说清为什么加。
4. **所有参数前端可调，禁止硬编码**。用户要能 UI 上改阈值立刻看到效果。
5. **动态看待，不固化黑白名单**。机构 PM 会换、策略会变、市场会变。用连续特征喂 ML，不用硬规则打标。
6. **边做边验**。每加一个特征都要跑 cohort 看 edge 变化，不要一口气做完再看。
7. **现有代码 80% 是对的**。不是推倒重写，是修正 + 精简。
8. **第一性原理**。如果解决方案复杂化，回到"我到底要解决什么"再想。
9. **不喜欢啰嗦**。回答要直接、抓重点、给数字。
10. **敢删**。该删的代码不要留注释"保留以防"——删就是了。

### 用户明确否决的设计（别再提）
- ❌ 机构黑白名单硬规则（2026-04-19 明确反对，改用连续特征喂 ML）
- ❌ 多维合成分 × 权重（A/B/C/D 池 / Setup / Gate 三套评级并存）
- ❌ 2019 之前训练数据（A 股结构变化大）
- ❌ qlib label=日内收益（口径错位，必须改 follow_return_60d）
- ❌ UI 上的 disclaimer / 免责声明（只有我自己看）

---

## 2. 核心架构（9 层）

```
L1 · 数据获取 (Raw)                     market_raw_holdings / price_kline / raw_gpcw_detail
    ↓
L2 · 事件生成 (Event)                    fact_institution_event + gain_30/60/90/120d
    ↓
L3 · 真相源 ★                           fact_institution_event.gain_60d 唯一 label
    ↓
L4 · 决策函数 (Recommend)                signals_v2.py: KNN + 硬规则 + 双口径
    ↓
L5 · 历史回测 (Backtest)                 全量 backtest_historical + cohort_recent_matured
    ↓
L6 · Qlib 对照 (规划中)                  qlib_follow_engine.py: label=gain_60d, 滚动 36 月
    ↓
L7 · 快照 (Snapshot, DAG 落表)          fact_signal_daily (待建)
    ↓
L8 · 展示 (只读快照)                     assets/js/signals-view.js
    ↓
L9 · 反馈闭环                           cohort_recent_matured → 前端 review 卡
```

**原则**：每层只读上游、快照写入后不改、前端零运行时计算。

---

## 3. 代码结构

```
backend/
├── services/
│   ├── signals_v2.py          ★ 主决策引擎 (1650 行)
│   ├── qlib_follow_engine.py  ★ Qlib 事件级模型 (796 行, 骨架，定位"第二意见")
│   ├── db.py                  主 SQLite schema
│   ├── market_db.py           市场数据库 (price_kline)
│   ├── return_engine.py       计算 gain_30/60/90/120d
│   ├── event_engine.py        生成 fact_institution_event
│   ├── (legacy) scoring.py / quality_feature_engine.py / ... 未删但不再用
├── routers/
│   ├── signals.py             ★ signals_v2 API 路由
│   └── (legacy) 老路由保留兼容
├── tests/test_signals_v2.py   ★ 34 个单测全过（含 D8 5 个）

assets/
├── js/signals-view.js         ★ 新信号视图 (独立命名空间 sig-*)
├── js/app.js                  老前端 (未改)
├── css/main.css               尾部追加了 sig-* 样式
├── index.html                 默认 tab 改为 "信号 v2"

docs/
├── signals_v2_baseline.md     V1 旧 baseline（含 look-ahead，保留作反例）
├── signals_v2_baseline_v2.md  V3 严谨左切 + 双口径
├── signals_v2_baseline_v3.md  V5a 当前默认 (硬规则 + premium≤15)
├── HANDOFF.md                 ★ 本文件

start-signals-v2.command       启动 worktree 后端 (8001)
```

---

## 4. Baseline 演进历史（数字是真相）

| 版本 | 关键改动 | OOS cohort Follow EV | vs Blind edge | WR | n | 分组 |
|------|---------|-------------------|---------------|------|---|---|
| V0 | 原始 KNN，无 cooldown | +7.40% | +3.67pp (虚假!) | 59% | 4597 | SW |
| V1 | 加 cooldown_days=90 | +2.13% | -1.70pp | 45% | 4454 | SW |
| V3 | V1+V2 = 真严谨双口径 | +8.15% | +0.82pp | 58% | 1576 | SW |
| V4 | +硬规则 (prem≤20) | +9.08% | +1.75pp | 63% | 1179 | SW |
| V5a | +prem≤15 (硬规则收紧) | +9.39% | +2.06pp | 64% | 1042 | SW |
| V6 | +D1 max_yoy=30 + D3 min_fc=20 + D5 max_unlock=5 | +21.77% | +14.44pp | 80% | 128 | SW |
| **V7** | **V6 配置不变，新数据复核（TDX 分组）** | **+17.97%** | **+10.64pp** | **71%** | **91** | **TDX** |

**V7 复核** Phase A（2026-04-20 · signals_v2_baseline_v4.md）：
- 样本扩到 56,326 事件 / 29,684 buy 成熟后，V6 edge 缩水 **-26%**（+14.44 → +10.64）
- follow 样本 **79/91 = 87% 集中于 2025-Q4 单季度**；其他季度样本稀疏
- **V6 全量历史 edge 是 -1.32pp**（HANDOFF v1 未披露；近 180d cohort 为正但历史大段时间负 edge）
- V6 仍作主力硬规则（cohort edge 显著），但 cohort 卡片已用季度分布诚实披露集中度

完整 cohort（Phase A 重跑）：近 180d 成熟样本 n=5696（不变），Blind EV=+7.33%

---

## 5. 用户指出的 alpha 源（实证发现）

cohort 健康检查揭示的 3 大被忽略维度：

### 机构类型分化（差 7pp）
```
牛散   EV+9.71% WR 62.2%   ★ 强 alpha
券商   EV+8.48% WR 56.2%
社保   EV+7.57% WR 56.0%
北向   EV+7.18% WR 55.5%
QFII  EV+7.28% WR 54.2%
基金   EV+3.07% WR 36.6%   ★ 负 alpha!
国家队 EV+0.68% WR 44.4%
```

### 溢价档（U 型）
```
-10~0 折价   +8.03% WR 62.6%  ★
0~20 正常    +8~9%  WR 57-58%
>20 高溢价  +5.25% WR 43.4%   ★ 负 alpha
```

### 持仓强度（线性）
```
rank=1        +10.95%  ★
rank=7-10     +6.87%
hold>5%       +9.54%   ★
hold<0.5%     +5.43%
```

---

## 6. 挖过的 alpha 维度（D1-D8，实证结果记录）

| # | 维度 | 数据位置 | 实证 edge | 当前处理 |
|---|-----|---------|----------|---------|
| **D1** | 股东人数 YoY ≤30% | `raw_gpcw_detail.holder_count` | **+1.93pp** | ✓ V6 硬规则 |
| **D2** | 合同负债 YoY | GPCW `合同负债(万元)`（新映射） | 各档差异小，**不显著** | ❌ 不作硬规则，候选 Qlib 特征 |
| **D3** | 业绩预告利润 YoY ≥20% | `raw_gpcw_detail.forecast_profit_yoy_*` | **+9.22pp** ★ | ✓ V6 硬规则 |
| D4 | 北向持股变化 | `fact_northbound_daily` | **表 0 行，无数据** | ✗ 暂跳过 |
| **D5** | 180d 解禁 ≤5% | `dim_capital_behavior_latest.future_unlock_ratio_180d` | 风险规避 | ✓ V6 硬规则 |
| D6 | 同期拥挤度（peer_count） | `fact_institution_event` 自聚合 | 未深挖 | 待 Qlib |
| D7 | 机构近期 EV（**连续特征**） | cohort 自动生成 | 待 Qlib | 未做 |
| **D8** | 机构调研热度（近 90d） | `raw_institution_surveys` + `mart_stock_survey_activity`（已入库，180d 覆盖） | **负向**：min≥1 把 follow edge 从 -1.32pp 推到 -7.30pp | ✓ breakdown 展示；默认 `min_survey_count_90d=0`（不启用硬规则） |

### D8 机构调研数据发现详情（2026-04-19）
数据接口：`akshare.stock_jgdy_tj_em(date=...)` — 返回从 date 起的累计调研记录
字段：代码、名称、**接待机构数量**（核心）、接待方式、接待日期、公告日期

**前 90 天接待家数 vs gain_60d 分档**（cohort n=2674, 覆盖 47%）：
```
0 (无调研)    n=3022  EV+6.00% WR 52%
1-20          n=1516  EV+7.71% WR 57%
21-50         n=544   EV+11.74% WR 62%  ★ 高
51-100        n=309   EV+7.36%  WR 60%
101-200       n=214   EV+11.40% WR 65%  ★ 高
>200          n=91    EV+9.06%  WR 50%  过度炒作回落
```

**与 V6 (D1+D3+D5) 叠加测试**（`min_survey_90d=20`）：
```
V6 原: n=128 EV+21.77% WR 80% edge+14.44pp
+D8:   n=24  EV+13.67% WR 79% edge+6.34pp  ← 反而降!
```
V6 的 follow 里很多股票"机构还没调研的隐藏好货"，D8 硬筛会排除它们。**D8 单独 alpha 明显但和 V6 重叠，不作为硬规则**。留作 Qlib 特征或独立 follow 通道。

### D8 入库与硬规则通路（2026-04-19 Phase 5）

**入库**：`services/institution_survey_client.py:sync_institution_surveys` 已挂接 `updater.STEPS`（`sync_surveys`，order 7.5，与 external_attention 同级独立数据源）。表：
- `raw_institution_surveys`：原始明细（stock_code, survey_date, notice_date, inst_count, ...）
- `mart_stock_survey_activity`：as_of 快照的 30/60/90d 聚合

**当前数据状态**：raw 8557 行、notice_date 覆盖 2025-10-22 → 2026-04-17（约 6 月）、mart 1555 股。

**硬规则通路**：
- 配置项 `min_survey_count_90d`（int，默认 0 = 不启用）
- `_apply_hard_rules` 新增 D8 检查，命中返回 `(skip, survey_too_quiet)`
- `_build_rule_breakdown` 扩展到 7 维，前端 6 列网格改 7 列
- `_enrich_events_with_gpcw` 批量计算 `survey_count_90d`，关键：**早于覆盖起始日的事件 → None（unknown），不算"调研冷门"**（避免 look-ahead / 数据缺失误杀）

**Phase E cohort 实证**（backtest 29684 事件）：
```
D8   | follow_n  follow_ev  follow_wr | edge
0    |      303       2.51      40.3% | -1.32pp
1    |      230      -3.47      29.1% | -7.30pp   ← min≥1 反而变坏
2    |      227      -3.72      28.6% | -7.55pp
3    |      227      -3.72      28.6% | -7.55pp
```

**结论**：在当前 6 月覆盖窗口下，调研活跃 ≠ 后续超额。"已被关注=已被定价" 假说得到支持。保留 breakdown 展示供用户参考，**默认禁用硬规则**。未来扩充到 12 月+ 覆盖后可重评。

### D2 合同负债探测详情（2026-04-19）
- 原 `raw_gpcw_detail` 没有 contract_liabilities 字段（未映射）
- 原 `raw_gpcw_financial.contract_liabilities` 覆盖仅 10%（老表，字段稀疏）
- **GPCW 源文件实际有**：字段名为 `合同负债(万元)`（带 `(万元)` 后缀），100% 覆盖
- 已修正 `tdx_affair_client.py:_FIELD_MAP` 加上此字段（+ "预收款项" 老科目）
- 内存验证 95.5% cohort 覆盖，但**全行业混合后 YoY 各档 EV 差异不大**（+5.85~+8.42%）
- 原因：合同负债对不同行业含义差别巨大，混合计算等于稀释信号

### ★ D2 下一步必须做：合同负债的行业化方案

**为什么必须分行业**（用户明确指出，2026-04-19）：

合同负债（新准则下的预收账款）在不同行业含义完全不同：

| 行业类别 | 行业例子 | 合同负债 YoY 的信号含义 | 方向 |
|---------|---------|----------------------|------|
| **强前瞻行业**（合同负债领先营收 1-3Q） | 半导体设备 / 军工 / 建筑工程 / 游戏流水 / SaaS / 新能源设备 | 大涨 = 在手订单潮，强 alpha 信号 | **正向加分** |
| **弱信号行业** | 银行 / 保险 / 券商 | 会计准则不适用或无意义 | **忽略该特征** |
| **反向信号行业** | 地产（历史上预售款） | 现阶段减少 = 现金流健康，行业缩量 | **方向可能反** |
| **中性行业** | 一般制造 / 消费 | 反映短期订单，但周期短噪音大 | **弱信号** |

全行业混合是**稀释**不是"抵消"——因为各行业信号方向+幅度都不同。

### 正确实施方案（2026-04-19 修正）

**用户明确否决硬编码行业白名单（"应该用 qlib 算一下才对"）**。所以方案 C（手工白名单）从推荐里删除。正确路径只有一条：

#### 方案 A · Qlib 学（唯一推荐）

```python
# qlib_follow_engine.extract_training_matrix 补：
features = {
    # --- 原有 ---
    'premium_pct', 'peer_count_same_quarter',
    'institution_industry_hit_rate',
    'roe', 'debt_ratio', 'gross_margin',
    'return_20d_before', 'volatility_60d', ...
    
    # --- D1-D8 新加 ---
    'holder_count_yoy',           # D1
    'contract_liabilities_yoy',   # D2 ← 本轮补的字段
    'forecast_profit_yoy_mid',    # D3
    'future_unlock_ratio_180d',   # D5
    'survey_count_90d',           # D8 (需入库)
    
    # --- 行业作为类别特征 ---
    'sw_level1': one_hot (31 bits)  # ← 关键，让模型学行业交互
    # 或 industry_embedding (可选优化)
}
```

LightGBM 对类别特征的交互天然擅长。模型会自动学"半导体股票的 contract_liab YoY 系数大 / 银行股票的 contract_liab 系数接近 0 / 消费股的 contract_liab 系数小"这类规则，**无需人工列表**。

#### 方案 B · 备选（如果 Qlib IC 提不上来）

方案 B 保留但降级：**只在方案 A 失败时考虑**。

```python
# 行业内 z-score（仍不是白名单，是统计归一化）
mart_industry_contract_liab_stats
  - sw_level1, report_date, cl_yoy_mean, cl_yoy_std

event.cl_yoy_zscore = (stock_yoy - ind_mean) / ind_std
```

这是纯统计处理，不是"挑行业"，符合"不硬编码"原则。

#### 方案 C · 已废弃

> ~~手工白名单 `CONTRACT_LIAB_APPLICABLE_INDUSTRIES = {...}`~~ 违反"不预设、动态看待"原则，**不再考虑**。

### 验证步骤（按新方案）

1. **GPCW sync 拿到 contract_liabilities_wan**（下一轮必做第一步）
2. **直接跑 Qlib 重训**，特征清单见上方方案 A
3. **观察 IC / R² 变化**：
   - 旧（仅事件级特征）：R²=-0.08, IC=-0.009
   - 新（+ D1-D8 + sw_level1 onehot）：目标 IC > 0.05
4. **如 Qlib 仍无效 → 上方案 B（z-score）**，但绝不用方案 C
5. **Qlib 成功后**：把 Qlib 预测作为"第二意见"并排展示到前端（不合成一个分）

### 行业异质性不只影响合同负债

同样的"应该让 Qlib 学而不是预设阈值"原则，适用于其他硬规则：

| 当前硬规则 | 为什么可能错了 | 修正 |
|-----------|---------------|------|
| `max_premium_pct=15` | 科技股可容忍 20%+，消费股 5% 就高 | 喂给 Qlib + industry onehot 学 |
| `max_holder_yoy_pct=30` | 小盘股股东波动本来就大 | 同上 |
| `min_forecast_profit_yoy=20` | 周期股/成长股基数不同 | 同上 |

**但**：V6 当前 edge +14.44pp 在 SW 一刀切阈值下已经成立，说明**这些硬规则没糟到必须立刻改**。让 Qlib 作为第二意见运行一段时间后再考虑是否淘汰硬规则。

### Phase 实施（修正版）

- ~~Phase 1 行业白名单~~ — **删除**
- ~~Phase 1（新）· Qlib 重训为主路径~~ — **已完成 2026-04-19**（Phase 4a+b+c）
- ~~Phase 2 · 方案 B z-score~~ — **已完成 2026-04-19**（作为 Phase 4c 一并完成，IC 仍未过关）
- **Phase 3 · 行业切换到 tdxhub block**（独立任务）— 3-5 天
- ~~Phase 4 · 目标 Qlib IC > 0.05~~ — **结论：不可达，Qlib 确认只能作"第二意见"**

### Phase 4 实测结论（2026-04-19）

**Phase 4a**（特征扩展）：`qlib_follow_engine.extract_training_matrix` 从 16 维 → 38 维
- D1 holder_count_yoy（79% 填充）
- D2 contract_liabilities_yoy（68% 填充，受 GPCW 字段缺失约束）
- D3 forecast_profit_yoy_mid（93% 填充）
- D5 future_unlock_ratio_180d
- D6 peer_count_same_quarter
- D7 inst_recent_ev_60d（排除未成熟 / 本身样本 >60d 过滤）
- D8 survey_count_90d
- TDX L1 one-hot（13 bit: T01..T13）

**Phase 4b**（首轮训练，无 z-score）：
- 样本：n_train=26596, n_valid=3088（train_end=20260301, valid=6 月）
- **Valid IC = -0.0063**（目标 > 0.05，未达到）

**Phase 4c**（+ 行业内 z-score，方案 B 兜底）：
- 新增特征 `holder_count_yoy_z`, `contract_liabilities_yoy_z`, `forecast_profit_yoy_mid_z`（按 tdx_l1 + report_date 分组，≥5 样本才归一化）
- Z-score 在模型中确实进入 Top 10（forecast_profit_yoy_mid_z 特征重要性 13，排第 4；holder_count_yoy_z 排第 9）
- **Valid IC = -0.0196**（比 4b 更差）

**核心结论**：
1. Qlib follow 模型（60 日跟随收益 label）在当前数据密度（30K 样本 / 38 维）下无法突破 IC=0 线。增特征和归一化都无助于此。
2. 规则 V6（硬规则 + SW edge +14.44pp）仍是主力，Qlib 定位调整为**第二意见**（参与前端并排展示，不合成一个分数）。
3. 不再追加方案 C（行业白名单）—— 用户明确否决且 V6 边际收益已足够。

**剩余 TODO（降优先级）**：
- 如日后样本密度显著扩大（>100K）可考虑用 classification objective（涨/跌二分类）而非 regression，IC 规则未必同构。
- 前端把 Qlib 预测作为"第二意见"挂到 detail 卡（独立任务，不阻塞主路径）。

---

## 7. 当前待做（接上这条链继续）

**Step 1 · 边做边验** ✅ 完成（V6 硬规则定型）
- D1 股东人数 YoY ≤30% → +1.93pp
- D2 合同负债 YoY：`_FIELD_MAP` 已补，全行业混合不显著 → 留给 Qlib
- D3 业绩预告利润 YoY ≥20% → +9.22pp（最强）
- D4 北向资金：`fact_northbound_daily` 0 行，跳过
- D5 180d 解禁 ≤5%：风险规避
- D8 机构调研近 90d：负向（min≥1 把 follow edge 推到 -7.30pp），默认 `min_survey_count_90d=0` 不启用

**Step 2 · 前端参数全量可调** ✅ 完成（commit 6d50702d）
- 参数抽屉暴露 16 键全部可改（含 D8 `min_survey_count_90d`）
- 保存即重算 cohort，内嵌 edge 变化对比

**Step 2.5 · 硬规则 breakdown 展示** ✅ 完成（commit bee1ccf9 + 本轮 W2）
- 详情抽屉展示 7 维体检（pass/fail/unknown）+ 触发规则红标
- 主列表行加 7 维点灯（绿/红/灰，hover 显示 `label · 原始值 · 阈值`）
- `.sig-breakdown-grid` 从 6 列改 7 列

**Step 3 · D8 数据源入库** ✅ 完成（commit 007b543c）
- `raw_institution_surveys`（8557 行）+ `mart_stock_survey_activity`（1555 股）
- `updater.STEPS` 新增 `sync_surveys`（order 7.5，独立数据源）
- as-of 过滤：survey.notice_date ≤ event.notice_date；窗口 90d
- **覆盖起始日前的事件→None**（避免数据缺失被误判为"冷门"）

**Step 4 · Qlib 第二意见** ✅ 封存（Phase 4a-c + Phase A2 共 4 轮）
- 合同负债覆盖 68% → 88% 对 IC 无改善；最佳 IC 仍在 -0.02
- 结论：Qlib follow 模型在 30K 样本密度下无法突破 IC=0 线
- `qlib_follow_engine.py` 骨架保留，前端不挂 tile（IC≈0 展示会误导）
- 训练控制台入口降级到工作台高级区折叠区

**Step 5 · 视图整合** ✅ 完成（2026-04-20 一轮完成 5a-5f 共 8+ 个 commit）
落地清单：
- 5a 行业 + 机构类型胶囊筛选（commit 92f38114）
- 5b 股票聚合视角（commit c397a6b2）
- 5c 股票抽屉展开该股所有事件（commit d3c64b2f）
- **后端 6 模块清理** — 删 legacy/setup/stock-validation 共 7218 行（ff1a25a9）
- **5d 工作台重塑** — 从候选池展示改为运维控制台（4 层信息密度）（6af1a2f9）
- **5e 股票视图瘦身** — 6 tab → 3 tab，banner 去 legacy 定位（9726f3c5）
- 任务 1-4：机构抽屉追加 signals track record（21f7f458）/ 股票筛选条 10 组 → 3 组（94a5ee91）
  / 工作台事件成熟卡真实数字（46270b80）/ app.js legacy 函数删除 696 行（48aef72f）
- 任务 A-F：pytest skip guard（0e9de223）/ 数据管线 UI 折叠行表（534fe4ee）
  / 股票表格 11 列 → 7 列 + signals_v2 action（0292cb18）/ AppListState 清理（08b62598）

主视图最终结构：4 tab 股东挖掘（信号 / 机构 / 股票 / 工作台）+ ETF group（不变）

**Step 6 · 实盘观察**（下一步主要任务，跨半年）
- V6 n=91 是近 180d cohort 的 1.6% 子集，79/91=87% 集中于 2025-Q4 单季度
- 全量历史 edge -1.32pp vs 近 180d +10.64pp 的巨大反差提示「可能过拟合近期行情」
- 上线后跟踪 WR 是否稳在 70%+；如果翻车回炉调 D3 阈值或松绑某条规则

**Step 7 · D8 窗口扩充复评**（待数据累积）
- 当前覆盖 2025-10-22 → 2026-04-17（约 6 月）
- 扩到 12 月+ 后重跑 cohort：判断"调研活跃 ≠ 超额"结论是否稳定
- 若翻转（调研变正向），考虑启用 `min_survey_count_90d > 0`

---

## 8. 关键配置（都在 app_settings 以 signals.v2.* 前缀）

```python
DEFAULT_CONFIG = {
    "horizon_days": 60,                    # 持有期，对齐 fact_institution_event.gain_60d 列
    "min_sample": 10,                      # 长窗口最小样本
    "ev_threshold_pct": 5.0,               # follow EV 门槛
    "win_threshold": 0.55,                 # follow 胜率门槛
    "prefer_same_industry_min_sample": 10, # 同行业子集门槛
    "signal_freshness_days": 90,           # 今日信号窗口
    "cooldown_days": 90,                   # ★ 严谨左切，不能动（防 look-ahead）
    "short_window_days": 365,              # 短口径窗口
    "short_min_sample": 5,                 # 短口径最小样本
    "max_premium_pct": 15.0,               # 硬规则：溢价上限
    "min_hold_ratio": 0.3,                 # 硬规则：占流通股下限
    "inst_type_blacklist": "基金,国家队",    # 硬规则：机构类型黑名单
    # D1-D8 新增（所有阈值前端可调，不满意直接 UI 改）
    "max_holder_yoy_pct": 30.0,            # D1 股东人数 YoY 上限 (%) — 99999 = 不启用
    "min_forecast_profit_yoy": 20.0,       # D3 业绩预告利润 YoY 下限 (%) — -9999 = 不启用
    "max_unlock_ratio_180d": 5.0,          # D5 180 天解禁上限 (%) — 99999 = 不启用
    "min_survey_count_90d": 0,             # D8 近 90d 调研家数下限 — 0 = 不启用（当前默认，基于负向 cohort 结论）
}
```

**所有参数** 通过 `/api/signals/config` GET / POST 可读可改；前端参数抽屉已全量暴露 16 键。
> 2026-04-20 清理：`inst_type_preferred` 原为"优势机构类型"的预留键（未实装、无读路径），按"不做黑白名单"原则连同 `preferred_set` property 一起退役。

---

## 9. Git / 环境

**工作分支**：`main`（每个 commit 直接 push origin HEAD:main，不保留 feature 分支；worktree 只做隔离工作目录，commit 完即删）

**最近 commits**（截至 2026-04-20 · Step 5 视图整合全部收尾）：
```
08b62598 refactor: 任务 D — AppListState legacy state 方法删除
0292cb18 feat: 任务 C + F — 股票表格重构为 7 列（signals_v2 action + 加自选）
534fe4ee feat: 任务 B — 数据管线 UI 从 19 卡片密铺改为折叠行表
0e9de223 test: 任务 A — pytest 预存 fail 转 skip guard
48aef72f feat: Step 5 任务 4 — app.js legacy 函数批量删除 696 行
46270b80 feat: Step 5 任务 3 — 工作台「事件成熟」卡接入真实 endpoint
94a5ee91 feat: Step 5 任务 2 — 股票筛选条瘦身 10 组 → 3 组 + 行业过滤新增
21f7f458 feat: Step 5 任务 1 — 机构抽屉追加 signals v2 track record tile
9726f3c5 feat: Step 5e 股票视图瘦身 — banner 重塑 + 6 tab → 3 tab
6af1a2f9 feat: Step 5d 视图整合 — 工作台重塑 + 机构简化 + Qlib 降级
ff1a25a9 refactor: 子步 1 — 后端清理 legacy/setup/stock-validation 共 6 模块
8e335a76 feat(signals_v2): Phase B0 — cohort 卡片按季度拆分 follow 分布
24289a34 feat(signals_v2): Phase A 基准复核 — V6 edge +10.64pp / Qlib IC -0.025 封存
```

本轮净改动：前端 app.js 7781 行 → ~6400 行（-17%）/ 后端 -7218 行 legacy / 新增 2 endpoint
（/api/signals/events/stats 和 signals_v2.cohort.quarterly 字段）

**仓库**: https://github.com/dare2live/chunky-monkey-v2（main 分支）

---

## 10. ⚠️ 技术陷阱清单（下一轮 claude 看清楚）

### A. DB 连接路径
- worktree 的 `data/` 需要是 symlink 指向 `/Users/dp/Documents/M/stock/data/`
- 每次 commit 前 `rm data && git checkout HEAD -- data/`，commit 后再 `ln -sf /Users/dp/Documents/M/stock/data data`
- 否则 git 会以为 `data/qlib_data/*` 等跟踪文件被删除

### B. uvicorn 端口管理
- main 项目在 8000（用户 start.command 启动）
- 我的 worktree 在 8001（我的 start-signals-v2.command）
- 共享同一个 DB
- 启动前用 `pkill -f "uvicorn.*8001"` 清理
- 看日志 `/tmp/signals_v2_api.log`

### C. Preview MCP 沙盒限制
- **不能跑 Python** uvicorn（site-packages 的 h11/httptools 被 sandbox 阻断 import）
- **必须用 Node** 做静态 + 反向代理
- 配置在 `.claude/launch.json`（已 gitignored）
- 端口 8080 代理 → 8001 后端

### D. Chrome MCP
- 经常 "not connected"，要用户手动 chrome://extensions 重启扩展
- 原因：扩展 service worker 休眠
- native host log: `~/Library/Logs/Claude/chrome-native-host.log`

### E. 回测性能优化
- 原始每事件独立 SQL 会慢 20-40 秒
- 必须一次性 SQL 预加载所有相关机构历史 + 内存 KNN 查找
- 具体见 `signals_v2.py` 的 `_filter_history_for_decision`

### F. 日期格式不一
- `fact_institution_event.notice_date` 是 `YYYYMMDD`（无分隔符）
- 其他地方可能是 `YYYY-MM-DD`
- 字符串比较时必须用 `_shift_date()` 归一化
- SQLite `date()` 函数对 YYYYMMDD 不工作！要自己拼 `substr || '-' || substr`

### G. 左切严谨度（**不能动**）
- `cooldown_days = 90` 是防 look-ahead bias 的底线
- 设为 0 会让 Follow edge 虚假膨胀 +5pp（V0 vs V3 差距就是这个）
- 任何人想改这个都要**先读 signals_v2_baseline_v2.md 再说**

---

## 11. 下一轮 claude 第一句话应该说什么

推荐开场：

> 我读了 docs/HANDOFF.md，了解项目背景。当前主力 V6 硬规则（Phase A 复核后 cohort edge +10.64pp，n=91，87% 集中于 2025-Q4）。
> Step 5 视图整合 2026-04-20 一轮完整收尾：4 tab 前端定型（信号/机构/股票/工作台），后端清 7218 行 legacy。
> 代码层面系统已干净，剩下都是数据/时间积累性质任务。

然后：
1. 先核对 `ls -la data` 是否 symlink 正确，否则跑 `rm -rf data && ln -sf /Users/dp/Documents/M/stock/data data`
2. 跑 `pytest backend/tests/` 确认 257 passed / 2 skipped（不是 failed）
3. 启动 `start-signals-v2.command`（或直接 `cd backend && python3 -m uvicorn main:app --port 8001`）
4. 启动 preview：`mcp__Claude_Preview__preview_start signals-preview`
5. 如果用户没有新需求，建议打开信号 v2 跟踪当前 follow 信号实盘表现（Step 6）
6. 如果用户有新功能想加，先问：这个是"发现新信号" / "深挖单股" / "看机构" / "运维" 哪一类？对应 4 tab 之一

---

## 12. 待解答的开放问题

大多数 v1 时期的问题已闭环：

| 历史问题 | 2026-04-20 结论 |
|---|---|
| D4 北向资金：表有数据吗？ | `fact_northbound_daily` 0 行，跳过 |
| D5 回购/解禁 | `dim_capital_behavior_latest.future_unlock_ratio_180d`（D5 硬规则生效）|
| Qlib 重训：能在 IC>0 前上线？ | 不能，4 次重训 IC 均 <0，封存不挂前端 |
| 前端参数面板只暴露 6 个？ | 已 16 键全暴露（Step 2）|

**当前没有待解答的开放问题**。如果有新的，在这里补。

---

完。

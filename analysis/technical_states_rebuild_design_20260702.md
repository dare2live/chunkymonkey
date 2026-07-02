# B2 形态识别重建 — 设计定稿 (2026-07-02)

> owner: 主会话。输入: 旧实现对抗审查裁决 (`technical_states_audit_20260702.json`, 19-agent workflow,
> 14 confirmed / 0 误报 / 40 keeps, 全部实测证据) + F0 普查 + F1 重设计 + v2 评审。
> 用户指令: "旧实现先验证设计正确合理, 该修正的修正" — 本文档 = 验证后的取舍定稿。
> 定位: **Type A 描述层** (master plan §1.1) — 截至 t 的 K线 → t 日形态标签, 每日 process 步跑;
> 形态=结构层非 alpha (F1 裁决), alpha 验证归 D2。

## 1. 审查裁决 → 设计决策 (核心表)

| # | 审查裁决 (实测证据) | 设计决策 |
|---|---|---|
| C1 | **CRITICAL**: 放量突破瞬时态 = F0 证伪的裸突破 (71% 高位触发, forward 全场最差) | 瞬时"放量突破"态**删除**; 改 **event-in-context 突破检测器** (overlay, §4): 底盘前置 + 触发 + 可成交闸 — 这也是 D1 GT 起涨点的直接输入 |
| C2 | **CRITICAL**: 代码级 bull/bear/mtf_aligned 方向语义有害 (aligned-bull win 35-44% 全场最差) 且双份 hardcode 不一致 | **删除全部代码级方向语义**; 多 TF 只输出各框描述态, 无方向 claim; 方向/确认判断只许 D 阶段用含成本 forward gate 立 |
| H1 | resample 尾部未闭合周期 bar: 决策日 weekly 23%/monthly 38% 被未来改写 (train/serve skew 实测复现) + 周初量比只有完整周 20% | **resample 只输出已闭合周期 bar** (交易日历判闭合); live 周三的 weekly = 上周五闭合 bar, 与批量回填逐 bit 一致 |
| H2 | sigmoid 连乘量纲随条件数 3/4/5 变化 (PARTIAL: Optuna 吸收大半, 残余 8.6-11% 边界翻转 + "加条件=静默压缩量纲") | 态分改**几何均** `(∏g)^(1/k)` + 除以联合 max_score 归一到 [0,1]; 全部阈值随之重定 (非沿用旧值) |
| H3 | 一字跌停检测死分支 (is_one 只涨停侧; 一字跌停=卖不出的最典型场景) | is_one 双侧修复 + 一字跌停单测 |
| H4 | 可成交性标注全链缺失 (flags 算了没人消费) | 输出层必带 `buyable` (非收盘封涨停) / `sellable` (非收盘封跌停) / `is_one_word` — 回测消费方"看不见"成为不可能 |
| H5 | coupling 自动镜像 mutation 已违反声明 (0.68 vs -2.36) | coupling.py **整体删除**; 边界约束改 config load 时只读校验 (违反 fail loud) |
| H6 | 阈值来源=无监督可分性 0.816 伪精度 (取整后标签仅变 1.56%) | 阈值全部**理论锚定取整值** (Weinstein/Wyckoff/O'Neil 文献锚 + 人话注释); B2 **不跑 Optuna** (无 forward 消费方, grill 纪律); 寻优留 D2 |
| H7 | 9 态混轴 (位置轴三种处理方式不一致); 缩量回踩死态根因=连乘压死 5 条件态 | **正交轴重建** (§2): 每轴独立分类, softmax 只在单轴内竞争; 前序依赖态 (回踩) 走 context 两遍架构非瞬时态 |
| H8 | 波动率 regime 轴真缺 (range_pct ρ=0.71 部分代理, zvol ρ=0.006) | 特征 +3: `rv_pctile` (已实现波动率 120 日分位) / `pth` (52周高贴近度) / `rs_ratio` (vs HS300 Mansfield); vol-regime 分桶**落 B1 dim_stock_segment_daily 加列** (与市值/换手同表同 PIT 口径), 形态模块只消费 |

**40 项 keeps 直接沿用** (全部实测验证): 日线 12 维特征窗口 PIT 0 diff / 声明式 config evaluator (手算 vs 实现 0 mismatch) / context 两遍架构 (前序 <=t-1) / patterns 3 模板零参数不回贴 / limits stk_limit 真相源 + 涨停量比 proxy 方向 / candles 一字板特判 + prior_trend 消歧 / 熵与 er/maxdd/accel 公式 / ISO 周分组。**放量下跌态携带进重建** (实测全场最强 forward: fwd10 median +1.70%, win 56%)。

Medium/low 31 项修正随实现顺手做, 关键几条: pctile tie 虚高 (死平股误判高位) 改严格分位; 零成交量日不再静默填中性 (标 not covered); MA 窗含当日对齐教科书定义; 三套条件 DSL 收敛为一套; 周边模块 `cfg=None` 内置默认值双真相源禁止。

## 2. 轴系统 (正交, F1 蓝图落地)

| 轴 | 取值 | 特征 | 文献锚 |
|---|---|---|---|
| A 位置 | low / mid / high | pctile(120d) + pth(52周高) | George-Hwang (强) |
| B 趋势方向 | up / flat / down | ma_slope + ma_align | Weinstein (条件桶, 非信号) |
| C 趋势纯度 | trending / choppy | er + r2 | Kaufman (中) |
| D 量能 | shrink / normal / heavy | vol_ratio + zvol (涨跌停修正后) | Wyckoff volume |
| E 波动 regime | low_vol / high_vol | rv_pctile (**B1 表新列, 本模块消费**) | 低波异象 (强) |

- 每轴独立小分类器 (sigmoid 门 + 几何均归一, 轴内 softmax); **跨轴不竞争** — 混轴 9 态 argmax 的结构病根被移除。
- **人话标签 = cell → 标签映射表** (config): 如 `low+flat+choppy+shrink → 低位横盘(地量)`; `high+flat+*+heavy → 高位滞涨(放量派发)`; 9 态词汇保留给档案/前端 (v2 治理: 档案层保全), 但由 cell 派生 — 单一计算点, 无第二真相源。
- 前序依赖态 (缩量回踩/中继) = context 两遍架构 pass-2 派生 (沿用, PIT 已验), 不做瞬时态。

## 3. 模块骨架 (16 文件 → 7 + 1 config)

```
services/technical_states/
  features.py     日线特征 12+3 维 + resample(只闭合bar)   [沿用+修 H1/H8/medium]
  axes.py         5 轴独立分类 (通用 evaluator, 读 config)  [替代旧 classifier 打分层, 修 H2/H7]
  labeler.py      cell→人话标签映射 + context 两遍 + 多TF as-of (无方向语义)  [修 C2]
  breakout.py     突破 event-in-context 检测器 (底盘+触发+可成交闸)  [新, 修 C1, D1 输入]
  limits.py       涨跌停/一字板 flags (stk_limit 真相源)    [沿用+修 H3]
  candles.py      单日K构件 (prior_trend 消歧)              [沿用]
  patterns.py     命名形态 3 模板 (零参数, 不回贴)          [沿用]
config/technical_states.yaml   轴判据/cell映射/突破参数/涨停proxy — 理论锚定取整值, 每条带文献注释
```

**8 个档案挂件不进 B2** (审查裁定范围蔓延, 新地基已各有归属): capital→资金流归 B4 pulse + moneyflow 数据; chips→cyq 数据; rs→B4 sw 链 RS; sector_context→B4; fundamentals→财务模块; regime→B4 market pulse; events (UTAD/Spring) 后延 (v2 8号问题未解); coupling→删 (H5)。

## 4. 突破检测器 (event-in-context, D1 GT 的起涨点原语)

三层 (参数全 config, 理论锚 VCP/O'Neil):
1. **底盘 context**: 位置轴=low/mid 持续 >= base_min_days (默认 20) + 波动收缩 (rv_pctile 下行) + 缩量;
2. **触发**: 收盘破 N 日高 (默认 60) + 量比 >= vol_mult (默认 2.0, 涨停日用 proxy);
3. **可成交闸**: 一字板 → `tradable=false`; 收盘封涨停 → `buyable=false`; T+1 语义标注。
输出 overlay 事件行 (非态): (stock, date, base_days, trigger_strength, tradable)。
F0 教训明标: 突破胜率 44.5% 是接刀 — 检测器只做**描述与 GT 标注原语**, 不是买入信号。

## 5. 产物表 fact_stock_form_daily (smartmoney, L1k)

```
stock_code, trade_date,
axis_pos, axis_trend, axis_purity, axis_vol, axis_volregime,   -- 5 轴值
axis_pos_memb, axis_trend_memb, ...                          -- 各轴归一分 [0,1]
form_name, form_sub                                            -- cell 派生人话标签
weekly_name, monthly_name                                     -- 已闭合周期 as-of (无方向claim)
is_breakout_event, base_days, buyable, sellable, is_one_word    -- 事件 overlay + 可成交
built_at
```
消费方: D1 GT 标注 (长底+突破) / D2 L0 裸K 层特征 / C 前端档案维度① / B4 感知页 stage 分布。

## 6. 单测计划 (旧 400 行模式照搬 + 审查暴露的缺口)

1. PIT 截断不变性: 加未来 bar, 全特征+全轴+标签逐位 0 diff (旧模式扩到 15 特征 5 轴);
2. **决策日 live=batch 一致性** (新, H1 的证伪门): 随机截断点 live 视角 vs 全量回算, weekly/monthly 标签 100% 一致;
3. 一字跌停检测 (新, H3); 零成交量→not covered (新); pctile tie (新);
4. 几何均量纲不变量 (新, H2): 3 条件轴与 5 条件轴在"每门同满足度"下分数相等;
5. 突破检测器: 无底盘的高位放量破新高 → 不触发 (C1 的证伪门); 一字板触发日 tradable=false;
6. config 契约: 每轴判据可加载/取整阈值/单位一致; cell 映射全覆盖 (任意轴组合有标签, 无静默 fallback)。

## 7. 验收

- 全量跑通 2019+ 全市场, 份额分布 sanity (无 >50% 单标签, 无 <0.1% 死标签 [事件除外]);
- PIT/一致性单测全绿 (上述 1-2 是硬门);
- 抽查 5 股人工核对 (茅台/宁德时代等的已知走势段 vs 标签语义);
- ~~与 archive fact_rally_stage 对齐~~ **废弃该验收** (master plan 原文写的这条不成立 — v2 评审已裁定旧 fact_rally_stage 是 POST-HOC 未来 peak 切分, 拿它当基准=拿被证伪物当真相源); 替代 = 上面三条。

## 8. 明确不做 (B2 范围外)

- Optuna 阈值寻优 (无 forward 消费方; 留 D2 与消融一起, 目标函数=标注 episode 一致率或下游效用);
- 边界事件层 UTAD/Spring (v2 未解, 后延); 命名形态扩编 (维持 3 模板);
- 方向/确认/买卖信号 (C2 裁决, D 阶段 forward gate 立);
- vol-regime 桶的计算 (落 B1 segments.py 加列, 本模块只消费 — 单一计算点)。

# ChunkyMonkey 最终策略组合方案 (FINAL strategy portfolio)

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。


> 生成: 2026-06-11 | 作者: 首席策略综合师 (Claude)
> 输入: 3 份蓝图 (B-reversal-plus / C-theme-diffusion / D-rank-enhance, blueprint_A 始终未产出, 三评委均未对其评分) + 3 评委结构化打分 + 评委批注 + user_vision_hologram.md + repo 只读核验
> 北极星 KPI (owner: goal.md): 年化 ≥30% / max_dd ≥-20% / 超额 HS300 >0 / 月胜率 ≥55%
> 诚实声明: 本文所有"推算/设计目标/unknown"标注均为约束性的, 不是修辞。组合整体能否达 KPI = unknown, 直到 W8 含成本 paper_sim 出数。

---

## 0. 综合判决与引用口径声明

### 0.1 评委共识 (3 评委独立打分)

| 蓝图 | 评委1 | 评委2 | 评委3 | 共识定位 |
|---|---|---|---|---|
| B-reversal-plus | 8.2 (第1) | 7.5 (第2) | 8.3 (第1) | **主干**: 全项目唯一实测 OOS 正收益地基 + 执行端最贴合 T+1 个人投资者 (回调买点天然低高开) |
| D-rank-enhance | 7.4 (第2) | 8.5 (第1) | 7.0 (第2) | **测量仪 + 公共层**: 最诚实最严谨, 但自认非独立策略 (RankIC 0.015 vs 所需 0.20 差一个量级), 近期 P&L 拉动最弱 |
| C-theme-diffusion | 6.4 (第3) | 6.5 (第3) | 5.0 (第3) | **降级为数据资产采集**: 设计与诚实度高, 但正面 alpha 证据为零 + 木桶数据 (dc_member 历史) unknown + 最卷生态最高换手 — 恰好走 C 自己 §6.3 预设的降级路径 |

三评委对综合结构的建议高度一致: **B 为主干 (V0 本周启动), D 的逐族 ablation 作为 B 确认层选型的测量仪 (D 测出哪族有截面信息, B 决定哪层进 gate), C 降级为数据资产先行 + 策略缓议**。本方案照此拼装, 不另起炉灶。

### 0.2 地基数字引用口径 (强制收敛, 三评委一致实锤)

PROJECT_INDEX.md 摘要区 (L43) 与 §10 表 (L601) 对 `reversal_1m_mild × stage=1.5` 自相矛盾 (+0.435/58.5% vs +0.342/51.9%)。本方案及全部下游文档**统一以 §10 实表为准**:

| formula × stage | avg OOS sharpe | avg win | avg single ret | 角色 |
|---|---|---|---|---|
| `reversal_1m_deep × stage=1` | **+0.392** | **58.1%** | +5.22% | **地基旗舰** (58% 胜率叙事唯一合法支撑) |
| `reversal_1m_mild × stage=1.5` | +0.342 | 51.9% | +4.49% | 次选组合 (胜率地基 <55%, 不得再引摘要区高口径) |

→ **上游待修项 (主会话执行, 本方案前置依赖)**: 收敛 PROJECT_INDEX.md L43 摘要区与 L601 §10 表双口径 (评委批注三票点名"它已污染第一份下游引用")。

### 0.3 单一最高优先级判决实验 (评委3 共同风险提示)

B 与 D 高度共享数据域: **若 moneyflow 全期截面信息含量为零 (D 的 20 日前哨 RankIC +0.0016/-0.0121 已暗示此可能), B 的 L2 层与 D 的 F1 族同时报废** — 两蓝图失败相关性高于各自自评。处置: 把 **"moneyflow 截面信息含量判决实验"** 前置到第 3 周 (见 §4), 一次跑批同时裁决两个流派的核心假设; 判负则两者同步降级 (B 退回地基版 + L2 仅退出端使用, D-F1 转 regime/卖侧用途), 不留恋。

---

## 1. 三套策略组合方案

### 套一 (主书): Reversal-Plus v2 — 回调增强 (B 主干 ⊕ D 测量仪)

**完整定义**

- **地基**: `reversal_1m_deep × stage=1` (实测 +0.392/58.1%, §10 表口径) 为主, `mild×1.5` 等 3 个正 sharpe 组合进 formula_variant categorical search space。信号 t 收盘生成, t+1 开盘执行, 全部 KPI 用 T+1 open 含成本口径。
- **三层确认 (B §2, 全部待验证, gate 形式 {enhance/neutral/veto}, 每层 mode={off,veto_only,enhance_only,both} 进 search space)**:
  - L1 筹码 (cyq_perf, JOIN t-1): winner_rate / dist_wavg / chip_spread — **期望打对折** (评委1: 与 ret20 先验共线 + 项目"CYQ 入场 filter 无效"前科, 蓝图自己 5 股探针无单调性); 进 Optuna 的前置 = **正交性生死关** (对 ret20/rel_std/价格位置回归取残差后增量 RankIC 为零 = 死刑)。
  - L2 资金流 (moneyflow, JOIN t-1): elg_net_5d / elg_streak / sm_elg_div / pv_div — 生死系于 §0.3 判决实验。
  - L3 板块顺风: dc_member 探底结果决定主口径; **探底 <2 年则降级用申万 L2 行情动量做顺风代理** (index_member_all in_date/out_date 真 PIT, 2003 起), 资金流口径攒够再升级 (B-F6 预案)。
- **确认层选型机制 (D 的测量仪嫁接)**: B 的 L1/L2/L3 本质就是 D 的 F2/F1 族 — **共用一次回填、一套 ablation** (评委3 明确要求, 避免重复跑批)。流程: D-A0 基线复跑锚点 (v6 OOS RankIC 必须落回 0.0108-0.0203 带, 否则先修管线) → 逐族 A1/A2 ablation 测截面信息含量 → 只有测出增量的族才进 B 的 gate search space。
- **退出端 (失败残值标杆, 三评委一致最高评价)**: `exit.wr_takeprofit` (持仓 winner_rate 升破阈值止盈) + `exit.elg_outflow_exit_days` (连续特大单净流出退出) 作为**独立交付组件**单独 ablation — 即使入场确认层全军覆没, 退出组件仍交付给所有流派 (直接回应全息图盲点 2/4: 主力数据强项在退出端, 入场研究 16 版退出只有 1 版)。
- **仓位/风控**: 复用 paper_sim v2 (100 万 / max 5 仓 / wilson_kelly / min_cash 5%); 新增同板块 (申万 L2) 持仓 ≤2 只 (categorical {1,2,3} 进 space, 防反转信号板块退潮日成片出现 = 5 仓同坑); regime gate 见套二。
- **验收范式**: **期望值账单一级验收** — Δ胜率/Δsharpe/Δ信号量三列强制同表, 胜率升但账单变差 = FAIL (治胜率执念, 盲点 1); 预注册成败判据 (B §6.3) 写在跑批之前; 4 基准缺一不可 (地基本身 / random-entry+same-exit / HS300 / 等权)。

**为什么这样组合 (评委依据)**

1. B 两票第一, "站在全项目唯一实测 OOS 正收益的地基上, 执行端最贴合 T+1 个人投资者现实" (评委1); 反转买点是阴线/十字星日, 高开抢跑风险结构性低于突破族 — 三蓝图里唯一正面回应"涨停买不进"痛点的入场端。
2. D 的 ablation 矩阵被三评委一致定为"全项目特征族强制测量层"; 评委3: "B 的 L1/L2/L3 特征本质上就是 D 的 F1/F2 族, 两者应共用一次回填、一套 ablation"。
3. B 的致命伤已在本方案内修复: 地基引用择优 → §0.2 收敛到 §10 表; L1 开局死亡概率高 → 正交性生死关前置 + 期望对折; 信号量 vs 5 仓张力 → 期望值账单一级验收 + enhance_only 兜底。
4. 熊市样本无解 (评委2 fatal flaw: 验证窗仅 2023-01~2026-05, F3 急熊只能认栽) → 由套二的 Tier-A 16 年面板条件性补课 (§2 P2), 主书诚实接受"2018 式急熊未验证"标注直到那时。

**MVP 实验 (一周内, 排好先后)**

| 序 | 实验 | 依赖 | 产出/验收 |
|---|---|---|---|
| E0 (D1) | PROJECT_INDEX 双口径收敛 (上游修复) + 预注册 B §6.3 成败判据存档 | 0 | 引用口径唯一 + 判据先于数据存在 |
| E1 (D1-3) | **B-V0**: 在库 `raw_fund_flow_daily` (akshare, 86,426 行, 2025-08-21~2026-04-24, 评委实测核验) × 既有 `fact_technical_trigger` reversal 信号 (5.9M 行) 做 L2 条件分桶: 特大单净流入 5 日符号/分位 × forward 5/10/20d 胜率与均值, 分市值桶 | **0 新数据 0 API** | L2 方向性 go/no-go 表; akshare 口径仅方向参考, 永不入生产决策 |
| E2 (D2-5) | moneyflow 2022-01→今 回填 (~1,070 calls, writer 5 项 gate 先行) + D-A0 基线复跑锚点 | need_027 余 5 gate | fact_moneyflow_daily 落库 audit PASS + A0 落回 0.0108-0.0203 带 |
| E3 (D5-7) | 重叠期对账: raw_fund_flow_daily vs moneyflow (2025-08~2026-04 两源一致性) + B-V0 同口径 TuShare 版重跑 | E2 | 双源一致性报告 + V0.5 方向确认 |

---

### 套二 (公共增强层, 不占独立资金): 截面排序 v7 + regime gate + 16 年验证场 (D 主干)

**完整定义**

- **定位 (D 自己的诚实代数账)**: 不是独立策略 — 5 持仓 ρ=0.3 有效下注数 2.27, 30% 年化需有效 RankIC≈0.20, v6 的 0.015 差一个量级。它是三个乘法因子: ① 候选池排序 (同日 N 个信号选 5 的选择增益, `score_blend_w_v7` 混合权重, **w=0 一键回退 v6 保险丝**); ② 降权/排除 gate (散户接盘 / 全员获利 / 拥挤), 作用于 max_dd 与月胜率左尾; ③ **regime gate 数据化** — `lmt_market_temp` (炸板率/连板高度/晋级率, C-M5 已给真实量纲: 炸板率 median 0.241 range 0.115-0.66, 连板高度 median 5, 日涨停 28-1372) + `moneyflow_mkt_dc` 大盘净流入, 替换 "bear/sideways/bull 拍脑袋" 反例, 服务"等待>操作"。
- **特征族纪律 (全盘吸收 D)**: 按历史深度分 Tier (A: moneyflow/report_rc 16 年; B: cyq_perf/stk_factor_pro 8 年; C: limit_list_d/dc 短史 **只做 add-on 不单独立论**); 每族 5-8 列硬上限 + 族内去共线 + fold 间 std 一级稳定性指标; A0-A7 逐族/留一 ablation; **相对提升 ≥+50% 自动触发 pit-audit 5 步** (0.015→0.0225 即触发), 阴性结果归档算合格产出。
- **report_rc 卖方一致预期族**: D 里 alpha 期望最高的一支 (16 年长史 + 与现有 46 维量价正交); `report_date ≤ t-1 ∧ create_time ≤ t 盘前` 双锚防研报补录泄漏 (教科书设计, 推广为一切公告/研报类数据范式); 行量 unknown → 先探 3 个月再排期。
- **16 年 Tier-A 验证场 (条件性, 全项目公共品)**: daily+adj_factor 2010-2021 回填 (含退市股宇宙) 后, 任何流派 (含套一) 都能重放 2015/2018 熊市生存测试 (盲点 8 的公共补法)。**前置 gate**: W3 判决实验非全阴 或 用户显式拍板"熊市生存测试必须做" — 不满足则该回填不启动 (评委1: 投入产出比 unknown)。

**为什么这样组合 (评委依据)**

1. 评委2 给 D 全场最高 (8.5): "统计机械最硬 (A0 锚点/Tier 分层/留一法/红线自动化), 实证数字可复现性最高 (资金流前哨复现到小数点后四位)" — 这正是测量仪该有的品质。
2. 三评委一致的 D fatal flaw 是"不是独立策略 / 近期 P&L 拉动最弱 / 排序增益依赖候选过剩" — 所以**不给它独立资金、不让它单独排期**, 它的价值与套一的信号产能强耦合 (评委3: "单独排期无意义"), 全部产出以"套一的乘法因子 + 全项目公共件"形态交付。
3. 主打族先验偏空 (前哨 RankIC≈0) 被 D 自己用"测量而非假设"框架正确处理 — 综合方案保留这个框架: 测出零也是合格产出, 该族转 regime/卖侧用途。

**MVP 实验 (一周内)**

| 序 | 实验 | 依赖 | 产出 |
|---|---|---|---|
| E4 (D1-2) | limit_list_d 2020 起回填 (~1,460 calls, ~0.7M 行) + stk_limit/stock_st/suspend_d 三件套 + moneyflow_mkt_dc 全史 (1 行/日) | Phase 1 writer | 可执行性真相源 + 情绪温度计原料落库 |
| E5 (D3-5) | `lmt_market_temp` 三合一 z + 大盘净流入 20 日均: 用 M5 实测量纲建 regime gate v0 (阈值进 Optuna 不拍死), 对 2022+ 历史标注 go/half/stop 分布 | E4 | regime 标注序列 + 与既有 hs300_60d_ret 三档对照表 |
| E6 (D5-7) | D-A1 准备: F1.1-F1.4 四列特征进 panel (registry 注册 + null_policy) + ROI 预检 (coverage ≥80% / Spearman 自相关 / 方差非退化) | E2 | ROI gate 报告 (判决实验 W3 开跑的全部前置) |

---

### 套三 (卫星书, 条件激活, 当前 0 资金): 题材扩散 — 数据资产先行, 策略缓议

**完整定义**

- **当前形态 = 纯数据资产采集 (C 自己 §6.3 预设的降级路径, 三评委一致建议)**:
  1. **每日快照自养今天就开始**: ths_member / dc_concept_cons / dc_member 每日落盘 (launchd wrapper, 不走裸 cron), 攒一天少一天, 历史买不到 — 零成本不可逆资产, 与策略 go/no-go 完全解耦。
  2. **dc_member/dc_index/moneyflow_ind_dc/limit_cpt_list 历史探底** (二分查找最早可取日期) — 全策略木桶短板, 探底结果决定 C 线能否严肃回测。
  3. **三件保值资产**进全项目公共层: 概念成分 PIT (用户点名 5 方向的共同地基) / 涨停事实表 (formula_limit_up_pullback 真相源升级) / 情绪温度计 (已被套二消费)。
- **策略线复审条件 (W9, 三道门全过才立项)**: ① dc_member 探底 ≥2022-01 (≥4 年, 可严肃 walk-forward); ② 套一/套二的 moneyflow 实证非全阴 (个股排序特征有据); ③ 概念粒度 null 基线测试 (vs C-M3/M4 已实测的 L2 粒度 ≈ 0 基线) 出正向差值。任一不满足 → 继续养数据, 下季度再审。
- **若激活**: 按 C 蓝图原案执行 (D2 涨停传染主通道 + 新点火约束 + 第二梯队承接), 但补上评委要求的硬约束: 总资金 ≤20% 卫星预算; PIT 双口径敏感性测试为一级 NO-GO 门 (t-1 口径下 edge 消失 = 策略本质不成立); 人工执行摩擦单独建模 (评委1: 题材退潮日集体低开清仓的实际成交价 + 盘后高频事件流对人工决策者的注意力负担, 蓝图按全自动假设设计是缺口); 换手成本全套结算 (54.9x 换手教训)。

**为什么这样组合 (评委依据)**

1. 三评委一致第三且分差大 (5.0-6.5): "正面 alpha 证据为零 (M3 -0.97%/M4 -0.26% 全部为负或回到基线), 整个策略押注未测假设""存在永远无法严肃回测的真实路径, 对以交易为生的用户是不可接受的资金占用风险" (评委3) — **作为策略下注当前不合格, 作为数据投资风险有限** (NO-GO 残值清单三件全保值)。
2. 但 C 的侦察价值被三评委一致评为最高单点: M1 伪 rank 发现 (三评委独立复现 spearman 0.07/0.071/0.084) "单这一条就值回蓝图成本"; null 基线先行是"三份里唯一真跑了回测的"。这些以纪律件形态全额吸收 (§5)。
3. regime 互补真实存在 (题材牛最强 / 冰点归零, 与 B 震荡市最强/趋势牛最弱错开), 但只有在证据出现后才值得用真金白银表达。

**MVP 实验 (一周内, 全部零/低成本)**

| 序 | 实验 | 依赖 | 产出 |
|---|---|---|---|
| E7 (D1) | ths_member/dc_concept_cons/dc_member 每日快照 launchd 任务上线 (失败告警走 wrapper 链) | 0 | 首日落盘成功 + 告警链验证 |
| E8 (D1-2) | 四接口历史探底 (dc_member/dc_index/moneyflow_ind_dc/limit_cpt_list) | 0 (各 ~10 次试探调用) | 真实 data_start 四元组 → 直接决定 W9 复审走向 + 套一 L3 主口径 |
| E9 (D3-7) | M1 伪 rank 陷阱 + "截面 rank 必须自算" 纪律写入 PROJECT_INDEX 命名陷阱表; concept_blacklist.yaml 骨架 (仅按概念类型规则化定义, 禁止按事后表现挑选) | 0 | 全项目级数据陷阱免疫 (上游修复项, 主会话执行) |

---

## 2. 数据回填总计划 (三套合并去重)

### 2.1 接口清单 (合并后, 按周排期; 调用量为蓝图推算, 跑前以 writer 实测为准)

| 波次 | 接口 | 范围 | 调用量/行数 (推算) | 消费方 | 排期 |
|---|---|---|---|---|---|
| W1 前置 | need_027 余 5 项 required gate (pit_key/freshness_sla/writer/watermark/failure_queue) | — | — | 一切回填的闸门 | W1 (现有 pending 任务 #1) |
| P0-a | `moneyflow` | **先 2022-01→今** (MVP), 再扩 2018 | ~1,070 calls / +~990 calls; 2018+ 全程 ~10.7M 行 | 套一 L2 + 套二 F1 + 退出组件 (**一次回填三用**) | W1-2 |
| P0-b | `limit_list_d` + `stk_limit` + `stock_st` + `suspend_d` | 2020/2022 起 | ~1,460 calls + 小表 ×3, 合计 <9M 行 | 套二 regime + 全流派可执行性真相源 | W1-2 |
| P0-c | `moneyflow_mkt_dc` | 全史 (1 行/日) | ~800 calls, ~4k 行 | regime gate (ROI 最高, 120 分可试用) | W1 |
| P0-d | 探底四件 (`dc_member`/`dc_index`/`moneyflow_ind_dc`/`limit_cpt_list`) + 每日快照自养 | 探底 + 增量 | 探底 ~40 calls; 快照 ~25k 行/日 | 套三 + 套一 L3 | W1 起每日 |
| P0-e | `index_member_all` + `index_classify` + `sw_daily` | 全量 (2003 起) | 一次性小表 | 套一 L3 兜底 + 组合层同板块限制 (申万 L2 已实测最优区分度) | W2 |
| P1-a | `cyq_perf` | 2018→今 (按股循环) | ~5,400 calls, ~10-11M 行 | 套一 L1 + wr_takeprofit + 套二 F2 (**一次回填三用**) | W3-4 |
| P1-b | `stk_factor_pro` (裁剪 ~25 列) | 2018→今 | ~1,940 calls, ~10.5M 行 | 套二 F5 + ATR 止损 (修 vol-aware hardcode 反例) | W4-5 |
| P1-c | `moneyflow` 2018 扩展 | 2018-2021 | ~990 calls | L2/F1 全期复验 | W3 (判决实验需要) |
| P2-a (条件) | `daily` + `adj_factor` 2010-2021 (含退市股) + trade_cal 2010+ | 12 年 | 各 ~2,900 calls, 各 ~7M 行 | 16 年 Tier-A 验证场 (全项目公共品) | **gate: W3 判决非全阴 或 用户拍板熊市测试**; W7+ |
| P2-b (条件) | `report_rc` | 先探 3 个月行量 | unknown | 套二 F4 (alpha 期望最高支) | W5 探, 行量明确后排 |
| P2-c (条件) | `moneyflow_dc` | 2023-09→今 | ~700 calls, ~3.5M 行 | 三口径共识第二票 | 仅当 L2/F1 出增量 |
| 永不接 | `cyq_chips` | — | 数十亿行不可行 | cyq_perf 分位已是低维摘要 | — |

**Writer 纪律 (need_027 契约延续)**: 0 行返回 = 失败重试 (TuShare 间歇空响应实测); watermark + failure_queue; 单位归一 (moneyflow 万元 / ind_dc 元 / ths 亿元); 字段改名隔离 (`buy_elg_amount` 净额 vs 买入额双语义陷阱); winner_rate clamp [0,100]; 新表全部纳入 freshness SLA, 不走裸 cron。

**存储**: 全部波次合计 ~45-60M 行, DuckDB 压缩后 3-6 GB (D 蓝图推算) — 本地可承载, 存储不构成上 modal 的理由。

### 2.2 modal 跑批与 $30/月预算映射

| 任务 | backend | 规模锚点 | 预算映射 |
|---|---|---|---|
| 全部 MVP (E0-E9) + 首轮 ablation (A0/A1, ~40 folds) | **local** | reversal 34 月窗 walk-forward 实测 7.5h 本地 | $0 |
| 套一 V2 全量 ablation (4 公式 × 3 层 × 4 模式主对角) | local 优先, 切片过夜跑 | 推算 22-38h 本地等效 [**估算, 跑前必须 1 公式 × 1 层实测单窗**] | $0; 单 fold 实测 >30min 才评估升 modal |
| 16 年 Tier-A walk-forward (~156 folds × 2 配置) | **modal 唯一候选项** | unknown — 纪律: local 实测 1 fold wall time → `plan_validator.enforce_optuna_plan()` → 才定规模 | $30/月额度**只排这一项**; 映射公式 = 单 fold 实测时长 × 156 × 2 × modal 单价, 超额则裁剪到 2015/2018 两个关键 regime 窗口切片跑 |

modal 硬前置 (CLAUDE.md §9): reviewed adapter + artifact-manifest 契约 PASS 之前一律 blocked, 全部本地; 排期最早 W11。**禁止线性外推拍跑批时长** ("估算 2min 实跑 28min" 反例 + 2026-05-26 29/34 公式无 search space 白跑反例 → 跑前 plan_validator + grill gate 双查)。

---

## 3. 资金分配与相关性管理

### 3.1 资金分配 (100 万, 分两个时期)

| 时期 | 主书 (套一) | 卫星书 (套三) | 现金 | 说明 |
|---|---|---|---|---|
| **验证期 (W1-W12)** | 0 新增真金白银 | 0 | — | 新策略全部 paper_sim 候选态; 现行实盘维持既有纪律不动。以交易为生 = 验证期不拿生活费下注未验证假设 |
| **目标态 (W12 gate 过后)** | 70-80% | 0-20% (**条件激活**: 套三 W9 三道门全 GO + 自身含成本 OOS 过 gate) | ≥5% (min_cash 既有) | 套二不占资金 — 它是主书 selector 的乘法因子与全组合 regime 调仓器 |

硬线 (合并 NAV 层, 不可搜索): 总 max_dd -20% KPI 同口径监控; 累计 -25% 全清观望 5 日 (paper_sim 既有); 不加杠杆。

### 3.2 失败相关性矩阵 (比收益相关性更优先管理)

| 对 | 失败相关性 | 共因 | 管理动作 |
|---|---|---|---|
| 套一 L2 ↔ 套二 F1 | **高** (同 moneyflow) | 资金流族截面信息为零 | W3 判决实验一次裁决 (§0.3); 判负两者同步降级, 该域只保留退出端/对账用途 |
| 套一 L1 ↔ 套二 F2 | **高** (同 cyq_perf) | 筹码与价格位置共线 | 共用一次回填一套 ablation; 正交性生死关前置 (W4) |
| 套一地基 ↔ 套二 v6 | 中 (同 K 线量价域, 公式 vs ML) | regime / 数据断流 | regime gate 数据化 (E5) + launchd 告警链 (已根治路径) + freshness SLA |
| 套三 ↔ 套一 | 低-中 | **题材退潮日**: 反转信号成片出现 ∧ 卫星书清仓同日 | 同板块 (申万 L2) ≤2 仓跨书统一额度; 同题材互斥 (主书持仓所在题材, 卫星书不开新仓); 2026-04 崩盘段压力切片必测 |
| 全组合 | — | 数据断流 (cron 静默失败 2 次前科) | 全部新表走 wrapper + ALERT flag 启动检查 (已根治, 防回退) |

### 3.3 收益相关性 (regime 互补设计)

- 套一: 震荡市最强 (回调-修复循环密集), 强趋势牛跑输 (F4), 急熊接刀 (F3, regime gate + 硬止损兜底, 2018 式未验证须诚实标注)。
- 套三 (若激活): 题材牛最强, 冰点归零 (事件数趋零 = 天然 gate, 双 book 下可接受)。
- 套二 regime gate 是两书共用的仓位 scaling 器: 弱市少入场是 max_dd KPI 的第一防线 ("等待>操作")。
- 月胜率 ≥55% KPI 在**合并 NAV 月度分布**上验收, 不在单书上 — 两书 regime 收益曲线先验错开本身就是月胜率平滑器。

---

## 4. 12 周路线图 (周粒度, 对齐 implementation_plan Phase 2-4)

> Phase 对齐: W1-2 = Phase 1 收尾 (TuShare 接入 5 gates); W2-6 = Phase 2 (Alpha 研究: 资金流族→筹码族→板块/概念, ROI gate + ablation); W3-8 = Phase 3 (回测收敛, 与 2 并行: 三基准内建 + paper_sim 超参进 Optuna); W6-12 = Phase 4 (v7 逐族 ablation + ensemble 权重 + regime gate 数据驱动 + 胜率专项月度分布验收); W12 = Phase 5 入口评审。
> 每周里程碑均为可验证 artifact (表/报告/gate 状态), 不是"代码写完"。

| 周 | 主线动作 | 可验证里程碑 |
|---|---|---|
| **W1** | E0 双口径收敛 + 预注册判据存档; need_027 余 5 gates; E1 B-V0 分桶 (0 新数据); E7 快照自养上线; E8 四接口探底; E4 limit_list_d 等回填启动 | ① V0 方向表 (L2 条件分桶 × 分市值桶) ② 探底四元组报告 ③ 5 gates PASS ④ 快照首日落盘 + 告警链验证 |
| **W2** | moneyflow 2022+ 落库 + audit; D-A0 基线复跑; E3 双源对账; E5 regime gate v0; index_member_all 申万 L2 PIT 落库 | ① fact_moneyflow_daily audit PASS ② **A0 锚点落回 0.0108-0.0203 带** (否则先修管线, 后续顺延) ③ 双源一致性报告 ④ regime 标注序列 2022+ |
| **W3** | **判决实验** (§0.3): moneyflow 扩 2018 + D-A1 ablation (v6+资金流, 2023-01→2026-05 ~40 folds, local) + B-L2 全期条件分桶 (2018+) | **moneyflow 截面信息含量判决表** (ΔRankIC + fold std + B-L2 Δ期望值账单三列) — B-L2/D-F1 共同生死门; 阴性 = 两者降级并归档 (合格产出) |
| **W4** | cyq_perf 回填; B-V1 **正交性生死关** (L1 残差增量 RankIC, 分市值桶); D-F2 ablation 同批 | ① cyq 表 audit PASS (winner_rate clamp 验证) ② L1 生死判决书 (残差零增量 = 死刑不进 Optuna) |
| **W5** | L3 口径决定 (dc_member 探底结果 → dc 主口径 或 申万 L2 动量代理); stk_factor_pro 裁剪回填; report_rc 3 个月行量探测 | ① L3 主口径决定记录 (含降级理由) ② F5 族 ROI 预检报告 ③ report_rc 行量 → P2-b 排期决定 |
| **W6** | B-V2 全量 ablation 开跑 (4 公式 × 存活层 × 4 模式, expanding_monthly ~35 窗, local 切片过夜; 跑前 plan_validator + grill); 退出组件独立 ablation 同批 | ① 跑批 plan 存档 (search space 非空验证) ② ablation 中期表 (先出公式 × veto_only 对角) |
| **W7** | B-V2 完成 + 预注册判据结算; 退出组件 (wr_takeprofit/elg_outflow_exit) 判决; (条件) Tier-A 回填启动 | ① **每层 OOS uplift 表 (Δ胜率/Δsharpe/Δ信号量同表)** vs §6.3 预注册判据逐条结算 ② 退出组件交付/否决书 (独立残值) |
| **W8** | **B-V3 paper_sim 含成本组合口径**: 最优配置 vs 地基, T+1 open / 涨停不可买 / 5 仓重叠 NAV, 对 4 基准 (含 random-entry+same-exit), 写入 fact_sim_run | **第一张含成本组合年化/max_dd/月胜率分布/超额 HS300 表** — 第一个能回答 "30% 行不行" 的数字 (会比 per-trade 难看, 但它是真的) |
| **W9** | v7 候选拼装 (A6 全族 + A7 留一法) + score_blend_w_v7 选层; **套三复审** (三道门: dc_member 探底 / moneyflow 实证 / 概念粒度 null 基线) | ① v7 candidate + 保险丝验证 (w=0 回退 v6 等价性) ② 套三立项/缓议决议书 |
| **W10** | 组合层联调: 主书 × v7 排序 × regime gate 合并 paper_sim; sector_budget/同板块限制进 Optuna; 集中度压力切片 (2026-04 崩盘段 + 题材退潮日) | ① 合并 NAV + 压力切片报告 ② 相关性矩阵实测版 (替换 §3.2 先验) |
| **W11** | (条件) modal adapter gate 评审 → Tier-A 16 年 walk-forward 排期/裁剪; 胜率专项: OOS 月度胜率分布验收 (Phase 4 既定) | ① Tier-A go/no-go + 预算映射 (单 fold 实测 × 156 × 2 vs $30) ② 月度胜率 ≥55% 占比报告 (KPI 同构口径) |
| **W12** | KPI gate 结算: 北极星 4 指标 vs 4 基准, 全部含成本 OOS 口径; Phase 5 入口评审 (实盘资金分配决议, §3.1 目标态激活与否) | **GO/NO-GO 决议 + 资金分配决议书** (含: 哪些层进生产 / 哪些归档为反例 / 下季度数据投资清单) |

依赖链上的诚实声明: W3 判决实验阴性不阻塞主线 (地基版 B + 退出组件 + regime gate 照常推进到 W8 出数); W2 A0 锚点失败会顺延全部 ablation (先修管线是纪律, 不是可选项); 探底结果最早 W1 末才知道, L3/套三的全部排期受其支配。

---

## 5. 全局强制纪律件 (从三蓝图吸收的公共范式, 全流派适用)

| # | 纪律件 | 来源 | 适用 |
|---|---|---|---|
| 1 | **截面 rank 类字段必须自算全量** (moneyflow_ind_dc.rank 是每 50 行循环的分页伪 rank, 三评委独立复现 spearman 0.07-0.084) → 进 PROJECT_INDEX 命名陷阱表 | C-M1 | 一切 vendor rank 字段 |
| 2 | **PIT 双口径敏感性测试** (盘后表 t 行 vs t-1 行各跑一遍, 量化"当晚可得性"价值; t-1 口径下 edge 消失 = 策略本质不成立 NO-GO) → 写入 strategy_validation_contract | C | 一切盘后数据策略 |
| 3 | **A0 锚点复跑** (任何 ablation 前先复跑基线, 必须落回历史带, 否则先修管线 — 校准测量仪再测量) | D | 一切 ablation |
| 4 | **相对提升 ≥+50% 自动触发 pit-audit 5 步** (机制化, 不靠人记) | D | 一切特征/模型升级 |
| 5 | **正交性生死关** (新特征族对既有先验回归取残差, 残差零增量 = 不进 Optuna) | B | 一切新特征族 |
| 6 | **期望值账单一级验收** (Δ胜率/Δsharpe/Δ信号量三列同表, 胜率升账单差 = FAIL) | B | 一切策略改动 |
| 7 | **公告/研报类双锚** (业务日期 ≤ t-1 ∧ create_time/入库时间 ≤ t 盘前) | D | report_rc 及一切公告类 |
| 8 | **回退保险丝** (新模型混合权重含 w=0 = 一键回退旧版) | D | 一切模型上线 |
| 9 | **概念黑名单只按类型规则化定义, 禁止按事后涨跌挑选** (防软泄漏) + 维表版本化 | C | 一切人工维表 |
| 10 | **null 基线先行 + random-entry-same-exit 第四基准** | C + 全息图盲点 3 | 一切入场逻辑 |
| 11 | **Tier 分层: 短史族只做 add-on 不单独立论** | D | 一切短史数据 (limit_list_d/dc/热榜) |
| 12 | **预注册成败判据写在跑批之前** (防事后挪门柱) | B/C | 一切跑批 |
| 13 | **快照类数据今天就开始攒** (攒一天少一天, 与策略成败解耦) | C | dc_member/ths_member 等 |

---

## 6. 诚实声明 (unknown / 推算清单)

| 项 | 状态 |
|---|---|
| 组合整体年化/max_dd/月胜率 | **unknown 直到 W8 含成本 paper_sim** — 本文不预报任何 KPI 数值, 报了就是 estimate not measured |
| 确认层 uplift (胜率 +3~6pp 等) | 设计目标, 零实测证据; L1 期望已按评委意见打对折 |
| moneyflow 截面信息含量 | unknown, 前哨 (20 日窗) ≈0 且偏负; W3 判决 |
| dc_member 历史下限 | unknown, W1 探底; 决定套三能否立项与套一 L3 主口径 |
| report_rc 行量 / create_time 防御强度 | unknown, W5 探测 / 入库后尾部分布抽查 |
| B-V2 ablation 22-38h / Tier-A modal 预算 | 推算, 跑前必须小样本实测单窗 (项目反例纪律) |
| 2018 式急熊生存 | 主书验证窗 2023-01~2026-05 不含, 诚实标注; 补课依赖 P2-a 条件回填 |
| 三蓝图小样本数字 (M1-M5 / 前哨 / 5 股探针) | 全部"初步", 仅证明管道可行与方向排除, 不构成 alpha 证明 |
| blueprint_A (推测主升浪/突破族) | 未产出未评分; 若后续出现, 其与套一的 regime 互补性 (B 自评趋势牛最弱) 需补评, W9 是自然插入点 |

— 完 —

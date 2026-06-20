# D-step-2: 入场信号含成本回测 — reversal/vol 买点不可交易 (2026-06-20)

> owner: 本文件 (D 因子矩阵 step2, C-R1 真金白银裁决)。承接 D-step-1 (rally_factor_discrimination_d1_20260620.md)。
> 引擎成本: ExecConfig (buy 0.11% / sell 0.16% 含印花)。候选: entry_pit(正9070)+entry_negative(负35198) 长底 pivot。

## 裁决: reversal/vol 买点信号 = 不可交易 (AUC 是 pivot look-ahead, 非 alpha)

| 入场时点 | top20%(高rev+vol) 净@120d | 全部 净@120d | 全部胜率@20d |
|---|---|---|---|
| 天真 (pivot 当日 T+1) | 中位 +5.8% | +1.4% | **90%** |
| **现实 (pivot+21 确认后)** | 中位 **-4.2%** / 均 +0.1% | **-5.9%** | 40% |

## 根因: pivot-low 的 ±LOWWIN 前瞻 (异常漂亮=警报抓出)
- 候选是 **pivot-low** (局部最低), 定义 `lows[i]==min(lows[i-20:i+20])` **用 i+20 未来窗**。"这是底" 只有
  事后 20 日才确认。在 pivot 当日 T+1 买 = 假设已知是底 = **前瞻泄漏**; 底后必涨 → +90% 胜率是 artefact。
- **可实盘入场** = 等 pivot 确认 (20 日低点守住, 实时可观测) → pivot+21 买。此时初始反弹 (B: 底后5日+5.3%)
  已错过, forward 净收益 **全线负** (-2%~-6% 含成本), 80% 候选是没涨的负样本主导亏损。
- reversal/vol top20% (现实入场 +0.1% 均@120d) **救不回正期望**: AUC 0.64 判别力来自 pivot 当日 (含look-ahead),
  到可实盘点 (pivot+21) 信号衰减, 不能选出赢家。

## C-R1 实证 (necessary-not-sufficient)
D-step-1 的 reversal/vol AUC 0.64 + OOS 稳定 **都是真的**, 但**判别的是 pivot look-ahead 确认的底**, 不是
可交易信号。**AUC ≠ 赚钱** 再次坐实 (同 Stage1.5 reversal IC+0.195/含成本-34.6% 同族)。异常漂亮 (90%胜率)
=查泄漏纪律, 救于建在假 edge 上。

## 重定向 (D 下一步)
- **买点 reversal/vol 死** (与用户 "买点 AUC≈0.52≈噪音, secondary" 一致)。不在简单量价买点上继续烧算力。
- **转用户优先: 出场 > 延续 > 买点**:
  1. **出场 (鱼尾)**: D-step-1 顶部 signature = 高mom+高vol+极负reversal (climax)。测: 在 fact_rally_stage 顶部段
     这些信号能否提前出场, 改善持有期收益 (vs 固定持有/移动止损)。**出场是 episode 内部择时, 无 pivot look-ahead 问题**。
  2. **延续 (鱼身)**: 主升段持仓信号 (动量延续 vs 转弱)。
  3. **入场若续做**: 需用户想要的 **量价异常放大 (换手/成交量 vs 历史基线) + 板块概念热度 + 筹码** 因子
     (当前 5 panel 因子无这些); 且入场必须现实时点 (无 pivot 前瞻) 重做候选universe。
- **方法论校正**: 后续任何 episode 级回测, 入场时点必须是**实时可确认点**, 不许用 pivot/peak 等 ±窗定义的事后点
  当入场 (本次教训沉淀)。

## D-step-2 续: 量价+资金 领先因子全盘筛 (2026-06-20, 用户纠偏"用领先因子尽早确认拐点")
用户 push back: "买点死" 是 defeatist, 目标是用领先因子早确认拐点 (否则全市场量化都不用做)。据此全盘测领先量价+资金
(daily_basic volume_ratio/turnover 回补2019 + moneyflow 主力/大单), 判别真底(entry_pit) vs 假底(entry_negative):

| 领先因子 (底部/底后窗) | AUC | 读 |
|---|---|---|
| volume_ratio 量比(vs短期) | 0.491 | 噪音 |
| turnover_rate 换手 | 0.587 | 中等 |
| turnover_rate_f 自由流通换手 | 0.609 | 中等(最强领先量价) |
| 换手放大 (底前5日 vs 长底基线) | 0.489 | 噪音 (底部仍缩量) |
| 启动放大 (底后5日 vs 基线) | 0.476 | 噪音; **高放大续涨更差**(派发非吸筹) |
| 主力净流入占比 (底当日) | 0.574 | 中等 |
| 大单+特大单净流入 (底后5日) | 0.531 | 噪音; 真假底大单都净流出(散户接盘) |

**第一性原理结论 (重要, 非 defeatist)**: 单股**量价+资金在拐点上, 真底假底几乎一样** (全 AUC 0.46-0.61)。
边际信息**不在匿名单股量价/资金里** (符合现实: 易抓底则全市场量化发财)。**alpha 大概率在"相对/横截面"信号**:
板块概念热度 (个股随板块/题材轮动启动) + 市场 regime (牛市背景) + 筹码集中度 (cyq); 主升浪需 tailwind = 横截面非单股。
**下一步 D-step-3 = 横截面/相对因子** (板块相对强度 + regime + cyq), 多因子 model (非单因子 AUC) + 现实入场点回测。

## D-step-3: 多因子 GBDT model = BREAKTHROUGH (2026-06-20, 用户选"多因子 model 组合")
板块动量也弱 (sret20 AUC 0.417, 真底反在更弱板块)。单因子全测尽 (量价/资金/板块), 天花板 ~0.6。
**但 HistGradientBoosting 多因子组合 walk-forward (train<=2022 n25262 / test>=2023 n19006):**
**OOS AUC = 0.738** (train 0.755, 差0.017 不过拟合), **大幅超单因子 (turnover_f 0.61 / reversal 0.64)**。
12 特征: base_days/reversal/vol/mom/mf_trend/roe/turnover/turnover_f/volume_ratio/pb/total_mv/net_ratio。
**结论翻案**: 单因子"买点死"是 defeatist 错判 (用户两次纠偏对); GBDT 抓交互/非线性把弱信号组合成真判别力。
合法性: walk-forward OOS (非in-sample) + 特征全PIT(<=bottom) + 按年切分无时间泄漏 + 0.738<0.9告警线。
**仍 C-R1 必要非充分** (D-step-2: AUC0.64含成本亏); 但0.738远强, model-top分位可能真盈利。
**→ D-step-4 = model 的含成本现实入场回测** (C-R1 裁决) + 特征重要性 + 加板块/regime/cyq (可能再升) + walk-forward多折 + Optuna/Modal 调超参。

## D-step-4a: GBDT model 含成本现实入场裁决 = edge 真实但远不及 KPI (2026-06-20)
> 引擎: portfolio_execbacktest (T+1 open / 非对称成本 0.11%买 0.16%卖 / 一字板剔篮 / 容量 / 停牌冻结)。
> realizable 入场: pivot 需 ±LOWWIN(20) 未来窗确认 → 合法只能等确认(bottom+21)入场。OOS AUC 复现 0.730。
> sandbox=sandbox/d4_entry_backtest (probe.py per-trade + portfolio_sim.py 组合); 裁决 experiment_store run d4a_*。

**per-trade (realizable 晚入场 bottom+21) @120d 含成本净收益**:
| 桶 (按 score) | 中位净 | 胜率 | |
|---|---|---|---|
| model_top10% | **+5.92%** | 62.4% | 含成本净正 |
| 全体 | −5.62% | 38.1% | 宇宙净负 |
| model_bottom50% | −7.98% | 31.2% | 单调 ↓ |
- A 上界 (bottom+1 look-ahead, 仅参考非可交易): top10% +16.22% @120d = 判别力天花板 (timing 免费)。
- **晚入场 vs 上界缺口 (+5.9% vs +16%)** = realizable 让出初始反弹 (B: 底后5日+5.3%), 早入场是最大 return 杠杆。

**组合级 (忠实 fixed-120d-hold, 等权持有全部 active top-decile)**:
| 策略 | 年化 | max_dd | sharpe | |
|---|---|---|---|---|
| model_decile | **+6.4%** | −28.5% | 0.43 | beats random **+6.6pp** |
| random_decile | −0.2% | −39.2% | 0.08 | 同宇宙随机基线 |
| model_decile + regime(趋势) | −2.3% | −29.8% | −0.08 | **regime 杀收益** |

**裁决 (C-R1, 真金白银诚实)**:
1. **模型 edge 真实**: 忠实持有下 model 选股比随机 +6.6pp 年化, per-trade 单调超全体净负宇宙。判别力(AUC0.73) → realizable 含成本组合价值, 非 IC 幻觉 (区别于 Phase B 33σ 仍 gross 负)。
2. **但 necessary-not-sufficient**: 6.4%≪KPI 30%; −28.5%≪KPI −20%。AUC 0.73 不足以达 KPI。
3. **churn 磨没 edge (新反例)**: top_k≪active 的"持最高分N名+周期重排"版 model≈random(+1.6pp), 因重排把未持满120d的赢家提前卖。**事件信号必须忠实 fixed-hold (各持满 horizon), 不能套'持top-k+重排'组合引擎** —— 后者把 per-trade edge 磨没。
4. **趋势 regime gate 证伪 (新反例, 免走弯路)**: `指数>MA60` 只在市场已涨"ON"(占13%交易日), 而主升浪入场在**底部**(regime OFF) → 趋势过滤杀掉所有抄底入场, 年化6.4%→−2.3% 且 dd 不改善。**抄底策略 ⟂ 趋势跟随 regime**; dd 控制需 contrarian-超卖 regime / 止损 / vol-target 仓位, 非趋势 gate。
5. **−28.5% dd 主要是市场 beta** (random 也 −39%, 主升浪候选偏小盘, 2023-24 A股熊): 选股解不了, 须仓位/风控层。

**→ D-step-4b 杠杆 (按 return/dd 缺口排序, 用户优先早入场)**:
(1) **早入场** (用户"尽早确认拐点"): 重建 PIT trailing-low 候选 (无未来窗, 不用 pivot ±窗) + 重训 → 早触发捕反弹 (B+5.9%→逼近 A+16%)。最大 return 杠杆。
(2) **dd 控制 (非趋势)**: 止损 / vol-target 仓位 / contrarian 超卖 regime。
(3) **加特征**: 板块热度 + cyq 筹码 (当前12特征无)。
(4) **鱼尾出场**: 固定120d → 主升浪见顶择时出场。

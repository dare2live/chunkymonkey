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

# 主升浪猎手 ground truth 重建 + 重验计划 (草案)

> 状态: 草案 (2026-06-13) — 执行前必须过 grill gate (chunkymonkey-governance)。
> 背景: 05-28 研究的 16 版原型 + ground truth CSV 全在 /tmp 已灭失; 70/78% 数字 =
> 假设, 须按 strategy_validation_contract 验证边界重验 (Order 0)。用户 06-13 指令:
> 尽快开始验证工作, modal 该用就用。

## 第一性原理

真相源 = market.price_kline_tdxhub (537 万行, 至 2026-06-12) + dim_trading_calendar。
ground truth 定义全量在档 (研究日志 §7), 重建 = 纯 K 线计算, **可复现可审计**:
- 突破事件: close > 前 60 日 high
- 主升浪: 突破后 60-180 日涨幅 >= 50% AND 期间 max_dd > -20%
- 标签: TRUE_RALLY / FAKE_BREAKOUT / NEUTRAL
- 一字天梯剔除: >=5 连板 OR >=3 一字板 OR >=10 涨停日 (散户接不到)

## 与原研究的三个升级 (修旧研究的已知局限, 研究日志 §1 自列)

1. **时间窗**: 原 2022-2026 仅 5 年 → 升级 2019(或 2005)-2026 (K 线已全量, 含 2019-2021
   结构性行情; 2015/2018 需先核 K 线起点)。样本量直接翻倍+, 缓解 §1.A 样本局限。
2. **复权口径**: 用 adj_factor 后复权比值 (LHB 实验同款), 原研究复权口径未声明 — 重建时显式。
3. **产物落库不落 /tmp**: ground truth 表进 smartmoney (fact_rally_ground_truth),
   manifest + PROJECT_INDEX 注册, 防再次灭失。

## 分阶段 (每阶段独立验收, 不跳)

| 阶段 | 内容 | 算力 | 验收 |
|---|---|---|---|
| S1 扫描 | ground truth 重建 (突破事件 + 标签 + 形态分档) | 本地 DuckDB 窗口函数, 分钟级 | 与研究日志 §7 数字对账 (2022-2026 段: 31,577 events / 3,012 TRUE / base rate 9.5% ±容差) — **对上 = 复现成立, 对不上 = 先查口径再前进** |

**S1 验收结果 (2026-06-13, `backend/scripts/rally_ground_truth_scan.py` + `analysis/rally_gt_reproduction_20260528.json`)**:
原文口径有两处未记录的隐含约束, 三角法定位成功 — (1) 事件 = **穿越** (昨日 ≤ 前 60 日 high,
今日 >) 而非状态 (状态口径 114,488 = 3.6x 锚); (2) **同股 60 日冷却** (cooldown 扫描
0/10/20/30/60/120 → 74,595/51,528/43,441/38,505/**31,551**/23,602, 60 唯一吻合锚 31,577,
99.92%, 且结构自洽 = 回看窗)。TRUE 读法 B (峰位 <=180 日, gain>=50%, dd>-20%):
**3,247 vs 锚 3,012 (+7.8%)**, base rate 10.3% vs 9.5%, 中位涨幅 73.9% vs 75.5%。
中位持续 66 vs 90 = 时长统计口径差异 (疑原文按涨势结束日非峰日), 不影响成员判定。
**复现成立, 重建口径已显式化锁入脚本常量 (比原文更可审计)。** FAKE/NEUTRAL 边界原文
未记录, S3 训练标签立法时再定 (不伪装复现)。
| S2 特征 | V14 级特征面板 (量价 + 板块共振 + CYQ + regime) | 本地为主; CYQ 全市场复算可上 modal (cyq_replay_all 已 deploy) | 特征 PIT audit (盘后字段 t-1 JOIN) |
| S3 模型 | LightGBM walk-forward (purge+embargo, 5 fold) 重验 70/78% 假设 | **modal** (并行 fold, $30/月额度内) | 预注册判据先冻结 (判官数值线按成本锚立法, 不从旧数字反推) |
| S4 判决 | 三判官 + 处置 | 本地 | 入 ledger; 判正才谈接入 live |

## 预算

S1 本地 ~分钟; S2 CYQ modal ~$1-3 (5200 股全市场, batch 100 已测分钱级/股池);
S3 modal LightGBM ~$2-5。总注意力 ~1-2 天。

## 待 grill 的开放问题

- 时间窗起点: K 线 2019 前覆盖与质量? (跑 S1 前实测 min(date) + 早期行数)
- S3 判据数值线: 胜率锚还是收益锚? 与 KPI (年化>=30%) 怎么穿透? — 立法时定
- 原研究 "K 线层天花板 60%" 是否直接接受 → S3 只跑 ML 层, 还是 K 线层也复扫?

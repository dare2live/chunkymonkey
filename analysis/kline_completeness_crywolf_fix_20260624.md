# data_audit cry-wolf 三检查 triage — 2 真修复 + 1 真 gap 升级 (2026-06-24)

> 触发: daily 真跑验证暴露 3 个 data_audit FAIL, 前会话 PROJECT_INDEX 记"口径artifact非真缺口
> (K线中位1804/1810天=正常停牌, 209码=退市股历史=生存者正确)", flag"阶段二§8门要扣停牌"。
> 本次逐个用**真实数据**核证 (measured-not-estimated): 2 个确是 cry-wolf 已修, **第 3 个是被误判的真 survivorship gap, 升级用户**。
> owner = 蓝图 §8 (每阶段验收门) data_module_architecture_20260624.md。

## 背景: data_audit 是 M2(clean)阶段的验收门
`run_post_sync_audit` (services/data_audit.py) 被 `pipeline/clean.py` Step 3c 调 = clean 阶段跑完的校验环。7 检查; 3 个曾 FAIL。

## 检查 1: kline_completeness — 真 cry-wolf, 已修 ✓

**根因 (实测)**: 旧口径 `_check_kline_completeness` 拿 clean(v_price_kline_qfq) 每股的 distinct date 数, 比"该股 [min,max] 间**全交易日历**交易日数" (`dim_trading_calendar`)。隐含假设"每股每交易日都有数据" — 但停牌/退市股某些交易日无 K线是**合法**的。threshold=0.0 → 任何缺口即 FAIL。
- 实测: **1711/5431 股 (31.5%) FAIL** (样本 300011 缺 5天/1810, 300013 缺 8天)。
- 扣 suspend_d 'S' 天后仅降到 954 FAIL (44%) — 因 `raw_tushare_suspend_d` 只覆盖 2022-01-04+ (15561行), K线回到 2019, 2022 前停牌 suspend_d 没有 → 朴素"扣停牌"是 partial fix。

**第一性原理正解 (clean-vs-source)**: M2(clean)的职责是**无损变换** raw_tushare_daily → qfq。门该验的是"clean 丢了 source 有的行吗", 与停牌/退市/日历**无关** (源本就没有停牌日 = 合法非缺口)。
- 实测决定性证据: clean(v_price_kline_qfq) 与 source(raw_tushare_daily) 逐 (code,date) 集合差 = **0 丢失行 / 0 涉及股** (双向); 7/7 失败样本股 clean dates == source dates。
- 修复: 口径改 clean-vs-source (split_part 去 ts_code 后缀对齐 code; date 去横线对齐 YYYYMMDD; 只比 clean 宇宙内的股)。新口径实测 **PASS** (旧 1711 FAIL)。
- "拉全 tushare" 是 M1(acquire) 的职责, 由 sync watermark/drain 守 (另一阶段门), 不在此 calendar 比对。

## 检查 2: kline_consistency — 真 cry-wolf (gap 部分), 已修 ✓

**根因**: `_check_kline_consistency` 做两件事 — (a) 重复 (code,date) 检测 [真bug, 保留]; (b) 连续 clean 行的交易日 gap > gap_max_days(5) 即 FAIL [cry-wolf: 停牌>5交易日的合法 gap 被误报, 实测 000004 +10/+36天=停牌]。
**修复**: 移除 gap-vs-calendar 判定 (clean 丢 source 行已由 check 1 的 clean-vs-source 守, 不重复 calendar 比对); 保留 (a) 重复检测 + (b) **新增** clean 行落在非交易日检测 (clean 不该有非交易日行 = 真不一致)。实测修后 **PASS**。

## 检查 3: cross_table_consistency "209 codes not in universe" — 不是 cry-wolf, 是真 GAP, 升级用户 [RED-LINE]

**前会话判定**: "209码=退市股历史=生存者正确" → 当 benign cry-wolf。
**本次实测推翻**:
- 209 = kline 有、但 `dim_active_a_stock`(5208) 和 `dim_all_ever_listed`(5210) **都没有**的 A股码 (00:93/60:77/30:37/68:2, **非北交所**)。
- 209 全部**不在** `dim_listing_status` (0/209)。
- 它们有真实 kline 史, max(date) 散布 2019-2026 (000018 停 2020-01, 000043 停 2019-12 = 退市; 000040 停 2025-03)。
- = **209 个真实退市/已停 A股, universe 真相源 (dim_all_ever_listed + dim_listing_status) 缺了它们**。

**为何是红线不是 cry-wolf**: kline 含这些退市股 = survivorship-**正确** (好); 但 **universe 表缺它们**。任何回测/选股若用 `dim_all_ever_listed` 当"含退市的全宇宙" → 漏掉这 209 退市股 = **survivorship bias = leakage 红线** (CLAUDE §4.1: 宇宙必含已退市)。cross_table 门**正确报了一个真 gap**, 前会话"生存者正确"判反了 (混淆了"kline 含退市"[对] 与"universe 表完整"[缺])。

**为何升级不自主修**: (1) survivorship/真金白银红线; (2) 修需补 universe 真相源 (dim_all_ever_listed + dim_listing_status), 触 25+ 消费方 (§9 审计); (3) 须先 root-cause universe builder 为何漏这 209 (否则补了还会再漏)。= mio 法典"红线 + 高 blast 真相源改 → escalate/焦点执行非自主 relax"。

**保留 cross_table 门 FAIL 状态** (不 relax) — 它在诚实报红线, 直到 universe 补全。

## 本次改动 + 验证
| 文件 | 改动 |
|---|---|
| services/data_audit.py | `_check_kline_completeness` 改 clean-vs-source; `_check_kline_consistency` 移 calendar-gap 保留 dup+非交易日; `_open_conn` attach tushare_raw |
| config/data_audit_rules.yaml | kline_checks 加 source_raw_table/code/date (clean-vs-source 口径配置) |
| tests/test_data_audit_kline_completeness.py | 新单测 (clean无损→PASS / clean丢行→FAIL / 源含clean外股→ignore) 3 绿 |

验证: 单测 3 绿; 真实 data_audit kline_completeness PASS (旧 1711 FAIL) + kline_consistency PASS; cross_table_consistency **仍 FAIL (正确, 真 survivorship gap 待补 universe)**。

## NEXT (升级用户决策)
**universe 真相源补全 (survivorship gap)**: root-cause dim_all_ever_listed / dim_listing_status 为何漏 209 真实退市 A股 (查 build_dim_listing_status + 上游 tushare stock_basic list_status 过滤) → 补全 (含退市) → 重建路径防再漏。属红线 + 高 blast 真相源改, 焦点执行。

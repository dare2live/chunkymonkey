# data_audit cry-wolf 三检查 triage — 2 真修复 + 1 真 gap 升级 (2026-06-24)

> 生命周期：历史证据（evidence-only）。本文不拥有当前 K 线契约或 gate；现行数据真相和验收边界见 `docs/MASTER_TOPLEVEL_DESIGN.md` 与 `docs/engineering_governance.md`。

> 触发: daily 真跑验证暴露 3 个 data_audit FAIL, 前会话 PROJECT_INDEX 记"口径artifact非真缺口
> (K线中位1804/1810天=正常停牌, 209码=退市股历史=生存者正确)", flag"阶段二§8门要扣停牌"。
> 本次逐个用**真实数据**核证 (measured-not-estimated): 2 个确是 cry-wolf 已修, **第 3 个是被误判的真 survivorship gap, 升级用户**。
> 当时 owner 已退役；本文只保留 clean-vs-source 口径形成时的实测证据。

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

## 检查 3: cross_table_consistency "209 codes not in universe" — 第3个 cry-wolf, 已修 ✓ (我曾误判为 survivorship gap, 用户纠正)

**演进**: 前会话判"209=生存者正确 benign"; 我一度反向误判为"universe 真相源缺退市股=survivorship 红线 gap, 要补 dim 表"; **用户纠正 → 回 universe.py 真相源核证, 确认我过度纠结了**:
- `services/universe.py` 文档第一句: **"奥卡姆剃刀: 不需要 dim_all_ever_listed / 快照比对"**。universe = **K线近90天有交易 + 板块前缀白名单(60/00/30/68) + 非ST**, 三条排除规则硬控 (`assert_universe_clean`)。
- `get_active_universe`: 基 = K线(真相源), dim_active_a_stock 只用作身份交集**剔指数 benchmark**(非 survivorship 白名单), dim_all_ever_listed **完全不用**。
- 退市股: 数据**本就在 K线史里**(survivorship-正确); 由规则3(K线近90天无交易)PIT 排除当前 universe。**不靠也不需要 dim 表枚举退市股** → 不存在"漏退市=survivorship bias", 因为没人拿 dim_all_ever_listed 当宇宙。
- 实测: 209 extras + 全部 kline 码都是合法 A股板块 (00/60/30/68, `classify_exclusion` 全返 None, 0 个非法)。

**为何是 cry-wolf**: cross_table 旧口径"kline ⊆ dim_active∪dim_all_ever_listed"= 要求 dim 表完整枚举 kline, **违第一性原理(K线=真相源)+ 重造了第二套 universe 判定**(项目身份真相源是 universe.classify_exclusion 的前缀规则)。退市A股(合法板块+真实K线, 但不在 current stock_basic)被误报。

**修复 (单一真相源 DRY)**: cross_table kline_universe_coverage re-point 到 `services.universe.classify_exclusion` — kline 码合法性 = 板块前缀身份(非 dim 表枚举); 仅"非A股板块(北交所83x/三板/指数) leak 进A股K线"才 flag。退市A股 pass。实测改后 **PASS**, data_audit **7/7 PASS**。(config universe_tables 删, builder 不动, 不补任何退市数据。)

## 本次改动 + 验证
| 文件 | 改动 |
|---|---|
| services/data_audit.py | `_check_kline_completeness` 改 clean-vs-source; `_check_kline_consistency` 移 calendar-gap 保留 dup+非交易日; `_open_conn` attach tushare_raw |
| config/data_audit_rules.yaml | kline_checks 加 source_raw_table/code/date (clean-vs-source 口径配置) |
| tests/test_data_audit_kline_completeness.py | 新单测 (clean无损→PASS / clean丢行→FAIL / 源含clean外股→ignore) 3 绿 |

验证: 单测 3 绿; 真实 data_audit **7/7 PASS** (kline_completeness 旧1711→PASS / kline_consistency→PASS / cross_table_consistency→PASS)。3 个 cry-wolf 全消灭, 门恢复可信 (无慢性红=无感知死)。

## 教训 (沉淀)
- **不需要补任何退市数据**: 退市股数据本在 K线 (真相源); universe = K线+前缀+ST 排除三规则 (universe.py "不需要 dim_all_ever_listed")。我一度把 dim_all_ever_listed 误当 survivorship 白名单要回补 = 违项目第一性原理, 用户纠正。
- **审计门别重造第二套 universe 判定**: cross_table 旧口径自建 dim-table 枚举 = 第二真相源, 应消费 universe.classify_exclusion 单一真相源。
- 校验异常 (31.5%/209) 先回第一手真相源 (universe.py)+实测分类, 别基于"中间表该完整"的假设升级成红线。

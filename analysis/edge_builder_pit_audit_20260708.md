# Edge 加工模块 PIT 安全性审计 (2026-07-08)

> 生命周期：历史证据（evidence-only）。本文记录当时审计结论，不是当前 PIT 证书；现行验证要求见 `docs/strategy_validation_contract.md`，代码与数据必须 live 重验。

## 背景

用户纠正了此前一处不准确的表述("edge/策略层代码现在还是空的")——`institution_profile.py`/`segments.py`/`technical_states/`/`market_pulse.py`/`rally_gt.py` 这批"加工 builder"模块 2026-07-02 起已经落地并在跑, 只是这两轮"数据地基是否稳固"的审计从未验证过它们内部 JOIN/时间锚逻辑的 PIT 正确性(只验证过它们该不该绕开 DataAccess 这个架构问题)。用户要求"并行查"。

## 方法

16-agent workflow: 5 个模块各自深度审计（逐 JOIN/窗口函数/复权口径检查当时采用的五类 PIT 反模式）+ 每条发现独立对抗验证（不采信单一审计员“干净”结论）。现行 PIT 口径以 `docs/strategy_validation_contract.md` 为准。

## 结论

| 模块 | 裁决 |
|---|---|
| segments.py | CLEAN(4处JOIN/窗口全部`<=t`锚定验证通过) |
| technical_states/ | CLEAN(5类反模式逐一验证通过) |
| market_pulse.py | CLEAN(申万码表不污染个股归属的关键交叉验证成立) |
| rally_gt.py | CLEAN(GT标注"用未来走势"属设计本质非bug, 唯一进特征字段严格不含当日) |
| **institution_profile.py** | **真问题, 已修复** |

## institution_profile.py 的问题

`recent_signals()`(读侧"跟随入口"函数)用 `open_date`(机构持仓披露的**报告期末**, 如季末0630)作为"近N天新建仓"的时间过滤锚, 但市场实际知道这次持仓变动的时间是 `notice_date`(**真实披露日**)——`notice_date >= report_date` 恒成立且实测**中位滞后31天**(`fact_top10_holder_period` 172.6万行全量测)。

`open_notice` 字段(即 `notice_date`)在 SQL 里已经被 SELECT 出来, 但从未进入 WHERE 过滤条件。

**实测生产数据影响**(2026-07-08, 30天窗口): 旧逻辑(open_date锚)显示35条"近期信号", 新逻辑(open_notice锚)显示46条, 二者重叠35条——即旧逻辑漏掉了11条**真实刚披露、市场明明看得到**的信号(约24%的真实近期信号被静默隐藏), 且未发现旧逻辑误报"过期新闻当最新"的实例(该方向的错误已用合成测试独立验证过存在可能性, 只是当前这批真实数据snapshot恰好没撞上)。

## 与"机构跟随策略"设计的关系

用户追问"机构没法知道确切哪天建仓, 只能靠公告日次日买入验证, 有什么建议"——查证 ~~`analysis/institution_follow_strategy_design_20260702.md`~~ **已被 `2d8f1dbb9`（2026-07-23 doc governance 删 62 份）删除，内容见 git history** (2026-07-02定稿)发现这个问题**已经被设计过**: §4"跟随策略模拟"明确规定信号日=`notice_date`, 入场=信号日次交易日开盘(涨停顺延), 并有独立的"跟随对象PIT评级红线"(选机构只能用expanding window历史战绩, 不能用全期战绩选——否则是selection leakage)。

但 `recent_signals()` 现在展示的 `median_alpha`/`win_rate_alpha` 是 **机构自身的历史战绩**(自身整窗VWAP成本口径, `mart_inst_profile` 产出), 不是"跟随该signal的预期收益"——这是设计文档里两个不同的问题(§3机构画像 vs §4跟随策略回测)被 `recent_signals()` 单个函数混着展示了。§4 真正的 execution-aware 跟随回测(含成本/涨跌停/PIT expanding-window机构评级)按设计文档 §5 探索弧规划走 E3-E5(sandbox验证→裁决→promote), 目前还没实现。

## 执行(本次修复范围, 刻意收窄)

1. **`recent_signals()` 过滤字段修正**: `open_date`→`open_notice`(`backend/services/institution_profile.py:390-403`), ORDER BY 次要排序字段同步改为 `open_notice DESC`, 加 `open_notice IS NOT NULL` 显式守卫。
2. **诚实标注**: 函数 docstring 加两段说明——(a) PIT锚修复的原因和实测证据; (b) 明确声明返回的 alpha/win_rate 是机构自身历史战绩非跟随预期收益, 真正的跟随回测是 §4 定义的独立未实现功能。
3. **红绿测试**(`backend/tests/test_institution_profile_api.py::test_recent_signals_anchors_on_notice_date_not_report_date`): 用相对当前时间动态构造的两个反向场景(report_date过期但notice_date刚披露 / report_date新但notice_date早过期)验证过滤逻辑双向正确; 手工临时回退代码验证测试确实会失败(红), 恢复后确认通过(绿)。

**明确不做的事**(避免趁着修PIT bug顺便扩大范围): 不实现 §4"跟随策略模拟"(execution-aware回测/涨跌停处理/PIT机构评级)——这是一整块未建功能, 应按设计文档自己定的节奏走独立的 sandbox 探索弧, 不应该趁修一个过滤字段的时候顺手建。

## 验证

全量测试562→563 passed(新增1条红绿测试), moth/dead-references/doc-drift/serve-read-layer 全绿, codegraph+complexity无新增, 生产数据实测确认修复方向正确(11条被漏信号补回)。

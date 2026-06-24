# aif10 gap 判定对抗性复核 (2026-06-24)

> 任务: 对抗性复核 `tushare_vs_aif10_comparison_20260624.md` 的 gap 判定 (哪边独有), 逐条用字段实证抓 overclaim。
> 默认每条判定可能错。
>
> 证据源 (measured, 非空断):
> - tushare 真相源 = `backend/config/tushare_api_catalog.json` (241 interface, 每个含 interface_code/title/description/output_fields) — 第一手官方目录字段, 未调 API。
> - aif10 真相源 = `analysis/aif10_field_inventory_20260624.json` (74 报表, 含 schema 字段名 + 实弹样本值)。
> - 复核了: 清单 A 12 项 + 清单 B 10 项 + 清单 C 5 项 + 18 域 verdict 抽查 = **共 27 条 gap 判定 + 18 域**。
>
> 三档纪律 (alpha 决策关键): **[T1] tushare 直接有该接口** / **[T2] tushare 能自算出来** (有底层字段, 需自己加工) / **[T3] tushare 完全没有**。

---

## 0. 一句话裁决 (复核后)

原对比的**基石层 (清单 A) 判定基本全部成立 [CONFIRMED]** — K线/复权/交易日历/涨跌停/集合竞价/资金流/筹码/质押/回购 tushare 真独有, aif10 实证无对应。

**清单 B (妙想独有 gap 菜单) 是 overclaim 重灾区**: 10 项里 **3 项确认 OVERCLAIM** (#5 派现概率部分、#6 盈利预测明细=已知错、#8 限售解禁持有人维度), **2 项需降档** (#2 估值分位、#4 机构分桶 是 T2 tushare 能自算, 非"独有"), 真正干净的 T3 妙想独有只剩 **资本运作(#7) + 经营评述NLP(#9) + 题材文本(#10)** 3 项 (且后两项 PIT 不可用/探索性)。

净: 妙想相对 tushare 的**真·独有数据价值显著缩水** — 真正 tushare 完全拿不到也算不出的, 只有"资本运作结构化(增发/配股/募投)"一项有实战 alpha 潜力; 其余妙想价值在**及时性/预算便利 (省自算)**, 不是"独有数据"。

---

## 1. 清单 A 复核 (tushare 独有 12 项) — 逐项实证 aif10 真无

| # | 项 | 复核结论 | 证据 |
|---|---|---|---|
| A1 | K线 OHLCV (日/周/月/分钟) | [CONFIRMED] | aif10 `RPT_PCF10_MARKETPER` 实证字段仅 `CHANGERATE/HS300_CHANGERATE/TIME_TYPE` (区间涨跌幅), **无 open/high/low/close/vol**。tushare `daily`(11字段含 open/high/low/close/vol/amount)。妙想真无 OHLCV。 |
| A2 | 复权因子 | [CONFIRMED] | aif10 全 74 报表无 adj_factor 字段。tushare `adj_factor`(ts_code/trade_date/adj_factor)。 |
| A3 | 交易日历 | [CONFIRMED] | aif10 无 trade_cal 等价。tushare `trade_cal`(exchange/cal_date/is_open/pretrade_date)。 |
| A4 | 每日指标 daily_basic (逐日 PE/PB/换手/量比/市值) | [CONFIRMED·近独有] | tushare `daily_basic` 18字段=逐日全市场点值序列 (pe/pe_ttm/pb/ps/turnover_rate/volume_ratio/total_mv/circ_mv...)。aif10 `STOCKVALUATIONTANTILE` 只给分位非逐日点值时序, `MARKETPER` 无估值点值。逐日时序 tushare 独有成立。 |
| A5 | 涨跌停价/集合竞价/停复牌 | [CONFIRMED] | tushare `stk_limit`+`stk_auction_o/c`(9字段含 vol/amount/turnover)+`suspend_d`。aif10 无任一对应。execution-aware 回测必需, 妙想真无。 |
| A6 | 筹码分布/胜率 | [CONFIRMED] | tushare `cyq_chips`/`cyq_perf`(获利盘/平均成本/胜率, 2018起)。aif10 无。 |
| A7 | 个股资金流向 | [CONFIRMED] | tushare `moneyflow`(20字段)/`moneyflow_dc`/`moneyflow_ths`(主力/超大单)。aif10 F10 层无逐日资金流。 |
| A8 | 股权质押 (统计+明细) | [PARTIAL — 见下] | tushare `pledge_stat`/`pledge_detail`(质押率/明细) 确有。**但 aif10 `RPTA_APP_ACCUMDETAILS` 实为质押累计明细报表** (字段 ACCUM_PLEDGE_TSR/PRE_ACCUM_PLEDGE_TSR/WARNING_LINE/OPENLINE/PF_ORG=质押方), 被原对比误归类为"限售解禁持有人维度"。所以"质押 tushare 独有"应改为"**双方都有质押明细**, tushare 是 pledge_detail, aif10 是 ACCUMDETAILS"。质押统计(pledge_stat)层 tushare 仍更直接。 |
| A9 | 股票回购 | [CONFIRMED] | tushare `repurchase`(9字段)。aif10 无独立回购报表 (DIVIDENDNEW 系列是分红非回购)。 |
| A10 | 申万行业 PIT 成员 | [CONFIRMED] | tushare `index_member_all` 字段含 `in_date/out_date/is_new` = 真 PIT 区间。aif10 `RPT_F10_RELATE_GN` date_field=None (snapshot 无 out_date) = latest-snapshot leakage 变体。PIT 行业成员 tushare 独有成立。 |
| A11 | 互动易 Q&A | [CONFIRMED] | tushare `irm_qa_sh`/`irm_qa_sz`(投资者问答全文)。aif10 无。 |
| A12 | 技术因子库 | [CONFIRMED·但措辞] | tushare `stk_factor`(35)/`stk_factor_pro`(261)/`stk_nineturn`。aif10 无预算技术因子。**注: 项目可由 K 线自算**, 故是 T1(tushare直给)但非"完全独有的数据" — 原对比已标注 "项目可自算可替", 准确。 |

**清单 A 小结**: 12 项中 11 项 [CONFIRMED] 基石/独有成立。仅 **A8 质押有归类错** (aif10 ACCUMDETAILS 其实是质押明细而非解禁持有人维度), 但不改"tushare 质押数据齐全"的大方向, 反而说明妙想质押也有数据 (双方都有)。基石不可替代结论稳固。

---

## 2. 清单 B 复核 (妙想独有 gap 菜单 10 项) — overclaim 重灾区

| # | 项 | 原判定 | **复核裁决** | tushare 实证 |
|---|---|---|---|---|
| B1 | 十大流通股东季中ad-hoc变动 | 妙想独有(已promote主源) | [OK·成立·但语义校正] | tushare `top10_floatholders` **有该数据 (含 ann_date 公告日)**, 不是"没有"。妙想优势是**及时性/覆盖** (收季中临时公告), 非"tushare 无此数据"。原对比清单 C 表述准确 (双方都有妙想更及时), 但勿表述为"tushare 拿不到十大流通股东"。**T1 双方都有, 妙想及时性更优**。 |
| B2 | 估值历史分位 PE/PB/PS/PEG @多窗 | 妙想独有(已沙化正式源) | [PARTIAL·应降 T2] | tushare 无"分位"接口 (catalog 无 分位/percentile 命中)。**但 `daily_basic` 逐日给 pe/pe_ttm/pb/ps/ps_ttm 全历史**, 分位是这些点值的历史百分位 = **T2 tushare 能自算** (且自算的还是真 PIT 时序, 比 aif10 snapshot 更干净)。妙想价值=省自算 + PEG (daily_basic 无 PEG, 需配 forecast 增长率自算)。**不是"独有数据", 是"预算便利"**。 |
| B3 | 同行估值/成长/杜邦排名 | 妙想独有(已沙化正式源) | [PARTIAL·应降 T2] | tushare 无直接"行业排名"接口。**但 `index_member_all`(申万成员)+`daily_basic`(估值点值)+`fina_indicator`(成长/杜邦) JOIN 后按行业分组算分位/排名 = T2 可自算** (原对比清单 C 已诚实写 "tushare 需自 JOIN 算")。妙想价值=预算便利 (省 JOIN), 非独有数据。注: aif10 同行表多为 report_period 锚需外接披露日。 |
| B4 | 机构持仓 ORG_TYPE 分桶 | gap(QFII已接) | [PARTIAL·T1+T2 混合] | tushare `fund_portfolio`(仅公募)+`stk_holdertrade`(含 holder_type/holder_name 增减持)+`stk_surv`(org_type)。**公募持仓 T1 直给; QFII/社保/险资/信托独立分桶持仓 tushare 无 → 这部分 T3 妙想独有**。妙想 `ORGHOLDDETAILS` 的 ORG_TYPE 全桶(基金/QFII/社保/券商/保险/信托) tushare 拼不齐。**部分独有 (非公募桶)成立, 但"机构持仓"整体 tushare 有公募, 勿全归妙想**。 |
| B5 | 分红派现概率提示 | gap | [PARTIAL — 拆两半] | **(a) 派现概率/分红评级 (DIVIDENDNEW_LITY 的 SCORE/DIVIDEND_LEVEL/派现概率)= T3 妙想独有** (tushare 无预测性派现概率, 仅 `dividend` 历史实施)。**(b) 但分红事实数据 tushare `dividend` 齐全** (含 div_proc 进度/cash_div/ex_date/pay_date/imp_ann_date 实施公告日), aif10 `DIVIDEND_MAIN` 等价。原对比把整项当独有偏宽 — **只有"派现概率预测"这个衍生评分独有, 分红基础数据双方都有**。 |
| **B6** | **盈利预测明细 (机构-分析师-发布日)** | **gap(高潜,PIT干净)** | **[OVERCLAIM — 已知错·确认]** | tushare **`report_rc`=卖方盈利预测数据** (23字段: org_name 机构/author_name 分析师/report_date 发布日/eps/np/tp 目标价/rating 评级/imp_dg 评级变动, 2010起)。**与 aif10 `PREDICTDETAIL`(机构-分析师-发布日) 几乎一一对应**。**T1 tushare 直接有, 非妙想独有**。补强: tushare 另有 `forecast`(业绩预告, p_change_min/max 公司自身预告)+`broker_recommend`(券商金股) 也属预期域。**此项判定错误, 须从妙想独有 gap 菜单删除**。 |
| B7 | 资本运作 (重组/募资/募投) | gap(tushare全无结构化) | [CONFIRMED·真独有] | catalog 关键词扫 `重组/募集/增发/配股/再融资/资本运作/收购/并购/定增/募投` **零命中**。tushare 仅 `dividend`(送股)/`share_float`(解禁)/`anns_d`(公告全文 PDF URL, 非结构化)。aif10 `RECAPITALIZE/CAPITAL_RAISE/CAPITAL_ITEM`(增发配股募投结构化, NOTICE_DATE PIT 可用)。**T3 妙想真独有, 高潜成立**。 |
| **B8** | **限售解禁 (持有人维度)** | **gap(tushare share_float 无持有人拆分)** | **[OVERCLAIM]** | tushare **`share_float` 字段实证含 `holder_name`** (ts_code/ann_date/float_date/float_share/float_ratio/**holder_name**/share_type)。**原判定"tushare 无持有人拆分"是错的 — tushare share_float 就带 holder_name**。此外 aif10 被引为持有人维度证据的 `ACCUMDETAILS` 实为**质押累计报表**(见 A8), 非解禁持有人明细; 真解禁表 `LIFTFUTURE` 只有 `LIFT_HOLDER_NUM`(户数) 无逐 holder。**T1 双方都有(均含 holder_name), 妙想无明显优势, 此 gap 不成立**。 |
| B9 | 经营评述 NLP 全文 | gap(探索性) | [CONFIRMED·真独有·但 PIT 弱] | tushare 无管理层讨论(MD&A)结构化全文接口 (`anns_d` 仅 PDF URL 需自己 OCR/解析)。aif10 `OP_BUSINESSANALYSIS` 直给文本。**T3 妙想独有成立**, 但 report_period 锚需外接披露日, 探索性, alpha 未验。 |
| B10 | 题材亮点/详情文本 | gap(PIT不可得慎用) | [CONFIRMED·真独有·但 PIT=NO] | tushare 概念是成分(ths_member/dc_member)非"题材描述文本"。aif10 `CORETHEME_CONTENT/BOARDTYPE` 给文本。**T3 数据独有**, 但 date_field=None 静态 snapshot, **单用 leakage (项目 §4.5 红线)**, 实战不可用。 |

### 清单 B overclaim 汇总

- **B6 盈利预测明细 = OVERCLAIM (确认已知错)**: tushare `report_rc` 就是它, T1 直给。**删出独有菜单**。
- **B8 限售解禁持有人维度 = OVERCLAIM (新抓)**: tushare `share_float` 字段带 `holder_name`, "tushare 无持有人拆分"判断错; 且 aif10 引用的 ACCUMDETAILS 实为质押报表归类错。**此 gap 不成立**。
- **B5 分红派现概率 = 部分 OVERCLAIM**: 只有"派现概率预测评分"独有, 分红基础数据 tushare `dividend` 齐全。**应收窄为"派现概率预测栏目独有"**。
- **B2 估值分位 / B3 同行排名 = 降档 T2 (非独有)**: tushare `daily_basic`+`fina_indicator`+`index_member_all` 能自算 (且自算更 PIT 干净)。妙想价值=预算便利非独有数据。**不应表述为"妙想独有"**。
- **B4 机构分桶 = 部分**: 公募 T1 tushare 有, 非公募桶(QFII/社保/险资/信托) T3 妙想独有。

---

## 3. 清单 C 复核 (双方都有妙想更优 5 项) — 确认 tushare 侧接口真实

| # | 域 | tushare 接口 | 复核 | 字段实证 |
|---|---|---|---|---|
| C1 | 十大流通股东 | `top10_floatholders` | [OK] 存在·语义对 | 9字段(holder_name/hold_amount/hold_ratio/hold_change/**ann_date**)。妙想更及时(及时性优势)成立; 但 tushare **有** ann_date PIT 锚, 勿说 tushare "无 PIT"。600388 例子是覆盖滞后(季报驱动), 非字段缺失。 |
| C2 | QFII 持仓 | `fund_portfolio`(仅公募) | [OK] 接口真实·语义对 | fund_portfolio 确仅公募(ts_code=基金代码/symbol=持仓股)。tushare 无独立 QFII → 妙想 `DMSK_HOLDERS` 更优**成立 (T3 QFII 桶独有)**。 |
| C3 | 估值分位 | `daily_basic`(点值自算) | [OK·但应标 T2] | daily_basic 有 pe/pb/ps 逐日, 分位可自算。"妙想更优"=省自算便利, 准确; 但本质是 T2 而非妙想独有数据 (见 B2)。 |
| C4 | 同行财务对比 | 需自 JOIN 算 | [OK] 诚实 | tushare 确无行业聚合接口, 需 index_member_all + daily_basic/fina_indicator JOIN。表述诚实 (T2)。 |
| C5 | 股本结构时序 | `stk_premarket`(盘前快照) | [OK] 接口真实 | stk_premarket 7字段(total_share/float_share 盘前点值)。aif10 `EH_EQUITY` 70字段历年时序更细**成立**。注: tushare 历年股本变动可由 `namechange`/财报推, 但无 EH_EQUITY 这种现成时序表 → 妙想更优合理。 |

**清单 C 小结**: 5 项 tushare 侧接口全部真实存在·语义对 [OK]。唯一须补正: C1/C3 的 tushare 侧**有数据有 PIT 锚**, "妙想更优"的真实含义是**及时性(C1)/预算便利(C3/C4)**, 不是 tushare "拿不到", 措辞勿夸大成 tushare 空白。

---

## 4. 18 域 verdict 抽查 (字段实证一致性)

| 域 | 原 verdict | 复核 | 备注 |
|---|---|---|---|
| 01 行情K线 | tushare_only_bedrock | [OK] | MARKETPER 无 OHLCV 已实证, 基石成立。 |
| 02 财务三表 | both_have | [OK] | tushare balancesheet(158)/income(94)/cashflow(97)+disclosure_date(PIT锚) vs aif10 G* 系列。tushare 有 disclosure_date(actual_date 真披露日)是 aif10 缺的 PIT 锚 — 原对比已正确指出。 |
| 03 财务指标 | both_have | [OK] | fina_indicator 167字段 vs MAINFINADATA 166。对等。 |
| 04 股东 | both_have_aif10_more_timely | [OK] | 见 B1/C1, 及时性成立。 |
| 05 质押回购 | tushare_mostly_only | [PARTIAL] | 质押 tushare 直接(pledge_stat/detail), **但 aif10 ACCUMDETAILS 也是质押累计明细** (原对比误标为解禁持有人), 应改为"质押双方有明细, 回购 tushare 独有, 解禁双方有"。 |
| 06 估值分位/同行 | aif10_richer | [PARTIAL] | 应标"妙想预算便利(T2 可自算)", 非"独有"。见 B2/B3。 |
| 07 分红 | both_have_aif10_more | [OK·收窄] | 分红基础双方有(tushare dividend 含 imp_ann_date/div_proc), 妙想多"派现概率预测"(B5)。 |
| 08 机构持股/预测 | aif10_richer | [PARTIAL — 重要] | **盈利预测 tushare report_rc 直给(B6 错)**; 机构持仓公募 tushare 有(B4); 调研双方有(stk_surv 含 org_type)。妙想真优=非公募持仓桶 + 评级统计。verdict 应从 "aif10_richer" 降为 "both_have_aif10_partial_richer"。 |
| 09 行业对比 | both_have_diff_axis | [OK] | 申万 PIT tushare 主, RELATE_GN snapshot leakage 风险已正确标注。 |
| 10 龙虎榜 | both_have | [OK] | top_list/top_inst/hm_detail/hm_list(游资名录 tushare 独有)。 |
| 11 大宗交易 | both_have 等价 | [OK] | block_trade(7) vs BLOCKTRADE(38), 等价。 |
| 12 融资融券 | both_have | [OK] | 个股两融双方有; tushare 多转融券(slb_*)。 |
| 13 北向 | both_have_both_degraded | [OK] | 双方都受停披露限制, 准确。 |
| 14 股本结构 | both_have | [OK] | 见 C5, EH_EQUITY 时序更细成立。 |
| 15 高管 | both_have | [OK] | tushare stk_managers/stk_rewards(薪酬独有) vs MANAINTRO/EXECUTIVE_HOLD。 |
| 16 资本运作 | aif10_only | [OK·真独有] | 关键词零命中已实证 (B7), T3 妙想真独有。 |
| 17 概念题材 | both_have_diff_nature | [OK] | 成分 tushare(含资金流 moneyflow_cnt), 文本 aif10(PIT 不可用)。 |
| 18 资讯公告研报 | both_have_diff_nature | [OK·校正] | **report_rc 应明确算 tushare 卖方预测主源**; aif10 BUSINESSANALYSIS(MD&A)/题材文本是 tushare 缺的; 互动易 tushare 独有。 |

**18 域抽查小结**: 14 域 [OK], 4 域需校正措辞 (05质押归类 / 06估值降 T2 / 08预测错 / 18 report_rc 定位) — 均与清单 B 的 overclaim 同根 (高估"妙想独有")。

---

## 5. 复核后·真正干净的"妙想独有"清单 (T3, 破 §4.3 例外的真候选)

只有以下是 **tushare 完全没有也算不出 (T3)** 的妙想数据:

| 真独有项 | 妙想报表 | alpha 潜力 | PIT 可用 | 破例资格评估 |
|---|---|---|---|---|
| **资本运作结构化 (增发/配股/募投项目)** | RECAPITALIZE/CAPITAL_RAISE/CAPITAL_ITEM | 事件驱动 (再融资/重组) | [OK] NOTICE_DATE PIT 可用 | **最强候选** — tushare 仅公告全文 PDF, 无结构化, 实战可用 |
| **非公募机构持仓分桶 (QFII/社保/险资/信托)** | DMSK_HOLDERS, ORGHOLDDETAILS(非公募桶) | 资金属性 (聪明钱) | DMSK 带 NOTICE_DATE(部分临时公告); ORGHOLD 报告期锚需外接 | 中候选 — QFII 已接, 公募部分 tushare 已有 |
| **派现概率预测评分** | DIVIDENDNEW_LITY (SCORE/DIVIDEND_LEVEL) | 红利增强 | [WARN] 无 date_field snapshot | 弱候选 — 是衍生评分非原始数据, PIT 须自存 |
| 经营评述 MD&A 全文 | OP_BUSINESSANALYSIS | NLP (探索性) | [WARN] report_period 需外接披露日 | 探索性, alpha 未验 |
| 题材亮点/详情文本 | CORETHEME_CONTENT | 概念标签 | **[NO] 静态 snapshot, 单用 leakage** | **不可用** (PIT 红线) |

**从原 10 项 gap 菜单收敛到 5 项 T3 真独有**, 其中 PIT 干净 + 实战可用的只有 **资本运作(1 项)** 强、**QFII/机构分桶(1 项)** 中。其余 2 项 PIT 弱/探索性, 1 项 PIT 不可用。

---

## 6. 一句话: 妙想真实独有价值

**显著缩水。** 原 gap 菜单的"妙想独有"10 项, 复核后 **3 项 overclaim (盈利预测/限售解禁持有人/派现基础数据)、2 项降为 T2 tushare 能自算 (估值分位/同行排名)**。真正 tushare 拿不到也算不出 (T3) 且 PIT 干净可实战的, **只剩"资本运作结构化"1 项有硬 alpha 价值, "非公募机构持仓分桶"1 项中等**。妙想在已 promote 域的真实优势是**及时性 (十大流通股东季中变动) + 预算便利 (估值分位/同行排名省自算)**, 而非"独有数据" — 这与 §4.3 已承认的"holder 主源因 tushare 季报滞后"理由一致, 但**不构成更多新的破例理由**。措辞上须把"妙想独有"严格限定到 T3 资本运作 + 非公募机构桶, 其余降级为"更及时/预算便利"。

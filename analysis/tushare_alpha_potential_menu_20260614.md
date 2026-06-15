# tushare 未抓接口 Alpha 潜力评估菜单 (2026-06-14)

> **状态 (2026-06-15 N27 点名 + R1 后 superseded)**: 接口 catalog 仍有效; 但 edge 列(medium/low)是 a-priori 脑补假设**从未 measured**(违 §1.2, N27; flow 族实测兄弟 moneyflow net_mf≈0 已证近零增量), 应改 **measured/unknown 两段**; 入选/转正判据"超 +0.064 RankIC"被 R1 推翻(IC necessary 快筛, **转正须含成本 execution-aware 绝对收益** tradability_verdict+kpi_verdict); 优先级反转(慢衰减绝对>快衰减相对), block_trade/打板等快衰减降级。owner 冲突优先: 判断法典=`docs/strategy_validation_contract.md` · 缺陷体系 N25/N27/N29=`analysis/design_deficiencies_extension2_20260615.md` · P3 裁决+Phase D=`analysis/p3_execution_aware_verdict_20260615.md`。

> 方法: 137 个 A股相关未抓接口 (catalog 239 - 已抓29 - off-strategy73), 8 域研究员并行评估 alpha 潜力
> (Workflow wf_7bb5e2a9, 综合 agent 崩→controller 从 journal 抢救 8 域 90 接口评估自综合, mythos §18)。
> 维度: alpha 假设 / 因子族 / PIT 风险 / 与已抓29冗余 / 预期 edge / 优先级。
> inventory 原始: analysis/tushare_ungrabbed_inventory_20260614.json。
> 诚实基线: 90 接口 **无一 high edge** (23 medium / 52 low / 15 unknown) — 多数新数据真实增量低 (符合项目真相);
> alpha 须超 L0 标尺 reversal +0.064 才入。验证优先于抓取。

## 优先级分布
3 P0 / 22 P1 / 28 P2 / 37 skip(冗余)。

## P0 (最该下一个验, 3)
| api | 中文 | 因子族 | edge | PIT | alpha 假设 |
|---|---|---|---|---|---|
| `cashflow` | 现金流量表 | quality+growth | medium | medium | FCF/经营现金质量, FCF vs 净利润背离=盈利质量; 补全财务三大表 (income 已抓) |
| `block_trade` | 大宗交易 | flow | medium | **low** | 机构加权成本 vs 现价 + 成交规模 + 买卖方向 = 机构折价/sniper confluence; moneyflow 无此 |
| `anns_d` | 全量公告 | event/sentiment | medium | medium | 公告类型+ann_date → 事件日历 → 价格冲击窗 (PEAD 系统化) |

## P1 精选 (medium edge + low PIT = 风险调整最优)
| api | 中文 | 因子族 | 备注 |
|---|---|---|---|
| `moneyflow_dc` | 个股资金流(DC) | flow | 大单分层/主力介入度; 与已抓 moneyflow 有增量(分层) |
| `hsgt_top10` | 沪深股通十大 | flow | 北向 per-stock 意图 (已抓 moneyflow_hsgt 仅市场级聚合, 这是真增量) |
| `ccass_hold` | 中央结算持股 | flow | 北向真实持仓占比, PIT 锚 T 日清晰 |
| `daily_info` | 市场交易统计 | sentiment | 市场宽度/regime 诊断 |
| `margin` | 融资融券汇总 | flow | 杠杆情绪 (融资率高位=反转信号), 全新族 |
| `limit_step` | 连板天梯 | momentum/sentiment | 主升浪猎手相关 (但 medium PIT + 均值回归 regime 存疑) |
| `disclosure_date` | 财报披露计划 | event | PEAD 事件锚 (披露意外性) |
| `stk_auction` | 集合竞价 | microstructure | 开盘情绪温度/日内反转 |

## 重要反转 (spec 旧候选被研究降级)
| api | 旧定位 | 新判 | 为何降 |
|---|---|---|---|
| `cyq_chips` | spec P1 (救活winner_rate) | **P2** | 与已抓 cyq_perf 双料冗余 (都盘后筹码; cyq_perf 已有获利盘/cost); 问题是 cyq_perf 复权口径冻结, 非缺数据 |
| `kpl_list` | spec P1 (连板) | **P2 高PIT** | 榜单 T 日 16点前不齐=隐形未来函数; limit_list_d/limit_step 已覆盖涨跌停 |
| `repurchase` | spec P2 (回购事件) | P2 | PEAD 信号被已抓 forecast 吸收, 增量微弱 |
| `top10_holders` | (十大股东) | P2 高PIT | 快照无 ann_date=PIT 陷阱; 均值回归 regime 下筹码信号弱 |

## 冗余 skip (37, 防重复抓取的关键发现)
- **技术因子**: `stk_factor`/`stk_factor_pro`/`stk_nineturn` → 冗余于 20 现成公式 (L0 已覆盖, 动量已证负相关)。
- **行情衍生**: `monthly`/`weekly`/`bak_daily` → daily 可推导; `index_monthly`/`index_weekly` → index_daily resample。
- **板块系统**: `ci_*`(中信)/`tdx_*`/`dc_concept*`/`ths_member` → 冗余于已抓 dc_member/index_member_all。
- **资金流变种**: `moneyflow_ths`/`cnt_ths`/`ind_ths` → 冗余于已抓 moneyflow/dc_member。
- **转融券**: `slb_*` → 冗余于 margin。
- **股东派生**: `stk_holdernumber`/`top10_floatholders`/`stk_managers`/`stk_rewards` → 冗余于 cyq_perf/moneyflow/fina_indicator。
- **热度变种**: `kpl_concept_cons`/`dc_hot`/`hm_list` → 冗余于 dc_member/top_list/ths_hot。
- **宏观共线**: `cn_gdp`/`eco_cal` → 与 cpi/ppi/m 高度共线; regime 已由日线波动率覆盖。

## 仍空白的因子族 (真缺口)
1. **现金流质量** (cashflow) — 已抓仅 income, 无现金流/资本支出 → FCF/盈利质量空白。
2. **机构折价/大宗** (block_trade) — moneyflow 是散户+主力混合, 无机构大宗折价信号。
3. **北向 per-stock 真实持仓** (ccass_hold/hsgt_top10) — moneyflow_hsgt 仅市场聚合。
4. **事件日历系统化** (anns_d/disclosure_date/share_float) — 公告/披露/解禁事件窗未系统化。
5. **杠杆情绪** (margin) — 融资融券完全空白。

## 建议: 下一步接入 consumer_alpha 矩阵验证的 2-3 个
1. **cashflow** — 填 quality 缺口, 补全财务三大表, 与已抓 income/fina_indicator 组成完整基本面族。
2. **block_trade** — flow 全新信号, PIT 干净 (low), 机构折价是 moneyflow 抓不到的维度。
3. **hsgt_top10** (或 daily_info) — 北向 per-stock 真增量 / 或 regime 诊断。
理由: 都 P0/P1 + 低PIT + 非冗余 + 填空白族 + 因子族互补 (quality+flow+flow)。**但全是 medium edge**:
按验证优先原则, 先接入跑矩阵, 只有 OOS RankIC 超 +0.064 (且过 Tier-2) 才转正抓取保鲜, 否则落档 dead。

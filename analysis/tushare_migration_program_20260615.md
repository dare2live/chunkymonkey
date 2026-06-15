## tushare 全量迁移工程 — 分阶段执行计划 (2026-06-15)

> 状态: live (执行中)。owner=本文件。上承 `tushare_full_migration_map_20260611.md` (44表范围) + CLAUDE §4.3。
> 缘起: 用户 2026-06-15 "切 tushare 要退役旧数据删除不是隐藏 + 确保全部数据来自 tushare"。
> tdxhub 2022-12-30 复权 glitch (比亚迪 tdxhub+210% vs tushare raw+0.87%) 实证 tdxhub K线质量不如 tushare, 强化迁移。
> 政策裁决 (2026-06-15 覆盖 06-11 热备政策): tdxhub K线 build/sync 退役; 物删旧表 (用户"删除非隐藏")。

### 每阶段铁律 (迁移地图执行纪律 #5)
1. repoint: 所有消费方切到 tushare 源 (grep 全消费方, 不漏 mythos §14)
2. 双轨核对: 新旧重叠期关键字段一致率 (≥20 交易日; K线已做 avg收益diff 0.03%)
3. 物删: 旧表 DROP + build/sync 脚本 git rm + daily_update/registry step 删 (能删必删, 不注释/隐藏)
4. 对抗复审: 重大改动 (§11) 关键 diff 复审; moth/gate 全绿
5. INDEX/data_layers/控制面同步; 高频 commit

### 阶段 (按依赖序)

| 阶段 | 内容 | 消费方/范围 | 状态 |
|---|---|---|---|
| **M0** | 回测 K线切 tushare 前复权 (load_kline) | 回测实验 | [DONE] (82180b0f, price_kline_qfq_tushare 2019+) |
| **M0.5** | moneyflow 扩 2020 回补 | 资金流因子 | [WIP] 回补中 (8a9d3b97) |
| **M1** | **K线 serving 消费链切换**: 重定义 `v_price_kline_qfq` 从 tushare (全16列schema); 退役 tdxhub K线 build/sync (build_price_kline_tdxhub.py / sync_runner kline / updater_market_data); repoint 19 直接 price_kline_tdxhub 读者 | v_price_kline_qfq 20+ 消费方 (data_quality/regime/picture/scoring/screening/universe/turtle) + 19 直接 | 进行中 |
| **M2** | **ETF → fund_daily**: ETF 子系统 (etf_engine/mining/snapshot) 从 akshare price_kline 迁 tushare fund_daily; 然后物删 akshare price_kline | ETF 10+ 消费方 | 待 M1 后 |
| **M3** | **物删 tdxhub/akshare K线表**: M1/M2 repoint 完成后 DROP price_kline_tdxhub + price_kline | 表删除 | 待 M1/M2 |
| **M4** | **其余 39 表迁移**: aif10 9 (估值分位→daily_basic自算/财务→income/一致预期→report_rc) + akshare 22 (财务/解禁/大宗/陆股通/回购/ETF/基金持股等→对应tushare接口, 见迁移地图W-B) | 逐表 (双轨→物删) | 待 |

### 真例外 (保留旧源, 迁移地图末节)
- 股东增减持计划 (fact_shareholder_plan): tushare 无结构化接口; tdxhub 热备保留
- 调研事件 (stk_surv): W-C 分页支持后切
- CYQ 筹码: 本地计算保留, 输入切 daily+daily_basic.float_share

### 当前执行: M1 (K线 serving 消费链切换)
见 commit 序列; v_price_kline_qfq 重定义为 tushare-backed (全16列), 双轨已验 (M0 收益对账 0.03%), 退役 tdxhub K线 build/sync。

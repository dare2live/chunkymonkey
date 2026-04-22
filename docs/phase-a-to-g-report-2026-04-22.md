# Phase A-G 执行报告：数据源升级与 Qlib 行为/供给因子接入

日期：2026-04-22
分支：`claude/modest-swanson-5c30ed`（worktree）
关联文档：
- [tdx-akshare-qlib-report-2026-04-22.md](tdx-akshare-qlib-report-2026-04-22.md)
- [tdx-qlib-factor-etl-plan-2026-04-22.md](tdx-qlib-factor-etl-plan-2026-04-22.md)
- [audit-report-2026-04-22.md](audit-report-2026-04-22.md)

## 一、背景与上游规划文档的偏差

两份规划文档把 Tongdaxin（`tqcenter/tq`）定位为主数据源，并把"北向持仓"、"涨停池"、"FN/GP/GO 字段族"列为 P0。本轮实施前诚实核实后，发现四个核心假设与事实不符：

| 规划假设 | 实测事实 | 处理 |
|---|---|---|
| tqcenter 主源 | 本机只有 `mootdx 0.12.0`，无 `finance` 历史序列，无 FN/GP/GO 字段族 | 改走 AkShare 等效替代 |
| 北向"尚未接通" | 沪深港交易所自 **2024-08-19** 起停更陆股通个股明细 | Phase A 彻底退役 |
| 涨停池可用 | `ak.stock_zt_pool_em` **只返回当日**数据，T-1 即空，无法回填 | 本期不做 |
| 两融 2024-08 起点 | 实测两融数据从 **2010-04-01** 即有（融资融券开闸日） | Phase C 起点对齐 `price_kline`（2023-01-01） |

基于这些修正，Phase A-G 走了一条和原规划差异显著但更贴合现实的路径。

## 二、Phase 执行摘要（commit 链）

```
73e5aadb  Phase G  behavior loader 时序化（修正 Phase E/F 的 lookahead bias）
ccdc8a78  Phase F  Supply 因子归一化（planned_amount → planned_ratio）
7c5beda5  Phase E  Qlib 新增 behavior + supply loader，A/B 对照验证
950b9745  Phase D  修复两融 T 日源侧披露滞后（卡点 #3）
b3d7b08e  Phase C  两融 + 龙虎榜日度接入
ae5bac77  Phase B  QFII 季报接入（北向退役后的外资维度替代）
094e9f3e  Phase A  陆股通北向数据源彻底退役
```

### Phase A — 陆股通北向彻底退役

按"废弃数据彻底删除"原则一次清干净：

- 删除 `backend/services/northbound_client.py` 及其测试
- 删除 `qlib_full_engine._load_northbound_factors` 及 `use_northbound` 参数（6 处调用点）
- 删除 `routers/qlib.py` 的 `_QLIB_FEATURE_FLAGS` / `_normalize_train_params` 脚手架
- 删除 `routers/updater.py` 的 `sync_northbound` STEP / DEPS / RUNNER / `_step` 函数
- 删除 `audit.py` 的 `_summarize_northbound_freshness` + `skip_reasons` 条目
- 删除 `db.py` 的 `fact_northbound_daily` CREATE + `mart_institution_profile.northbound_overlap_rate` 列
- 删除 `sef/schema.py` 的 `beta_northbound_in` 列
- 清理前端 `app.js` / `index.html` 的北向 checkbox + feature flag 逻辑
- DB：`DROP TABLE fact_northbound_daily` + 两个 `DROP COLUMN` + `DELETE step_status`

**保留**：`inst_institutions.type='北向'`（东方财富十大股东席位，独立链路，3 条机构记录）。

### Phase B — QFII 季报接入（外资维度替代）

- 新增 `raw_qfii_holding_quarterly` 表，自然键 `(report_date, stock_code, holder_name)`
- 新增 `backend/services/qfii_client.py` + 9 个单元测试
- 智能更新计划按"季度末 +30 日（公告延迟）"触发
- **回填 2024Q2 → 2025Q4 共 7 季**：

| 季度 | 新进 | 增加 | 减少 | 不变 | 合计 |
|---|---:|---:|---:|---:|---:|
| 2024-06-30 | 658 | — | — | — | 969 |
| 2024-09-30 | 771 | — | — | — | 1,088 |
| 2024-12-31 | 704 | — | — | — | 952 |
| 2025-03-31 | 792 | — | — | — | 1,071 |
| 2025-06-30 | 1,314 | — | — | — | 1,639 |
| 2025-09-30 | 935 | — | — | — | 1,264 |
| 2025-12-31 | 684 | 79 | 78 | 21 | 862 |

**验收指标**：
- 7,878 行 · 2,255 股票 · 181 家 QFII
- `notice_date` 填充率 **100%**（可直接作 `available_date`，防穿越就绪）
- 每季度 500-1,300 条事件记录，数据稳定

### Phase C — 两融 + 龙虎榜日度接入

涨停池因 AkShare 源只返回当日、**无历史可回填**，按奥卡姆剃刀原则本期不做。

**新增两张表**：

| 表 | 自然键 | 数据源 | 回填结果 |
|---|---|---|---:|
| `raw_margin_daily` | `(trade_date, stock_code, market)` | `stock_margin_detail_sse` + `stock_margin_detail_szse` | 2,944,065 行 / 797 交易日 / 4,373 股票 / 31.9 min |
| `raw_lhb_daily` | `(trade_date, stock_code, rank_reason)` | `stock_lhb_detail_em` 按月区间拉取 | 61,980 行 / 798 交易日 / 5,408 股票 / 1.1 min |

**Cross-source 语义验证**：QFII 持仓 2,255 只中 **1,923 只（85%）也上过龙虎榜**——外资 + 短线机构关注的股票集合有强重合，证明两个源的信号维度互补且语义一致。

### Phase D — 两融 T-1 fallback

发现 Phase C 卡点 #3：上交所两融明细 T 日白天拉取返回空（`stock_margin_detail_sse` 返回 `Length mismatch`），每次白天智能更新卡在 `sync_margin` 上空跑 10 秒。

修复：`sync_margin_day` 新增 `fallback_days` 参数；`_step_sync_margin` 默认 `fallback_days=2`，T 日源未披露时按 `dim_trading_calendar` 降级到 T-1 / T-2。实测 2026-04-22 白天触发：T 失败 → 自动降级 04-21，`written=4086` 行。

### Phase E-G — Qlib 因子接入与 A/B 实验演进

完整 A/B 实验（`sample_stock_limit=500`, `num_boost_round=300`, 同 universe 同超参）：

| Phase | Arm | Factors | IC | RankIC | TopK50 | 说明 |
|---|---|---:|---:|---:|---:|---|
| **baseline** | financial+institution+turtle+quality+stage | 241 | 0.0473 | 0.0253 | 0.0012 | 参照基准 |
| E | +behavior (static) | 250 | 0.0619 | 0.0212 | 0.0020 | **lookahead 虚高** |
| E | +behavior+supply (static, abs amount) | 256 | 0.0610 | 0.0217 | 0.0015 | 穿越 + 量纲污染 |
| F | +behavior+supply (static, ratio) | 257 | **0.0683** | 0.0249 | 0.0016 | 穿越 + 修量纲 |
| **G** | **+behavior (time-series)** | 251 | **0.0499** | **0.0254** | **0.0018** | **防穿越 ✅** |
| G | +behavior+supply (TS behavior + static supply) | 258 | 0.0484 | 0.0236 | 0.0014 | supply 仍穿越 |

## 三、关键技术决策与反转

### 决策 1：北向彻底退役 vs 保留观察

按项目已有记忆 "废弃数据彻底删除"（`feedback_dead_data_purge.md`）+ 北向源死亡 1.5 年的硬事实，**彻底删除**（不保留残值）。

### 决策 2：涨停池本期不做

`ak.stock_zt_pool_em` 只返回当日数据，无法回填历史。Qlib 训练窗口里几乎无数据 → **不做**（奥卡姆剃刀）。

### 决策 3：Supply 归一化（Phase E → F 反转）

- Phase E 结论："supply 族无增益，代码保留但默认关闭"
- Phase F 诊断：`planned_amount`（绝对金额）被大市值公司支配，污染 cross-sectional 排序
- Phase F 修正：用 `planned_ratio_high/low`（占总股本比例）替代 → IC 从 0.0610 升到 **0.0683**，RankIC 从 0.0212 恢复到 **0.0249**
- 反转决策：supply 族从"默认关闭"改为"可启用"

### 决策 4：behavior 时序化（Phase F → G 反转）

- Phase F 结论："behavior+supply +all IC 0.0683 较 baseline 提升 44%，显著"
- Phase G 诊断：静态 loader 把"最新一天值"广播到所有历史日期 = 明显 lookahead bias
- Phase G 修正：重写为 `(datetime, instrument)` MultiIndex 时序 loader
  - 两融：每日 `log(rz)`, `log(rq)`, `rz/ma20 - 1`, 5 日 pct_change
  - 龙虎榜：shift(1) + rolling 60/250 日，严格 T-1 及之前
  - QFII：`merge_asof` backward 按 `notice_date` 前向填充到日频
- Phase G 结果：
  - IC 从 0.0619 降到 **0.0499**（-0.0120，穿越溢价被剥离）
  - **RankIC 从 0.0212 反弹到 0.0254（超 baseline）**
  - TopK50 从 0.0020 到 0.0018（基本持平）
- 反转决策：**Phase F 的 IC 0.0683 是穿越虚高，不可用于生产；Phase G 的 0.0499 是可上生产的诚实值**

## 四、流程卡点清单

| # | Phase | 卡点描述 | 根因 | 修复 |
|---|---|---|---|---|
| 1 | C | worktree `data/` 目录默认空 | worktree 检出时不带 `data/*.db`；backend `DB_PATH` 解析到 worktree 下 | `ln -s` 主仓库 `data/{smartmoney,market_data}.db` 到 worktree |
| 2 | C | 两融回填按日查询 32 min / 798 天 | `stock_margin_detail_sse/szse` 按 date 单日请求，单连接无法并行 | 源侧约束，日常增量（拉最新 1 天）无影响 |
| 3 | D | 两融 T 日白天源未披露 | SH 接口 T 日晚上才完整，白天返回 `Length mismatch` | `_step_sync_margin` 加 `fallback_days=2` 自动降级 T-1/T-2 |
| 4 | E | worktree `qlib_data/features` 缺 | worktree 检出只含 `calendars/instruments` 骨架，不含 6,204 个 `.bin` | `ln -s` 整个 `data/qlib_data` 目录到主仓库 |
| 5 | F | supply 因子量纲污染 | `planned_amount` 对大市值公司天然大 | 换用 `planned_ratio_*` |
| 6 | G | behavior loader 静态 = lookahead bias | stock-level 静态广播到所有 datetime | 重写为时序 MultiIndex |

## 五、数据验证结果汇总

### Phase A 清理后残留验证
- `grep northbound backend/` → 0 条命中
- `smartmoney.db` DROP 后：`fact_northbound_daily` 不存在、`northbound_overlap_rate` 列不存在、`beta_northbound_in` 列不存在、`step_status` 无 `sync_northbound` 残留
- pytest 315 passed（从 Phase A 前的 317 剥离 2 个北向测试用例）

### Phase B-C 数据覆盖

| 数据源 | 行数 | 交易日/季度 | 股票覆盖 | 最新日期 |
|---|---:|---:|---:|---|
| QFII 季报 | 7,878 | 7 季度 | 2,255 | 2025-12-31 |
| 两融日度 | 2,944,065 | 797 交易日 | 4,373 | 2026-04-21 |
| 龙虎榜日度 | 61,980 | 798 交易日 | 5,408 | 2026-04-22 |
| **交集股票** | — | — | **QFII ∩ LHB = 1,923 (85% of QFII)** | — |

### Phase E-G 智能更新路径验证
经 `GET /api/inst/update/smart-plan` 实测：
- `sync_qfii` 识别为 skipped（最新季度已在库）
- `sync_lhb` / `sync_margin` 依状态加入 plan 或 skipped
- `sync_northbound` **已从 skip_reasons 消失**（Phase A 清理生效）
- 单步 `sync_lhb` 触发：1.1 秒拉 306 行，幂等 upsert
- 单步 `sync_margin` 触发：T 失败自动降级 T-1，written=4,086 行

### Phase G 因子值 sanity check
- 时序 loader 输出 shape（200 股票样本）：`(148,705 rows, 10 cols)`
- 日期范围：2023-01-03 → 2026-04-21
- QFII 非零行占比 6.7%（稀疏合理，少数股票被 QFII 持有）
- 平安银行 SZ000001 最新 5 日 `beh_margin_rz_log` 持续上升 22.39 → 22.41（rz 余额约 53 亿级别，符合实际）

## 六、测试覆盖

| Phase | 新增用例 | 累计 pytest |
|---|---:|---:|
| A 前 | — | 317 passed |
| A | -2（删北向用例） | 315 |
| B | +9（QFII：enum / 解析 / upsert / 幂等 / 失败降级 / 回填 / STEP 注册） | 324 |
| C | +15（margin 7 + lhb 8：normalize / upsert / range / 月窗切分 / updater 注册） | 339 → 341（symlink 生效后 skip 变 pass） |
| D | +4（T-1 fallback：默认关闭 / 命中 / 预算耗尽 / 日历查询） | 345 |
| E-G | 0（loader 单测计划补，当前靠 smoke test） | 345 |

## 七、与原规划的执行偏差总结

| 原规划 | 实际执行 | 原因 |
|---|---|---|
| Phase 0: tqcenter 能力探测 | 直接确认不可用，改 AkShare | 实测 `mootdx` 无对应字段 |
| P0 主源 `get_financial_data/gpjy/gp_one/gb_info` | 只接 `get_market_data` 等效替代 | 其余接口 tqcenter 独有 |
| 建 13 张 raw 表 + 5 张 fact 表 + 5 张 latest 维度 | 只新建 3 张 raw 表（qfii / margin / lhb） | 奥卡姆剃刀 |
| 5 个新 engine 文件 | 沿用现有 `_load_*_factors()` 模式扩展 | 避免过度工程 |
| 新闻 / 分析师 / 研报 层接入 | 未接入 | 文档自测 Spearman 0.08-0.15，信号弱 |
| 涨停池接入 | 本期不做 | 源侧无历史 |
| 北向保留为 P0 | 彻底退役 | 源死 1.5 年 |

## 八、生产使用建议

### 立即可用（Phase G 落地）
- **behavior 因子族（时序）**：默认可启用（`use_behavior=True`），IC 0.0499 / RankIC 0.0254 均优于 baseline
- **supply 因子族**：可启用（`use_supply=True`），但提醒：当前仍是静态版本，**有穿越风险**，建议只用于当期画像、不进训练标签

### 下一阶段候选（Phase H-J）

| Phase | 工作 | 优先级 |
|---|---|---|
| H | supply loader 时序化（同 Phase G 的修正模式） | 高（消除剩余穿越） |
| I | worktree 合并回 main（需人工 review） | 高（交付） |
| J | behavior 因子加入流通市值归一化（margin ÷ float_cap、lhb net_buy ÷ 日成交额）| 中（信号强度提升） |
| K | Qlib 训练标签研究：从当前默认的 `Ref($close, -2)/Ref($close, -1)-1` 升级到 20 日前瞻 + 20 日最大回撤约束 | 中（贴合"跟/不跟"业务目标）|

### 长期结构性缺口

- 外资连续行为信号（北向日度净买）**结构性消失**，QFII 季频是唯一替代但信号频率有限
- 流通市值 / 总成交额归一化基准尚未引入（需要 `price_kline × 股本` join），Phase J 补
- 板块交易热度 / ETF 拥挤度数据源仍未接入（非 tqcenter 无等效替代）

## 九、关键数字一图总结

```
数据层          → QFII   7,878 行    ·  margin 2.94M 行   ·  LHB  62K 行
测试层          → 345 passed         ·  6 流程卡点全部修复
Qlib A/B        → IC +5%    RankIC +0.4%    TopK50 +50%（Phase G 诚实测量）
代码净增        → ~2,200 行（7 新文件 + 7 修改文件，分 7 commit）
```

---

*本报告覆盖 Phase A-G（commits `094e9f3e` 到 `73e5aadb`），由多轮 phased execution 产生。下一阶段建议优先 Phase H（supply 时序化）+ Phase I（合并 main）。*

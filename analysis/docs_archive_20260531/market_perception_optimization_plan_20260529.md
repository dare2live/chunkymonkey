# 市场感知项目优化方案 — 2026-05-29

> 基于 2026-05-28 主升浪猎手研究 session 的经验和教训, 本文档列出市场感知项目的完整优化路径。核心结论: **当前感知项目的最大瓶颈是上游 dim_stock_tdx_industry_history 只有 9 天数据, 导致 theme/leader_follower/under_reaction/stock_context 全部只有 14 天历史, 无法跟 ML 联调验证 lift**。

---

## 目录

| § | 内容 |
|---|---|
| 1 | 数据完整性现状(从上游到下游) |
| 2 | 根 blocker 诊断: industry_history 历史缺失 |
| 3 | 项目宪法约束: 不允许 current_label_fallback 做历史 |
| 4 | 3 条优化路径(按权限/工期/价值) |
| 5 | 推荐路径 + 实施步骤 |
| 6 | 关联性探索任务(不需要写 DB) |
| 7 | 风险和依赖 |

---

## 1. 数据完整性现状(完整 layered 诊断)

### 数据层级关系

```
L0  基础 (dim 表)
  ├─ dim_trading_calendar(交易日历, 2023-01-03+, 缺 2022 的 243 天)
  ├─ dim_stock_tdx_industry(当前行业映射, 5617 行, 一次性 snapshot)
  └─ dim_stock_tdx_industry_history(历史行业, 仅 9 天! ← 根 blocker)
        ↓
L1  PIT 派生
  └─ mart_stock_industry_pit
      ├─ observed_snapshot(2026-04-25~2026-05-07, 6 天) ← 跟 L0 一致
      └─ current_label_fallback(1900-01-01, 1 行) ← 整段 fallback, 但宪法禁用
        ↓
L2  板块层
  └─ fact_sector_momentum_daily(2023-01-03 ~ 2026-05-26, 819 天) [OK] 完整
        ↓
L3  感知层(mart_market_perception_*)
  ├─ daily(regime, 373 天 / 2024-11+) 警告: 半覆盖
  ├─ emotion(814 天 / 2023-01+) [OK] 本 session backfill 完
  ├─ theme(14 天 / 2026-04-27+) [高危] 几乎全空
  ├─ leader_follower(14 天) [高危] 几乎全空
  ├─ under_reaction(14 天) [高危] 几乎全空
  └─ stock_context(14 天) [高危] 几乎全空
        ↓
L4  消费层
  ├─ paper_sim selector(regime_gate 占位但未实施)
  ├─ V14-V16 LightGBM ML 框架
  └─ BestChoice 公式
```

### 实测数据覆盖表

| 层 | 表 | 范围 | 唯一日期 | 状态 |
|---|---|---|---|---|
| L0 | dim_trading_calendar (is_trading=1) | 2023-01-03 ~ 2026-12-31 | **969** | 警告: 缺 2022 的 243 天 |
| L0 | dim_stock_tdx_industry | snapshot only | 5617 行 | [OK] 当前完整 |
| **L0.5** | **dim_stock_tdx_industry_history** | **2026-04-25 ~ 2026-05-26** | **9** | [高危][高危][高危] **根 blocker** |
| L1 | mart_stock_industry_pit observed_snapshot | 2026-04-25 ~ 2026-05-07 | 6 | [高危] L0.5 衍生 |
| L1 | mart_stock_industry_pit current_label_fallback | 1900-01-01 | 1 | [OK] 但宪法禁用 |
| L2 | fact_sector_momentum_daily | 2023-01-03 ~ 2026-05-26 | **819** | [OK] |
| L3 | mart_market_perception_daily(regime) | 2024-11-01 ~ 2026-05-19 | 373 | 警告: |
| L3 | mart_market_perception_emotion_daily | 2023-01-03 ~ 2026-05-19 | **814** | [OK] |
| L3 | mart_market_perception_theme_daily | 2026-04-27 ~ 2026-05-19 | 14 | [高危] |
| L3 | mart_market_perception_leader_follower_daily | 2026-04-27 ~ 2026-05-19 | 14 | [高危] |
| L3 | mart_market_perception_under_reaction_daily | 2026-04-27 ~ 2026-05-19 | 14 | [高危] |
| L3 | mart_market_perception_stock_context_daily | 2026-04-27 ~ 2026-05-19 | 14 | [高危] |

---

## 2. 根 blocker 诊断

### `dim_stock_tdx_industry_history` 只有 9 天历史

| 维度 | 现状 |
|---|---|
| 表名 | dim_stock_tdx_industry_history |
| 数据量 | 50,489 行 |
| 时间范围 | 2026-04-25 ~ 2026-05-26 |
| **唯一日期数** | **9** |
| 每日股票数 | ~5600 只 |
| 字段 | stock_code, snapshot_date, tdx_l1/l2/l3 + names, source_label, fetched_at, source_available_date |

### 衍生影响

| 下游表 | 影响 |
|---|---|
| mart_stock_industry_pit observed_snapshot | 6 天 (因为只有 9 天 history) |
| mart_market_perception_theme_daily | 14 天 (因为 observed_snapshot 不够) |
| mart_market_perception_leader_follower_daily | 14 天 (因为 theme 不够) |
| mart_market_perception_under_reaction_daily | 14 天 |
| mart_market_perception_stock_context_daily | 14 天 |
| **V14-V16 LightGBM**(perception 特征) | 只能用 emotion(100%) + daily(44%), theme/LF 无 |

### 为什么这是根 blocker?

theme_lifecycle_engine.py 的 `_validate_observed_pit_coverage` 函数(line 279-304):

```python
def _validate_observed_pit_coverage(conn, start_day: date, end_day: date) -> None:
    cfg = get_theme_config()
    # 查询: 在 [start, end] 间, 有多少 trading_day 被 mart_stock_industry_pit 的
    # observed_snapshot 区间覆盖
    rows = _fetchall(conn, sql_with_join_on_observed_snapshot)
    covered = int(rows[0]["covered_days"])
    expected = len(_trading_days(conn, start_day, end_day))
    if covered != expected:
        raise ValueError(f"observed PIT industry coverage incomplete: {covered}/{expected}")
```

引擎拒绝跑任何"observed_snapshot 覆盖不全"的区间, 这是 **PIT 严格红线**(防 leakage)。

---

## 3. 项目宪法约束: 不允许 current_label_fallback 做历史

[backend/config/market_perception.yaml:121](backend/config/market_perception.yaml) 明确规定:

```yaml
required_member_confidence:
  value: "observed_snapshot"
  evidence: "P3 PIT rule: only observed mart_stock_industry_pit intervals are eligible;
             current_label_fallback is excluded from historical theme lifecycle backfill."
```

### 为什么宪法禁止 current_label_fallback?

| 原因 | 解释 |
|---|---|
| **PIT leakage** | 用当前行业映射做 2023 年的 backfill = 用 2026 年知识 |
| A 股行业 reclassification 不频繁但存在 | 即使 1-2 次重分类也会影响主升浪识别(板块板块换了) |
| 真金白银项目 | 不允许任何 PIT 妥协 |

---

## 4. 3 条优化路径(权限/工期/价值)

### 路径 A — 最小代价: 用 emotion 100% + daily 部分 工程化 ML 框架

| 维度 | 内容 |
|---|---|
| 权限 | 不需要新 DB 写权限 |
| 工期 | 2-3 周 |
| 工作 | V14-V16 框架进 backend/services/zhushenglang/, 接 paper_sim |
| 期望 | V16 当前 70% 胜率(prob>0.6)接 paper_sim 真实 tx_cost + T+1 模拟 |
| 缺口 | theme/LF/under_reaction 仍空, 实战胜率可能从 70% 降到 60-65%(因为缺感知信号) |

### 路径 B — 中等代价: 补 dim_trading_calendar 2022, 跑 regime/daily 完整 backfill

| 维度 | 内容 |
|---|---|
| 权限 | 需要 DB 写授权(dim_trading_calendar INSERT 243 行) |
| 工期 | 0.5 天 |
| 工作 | 用 K 线推断 2022 trading days, INSERT INTO dim_trading_calendar |
| 期望 | mart_market_perception_daily(regime) 从 373 行 → ~ 800 行(覆盖 2023+) |
| 缺口 | theme/LF 仍空 |

### 路径 C — 高代价: backfill dim_stock_tdx_industry_history 历史 + 重建 PIT + 跑 theme/LF

| 维度 | 内容 |
|---|---|
| 权限 | 需要 DB 写授权 + 多张表写入 |
| 工期 | 3-5 天 |
| 数据源 | tdxhub block_zs/fg/gn 文件(看 services/block_client.py 是否能拉历史) |
| Fallback 方案 | 如果 tdxhub 历史拿不到, 用 akshare/miaoxiang 历史板块成分股 |
| 工作步骤 | <br>1. 调研 dim_stock_tdx_industry_history 的数据源 + 是否支持回溯 <br>2. 跑历史 industry 采集 backfill 2022-2026 ~ 5600 只 × 250 天/年 = 7M 行 <br>3. 重新跑 build_industry_pit.py 生成 observed_snapshot 全期 <br>4. 跑 theme/LF/under_reaction/stock_context backfill <br>5. V17 用完整 perception 数据 训练 LightGBM |
| 期望 | 胜率 70% → 75-82% (基于 V15 子样本观察) |
| 风险 | tdxhub 可能没有真实历史 industry, 必须用其他源(增加 PIT 不确定性) |

---

## 5. 推荐路径 + 实施步骤

### 优先级排序

| 优先级 | 路径 | 理由 |
|---|---|---|
| **P0** | 路径 B(补 trading_calendar 2022 + regime backfill) | 最小代价 + 立刻解锁 regime 数据 |
| P1 | 路径 A(emotion + 工程化进 paper_sim) | 不依赖新数据, 验证当前 ML 框架真实表现 |
| P2 | 路径 C(industry_history backfill) | 需要更深调研数据源, 工期长 |

### 路径 B 实施步骤(详细)

#### B.1 trading_calendar backfill

```python
# 用 K 线推断 2022 交易日, INSERT INTO dim_trading_calendar
import duckdb
con_m = duckdb.connect('data/market.duckdb', read_only=True)
days_2022 = con_m.execute("""
    SELECT DISTINCT date FROM v_price_kline_qfq
    WHERE freq='daily' AND date < '2023-01-03'
    ORDER BY date
""").df()
con_m.close()

con_s = duckdb.connect('data/smartmoney.duckdb', read_only=False)
for d in days_2022['date']:
    con_s.execute(
        'INSERT OR IGNORE INTO dim_trading_calendar (trade_date, is_trading) VALUES (?, 1)',
        [d]
    )
con_s.commit()
con_s.close()
# 预期: 新增 ~243 行 2022 交易日
```

#### B.2 跑 regime daily backfill

```bash
python3 backend/scripts/build_market_perception_daily.py \
    --start 2022-01-04 --end 2024-10-31
```

#### B.3 验证 + 触发 ML 重训

```bash
# 验证 mart_market_perception_daily 覆盖完整
python3 -c "import duckdb; con=duckdb.connect('data/smartmoney.duckdb', read_only=True); print(con.execute('SELECT MIN(snapshot_date), MAX(snapshot_date), COUNT(*) FROM mart_market_perception_daily').fetchone())"

# 用完整 regime 重新跑 V17 (V14 + 完整 perception)
python3 /tmp/v17_full_perception.py  # 需要写
```

### 路径 A 实施步骤(并行可做)

| 步骤 | 工作 | 工期 |
|---|---|---|
| 1 | V14 框架 prototype 迁移进 backend/services/zhushenglang/ | 2-3 天 |
| 2 | walk_forward 走项目中央层(services.optimization) | 1-2 天 |
| 3 | paper_sim selector 接 LightGBM prob 输出 | 1 天 |
| 4 | regime gate 接 hs300_60d_ret(用现有 daily 部分覆盖) | 0.5 天 |
| 5 | paper_sim 1 月 forward 真实 tx_cost + T+1 模拟 | 1 周 |
| 6 | 写 audit + delivery_readiness | 1 天 |

### 路径 C 实施步骤(需要前置调研)

| 步骤 | 工作 | 工期 |
|---|---|---|
| 1 | 调研 services/block_client.py / tdx_industry_client.py 是否能拉历史 industry | 0.5 天 |
| 2 | 如果 tdxhub 不行, 调研 akshare ak.stock_board_industry_hist_em 等 | 0.5 天 |
| 3 | 写历史 industry 采集脚本(可能涉及 GCP controlled use, 数据量大) | 1 天 |
| 4 | 跑历史采集 2022-2026 | 0.5-1 天 |
| 5 | 写入 dim_stock_tdx_industry_history | 0.5 天 |
| 6 | 跑 build_industry_pit.py 生成 observed_snapshot | 0.5 天 |
| 7 | 跑 theme/LF/under_reaction/stock_context backfill | 0.5 天 |
| 8 | V17 LightGBM 用完整 perception 训练 + walk_forward 5 fold | 0.5 天 |

---

## 6. 关联性探索任务(不需要写 DB, 可立刻做)

利用本 session backfill 完成的 emotion 100% 覆盖, 在 V12 OOS 样本上探索:

### 6.1 emotion_state 分组的 V12 胜率

```sql
-- 在 587 个 V12 OOS case 上, 按 emotion_state 分组看胜率
SELECT
    em.emotion_state,
    COUNT(*) as n_cases,
    AVG(target) as win_rate,
    AVG(final_ret) as mean_ret
FROM v12_oos_preds p
JOIN mart_market_perception_emotion_daily em
  ON p.breakout_date = em.snapshot_date
GROUP BY em.emotion_state
```

预期:
- 赚钱效应扩张: 胜率较高(~75%+)
- 亏钱效应/杀跌: 胜率较低(~50%)
- 验证 emotion 真的能加 regime gate 价值

### 6.2 V12 prob × emotion_state 交叉

| emotion_state | prob>0.6 | prob<0.4 |
|---|---|---|
| 赚钱效应扩张 | ? | ? |
| 中性 | ? | ? |
| 亏钱效应 | ? | ? |
| 杀跌 | ? | ? |

### 6.3 hs300_60d_ret 数值分桶的 V12 胜率

```sql
WITH bucket AS (
    SELECT *, CASE
        WHEN hs300_60d_ret > 15 THEN '强牛'
        WHEN hs300_60d_ret > 5 THEN '牛'
        WHEN hs300_60d_ret > -5 THEN '震荡'
        WHEN hs300_60d_ret > -15 THEN '弱'
        ELSE '熊'
    END AS regime_bucket
    FROM joined_data
)
SELECT regime_bucket, COUNT(*), AVG(target), AVG(final_ret)
FROM bucket GROUP BY regime_bucket
```

### 6.4 action_bias 分组

action_bias 取值:
- 追强有效(top 10 active rate)
- 防御为主
- 试错

每种状态下 V12 ML lift 多少?

### 6.5 leader_follower_diffusion_buy 信号(虽然 LF 仅 14 天)

bc_absorbed 包里的 `leader_follower_diffusion_buy` 信号能否在 14 天 LF 数据上验证 +X pp 胜率?

---

## 7. 风险和依赖

### 数据风险

| 风险 | 严重性 | 缓解 |
|---|---|---|
| tdxhub 没有历史 industry 数据 | [高危] 高 | 用 akshare/miaoxiang fallback, 但增加 PIT 不确定性 |
| akshare 历史 industry 接口可能也只能拿当前快照 | 中 | 调研多源, miaoxiang / wind / choice |
| 大规模 backfill 影响 production DB 性能 | 中 | 在 GCP 跑 + 回传, 不动 local production |

### 工程风险

| 风险 | 缓解 |
|---|---|
| dim_stock_tdx_industry_history 写入 7M 行 | 分批写, 每批 100K, 用 INSERT OR REPLACE |
| build_industry_pit.py 跑 2022-2026 可能耗时 | 试 1 个月 sample 看耗时 |
| theme/LF engine 改 SQL 可能漏 PIT | 严格 audit_pit_integrity.py 跑 |

### 决策依赖(需要用户授权)

| 决策 | 谁可决定 |
|---|---|
| 是否授权 DB 写 dim_trading_calendar / mart 表 backfill | 用户 |
| 路径 A/B/C 优先级 | 用户 |
| GCP 是否启用做历史 industry 采集 | 用户(需 controlled use 授权) |
| 是否动 backend/config/market_perception.yaml 改 confidence_level | 用户(项目宪法层) |

---

## 8. [高危] 关键诊断 — industry history 不可 backfill!

### 调研结果(2026-05-29)

| 调研项 | 结论 |
|---|---|
| `tdx_industry_client.py` 数据源 | TDX 协议拉 **当前** `tdxhy.cfg` 文件 |
| 每次同步行为 | snapshot_date = today, 写入 dim_stock_tdx_industry_history |
| **TDX 是否提供历史 industry 重分类 API** | **[NG] 不提供** |
| 当前 9 天历史是怎么来的 | 最近 9 天每天跑一次 sync 自然积累 |
| 设计意图 | **forward-only 积累**, 不支持 backward fill |

### 含义(对感知项目优化)

**短期(0-3 月)**:
- emotion 100% 是当前感知数据上限
- theme/LF/under_reaction/stock_context 不可能历史 backfill
- 必须接受现状

**中期(3-12 月)**:
- 每天定时跑 tdx_industry_client 自然积累 history
- 12 个月后有 ~250 天 history
- 但仍不够 2022-2026 完整覆盖

**长期(12 月+)**:
- 累积到 18-24 个月 history 后再考虑 theme/LF backfill
- 或者改项目宪法允许 current_label_fallback 作 historical proxy(项目层决策)

### 实测 ML lift (本 session 完成 V17)

| 模型 | AUC | 平均胜率 | std | top 5% mean_ret |
|---|---|---|---|---|
| V14 base 41 维 | 0.617 | 60.1% | 13.8 | — |
| V16 + emotion + mp 单一阶 61 维 | 0.606 | 62.0% | 13.3 | — |
| **V17 + 5 个 interaction features 66 维** | **0.625** | **63.0%** | **11.9** | **+20.41%** |

V17 全 OOS 跨阈值:
- prob > 0.7: 73.7% (vs V12 71.9%, +1.8pp)
- prob > 0.75: 75.6% (vs V12 73.4%, +2.2pp)
- top 5%: 82.8%, mean_ret **+20.41%** (vs V12 +15.15%, **+5.3pp 长尾增强**)
- top 10%: 81.0%

### 修正后的优化路径

| 路径 | 状态 |
|---|---|
| ~~路径 C(historical industry backfill)~~ | [高危] **数据源不存在, 不可执行** |
| 路径 B(trading_calendar + regime daily backfill) | 警告: 需 DB 写授权, 可执行但 lift 估计 +1-2pp |
| **路径 A(用 emotion 100% + V17 interaction + 工程化进 paper_sim)** | [OK] **当前唯一真正可推进路径** |
| **路径 D(forward 积累 industry history, 长期等待)** | 警告: **必须从今天开始定时跑**, 12 个月后才有用 |

### 推荐顺序(修正)

1. **立即(无需新授权)**: V17 完成 + 关联探索文档(已做)
2. **短期 P0(需要 DB 写授权)**: 路径 B(trading_calendar 2022 + regime daily backfill, 0.5 天)
3. **中期 P1(需要代码改动授权)**: 路径 A(V17 工程化进 paper_sim, 2-3 周)
4. **长期 P2(自动化)**: 路径 D(添加 industry sync 到 daily_update.sh, 每天积累)
5. **[禁] 不做**: 路径 C(historical industry backfill - 数据源不存在)

---

## 9. 立即可执行的非侵入式行动(无需新授权)

| 行动 | 内容 | 已完成? |
|---|---|---|
| 1. emotion 100% backfill | mart_market_perception_emotion_daily 2023-01 ~ 2026-05 (814 行) | [OK] 完成 |
| 2. V17 ML 重训 | V14 + emotion + interaction features, AUC 0.625 | [OK] 完成 |
| 3. 关联探索分析 | emotion_state × prob 交叉, action_bias 跨年验证 | [OK] 完成 |
| 4. 完整 blocker 诊断 | 数据完整性 + tdx_industry 数据源限制 | [OK] 完成 |
| 5. 优化方案文档 | 本文档 | [OK] 完成 |

## 10. 需要授权的下一步

| 决策 | 谁可批准 | 期望价值 |
|---|---|---|
| trading_calendar 2022 backfill(DB 写) | 用户 | 解锁 regime daily backfill |
| V17 工程化进 backend/services/zhushenglang/ | 用户 | 工程化 ML 框架 |
| daily_update.sh 加 tdx_industry sync | 用户 | 长期积累 industry history |
| 项目宪法 §市场感知 重新讨论 current_label_fallback | 用户 + Codex review | 短期 theme/LF 可用(代价: 轻微 PIT proxy) |

---

## 11.5 板块脉动/轮动回测验证(2026-05-29 新增)

> 用户问: "市场感知能否准确抓住市场脉动(养殖强时识别龙头) + 预测板块轮动?"
> 用 fact_sector_momentum_daily(L1 13 板块, 819 天) + 自建 L2(56 板块) 完整回测。

### 验证结论: 板块层面无稳定可预测 alpha

| 验证 | 结果 |
|---|---|
| **L1 板块动量 IC** | -0.017 (ICIR -0.04), 几乎无预测力 |
| **L2 板块动量 IC** | **-0.069 (ICIR -0.25)**, 显著均值回归(强转弱) |
| L2 强板块 forward_20d | -0.02% (vs 弱板块 +1.12%, 价差 -1.14%) |
| 月度反转策略(买最弱 bottom5) | 年化 +9.0%, **跑不赢全板块等权 +11.9%** |
| 月度动量策略(买最强 top5) | 年化 +9.5%, 也跑不赢基准 |
| 反转跨年稳定性 | 2023 -2.08% / 2024 +2.31% / 2025 +3.05% / 2026 -1.12%(极不稳) |

**核心结论**:
1. **板块轮动(L1/L2 行业层面)是伪命题** — 动量 IC≈0, 反转不稳, 择时跑不赢等权 beta
2. **全板块 beta 才是 A 股板块层面的主收益来源**, 任何择时都是负贡献

### 养殖(农林牧渔)case

| 能力 | 结果 |
|---|---|
| 强弱时序描述(事后) | [OK] 能 — 2024-06 rank 55/56(猪周期谷), 2025-04 rank 3/56(最强) |
| 提前识别(事前) | [NG] ret_20d 是 20 天回看, 滞后 |
| 龙头识别 | [NG] leader_follower 仅 14 天 + L2 成分混杂(平潭发展[海洋经济]混入农林牧渔 96 只成分) |

### 市场感知价值重新定位

| 感知功能 | 回测结论 | alpha? |
|---|---|---|
| 板块轮动预测(sector_momentum/theme) | L1/L2 动量反转都无稳定 alpha | [NG] |
| 板块龙头识别(leader_follower) | 数据 14 天 + 成分混杂 | [?] 无法验证 |
| **大盘 regime/emotion 择时** | V12 fold 诊断验证, 熊市暂停有效 | [OK] |
| **个股主升浪 ML(V12-V17)** | prob>0.7 胜率 73.7% | [OK] 真 alpha |

**修正方向**: 市场感知的价值在 **regime 大盘择时 + 个股 ML 选择**, 不在 L1/L2 板块轮动。真正的轮动在更细的"概念主题"层(猪周期/AI/机器人), 需要概念成分数据(项目当前缺)。

---

## 11. 一句话总结(更新)

调研发现 **dim_stock_tdx_industry_history 不可 backfill — TDX 协议只提供 current snapshot, 不提供历史**, 所以原"路径 C: historical industry backfill" 不可执行。**当前阶段感知项目优化的真正路径**:
1. **本 session 已完成**: emotion 100% backfill + V17 ML(平均胜率 63%, top 5% mean_ret +20.41%, AUC 0.625)
2. **立刻该做**: 把 tdx_industry sync 加进 daily_update.sh 开始 forward 积累 history
3. **短期 P0**: trading_calendar 2022 + regime daily backfill(需 DB 写授权)
4. **中期 P1**: V17 工程化进 paper_sim production
5. **长期**: 12-18 月后自然累积 industry history, 再考虑 theme/LF backfill

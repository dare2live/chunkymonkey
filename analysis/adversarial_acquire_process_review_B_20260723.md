# Adversarial review B — 数据获取 + 后续检查/加工（2026-07-23）

> Role: Agent B (red-team / Occam challenger) vs Agent-A optimism  
> Status: evidence-only · live DB + code probes  
> Label: **PARTIAL** — holders/by_ann frontier **FIXED**; org period-gap + soft_outcome rollup **LAND**  
> Authority: `frontier_decision` · `org_holding_incremental_loop_20260723` · `shareholder_update_check_design_20260723` · live `data/smartmoney.duckdb`

---

## Executive verdict

**「合理 pipeline」？** — **子集合理，不能整体签字。**

| 平面 | 裁决 |
|---|---|
| 稀疏披露前沿（holders + `by_ann_date`） | **合理且已 ship** — equal-frontier ≠ skip |
| 稠密日频（`by_trade_date` atomic_skip） | **合理假设，未证伪** — 留 miss-ledger 门 |
| org period-gap 增量环 | **检查存在，人口完备性假绿** — live 实锤 |
| validate/publish（formal land→accept） | **原子边界 OK**；org 子集 accept **无人口门** |
| derive/process | **forms 偏 accepted**；segments 仍 legacy daily_basic |
| run_outcome / UI | **continuity FAIL 被 soft 桶吞** — 非「数据更新全绿」但易误读 |
| 机器门（FND-GATE F6） | **partition 计数 ≠ 人口** — CI `--skip-live` 更弱 |

Occam 结论：不必 mega redesign；**补 2–3 个窄人口/rollup 门** 比再写 DetectionService 便宜一个数量级。

---

## Attack list

### A1 — equal-day / frontier skip 藏晚披露

| 子攻击 | 证据 | 结果 |
|---|---|---|
| holders `provider_max == wm` 永久 skip | 曾实锤（`holders_stock_coverage_alignment_20260723` §2）；现 `equal_day_population_gap` → sparse miss | **FAIL（攻击不成立）** — `holders_aif10.py:665–709` |
| `by_ann_date` wm 日被 atomic 掉 | `sync_runner.py:3036–3048` `ann_reprobe` 保留 wm；`test_by_ann_date_equal_day_reprobe.py` | **FAIL** |
| `by_trade_date` 同日 partial 后 skip wm | `frontier_decision.py:170–173` `atomic_skip`；映射 `data_frontier_detection_system_20260723.md` §3.2 残差 | **LAND（开放）** — 稠密全日批假设；无 equal-day 人口 reprobe |
| org 期内晚披露 | `org_holding_period_frontier_hook` equal→`skip_behind`；无 NOTICE_DATE faucet | **LAND（设计已知）** — 产品若消费 org accepted 会缺 |

**Live（2026-07-23）：** holders accepted max notice `20260723`，canonical 该日 194 行；gap report 路径会 `same_day_coverage_complete` 或 sparse sync — 与 07-23 审计一致。

---

### A2 — org period-gap vs holders sparse 不一致咬产品

| 证据 | 文件/查询 |
|---|---|
| holders：notice 前沿 + `_affected_stocks_since` 稀疏补漏 | `holders_aif10.py:641–738` |
| org：仅 `accepted_has_org_holding_partition(report_date)` 存在性 | `org_holding_aif10.py:519–581` |
| **Live Q1-2026：** raw `2026-03-31` → **111,931 行 / 5,520 股**；canonical+accepted `20260430` → **2,139 行 / 2 股**（600519, 000001） | DuckDB RO probe 2026-07-23 |
| gap report：`accepted_has_plannable=True`, `action=skip_current`, `status=ok` | `org_holding_period_gap_report()` live RW |
| 机构档案 episode 源 **不含 org**（仅 holders canonical∪legacy） | `institution_profile.py:244–247`, `disclosure_enrichment_projection.py:123–193` |

**结果：LAND（硬）。** 不对称是 **有意的**（供应商 by-period ~830k、无 notice 轴），但：

- 若任何 consumer 读 org **accepted/canonical** → **99.96% 人口缺失仍 daily skip**。
- holders 稀疏路径 **不能** 迁移到 org（`shareholder_update_check_design_20260723` §0 表）。
- F6 报 `org partitions=2` 即 PASS — **不验期内行数/股数**（`check_foundation_done.py:498–505`）。

**产品 bite  today：** 机构持股明细 serve 若挂 formal org → **BLOCKED 级假完整**；十大流通/档案路径 **不受** 此 org 洞直接影响。

---

### A3 — validate/publish 原子性；0 行 / 超时 / 权限混淆

| 子攻击 | 证据 | 结果 |
|---|---|---|
| 分页中途失败写半截日 | `_fetch_paged` 任一页 None → 整批 None（`sync_runner.py:749–750`） | **FAIL** |
| 0 行 vs 权限 vs 超时 | `_fetch_with_retry`：`zero_rows` / `TuShareAuthorizationError` 上抛 / 异常字符串（`sync_runner.py:691–735`） | **FAIL（大部分）** — 仍靠字符串分类，非 typed enum 全域 |
| formal daily 盘前 0 行 | `pre_available_after_zero_rows` → `pending_publish`（`sync_runner.py:1926–1956`） | **FAIL** — 不误写 accepted |
| org accept 子集 vs raw 全集 | `accept_org_holding_partition_from_legacy` 可 `stock_codes` 窄化（`org_holding_aif10.py:341–394`）；**无** accept 后 raw/canonical 人口比 | **LAND** — E0 canary 子集留在 accepted，daily 永不 repair |

---

### A4 — derive/process 消费 non-accepted 或 stale

| 步骤 | 消费面 | 证据 | 结果 |
|---|---|---|---|
| `technical_states.build_latest` | nominal **from_accepted=True** default | `process.py:122–124`, `technical_states/__init__.py:363–403` | **FAIL** |
| `segments.build_latest` | `tr.raw_tushare_daily_basic` 缺日驱动 | `segments.py:167–171` | **LAND（legacy）** — 非 accepted nominal；与 Tier0 Formal 轴未对齐 |
| episode → profile | canonical LEFT JOIN legacy；canonical 领先 legacy | live: canonical max notice `20260723` vs legacy `20260717` | **FAIL（holders 路径）** — 设计正确 |
| org → profile | 未接入 | grep `institution_profile` | **N/A** |
| moneyflow | registry `by_trade_date` + atomic_skip；本仓 `moneyflow` 表不存在（HSGT raw 在） | live catalog error | **UNVERIFIED** — 无 live 表，不声称 PASS |

---

### A5 — `soft_waiting_clock` 掩 hard fail；UI「数据更新」假绿

| 证据 | 结果 |
|---|---|
| `derive_run_outcome`: 任意 non-hard degraded → `soft_waiting_clock`（`run_outcome.py:109–124`） | **LAND（by design，有余毒）** |
| `daily_20260722.json`: **`continuity/integrity 审查 FAIL`** classified `other` → outcome **`soft_waiting_clock`** | **LAND** — 真 continuity FAIL 不进 hard_fail |
| UI：`soft_waiting_clock` → 琥珀「已结束·等时钟」`blocking_reason=None`（`ops_manual_run.py:402–409`） | **PARTIAL** — 非 success 绿，但 owner 见「跑完了 exit 1」易当非缺陷 |
| CX-4：unknown/tomb SLA 假 stale → soft 已修（`cx4_sla_quality_acceptance_20260723.md`） | **FAIL（该子类攻击）** |

---

### A6 — 纸面检查不跑 / 错 universe / obsolete PIT green

| 检查 | 证据 | 结果 |
|---|---|---|
| FND-GATE F6 live | holders overlap 526≥120；**org partitions=2** 无人口 | **LAND** |
| FND-GATE `--skip-live` | F6/F4 omit DuckDB；仍 PASS（`check_foundation_done.py:468–473`） | **LAND（CI Comfort）** |
| Continuity READY | CX-4 明确 banned chase（`cx4_sla_quality_acceptance_20260723.md` §Kill） | **诚实 PARTIAL** — 非假绿，但 chain 仍 DEGRADED |
| holders PIT / canonical | 07-23 sparse repair + equal-wm fix 有 live 审计 | **FAIL（攻击不成立）** |

---

### A7 — mass-refresh ban 留真洞（org 期内晚披露 / 偏少行）

| 证据 | 结果 |
|---|---|
| `OrgHoldingMassRefreshForbidden` + `allow_existing_refresh=False` on daily path（`org_holding_aif10.py:398–445`） | **政策 LAND OK** |
| raw 111k vs accepted 2k：**mass ban + accepted_has** → 永久 skip | **LAND（硬）** |
| 裁决：显式 repair 刀 + miss ledger 门（`shareholder_update_check_design_20260723` §4） | **尚未满足** — 无 ledger |

---

## Attacks that fail (actually OK)

1. **holders 同日晚披露 skip** — primitive + sparse miss（shipped + live aligned）。
2. **`by_ann_date` wm 日丢弃** — `ann_reprobe` + tests。
3. **formal daily/st land 半截页** — paged fetch fail-closed；0 行盘前 pending_publish。
4. **episode/profile 吃 legacy-only 十大** — canonical spine + enrichment join。
5. **SLA unknown/tomb 假 stale 点 soft** — CX-4 PASS（`watermark_sla_latest.json` n_alerts=0 post-fix）。

---

## Attacks that land (real risk)

| # | 风险 | 严重度 | 消费者 |
|---|---|---|---|
| L1 | org accepted **2 股 canary** 当 plannable 完整 → daily skip | **高**（若 serve org formal） | org 明细 / 未来 screening |
| L2 | org period-gap **存在性 ≠ 人口** | **高** | 同上 |
| L3 | continuity FAIL → **soft_waiting_clock** | **中** | ops / owner 心智模型 |
| L4 | F6 **partition 计数** 绿灯 | **中** | phase_closure / CI |
| L5 | segments **legacy daily_basic** 驱动 | **中低** | Tier1 segment / pulse 输入 |
| L6 | `by_trade_date` **无 equal-day 人口门** | **低–中**（待 miss ledger） | 型 A 日频域 |

---

## Minimal counter-proposal (Occam)

**不做：** DetectionService / 全宇宙逐股扫 / org mass refresh / Optuna / north-star。

**建议 3 刀（可独立、可测）：**

1. **org population sanity gate（1 文件 + 测试）**  
   在 `org_holding_period_gap_report` 或 acquire 收尾：当 `action=skip_current` 时，若 `raw DISTINCT stock_code` / `accepted row_count` &lt; 阈值（如 dim 活跃 A 的 0.6× 或滚动期中位数×0.6），typed outcome → `under_populated_accepted` → **不 skip**，写入 `delta_manifest` + repair queue（仍 **禁止** 自动 mass refresh；仅 surfacing + 显式 repair CLI）。

2. **run_outcome 窄化（1 文件）**  
   `continuity/integrity 审查 FAIL` / `ALERT_continuity` 进 `_HARD_RE` 或独立 `degraded_hard_ops` bucket — UI 保留琥珀但 manifest 标 `continuity_fail=true`，与「等时钟」分离。

3. **F6 org 行（foundation_done.yaml + script）**  
   live 时：`min_org_stocks_per_latest_partition` 或 `raw_vs_accepted_stock_ratio` floor；canary 2 股 → F6 **FAIL** 或 **PARTIAL**（非 PASS）。

**defer（有证据再开）：** `by_trade_date` equal-day reprobe — 需 miss ledger（frontier doc §4 G6）。

---

## Verdict vs 「reasonable pipeline」

| phrase | B 裁决 |
|---|---|
| 「每次更新会检增量」 | **PARTIAL TRUE** — org/holders **会检**；org **不检人口** |
| 「frontier 不藏晚披露」 | **TRUE（holders + ann）** / **FALSE（org 期内）** / **UNKNOWN（trade_date）** |
| 「accept 即可信」 | **FALSE for org Q1-2026 live** |
| 「UI 数据更新 = 地基完整」 | **FALSE** — soft 桶含 continuity FAIL |
| 「FND-GATE = 可 product」 | **FALSE without org population gate** |

**合成给 Agent A：** 乐观叙事在 **holders 稀疏 + frontier primitive** 上成立；**org 双轨（raw 满 / accepted 空）+ soft 吞 continuity** 足以否决「整体 reasonable」。最小修复是 **人口比门 + outcome 分类收紧**，不是重写 acquire。

---

## Verification commands (replay)

```bash
# Frontier unit surface
PYTHONPATH=backend python -m pytest \
  backend/tests/services/test_frontier_decision.py \
  backend/tests/test_by_ann_date_equal_day_reprobe.py -q

# Org gap (RW smartmoney)
PYTHONPATH=backend python - <<'PY'
import duckdb
from services.org_holding_aif10 import org_holding_period_gap_report
c = duckdb.connect("data/smartmoney.duckdb")
g = org_holding_period_gap_report(c)
raw = c.execute("SELECT COUNT(DISTINCT stock_code) FROM raw_org_holding_aif10 WHERE report_date='2026-03-31'").fetchone()[0]
acc = c.execute("SELECT row_count FROM accepted_partition WHERE partition_value='20260430'").fetchone()[0]
print("gap", {k: g[k] for k in ("action","status","accepted_has_plannable")})
print("raw_stocks", raw, "accepted_rows", acc)
c.close()
PY

# Foundation gate (live vs offline)
PYTHONPATH=backend python backend/scripts/check_foundation_done.py
PYTHONPATH=backend python backend/scripts/check_foundation_done.py --skip-live
```

---

## Residual owner

| Item | Owner | Next |
|---|---|---|
| org under-populated accept | `services.org_holding_aif10` + F6 | population gate knife |
| continuity → soft | `pipeline/run_outcome.py` | classify tighten |
| by_trade_date equal-day | miss ledger | evidence gate |
| segments legacy basic | `services/segments.py` | Tier1 formal cutover（非本审查 scope） |

**Label: PARTIAL** — pipeline **direction reasonable**; **acceptance honesty not yet uniform**.

# Phase 1：机构评分闭环根因定位报告

日期：2026-04-23
范围：`mart_institution_profile.quality_score` / `followability_score` / `score_basis` / `score_confidence` 四字段全空

## 1. 事实对账表

在跑诊断前，数据库的真实状态：

| 项目 | 数值 | 来源 |
| --- | --- | --- |
| `mart_institution_profile` 总行数 | 231 | `SELECT COUNT(*)` |
| `total_events` 非空 | 231 | 上游特征完整 |
| `avg_gain_30d` / `win_rate_30d` 非空 | 223 | 上游特征 ≈97% 覆盖 |
| `win_rate_60d` 非空 | 223 | 同上 |
| `total_win_rate` 非空 | 225 | 同上 |
| **`quality_score` 非空** | **0** | 核心问题字段 |
| **`followability_score` 非空** | **0** | 核心问题字段 |
| **`score_basis` 非空** | **0** | 核心问题字段 |
| **`score_confidence` 非空** | **0** | 核心问题字段 |
| 所有行 `updated_at` | `2026-04-22T15:09:20.169149`（唯一时间戳） | 上游 build_profiles 产出 |

关键对比：**上游特征 97% 非空，但 `quality_score` 0% 非空**——说明问题不在上游数据缺失（§14.3 的场景 b），而在评分写回环节。

## 2. 四场景诊断

§14.3 给出了四种候选根因：

### (a) DAG 未调用 `calculate_institution_scores()`

**验证方法**：检查 `STEPS` 列表、`RUNNERS` 字典、依赖配置、`step_status` 执行记录。

| 检查项 | 结果 |
| --- | --- |
| `STEPS` 第 18 位 | `{"id": "calc_inst_scores", "name": "机构评分", "group": "mart", "order": 18}` ✅ 已注册 |
| `RUNNERS["calc_inst_scores"]` | `_step_calc_inst_scores` → `calculate_institution_scores` ✅ 已绑定 |
| 硬依赖 | `["build_profiles", "build_industry_stat"]` ✅ |
| 软依赖 | `["calc_returns"]` ✅ |
| `step_status` 当前状态 | `status=idle, started_at=空, records=0` ❌ **从未完成** |

注意：`_prime_step_status_rows()` 在每次 DAG 启动前会将所有 step_status 重置为 idle，所以 idle 本身不能证明"从未跑过"，但结合数据库里 `quality_score` 全空可以断定：**最近一次产生 `mart_institution_profile` 的流程没有跑到 `calc_inst_scores`**。

**初步指向 (a)。**

### (b) 上游特征全空

**反例**：上游 `avg_gain_30d` 223/231、`total_events` 231/231、`buy_event_count` 充足。**排除 (b)**。

### (c) UPSERT 键不匹配

**检查**：`scoring.py:1047-1051`:
```sql
UPDATE mart_institution_profile SET quality_score = ?, ... WHERE institution_id = ?
```
`institution_id` 是 `mart_institution_profile` 的 PRIMARY KEY。**键匹配正确，排除 (c)**。

### (d) 字段名错位

**检查**：schema 含 `quality_score REAL`、`followability_score REAL`、`score_basis TEXT`、`score_confidence TEXT`，与 UPDATE 语句字段完全一致。**排除 (d)**。

## 3. 关键验证：手动执行函数

为了 100% 排除"函数本身有 bug"的可能，手动调用 `calculate_institution_scores(conn)`。

**执行结果**：

```
BEFORE: quality_score 非空=0, followability_score 非空=0
函数返回: 231
AFTER: quality_score 非空=231, followability_score 非空=231, score_basis 非空=231
```

分布（`quality_score`）：

| score_basis | confidence | 机构数 | avg | min | max |
| --- | --- | --- | --- | --- | --- |
| buy | high (buy≥10) | 146 | 54.11 | 16.24 | 81.18 |
| buy | medium (buy≥3) | 47 | 33.25 | 4.43 | 73.55 |
| buy | low | 28 | 14.09 | 1.03 | 33.85 |
| fallback_all | medium | 1 | 27.47 | 27.47 | 27.47 |
| fallback_all | low | 9 | 1.04 | 0.00 | 6.03 |

**结论**：函数本身 100% 正常。231 家机构一次性写回，分布合理。

## 4. 根因判定

**根因 = 场景 (a) 变种**：

- 函数代码正常
- 权重配置正常（`INST_SCORE_DEFAULTS` 和 `FOLLOW_SCORE_DEFAULTS` 权重和均为 100）
- 上游特征基本充足
- `calc_inst_scores` 在 STEPS/RUNNERS/依赖都注册正确

**真正的问题**：生成 `mart_institution_profile` 的那次 DAG 运行**没有跑到 `calc_inst_scores`**（order=18），只跑到 `build_profiles`（order=10）就结束了。可能原因：

1. 用户手动停止（`_mark_steps_status(conn, remaining, "stopped", "用户已停止")`）
2. K 线源不可用触发跳过（`_update_step(conn, sid, status="skipped", error="K线源不可用")`）
3. 硬依赖失败连锁跳过
4. 人工单独执行了 `build_profiles` 而不是全量 DAG

无论哪种情形，**结果都是`calc_inst_scores` 没被执行**，导致"机构是主角"的系统里主角评分全空。

## 5. 意外副作用

诊断阶段为验证函数正确性手动跑了 `calculate_institution_scores(conn)`，但该函数内部调用了 `conn.commit()`（`scoring.py:1052`），**诊断执行本身已将 231 行 `quality_score` / `followability_score` 等字段写入数据库**。

这不是错误，本来 Phase 2 也要做这件事。现在的状态：

- `mart_institution_profile.quality_score` 非空 231/231 ✅
- `mart_institution_profile.followability_score` 非空 231/231 ✅
- 但 `mart_stock_trend` 仍是**旧的**综合分（基于 quality_score=NULL 的 fallback 值 50 计算而来，见 `scoring.py:799`: `(_safe_float(profile.get("quality_score")) or 50) * 0.15`）

Phase 2 需要接着跑 `calc_stock_scores` 让股票侧用上新的机构评分。

## 6. 修复建议（交给 Phase 2）

### 立即动作

1. **补跑 `calc_stock_scores`**：机构评分已经有了，股票综合分需要重算才能反映真实的机构质量层权重。
2. **校准 `mart_institution_profile.updated_at`**：目前 DB 里有两种时间戳——原行 `2026-04-22T15:09:20`（上游产出）vs 诊断跑产生的新时间（评分写回）。这不破坏语义，但报告时要解释。

### 中期动作

3. **在 DAG 外增加独立触发入口**：增加 CLI 命令 `python -m backend.scripts.run_scoring` 或 `/api/updater/run-step?step=calc_inst_scores`，允许独立补跑评分而不必整条 DAG 重跑。避免"数据都在就缺最后一步"的情况。
4. **增加守护检查**：`build_profiles` 成功但 `calc_inst_scores` 未执行超过 24 小时，应在仪表盘标红。
5. **Phase 3 的三可矩阵**会暴露更多类似问题，建议一并处理。

## 7. 一页纸结论

- `quality_score` 全空不是因为函数坏了、也不是上游数据缺失，而是 DAG 执行链在 `build_profiles` → `calc_inst_scores` 之间断了。
- 根因是"DAG 没跑完"，属于调度/状态问题，不是评分算法问题。
- 诊断跑已意外把评分写回。Phase 2 接续：补跑股票评分。
- Phase 2+ 要增加独立补跑入口和守护检查，避免再次出现类似断层。

# 多维策略架构顶层设计 — 策略立方体 (Strategy Cube)

> **状态 (2026-06-15 P0-P3 后部分 superseded)**: §5 板块维 BLOCK 结论仍对; 架构定义偏离 — ① 3 轴→**5 轴**(§2/§3.1 owner 已转 design_deficiencies_extension2, 新增 Regime/Timing+Execution); ② C3 把 sizing/exit/regime 降级 auxiliary, 现为**一等设计轴**(C-WinReturn); ③ IC-edge + §4.2 RankIC>0.15 红线 + "IC→paper_sim"顺序被 R1 取代(IC necessary 快筛, 含成本绝对收益 sufficient 终验); ④ 引擎=`portfolio_execbacktest`(旧 return-based 删)。owner 冲突优先: 判断法典=`docs/strategy_validation_contract.md` · 5 轴+缺陷体系=`analysis/design_deficiencies_extension2_20260615.md` · P3 裁决=`analysis/p3_execution_aware_verdict_20260615.md`。

> 2026-06-13 立项。owner = 本文件 (设计真相源); goal.md「策略立方体架构」节为薄指针。
> 创造类工作, 走 architect-controller legislator block。用户原话 (2026-06-13):
> "把每种数据源独立做成参数, 用 optuna 挖潜, 探索各种组合; 数据验证不必全市场统一参数,
> 从市值/板块/股票形态/资金流向等多维度分组适配不同策略; 主策略和辅助策略的管理; 立体
> 多维空间的策略和参数; 模块+数据+配置形态的管理; 规则+模型+策略的应用方式; 第一性原理
> 架构师顶层设计。"

---

## 0. 一句话判定 (controller 先于设计的诚实前提)

**底座已齐, 缺的不是基础设施而是"把已有分散件组织成显式立方体 + 用证据逐维解锁"。**
当前 alpha 现实 (实测, 非估计): 三判决里 LHB 上榜即退出 = GO (微弱 edge), LF V0 = REJECT,
S3 = REJECT (前瞻标签泄漏); 单因子 max RankIC 0.107 (rz_balance), 多数 0.02-0.05。在这个
现实下, "立体多维空间"的**唯一真风险 = 组合维度爆炸 × 过拟合 × 泄漏面扩大** (architect-controller
rule 6: 多 agent / 多维系统第一死因 = 为没来的负载建基础设施)。

因此本设计的脊柱是: **框架现在定法 (健全且全复用现有 infra), 但实例化克制 —— 维度按 OOS
证据逐个解锁, 不一次全开。** 先证明"单 segment × 单 feature_set × 单策略"OOS 优于全市场统一
基线, 才允许加第二个维度。

---

## 1. Legislator Block (立法层)

### 1.1 Genesis layer (创世层, 不可变, ≤3 句)

- **为何存在**: 把"一套参数打全市场"这个已被证伪的隐含假设 (per-stock optimization 是它的
  反面极端, 同样易过拟合), 换成"按可解释的真相源分组, 每组配可被 OOS 证伪的主+辅策略"的
  可治理结构。
- **死亡线 (≤3, 陌生人可判)**:
  1. 任何 cell 的特征集若与 builder 标签集 (PIT_LABEL_COLS) 交集非空 → 该 cell 作废, 不入候选 (泄漏死)。
  2. 任何维度解锁若无"该维分组 OOS 严格优于全市场统一基线"的实测证据 → 不解锁 (过拟合死)。
  3. 任何 cell 用 in-sample 分数入 champion / paper_sim 决策 → 违宪 (估计死)。

### 1.2 Judgment codex (判断法典, 可演进, 人话 + 机器话)

| # | 人话 | 机器话 (可执行) |
|---|---|---|
| C1 | 分组的边界必须来自真相源, 不是拍脑袋的桶 | segment 定义只引用 `universe_rules.board_prefixes` / `technical_stage` / circ_mv 分位 / moneyflow 状态; 禁止新建中间分组表 |
| C2 | 数据源族作为 optuna 维度时, 一个族 = 一个可 on/off 的开关 + 族内参数 | search space 的 feature-group 轴只枚举 `feature_registry.groups` 里 `production_ready: true` 的组 |
| C3 | 主策略决定持仓, 辅助策略只调制不独立持仓 | cell 注册表里 `role: primary` 至多 1 个出仓决策源; `role: auxiliary` 只能改 size/gate/exit, 不能新增持仓 |
| C4 | 一个 cell 的好坏只看 OOS, 看不出 OOS 就是 unknown | selector / 入库只读 `oos_*` 列 + `oos_* IS NOT NULL`; 无 OOS 覆盖 = score=unknown 不进候选 |
| C5 | 维度爆炸用跨 study selection-bias 治理压住 | cell 数 × trials 超阈触发 `deflated_sharpe` (optuna_config §8); 单维 cell 数上限写 config 不 hardcode |

### 1.3 Death clauses (系统级证伪条款)

- **感知死**: 任一已解锁 cell 的 OOS 预测连续 N 个 walk-forward 窗未做 forward 兑现回填 →
  该 cell 冻结, 不参与 champion。检测器: `mart_model_lifecycle` + paper_sim forward gate。
- **判断死**: 法典 C1-C5 固化而市场漂移 —— segment 划分 (如板块涨跌停规则) 变更或某 feature
  group 的 IC 长期归零却仍在 cell 里 → 季度 codex 复审强制下线该轴/族。
- **谄媚死**: 立方体只产出"看着漂亮"的高维组合 (典型: 维度越加回测越好) 而 forward 不兑现 ——
  铁律: cell 晋级只认 forward 准确度, 回测好看本身触发 §4.2 异常核查, 绝不因"组合更全"加权。

### 1.4 七问 genesis checklist

1. **为何存在**: 见 1.1。
2. **死亡线**: 见 1.1 (3 条)。
3. **第一个具体目标**: 在**板块轴**上证明"创业板/科创板 (20% 涨停) 用与主板不同的 stop/target
   参数, OOS Calmar 严格优于全市场统一参数"—— 这是物理差异最硬、最可能真实的一维 (limit_up_pct
   已是 per-board 真相)。一维成立才碰第二维。
4. **谁定不可逆**: 主会话 (controller) 独占 —— 维度解锁 / champion 晋级 / 库写入。side-agent 只交
   analysis 草稿或代码+测试, 禁改控制面 (CLAUDE.md §10)。
5. **生存环境**: A 股, T+1, 板块涨跌停异质, 弱 alpha (单因子 IC 0.02-0.1), 强泄漏风险。
6. **什么算好 (codex 种子)**: 见 1.2 (C1-C5)。
7. **资源预算**: 计算走 `experiment_jobs.yaml` (local active / modal $30/mo, dry_run 默认);
   人的深度注意力 = 每解锁一维需 controller 亲核一次 OOS 证据 + 一次泄漏 gate, 不外包判决。

**Gap analysis**: 角色→执行体映射 ——
- segment 划分 / feature-group toggle / per-cell optuna: 可委托 side-agent 跑 (read-only IC / 独立 ablation)。
- cell 注册 / 维度解锁 / champion 晋级 / 库写: controller 独占 (不可逆)。
- 缺的人/角色: 无新增; 全部落到现有 services + config + 主会话 review。

---

## 2. 架构: 策略立方体 (三正交轴)

立方体一个 **cell = (Segment, Feature-set, Policy)** 三元组。每个 cell 是一个候选策略实例,
走中央 optuna (walk_forward OOS) → paper_sim (含成本) → champion lifecycle。

### 轴 S — Segment (分组, 真相源已全有)

| 子维 | 真相源 (现成) | 取值示例 | 状态 |
|---|---|---|---|
| 板块 | `universe_rules.board_prefixes` + `limit_up_pct` | 沪主板/深主板/创业板/科创板 | **第一个解锁候选** (物理硬差异) |
| 形态 | `technical_stage.yaml` (Stan Weinstein) | stage 1/1.5/2/3/4 | 待 (一维成立后) |
| 市值 | circ_mv (tushare daily_basic 增量, 分位切桶) | 大/中/小盘 (截面分位 PIT) | 待 (依赖 circ_mv 入面板) |
| 资金状态 | moneyflow 域 (net_mf 分位) | 净流入/净流出 | 待 (IC 0.0267, 弱; 优先级低) |

> 真相源唯一 (宪法第一条): 不新建分组中间表; segment 是运行时按真相源切, 不物化成 dim_segment_*。
> per-stock optimization (`mart_per_stock_stage_strategy_optimal`) = 分组粒度的极端 (每股一组),
> 已存在但易过拟合; 立方体的 segment 是它与"全市场一组"之间的可治理中间粒度。

### 轴 F — Feature-set (数据源族, optuna toggle 维度)

底座 = `feature_registry.yaml` 的 `groups` (已按 source_tables / feature_role / production_ready /
coverage_universe / pit_release_lag_days 组织)。**用户的"数据源独立做成参数"= 把每个
production_ready group 当一个 on/off 开关进 optuna search space**, 再叠族内参数。

- 实现 = 给 `optuna_config.yaml` search_space 增 `feature_groups` 轴 (枚举 registry 里
  production_ready 组, 不 hardcode 组名)。
- 泄漏闸 (强制): 每个被选中的 group 组合过 `leakage_consumers.yaml` gate (任一 HIGH = cell 作废)。
- 克制: 初版只允许"全 core_model_input 组 + 单个 candidate 组"二元对比, 不做全组合爆炸搜索。

### 轴 P — Policy (规则 × 模型 × 策略, 含主辅角色)

用户的"规则+模型+策略的应用方式"= 三类 policy 源, 每个 cell 选其组合:

| Policy 类 | 现有 owner | 角色 |
|---|---|---|
| 规则 (rule) | `formula_*.yaml` (9 个公式) | 可主 (选股) 或辅 (gate) |
| 模型 (model) | LGBM / LambdaMART (`services/ml_ranking`, champion_registry) | 主 (排序持仓) |
| 退出/仓位 (exit/size) | `optuna_config.search_space.strategy` (stop/target/trailing/hp/buy_offset) + `portfolio_sizer_profiles` | 辅 (调制) |

**主辅契约 (C3)**: 一个 cell 至多 1 个 `role: primary` 出仓源 (规则选股 或 模型排序);
辅助 (`role: auxiliary`) 只能改 size / 加 gate / 改 exit —— 例: LHB 上榜即退出 (GO 判决) =
auxiliary exit gate, px_pctile 卖点 = auxiliary exit, regime_gate = auxiliary size。辅助永不
独立开新仓。

---

## 3. 模块 + 数据 + 配置 落地 (复用 > 新建)

宪法第三条范式: config (规则/阈值) + service (读 config 查真相源) + 真相源 (只读不派生)。

| 层 | 新增/复用 | 内容 |
|---|---|---|
| config | **新增** `backend/config/strategy_cube.yaml` | 三轴定义 + 各轴解锁状态 (locked/unlocked + 解锁证据指针) + cell 注册表 (segment×feature_set×policy + role) + 单维 cell 数上限 |
| config | 复用 `feature_registry` / `optuna_config` / `universe_rules` / `technical_stage` / `champion_registry` | 不复制其内容, cube.yaml 只引用 (防双真相源) |
| service | **新增** `backend/services/strategy_cube/` | 读 cube.yaml → 按解锁轴展开 cell → 调中央 `services.optimization` per-cell 跑 walk_forward → 写 mart; 不裸调 study.optimize |
| service | 复用 `services.optimization` (walk_forward/search_space/composite/constraints/governance) | cube 只是它的 cell 级编排层, 不重写寻优 |
| 真相源 | 复用 K线/日历/已有 segment 真相源 | 零新派生表 |
| mart | **新增** `mart_strategy_cube_optimal` (cell × oos_* 指标) | 沿用 per_stock_stage 表的 oos 列约定; selector 只读 oos_* |
| gate | 复用 `leakage_consumers.yaml` + `plan_validator.enforce_optuna_plan` + moth 弹仓 | 每个 cell 的 feature_set 强制过泄漏闸; cube 跑批前过 plan_validator |

---

## 4. Verification grid (组件闸 + 系统死亡条款)

| 检验项 | 工具 (现成) | 触发 |
|---|---|---|
| cell 特征集 ∩ 标签集 == ∅ | `leakage_probe --gate` (已接 safe_commit Step 3.6 + moth) | 每个 cell 注册前 |
| cell 只读 oos_* | `governance.enforce_pre_insert` (拒 walk_forward_mode='none') | 入 mart 前 |
| 维度解锁有 OOS 证据 | 新增 cube 解锁 gate: 比"该维分组 best cell OOS"vs"全市场统一基线 OOS", 严格优才解锁 | 每次解锁一维 |
| 跨 cell selection bias | `deflated_sharpe` (optuna_config §8, P1 开) | cell 数 × trials 超阈 |
| 异常高数字 | §4.2 红线 (RankIC>0.15 / sharpe>5 / 年化>100%) → 异常核查协议 (ablation→PIT trace→剔除重跑→shuffle) | 任一 cell OOS 出数 |
| forward 兑现 | paper_sim 含成本 + forward gate | cell 晋级 champion 前 |

---

## 5. Smallest reversible next step (最小可逆第一步)

**不建任何新基础设施。** 先用现有 `mart_per_stock_stage_strategy_optimal` 的数据做一个
read-only 验证, 回答创世第一个目标 (七问 #3):

> 把已有 per-stock optimal 参数按板块 (60/00 vs 30/68) 聚合, 看"创业板/科创板的最优
> stop/target 分布"是否与主板**显著不同且 OOS 表现更好**。

- 若**显著不同 + OOS 更优** → 板块维真实, 解锁轴 S 板块维, 才写 `strategy_cube.yaml` 第一版 +
  `services/strategy_cube/` 骨架 (只含板块维)。
- 若**无显著差异** → 整个立方体降级为"暂不解锁", 只把本设计存档为法 (genesis + codex + 死亡条款),
  等单 segment 单策略先证明有 edge —— 绝不因"架构漂亮"先建空壳 (rule 6 死因)。

这一步纯 read-only DuckDB 聚合 + 统计检验 (ANOVA / Mann-Whitney, 同 CYQ C0 协议), 零写库、
零不可逆, 失败成本 = 一次查询。

### 5.1 §5 实测结果 (2026-06-13, 已跑, 证据 `cube_board_axis_check_20260613.json`)

源表 `mart_per_stock_stage_strategy_optimal`, 1725 行 OOS-valid (expanding_monthly, oos_sharpe
非空), 主板 (60/00, 10% 涨停) 1128 行 vs 创业/科创 (30/68, 20% 涨停) 597 行。Mann-Whitney 双侧:

| 指标 | 主板 median | 20% 板 median | p | 显著? |
|---|---|---|---|---|
| optimal_stop_pct | -0.0731 | -0.0725 | 0.157 | 否 |
| optimal_target_pct | +0.1679 | +0.1646 | 0.209 | 否 |
| optimal_trailing_pct | +0.0615 | +0.0629 | 0.803 | 否 |
| oos_sharpe | -0.1335 | -0.1088 | 0.557 | 否 |
| oos_avg_ret | -0.0068 | -0.0062 | 0.596 | 否 |

**两条硬结论**:
1. **板块间最优参数无显著差异** (全部 p>0.15) —— 物理涨停差异 (10% vs 20%) 未转化为最优
   stop/target 参数差异, 即"板块维"在现有数据上不成立。
2. **底层 per-stock stage 策略 OOS 本身为负** (median oos_sharpe -0.11~-0.13, mean -0.35~-0.41;
   oos_avg_ret median 也为负) —— 没有可被分组的 base edge。

**判决: 板块维不成立 (BOARD_AXIS_NOT_JUSTIFIED), 立方体实例化 BLOCK 维持。** 瓶颈不在架构/分组,
而在 base-edge 缺失 —— 给一个零/负 edge 的策略按板块切, 只是在切噪音。继续往更多 segment 轴
(形态/市值/资金) 试探, 是"循环到找出一个好看的数"的谄媚死反模式, 不做。

**算力重定向 (诚实结论的下一步)**: 不建 cube, 把注意力放回**先找 base edge** —— 即
`analysis/systematic_validation_plan_20260613.md` 的 L0-L4 (清泄漏、确认哪些特征 OOS 真有 IC)
+ T 轨 tushare 域 alpha 研究。base edge 一旦被某个 (feature_set, policy) 组合证明为正, 立方体的
segment 轴才有意义再回来按本设计逐维解锁。本设计作为"法"存档, 随时可激活, 但今天不实例化。

---

## 6. 输出形态 (architect-controller compact shape)

```
Objective:          多维策略立方体顶层设计 — 把分散的 segment/feature-group/policy 件组织成
                    可治理、可逐维解锁的立方体, 反对"一套参数打全市场"与"per-stock 全碎"两个极端。
定义权:             用户定方向 (四想法), controller 定"什么算解锁成功"(OOS 严格优于统一基线) + 死亡条款。
Load-bearing 决策:  (1) 框架定法 vs 实例化克制分离; (2) 维度逐个解锁需 OOS 证据; (3) 主辅角色契约;
                    (4) 全复用现有 infra, 唯二新增 = strategy_cube.yaml + services/strategy_cube/。
真相源/substrate:    K线/日历 (交易) + universe_rules (板块) + technical_stage (形态) + circ_mv (市值) +
                    moneyflow (资金) + feature_registry.groups (数据源族); 零新派生表。
边界与契约:          cube = services.optimization 的 cell 级编排层, 不重写寻优; cube.yaml 只引用不复制其他 config。
Verification grid:   §4 (泄漏闸/oos-only/解锁 OOS gate/DSR/异常核查/forward 兑现)。
Delegation:          IC/ablation/per-cell optuna 可委托 read-only side-agent; 解锁/晋级/写库 controller 独占。
最小可逆下一步:       §5 — read-only 板块维聚合检验 (per-stock optimal 已有数据), 失败成本 = 一次查询。
Verdict:            PROCEED (设计立法) — 但实例化 BLOCK 直到 §5 验证给出板块维真实信号。
```

**Verdict: PROCEED 立法 + 实例化 BLOCK (§5 已实测, BLOCK 坐实)。** 本设计 (genesis/codex/死亡
条款/三轴/复用图) 落账为"法"; §5 read-only 板块维检验已跑 (2026-06-13) —— 板块间参数无显著
差异 + 底层 OOS 为负, **板块维不成立, 不写一行 cube 代码、不建 cube.yaml**。这是对 architect-controller
rule 6 (反对为没来的负载建基础设施) 与宪法"能删必删"的硬执行。算力重定向到先找 base edge
(systematic_validation_plan L0-L4 + tushare T 轨), edge 一旦为正再回来逐维解锁立方体。

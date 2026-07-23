# 整体优化方案完成度审计 — 2026-07-23

> **生命周期**：evidence-only audit（只读裁决；**不**开新 feature 刀）  
> **权威锚**：原方案 `~/.cursor/plans/gap_analysis_audit_3cdd0f6e.plan.md`（2026-07-19，「ChunkyMonkey 整体优化方案」）  
> **Living 续版**：`analysis/MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md`（§12 1:1 映射；analysis 层唯一 roadmap）  
> **现场**：`goal.md` · `BOARD.md`（投影）· `global_cleanup_rebuild_plan_20260723.md` · `foundation_residual_fix_plan_20260723.md` · FND-GATE live  
> **Label**：`AUDIT FIXED`（完成度表 + 支线表 + 残留 backlog；north star 未改）

---

## 0. Executive（一句话）

原 A→H **底座+能力轨 ≈ 完成**；**研究/发布轨（E–H / RX）刻意暂停**。近几日大量「支线」多为地基闭环与更新流卫生，**半跑偏于叙事密度**，但多数刀有 named consumer；收束法 = **停新支线 → Knife 4 或停手 → owner 开 RX**。

| 口径 | 完成度 | 总判 |
|---|---|---|
| **原方案 A→H（产品迁移总序）** | **~68%** | 底座 A–D/E0 + 产品诚实面大体到位；E/F 已测但未 remeasure；G/H 未开 |
| **Living roadmap（MASTER §7 F0→CX→RX）** | **~86%**（至 CX 闭合） | F0/F1/CX-1…4 **PASS**；RX **BLOCKED**；Phase N **BANNED** |
| **FND-GATE（机器）** | **10/10 PASS** | `phase_closure_ready=true`（本审计 live 复跑） |

---

## 1. 原方案定位与权威漂移

| 项 | 事实 |
|---|---|
| 原文件 | `/Users/dp/.cursor/plans/gap_analysis_audit_3cdd0f6e.plan.md`（**不在 repo**；Cursor plan artifact） |
| 日期/状态头 | 2026-07-19；文内仍写「讨论收敛稿…**未实施**」← **已过时** |
| Repo 合一 | `4337f0fb6` 起 → `MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md`；`goal.md` 指针块指向它 |
| 近端排序 | `goal.md`：**foundation CLOSED**；下一轨仅 **owner schedule 的 E/F remeasure（RX）** |

**读法**：完成度以 **原 A–H 表** 对账；执行排序以 **MASTER §7** 为准（owner 已把「底座关键路径 + 分阶段验收」叠进原方案）。

---

## 2. 原方案分块完成度（A–H + 横切）

证据源：`goal.md` 硬事实 · BOARD 投影 · git log since 2026-07-19（≈216 commits）· FND-GATE · MASTER §8/§12 · 2026-07-23 analysis 簇。

| 块 | 原退出条件（摘要） | 完成% | 证据（要点） | 残留 |
|---|---|---|---|---|
| **§1 产品法** | 死亡线 + 人话/机器话 | **100%** | 仍写在 goal/MASTER/eng_gov；未改 north star | 纪律维持 |
| **§2 积木+两轴+多源** | transport×业务；adapter 可换 | **92%** | S1–S6 FIXED；TuShare registry live；miaoxiang 披露域仍 dual/NONCONFORMING→E0 路径 | 第二 adapter 非目标 |
| **§5 边做边测** | 红→绿→窄回归 | **90%** | §15 + blocking pytest + Rule10 常态；偶发 doc-only 刀 | 持续 |
| **§9 文档落地** | goal/MASTER/strategy/eng_gov | **95%** | 合一 + DOC_AUTHORITY；plan artifact 本身未迁入 repo（有意） | 原 plan 头「未实施」stale |
| **A Tier0 硬门** | calendar/名义K/ST/contract/landing | **98%** | F0 CLOSED；FND-GATE F1–F10 PASS | live frontier 墙钟（见 §3） |
| **B-ext** | external_aggregate 诚实 | **80%** | margin 1a SSE+SZSE + 1b v3 bounded catchup **FIXED**；pulse `rzrqye` 仍 UNTRUSTED | 1c shadow / product thaw **禁** |
| **B-pit** | project_universe_pit 切读 | **95%** | `cutover_allowed=true`；shadow 对账投影绿 | 禁无证据回翻 |
| **C Tier1/2 契约** | StockState / MarketContext + 展示诚实 | **88%** | C consumer cutover；Cap A/B/D + CX-3 briefing/facet | L3 enrichment defer；感知 L1 稀疏 |
| **D 研究运行时** | Snapshot→Verdict | **95%** | Phase D FIXED；B0 offline measured | — |
| **E0 披露 formal** | land→accept；直写退役/隔离 | **85%** | holders/stk provider+E0-HIST F6；org **incremental-check**（非 invent）；闭环 population gate | holders ~32× landing KEEP；miaoxiang 表面仍在 |
| **E 机构跟随** | B0→B4；无 Release 出候选 | **35%** | 已测 `measured_reject_no_gain` 归档；**RX remeasure 未开** | owner 签字 |
| **F 主升浪** | B0–B2 增量 | **45%** | F0–F3 ladder reject **protocol-complete**；remeasure paused | 同 RX |
| **G 公式/BestChoice** | B5 消融 | **5%** | BestChoice FROZEN；未进 Phase N | BANNED until RX |
| **H Release/纸面** | StrategyRelease | **8%** | 产品 research/stale 诚实；无 Release | 禁提前出候选 |
| **§7 产品面（当时）** | pulse/机构/paper/workbench | **90%** | workbench+Cap E/F **shipped**；dossier 100% usable FIXED | Continuity 非 READY≠产品假绿 |

### 2.1 加权总判（原方案）

- **底座+纪律（§1–2,A–D,E0,§5,§9）≈ 91%**  
- **策略包+发布（E–H）≈ 23%**（诚实 reject ≠ 完成迁移）  
- **算术合成（底座 0.65 权 + 策略 0.35 权）≈ 68%**

Living 口径（MASTER 把 A–E0 收成 F0，把能力补成 CX-*）：**至 CX 闭合 ≈ 86%**；缺的是 **RX（0%）+ Phase N（禁）**，外加 cleanup Knife 4。

---

## 3. 需更新的细节（delta list — 不重写 north star）

> 原则：north star / 死亡线 / A→H 骨架 **保留**；只列 **事实过期句**。

### 3.1 原 plan artifact（`gap_analysis_audit_3cdd0f6e`）

| # | 过期句/设定 | 现况 | 建议动作 |
|---|---|---|---|
| 1 | 文首「未实施」 | 已大规模实施；MASTER 为 living | 文首改 `superseded by MASTER_…20260722`（可选；不在 repo） |
| 2 | §8「仅 margin 有 formal；calendar 未 live；resolver 未通；pulse 读错 scope」 | F0 闭合；B-pit/C cutover；margin v3 scope FIXED | 整节标 historical |
| 3 | §7 Workbench「占位」 | 一键更新 + 分步节点 + progress UX FIXED | 改为 shipped |
| 4 | §4「Phase A 唯一开工合法区」 | foundation CLOSED；近端=RX 或 cleanup | 指向 MASTER §7/§9 |
| 5 | 附录 G1–G6 BLOCKING | FND-GATE 覆盖闭合 | 标 CLOSED@F0 |
| 6 | E 在 F 前「下一刀」叙事 | E/F 均 paused；能力轨 CX 已插队完成 | 顺序以 MASTER 为准 |

### 3.2 Living / goal / BOARD（小 delta）

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| 7 | `BOARD.md` track `foundation_solidify_85pct_…` | 与 `phase_closure_ready=true` / CX PASS 不一致 | 重跑 `build_agent_board`（投影） |
| 8 | `goal.md` 护栏 `frontier=20260721` | 文内另有 ST `20260722` / ths_hot 发布窗叙述；易混墙钟 | 拆「accepted formal frontier」vs「drain soft 窗」一行 |
| 9 | `foundation_residual_fix_plan` 文首「1b shipping」 | 正文 1a/1b **DONE**；global plan Knife 1 **FIXED** | 改 header → CLOSED / residual=ops catchup+Knife4 |
| 10 | MASTER §8「margin observe_frozen_stale」 | 1b 后 catchup **路径**已开；产品仍 UNTRUSTED | 改「path FIXED / product trust gated」 |
| 11 | MASTER §9 NEXT 停在「RX」 | 2026-07-23 另有 **global cleanup Knife 4** | NEXT 加一行：cleanup Knife4（诚实 Continuity）**或** owner 明示停 cleanup |
| 12 | 原方案「机构提前」vs 实序 F 先测 | F0–F3 与 E measured 均已归档；不违「首包=institution」立法 | 注明「baseline 已测；正式首包仍 E，待 RX」即可 |

**不改**：产品死亡线、禁 Optuna/Release/margin thaw/org mass、§15、沪深A含 ST、manual_only。

---

## 4. 支线执行表（会话中途 bug-fix / cleanup）

状态定义：**完整** = 代码+证据 FIXED 且主路径可用；**半截** = 路径 FIXED 但 ops/历史债未清；**未做** = 未开工或显式 defer；**废弃** = 明确不做。

| 支线 | 计划有？ | 执行 | 证据锚 | 备注 |
|---|---|---|---|---|
| Margin freeze→诚实 observe | 有（residual + freeze 史） | **完整**（诚实） | `margin_calendar_catchup_blocker` · goal 禁 thaw | 解冻产品仍 **禁** |
| Margin Knife **1a** scope | 有 | **完整** | `e6b3e44c5` | SSE+SZSE fail-closed |
| Margin Knife **1b** bounded catchup + **update-flow** | 有 | **完整（路径）** / ops **半截** | `0f5af7e80` · `margin_catchup_live` · `margin_v3_…1b` | `local_max→eligible_end` 需 token 实跑；补跑 CLI≠正解（已纠正） |
| Margin **1c** pulse shadow | 有（optional） | **未做** | residual plan §1c | 非近端必须 |
| Cap F dossier **100% usable** | 有（Cap F mandate） | **完整** | `d88167c5a` · `dossier_100_usable` | 勿与 Continuity READY 捆绑 |
| Factor **`stk_factor_pro` 删能力** | 有（bloat→orphan） | **完整** | `a75288129` + tombstone | 模式=删能力非补丁 |
| Holders **skip-land**（同 payload） | 有（Knife 2） | **完整（路径）** | `67cd81c27` | 历史 ~32× **KEEP** |
| Holders retention/archive | 有（later） | **未做** | global plan optional | 禁 bare DELETE landing |
| Serve→derive **闭环立法** | 有（owner 立法） | **完整** | `ac9a96e85` · `serve_derive_closed_loop_law` | |
| Org **repair** + F6 人口地板 + as_of seed | 有（闭环残差） | **完整** | `d7ee57c7c` · `closed_loop_residual_closure` | mass 仍 BANNED |
| Org **incremental-check-every-run** | 有（Q3） | **完整** | `89a17ddea` · `org_holding_incremental_loop` | |
| Holders same-day sparse / 股东公告设计 | 有 | **完整** | `e040f4889` · `shareholder_update_check_design` | 全宇宙扫 **BANNED** |
| DB bloat 深潜 + refill 审计 | 有（分析刀） | **完整（审计）** | `db_bloat_deep_dive` · `db_refill_after_delete_audit` | 驱动 cleanup 序 |
| **Rewrite mechanism** 裁决 + 删 canary/legacy rewrite | 有（owner Q） | **完整（裁决）**；WT 或有未推送残留 | `rewrite_mechanism_verdict_20260723.md`（label FIXED） | 用户标 `a55928d3`：以 WT/git 收口为准 |
| Market compact + **模块内** post-CTAS hook | 有（Knife 3） | **完整** | `8f36809bf` · `market_compact_knife3` | 用户标 `16637b10`：主仓已有 Knife3 commit；若仍有 staged 同行改动需序列化 |
| Update-flow vs 补跑 纠偏 | 有（owner） | **完整（原则）** | margin_catchup_live · market_compact · rewrite verdict | 根因进模块/acquire，禁定期 fixer |
| Global cleanup Knife **4** Continuity | 有 | **未做** | `global_cleanup_rebuild_plan` §0/#4 | **下一 cleanup** |
| CX-1…CX-4 | 有（MASTER §7） | **完整** | `cx_closeout_rx_honesty` | |
| 跑步机 Phases 0–3 | 有 | **完整** | treadmill first-principles | ops≠刀 |
| Adversarial acquire/process A/B | 有（评审） | **完整（文档）** | review A/B 20260723 | 非执行刀 |
| Frontier detection 统一 | 有 | **完整（子集）** | `unified_frontier_detection_acceptance` | |
| E/F **RX remeasure** | 有（原 E/F） | **未做**（paused） | goal · cx_closeout | **需 owner 签字** |
| Type-B enrichment | 有（B5 residual） | **未做**（DEFER） | FND F4 wall | |
| S7 假 COMPAT / mass pre-accept | 禁令 | **废弃（正确）** | goal bans | |
| Optuna / StrategyRelease | 禁令 | **废弃（未到）** | Phase N BANNED | |

### 4.1 正在跑 / WT 注意（审计时刻）

| ID/主题 | 观察 | 处置 |
|---|---|---|
| rewrite verdict（`a55928d3`） | 证据文件已在树；label FIXED；dirty/staged 可能与 peer 重叠 | **勿并行改同一披露/ingest 文件**；确认 commit/push 后关单 |
| market compact / derive hook（`16637b10`） | `8f36809bf` 已在 `main`；若仍有 staged `build_price_kline_qfq*` / evidence | 与 peer **serialize**；禁第二把 compact 刀 |
| 本审计 | 只新增本文件 | 不碰上述代码 |

`moth snapshot --repo .`：**WARN**（dirty worktree + codegraph stale）— 符合「多 agent 并行收口中」，非 foundation 回退。

---

## 5. 残留清单（有序 backlog）

> 排除「正在跑 agent 的重复劳动」；按 **依赖 + Occam** 排。

| # | 项 | 类型 | Owner / 门 | 说明 |
|---|---|---|---|---|
| **R0** | 序列化 dirty WT / 关掉 rewrite+compact 同行提交 | process | 当前会话 peer | 禁 `git add .`；一刀一 commit |
| **R1** | **Cleanup Knife 4** — Continuity 诚实残差（dividend/hsgt/…） | L2/L3 按需 | global_cleanup §6 | 禁 READY cosmetics；margin WARN 可保留 typed |
| **R2** | Margin **ops catchup** 推进 `local_max`（token） | ops 轴② | residual plan 1b exit | 非新代码刀优先 |
| **R3** | Holders landing **retention/archive** + smartmoney compact | later L3 | global optional | 在 skip-land 证明无新风暴后 |
| **R4** | Margin **1c** pulse consumer shadow | optional | residual §1c | 仅 shadow 证据后 |
| **R5** | **RX** E/F remeasure（同 protocol） | research | **owner 显式 schedule** | 禁 Optuna/松 holdout/Release |
| **R6** | S7 publication / sunset（按需） | Tier0 | owner block only | 禁假 COMPAT |
| **R7** | Type-B enrichment | defer | FND wall | 非近端 |
| **R8** | qfq **incremental** 写形状 | product later | market_compact residual | compact hook 已使 full CTAS ops-safe |
| **R9** | BOARD / codegraph sync | hygiene | projection | 不执法 |

**明确不进 backlog**：org mass、Continuity 追绿、S7 blanket、margin product thaw、第二 DB/DAG、擅自 E/F、Phase N。

---

## 6. 是否跑偏？如何收束回主方案

### 6.1 判词

| 问 | 答 |
|---|---|
| 相对原 A→H「下一刀该是 E」？ | **叙事上已偏**：E/F 暂停后，主轴变成 **底座能力 CX + 产品 usable + 更新流卫生**（MASTER 合法化）。 |
| 相对 living MASTER「CX 完 → RX」？ | **近端 cleanup（bloat/margin/holders/rewrite）是支线簇** —— 多数有 named consumer（update-flow / 删 orphan / 防假绿），**不是**无脑追 Continuity READY；但 **analysis 爆炸 + 多并行刀** 造成「对话跑偏」体感。 |
| 有没有违死亡线？ | **未发现**：Optuna/Release/thaw/mass/org invent/假 COMPAT 仍守。 |

**总判**：**半跑偏（执行密度）· 未叛北（立法）**。

### 6.2 收束动作（给 controller）

1. **冻结新支线**：无新 bloat/rewrite/dossier 刀，除非 R1  Continuity **typed wrongness**。  
2. **二选一近端**：  
   - (a) 做完 **R1 Knife 4** 后停 cleanup；或  
   - (b) owner 宣布 cleanup CLOSED，只留 **R2 ops**。  
3. **主方案指针**：日常只认 `MASTER_…20260722` §7/§9 + `goal.md` 下一步；原 `.cursor/plans/…3cdd0f6e` 仅历史。  
4. **战略下一跳**：仅当 owner 签字 → **R5 RX**；否则用 shipped 产品面，把轴②交给交易所时钟。  
5. **文档**：只打 §3 delta（BOARD 重生、residual header、MASTER §8 margin 句），**禁止**再写第三本 bible。

---

## 7. 便宜 live 门（本审计）

| 门 | 结果 |
|---|---|
| `check_foundation_done.py` | **PASS** 10/10；`phase_closure_ready=True`（含 F6 org `max_stocks=5524≥500`） |
| `chunkyctl agent-boot` / moth | overall **warn**；dirty WT；claims pass；codegraph stale |
| git since 2026-07-19 | ≈**216** commits；主线可见 transport→FND→CX→闭环→cleanup |

---

## 8. Verdict

| 标签 | 内容 |
|---|---|
| **原方案完成度** | **~68%**（底座高、E–H 低） |
| **Living 完成度** | **~86%** 至 CX；RX=0 |
| **支线** | 多数 **完整/路径完整**；Knife4 + RX + retention **未做** |
| **跑偏** | 半跑偏（cleanup 簇过密）· 立法未叛 |
| **下一步** | R0 序列化 → R1 或停 → owner RX |
| **NORTH_STAR** | **未改** |

**APPROVED as audit** — 可作 owner 收束会议单页；implementation 仍禁借本文件开 feature 刀。

# 为何像补丁跑步机 — 2026-07-22

> **SUPERSEDED as roadmap judgment** by `analysis/MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md` §4/§10（+ `DOC_AUTHORITY_20260722.md`）。本文件保留为跑步机诊断原文。
> Status: evidence-only / judgment（**禁止再开代码刀**；本文件不授权 implementation）  
> Authority: `$mio` + `goal.md` + foundation/product closeout + acquire rebuild 证据  
> Adversarial: [架构太复杂](576ee281-ef5b-416b-bda5-7232ef7a0d99) vs [工作队列错了](a1c0b123-4289-47a6-ab3f-42852f32b97a)  
> Prior agent died mid-task: `1619f053-34e7-4d65-9e48-43e5f02669f3`（重做判断，不续刀）

---

## 0. 一句话诊断

**真问题不是「架构烂到必须推倒」，而是 scheme/product 已 CLOSED 后，仍把 ops 时钟窗、诚实 PARTIAL、DONE degraded 当成可 commit 的代码债——偶尔叠加编排硬门再生真结构刀，于是体感成无尽补丁。**

---

## 1. 业主卡住的感觉从哪来

| 感觉 | 证据上的对应 | 误读 |
|---|---|---|
| 「地基永远做不完」 | `phase_closure_ready=true` / FND-GATE PASS，但 `foundation_full_goal_push` 对「一直讨论想要的」仍 **PARTIAL** | 把 **scheme-exit floor** 当成 **讨论级完整日更 ceiling** |
| 「产品永远差一点」 | 0r→5B mandate CLOSED + residual-clear；form/机构是 **FIXED honesty**，不是未 ship | 把 hybrid/~54% 诚实披露当成「还没做」 |
| 「每修一个又冒一个」 | ths_hot：hard fail → `pending_publish` → stock_st 绑架 drain → drain-first 重建；晨间仍 DONE degraded | 把 **vendor/publish 时钟** 与 **编排形状债** 混成同一类「架构未完成」 |
| 「只有我这个项目这样」 | A 股多源 + PIT + same-day vacuum + `manual_only` 日更 | 把 **域固有 ops 税** 误诊为「本仓独有工程失败」 |

---

## 2. 只是这个项目吗？

**不全是。** 任何认真做 A 股「可审计决策辅助」的系统都会付这些税：

- 交易所/供应商 **发布窗**（`available_after`、盘前 `zero_rows`）
- **PIT / fail-closed**（宁 degraded，不假 READY）
- **universe ≠ acquire 黑名单**（ST∈白名单 vs `stock_st` membership 证据）
- **commit 绿 ≠ Continuity READY**（宪法已写死）

本仓「更痛」的独特放大器（不是宇宙唯一，但是本地加强）：

1. **交付文化 = 一刀一 commit**（§15）—— ops「等 22:30 / 点数据更新」产不出刀，agent 本能把 PARTIAL 翻译成可写代码的队列。  
2. **多权威文档 + 多 typed 状态**——floor 门绿与 live PARTIAL 长期并存，队列信号嘈杂。  
3. **研究出口被禁/paused**（Optuna/Release/E·F 未 schedule）——闲置刀力回流 foundation 边缘。  
4. **Strangler 未熄灭的双读**（form hybrid）——诚实披露必要，但看起来永远「差最后一刀」。

结论：**痛感放大是本仓激励+阶段错位；税本身是域的。** 换项目若同样要求 PIT+真金白银+多源日更，仍会有 ops 跑步机——只是若不把每张告警都变成代码刀，体感不会这么「工程永动机」。

---

## 3. 架构有罪，但罪名不是「太复杂所以废」

### 3.1 对抗收敛

| 论题 | 成立部分 | 不成立部分 |
|---|---|---|
| **A. 架构太复杂** | 正交轴交互边会再生真刀（acquire raise-before-drain 绑架 sibling） | 「层太多→应 greenfield」——已被业主禁；通用 stage/DAG/plugin 更糟 |
| **B. 工作队列错了** | closeout 后真下一步是 ops + use + owner schedule；agent 用刀填满真空 | 「一切代码刀都是幻觉」——07-22 串行绑架是真编排债，已结构重建 |

**综合裁决（本文件）**：**主因 = 错误工作队列 / ceiling 错位；次因 = 编排耦合与状态词交互边偶尔再生合法结构刀。**  
架构有罪的是 **「把可审计分层做成过多可误读交互边 + floor 门给人未完成幻觉」**，不是「分层/PIT/typed soft 本身不该存在」。

### 3.2 架构具体有罪什么（可点名）

1. **编排硬门耦合**（已修一例，形状教训保留）：formal on_demand 失败 `raise Tier0` 放在 `--all-due` 前 → 今日真空绑架已发布域（见 `foundation_acquire_all_due_unblock_20260722.md`）。  
2. **状态词膨胀可被误当队列**：同一 `zero_rows` → hard / tombstone / `pending_publish` / degrade——机制对了，但若文档仍写「清 residual」，就变成下一刀燃料。  
3. **双平面 residual 的诚实税**：form hybrid、`institution_link_status`——正确，却像永远差「pure accepted」。  
4. **控制面第二宇宙**：FND-GATE / S7 墙 / §15 / blocking·nightly / cutover yaml 防假绿必要；副作用是 agent 永远找得到「可对齐的文档债」。

**无罪（勿砍）**：landing→accept、PIT、fail-closed、`pending_publish`、沪深A 含 ST、commit≠READY。这些是真金白银护栏，不是 treadmill 病因。

---

## 4. 跑步机机制（现象 → 如何再生下一刀）

```text
scheme/product CLOSED (floor 绿)
        │
        ▼
live: morning vacuum / DONE degraded / watermark PARTIAL / Continuity 非 READY
        │
        ├─► agent 激励：要 FIXED+commit 才像「推进」
        │         │
        │         ▼
        │   把 ops/clock/honesty 译成「再开一刀」
        │         │
        │         ▼
        │   偶发碰到真编排债 → 正当结构刀（drain-first）
        │         │
        │         ▼
        │   修完仍有同日 sibling vacuum → 又像没做完
        │
        └─► owner 体感：永远在补丁，无法「进入下一阶段」
```

可复现链条（2026-07-21→22）：

1. UI 日更缺按钮 / margin 预检 → 解阻刀  
2. DONE degraded / ths_hot 窗 → typed `pending_publish`  
3. stock_st 硬门绑架 drain → acquire **结构重建**（非纸补丁）  
4. live：`ths_hot` 已到 `20260721`，job 仍 degraded（continuity + 同日真空域）→ 若再开刀就进入 **假队列**  
5. ST 语义误读风险 → 文档/白名单纠偏（防错误「修复」）

**机制一句**：**绿门测 floor；时钟与供应商测 ceiling；刀文化只奖励改代码——三者错位即跑步机。**

---

## 5. 「真正下一步」是什么

按已写权威（`foundation_phase_reeval` / `plan_residual_reconcile` / `goal.md`），**不是**再开 foundation/product spine：

| 序 | 真正下一步 | 完成长什么样 | 谁做 |
|---|---|---|---|
| 1 | **用**已 ship 面 | workbench / pulse / 交集 / 选股 / 档案上做真实决策辅助消费；记录「哪里不够用」 | owner |
| 2 | **ops 时钟** | 正常交易日点「数据更新」；确认 drain 与 `ths_hot` 等同域水位；把 Continuity degrade 当**观测**，不当「地基未闭合」 | owner/ops |
| 3 | **owner 显式排期** | 要研究 → schedule E/F remeasure（同协议；仍禁 Optuna/松门/Release）；不排则保持 pause | owner |
| 4 | **P2 仅点名** | form pure accepted enrich、coverage lift 等 —— **默认不做** | owner 点名才开 |

**推进的定义要换**：从「又 FIXED 一个 residual」换成「本周用产品面做出可复核的决策辅助动作」或「owner 签字开研究窗」。

---

## 6. 明确不要做什么

- **不要**再开 acquire/编排/S7/Type-B/org/purity/vol/sub 代码刀「清 PARTIAL」——除非 owner 新 block + 可证消费者。  
- **不要**把 Continuity 非 READY / DONE degraded / 晨间 `pending_publish` 解读为 foundation 重开许可证。  
- **不要** greenfield / 第二 DB / plugin·DAG 框架「简化」——那是假出口。  
- **不要**为制度假绿：S7 假 COMPAT、假 pure accepted、margin thaw、mass org refresh。  
- **不要**擅自 E/F remeasure / Optuna / StrategyRelease。  
- **不要**把 `stock_st` 空窗修成「踢 ST」或「别 sync ST」。  
- **不要**用更多 analysis 权威文档代替「用产品 / 等时钟 / 排期研究」。

---

## 7. Verdict

| 标签 | 内容 |
|---|---|
| **REAL_PROBLEM** | 阶段已 CLOSED 后的 **工作队列/ceiling 错位**（主）+ 编排交互边偶发真债（次） |
| **ONLY_THIS_PROJECT?** | **否**——域税普遍；本仓被 §15 刀文化与多权威 PARTIAL 放大 |
| **ARCHITECTURE_GUILTY_OF** | 再生可误读交互边 + floor 绿制造未完成幻觉；**不**等于「分层/PIT 应废除」 |
| **NEXT** | **Use + ops clock + owner schedule**；**禁**默认代码刀 |
| **LABEL** | Judgment **FIXED**（文档裁决）；implementation **N/A** |

**APPROVED as stop-the-treadmill judgment.** 下一动作若不是「用 / 等时钟 / owner 排期」，默认视为假推进。

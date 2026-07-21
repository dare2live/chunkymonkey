# 吞吐瓶颈诊断 — 墙钟 vs 真相门（2026-07-21）

> **生命周期**：evidence-only / owner 裁决输入（非 owner contract）  
> **Authority 链**：`AGENTS.md` → `goal.md` → `docs/engineering_governance.md` §14–§15 → `backend/config/commit_tiers.yaml` / `ci_pytest_surface.yaml` → ledger 实测条目  
> **方法**：Mio（后果优先 + measured-not-estimated + 流程根治）+ Occam（先删「 vibes 瓶颈」，保留有测量背书的解释）  
> **对照基线**：「普通 app 仓库」= 少量 lint/test gate、单平面 CRUD、无 PIT/landing/accepted 分层、无独立 Rule 10、无 live-DB readiness 投影

---

## 0. 一句话结论

**进度慢的主因不是「门跑太慢」，也不是 Agent-OS 没做够——而是近端工作类型（transport strangler + Tier0 真相迁移）叠加编排仪式未收敛（L3 刀仍按 micro-commit、父 agent 串行、同步等 CI、subagent 空转），再被领域物理（≤40d、DuckDB 单写、双平面 inventory）放大。**  
项目**确实比普通 CRUD 难**，但「特别难」不足以解释墙钟差距的 **~60%**；其余是 **可改的 process/agent-ops**，不是放松 PIT 的借口。

---

## 1. Ranked 瓶颈（粗 attribution）

| 排名 | 类别 | 粗占比 | 机制（证据） | 与普通 CRUD 对比 |
|---|---|---:|---|---|
| **1** | **Process / 编排仪式** | **~32%** | eng_gov §15 T0：**L3 门 27.1s、CI ~1min**——机械门不是墙钟主因；ledger 1878–1880 行：**损失 = 每 micro-commit 一次 Rule 10、每 slice 同步 `gh run watch`、父→单子 agent 串行**。§15 已立法「刀级 Rule 10 + 异步 CI + 默认并行」，但 ledger 2057 / handoff §运维坑：**行为未完全落地**（仍见 staged-not-committed 长留、Multitask 激进串行）。 | CRUD：改完 push 一次 CI，无 moth/codegraph/Rule10 链 |
| **2** | **Domain physics / 领域物理** | **~28%** | **≤40d 硬窗**：daily 扩 2019→2026 需 **19×≤40d** land-then-accept（ledger 2481–2484）；非一次 backfill。**DuckDB 单写**：eng_gov §6/§15 — 同库写、provider job、stage→accept **必须串行**。**双平面**：S7 inventory **41/46 ssot**（ledger 2488–2489），每域 formal\|sunset 是边界迁移非删一行代码。**Landing purity + PIT**：acquire 全市场、universe 在读时；accept 原子链不可拆（plan §3.2–§3.3）。 | CRUD：单表 migrate；无 population/availability 正交轴 |
| **3** | **Inherent difficulty / 固有难度** | **~18%** | Tier0 量化底座：transport≠business 分层、resolver SSOT、策略 B0→B5 可证伪、真金白银门（mio #8）。近端主线 = **S1–S7 strangler**（plan §0），不是 greenfield feature。**正确性已部分 FIXED**（S1–S6、frontier 20260720），剩余是 **长尾退役 + E0 披露域**，单位刀 ROI 低于「加一个 API 字段」。 | CRUD：需求→模型→页面，无 accepted partition writer |
| **4** | **Policy / 治理政策** | **~14%** | **L3 默认面宽**：`backend/services/` 整体 L3（commit_tiers.yaml L33–34）；transport 刀几乎必 L3 **全 18 门**。**Rule 10 blocking**（§14）。**CI pytest SSOT ~100 文件**（ci_pytest_surface.yaml）— 离线面大但 **单次 ~17–27s 门时间仍非主因**；政策真正税的是 **「不敢合并 diff → 更多 commit 轮次」** 的心理+流程，不是单次 pytest 秒数。**WP6 影子期**：仪式 flip 未闭合 → agent 仍过度保守。 | CRUD：pre-commit 可选；无 tier 分类 fail-closed |
| **5** | **Agent-ops / 工具链** | **~8%** | **Subagent connection death**：goal 禁令 + handoff「2 行 transcript、无 tool_result」（Fable5/Opus/Sonnet/Shell）；**Multitask 串行放大空等**。**agent-boot 11.6s**（moth≈7s）— §15 明确 **不做 --fast**（moth 状态是 boot 价值）。DB cleanup（20260721 compact）**已减 I/O 摩擦**，不改变单写语义。 | CRUD：无 moth/codegraph/agent-boot |

**Occam 删掉的假瓶颈（有测量，非 vibes）** — eng_gov §15 / ledger 1892–1897：

- `agent-boot --fast`（省 ~7s，丢 moth 价值）
- L2 门集手术（17s 非痛点；改 `commit_tiers.yaml` = L3 + Rule10 + 影子 parity 风险）
- CI concurrency/cancel 机械
- 「门太多所以慢」— **单次 L3 27s << 等人+串行+多轮 commit 的分钟~小时级**

---

## 2. Agent-OS / Delivery-OS **修了什么** vs **修不了什么**

### 2.1 已 FIX（机械面 + 政策面）

| 交付 | 效果 | 证据 |
|---|---|---|
| **WP1 tiered safe_commit** L1/L2/L3 | 纯 docs/analysis **1.6s**；tests/routers **17s**；不再「改 README 也跑全门」 | ledger 1691–1698；T0 1870–1872 |
| **WP2–WP4** BOARD 生成、`agent-boot`、AGENTS 瘦身 | 启动/状态投影标准化；boot **11.6s** 可预期 | ledger 1707–1765 |
| **Delivery-OS §15** | 并行 subagent 默认、异步 CI、刀级 Rule 10、owner-doc 读一次 | eng_gov §304–336；ledger 1887–1891 |
| **CI L1 paths-ignore** | docs/board-only push **不跑 CI** | c473f5b4；test_ci_paths_policy |
| **ci_pytest_surface SSOT** | 本地 L2/L3 = public CI 同面；消灭「本地绿 CI 红」假安全 | ci_pytest_surface.yaml header；safe_commit 3.4 |
| **DB storage hygiene** | ~1.28GiB free-block reclaim；**不减** writer 串行 | db_storage_hygiene_20260721 |

### 2.2 刻意不能 / 不应 FIX（真相门）

- accept / PIT / calendar / fail-closed / cutover / E 门语义
- landing 不做 universe filter；population vs availability 正交
- DuckDB **单写**（不能为吞吐开第二 DB / plugin bus — goal 禁令）
- ≤40d / 禁 mass backfill（授权窗外历史必须分 chunk）
- grain/continuity **live-DB 投影**（L3 only；代码 commit ≠ Tier0 READY）
- Rule 10 **blocking** 本身（只能改 **粒度=刀**，不能 skip）

### 2.3 修了政策但 **墙钟仍慢** 的原因

Agent-OS 解决的是 **「错误类的慢」**（docs 全门、CI 无 pytest、状态散落）。  
当前慢的是 **「正确类的慢」**：

1. **工作队列** = S7 41 张 ssot 长尾 + E0 披露 BLOCKED 点（org_holding provider land），不是竖切 feature。
2. **编排 adoption gap** — §15 在纸上 FIXED，session 仍 micro-commit + 串行 + subagent 空转。
3. **领域物理下限** — 1829d daily accepted = 数十次 authorized chunk；这是 **日历/授权设计**，不是 bug。

---

## 3. 「项目特别难」是主故事还是 process 遮羞布？

### 3.1 难是真的（~18% attribution，不可假装 CRUD）

- **对象**：Tier0 金融真相 + PIT + 策略可证伪，不是 todo app。
- **架构**：landing → canonical → accepted → serve **四段 transport**，外加 legacy 平行面（plan §3.1）。
- **验收**：reject / `claimable=false` 是正式交付（F0–F3）；「没 lift」≠ 失败交付物。
- **operator 模型**：`manual_only`、frontier 跟墙钟、continuity BLOCKED ≠ 代码不能提交（goal Blocker）。

### 3.2 但「难」被 **过度叙事** 时会遮 process 债

| 若只说「难」 | ledger/§15 反证 |
|---|---|
| 「门太多跑不动」 | L3 **27.1s**；瓶颈在 **编排**（1878–1880） |
| 「PIT 所以啥都慢」 | PIT 主要增加 **设计+测试+审计时间**，不是每次 commit 多 20 分钟；**真正拖墙钟的是 19×40d 人为分刀 + 不敢合并** |
| 「Agent-OS 白做了」 | L1 **1.6s**、CI skip、pytest SSOT **已消除一类假慢**；剩余慢 **不在 WP0–4 范围** |
| 「只能 greenfield 重写才快」 | owner 已裁决 **strangler + 聚焦**（goal 76–78）；greenfield 会 **重付 PIT 债** |

**裁决（Mio #10 目标量 vs 诊断量）**：

- **主故事** = **strangler 长尾 + 编排未收敛**（process + domain physics ≈ **60%**）
- **「特别难」** = **真实背景约束（~18%）**，不是解释「比别的项目慢 3–5×」的全部理由
- 用「难」当 **不改 §15 行为** 的 cover = **糊弄**（mio 反模式：症状 patch / 不根治）

---

## 4. 下周可加速的 Top 3（**不**碰 PIT/integrity 禁令）

### 4.1 刀级合并 + §15 真执行（预期 **−30~40% 墙钟**）

**做**：

- 定义 **刀 = 逻辑单元**（例：单域 S7 formal\|sunset、单 CLI 面、单 E0 域 land 路径），**刀内允许多文件、一次 stage、一次 Rule 10、一次 safe_commit L3**。
- push 后 **不** `gh run watch`；开下一刀前 **只**回读该刀 CI verdict（§15 异步 CI）。
- 写集不相交的刀 **并行** subagent；共享面（goal/ledger/DuckDB/git commit）串行清单照 §15。

**不做**：减 Rule 10、减 L3 门集。

**验收**：下一周 ledger 条目里 **commits/knife ≤ 1.5**；无「同刀 3 commit 各审一次 Rule 10」。

### 4.2 刀前一次 impact 审计（预期 **−20% 返工**）

**做**（mio #11）：动 `backend/services`/删 config 前 **固定三联** — `moth coupling --impact` + `codegraph explore callers` + 最窄 pytest red-first；**一次规划、一次绿**，避免 ledger 历史「连崩 4 层 CI、6 commit 才绿」。

**不做**：跳过 post-fix-audit（PIT/schema 刀后仍 `$post-fix-audit`）。

### 4.3 队列按 ** formal\|sunset 域边界** 排刀，不按「顺手改一点」（预期 **+单位刀 merged 表数**）

**做**：

- S7：从 inventory **选 1–2 个 ssot 域**/周，刀内完成 formalize **或** sunset 证据 + test + legacy_raw_plane inventory shrink（目标：每周 inventory **−2~4 ssot**，不是 19×40d 再扫一遍）。
- E0：优先 **stk_holdertrade / holders_top10** provider land 已通路径上的 accept 扩窗；**org_holding** 保持 BLOCKED 文档化，不 fake progress。
- 数据扩窗：**batch 计划**（一次 session 连续跑 authorized chunks），不要「一日一 session 重启 boot+读 MASTER」。

**不做**：mass backfill；ST pre-2022 invent；第二 DB。

---

## 5. 明确 **不要** 做

| 诱惑 | 为何禁止 |
|---|---|
| **放松 accept/PIT/calendar/fail-closed** | 真金白银门；mio #7 不可自批绕过 |
| **skip / 软化 Rule 10** | §14 blocking；只能 **刀级一次**，不能关 |
| **greenfield 重写** | goal 禁令；重付 Tier0 债 |
| **扩 L2 含 `backend/services/`** | writer/PIT 机制全在内；会假绿 Tier0 |
| **`agent-boot --fast`** | §15 Occam-reject；丢 moth 状态 |
| **CI concurrency cancel 工程** | 测量非痛点；复杂度高 |
| **把 continuity BLOCKED 当「代码不能动」** | eng_gov §5 — 代码与 live readiness **分状态机** |
| **背景 Fable5 subagent 硬派** | handoff：2 行卡死；父会话或 `shell` 子代理 |
| **为吞吐开 plugin bus / 第二 DB** | goal 禁令 |
| **用「项目难」拒绝合并 commit** | 掩盖 §15 adoption gap |

---

## 6. 附录 — 证据索引

| 主题 | 位置 |
|---|---|
| T0 门耗时 | `docs/engineering_governance.md` §15 L306–311；ledger「2026-07-20 Delivery-tax knife」 |
| L1/L2/L3 政策 | `backend/config/commit_tiers.yaml`；§14 |
| CI pytest SSOT | `backend/config/ci_pytest_surface.yaml`；`scripts/safe_commit.sh` Step 3.4 |
| 编排诊断原文 | ledger L1878–1880 |
| Subagent 运维坑 | `analysis/account_switch_handoff_20260720.md` §运维坑；`goal.md` 禁令 |
| S7 19×40d / inventory | ledger L2481–2489 |
| Strangler 排序 | `analysis/plan_reeval_first_principles_20260720.md` §0 |
| DuckDB 单写 | eng_gov §6 L122；§15 L316–317 |
| Agent-OS 交付范围 | ledger WP0–WP6；goal L17–23 |

---

## 7. Status

**FIXED**（诊断交付）。

### 7.1 Adoption started（2026-07-21 owner-agreed execute）

| 项 | 落地 |
|---|---|
| §15 knife-merge binding | eng_gov §15.1 + `goal.md` + `AGENTS.md` boot pointer |
| 薄 enforcement | `agent-boot` delivery 提醒；`chunkyctl pre-knife <name>` |
| 刀前 impact | eng_gov §15.2 = moth coupling + codegraph explore once |
| S7 inventory | **41→36 ssot**（本刀 −5；membership/flow/identity/adj） |
| 未动 | L3/Rule10/PIT/≤40d；E/F paused；禁令不变 |

**next verification** = 2026-07-28 前一周 session 采样：`commits/knife`、Rule10 次数/knife、是否仍 sync CI watch、S7 inventory delta。

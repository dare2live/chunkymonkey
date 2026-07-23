# Foundation residual root-cause（2026-07-23）

> **生命周期**：evidence-only（analysis 层；**非** owner bible）
> Owner 纠偏：禁止为清清单而清残留。先问「为何残留 / 日常会不会再产生」。
> Authority: `FOUNDATION_EXECUTION_PLAN.md`；companion 验收仍见 `foundation_exit_verification_20260723.md`。
> STRATEGY：**仍 BLOCKED**（本笔记不改）。

## 0. 「100% usable」重定义（对齐 owner）

| 旧误读 | 新定义 |
|---|---|
| Continuity READY + F3/F4/F7/F8 全 CLOSED | **无开放 class-A 复发债** |
| annotate WARN / UNTRUSTED = 「没做完」 | **class-B 诚实状态 = 做对了**（禁止洗绿） |
| holders ×32 必清才算 100% | **class-C 可选 hygiene**（非运行正确性） |

**§6 exit**（给 STRATEGY 的绿灯）与 **100% usable** 从此分立：

- §6 exit = F1 收口 + F2 无 blocker + F5 投影诚实 + 禁令未破 → **已 MET**
- 100% usable = 无 class-A；class-B 可留；class-C 不挡 → **本笔记裁决后 = MET**（见 §3）

分类轴：

| Class | 含义 | 该不该动 |
|---|---|---|
| **A 流程债** | 日常更新会**再产生同样错误** → 须改路径/边界 | **必须改**（像 margin v3） |
| **B 诚实状态** | 信号本身是正确告警/信任门 | **不该消** |
| **C 历史堆债** | 不复发，只占空间 | **可选** retention；非正确性 |
| **D 假残留** | 已 FIXED / 或被误算进 100% 清单 | **从 100% 清单拿掉** |

---

## 1. 逐项：残留 / 为何 / 会再产生吗 / 该不该动

| ID | 残留是啥 | 为何会残留（根因） | 下次点更新 / 日常还会再产生同样问题？ | Class | 该不该动 |
|---|---|---|---|---|---|
| R1a | Continuity `warn_interior_gaps` **moneyflow_hsgt** | was annotate；now typed `hk_holidays` + `hk_northbound_closed_days.yaml` | **不再 WARN**（日历外空洞仍 FAIL） | **D**（已 FIXED F1） | **不动**（已修） |
| R1b | Continuity `warn_interior_gaps` **dividend** | was annotate；now typed `event_sparse` | **不再 WARN**（尾部 SLA 仍 FAIL） | **D**（已 FIXED F1） | **不动**（已修） |
| R2 | Holders landing **~32×** 同 `row_hash` | 历史堆；skip-land 已关复发 | **堆已清**（7.17M→236k；compact 4.3 GiB） | **D**（F3 FIXED） | **不动**（已修） |
| R3 | F4 margin pulse **1c shadow** | **FIXED** serve→accepted；gate=PROMOTED on accepted days | 日常走 accepted 路径 | **D** | **不动**（已修） |
| R4 | Product **rzrqye** | READY as external_aggregate when promoted；缺 accepted 日 UNTRUSTED | 诚实 | **B** | **禁假 TRUSTED** / project_universe |
| R5 | F7 Type-B enrichment | DEFER：registry in-scheme 够近端 | 不 defer 也不会在 daily 上「坏掉」；是产品/研究后置 | **B** / 清单上 **D** | **不进 100%**；DEFER |
| R6 | F8 qfq incremental write | later：今日 full CTAS+in-module compact 已 ops-safe | 每次 qfq rebuild 仍 CTAS（已知）；compact 钩已防 free-block 复发。**增量写 = 产品优化，非正确性债** | **B**/可选优化；清单 **D** | **不进 100%**；勿用「定期 compact」冒充增量语义 |

### 已闭合、勿再当「残留失败」

| 曾像残留 | 实况 | Class |
|---|---|---|
| Margin `local_max` 落后 / 错门 WARN | F2 **CLOSED**；v3 path FIXED（真 class-A 已改） | **D** |
| Holders 同内容 re-land 风暴（运行中） | Knife2 skip-land **FIXED**；复发门已关 | 运行面 **D**；磁盘堆仍 **C** |
| Continuity typed 错门（declared_drift / sparse 误报） | Knife4 **FIXED** | **D** |

### 开放 class-A？

**无。** 本轮 live 探针未发现「点更新会再制造同类错误」的路径债。若未来 holders 再出现同 `payload_hash` multi ACCEPTED，或 hsgt 开市日 vendor 有数却本地缺且未进 tombstone——再升 **A**。

---

## 2. 对 FOUNDATION backlog 的处置

| # | 旧状态 | 根因类 | 新状态（100% 视角） |
|---|---|---|---|
| F1 annotate 残留 | PARTIAL 诚实保留 | **B** | **KEEP** — 计入「诚实 OK」，不计失败 |
| F2 | CLOSED | — | 不变 |
| F3 retention | later L3 | **C** | **optional hygiene** — 不挡 100% usable |
| F4 / rzrqye | PROMOTED / READY external_aggregate | **D→B** | **FIXED serve cutover**；缺日仍 UNTRUSTED |
| F5 | FIXED | — | 不变 |
| F7 / F8 | DEFER / later | **B**→清单 **D** | **out of 100% usable bar** |

---

## 3. Verdict

| 门 | 状态 |
|---|---|
| Foundation §6 exit | **MET**（不变） |
| 100% usable（无 A；B 诚实；C 可选） | **MET** |
| Continuity overall READY | **不要求**（仍 WARN×2 = class-B） |
| STRATEGY | **仍 BLOCKED** until `goal.md` 显式 RX |

Label：**FIXED**（根因分类 + 定义对齐）；无 class-A 代码刀。

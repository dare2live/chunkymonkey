# Opus 接手硬化纲领 — Fable 5 离场前 10 天交付清单 (2026-06-12 → 06-22)

> 背景: 用户 2026-06-22 后无法继续以订阅方式使用 Fable 5, 项目日常将由 Opus 档模型接手。
> 本文件 = 离场前框架硬化的唯一纲领。第一性原理: **降级期实证失败模式 → 逐条机械化防线**,
> 把"需要智力的判断"前置成"机械可检的不变量 + 流程强制点", 让弱一档的模型按图施工即可
> 保持水准。完成一项勾一项, 06-21 做离场对账。

## 0. 设计依据: Opus 实证失败模式 → 防线类型

两次降级期 + 全部反例表的缺陷归类 (全部真实付过代价):

| 失败模式 | 实例 | 防线类型 |
|---|---|---|
| 语义/实测类错误 (字段方向/时区/单位) | dc_member 方向反 (7 测全绿照样反) / UTC 乌龙 x2 | 机械不变量 (语义探针) |
| 轻信声称 (文档/自己的修复) | "validation 全 PASS" 验收尺盲区 / "写入面仅 4 表" 实为 165 | claims 对账 + 对抗复审 |
| 静默数据缺陷 | dc_member 整 5000 截断 / 日历 clamp 2005-2022 零告警 | 形态侦测断言 |
| 守门自身失效 | print-not-fail / 孤儿 checker / 空弹仓 | 守门有效性审计 |
| 多步推理丢约束 | 门柱挪动 / 预注册条款被遗忘 | 预注册成文 + go 前置清单脚本化 |
| 不复查自己 | post-fix 残留 / INDEX 不同步 | commit hooks (已有) + degraded 协议 |

## 1. 交付清单 (P0 = 06-15 前, P1 = 06-19 前, P2 = 06-21 前)

### A. 不变量机械化 (moth + 项目 gates)

- [x] **A1 assertion-pack 引擎 + 首个 claims 弹仓** (06-12 完成: moth 1aed3e6 + 本仓 19052ad2,
      8 断言 6 PASS; dc_member 截断断言 = 真实 red→green 演示)
- [ ] **A2 (P0) 反例→check 映射表**: CLAUDE.md §4.5 每条反例标注对应 check (已有/弹仓新增/无法机械化),
      "无法机械化"项显式列出 = Opus 期间的强制对抗复审触发器。映射表本身入 claims 弹仓目录 README。
- [ ] **A3 (P0) 语义探针弹仓** `.moth/assertions/semantic_probes.yaml`: per-table 键形态
      (概念键 BK 模式)/时间戳时区契约/单位量级带/整千 pin (全部 page_limit 域)/
      min(date) vs data_start 对账 (全部 by_trade_date 域, 防 clamp 复发)。
- [ ] **A4 (P1) moth 第二刀 — 守门有效性审计**: 枚举 checker/test → (a) 有调用方 (grep 接线)
      (b) 物理可红 (对已知坏样本 FAIL)。抓 print-not-fail / 孤儿 checker / 空弹仓。
- [ ] **A5 (P1) `moth doctor` 进 safe_commit**: Step 2.x 跑弹仓, FAIL 挡 commit (告警疲劳防线:
      只挡 fail/error, 不挡 codegraph WARN)。

### B. 决策面文档化 (把总指挥的判断写成可执行契约)

- [ ] **B1 (P0) LF V0 + LHB 退出预注册成文冻结** (16 combo 底稿未持久化, 数值判据/样本窗/
      成败线/判负处置全部跑前冻结; 模板化 = 后续实验照抄结构)。
- [ ] **B2 (P0) go 前置清单脚本化**: X 轨 G1-G6 两套 gate 写成 `scripts/experiment_gates.py`
      (read_only, 输出 PASS/FAIL 表) — Opus 跑实验前一条命令判 "能不能开跑", 不靠读文档。
- [ ] **B3 (P1) Playbook 三份** (docs/playbooks/): ① chain 发射检查单 (chain9 链首自检模式
      固化: 日历/写锁/min_rows/探底) ② 实验跑前 grill 模板 (三问 + 预注册检查) ③ 事故诊断
      决策树 (断流/截断/clamp/锁冲突 四类的第一刀命令)。
- [ ] **B4 (P2) goal.md/作战图状态机器可读化**: 状态行尽量挂 data-status/doctor/moth 命令,
      Opus 接手"先跑三条命令拿全状态", 不读长文档。

### C. 流程强制点

- [ ] **C1 (P0) degraded 协议写进 CLAUDE.md §8.25 升级版**: Opus 期间 (a) commit 自动带
      `model-context: degraded` (hook 检测模型名) (b) 数据语义/策略/资金路径三类改动强制走
      对抗复审 workflow (c) moth doctor verdict 贴 commit message。
- [ ] **C2 (P1) 对抗复审 workflow 模板固化**: 本周实证有效的 调查→对抗核验→完备性审查 三段
      workflow 脚本存 `.claude/workflows/` + 使用说明 — Opus 可直接 resume/调用, 不必重新设计。
      注意 mythos 教训: verifier 同模型同盲 → 模板里确定性检查 (弹仓/gate 脚本) 承担主力,
      agent 只做语义层。
- [ ] **C3 (P2) probed_* 回写纪律**: 每次接口实弹把状态回写 catalog (脚本已支持), 杜绝
      "171 ok 名单不可查" 复发。

### D. 数据面收口 (Opus 接手时数据底座必须是"全绿或显式标黄")

- [ ] **D1 (P0) chain9 + 9.5 完成**: LHB gate 解锁 / dc_member 干净重拉 / 7 域转正 /
      失败队列僵尸单清零。
- [ ] **D2 (P0) E7 退役收尾**: observed vs reconstructed 对账 → 退役记录入 ledger。
- [ ] **D3 (P1) index_daily + index_member_all 落库** (KPI 超额主源转正, 申万 L2 PIT)。
- [ ] **D4 (P1) modal 供给线**: smoke 覆写修复 → cyq_chips 60 call 口径对照 (预注册) →
      (条件) 坐标换算层 + modal 全市场复算。
- [ ] **D5 (P2) chain11/12 按图执行或显式排期移交** (dc_daily; 财务三表+disclosure_date)。

### E. 离场对账 (06-21)

- [ ] **E1 全弹仓绿 / doctor 绿 / 测试基线绿** 三绿快照入 ledger。
- [ ] **E2 SESSION_HANDOFF + quickstart 按 Opus 视角重写启动序**: 三条命令拿状态 →
      读作战图 → 按 playbook 干活。
- [ ] **E3 mythos / memory 增量沉淀本 10 天新教训**。

## 1.5 实用工具清单 (用户 06-12 晚点名 "留下类似 moth 的实用工具", 与上面条目互为索引)

| 工具 | 形态 | 一句话 | 对应条目 |
|---|---|---|---|
| `moth doctor` + assertion 弹仓 | 已交付 | 声称-实况对账, 文档腐烂/schema 回归/截断签名一条命令 | A1 |
| `cm_takeover_audit` | 新脚本 (P0) | **接手对账器**: alert flags + git + doctor + data-status + moth + chain 日志尾 → 单页 verdict; 新 session 第一条命令, 把本周 13-agent 人肉对账的确定性部分一键化 | 新增 |
| `experiment_gates` | 新脚本 (P0) | 实验 go/no-go 跑批器: gate 写 YAML (SQL/shell 判据), 跑前一条命令出 PASS/FAIL 表, 防"门柱挪动/带病开跑" | B2 |
| moth 守门有效性审计 | moth 第二刀 (P1) | 每个 checker/test 验 (a) 有调用方 (b) 物理可红 — 抓 print-not-fail/孤儿守门/空弹仓 | A4 |
| diff 风险分类器 | hook (P1) | staged diff 按路径+模式规则 (YAML) 分类 数据语义/策略/资金路径, 高风险类强制对抗复审标记, 机械化 degraded 协议 | C1 |
| 对抗复审 workflow 模板 | .claude/workflows (P1) | 调查→对抗核验→完备性审查 三段式固化, Opus 直接调用; 确定性检查为主力, agent 只做语义层 | C2 |
| 语义探针弹仓 | YAML (P0) | 键形态/时区/单位带/整千 pin/min-date 对账, 反例驱动逐条扩 | A3 |

## 2. 明确不做的 (奥卡姆)

- 不做"Opus 专用降智版文档" — 文档已经是契约化的, 弱模型需要的是更少自由发挥面, 不是更多解释。
- 不做新框架/新抽象 — 全部交付物复用既有原语 (moth/registry/hooks/workflow), 只补缺口。
- 不提前做 chain10/hm_detail 等条件触发项 — 条件没到就是不跑, Opus 接手后按 gate 判。

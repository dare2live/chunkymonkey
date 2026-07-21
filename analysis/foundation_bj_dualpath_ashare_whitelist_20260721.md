# BJ / dual-path / 沪深A whitelist knife (2026-07-21)

> Status: evidence-only
> Label: FIXED (serve whitelist + formal continuity) / PARTIAL notes below  
> Authority: RCA `foundation_daily_update_degraded_rca_20260721.md` (`4bdfdeaf5`), AGENTS Tier0, goal.md

## Q&A（白话）

### 1. 根因是什么？以前排除列表为何失效？

白名单定义早就在 `universe_rules.yaml`（`60/00/30/68`）。失效不是「漏写 90/20 denylist」。

真正断裂：

1. A4 landing 纯度后，`universe_filter: true` **不再删行**，只校验列形状。  
2. 注释说过滤改到 serve 的 `apply_universe_serve_filter`。  
3. **生产路径从未调用**该函数（仅单测）。  
4. `market_pulse` / qfq / 成分下钻直接读 unfiltered canonical + `dc_index.leading_*`。

证据：canonical 有 328 BJ；B股不在 daily，却从 `dc_index.leading_code` 进 pulse（当晚约 B10+BJ27）。

`excluded_boards` 缺 90/20 只影响分类文案，不是感知漏过滤根因。

### 2. 白名单打在哪一层？

**项目分析/serve 面**（不是 landing，也不改 `raw_evidence` accept 语义）：

- qfq CTE  
- pulse 广度 / leading_code / 成分与资金流 JOIN  
- pulse 成分 API  

契约：landing 可含 BJ；accept 保留供应商证据；沪深A 产品面必须白名单。

### 3. continuity：修还是降级？

**修。** formal accepted（`20260721`）是真相；legacy raw MAX=`20260716` 是 strangler 旁路观察者。continuity/SLA 改判 `accepted_partition`。

### 4. share_float 裸码 / ths_hot 空行

- share_float `874075`：形状门正确 fail-closed；本刀规范化为 `874075.BJ`，**不放宽**正则。  
- ths_hot `20260721`：跑批 21:53–22:08 早于 `available_after=22:30`；属发布窗/源端空，非解析 bug；不假 tombstone，22:30 后再 drain。

## FIXED / PARTIAL

| Knife | Status |
|---|---|
| B/BJ 感知漏出 → serve 白名单 | FIXED |
| dual-path continuity/SLA | FIXED |
| share_float 裸 BJ | FIXED |
| ths_hot 当日空 | PARTIAL（已解释） |
| BJ 仍可在 accepted raw_evidence | by design |

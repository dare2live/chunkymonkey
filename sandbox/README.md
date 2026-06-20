# sandbox/ — 隔离探索区 (用完直接删, 不污染主代码/文档)

> 立 2026-06-17 (用户: "建一个隔离的区域, 实验结束后直接可删, 专门用于探索, 而不是把各种
> 文档和代码并去其他代码和文档里")。
> **根因**: 探索的脚本 / findings 草稿 / 中间结果 散进 `backend/scripts/` + `analysis/` +
> `docs/` → 反复污染主代码与文档 → 反复大清理 (本对话清了 ~100 文件)。
> **解法**: 所有 ephemeral 探索 (脚本 / 草稿 / 结果 / scratch 数据) 只住这里, gitignored,
> 一条命令全删。唯一跨删存活的 = `experiment_store.duckdb` 里的裁决。

## 铁律 (探索物理上进不了主代码/文档/git)

1. **探索脚本** (一次性 runner: 扫因子 / 试 backtest / 调参探路) → `sandbox/<exp>/`,
   **绝不进 `backend/scripts/`**。
2. **草稿 / findings** (中间想法 / 结果笔记) → `sandbox/<exp>/notes.md`,
   **绝不进 `analysis/` 或 `docs/`**。
3. **中间结果** (json / csv / 图) → `sandbox/<exp>/results/`,
   **绝不进 `analysis/` 或 `data/reports/`**。
4. **scratch 数据** → `sandbox/<exp>/scratch.duckdb` (per-exp, 随探索一并 wipe), **不碰 6 个主库**
   (smartmoney / market / tushare_raw / feature_store / etf / experiment_store)。
5. **边界水密 (运行时硬门, 非约定)**: 探索脚本首行
   `from services.sandbox_guard import enable_sandbox_guard, read_only_main, sandbox_scratch; enable_sandbox_guard()` —
   此后 read_write 打开主库 = `raise SandboxBoundaryError`。读主库用 `read_only_main("market")`,
   写探索数据用 `sandbox_scratch("<exp>")`。`scripts/sandbox.sh new <exp>` 生成的 probe.py 模板已带。
6. **gitignored**: `sandbox/` 整个不进 git (除本 README)。

## 用完直接删 (重点)

```bash
bash scripts/sandbox.sh wipe <exp>     # 删一个探索
bash scripts/sandbox.sh wipe-all       # 删全部 (留 README) + scratch.duckdb
rm -rf sandbox/<exp>                    # 等价
```

删 sandbox 后, 主代码 / 文档 / git **零残留**。

## 唯一留存的 (跨 wipe 存活)

1. **裁决** → `experiment_store.duckdb` (`services.experiment_store.record_verdict`, 隔离
   verdict 库, 不污染 live)。探索结论 (PASS/REJECT + 关键数字 + prereg_hash) 写这里。
2. **(可选) 1 行 ledger** → `analysis/project_state_ledger.md` (只 1 行: 试了啥 / 结论 / 日期)。
3. **promotion**: 若探索找到真 edge → 验证逻辑 **干净重写** 进 `backend/services/` + 单测
   (经评审), 不是从 sandbox copy 脚本。

## 生命周期

```
explore (sandbox/)  →  record_verdict (experiment_store)  →  wipe sandbox/
                                                              ↓ (仅真 edge)
                                          promote: 重写进 backend/services + 单测
```

## promotion 纪律 (2026-06-21 立, 4+ 次隔离失守根治)

> 反例 (本次根因): sandbox **脚本**隔离了, 但探索弧的**产物** (主库表 / backend builder /
> 控制面文档 KPI / 裁决) 在**方法确认前**就 promote 进主项目 → 一条**跑偏弧**的残留污染主代码/文档/库,
> wipe sandbox 后主项目仍一堆残留。隔离=脚本进不去**不够**, 产物也不许漏。

**探索弧期间 (方法未确认)**: 产物**全留 sandbox** —
派生数据 → `sandbox/<exp>/scratch.duckdb` (**不往主 6 库建表**);
findings/草稿 → `sandbox/<exp>/notes.md` (**不往控制面文档 goal/INDEX/hunter 写 KPI 结果**);
runner → `sandbox/<exp>/` (**不进 backend/scripts**)。
唯一可跨 sandbox 写的 = `experiment_store.record_verdict` (探索裁决, confirmed_by_owner=0)。

**promotion (单独 gated 步, 方法确认后)**: 只有真 edge 才 promote —
干净重写进 `backend/services/` + 单测 → 主库建表 (builder 进 backend/scripts) → 控制面文档引用 →
`record_verdict(confirmed_by_owner=1)` (须带含成本+leakage证据, C-R1/C-LEAK 闸)。
**控制面文档只许引用 confirmed_by_owner=1 的结论, 不嵌探索期 (=0) 结果。**

## 执法

- `.gitignore`: `sandbox/` (除 README) — 探索进不了 git。
- **`check_sandbox_isolation.py`** (C1/C2/C3, wired into `sandbox.sh check` + `safe_commit` Step 3.8):
  - C1 (FAIL): `backend/` 代码引用 `sandbox/` (测试码漏进主代码);
  - C2 (WARN): 控制面文档 (goal/INDEX/CLAUDE/AGENTS/hunter/data_validation) 嵌入未 promote
    (confirmed_by_owner=0) 的 experiment_store run_id;
  - C3 (FAIL): `backend/scripts/` 有 `experiment_*` / `analyze_*` 探索 runner。
- moth `exploration-isolated-in-sandbox`: 同 C3 (它们该在 sandbox)。
- 主库写锁隔离 (运行时硬门): `sandbox_guard` — 探索 read_write 打开主 6 库 = raise; scratch 用 `sandbox_scratch()`。

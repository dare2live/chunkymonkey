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
4. **scratch 数据** → `sandbox/scratch.duckdb` (随便建表玩), **不碰 6 个主库**
   (smartmoney / market / tushare_raw / feature_store / etf / experiment_store)。
   读主库一律 `read_only=True`。
5. **gitignored**: `sandbox/` 整个不进 git (除本 README)。

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

## 执法

- `.gitignore`: `sandbox/` (除 README) — 探索进不了 git。
- moth `exploration-isolated-in-sandbox`: `backend/scripts/` 0 个 `experiment_*` / `analyze_*`
  探索 runner (它们该在 sandbox)。`bash scripts/sandbox.sh check` 本地同检。
- 主库写锁隔离: 主 6 库给探索只读; scratch 用 `sandbox/scratch.duckdb`。

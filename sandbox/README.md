# sandbox/ — 可整体删除的隔离探索区

这里仅承载一次性 runner、草稿、中间结果和 scratch 数据。它不是 Tier3 research runtime，
也不拥有项目结论；当前架构与验证契约以 `docs/` 下的 owner 文档为准。

## 边界

1. 一次性扫描、回测探路和参数试验只放 `sandbox/<exp>/`，不进入 `backend/scripts/`。
2. 草稿只放 `sandbox/<exp>/notes.md`，不进入 `docs/` 或 `analysis/`。
3. 中间 JSON/CSV/图只放 `sandbox/<exp>/results/`，不进入正式报告目录。
4. scratch 写入 `sandbox/<exp>/scratch.duckdb`；manifest 管理的主库一律只读。
5. 探索脚本先启用运行时边界：

```python
from services.sandbox_guard import enable_sandbox_guard, read_only_main, sandbox_scratch

enable_sandbox_guard()
```

读主库用 `read_only_main("market")`，写探索数据用 `sandbox_scratch("<exp>")`。
直接 read-write 打开主库必须抛出 `SandboxBoundaryError`。

## 生命周期

```text
explore in sandbox
  -> independent validation against the strategy contract
  -> REJECT: wipe everything
  -> candidate worth keeping: rewrite as a versioned Tier3 package + tests + evidence
  -> Rule 10 review
  -> only then may a governed ExperimentRun/StrategyRelease be created
```

当前仓库没有已闭合的 ExperimentRun/StrategyRelease writer。历史
`fact_experiment_verdict` 表只是 evidence schema，不能把不存在的 `record_verdict` 当执法点，
也不能从 sandbox 直接写主库或把探索结果称为已发布结论。

## 清理

```bash
bash scripts/sandbox.sh wipe <exp>
bash scripts/sandbox.sh wipe-all
```

`sandbox/` 整体被 gitignore（仅保留本 README）。如果某项结果确实值得保留，先按
`docs/strategy_validation_contract.md` 重做成有 snapshot/config hash、PIT、成本、执行约束和
独立复核的正式 evidence；不能复制探索脚本或仅留一条聊天结论。

## 门禁

- `check_sandbox_isolation.py`：主代码引用 sandbox、探索 runner 漏进 backend、主库写边界。
- `scripts/sandbox.sh check`：本地隔离检查。
- `scripts/safe_commit.sh`：提交前调用隔离门和 Rule 10。
- Moth `exploration-isolated-in-sandbox`：防探索 runner 回流。

任何门禁 PASS 都只证明隔离边界，没有证明策略有效或数据可发布。

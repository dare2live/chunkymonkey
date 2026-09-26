# sandbox/ — 可整体删除的隔离探索区

这里只放一次性脚本、草稿、中间结果和 scratch 数据，不拥有项目结论；项目目标与规则以根目录 `../goal.md` 与 `../CLAUDE.md` 为准。

## 边界

1. 一次性扫描、探针和试算只放 `sandbox/<exp>/`，不进 `backend/scripts/`。
2. 草稿只放 `sandbox/<exp>/notes.md`；仓库跟踪的 md 只有 `backend/config/tracked_doc_allowlist.yaml` 登记的那几份，探索笔记不进仓库。
3. 中间 JSON/CSV/图只放 `sandbox/<exp>/results/`。
4. scratch 写入 `sandbox/<exp>/scratch.duckdb`；manifest 管理的主库一律只读。DuckDB 跨进程读与写互斥：日更或其它写任务在跑时不打开主库，长时间分析优先读只读快照。
5. 探索脚本先启用运行时边界：

```python
from services.sandbox_guard import enable_sandbox_guard, read_only_main, sandbox_scratch

enable_sandbox_guard()
```

读主库用 `read_only_main("market")`，写探索数据用 `sandbox_scratch("<exp>")`。
直接 read-write 打开主库必须抛出 `SandboxBoundaryError`。

## 从探索到正式代码

```text
在 sandbox 探索
  -> 不值得落地：sandbox.sh wipe
  -> 值得落地：写方案（目的、验收判据、影响面、重复与矛盾、边界情况、关键条件测试计划）
  -> 在 backend/ 按方案重写并带测试（不复制探索脚本）
  -> 审查只核验收判据与方案未声明的冲突（CLAUDE.md 第 19 条）
  -> safe_commit 提交；要生产验收的，在第一次真实运行上对账
```

探索结果不能从 sandbox 直接写主库，也不能把探索结论称为已发布结果；值得保留的只能按上面的路径重写进 `backend/`，不能只留一条聊天结论。

## 清理

```bash
bash scripts/sandbox.sh wipe <exp>
bash scripts/sandbox.sh wipe-all
```

`sandbox/` 整体被 gitignore（仅保留本 README）。

## 门禁

- `backend/scripts/check_sandbox_isolation.py`（safe_commit 的 `sandbox_isolation` 门）：backend/ 引用 sandbox/、探索 runner（`experiment_*` / `analyze_*`）漏进 backend/scripts/。
- `services.sandbox_guard`：运行时挡住 read-write 打开主库。
- `scripts/sandbox.sh check`：本地隔离检查。
- Moth `exploration-isolated-in-sandbox`：防探索 runner 回流。

任何门禁 PASS 都只证明隔离边界，不证明结论成立或数据可发布。

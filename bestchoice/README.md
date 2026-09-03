# BestChoice frozen challenger

BestChoice 不是可运行应用、生产策略或独立数据平台；它是 ChunkyMonkey
Tier 3 的冻结公式 challenger 证据包。

当前只保留：

- 五个历史公式实现：`formula_engine.py`；
- 历史执行口径：`execution_model.py`（仅用于解释旧结果）；
- 两个无外部数据依赖的回归 smoke；
- `analysis/` 下最小、不可变的历史机器证据。

边界、hash、证据含义和重新接入条件只看 [FROZEN.md](FROZEN.md)。主项目的
现行研究规则以 `../CLAUDE.md` 为准。

窄验证（不读取市场库、不启动研究任务）：

```bash
PYTHONDONTWRITEBYTECODE=1 python scripts/verify_frozen_evidence.py
PYTHONDONTWRITEBYTECODE=1 python scripts/formula_engine_smoke.py
PYTHONDONTWRITEBYTECODE=1 python scripts/execution_model_smoke.py
```

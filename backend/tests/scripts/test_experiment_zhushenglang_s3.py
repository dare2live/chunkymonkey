"""主升浪 S3 — 预注册一致性 + 泄漏防线常量守门."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "backend" / "scripts" / "experiment_zhushenglang_s3.py"
VENV_PY = str(REPO / ".venv" / "bin" / "python")
SUB_ENV = {**os.environ, "PYTHONPATH": str(REPO / "backend")}


def test_prereg_consistency_machine_check():
    r = subprocess.run([VENV_PY, str(SCRIPT), "--check-prereg"],
                       capture_output=True, text=True, env=SUB_ENV)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def test_leakage_guards_frozen():
    """泄漏防线常量必须在脚本里 (embargo 180 / label 排除 / 固定超参不判决轮 Optuna)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("s3", SCRIPT)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert m.EMBARGO_DAYS >= 180, "embargo 必须 >=180 交易日 (label horizon, 防重叠标签泄漏)"
    # forward_ret / close / built_at / 键 必须在排除集 (label 或非因果)
    for c in ("forward_ret_5d", "forward_ret_20d", "forward_ret_90d", "close", "built_at", "stock_code", "date"):
        assert c in m.EXCLUDE_COLS, f"{c} 必须排除出特征 (label/非因果/泄漏)"
    assert m.PREREG["auc_ceiling"] == 0.75, "异常高 leakage 红线冻结 0.75"
    assert m.LGBM_PARAMS.get("random_state") == 20260613, "固定种子 (可复现)"

"""G3 门#1 散落死 checker 测试 — 红绿实证 (合规过/裸跑红/豁免过)。"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "check_experiment_harness", REPO / "backend" / "scripts" / "check_experiment_harness.py"
)
ch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ch)


def test_compliant_via_harness_import():
    assert ch.is_compliant_text(
        "from services.phaseD_signal_eval import evaluate_signal\n...", "experiment_x.py"
    )


def test_compliant_via_store_api():
    assert ch.is_compliant_text(
        "from services.experiment_store import record_verdict, open_store\n...", "experiment_y.py"
    )


def test_naked_script_fails():
    """裸跑脚本 (只 duckdb+json, 不留档) 必须红 — 这是门要拦的。"""
    naked = "import duckdb\ncon = duckdb.connect('x.duckdb')\nimport json\njson.dump(r, open('analysis/x.json','w'))\n"
    assert not ch.is_compliant_text(naked, "experiment_naked.py")


def test_exempt_passes_even_naked():
    """豁免名单内即使裸跑也过 (但豁免须显式 + 理由)。"""
    ch.EXEMPT.add("experiment_exempt.py")
    try:
        assert ch.is_compliant_text("naked no token", "experiment_exempt.py")
    finally:
        ch.EXEMPT.discard("experiment_exempt.py")


def test_all_real_experiments_compliant():
    """实测仓内全部 experiment_*.py 当前应 100% 合规 (门落地前提)。"""
    files = ch._all_experiment_files()
    assert files, "未找到 experiment_*.py"
    missing = [f.name for f in files if not ch._is_compliant(f)]
    assert not missing, f"裸跑脚本 (应先修或豁免): {missing}"

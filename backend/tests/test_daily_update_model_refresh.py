from __future__ import annotations

import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "daily_update.sh"


def test_daily_update_shell_syntax_valid():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], cwd=REPO, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_step4_model_refresh_branches_and_cost_control_are_wired():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "CHUNKY_DOW_OVERRIDE" in text
    assert "run_backtest_validation_gate" in text
    assert 'run_all_gates("daily_update_model_refresh")' in text
    assert 'if [[ "$DOW" == "1" ]]' in text
    assert "bash gcp/vm_start.sh" in text
    assert "run_lambdamart_v6_retrain_on_vm" in text
    assert "--n-trials 50" in text
    assert "--full" in text
    assert "bash gcp/vm_stop.sh" in text
    assert "stop_model_refresh_vm" in text
    assert 'elif [[ "$DOW" -ge 2 && "$DOW" -le 5 ]]' in text
    assert "use cached LambdaMART v6 model" in text

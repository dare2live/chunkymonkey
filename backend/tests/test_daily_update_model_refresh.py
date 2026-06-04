from __future__ import annotations

import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "daily_update.sh"


def test_daily_update_shell_syntax_valid():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], cwd=REPO, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_step4_model_refresh_branches_and_cost_control_are_wired():
    """Verify Step 4 wired with event-driven (alpha_decay) + quarterly fallback (DOM=1 Jan/Apr/Jul/Oct).
    GCP retrain 改全手工触发 (user push back 2026-05-18), 不在 daily_update 自动调.
    旧 cron Monday DOW=1 + GCP auto retrain 已 deprecated, 替换 IS_QUARTER_START.
    """
    text = SCRIPT.read_text(encoding="utf-8")

    # Pre-flight + gate + variables
    assert "CHUNKY_DOW_OVERRIDE" in text  # DOW still computed for general use
    assert "run_backtest_validation_gate" in text
    assert 'run_all_gates("daily_update_model_refresh")' in text

    # Event-driven + quarterly fallback (replaces old Monday cron)
    assert "ALPHA_DECAY" in text  # event-driven trigger
    assert "IS_QUARTER_START" in text  # quarterly fallback
    assert 'DOM' in text  # day-of-month check

    # GCP retrain 改手工触发 — 文档化提示用户手工跑命令
    assert "run_phase5_extended_retrain.sh" in text or "retrain_lambdamart_v6.py" in text


def test_profit_forecast_snapshot_also_refreshes_live_shadow_mart():
    """Daily update must keep raw forecast and live shadow mart on the same PIT date."""
    text = SCRIPT.read_text(encoding="utf-8")

    raw_command = (
        "backend/scripts/ingest_profit_forecast_snapshot.py \\\n"
        '            --snapshot-date "$FORECAST_SNAPSHOT_DATE"'
    )
    mart_command = (
        "backend/scripts/compute_forecast_upside_live.py \\\n"
        '                --snapshot-date "$FORECAST_SNAPSHOT_DATE"'
    )
    raw_pos = text.index(raw_command)
    mart_pos = text.index(mart_command)

    assert "FORECAST_SNAPSHOT_DATE=$(date +%Y-%m-%d)" in text
    assert raw_pos < mart_pos
    assert "skip forecast_upside live mart" in text

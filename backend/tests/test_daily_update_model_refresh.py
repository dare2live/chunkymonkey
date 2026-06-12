from __future__ import annotations

import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "daily_update.sh"


def test_daily_update_shell_syntax_valid():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], cwd=REPO, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_step4_model_refresh_branches_and_job_contract_are_wired():
    """Verify Step 4 wired with event-driven (alpha_decay) + quarterly fallback (DOM=1 Jan/Apr/Jul/Oct).
    Heavy retrain 改全手工触发 (user push back 2026-05-18), 不在 daily_update 自动调.
    旧 cron Monday DOW=1 + auto retrain 已 deprecated, 替换 IS_QUARTER_START.
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

    # Heavy retrain 改手工触发，并先登记 provider-neutral job plan.
    assert "scripts/chunkyctl jobs --family model_training" in text
    assert "retrain_lambdamart_v6.py" in text
    assert "CHUNKYMONKEY_GCP" not in text


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


def test_watermark_refresher_wired_after_data_steps():
    """Step 2.97 watermark 刷新器接线防回退.

    根因反例 (2026-06-13): 调度手动化时 cron_daily phase_watermarks 孤儿化 — Step 1 SLA
    检查器只读水位从不写, kline_daily 水位卡死 06-03 而 price_kline_tdxhub 实际到 06-12。
    检查器与刷新器是两个器官, 链里必须都有, 且刷新器在数据步之后。
    """
    text = SCRIPT.read_text(encoding="utf-8")
    assert "refresh_source_watermarks.py" in text  # 刷新器在链
    # 刷新器必须在 drain (最后一个数据步) 之后, panel 重建之前
    drain_pos = text.index("sync_registry drain")
    refresh_pos = text.index("refresh_source_watermarks.py")
    panel_pos = text.index("Step 3: Label")
    assert drain_pos < refresh_pos < panel_pos, "刷新器位置错: 必须在数据步后 panel 前"
    # 失败必须送达 (step_degraded), 不许静默
    step_block = text[refresh_pos:refresh_pos + 400]
    assert "step_degraded" in step_block

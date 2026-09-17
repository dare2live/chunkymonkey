"""backend/services/pipeline/evidence_paths.py — typed loader for evidence path templates.

store.py / preflight.py / chain_run.py 共用同一份路径模板 (CLAUDE.md #11: 规则进
typed YAML 不 hardcode); 本文件锁住 loader 的 fail-closed 边界。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.pipeline.evidence_paths import (
    PipelineEvidencePathsError,
    load_pipeline_evidence_paths,
)


def test_loads_real_repo_config_without_monkeypatching():
    """至少一条测试读真实配置文件 (不 monkeypatch) —— 锁住 store.py/preflight.py 实际消费的值。"""
    paths = load_pipeline_evidence_paths()
    assert paths.daily_report_path_template == "data/reports/daily_{date}.json"
    assert paths.watermark_sla_path_template == "data/audit/watermark_sla_{date}.json"
    assert (
        paths.watermark_sla_before_path_template
        == "data/audit/watermark_sla_before_{date}.json"
    )
    assert paths.daily_report_rel(date="20260912") == "data/reports/daily_20260912.json"
    assert paths.watermark_sla_rel(date="20260912") == "data/audit/watermark_sla_20260912.json"
    assert (
        paths.watermark_sla_before_rel(date="20260912")
        == "data/audit/watermark_sla_before_20260912.json"
    )
    # daily_update_log_name_template 必须与 context.py::PipelineContext.__post_init__
    # 手写的 f"chunkymonkey_daily_update_{date}.log" 逐字节相同 (context.py 尚未接这份
    # loader —— 见 pipeline_evidence_paths.yaml 里的说明); 这条断言就是那份「同步」的锁。
    assert paths.daily_update_log_name_template == "chunkymonkey_daily_update_{date}.log"
    assert paths.daily_update_log_name(date="20260912") == "chunkymonkey_daily_update_20260912.log"


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "pipeline_evidence_paths.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_file_fails_closed(tmp_path):
    with pytest.raises(PipelineEvidencePathsError, match="missing"):
        load_pipeline_evidence_paths(tmp_path / "does_not_exist.yaml")


def test_wrong_version_fails_closed(tmp_path):
    p = _write(
        tmp_path,
        """version: 2
daily_report_path_template: "data/reports/daily_{date}.json"
watermark_sla_path_template: "data/audit/watermark_sla_{date}.json"
watermark_sla_before_path_template: "data/audit/watermark_sla_before_{date}.json"
daily_update_log_name_template: "chunkymonkey_daily_update_{date}.log"
""",
    )
    with pytest.raises(PipelineEvidencePathsError, match="version"):
        load_pipeline_evidence_paths(p)


def test_unknown_key_fails_closed(tmp_path):
    p = _write(
        tmp_path,
        """version: 1
daily_report_path_template: "data/reports/daily_{date}.json"
watermark_sla_path_template: "data/audit/watermark_sla_{date}.json"
watermark_sla_before_path_template: "data/audit/watermark_sla_before_{date}.json"
daily_update_log_name_template: "chunkymonkey_daily_update_{date}.log"
extra_unknown_key: "surprise"
""",
    )
    with pytest.raises(PipelineEvidencePathsError, match="unknown keys"):
        load_pipeline_evidence_paths(p)


@pytest.mark.parametrize(
    "missing_key",
    [
        "daily_report_path_template",
        "watermark_sla_path_template",
        "watermark_sla_before_path_template",
        "daily_update_log_name_template",
    ],
)
def test_missing_required_key_fails_closed(tmp_path, missing_key):
    """每个必填键单独隔离一条用例: 其它键全满足, 只缺这一个。"""
    values = {
        "daily_report_path_template": "data/reports/daily_{date}.json",
        "watermark_sla_path_template": "data/audit/watermark_sla_{date}.json",
        "watermark_sla_before_path_template": "data/audit/watermark_sla_before_{date}.json",
        "daily_update_log_name_template": "chunkymonkey_daily_update_{date}.log",
    }
    del values[missing_key]
    body = "version: 1\n" + "\n".join(f'{k}: "{v}"' for k, v in values.items()) + "\n"
    p = _write(tmp_path, body)
    with pytest.raises(PipelineEvidencePathsError, match="non-empty string"):
        load_pipeline_evidence_paths(p)


def test_template_without_date_placeholder_fails_closed(tmp_path):
    p = _write(
        tmp_path,
        """version: 1
daily_report_path_template: "data/reports/daily.json"
watermark_sla_path_template: "data/audit/watermark_sla_{date}.json"
watermark_sla_before_path_template: "data/audit/watermark_sla_before_{date}.json"
daily_update_log_name_template: "chunkymonkey_daily_update_{date}.log"
""",
    )
    with pytest.raises(PipelineEvidencePathsError, match=r"\{date\}"):
        load_pipeline_evidence_paths(p)


def test_log_name_template_without_date_placeholder_fails_closed(tmp_path):
    """隔离用例: 其它三个模板全满足, 只有新加的第四个模板缺 {date} 占位符。"""
    p = _write(
        tmp_path,
        """version: 1
daily_report_path_template: "data/reports/daily_{date}.json"
watermark_sla_path_template: "data/audit/watermark_sla_{date}.json"
watermark_sla_before_path_template: "data/audit/watermark_sla_before_{date}.json"
daily_update_log_name_template: "chunkymonkey_daily_update.log"
""",
    )
    with pytest.raises(PipelineEvidencePathsError, match=r"\{date\}"):
        load_pipeline_evidence_paths(p)


def test_non_mapping_root_fails_closed(tmp_path):
    p = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(PipelineEvidencePathsError, match="mapping"):
        load_pipeline_evidence_paths(p)

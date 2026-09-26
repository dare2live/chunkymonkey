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

# 五个模板键 + 一个改写清单键的合法基线, 供各测试按需删/改一个键 (§4.3 隔离要求:
# 每个必填键一条其它全满足只它为假的用例)。
_VALID_VALUES = {
    "daily_report_path_template": "data/reports/daily_{date}.json",
    "watermark_sla_path_template": "data/audit/watermark_sla_{date}.json",
    "watermark_sla_before_path_template": "data/audit/watermark_sla_before_{date}.json",
    "daily_update_log_name_template": "chunkymonkey_daily_update_{date}.log",
    "dry_run_root_template": "data/scratch/dry_run/{date}",
}


def _yaml_body(*, values: dict[str, str] | None = None, redirect_args=("--json-out",), version=1) -> str:
    vals = values if values is not None else _VALID_VALUES
    lines = [f"version: {version}"]
    lines += [f'{k}: "{v}"' for k, v in vals.items()]
    if redirect_args is not None:
        rendered = ", ".join(f'"{a}"' for a in redirect_args)
        lines.append(f"dry_run_redirect_args: [{rendered}]")
    return "\n".join(lines) + "\n"


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
    # cut_dry_isolation (2026-09-26): dry 根模板 + 改写清单同样从真实配置读, 不 monkeypatch。
    assert paths.dry_run_root_template == "data/scratch/dry_run/{date}"
    assert paths.dry_run_root(repo=Path("/repo"), date="20260925") == Path(
        "/repo/data/scratch/dry_run/20260925"
    )
    assert paths.dry_run_redirect_args == ("--json-out", "--json-output", "--alert-flag")


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "pipeline_evidence_paths.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_file_fails_closed(tmp_path):
    with pytest.raises(PipelineEvidencePathsError, match="missing"):
        load_pipeline_evidence_paths(tmp_path / "does_not_exist.yaml")


def test_wrong_version_fails_closed(tmp_path):
    p = _write(tmp_path, _yaml_body(version=2))
    with pytest.raises(PipelineEvidencePathsError, match="version"):
        load_pipeline_evidence_paths(p)


def test_unknown_key_fails_closed(tmp_path):
    body = _yaml_body() + 'extra_unknown_key: "surprise"\n'
    p = _write(tmp_path, body)
    with pytest.raises(PipelineEvidencePathsError, match="unknown keys"):
        load_pipeline_evidence_paths(p)


@pytest.mark.parametrize(
    "missing_key",
    [
        "daily_report_path_template",
        "watermark_sla_path_template",
        "watermark_sla_before_path_template",
        "daily_update_log_name_template",
        "dry_run_root_template",
    ],
)
def test_missing_required_key_fails_closed(tmp_path, missing_key):
    """每个必填键单独隔离一条用例: 其它键全满足, 只缺这一个。"""
    values = dict(_VALID_VALUES)
    del values[missing_key]
    p = _write(tmp_path, _yaml_body(values=values))
    with pytest.raises(PipelineEvidencePathsError, match="non-empty string"):
        load_pipeline_evidence_paths(p)


def test_missing_dry_run_redirect_args_fails_closed(tmp_path):
    """隔离用例: 五个模板键全满足, 只缺新加的 dry_run_redirect_args 列表键。"""
    p = _write(tmp_path, _yaml_body(redirect_args=None))
    with pytest.raises(PipelineEvidencePathsError, match="dry_run_redirect_args"):
        load_pipeline_evidence_paths(p)


@pytest.mark.parametrize(
    "bad_yaml_value",
    ['"--json-out"', "[]", "[1, 2]", '["", "--alert-flag"]'],
)
def test_dry_run_redirect_args_wrong_type_fails_closed(tmp_path, bad_yaml_value):
    """类型错隔离: 字符串代替列表 / 空列表 / 非字符串元素 / 含空字符串元素, 各自 fail-closed。"""
    body = _yaml_body(redirect_args=None) + f"dry_run_redirect_args: {bad_yaml_value}\n"
    p = _write(tmp_path, body)
    with pytest.raises(PipelineEvidencePathsError, match="dry_run_redirect_args"):
        load_pipeline_evidence_paths(p)


def test_template_without_date_placeholder_fails_closed(tmp_path):
    values = dict(_VALID_VALUES, daily_report_path_template="data/reports/daily.json")
    p = _write(tmp_path, _yaml_body(values=values))
    with pytest.raises(PipelineEvidencePathsError, match=r"\{date\}"):
        load_pipeline_evidence_paths(p)


def test_log_name_template_without_date_placeholder_fails_closed(tmp_path):
    """隔离用例: 其它模板全满足, 只有日志文件名模板缺 {date} 占位符。"""
    values = dict(_VALID_VALUES, daily_update_log_name_template="chunkymonkey_daily_update.log")
    p = _write(tmp_path, _yaml_body(values=values))
    with pytest.raises(PipelineEvidencePathsError, match=r"\{date\}"):
        load_pipeline_evidence_paths(p)


def test_dry_run_root_template_without_date_placeholder_fails_closed(tmp_path):
    """隔离用例: 其它模板全满足, 只有新加的 dry 根模板缺 {date} 占位符。"""
    values = dict(_VALID_VALUES, dry_run_root_template="data/scratch/dry_run")
    p = _write(tmp_path, _yaml_body(values=values))
    with pytest.raises(PipelineEvidencePathsError, match=r"\{date\}"):
        load_pipeline_evidence_paths(p)


def test_non_mapping_root_fails_closed(tmp_path):
    p = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(PipelineEvidencePathsError, match="mapping"):
        load_pipeline_evidence_paths(p)

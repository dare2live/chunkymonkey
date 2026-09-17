"""Typed loader for daily_update evidence path templates.

Owner: ``backend/config/pipeline_evidence_paths.yaml``. store.py / preflight.py /
context.py / chain_run.py must import this module instead of holding their own literal copy
of ``data/reports/daily_{date}.json`` etc. (CLAUDE.md #11: 规则进 typed YAML,
代码里不许出现参数的字面量副本，同一个参数只在一处定义)。

fail-closed: 配置缺失 / 不可解析 / 未知键 / 缺键 / 模板不含 ``{date}`` 占位符
一律抛 :class:`PipelineEvidencePathsError`。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
DEFAULT_PATH = REPO / "backend" / "config" / "pipeline_evidence_paths.yaml"

_TEMPLATE_KEYS = (
    "daily_report_path_template",
    "watermark_sla_path_template",
    "watermark_sla_before_path_template",
    "daily_update_log_name_template",
)


class PipelineEvidencePathsError(RuntimeError):
    """配置缺失 / 不可解析 / 违反形状约束 —— 调用方必须 fail closed。"""


@dataclass(frozen=True)
class PipelineEvidencePaths:
    """四个 daily_update 证据路径/文件名模板 (前三个 repo-relative, 第四个是不含目录的
    日志文件名；全部含 ``{date}`` 占位符)。"""

    daily_report_path_template: str
    watermark_sla_path_template: str
    watermark_sla_before_path_template: str
    daily_update_log_name_template: str

    def daily_report_rel(self, *, date: str) -> str:
        return self.daily_report_path_template.replace("{date}", date)

    def watermark_sla_rel(self, *, date: str) -> str:
        return self.watermark_sla_path_template.replace("{date}", date)

    def watermark_sla_before_rel(self, *, date: str) -> str:
        return self.watermark_sla_before_path_template.replace("{date}", date)

    def daily_update_log_name(self, *, date: str) -> str:
        """文件名 only (无目录) —— 调用方拼 DEGRADED_FLAG.parent / 本返回值。"""
        return self.daily_update_log_name_template.replace("{date}", date)


def load_pipeline_evidence_paths(path: Path | None = None) -> PipelineEvidencePaths:
    """Read + validate the evidence path templates; raise on any malformed input."""

    p = path or DEFAULT_PATH
    if not p.is_file():
        raise PipelineEvidencePathsError(f"missing pipeline evidence paths config: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PipelineEvidencePathsError(f"unreadable pipeline evidence paths config: {exc}") from exc
    if not isinstance(raw, dict):
        raise PipelineEvidencePathsError("pipeline evidence paths config root must be a mapping")
    if raw.get("version") != 1:
        raise PipelineEvidencePathsError("pipeline evidence paths config version must be 1")
    unknown = set(raw) - {"version", *_TEMPLATE_KEYS}
    if unknown:
        raise PipelineEvidencePathsError(
            f"unknown keys in pipeline evidence paths config: {sorted(unknown)}"
        )
    values: dict[str, str] = {}
    for key in _TEMPLATE_KEYS:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PipelineEvidencePathsError(f"{key} must be a non-empty string")
        if "{date}" not in value:
            raise PipelineEvidencePathsError(f"{key} must contain a '{{date}}' placeholder")
        values[key] = value
    return PipelineEvidencePaths(**values)

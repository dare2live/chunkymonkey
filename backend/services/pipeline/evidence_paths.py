"""Typed loader for daily_update evidence path templates + dry-run path mapping.

Owner: ``backend/config/pipeline_evidence_paths.yaml``. store.py / preflight.py /
context.py / run.py / chain_run.py must import this module instead of holding their own
literal copy of ``data/reports/daily_{date}.json`` etc. (CLAUDE.md #11: 规则进 typed YAML,
代码里不许出现参数的字面量副本，同一个参数只在一处定义)。

fail-closed: 配置缺失 / 不可解析 / 未知键 / 缺键 / 模板不含 ``{date}`` 占位符
一律抛 :class:`PipelineEvidencePathsError`。

dry 映射 (cut_dry_isolation 2026-09-26): ``dry_path()`` 是把一条证据路径改写到
``dry_run_root_template`` 下的唯一实现——调用点 (context.py / preflight.py / store.py /
run.py) 一律传 ``repo=`` (通常是各自模块的 ``REPO``), 本函数不持有也不 import 任何模块级
REPO, 与 chain_run.py 的既有做法 (repo 作为显式参数) 一致。真实运行 (``dry=False``) 完全不
调用这两个函数, 路径与本刀之前逐字节相同。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]
DEFAULT_PATH = REPO / "backend" / "config" / "pipeline_evidence_paths.yaml"

_TEMPLATE_KEYS = (
    "daily_report_path_template",
    "watermark_sla_path_template",
    "watermark_sla_before_path_template",
    "daily_update_log_name_template",
    "dry_run_root_template",
)
_LIST_KEYS = ("dry_run_redirect_args",)


class PipelineEvidencePathsError(RuntimeError):
    """配置缺失 / 不可解析 / 违反形状约束 —— 调用方必须 fail closed。"""


@dataclass(frozen=True)
class PipelineEvidencePaths:
    """daily_update 证据路径/文件名模板 (前三个 repo-relative, 第四个是不含目录的
    日志文件名, 第五个是 dry 根目录模板；全部含 ``{date}`` 占位符) 加 dry 参数改写清单。"""

    daily_report_path_template: str
    watermark_sla_path_template: str
    watermark_sla_before_path_template: str
    daily_update_log_name_template: str
    dry_run_root_template: str
    dry_run_redirect_args: tuple[str, ...]

    def daily_report_rel(self, *, date: str) -> str:
        return self.daily_report_path_template.replace("{date}", date)

    def watermark_sla_rel(self, *, date: str) -> str:
        return self.watermark_sla_path_template.replace("{date}", date)

    def watermark_sla_before_rel(self, *, date: str) -> str:
        return self.watermark_sla_before_path_template.replace("{date}", date)

    def daily_update_log_name(self, *, date: str) -> str:
        """文件名 only (无目录) —— 调用方拼 DEGRADED_FLAG.parent / 本返回值。"""
        return self.daily_update_log_name_template.replace("{date}", date)

    def dry_run_root(self, *, repo: Path, date: str) -> Path:
        return repo / self.dry_run_root_template.replace("{date}", date)


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
    unknown = set(raw) - {"version", *_TEMPLATE_KEYS, *_LIST_KEYS}
    if unknown:
        raise PipelineEvidencePathsError(
            f"unknown keys in pipeline evidence paths config: {sorted(unknown)}"
        )
    values: dict[str, Any] = {}
    for key in _TEMPLATE_KEYS:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PipelineEvidencePathsError(f"{key} must be a non-empty string")
        if "{date}" not in value:
            raise PipelineEvidencePathsError(f"{key} must contain a '{{date}}' placeholder")
        values[key] = value
    for key in _LIST_KEYS:
        value = raw.get(key)
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            raise PipelineEvidencePathsError(
                f"{key} must be a non-empty list of non-empty strings"
            )
        values[key] = tuple(value)
    return PipelineEvidencePaths(**values)


def dry_path(p: str | Path, *, repo: Path, date: str) -> Path:
    """唯一的 dry 路径映射实现 (K1/K2c/K3 共用)。

    相对路径 (证据 JSON: 报告 / SLA before / SLA after / runtime check --json-out) 原样
    挂在 dry 根下, 保持同样的相对目录结构。绝对路径 (/tmp 下的降级告警旗标 / soft banner
    marker / 默认日志) 去掉前导 "/" 后挂在 dry 根的 ``tmp/`` 子目录下——两类互不重叠
    (dry 根下 "data/..." 与 "tmp/..." 不会碰撞), 且都在同一个 dry 根内, 方便一次性清理。

    不持有 REPO: 调用点必须显式传 ``repo`` (通常是调用方模块自己的 ``REPO``，如
    ``context.REPO`` / ``store.REPO``)，与 chain_run.evaluate_chain_run 的 repo 参数同一
    约定——这样 monkeypatch 调用方模块的 REPO 就足以让 dry 根一起跟着搬，不需要本模块
    再单独维护一份。

    K3: 调用点(如 --alert-flag 打开文件写入) 需要父目录先存在, 否则 FileNotFoundError
    与检查退出码非 0 分不清, 所以这里保证 ``target.parent`` 存在。
    """
    root = load_pipeline_evidence_paths().dry_run_root(repo=repo, date=date)
    pp = Path(p)
    if pp.is_absolute():
        target = root / "tmp" / Path(*pp.parts[1:])
    else:
        target = root / pp
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def redirect_runtime_check_args(args: list[str], *, repo: Path, date: str) -> list[str]:
    """dry=True 时改写 runtime check 参数列表里的路径类取值。

    只改写 ``pipeline_evidence_paths.yaml`` 的 ``dry_run_redirect_args`` 清单里点名的
    flag (--json-out / --json-output / --alert-flag) 紧跟的下一个值，其余参数原样透传。
    不在清单里的新 flag 不会被本函数意外改写 —— 白名单而非黑名单, 新增改写对象必须先
    进 YAML (CLAUDE.md #11)。
    """
    redirect_names = set(load_pipeline_evidence_paths().dry_run_redirect_args)
    out = list(args)
    i = 0
    while i < len(out):
        if out[i] in redirect_names and i + 1 < len(out):
            out[i + 1] = str(dry_path(out[i + 1], repo=repo, date=date))
            i += 1
        i += 1
    return out

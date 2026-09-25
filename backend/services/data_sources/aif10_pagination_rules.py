"""Loader for ``backend/config/aif10_pagination.yaml`` (typed, fail-closed).

This module only describes *what to check and what to compare* for a report's
pagination integrity (sort key / identity key / tolerance / duplicate policy)
and the holders 按日复核 timing parameters — it does not decide what to run or
in what order (CLAUDE.md 第 11 条; 红线 11 禁 plugin bus / 通用 DAG / YAML DSL).

Every unrecognized shape (未知键 / 悬空引用 / 类型不对 / 缺 evidence) is a
``ValueError`` subclass (:class:`AIF10PaginationRulesError`), prefixed
``aif10_pagination: <report|section>:`` so a failure names exactly which part
of the file it came from — no partial success, no silent default.

``policy_for``/``page_size_for`` feed the strict pagination engine
(``aif10_scraper.pagination.fetch_pages_strict``); an unregistered report
raises rather than falling back to a guessed policy — see
``aif10_scraper.batch.fetch_all_pages``'s own ``policy=None`` default (which
deliberately does *not* go through this loader, so org/qfii's unregistered
reports keep working under registry-derived STRICT defaults, §3.5 of
``sandbox/p1_specs_20260925/spec_holders_pagination.md``).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

import yaml

from aif10_scraper.pagination import PaginationPolicy
from aif10_scraper.registry import REPORT_BY_NAME

# backend/services/data_sources/aif10_pagination_rules.py -> backend/config/aif10_pagination.yaml
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "aif10_pagination.yaml"

_TOP_LEVEL_KEYS = frozenset({"version", "reports", "holders_notice_recheck", "audits"})
_REPORT_KEYS = frozenset(
    {
        "sort_columns",
        "sort_types",
        "identity_columns",
        "page_size",
        "row_tolerance_rows",
        "duplicates",
        "drift_refetch",
        "evidence",
    }
)
# 2026-09-25 主循环追加 (build_holders_cutA.md §15.2 S6 的刀 A 部分): 「曝露起点」只
# 有一个事实, 只留 audits.holders_notice_pagination.exposure_start 一个键 —— 本节不
# 再有 recheck_floor。max_days_per_run 也不进 YAML (跑批预算不是判据, CLAUDE.md 第
# 11 条), 刀 B 用代码常量。
_HOLDERS_NOTICE_RECHECK_KEYS = frozenset({"settle_days", "evidence"})
_AUDIT_HOLDERS_NOTICE_PAGINATION_KEYS = frozenset({"exposure_start", "dup_scan", "evidence"})
_VALID_DUPLICATES = frozenset({"error", "allow"})


class AIF10PaginationRulesError(ValueError):
    """``aif10_pagination.yaml`` 校验失败 (fail-closed, 不部分生效)。"""


@dataclass(frozen=True)
class ReportPaginationRule:
    sort_columns: str
    sort_types: str
    identity_columns: tuple[str, ...]
    page_size: int
    row_tolerance_rows: int
    duplicates: Literal["error", "allow"]
    drift_refetch: int
    evidence: str


@dataclass(frozen=True)
class HoldersNoticeRecheckRule:
    settle_days: int
    evidence: str


@dataclass(frozen=True)
class AuditRule:
    exposure_start: str
    dup_scan: str
    evidence: str


@dataclass(frozen=True)
class AIF10PaginationRules:
    version: int
    reports: Mapping[str, ReportPaginationRule]
    holders_notice_recheck: HoldersNoticeRecheckRule
    audits: Mapping[str, AuditRule]


def _fail(section: str, message: str) -> "AIF10PaginationRulesError":
    return AIF10PaginationRulesError(f"aif10_pagination: {section}: {message}")


def _require_exact_keys(got: Mapping, expected: frozenset, *, section: str) -> None:
    if not isinstance(got, dict):
        raise _fail(section, f"必须是 mapping, got {type(got).__name__}")
    if set(got) != expected:
        raise _fail(
            section,
            f"键集合必须精确等于 {sorted(expected)}, got {sorted(got)}",
        )


def _require_bool_like_str(value, *, section: str, field: str, choices: frozenset) -> str:
    if not isinstance(value, str) or value not in choices:
        raise _fail(section, f"{field} 必须是 {sorted(choices)} 之一, got {value!r}")
    return value


def _require_nonneg_int(value, *, section: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(section, f"{field} 必须是 >= 0 的整数, got {value!r}")
    return value


def _require_str_list(value, *, section: str, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _fail(section, f"{field} 必须是字符串列表, got {value!r}")
    return tuple(value)


def _require_nonempty_str(value, *, section: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(section, f"{field} 必须是非空字符串, got {value!r}")
    return value


def load_aif10_pagination_rules(path: Path | str | None = None) -> AIF10PaginationRules:
    """Load and validate ``aif10_pagination.yaml``.

    Fail-closed on every unrecognized shape — 未知键 / 悬空引用 (未注册的
    ``report_name``) / 类型不对 / 缺 ``evidence`` (在 ``duplicates: allow`` 或
    ``row_tolerance_rows > 0`` 或 ``drift_refetch > 0`` 时) 一律
    :class:`AIF10PaginationRulesError`, 不部分生效。
    """
    cfg_path = Path(path) if path is not None else _CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict) or set(raw) != _TOP_LEVEL_KEYS:
        raise _fail(
            "root",
            f"顶层键集合必须精确等于 {sorted(_TOP_LEVEL_KEYS)}, "
            f"got {sorted(raw) if isinstance(raw, dict) else type(raw).__name__}",
        )

    if raw.get("version") != 2:
        raise _fail("root", f"version 必须是 2, got {raw.get('version')!r}")

    reports_raw = raw.get("reports")
    if not isinstance(reports_raw, dict) or not reports_raw:
        raise _fail("reports", f"必须是非空 mapping, got {reports_raw!r}")

    reports: dict[str, ReportPaginationRule] = {}
    for report_name, cfg in reports_raw.items():
        section = f"reports.{report_name}"
        if report_name not in REPORT_BY_NAME:
            raise _fail(
                section,
                f"report_name {report_name!r} 未在 aif10_scraper.registry.REPORT_BY_NAME 注册 (悬空引用)",
            )
        _require_exact_keys(cfg, _REPORT_KEYS, section=section)

        sort_columns = _require_nonempty_str(cfg["sort_columns"], section=section, field="sort_columns")
        sort_types = _require_nonempty_str(cfg["sort_types"], section=section, field="sort_types")
        sort_cols_list = sort_columns.split(",")
        sort_types_list = sort_types.split(",")
        if len(sort_cols_list) != len(sort_types_list):
            raise _fail(
                section,
                f"sort_columns 与 sort_types 列数不一致: "
                f"{len(sort_cols_list)} != {len(sort_types_list)}",
            )

        identity_columns = _require_str_list(cfg["identity_columns"], section=section, field="identity_columns")
        sort_cols_set = {c.strip() for c in sort_cols_list}
        missing = [c for c in identity_columns if c not in sort_cols_set]
        if missing:
            raise _fail(
                section,
                f"identity_columns {missing} 不是 sort_columns={sort_columns!r} 的子集",
            )

        page_size = _require_nonneg_int(cfg["page_size"], section=section, field="page_size")
        if page_size <= 0:
            raise _fail(section, f"page_size 必须 > 0, got {page_size!r}")
        row_tolerance_rows = _require_nonneg_int(
            cfg["row_tolerance_rows"], section=section, field="row_tolerance_rows"
        )
        duplicates = _require_bool_like_str(
            cfg["duplicates"], section=section, field="duplicates", choices=_VALID_DUPLICATES
        )
        drift_refetch = _require_nonneg_int(cfg["drift_refetch"], section=section, field="drift_refetch")
        evidence = cfg["evidence"]
        if not isinstance(evidence, str):
            raise _fail(section, f"evidence 必须是字符串, got {evidence!r}")

        if duplicates == "allow" and identity_columns:
            raise _fail(section, "duplicates: allow 时 identity_columns 必须为空")
        # 2026-09-25 主循环追加 (S6 的刀 A 部分): identity_columns 非空 ⇒
        # row_tolerance_rows == 0 —— 有身份键就必须严格 (容差 0), 否则漂移期间的
        # 缺行/多行会被容差悄悄放过, 而身份判定本身却还在装作严格。
        if identity_columns and row_tolerance_rows != 0:
            raise _fail(
                section,
                "identity_columns 非空时 row_tolerance_rows 必须为 0 "
                f"(有身份键即容差 0), got row_tolerance_rows={row_tolerance_rows!r}",
            )
        needs_evidence = duplicates == "allow" or row_tolerance_rows > 0 or drift_refetch > 0
        if needs_evidence and not evidence.strip():
            raise _fail(
                section,
                "duplicates=allow 或 row_tolerance_rows>0 或 drift_refetch>0 时 evidence 必须非空",
            )

        reports[report_name] = ReportPaginationRule(
            sort_columns=sort_columns,
            sort_types=sort_types,
            identity_columns=identity_columns,
            page_size=page_size,
            row_tolerance_rows=row_tolerance_rows,
            duplicates=duplicates,  # type: ignore[arg-type]
            drift_refetch=drift_refetch,
            evidence=evidence,
        )

    recheck_raw = raw.get("holders_notice_recheck")
    _require_exact_keys(recheck_raw, _HOLDERS_NOTICE_RECHECK_KEYS, section="holders_notice_recheck")
    settle_days = _require_nonneg_int(
        recheck_raw["settle_days"], section="holders_notice_recheck", field="settle_days"
    )
    if settle_days < 1:
        raise _fail("holders_notice_recheck", f"settle_days 必须 >= 1, got {settle_days!r}")
    recheck_evidence = _require_nonempty_str(
        recheck_raw["evidence"], section="holders_notice_recheck", field="evidence"
    )
    holders_notice_recheck_rule = HoldersNoticeRecheckRule(
        settle_days=settle_days, evidence=recheck_evidence
    )

    audits_raw = raw.get("audits")
    if not isinstance(audits_raw, dict) or set(audits_raw) != {"holders_notice_pagination"}:
        raise _fail(
            "audits",
            f"键集合必须精确等于 {{'holders_notice_pagination'}}, "
            f"got {sorted(audits_raw) if isinstance(audits_raw, dict) else type(audits_raw).__name__}",
        )
    hnp_raw = audits_raw["holders_notice_pagination"]
    _require_exact_keys(
        hnp_raw, _AUDIT_HOLDERS_NOTICE_PAGINATION_KEYS, section="audits.holders_notice_pagination"
    )
    exposure_start = _require_nonempty_str(
        hnp_raw["exposure_start"], section="audits.holders_notice_pagination", field="exposure_start"
    )
    if not (len(exposure_start) == 8 and exposure_start.isdigit()):
        raise _fail(
            "audits.holders_notice_pagination",
            f"exposure_start 必须是 YYYYMMDD, got {exposure_start!r}",
        )
    dup_scan = hnp_raw["dup_scan"]
    if dup_scan != "all_partitions":
        raise _fail(
            "audits.holders_notice_pagination",
            f"dup_scan 必须是 'all_partitions', got {dup_scan!r}",
        )
    audit_evidence = _require_nonempty_str(
        hnp_raw["evidence"], section="audits.holders_notice_pagination", field="evidence"
    )
    audits = {
        "holders_notice_pagination": AuditRule(
            exposure_start=exposure_start, dup_scan=dup_scan, evidence=audit_evidence
        )
    }

    return AIF10PaginationRules(
        version=2,
        reports=reports,
        holders_notice_recheck=holders_notice_recheck_rule,
        audits=audits,
    )


def policy_for(report_name: str, *, rules: AIF10PaginationRules | None = None) -> PaginationPolicy:
    """严格翻页策略。未登记的报表 fail-closed (走 ``policy_for`` 的生产报表必须登记;
    ``fetch_all_pages``/``fetch_all_pages_sharded`` 的 ``policy=None`` 缺省走
    registry 不走这里, 所以 org / qfii 不受影响)。"""
    resolved = rules if rules is not None else load_aif10_pagination_rules()
    rule = resolved.reports.get(report_name)
    if rule is None:
        raise AIF10PaginationRulesError(
            f"aif10_pagination: report {report_name!r} 未登记 (fail-closed)"
        )
    return PaginationPolicy(
        sort_columns=rule.sort_columns,
        sort_types=rule.sort_types,
        identity_columns=rule.identity_columns,
        row_tolerance_rows=rule.row_tolerance_rows,
        duplicates=rule.duplicates,
        drift_refetch=rule.drift_refetch,
    )


def page_size_for(report_name: str, *, rules: AIF10PaginationRules | None = None) -> int:
    resolved = rules if rules is not None else load_aif10_pagination_rules()
    rule = resolved.reports.get(report_name)
    if rule is None:
        raise AIF10PaginationRulesError(
            f"aif10_pagination: report {report_name!r} 未登记 (fail-closed)"
        )
    return rule.page_size


def holders_notice_recheck(
    *, rules: AIF10PaginationRules | None = None
) -> HoldersNoticeRecheckRule:
    resolved = rules if rules is not None else load_aif10_pagination_rules()
    return resolved.holders_notice_recheck


__all__ = [
    "AIF10PaginationRules",
    "AIF10PaginationRulesError",
    "AuditRule",
    "HoldersNoticeRecheckRule",
    "ReportPaginationRule",
    "holders_notice_recheck",
    "load_aif10_pagination_rules",
    "page_size_for",
    "policy_for",
]

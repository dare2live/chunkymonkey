"""sync_runner — sync_registry.yaml 驱动的通用数据域同步器 (架构稿 §3.3).

一个 registry 条目 = 一个数据域。通用 runner 只负责:
  1. 按 batch_mode 切批 (交易日历驱动, 不 hardcode 日期)
  2. 调 source adapter fetch_raw (api 字段镜像, 不加工)
  3. 写 raw 表 (target_db 库, MERGE on grain, 加 built_at) — 幂等重跑
  4. legacy 域 watermark + failure_queue；formal 域在执行契约闭合前 fail closed
  5. 0 行 = 失败重试 (宪法 v2 第 6 条; allow_empty_batch 条目除外)

写锁纪律: raw 表写 tushare_raw.duckdb (manifest 注册), 与 smartmoney 主库锁解耦;
margin v3 = on_demand bounded calendar-eligible land/accept (SSE+SZSE);
v2 wrong-scope evidence remains read-only; 禁 --all-due / mass backfill。

watermark/failure_queue 在 smartmoney (既有表), 写入窗口短。

用法:
    PYTHONPATH=backend python -m services.data_sources.sync_runner \
        --domain moneyflow --backfill              # 从 data_start 回填到最新
    ... --domain moneyflow                          # 增量 (watermark 之后)
    ... --all-due                                   # 全部到期域 (daily_update 集成)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import yaml

from services.data_sources import margin_ingest
from services.data_sources.availability import (
    DomainEligibility,
    OperationWindow,
    SyncWindowError,
    TriggerMode,
    availability_policy_from_mapping,
    resolve_operation_window,
    resolve_sync_eligibility_frontier,
)
from services.data_sources.batch_integrity import (
    BatchCompletenessError,
    complete_batch_dates,
)
from services.data_sources.frontier_decision import (
    decide_frontier,
    plan_incremental_days,
)
from services.data_sources.runtime_limits import (
    apply_fetch_socket_timeout,
    fetch_socket_timeout_seconds,
)
from services.data_sources.sync_preconditions import CalendarFoundationError
from services.data_sources.sources.tushare import TuShareAuthorizationError

log = logging.getLogger("sync_runner")

_REPO = Path(__file__).resolve().parents[3]
_REGISTRY_PATH = _REPO / "backend" / "config" / "sync_registry.yaml"
SOURCE_TIER_TUSHARE = 2  # evidence: tushare = tier-2 源 (source_watermarks DOMAIN_SPECS sync:* 域全 tier 2; SLA_DAYS tier2=2d)

_EXECUTION_POLICY_KEYS = frozenset({"mode", "reason"})
_EXECUTION_REASON = re.compile(r"[a-z][a-z0-9_]*\Z")


@dataclass(frozen=True)
class DomainExecutionPolicy:
    """Typed permission to enter side-effectful synchronization."""

    mode: Literal["enabled", "disabled"]
    reason: str


class ExecutionPolicyError(ValueError):
    """A domain is disabled or its configured execution policy is invalid."""

    def __init__(
        self,
        domain: str,
        *,
        mode: str,
        reason: str,
        detail: str,
    ) -> None:
        self.domain = domain
        self.mode = mode
        self.reason = reason
        super().__init__(f"domain={domain} {detail}")


class PopulationScopeExecutionError(ValueError):
    """A formal dataset cannot enter execution without one propagated scope."""

    def __init__(self, domain: str, *, reason: str, detail: str) -> None:
        self.domain = domain
        self.reason = reason
        super().__init__(f"domain={domain} {detail}")


def _invalid_execution_policy(domain: str, detail: str) -> ExecutionPolicyError:
    return ExecutionPolicyError(
        domain,
        mode="invalid",
        reason="invalid_execution_policy",
        detail=detail,
    )


def execution_policy_for_spec(spec: Mapping[str, Any]) -> DomainExecutionPolicy:
    """Resolve one policy without loading registry YAML or touching runtime state."""

    domain = str(spec.get("domain") or "unknown")
    if "execution_policy" not in spec:
        raise _invalid_execution_policy(domain, "missing execution_policy")
    raw = spec.get("execution_policy")
    if not isinstance(raw, Mapping):
        raise _invalid_execution_policy(
            domain, "execution_policy must be a mapping"
        )
    missing = sorted(_EXECUTION_POLICY_KEYS - set(raw))
    unknown = sorted(set(raw) - _EXECUTION_POLICY_KEYS)
    if missing:
        raise _invalid_execution_policy(
            domain,
            f"missing execution_policy keys: {', '.join(missing)}",
        )
    if unknown:
        raise _invalid_execution_policy(
            domain,
            f"unknown execution_policy keys: {', '.join(unknown)}",
        )
    mode = raw["mode"]
    if mode not in ("enabled", "disabled"):
        raise _invalid_execution_policy(
            domain, f"unsupported execution policy mode={mode!r}"
        )
    reason = raw["reason"]
    if not isinstance(reason, str) or _EXECUTION_REASON.fullmatch(reason) is None:
        raise _invalid_execution_policy(
            domain, "execution policy reason contains malformed value"
        )
    return DomainExecutionPolicy(mode=mode, reason=reason)


def _require_execution_enabled(spec: Mapping[str, Any]) -> DomainExecutionPolicy:
    policy = execution_policy_for_spec(spec)
    if policy.mode == "disabled":
        domain = str(spec.get("domain") or "unknown")
        raise ExecutionPolicyError(
            domain,
            mode=policy.mode,
            reason=policy.reason,
            detail=f"execution disabled: {policy.reason}",
        )
    return policy


def preflight_execution_policies(
    registry: dict[str, Any], domains: list[str]
) -> None:
    """Reject disabled selected domains before calendar/lock/provider/DB."""

    for domain in domains:
        _require_execution_enabled(domain_spec(registry, domain))


def _formal_dataset_contract_for_spec(spec: Mapping[str, Any]):
    """Parse any formal dataset; margin-specific checks remain additive."""

    domain = str(spec.get("domain") or "unknown")
    from services.data_sources.margin_population_scope import (
        MarginPopulationScopeError,
    )

    try:
        margin_contract = None
        if domain == "margin":
            # Margin can never fall through to the generic legacy runner by
            # deleting its contract: its formal identity is blocking even when
            # a caller injects a mutated registry.
            margin_contract = margin_ingest.contract_for_spec(dict(spec))
        if "dataset_contract" not in spec:
            return None
        from services.data_sources.contracts import dataset_contract_from_spec

        contract = dataset_contract_from_spec(domain, spec)
        if domain == "margin":
            if margin_contract is None or (
                margin_contract.contract_hash != contract.contract_hash
                or margin_contract.config_hash != contract.config_hash
            ):
                raise ValueError("margin-specific and generic contracts disagree")
    except MarginPopulationScopeError as exc:
        raise PopulationScopeExecutionError(
            domain,
            reason="invalid_population_scope",
            detail=f"population scope invalid: {exc}",
        ) from exc
    except (TypeError, ValueError) as exc:
        raise PopulationScopeExecutionError(
            domain,
            reason="invalid_dataset_contract",
            detail=f"dataset contract invalid: {exc}",
        ) from exc
    return contract


def _bind_formal_execution_contract(spec: Mapping[str, Any], contract):
    """Bind + verify one DatasetExecutionContract; None when the domain is legacy."""

    if contract is None:
        return None
    domain = str(spec.get("domain") or getattr(contract, "domain", "unknown"))
    from services.data_sources.population_scope import bind_execution_contract

    if domain == "margin":
        from services.data_sources.margin_population_scope import (
            MarginPopulationScopeError,
            assert_margin_accepted_population_scope,
            assert_margin_transport_matches_accepted_scope,
        )

        try:
            assert_margin_accepted_population_scope(spec)
            assert_margin_transport_matches_accepted_scope(spec)
        except MarginPopulationScopeError as exc:
            raise PopulationScopeExecutionError(
                domain,
                reason="invalid_population_scope",
                detail=f"population scope invalid: {exc}",
            ) from exc

    raw_scope = spec.get("population_scope")
    policy = None
    if isinstance(raw_scope, Mapping) and raw_scope.get("kind") == "project_universe_pit":
        from services.universe import load_universe_policy

        policy = load_universe_policy()
    try:
        return bind_execution_contract(contract, spec, policy)
    except (TypeError, ValueError) as exc:
        raise PopulationScopeExecutionError(
            domain,
            reason="invalid_population_scope",
            detail=f"population scope invalid: {exc}",
        ) from exc


def _require_formal_population_execution(spec: Mapping[str, Any], contract):
    """Bind a formal scope and prove the same object reaches its consumer.

    Returns the attested ``DatasetExecutionContract`` for formal domains, or
    ``None`` for legacy domains.  After a successful handoff the caller must
    enter the formal runtime path and must not fall through to legacy sync.
    """

    execution = _bind_formal_execution_contract(spec, contract)
    if execution is None:
        return None
    domain = str(spec.get("domain") or execution.dataset.domain)
    from services.data_sources.formal_execution import (
        FormalExecutionHandoffError,
        propagate_formal_execution_contract,
    )

    try:
        return propagate_formal_execution_contract(domain, execution)
    except FormalExecutionHandoffError as exc:
        raise PopulationScopeExecutionError(
            exc.domain,
            reason=exc.reason,
            detail=exc.detail,
        ) from exc


def _refuse_formal_domain_runtime(domain: str, execution) -> dict[str, Any]:
    """Formal domains with a propagated contract never enter the legacy runner."""

    from services.data_sources.population_scope import verify_execution_contract

    attested = verify_execution_contract(execution)
    if attested.dataset.domain != domain:
        raise PopulationScopeExecutionError(
            domain,
            reason="formal_consumer_domain_mismatch",
            detail="execution contract domain does not match selected domain",
        )
    if domain == "margin":
        version = str(attested.dataset.contract_version)
        if version >= "3":
            raise PopulationScopeExecutionError(
                domain,
                reason="formal_runtime_misrouted",
                detail=(
                    "margin v3 must use the bounded catchup path "
                    "(explicit --start/--end); refused legacy fallthrough"
                ),
            )
        raise PopulationScopeExecutionError(
            domain,
            reason="formal_runtime_retired",
            detail=(
                "DatasetExecutionContract propagated to margin consumer, but the "
                "margin v2 live sync/writer runtime is retired; frozen evidence "
                "remains read-only"
            ),
        )
    raise PopulationScopeExecutionError(
        domain,
        reason="formal_runtime_unregistered",
        detail=(
            "DatasetExecutionContract propagated but no formal sync runtime is "
            "implemented for this domain"
        ),
    )


# Margin v3 catchup: calendar-eligible lag only. Cap covers a few open days of
# freeze debt without inviting mass history replay.
AUTHORIZED_MARGIN_CATCHUP_MAX_WINDOW_DAYS = 10


def _require_authorized_margin_catchup_window(
    *,
    backfill: bool,
    resume: bool,
    start: str | None,
    end: str | None,
    max_dates: int | None,
    eligible_end: str | None,
    coverage_start: str,
) -> list[str]:
    """Resolve explicit YYYYMMDD window for margin v3 bounded catchup."""

    if max_dates is not None:
        raise SyncWindowError("--max-dates is only valid for --drain")
    if backfill or resume:
        raise SyncWindowError(
            "domain=margin bounded catchup refuses --backfill/--resume "
            "(no mass history replay)"
        )
    if start is None or end is None:
        raise SyncWindowError(
            "domain=margin bounded catchup requires explicit --start/--end "
            f"(<= {AUTHORIZED_MARGIN_CATCHUP_MAX_WINDOW_DAYS} trading days, "
            "calendar-eligible only)"
        )
    start_c = str(start).replace("-", "")
    end_c = str(end).replace("-", "")
    for label, value in (("start", start_c), ("end", end_c)):
        if len(value) != 8 or not value.isdigit():
            raise SyncWindowError(
                f"domain=margin trade_date {label} must be YYYYMMDD; got {value!r}"
            )
    if start_c > end_c:
        raise SyncWindowError(
            f"domain=margin start must be <= end; got start={start_c} end={end_c}"
        )
    cov = str(coverage_start).replace("-", "")
    if start_c < cov:
        raise SyncWindowError(
            f"domain=margin v3 catchup start must be >= coverage_start={cov}; "
            f"got {start_c} (older days remain v2 read-only evidence)"
        )
    if eligible_end is not None and end_c > str(eligible_end).replace("-", ""):
        raise SyncWindowError(
            f"domain=margin end={end_c} exceeds eligible_end={eligible_end} "
            "(refuse future / unpublished partitions)"
        )
    days = trading_days(start_c, end_c)
    if not days:
        raise SyncWindowError(
            f"domain=margin no trading days in [{start_c},{end_c}]"
        )
    if len(days) > AUTHORIZED_MARGIN_CATCHUP_MAX_WINDOW_DAYS:
        raise SyncWindowError(
            f"domain=margin catchup window max "
            f"{AUTHORIZED_MARGIN_CATCHUP_MAX_WINDOW_DAYS} trading days; "
            f"got {len(days)} in [{start_c},{end_c}] — refuse mass backfill"
        )
    return days


def _project_margin_accepted_ops_watermark(
    spec: dict[str, Any],
    contract,
    *,
    through: str,
    provider_succeeded: bool = False,
) -> None:
    """Rebuild sync:margin Ops watermark from accepted facts (single writer).

    Catchup skip and land/accept must both refresh this projection; otherwise
    SLA audits stale v2 watermarks as ACCEPTED_PROJECTION_DRIFT after v3 accepts.
    """
    from services.data_sources.margin_projections import project_margin_accepted_state

    through_c = str(through or "").replace("-", "")
    cov = str(contract.coverage_start).replace("-", "")
    if len(through_c) != 8 or not through_c.isdigit():
        raise PopulationScopeExecutionError(
            "margin",
            reason="invalid_projection_window",
            detail=f"margin projection through must be YYYYMMDD; got {through!r}",
        )
    expected = trading_days(cov, through_c) if through_c >= cov else []
    raw = _target_conn(spec)
    ops = _smartmoney_conn()
    try:
        project_margin_accepted_state(
            raw,
            ops,
            expected,
            contract=contract,
            provider_succeeded=provider_succeeded,
        )
    finally:
        raw.close()
        ops.close()


def _publish_margin_bounded_catchup(
    spec: dict[str, Any],
    *,
    trade_dates: list[str],
    trigger_mode: TriggerMode | str = "manual",
) -> dict[str, Any]:
    """Land+accept margin v3 partitions day-by-day; stop on first hard error."""

    from services.data_sources.margin_catchup import (
        MarginCatchupError,
        land_then_accept_margin_day,
    )
    from services.data_sources.sources.tushare import TuShareAuthorizationError

    formal_contract = _formal_dataset_contract_for_spec(spec)
    formal_execution = _require_formal_population_execution(spec, formal_contract)
    if formal_execution is None:
        raise PopulationScopeExecutionError(
            "margin",
            reason="execution_contract_not_propagated",
            detail="margin v3 catchup requires a propagated DatasetExecutionContract",
        )
    from services.data_sources.population_scope import verify_execution_contract

    attested = verify_execution_contract(formal_execution)
    contract = attested.dataset
    if str(contract.contract_version) < "3":
        raise PopulationScopeExecutionError(
            "margin",
            reason="formal_runtime_retired",
            detail="margin catchup refuses contract_version<3",
        )

    eligibility = eligible_end_date(spec, trigger_mode=trigger_mode)
    apply_fetch_socket_timeout(spec)
    adapter = _adapter(str(spec["source"]))
    conn = _target_conn(spec)
    day_results: list[dict[str, Any]] = []
    total_rows = 0
    failed = 0
    last_ok: str | None = None
    try:
        for trade_date in trade_dates:
            try:
                result = land_then_accept_margin_day(
                    conn,
                    adapter,
                    spec,
                    trade_date,
                    fetch_logical_batch=_fetch_logical_batch,
                    quota_wall_classifier=_is_quota_wall,
                    contract=contract,
                )
            except TuShareAuthorizationError:
                raise
            except QuotaExhaustedError as exc:
                failed += 1
                day_results.append(
                    {
                        "partition_value": trade_date,
                        "status": "quota_halt",
                        "failed_batches": 1,
                        "error": str(exc)[:300],
                    }
                )
                break
            except MarginCatchupError as exc:
                failed += 1
                day_results.append(
                    {
                        "partition_value": trade_date,
                        "status": "error",
                        "failed_batches": 1,
                        "error": str(exc)[:300],
                    }
                )
                break
            if result.status != "ACCEPTED":
                failed += 1
                day_results.append(
                    {
                        "partition_value": trade_date,
                        "status": result.status,
                        "failed_batches": 1,
                        "batch_id": result.batch_id,
                        "rejection_code": result.rejection_code,
                        "error": result.detail,
                    }
                )
                break
            total_rows += int(result.row_count or 0)
            last_ok = trade_date
            day_results.append(
                {
                    "partition_value": trade_date,
                    "status": "ACCEPTED",
                    "failed_batches": 0,
                    "batch_id": result.batch_id,
                    "rows": result.row_count,
                    "content_hash": result.content_hash,
                }
            )
    finally:
        conn.close()

    # Ops watermark must track accepted v3 frontier (even on partial windows).
    project_through = last_ok or str(contract.coverage_start).replace("-", "")
    _project_margin_accepted_ops_watermark(
        spec,
        contract,
        through=project_through,
        provider_succeeded=failed == 0 and last_ok is not None,
    )

    completed_ok = sum(1 for r in day_results if int(r.get("failed_batches") or 0) == 0)
    return {
        "domain": "margin",
        "status": "ok" if failed == 0 else "partial",
        "batches": completed_ok,
        "rows": total_rows,
        "failed_batches": failed,
        "publication": "accepted_margin_exchange_daily_partition",
        "transport": "land_then_accept",
        "contract_version": str(contract.contract_version),
        "eligible_end": eligibility.eligible_end,
        "eligibility_reason": eligibility.reason,
        "partition_values": [
            str(r.get("partition_value") or "")
            for r in day_results
            if int(r.get("failed_batches") or 0) == 0
        ],
        "trade_dates": list(trade_dates),
        "day_results": day_results,
        "last_date": last_ok,
        "window_days_requested": len(trade_dates),
        "window_days_completed": completed_ok,
    }


def _refuse_formal_legacy_raw_path(domain: str) -> None:
    """Formal domains with active boundaries never fall through to legacy raw."""

    from services.data_sources.formal_boundaries import (
        FormalBoundaryError,
        refuse_legacy_raw_write_for_formal_domain,
    )

    try:
        refuse_legacy_raw_write_for_formal_domain(domain)
    except FormalBoundaryError as exc:
        reason = (
            "accepted_generation_pending"
            if domain == "trade_cal"
            else "accepted_partition_pending"
            if domain in {"daily", "stock_st"}
            else exc.reason
        )
        raise ExecutionPolicyError(
            domain,
            mode="disabled",
            reason=reason,
            detail=exc.detail,
        ) from exc


def preflight_formal_population_scopes(
    registry: dict[str, Any], domains: list[str]
) -> None:
    """Prove every selected formal population contract before side effects."""

    for domain in domains:
        spec = domain_spec(registry, domain)
        contract = _formal_dataset_contract_for_spec(spec)
        execution = _require_formal_population_execution(spec, contract)
        if execution is not None:
            # Preflight only attests handoff.  Enabled formal domains still cannot
            # reach provider/DB here; run_domain refuses the retired runtime next.
            from services.data_sources.population_scope import verify_execution_contract

            verify_execution_contract(execution)


def load_registry(path: Path | None = None) -> dict[str, Any]:
    raw = yaml.safe_load((path or _REGISTRY_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "domains" not in raw:
        raise ValueError("sync_registry.yaml: 缺 domains")
    return raw


def domain_spec(registry: dict[str, Any], domain: str) -> dict[str, Any]:
    """Return one merged registry entry through the stable public read seam."""

    spec = dict(registry.get("defaults") or {})
    entry = registry["domains"].get(domain)
    if entry is None:
        raise KeyError(f"sync_registry: 未注册的数据域 '{domain}' — 新域必须先加 registry 条目 (宪法 v2 第 7/9 条)")
    spec.update(entry)
    spec["domain"] = domain
    return spec


_TUSHARE_SOURCE: Any = None


def _adapter(source_name: str):
    """2026-07-07 精简: 原经 get_registry().get_source() 间接查找已删 (registry.py/base.py
    多源 fallback 机制 0 消费方物删, 见 git log --grep data_sources_registry_retirement)。
    sync_registry.yaml 47 域全声明 source: tushare, 单例直连即可。A5: formal 边界只许 tushare。"""
    global _TUSHARE_SOURCE
    from services.data_sources.formal_boundaries import (
        FormalBoundaryError,
        require_live_adapter,
    )

    try:
        require_live_adapter(source_name, domain="*")
    except FormalBoundaryError as exc:
        raise KeyError(
            f"data_sources: 未知 source '{source_name}' (精简后只剩 tushare)"
        ) from exc
    if _TUSHARE_SOURCE is None:
        from services.data_sources.sources.tushare import TuShareSource

        _TUSHARE_SOURCE = TuShareSource()
    return _TUSHARE_SOURCE


def _target_conn(spec: dict[str, Any]):
    """raw 库写连接 (manifest 解析路径); 表不存在由首批数据建."""
    from services.database_manifest import get_database_manifest
    from services.duck_adapter import connect

    db_alias = spec.get("target_db", "tushare_raw")
    path = get_database_manifest().path_for(db_alias)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return connect(str(path), read_only=False)


def _smartmoney_conn():
    from services.db import get_conn

    return get_conn()


def _warn_if_clamped(domain: str, start_d: str, days: list[str]) -> None:
    """回填/增量起点早于交易日历首日 = 更早段被静默 clamp — 必须显式可见.

    反例 (AGENTS.md Tier0 calendar truth, 2026-06-12): registry data_start=20050104 被日历起点
    2023-01-03 clamp, top_list 等 2005-2022 全军未落零告警; 验收必须按落库
    min(trade_date) 对账 data_start, 不是"跑完没报错"。
    """
    first = days[0] if days else None
    if first is not None and start_d < first:
        log.warning(
            "domain=%s 起点 %s 早于交易日历首日 %s — 更早段被日历 clamp 不会拉取; "
            "落库验收按 min(trade_date) 对账 data_start",
            domain, start_d, first,
        )
    elif not days:
        log.warning("domain=%s 请求窗口起点 %s 在交易日历内零交易日", domain, start_d)


def _existing_ts_codes(spec: dict[str, Any]) -> set[str]:
    """target 表已有数据的 ts_code 集 (断点续拉跳过用)。planning 期 tushare_raw 未被本 run 写锁, read_only 查。

    坑 (2026-07-05 income 深史回填 95% 未生效根因): "已有数据" != "已有深史"。若某股票此前
    因日常增量 sync 已写入近期几行(与本次全历史回填意图无关), --resume 会把它当"已拉过"整股跳过,
    深史 (2008-2021) 永远拿不到。--resume 仅适合"本次全量回填被中断, 重启跳过本次真已跑完的股票"
    这个窄场景; 若目标表此前被其它增量流程部分填充过, 真正的全历史回填必须用纯 --backfill
    (不带 --resume), 否则"回填成功"但只对表里原本 0 行的股票生效 (income 案例: 仅 260/5335=4.9%)。
    """
    import duckdb

    from services.database_manifest import get_database_manifest
    table = spec["target_table"]
    try:
        path = get_database_manifest().path_for(spec.get("target_db", "tushare_raw"))
        con = duckdb.connect(str(path), read_only=True)  # rule-compliance: ok evidence=只读查已拉ts_code供断点续拉, 非业务阈值/非主库写
    except Exception as exc:
        log.warning("[resume] 无法读 %s 查已拉 ts_code (回退全量拉): %s", table, str(exc)[:80])
        return set()
    try:
        if not con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone():
            return set()
        rows = con.execute(f"SELECT DISTINCT ts_code FROM {table} WHERE ts_code IS NOT NULL").fetchall()
        return {str(r[0]) for r in rows}
    finally:
        con.close()


def _latest_ended_report_period(today: str) -> str:
    """Acquire clock: latest quarter-end that has already occurred (YYYYMMDD)."""
    from services.data_sources.periodic_report_calendar import latest_ended_report_period

    return latest_ended_report_period(today)


def _latest_expected_report_period(today: str) -> str:
    """Statutory completeness clock (deadline <= today). Not an acquire gate."""
    from services.data_sources.periodic_report_calendar import (
        latest_statutory_complete_report_period,
    )

    return latest_statutory_complete_report_period(today)


def _stocks_up_to_date(spec: dict[str, Any], target_period: str, period_col: str = "end_date") -> set[str]:
    """target 表里 MAX(period_col) >= target_period 的 ts_code (=已有最新应披露期, 增量跳过)。
    read_only 查 (planning 期 raw 未本run写锁)。表无/列无 → 空集 (全拉)。"""
    import duckdb

    from services.database_manifest import get_database_manifest
    table = spec["target_table"]
    try:
        path = get_database_manifest().path_for(spec.get("target_db", "tushare_raw"))
        con = duckdb.connect(str(path), read_only=True)  # rule-compliance: ok evidence=只读查每股最新报告期供增量跳过, 非业务阈值/非主库写
    except Exception as exc:  # noqa: BLE001
        log.warning("[increment] 无法读 %s 查最新期 (回退全拉): %s", table, str(exc)[:80])
        return set()
    try:
        if not con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]).fetchone():
            return set()
        cols = {r[1] for r in con.execute(f"PRAGMA table_info('{table}')").fetchall()}
        if period_col not in cols or "ts_code" not in cols:
            return set()
        rows = con.execute(
            f"SELECT ts_code, MAX({period_col}) FROM {table} WHERE ts_code IS NOT NULL GROUP BY ts_code"
        ).fetchall()
        return {str(r[0]) for r in rows if r[1] is not None and str(r[1]) >= target_period}
    finally:
        con.close()


def _by_ts_code_batches(
    spec: dict[str, Any],
    *,
    resume: bool = False,
    backfill: bool = False,
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    """按股循环批清单 (单股接口如 stk_factor_pro/fina_mainbz)。

    股票清单真相源 = services.universe.get_active_universe (单一计算点): 白名单 60/00/30/68
    + 非退市 (K线真相源); **默认含 ST/*ST**（沪深A）。2026-06-17 用户: 排除列表=交易日历级
    硬真相源 — 不拉 B/BJ/三板/无K线退市股的逐股数据。owner 2026-07-22: ST 属白名单，
    不是与 B/BJ 同类的排除板。
    **include_st (默认 true)**: 产品/参考域拉活跃 ST；策略域可显式 `include_st: false`
    收窄（非白名单真相）。见 git log --grep hs_a_whitelist_includes_st。
    """
    from services.universe import get_active_universe

    fixed = dict(spec.get("fixed_params") or {})
    if start:
        fixed["start_date"] = start
    if end and "start_date" in fixed:
        fixed["end_date"] = end
    # mythos §10 data_start 参数口径: 部分 by_ts_code 接口 (如 stk_holdernumber) 不传 start_date
    # 只返回最近 ~8 期 (实测 600519 无日期 8 行 vs start_date=2019 给 38 行全史) → 回填必须显式传
    # start_date=data_start 才拿全史; 否则回填"成功"但只覆盖近期 (94min 白跑 0 净新增, 2026-06-24 实测踩坑)。
    # 增量 (非 backfill) 不传, 拿最近期即可 (覆盖新季); 对本就返全史的接口 (top10 by_ts_code) 传亦无害 (下界=K线对齐)。
    if backfill and spec.get("data_start") and "start_date" not in fixed:
        fixed["start_date"] = str(spec["data_start"])
    # ``run_domain`` resolves one live operation window before entering this
    # helper and passes its effective end.  Keeping a second eligibility lookup
    # here would let direct/explicit paths observe different clock frontiers.
    include_st = bool(spec.get("include_st", True))
    conn0 = _smartmoney_conn()
    try:
        codes = get_active_universe(conn0, include_st=include_st)  # 沪深A+活跃K线; ST 默认纳入
    finally:
        conn0.close()

    def _ts(code: str) -> str | None:
        if code.startswith("6"):
            return f"{code}.SH"
        if code.startswith(("0", "3")):
            return f"{code}.SZ"
        return None  # 白名单补集 (北交所/三板) 不在 universe

    batch = [{"ts_code": t, **fixed} for c in sorted(codes) if (t := _ts(c))]

    # by_report_period 增量: 目标 = 已结束报告期 (采集跟公告), 不是法定截止日。
    # 跳过 MAX(end_date) 已达该期的股; backfill 时全拉。
    if spec.get("increment_mode") == "by_report_period" and not backfill:
        import datetime as _dt
        today = _dt.datetime.now().strftime("%Y%m%d")  # Phase ψ.5 allowlist: 报告期增量参照日(算最新应披露季报期, 非trade-date end-date)
        target_period = _latest_ended_report_period(today)
        period_col = spec.get("period_col", "end_date")
        up_to_date = _stocks_up_to_date(spec, target_period, period_col)
        n0 = len(batch)
        batch = [b for b in batch if b["ts_code"] not in up_to_date]
        log.info("[increment by_report_period] %s 目标期=%s: 跳过 %d 已最新股, 剩 %d 待抓新期",
                 spec["domain"], target_period, n0 - len(batch), len(batch))
    elif resume:
        done = _existing_ts_codes(spec)
        if done:
            n0 = len(batch)
            batch = [b for b in batch if b["ts_code"] not in done]
            log.info("[resume] %s 跳过 %d 已拉 ts_code, 剩 %d 待拉 (断点续拉)",
                     spec["domain"], n0 - len(batch), len(batch))
    return batch


def _quarter_ends(start: str, end: str) -> list[str]:
    """报告期列表 (季末日 YYYYMMDD): [start, end] 内所有 0331/0630/0930/1231.

    用于 by_period 批 (财报快报 express_vip / 按报告期整批拉的接口 — ann_date/trade_date 不可批)。
    覆盖式快照接口重拉某 period 拿最新修订态, MERGE on grain 幂等。
    """
    sy, ey = int(start[:4]), int(end[:4])
    return [
        p
        for y in range(sy, ey + 1)
        for md in ("0331", "0630", "0930", "1231")
        if start <= (p := f"{y}{md}") <= end
    ]


def trading_days(start: str, end: str | None = None) -> list[str]:
    """交易日列表 (YYYYMMDD), 真相源 = 项目交易日历 (L0)."""
    # §9 拆库: dim_trading_calendar 迁 reference。不再开 smartmoney conn (latest_completed_trade_date
    #   已内部 reference 路由; 日历查询经 dim_read_conn(None,...) 直开 reference RO)。Stage E 物删 smartmoney
    #   副本后此处不受影响 (本就读 reference)。
    from services.utils import latest_completed_trade_date
    from services.data_access import resolver

    end_d = end or latest_completed_trade_date().replace("-", "")
    cal, cal_own = resolver.dim_read_conn(None, "dim_trading_calendar")  # rule-compliance: ok evidence=calendar truth-source in reference db
    try:
        rows = cal.execute(
            """
            SELECT replace(CAST(trade_date AS VARCHAR), '-', '') AS d
            FROM dim_trading_calendar
            WHERE is_trading AND replace(CAST(trade_date AS VARCHAR), '-', '') BETWEEN ? AND ?
            ORDER BY 1
            """,
            [start, end_d],
        ).fetchall()
    finally:
        if cal_own:
            cal.close()
    return [r[0] for r in rows]


class QuotaExhaustedError(RuntimeError):
    """信道账户级配额/反刷量墙命中 — 必须立即停链, 不可重试 (重试只会延长冷却)."""


# 代理网关 (jiaoch.site) 限流分两类 (2026-06-16 用户纠偏 + advrecv backfill 实测报错原文区分):
#   (1) 真·当日/账户级墙 (反刷量/防攻击): 命中后逐日续戳加重判定 → 必须熔断停链, 不可重试。
#       明确措辞 = "今日请求已达上限 / 请明天再试 / 攻击 / 封禁"。
#   (2) 瞬态限流 (每分钟单接口 120/多接口 200/并发上限 2/流量峰值): 等几分钟即恢复 → 退避重试, 绝不停链。
#       措辞 = "并发请求过多 / 请稍后重试 / 访问频率"。用户原话: "过几分钟重试就可以了"。
# 旧 bug (本次根因): 过宽标记 "请求过多" 把瞬态的"并发请求过多, 请稍后重试"误判成当日墙 → 误熔断停链
#   (advrecv backfill 两次 0 行停链, 主会话两次误判"配额墙")。修法: 只有明确当日/账户级措辞才算墙;
#   其余 (瞬态限流/超时/0 行) 一律退避重试, 终败入 failure_queue 由 drain 补 (mythos §10), 不停全链。
_HARD_WALL_MARKERS = ("今日请求已达上限", "请明天再试", "明日再试", "攻击", "封禁", "黑名单")
_TRANSIENT_RATELIMIT_MARKERS = (
    "并发请求过多", "请稍后重试", "稍后重试", "稍后再试", "访问频率", "频率超限", "请求过于频繁",
)


def _is_transient_ratelimit(msg: str) -> bool:
    return any(m in msg for m in _TRANSIENT_RATELIMIT_MARKERS)


def _is_quota_wall(msg: str) -> bool:
    """仅明确"当日/账户级"措辞算墙 (停链); 瞬态限流/超时/0 行都不算 → 退避重试。"""
    return any(m in msg for m in _HARD_WALL_MARKERS)


class _RateLimiter:
    """tinyshare 代理限流 **主动节流** (撞墙前先睡, 优于反应式退避): 单接口 per_interface/分钟 +
    全接口合计 total/分钟 滑窗。配置来源 sync_registry.yaml `defaults.rate_limit` (no-hardcode, 用户
    2026-06-17/19: 单接口120/多接口200/并发2)。runner 链串行单线程调用 → 并发=1 < max_concurrency;
    max_concurrency 配置守界, 未来并行需配 semaphore。reactive 退避 (_is_transient_ratelimit) 保留作兜底。"""
    _WINDOW = 60.0

    def __init__(self, per_interface_per_min: int, total_per_min: int):
        self.per_api = max(1, int(per_interface_per_min))
        self.total = max(1, int(total_per_min))
        self._api_calls: dict[str, deque] = defaultdict(deque)
        self._all_calls: deque = deque()
        self._lock = threading.Lock()

    @classmethod
    def _evict(cls, dq: deque, now: float) -> None:
        while dq and now - dq[0] > cls._WINDOW:
            dq.popleft()

    def acquire(self, api: str) -> None:
        """阻塞直到本次调用不超 单接口/全接口 每分钟上限, 然后登记本次调用时间。"""
        while True:
            with self._lock:
                now = time.time()
                self._evict(self._all_calls, now)
                dq = self._api_calls[api]
                self._evict(dq, now)
                wait = 0.0
                if len(dq) >= self.per_api:
                    wait = max(wait, self._WINDOW - (now - dq[0]))
                if len(self._all_calls) >= self.total:
                    wait = max(wait, self._WINDOW - (now - self._all_calls[0]))
                if wait <= 0:
                    ts = time.time()
                    dq.append(ts)
                    self._all_calls.append(ts)
                    return
            log.debug("[rate-limit] 主动节流 api=%s 睡 %.1fs (单接口%d/分 全%d/分)", api, wait, self.per_api, self.total)
            time.sleep(wait + 0.05)  # 锁外睡, 不阻塞其他线程窗口推进


_RATE_LIMITER: "_RateLimiter | None" = None
_RATE_LIMITER_INIT = False


def _get_rate_limiter(spec: dict[str, Any]) -> "_RateLimiter | None":
    """从 spec.rate_limit (defaults 合并) 懒初始化全局单例; 未配置 = 不节流 (向后兼容)。"""
    global _RATE_LIMITER, _RATE_LIMITER_INIT
    if _RATE_LIMITER_INIT:
        return _RATE_LIMITER
    _RATE_LIMITER_INIT = True
    cfg = spec.get("rate_limit") or {}
    per_api = cfg.get("per_interface_per_min")
    total = cfg.get("total_per_min")
    if per_api and total:
        _RATE_LIMITER = _RateLimiter(per_api, total)
        log.info("[rate-limit] 主动节流启用: 单接口 %s/分 + 全接口 %s/分 (并发上限 %s)",
                 per_api, total, cfg.get("max_concurrency"))
    return _RATE_LIMITER


def _fetch_with_retry(
    adapter,
    spec: dict[str, Any],
    params: dict[str, Any],
    *,
    empty_is_end: bool = False,
) -> list[dict[str, Any]] | None:
    """0 行/异常 → 退避重试; 终败返回 None (调用方入 failure_queue).

    ``empty_is_end`` 只由 _fetch_paged 在**后续页**(offset>0)上给 True: 那里的 0 行是
    "已经翻到末尾"这一合法信号, 不是采集失败。首页的 0 行仍按 allow_empty_batch 判 —— 那才是
    "源端真空 vs 我们没拉到"要区分的地方。

    配额/反刷量墙命中 → 抛 QuotaExhaustedError 立即上抛 (不消耗重试, 不逐日续戳),
    由 run_domain/drain/main 熔断停链。
    """
    retry = spec.get("retry") or {}
    attempts = int(retry.get("max_attempts", 3))
    backoffs = list(retry.get("backoff_seconds", [5, 30, 120]))
    # 瞬态限流 (每分钟级窗口) 需等几分钟才恢复, 普通退避太短 → 专用更长退避。
    # evidence: 用户 2026-06-16 "tushare 代理每分钟单接口120/多接口200, 过几分钟重试就可以了"。
    transient_backoffs = list(retry.get("transient_backoff_seconds", [60, 120, 180]))
    allow_empty = bool(spec.get("allow_empty_batch"))
    rate_limiter = _get_rate_limiter(spec)
    from services.data_sources.sources.tushare import TuShareAuthorizationError

    last_err: str | None = None
    for i in range(attempts):
        try:
            if rate_limiter is not None:
                rate_limiter.acquire(spec["api"])   # 主动节流: 撞墙前先睡 (config-driven)
            rows = adapter.fetch_raw(spec["api"], **params)
            if rows:
                return rows
            if allow_empty or empty_is_end:
                return []
            last_err = "zero_rows"
        except TuShareAuthorizationError:
            raise  # 账户授权是硬阻断；重试同一批只会制造噪音与额外请求
        except Exception as exc:  # noqa: BLE001 — 重试边界
            last_err = str(exc)[:200]
            if _is_quota_wall(last_err):
                raise QuotaExhaustedError(
                    f"信道配额/反刷量墙命中 domain={spec['domain']} params={params} err={last_err}"
                ) from exc
            # 瞬态限流/超时 → 不停链, 退避重试 (瞬态限流用更长退避等窗口恢复)
        if i < attempts - 1:
            wait = backoffs[min(i, len(backoffs) - 1)]
            if last_err and _is_transient_ratelimit(last_err):
                wait = max(wait, transient_backoffs[min(i, len(transient_backoffs) - 1)])
                log.warning("瞬态限流退避 %ds domain=%s params=%s (非当日墙, 不停链): %s",
                            wait, spec["domain"], params, last_err)
            time.sleep(wait)
    log.warning("fetch 终败 domain=%s params=%s err=%s", spec["domain"], params, last_err)
    return None


def _page_signature(page: list[dict[str, Any]]) -> int:
    """整页内容的序不敏感签名 (行序无关): 网关全量响应行序可能逐次漂移."""
    return hash(frozenset(
        tuple(sorted((k, str(v)) for k, v in row.items())) for row in page
    ))


def _fetch_paged(adapter, spec: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]] | None:
    """带 offset 分页的 fetch: registry 声明 page_limit 的域逐页拉到末页 (< limit 即止).

    单页上限不分页 = 静默截断丢数据 (top_inst 1000 整 / stk_surv 100 实测反例, 宪法第 6 条)。
    任何中间页终败 → 整批返回 None (当日整体失败重试) — 部分页写入会伪装成完整日,
    比整体失败更危险。50 页硬上限防接口异常死循环。
    """
    limit = int(spec.get("page_limit") or 0)
    if not limit:
        return _fetch_with_retry(adapter, spec, params)
    all_rows: list[dict[str, Any]] = []
    offset = 0
    prev_page: list[dict[str, Any]] | None = None
    for _ in range(50):  # 50 页 × page_limit 远超任何单日真实量, 防御性边界
        # offset>0 的页返回空 = 翻到末尾, 不是失败。
        # 2026-08-18 实测反例: dc_member 20250106 的 vendor 全量恰好 40,000 = 8x5000,
        # 第 8 页满页 -> 继续请求 offset=40000 -> 返回 0 行 -> 被判 zero_rows 终败 ->
        # 整批放弃。于是**凡总行数恰为 page_limit 整数倍的日子永远补不上**:
        # 该日库里长期只有 7,919 行 / 35 板块 (vendor 40,000 / 257 板块), 且每次重拉都以失败告终。
        page = _fetch_with_retry(
            adapter, spec, {**params, "limit": limit, "offset": offset},
            empty_is_end=offset > 0,
        )
        if page is None:
            return None  # 中间页失败不交部分结果
        if offset > 0 and not page:
            return all_rows  # 末页空 = 正好整倍数收齐
        if offset == 0 and len(page) > limit:
            # 首页行数 > limit = 网关无视 limit 直接给全量 (top_inst 实测 1231 > 1000),
            # 收下即完整数据, 不烧第二发探页
            log.info("网关无视 limit 返回全量 n=%d > %d, 单页收齐 domain=%s params=%s",
                     len(page), limit, spec["domain"], params)
            return page
        # 相同页守卫 (2026-06-12 实测 + 三判官修订): vendor 网关可能同时无视 limit/offset,
        # 每页返回相同全量 (top_inst 20180112 四参数组合同返 1231 行, 86 天 x50 调用白烧实证)。
        # 序不敏感比较 (判官抓的盲区): 网关返回行序不稳定时, 首/末行位置比较会失明 —
        # 改为整页内容集合签名, 行序无关。
        if prev_page is not None and page and prev_page and (
            len(page) == len(prev_page)
            and _page_signature(page) == _page_signature(prev_page)
        ):
            if len(page) % limit == 0:
                # 行数恰为 limit 整倍数 + offset 失效 = 无法区分 "全量" 与 "网关硬截断",
                # fail-closed 判失败 (dc_member 整 5000 pin 反例: 接受即静默截断)
                log.warning("分页相同页且行数整倍数, 疑网关截断+offset 失效 domain=%s params=%s n=%d",
                            spec["domain"], params, len(page))
                return None
            # 行数非整倍数 = 网关返回的就是全量 (limit 也被无视), 首页即完整数据
            log.info("网关 offset 失效但返回全量 (n=%d 非 %d 整倍数), 取首页 domain=%s params=%s",
                     len(page), limit, spec["domain"], params)
            return prev_page
        all_rows.extend(page)
        if len(page) < limit:
            return all_rows
        prev_page = page
        offset += limit
        time.sleep(0.4)  # rule-compliance: ok evidence=同 run_domain 节流口径 vendor-gateway-2026-06-11
    log.warning("分页超 50 页防御上限 domain=%s params=%s", spec["domain"], params)
    return None


def _fetch_logical_batch(
    adapter,
    spec: dict[str, Any],
    params: dict[str, Any],
    *,
    split_values_override: tuple[str, ...] | list[str] | None = None,
    fragment_callback: Callable[
        [str, dict[str, Any], list[dict[str, Any]] | None, BaseException | None],
        None,
    ]
    | None = None,
) -> list[dict[str, Any]] | None:
    """完整抓取一个逻辑批次；可选 callback 只观察通用分片结果。"""
    split_by = spec.get("split_by")
    if not split_by:
        return _fetch_paged(adapter, spec, params)

    split_param = str(split_by.get("param") or "")
    split_values = (
        list(split_values_override)
        if split_values_override is not None
        else list(split_by.get("values") or [])
    )
    if not split_param or not split_values:
        raise ValueError(f"{spec['domain']}: split_by 必须声明非空 param/values")

    combined: list[dict[str, Any]] = []
    date_param = str(spec.get("date_param") or "trade_date")
    batch_date = str(params.get(date_param) or "").replace("-", "")
    values_since = {} if split_values_override is not None else {
        str(key).upper(): str(value).replace("-", "")
        for key, value in (
            (spec.get("batch_completeness") or {}).get("required_groups_since") or {}
        ).items()
    }
    for value in split_values:
        since = values_since.get(str(value).upper())
        if since and batch_date and batch_date < since:
            continue
        request = {**params, split_param: value}
        try:
            rows = _fetch_paged(adapter, spec, request)
        except Exception as exc:
            if fragment_callback is not None:
                fragment_callback(str(value), request, None, exc)
            raise
        if fragment_callback is not None:
            fragment_callback(str(value), request, rows, None)
        if rows is None:
            # 任一分片终败即整个日期失败；绝不能把已返回分片先写成半截交易日。
            return None
        combined.extend(rows)
    return combined


_SAMPLE_DIR = _REPO / "backend" / "tests" / "fixtures" / "domain_samples"
_DEFAULT_SAMPLE_DIR = _SAMPLE_DIR   # 未被 monkeypatch 时的真实 git-tracked 路径, 见下方门判断


def _capture_domain_sample(spec: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """域真实样本存档 (字段语义契约, 根因 A 根治).

    WHY: dc_member 方向反事故 — registry grain 只声明键集合无字段语义, 消费代码
    fixture 用抽象命名 (C1/600000) 时测试与实现会一致地错。首批写入时把前 5 行
    真实数据存进 git, 任何消费代码的测试必须可用真实形态 — 抽象 fixture 失去借口。
    幂等: 样本文件已存在则跳过 (样本是注册时刻快照, 不随数据漂移)。失败不挡写入。

    单测跑合成/伪域 (monkeypatch adapter + fake registry) 经此函数会把假数据写进
    git-tracked 目录 (2026-07-04 实测: test_by_trade_date_fixed_params.py 跑一次
    产生 3 个污染文件) — pytest 运行时用 PYTEST_CURRENT_TEST 环境变量(pytest 标准
    机制自动设置)跳过存档, 不污染真样本契约。仅当 _SAMPLE_DIR 仍是默认 git-tracked 路径时
    才跳过; test_domain_sample_captured_on_first_batch 等专测本机制的用例会
    monkeypatch _SAMPLE_DIR 指向 tmp_path, 此时应正常存档 (测的就是这个存档行为本身)。
    """
    if os.environ.get("PYTEST_CURRENT_TEST") and _SAMPLE_DIR == _DEFAULT_SAMPLE_DIR:
        return
    path = _SAMPLE_DIR / f"{spec['domain']}.json"
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        sample = {
            "domain": spec["domain"],
            "api": spec.get("api"),
            "grain": spec.get("grain"),
            "note": "首批真实样本 (字段语义契约): 消费代码 fixture 必须用本形态, 禁抽象命名",
            "rows": rows[:5],
        }
        path.write_text(json.dumps(sample, ensure_ascii=False, indent=1, default=str),
                        encoding="utf-8")
        log.info("域样本已存档 %s (%d 行)", path.name, min(5, len(rows)))
    except Exception as exc:  # noqa: BLE001 — 样本存档失败不挡数据写入, 但必须可见
        log.warning("域样本存档失败 %s: %s", spec["domain"], str(exc)[:120])


def _prepare_batch_df(
    spec: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    effective_min_rows: int | None = None,
    expected_partition: dict[str, Any] | None = None,
):
    """Dedup + validate, then hand the batch to the write transaction.

    Landing preserves the provider response.  ``universe_filter`` no longer
    deletes rows before raw write; project-universe filtering belongs at
    canonical/serve via ``universe_serve_filter`` with policy-hash evidence.
    ``min_rows_per_batch`` is evaluated on post-dedup landing rows.
    """
    import pandas as pd

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["built_at"] = datetime.now(timezone.utc).isoformat()
    table = spec["target_table"]
    grain: list[str] = list(spec["grain"])
    missing = [g for g in grain if g not in df.columns]
    if missing:
        raise ValueError(f"{table}: api 返回缺 grain 列 {missing} — registry 条目或上游 schema 变了")

    # 批内去重 (复审 HIGH 根因, 2026-06-22): API/分页可返回同 grain 重复行 (limit_list_d 实测单日插14次,
    # 23116 重复行膨胀涨停家数 14x)。DELETE-INSERT MERGE 只跨批去重不去批内 → 必须先 drop_duplicates(grain),
    # 否则批内重复直接累积入库 (grain 无 DB 唯一约束兜底)。keep='last' 取最新一条。
    _ndup = len(df)
    df = df.drop_duplicates(subset=grain, keep="last")
    if len(df) < _ndup:
        log.info("[dedup] %s 批内去重 %d 行 (同 grain 重复)", table, _ndup - len(df))

    # A4 landing purity: validate filter-column wiring, never drop provider rows here.
    if spec.get("universe_filter"):
        from services.data_sources.universe_serve_filter import (
            normalize_provider_security_code,
            validate_universe_filter_column,
        )

        ucol = spec.get("universe_filter_col") or grain[0]
        if ucol in df.columns:
            # Repair bare BJ/A codes (e.g. 874075 → 874075.BJ) before shape gate.
            df[ucol] = df[ucol].map(normalize_provider_security_code)
            validate_universe_filter_column(
                df[ucol].tolist(), filter_column=str(ucol), table=table
            )
            log.info(
                "[universe-filter] %s landing preserves all %d provider rows; "
                "project-universe filter deferred to canonical/serve "
                "(universe_serve_filter + policy hash)",
                table,
                len(df),
            )
        else:
            raise ValueError(
                f"{table}: universe_filter_col={ucol!r} missing from provider columns"
            )

    if str(spec.get("write_mode") or "merge_grain") == "replace_partition":
        partition_by = [str(column) for column in spec.get("partition_by") or []]
        missing_partition = [column for column in partition_by if column not in df.columns]
        if not partition_by or missing_partition:
            raise ValueError(
                f"{spec['domain']}: replace_partition requires present partition_by; "
                f"configured={partition_by}, missing={missing_partition}"
            )
        if df[partition_by].isna().any(axis=None):
            reason = (
                "response does not match requested partition: observed NULL"
                if expected_partition is not None
                else "requires exactly one partition and partition values cannot be NULL"
            )
            raise BatchCompletenessError(
                f"{spec['domain']}: replace_partition {reason}"
            )
        observed_partitions = df[partition_by].drop_duplicates()
        if len(observed_partitions) != 1:
            raise BatchCompletenessError(
                f"{spec['domain']}: replace_partition requires exactly one partition; "
                f"observed={observed_partitions.astype(str).to_dict('records')[:5]}"
            )
        if expected_partition is not None:
            missing_expected = [
                column for column in partition_by
                if column not in expected_partition or expected_partition[column] is None
            ]
            if missing_expected:
                raise BatchCompletenessError(
                    f"{spec['domain']}: requested partition missing keys={missing_expected}"
                )

            def _normalise_partition_value(column: str, value: Any) -> str:
                text = str(value).strip()
                if column == str(spec.get("date_param") or "trade_date"):
                    return text[:10].replace("-", "")
                return text

            observed = observed_partitions.iloc[0].to_dict()
            mismatches = {
                column: {
                    "requested": expected_partition[column],
                    "observed": observed[column],
                }
                for column in partition_by
                if _normalise_partition_value(column, observed[column])
                != _normalise_partition_value(column, expected_partition[column])
            }
            if mismatches:
                raise BatchCompletenessError(
                    f"{spec['domain']}: response does not match requested partition: "
                    f"{mismatches}"
                )

    min_rows = int(
        spec.get("min_rows_per_batch", 0)
        if effective_min_rows is None
        else effective_min_rows
    )
    if len(df) < min_rows:
        raise BatchCompletenessError(
            f"{spec['domain']}: post_filter_rows={len(df)} < min_rows={min_rows}"
        )

    contract = spec.get("batch_completeness") or {}
    group_from = contract.get("group_from") or {}
    required = {str(v).upper() for v in (contract.get("required_groups") or [])}
    conditional = {
        str(group).upper(): str(since).replace("-", "")
        for group, since in (contract.get("required_groups_since") or {}).items()
    }
    date_col = str(spec.get("date_param") or "trade_date")
    batch_dates = (
        [str(value).replace("-", "") for value in df[date_col].dropna()]
        if date_col in df.columns
        else []
    )
    if batch_dates:
        batch_date = max(batch_dates)
        required.update(
            group for group, since in conditional.items() if batch_date >= since
        )
    if required:
        column = str(group_from.get("column") or "")
        transform = str(group_from.get("transform") or "")
        if column not in df.columns:
            raise BatchCompletenessError(
                f"{spec['domain']}: batch_completeness group column missing: {column!r}"
            )
        if transform == "exchange_suffix":
            observed = {
                str(value).rsplit(".", 1)[-1].upper()
                for value in df[column].dropna().astype(str)
                if "." in str(value)
            }
        elif transform == "identity":
            observed = {
                str(value).strip().upper()
                for value in df[column].dropna().astype(str)
                if str(value).strip()
            }
        else:
            raise ValueError(
                f"{spec['domain']}: unsupported batch_completeness transform={transform!r}"
            )
        missing_groups = sorted(required - observed)
        if missing_groups:
            raise BatchCompletenessError(
                f"{spec['domain']}: required_groups missing={missing_groups}, "
                f"observed={sorted(observed)}"
            )
    return df


def _authorize_disclosure_legacy_raw_write(
    domain: str, *, legacy_direct_only: bool = False
) -> None:
    """E0: naked legacy raw writes require explicit legacy_direct_only test escape.

    Production disclosure domains route through formal dual-write; this permit is
    not issued on the default path.
    """

    from services.data_sources.disclosure_boundaries import (
        DisclosureBoundaryError,
        authorize_nonconforming_direct_write,
        disclosure_boundary,
    )

    if disclosure_boundary(domain) is None:
        return
    if not legacy_direct_only:
        return
    try:
        authorize_nonconforming_direct_write(
            domain,
            conformity="NONCONFORMING",
            allow_test_escape=True,
        )
    except DisclosureBoundaryError as exc:
        raise ExecutionPolicyError(
            domain,
            mode="disabled",
            reason=exc.reason,
            detail=exc.detail,
        ) from exc


def _refuse_disclosure_formal_via_naked_write_batch(
    domain: str, spec: Mapping[str, Any]
) -> None:
    """Formal disclosure land/accept must not go through naked ``_write_batch``.

    Domains with a registered disclosure execution consumer publish only via
    ``propagate_disclosure_execution_contract`` + land/accept writers.  This
    path remains NONCONFORMING strangler (or refuse) — never accepted truth.
    """

    from services.data_sources.disclosure_boundaries import (
        DisclosureBoundaryError,
        disclosure_boundary,
        refuse_accepted_publication_claim,
    )
    from services.data_sources.formal_execution import disclosure_consumer_domains

    boundary = disclosure_boundary(domain)
    if boundary is None:
        return
    claim = str(
        spec.get("publication_claim") or spec.get("conformity") or ""
    ).strip()
    if claim and claim != "NONCONFORMING":
        try:
            refuse_accepted_publication_claim(domain, claim)
        except DisclosureBoundaryError as exc:
            raise ExecutionPolicyError(
                domain,
                mode="disabled",
                reason=exc.reason,
                detail=exc.detail,
            ) from exc
        if claim in {"accepted", "canonical", "landing", "ACCEPTED", "formal"}:
            raise ExecutionPolicyError(
                domain,
                mode="disabled",
                reason="disclosure_formal_requires_execution_handoff",
                detail=(
                    "naked _write_batch cannot claim formal disclosure publication; "
                    "use propagate_disclosure_execution_contract + land/accept"
                ),
            )
    if domain in disclosure_consumer_domains():
        # holders_top10 tracer: registry raw write is never the formal path.
        if claim in {"accepted", "canonical", "landing", "ACCEPTED", "formal", "DatasetSnapshot"}:
            raise ExecutionPolicyError(
                domain,
                mode="disabled",
                reason="disclosure_formal_requires_execution_handoff",
                detail=(
                    f"domain={domain} has disclosure execution consumer; "
                    "formal writes require contract handoff, not _write_batch"
                ),
            )


def _route_disclosure_formal_dual_write(
    conn,
    domain: str,
    spec: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> int | None:
    """E0: stk_holdertrade production writes go formal_only by default.

    Returns written count when routed; ``None`` leaves the naked merge path.
    Explicit ``legacy_direct_only`` keeps the NONCONFORMING escape hatch.
    ``enable_legacy_mirror`` on the spec is test/emergency only.
    """

    from services.data_sources.disclosure_boundaries import disclosure_boundary

    boundary = disclosure_boundary(domain)
    if boundary is None:
        return None
    if boundary.runtime_state not in {
        "formal_only",
        "formal_default_legacy_mirror",
    }:
        return None
    if boundary.landing_writer is None or boundary.canonical_writer is None:
        return None
    if bool(spec.get("legacy_direct_only")):
        return None
    if domain != "stk_holdertrade":
        # holders/org_holding use aif10 writers, not registry _write_batch.
        return None
    from services.data_sources.disclosure_dual_write import (
        write_stk_holdertrade_formal_then_mirror,
    )

    outcome = write_stk_holdertrade_formal_then_mirror(
        conn,
        rows,
        enable_legacy_mirror=bool(spec.get("enable_legacy_mirror")),
    )
    return int(outcome.canonical_rows)


def _write_batch(
    conn,
    spec: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    effective_min_rows: int | None = None,
    expected_partition: dict[str, Any] | None = None,
) -> int:
    """验证后原子 MERGE on grain；失败批不改旧数据。"""
    domain = str(spec.get("domain") or "")
    legacy_direct_only = bool(spec.get("legacy_direct_only"))
    _refuse_disclosure_formal_via_naked_write_batch(domain, spec)
    write_mode = str(spec.get("write_mode") or "merge_grain")
    if write_mode not in {"merge_grain", "replace_snapshot", "replace_partition"}:
        raise ValueError(
            f"{spec['domain']}: unsupported write_mode={write_mode!r}"
        )
    if not rows:
        return 0
    df = _prepare_batch_df(
        spec,
        rows,
        effective_min_rows=effective_min_rows,
        expected_partition=expected_partition,
    )
    if df.empty:
        return 0
    # After universe/grain prep: disclosure formal_only (keeps filter semantics).
    prepared_rows = df.to_dict(orient="records")
    routed = _route_disclosure_formal_dual_write(
        conn, domain, spec, prepared_rows
    )
    if routed is not None:
        return routed
    # Naked merge path: disclosure domains require explicit legacy_direct_only.
    from services.data_sources.disclosure_boundaries import disclosure_boundary

    if disclosure_boundary(domain) is not None and not legacy_direct_only:
        raise ExecutionPolicyError(
            domain,
            mode="disabled",
            reason="naked_disclosure_write_retired",
            detail=(
                "disclosure domains default to formal_only land→accept; "
                "set legacy_direct_only for test/emergency naked merge"
            ),
        )
    _authorize_disclosure_legacy_raw_write(
        domain, legacy_direct_only=legacy_direct_only
    )
    table = spec["target_table"]
    grain: list[str] = list(spec["grain"])

    # duck_adapter 包装层挡住 DataFrame replacement scan, 显式注册视图
    raw_con = getattr(conn, "_con", conn)
    raw_con.register("df", df)
    try:
        # NULL-safe 等值: grain 可空时 NULL 必须与 NULL 匹配，重跑才是覆盖而非累积。
        key = " AND ".join(f't."{g}" IS NOT DISTINCT FROM s."{g}"' for g in grain)
        cols = ", ".join(f'"{c}"' for c in df.columns)
        if write_mode == "replace_snapshot":
            delete_sql = f"DELETE FROM {table}"
        elif write_mode == "replace_partition":
            partition_by = list(spec.get("partition_by") or [])
            missing_partition = [column for column in partition_by if column not in df.columns]
            if not partition_by or missing_partition:
                raise ValueError(
                    f"{spec['domain']}: replace_partition requires present partition_by; "
                    f"configured={partition_by}, missing={missing_partition}"
                )
            partition_key = " AND ".join(
                f't."{column}" IS NOT DISTINCT FROM s."{column}"'
                for column in partition_by
            )
            delete_sql = (
                f"DELETE FROM {table} t WHERE EXISTS "
                f"(SELECT 1 FROM df s WHERE {partition_key})"
            )
        elif write_mode == "merge_grain":
            delete_sql = f"DELETE FROM {table} t WHERE EXISTS (SELECT 1 FROM df s WHERE {key})"
        else:  # validated before any table/view mutation
            raise AssertionError(f"unreachable write_mode={write_mode!r}")

        def _replace_in_transaction(
            widen_types: dict[str, str] | None = None,
        ) -> None:
            conn.execute("BEGIN TRANSACTION")
            try:
                # 首次建表、schema 演进与数据替换属于同一发布事务；后续故障不得
                # 留下空 target 或半演进 schema，让控制面误认作已发布数据集。
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM df LIMIT 0"
                )
                existing = {r[0] for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    f"WHERE table_name = '{table}'"
                ).fetchall()}
                for col in df.columns:
                    if col not in existing:
                        conn.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" VARCHAR')
                for col, target_type in (widen_types or {}).items():
                    conn.execute(
                        f'ALTER TABLE {table} ALTER COLUMN "{col}" '
                        f'SET DATA TYPE {target_type}'
                    )
                conn.execute(delete_sql)
                conn.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM df")
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001 — 保留原始写故障
                    log.exception("rollback 失败 table=%s", table)
                raise

        try:
            _replace_in_transaction()
        except Exception as exc:
            # 首批类型推断陷阱: 先 rollback 恢复旧行，再做单调加宽，最后从 DELETE
            # 开始重跑完整事务；绝不能在已删除旧行的半状态上只重试 INSERT。
            if "Conversion" not in type(exc).__name__ and "Conversion" not in str(exc):
                raise
            col_types = {r[0]: r[1] for r in conn.execute(
                f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table}'"
            ).fetchall()}
            widen_types: dict[str, str] = {}
            for col in df.columns:
                dtype = str(df[col].dtype).lower()
                cur = col_types.get(col, "")
                target = None
                if dtype.startswith("int") and cur in ("INTEGER", "SMALLINT", "TINYINT"):
                    target = "BIGINT"
                elif dtype.startswith("float") and cur in ("INTEGER", "SMALLINT", "TINYINT", "BIGINT"):
                    target = "DOUBLE"
                elif dtype in ("object", "str", "string") and cur not in ("VARCHAR", ""):
                    target = "VARCHAR"
                if target:
                    widen_types[col] = target
            if not widen_types:
                raise
            widened = [
                f"{col}:{col_types.get(col, '')}->{target}"
                for col, target in widen_types.items()
            ]
            log.warning("首批类型推断加宽 table=%s %s (完整事务重试)", table, widened)
            _replace_in_transaction(widen_types)
    finally:
        raw_con.unregister("df")
    _capture_domain_sample(spec, df.to_dict("records"))
    return len(df)


# allow_empty 交叉参照门默认阈值 (R1 根因2, 2026-07-03): 交叉域同日行数 > 此值而本域 0 行 = 可疑空。
# evidence: top_inst 8.5 年 (2018-2026, 2043 有数日) 单日行数 min=286/median=780, 从无 <50 行日;
#   16 个 0 行缺日 live 探针证实源端全有数据 = allow_empty 静默吞 (audit data_foundation_audit_20260703)。
_CROSS_CHECK_MIN_DEFAULT = 50


def _cross_check_suspicious_empty(
    conn, reg: dict[str, Any], spec: dict[str, Any], params: dict[str, Any]
) -> tuple[bool, int, str]:
    """allow_empty 域 0 行批的交叉参照门 (R1 根因2: 协议层区分"合法空"与"故障空").

    registry 域声明 cross_check_domain (如 top_inst→top_list / block_trade→daily) 时:
    本域当日 0 行 但 交叉域同日行数 > cross_check_min → 判可疑空 (网关间歇空响应被
    allow_empty 吞掉的指纹), 调用方入 failure_queue + 计 failed_batches, 不当合法空。
    交叉表缺失 / 交叉域同日也空 → 合法空放行 (真·市场无数据两域应同空)。
    返回 (suspicious, cross_rows, cross_table)。
    """
    cross_domain = spec.get("cross_check_domain")
    if not cross_domain:
        return False, 0, ""
    d = params.get(spec.get("date_param", "trade_date"))
    if not d:
        return False, 0, ""  # 非按日批 (range/full_refresh) 无单日交叉语义
    known_empty = {str(x).replace("-", "") for x in (spec.get("known_empty_days") or [])}
    if str(d).replace("-", "") in known_empty:
        return False, 0, ""  # 墓碑: 实测核证过源端真空的日 (与 drain 同语义) — 不进失败循环
    try:
        cross = domain_spec(reg, cross_domain)
    except KeyError:
        log.warning("domain=%s cross_check_domain=%s 未注册 — 交叉门跳过 (修 registry)",
                    spec["domain"], cross_domain)
        return False, 0, ""
    table = cross["target_table"]
    cross_date_col = cross.get("date_param", "trade_date")
    own = cross.get("target_db", "tushare_raw") != spec.get("target_db", "tushare_raw")
    if own:
        import duckdb

        from services.database_manifest import get_database_manifest
        c = duckdb.connect(  # rule-compliance: ok evidence=只读查交叉域行数, 非主库写
            str(get_database_manifest().path_for(cross.get("target_db", "tushare_raw"))),
            read_only=True)
    else:
        c = conn  # 同库: 复用本域写连接查交叉表 (top_inst/top_list 同在 tushare_raw)
    try:
        if not c.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone():
            return False, 0, table
        n = int(c.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE "{cross_date_col}" = ?', [d]
        ).fetchone()[0])
    finally:
        if own:
            c.close()
    threshold = int(spec.get("cross_check_min", _CROSS_CHECK_MIN_DEFAULT))
    return n > threshold, n, table


def _record_suspicious_empty(spec: dict[str, Any], params: dict[str, Any],
                             cross_rows: int, cross_table: str) -> None:
    """可疑空日入 failure_queue (error_type='suspicious_empty') — 可见可追, 非静默接受."""
    from services.source_watermarks import record_source_failure

    conn = _smartmoney_conn()
    try:
        record_source_failure(
            conn,
            data_domain=f"sync:{spec['domain']}",
            source_name=spec["source"],
            source_tier=SOURCE_TIER_TUSHARE,
            error_type="suspicious_empty",
            last_error=json.dumps({
                "params": params,
                "cross_check_domain": spec.get("cross_check_domain"),
                "cross_table": cross_table,
                "cross_rows": cross_rows,
            }, ensure_ascii=False),
            commit=True,
        )
        try:
            conn.commit()
        except Exception:  # noqa: BLE001 — duckdb autocommit 兼容
            pass
    finally:
        conn.close()


def _last_watermark_date(domain: str, source: str) -> str | None:
    conn = _smartmoney_conn()
    try:
        from services.source_watermarks import ensure_source_watermark_schema

        ensure_source_watermark_schema(conn)
        row = conn.execute(
            "SELECT last_data_date FROM mart_data_source_watermark WHERE data_domain = ? AND source_name = ?",
            [f"sync:{domain}", source],
        ).fetchone()
        return str(row[0]).replace("-", "") if row and row[0] else None
    finally:
        conn.close()


def _failure_dates_from_error(raw: Any) -> set[str]:
    """从合法或被旧 1000 字符边界截断的 failure payload 恢复日期锚点。"""
    date_keys = {
        "trade_date", "ann_date", "report_date", "period", "end_date",
        "ex_date", "still_failed", "drain_still_failed", "earliest_failed_date",
    }
    found: set[str] = set()

    def _walk(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                _walk(child, str(child_key))
            return
        if isinstance(value, list):
            for child in value:
                _walk(child, key)
            return
        if key not in date_keys or value is None:
            return
        candidate = str(value).strip().replace("-", "")
        if len(candidate) == 8 and candidate.isdigit():
            found.add(candidate)

    if not raw:
        return found
    text = str(raw)
    try:
        _walk(json.loads(text))
    except (TypeError, ValueError):
        # 兼容旧记录：source_watermarks 曾把 JSON 生截到 1000 字符。批次按时间升序，
        # 首个日期通常仍在前部；只识别明确日期字段，绝不把任意 8 位业务数字当日期。
        key_pattern = "|".join(sorted(date_keys))
        for match in re.finditer(
            rf'"(?:{key_pattern})"\s*:\s*"(\d{{4}}-?\d{{2}}-?\d{{2}})"',
            text,
        ):
            candidate = match.group(1).replace("-", "")
            if len(candidate) == 8 and candidate.isdigit():
                found.add(candidate)
    return found


def _compact_failure_error(error: str | None, earliest: str | None) -> str:
    """生成不会被 1000 字符存储边界截成坏 JSON 的失败摘要。"""
    payload: dict[str, Any] = {
        "earliest_failed_date": earliest,
        "detail": str(error or "")[:400],
    }
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded) >= 900:
        payload.pop("detail", None)
        payload["detail_omitted"] = True
        encoded = json.dumps(payload, ensure_ascii=False)
    return encoded


def _pending_failure_start(spec: dict[str, Any]) -> str | None:
    """从未关闭的批失败证据提取最早日期，供非 drain 域从缺口前沿重放。"""
    from services.source_watermarks import ensure_source_watermark_schema

    conn = _smartmoney_conn()
    try:
        ensure_source_watermark_schema(conn)
        rows = conn.execute(
            "SELECT last_error FROM mart_data_source_failure_queue "
            "WHERE data_domain = ? AND source_name = ? "
            "AND error_type = 'sync_batch_failed' AND status != 'resolved'",
            [f"sync:{spec['domain']}", spec["source"]],
        ).fetchall()
    finally:
        conn.close()
    found = {
        date
        for row in rows
        for date in _failure_dates_from_error(row[0])
    }
    return min(found) if found else None


def _record_outcome(spec: dict[str, Any], *, ok: bool, last_date: str | None,
                    rows: int, error: str | None = None,
                    resolve_failures: bool = False,
                    failure_type: str = "sync_batch_failed",
                    provider_succeeded: bool = False,
                    success_at: Any | None = None) -> None:
    """watermark 推进与失败记录解耦 (2026-07-06 全面数据审计根因根治):
    此前 `ok=False`(range 内任一批失败, 哪怕只有 1 个历史日) 时整个跳过 upsert_watermark ——
    即便 last_date 已经正确前移到本轮真正成功写到的最新日期, watermark 时间戳仍原地冻结。
    实测 stk_factor_pro 冻结 17 天 / block_trade 曾冻结 9.5 个月: 只要该域某个 (通常是历史)
    批次持续失败(suspicious_empty/below_min_rows 等), 后续每次跑批哪怕新日子都写成功了,
    watermark 也永远推不动——冻结的是"监控信号"本身, 而不是数据真的没更新。
    修复: 有日期域的 frontier 只看 `last_date 是否有真实前移`；无日期 full-refresh 则只刷新
    真实 provider success 时间/行数并保留 NULL 日期。两者都与 `ok`(本轮是否存在任何失败批,
    用于决定要不要同时记 failure_queue)解耦。普通 run_domain 只有在实际覆盖 failure queue
    的日期/期间锚点时才有关账证据；完整快照成功或完整、非截断的 drain 重扫也可关账。
    也就是说，只有覆盖失败锚点的完整重放（日期 drain、公告日/报告期重放或 full-refresh）
    才能 resolve；
    半清时 watermark 可按真实成功日期推进，但真失败继续保留。"""
    from services.source_watermarks import (
        ensure_source_watermark_schema,
        record_source_failure,
        resolve_source_failures,
        upsert_watermark,
    )

    conn = _smartmoney_conn()
    try:
        domain_key = f"sync:{spec['domain']}"
        ensure_source_watermark_schema(conn)
        if last_date or provider_succeeded:
            # 历史显式重放可以成功写到旧日期，但绝不能让 freshness frontier 倒退。
            # 无日期列的 full-refresh 快照仍须刷新真实 success 时间/行数；这时保留原
            # last_data_date（可为 NULL），绝不拿运行日伪造数据日期。
            existing = conn.execute(
                "SELECT last_data_date FROM mart_data_source_watermark "
                "WHERE data_domain = ? AND source_name = ? AND source_tier = ?",
                [domain_key, spec["source"], SOURCE_TIER_TUSHARE],
            ).fetchone()
            existing_date = (
                str(existing[0]).replace("-", "")
                if existing and existing[0]
                else None
            )
            if last_date:
                candidate = str(last_date).replace("-", "")
                monotonic_date = max(candidate, existing_date) if existing_date else candidate
            else:
                monotonic_date = existing_date
            upsert_watermark(conn, {
                "data_domain": domain_key,
                "source_name": spec["source"],
                "source_tier": SOURCE_TIER_TUSHARE,
                "last_success_at": (
                    str(success_at)
                    if success_at is not None
                    else datetime.now(timezone.utc).isoformat()
                ),
                "last_data_date": monotonic_date,
                "row_count": rows,
                "parser_version": "sync_runner_v1",
            })
        if ok:
            if provider_succeeded:
                # 一次真实 provider 成功足以证明配额墙已恢复，但不能洗掉尚未重放的数据批失败。
                resolve_source_failures(
                    conn,
                    data_domain=domain_key,
                    source_name=spec["source"],
                    error_type="sync_quota_halt",
                    commit=True,
                )
            if resolve_failures:
                resolve_source_failures(
                    conn,
                    data_domain=domain_key,
                    source_name=spec["source"],
                    commit=True,
                )
        else:
            if failure_type == "sync_batch_failed":
                dates = _failure_dates_from_error(error)
                previous = conn.execute(
                    "SELECT last_error FROM mart_data_source_failure_queue "
                    "WHERE data_domain = ? AND source_name = ? "
                    "AND error_type = 'sync_batch_failed' AND status != 'resolved'",
                    [domain_key, spec["source"]],
                ).fetchall()
                dates.update(
                    date
                    for row in previous
                    for date in _failure_dates_from_error(row[0])
                )
                # 同一 failure_id 后写会替换 last_error；显式保留历史最早未复核 frontier。
                error = _compact_failure_error(error, min(dates) if dates else None)
            record_source_failure(
                conn,
                data_domain=domain_key,
                source_name=spec["source"],
                source_tier=SOURCE_TIER_TUSHARE,
                error_type=failure_type,
                last_error=error,
                commit=True,
            )
        try:
            conn.commit()
        except Exception:  # noqa: BLE001 — duckdb autocommit 兼容
            pass
    finally:
        conn.close()


def _calendar_days(start: str, end: str) -> list[str]:
    """全日历日列表 (YYYYMMDD, 含周末) [start, end]。用于 by_ann_date — 公告日可落任意日 (含周末, 实测18%),
    不能用交易日历过滤 (会漏周末公告)。增量 watermark→today 仅几日, 空日 fetch 返0行廉价。"""
    import datetime as _dt
    s = _dt.date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    e = _dt.date(int(end[:4]), int(end[4:6]), int(end[6:8]))
    out, d = [], s
    while d <= e:
        out.append(d.strftime("%Y%m%d"))
        d += _dt.timedelta(days=1)
    return out


def _publish_trade_cal_accepted_generation(spec: dict[str, Any]) -> dict[str, Any]:
    """Caller-only S1→S2: land calendar generation, then accept (no fused publish)."""

    from services.data_sources.calendar_contract import calendar_contract_for_spec
    from services.data_sources.calendar_runtime import (
        CalendarRuntimeError,
        accept_calendar_from_landing,
        capture_and_land_authorized_calendar_generation,
    )

    contract = calendar_contract_for_spec(spec)
    apply_fetch_socket_timeout(spec)
    adapter = _adapter(str(spec["source"]))

    def _fetch_page(params: Mapping[str, Any]):
        return _fetch_with_retry(adapter, spec, dict(params))

    from services.data_sources.calendar_landing import CalendarAcceptanceError
    from services.data_sources.calendar_schema import CalendarSchemaError

    conn = _target_conn(spec)
    try:
        try:
            batch = capture_and_land_authorized_calendar_generation(
                conn,
                contract,
                fetch_page=_fetch_page,
                bootstrap=True,
            )
            outcome = accept_calendar_from_landing(
                conn, str(batch.batch_id), contract, bootstrap=False
            )
        except (CalendarRuntimeError, CalendarAcceptanceError, CalendarSchemaError) as exc:
            return {
                "domain": "trade_cal",
                "status": "error",
                "batches": 0,
                "rows": 0,
                "failed_batches": 1,
                "error": str(exc)[:500],
                "transport": "land_then_accept",
            }
    finally:
        conn.close()

    accepted = outcome.status == "ACCEPTED"
    return {
        "domain": "trade_cal",
        "status": "ok" if accepted else str(outcome.status).lower(),
        "batches": 1,
        "rows": int(outcome.row_count or 0),
        "failed_batches": 0 if accepted else 1,
        "batch_id": outcome.batch_id,
        "generation_id": outcome.generation_id,
        "content_hash": outcome.content_hash,
        "rejection_code": outcome.rejection_code,
        "publication": "accepted_calendar_generation",
        "transport": "land_then_accept",
    }


# Formal daily/stock_st: each accepted partition is still one trade_date.
# CLI may pass an identical day or a bounded calendar window that expands to
# trading days and publishes day-by-day. Refuse unbounded/--backfill/mass years.
# Cap (~40 trading days) supports claimable-power B0 measurement only.
AUTHORIZED_SECURITY_DAY_MAX_WINDOW_DAYS = 40

# 走"授权短窗"发布路径的域。这些域**结构上拒绝无参数 --drain**(见
# _require_authorized_short_trade_date_window), 所以它们没有自动补缺口的路径:
# 漏掉的交易日会一直留着, 只能由人带 --start/--end 手工补。
# 提成模块级常量是为了让 check_continuity_integrity 能 import 同一份真相 ——
# 此前它按 hardcode 假设所有域都能 --drain, 给出的补拉命令对这两个域跑不通
# (2026-08-17 实测: 照它的提示跑得到 SyncWindowError)。
# NOTE: 归属仍是代码而非 YAML, 与 docs/engineering_governance.md §7(配置与 hardcoding)
# 相悖; 搬进 registry 是独立的一步。
AUTHORIZED_SHORT_WINDOW_DOMAINS = frozenset({"daily", "stock_st"})


def _require_authorized_short_trade_date_window(
    domain: str,
    *,
    backfill: bool,
    resume: bool,
    start: str | None,
    end: str | None,
    max_dates: int | None,
    trading_day_values: list[str] | None = None,
) -> list[str]:
    """Resolve authorized formal daily/ST trade dates from --start/--end.

    Returns 1..AUTHORIZED_SECURITY_DAY_MAX_WINDOW_DAYS trading days inclusive.
    Each day is published via the single-day accepted path (no batch merge).
    """

    if max_dates is not None:
        raise SyncWindowError("--max-dates is only valid for --drain")
    if backfill or resume:
        raise SyncWindowError(
            f"domain={domain} accepted partitions are authorized short-window "
            "single-day only; refuse --backfill/--resume"
        )
    if start is None or end is None:
        raise SyncWindowError(
            f"domain={domain} authorized short window requires explicit "
            "--start/--end (same day or <= "
            f"{AUTHORIZED_SECURITY_DAY_MAX_WINDOW_DAYS} trading days)"
        )
    start_c = str(start).replace("-", "")
    end_c = str(end).replace("-", "")
    for label, value in (("start", start_c), ("end", end_c)):
        if len(value) != 8 or not value.isdigit():
            raise SyncWindowError(
                f"domain={domain} trade_date {label} must be YYYYMMDD; "
                f"got {value!r}"
            )
    if start_c > end_c:
        raise SyncWindowError(
            f"domain={domain} start must be <= end; got start={start_c} end={end_c}"
        )
    if trading_day_values is not None:
        days = [
            d
            for d in trading_day_values
            if len(d) == 8 and d.isdigit() and start_c <= d <= end_c
        ]
    else:
        days = trading_days(start_c, end_c)
    if not days:
        raise SyncWindowError(
            f"domain={domain} no trading days in [{start_c},{end_c}]"
        )
    if len(days) > AUTHORIZED_SECURITY_DAY_MAX_WINDOW_DAYS:
        raise SyncWindowError(
            f"domain={domain} authorized short window max "
            f"{AUTHORIZED_SECURITY_DAY_MAX_WINDOW_DAYS} trading days; "
            f"got {len(days)} in [{start_c},{end_c}] — refuse mass backfill"
        )
    return days


def _require_authorized_single_trade_date(
    domain: str,
    *,
    backfill: bool,
    resume: bool,
    start: str | None,
    end: str | None,
    max_dates: int | None,
) -> str:
    """Backward-compatible single-day resolver (start must equal end)."""

    days = _require_authorized_short_trade_date_window(
        domain,
        backfill=backfill,
        resume=resume,
        start=start,
        end=end,
        max_dates=max_dates,
    )
    if len(days) != 1 or days[0] != str(start).replace("-", ""):
        raise SyncWindowError(
            f"domain={domain} authorized single-day helper requires start==end; "
            f"got start={start} end={end} days={days}"
        )
    return days[0]


def _publish_security_day_short_window(
    domain: str,
    spec: dict[str, Any],
    *,
    trade_dates: list[str],
    trigger_mode: TriggerMode | str = "manual",
) -> dict[str, Any]:
    """Publish each authorized trade_date via the formal single-day path."""

    day_results: list[dict[str, Any]] = []
    total_rows = 0
    total_batches = 0
    failed = 0
    last_ok: str | None = None
    completed_ok = 0
    for trade_date in trade_dates:
        result = _publish_security_day_accepted_partition(
            domain,
            spec,
            trade_date=trade_date,
            trigger_mode=trigger_mode,
        )
        day_results.append(result)
        total_rows += int(result.get("rows") or 0)
        total_batches += int(result.get("batches") or 0)
        day_failed = int(result.get("failed_batches") or 0)
        failed += day_failed
        if day_failed == 0:
            last_ok = trade_date
            completed_ok += 1
        else:
            # Fail closed: stop so a mid-window provider failure is visible.
            break
    publication = (
        day_results[0].get("publication")
        if len(day_results) == 1
        else "accepted_security_day_short_window"
    )
    return {
        "domain": domain,
        "status": "ok" if failed == 0 else "partial",
        "batches": total_batches,
        "rows": total_rows,
        "failed_batches": failed,
        "publication": publication,
        "partition_values": [str(r.get("partition_value") or "") for r in day_results if int(r.get("failed_batches") or 0) == 0],
        "trade_dates": list(trade_dates),
        "day_results": day_results,
        "last_date": last_ok,
        "window_days_requested": len(trade_dates),
        "window_days_completed": completed_ok,
    }


def _publish_security_day_accepted_partition(
    domain: str,
    spec: dict[str, Any],
    *,
    trade_date: str,
    trigger_mode: TriggerMode | str = "manual",
) -> dict[str, Any]:
    """Caller-only S1→S2: land then accept one trade_date (no fused publish)."""

    from services.data_sources.security_day_partition import SecurityDayError
    from services.data_sources.security_day_transport import (
        land_then_accept_authorized_security_day,
    )

    eligibility = eligible_end_date(spec, trigger_mode=trigger_mode)
    operation_window = resolve_operation_window(
        eligibility,
        requested_start=trade_date,
        requested_end=trade_date,
    )
    partition = operation_window.effective_end or trade_date
    apply_fetch_socket_timeout(spec)
    adapter = _adapter(str(spec["source"]))

    def _fetch_rows(params: Mapping[str, Any]):
        request = {"trade_date": str(params.get("trade_date") or partition)}
        return _fetch_with_retry(adapter, spec, request)

    from services.data_sources.nominal_ohlcv_runtime import NominalOhlcvRuntimeError
    from services.data_sources.security_day_acquire import (
        ACQUIRE_MODE_PROVIDER_TUSHARE,
        resolve_security_day_acquire,
    )
    from services.data_sources.stock_st_runtime import StockStRuntimeError

    acquired = resolve_security_day_acquire(
        ACQUIRE_MODE_PROVIDER_TUSHARE,
        domain,
        trade_date=partition,
        fetch_rows=_fetch_rows,
    )

    if domain == "daily":
        publication = "accepted_nominal_ohlcv_partition"
    elif domain == "stock_st":
        publication = "accepted_stock_st_partition"
    else:
        raise SyncWindowError(
            f"domain={domain} is not a security-day accepted publication"
        )

    # Same-day vendor vacuum → typed pending_publish (not failed_batches /
    # not tombstone).
    # (a) Any formal domain before available_after (daily 18:00 morning).
    # (b) stock_st only after optimistic available_after=09:20 while still
    #     same-day empty (measured UI Run B 09:23). Daily/other after their
    #     true window stay fail-closed below. Non-today empty always fails closed.
    if not acquired.rows:
        probe_params = {"trade_date": partition}
        if _is_pre_publish_same_day_zero(spec, probe_params):
            pending_reason = "pre_available_after_zero_rows"
        elif domain == "stock_st" and _is_same_day_partition(spec, probe_params):
            pending_reason = "same_day_vendor_vacuum"
        else:
            pending_reason = None
        if pending_reason is not None:
            log.info(
                "pending_publish domain=%s trade_date=%s reason=%s "
                "(same-day zero_rows; retry when vendor publishes)",
                domain,
                partition,
                pending_reason,
            )
            return {
                "domain": domain,
                "status": "ok",
                "batches": 0,
                "rows": 0,
                "failed_batches": 0,
                "pending_publish": True,
                "pending_publish_reason": pending_reason,
                "publication": publication,
                "transport": "land_then_accept",
                "acquire_mode": acquired.acquire_mode,
                "eligible_end": eligibility.eligible_end,
                "eligibility_reason": eligibility.reason,
                "partition_value": partition,
            }

    def _acquired_rows(_params: Mapping[str, Any]):
        return list(acquired.rows)

    if domain == "daily":
        from services.data_sources.nominal_ohlcv_contract import (
            nominal_ohlcv_contract_for_spec,
        )

        contract = nominal_ohlcv_contract_for_spec(spec)
        publish = lambda conn: land_then_accept_authorized_security_day(
            "daily",
            conn,
            contract,
            trade_date=partition,
            fetch_rows=_acquired_rows,
            bootstrap=True,
        )
    else:
        from services.data_sources.stock_st_contract import stock_st_contract_for_spec

        contract = stock_st_contract_for_spec(spec)
        publish = lambda conn: land_then_accept_authorized_security_day(
            "stock_st",
            conn,
            contract,
            trade_date=partition,
            fetch_rows=_acquired_rows,
            bootstrap=True,
        )

    conn = _target_conn(spec)
    try:
        try:
            outcome = publish(conn)
        except (
            SecurityDayError,
            NominalOhlcvRuntimeError,
            StockStRuntimeError,
        ) as exc:
            return {
                "domain": domain,
                "status": "error",
                "batches": 0,
                "rows": 0,
                "failed_batches": 1,
                "error": str(exc)[:500],
                "publication": publication,
                "transport": "land_then_accept",
                "acquire_mode": acquired.acquire_mode,
            }
    finally:
        conn.close()

    accepted = outcome.status == "ACCEPTED"
    return {
        "domain": domain,
        "status": "ok" if accepted else str(outcome.status).lower(),
        "batches": 1,
        "rows": int(outcome.row_count or 0),
        "failed_batches": 0 if accepted else 1,
        "batch_id": outcome.batch_id,
        "partition_value": outcome.partition_value,
        "content_hash": outcome.content_hash,
        "rejection_code": outcome.rejection_code,
        "publication": publication,
        "transport": "land_then_accept",
        "acquire_mode": acquired.acquire_mode,
        "eligible_end": eligibility.eligible_end,
        "eligibility_reason": eligibility.reason,
    }


def accept_disclosure_from_landing_batch(
    domain: str,
    *,
    batch_id: str,
) -> dict[str, Any]:
    """E0 S2 CLI: accept one LANDED disclosure batch. Zero provider fetch."""

    from services.data_sources.disclosure_transport import (
        DISCLOSURE_TRANSPORT_DOMAINS,
        DisclosureTransportError,
        accept_disclosure_from_landing,
        disclosure_target_db_alias,
    )
    from services.database_manifest import get_database_manifest
    from services.duck_adapter import connect

    if domain not in DISCLOSURE_TRANSPORT_DOMAINS:
        raise SyncWindowError(
            "accept-from-landing disclosure supports "
            f"{sorted(DISCLOSURE_TRANSPORT_DOMAINS)}; got domain={domain}"
        )
    resolved = str(batch_id or "").strip()
    if not resolved:
        raise SyncWindowError("--accept-from-landing requires --batch-id")
    db_alias = disclosure_target_db_alias(domain)
    path = get_database_manifest().path_for(db_alias)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(path), read_only=False)
    try:
        try:
            outcome = accept_disclosure_from_landing(
                domain, conn, resolved, bootstrap=True
            )
        except DisclosureTransportError as exc:
            raise SyncWindowError(str(exc)) from exc
    finally:
        conn.close()

    accepted = outcome.status == "ACCEPTED"
    return {
        "domain": domain,
        "status": "ok" if accepted else str(outcome.status).lower(),
        "batches": 1,
        "rows": int(outcome.row_count or 0),
        "failed_batches": 0 if accepted else 1,
        "batch_id": outcome.batch_id,
        "partition_value": outcome.partition_value,
        "content_hash": outcome.content_hash,
        "rejection_code": getattr(outcome, "rejection_code", None),
        "publication": f"accepted_{domain}_from_landing",
        "transport": "accept_from_landing",
        "target_db": db_alias,
    }


def land_disclosure_partition_from_legacy_batch(
    domain: str,
    *,
    partition: str,
) -> dict[str, Any]:
    """E0 S1 CLI: land one disclosure partition from local legacy. No accept."""

    from services.data_sources.disclosure_transport import (
        DISCLOSURE_TRANSPORT_DOMAINS,
        DisclosureTransportError,
        disclosure_target_db_alias,
        land_disclosure_partition_from_legacy,
    )
    from services.database_manifest import get_database_manifest
    from services.duck_adapter import connect

    if domain not in DISCLOSURE_TRANSPORT_DOMAINS:
        raise SyncWindowError(
            "land-only disclosure supports "
            f"{sorted(DISCLOSURE_TRANSPORT_DOMAINS)}; got domain={domain}"
        )
    part = "".join(ch for ch in str(partition or "") if ch.isdigit())[:8]
    if len(part) != 8:
        raise SyncWindowError(
            f"disclosure land-only requires YYYYMMDD partition; got {partition!r}"
        )
    db_alias = disclosure_target_db_alias(domain)
    path = get_database_manifest().path_for(db_alias)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(path), read_only=False)
    try:
        try:
            batch = land_disclosure_partition_from_legacy(
                domain, conn, partition=part, bootstrap=True
            )
        except DisclosureTransportError as exc:
            err = str(exc)
            # Local-raw only: empty partition is a typed no-announcement day,
            # not a hard failure. Enables ≤40d window broaden across weekends
            # without inventing mass / crawling past real writer errors.
            if "no legacy rows" in err:
                return {
                    "domain": domain,
                    "status": "empty_skip",
                    "batches": 0,
                    "rows": 0,
                    "failed_batches": 0,
                    "error": err[:500],
                    "partition_value": part,
                    "publication": "land_only_disclosure_from_local_raw",
                    "transport": "land_only",
                    "acquire_mode": "local_legacy_raw_materialize",
                    "target_db": db_alias,
                }
            return {
                "domain": domain,
                "status": "error",
                "batches": 0,
                "rows": 0,
                "failed_batches": 1,
                "error": err[:500],
                "partition_value": part,
                "publication": "land_only_disclosure_from_local_raw",
                "transport": "land_only",
                "acquire_mode": "local_legacy_raw_materialize",
                "target_db": db_alias,
            }
    finally:
        conn.close()

    return {
        "domain": domain,
        "status": "ok",
        "batches": 1,
        "rows": len(tuple(batch.rows)),
        "failed_batches": 0,
        "batch_id": batch.batch_id,
        "partition_value": batch.partition_value,
        "publication": "land_only_disclosure_from_local_raw",
        "transport": "land_only",
        "acquire_mode": "local_legacy_raw_materialize",
        "target_db": db_alias,
    }


def land_disclosure_partition_from_provider_batch(
    domain: str,
    *,
    partition: str,
) -> dict[str, Any]:
    """E0 S1 CLI: land one disclosure partition from provider. No accept."""

    from services.data_sources.disclosure_transport import (
        DISCLOSURE_PROVIDER_LAND_DOMAINS,
        DISCLOSURE_TRANSPORT_DOMAINS,
        DisclosureTransportError,
        disclosure_target_db_alias,
        land_disclosure_partition_from_provider,
    )
    from services.database_manifest import get_database_manifest
    from services.duck_adapter import connect

    if domain not in DISCLOSURE_TRANSPORT_DOMAINS:
        raise SyncWindowError(
            "land-only disclosure supports "
            f"{sorted(DISCLOSURE_TRANSPORT_DOMAINS)}; got domain={domain}"
        )
    if domain not in DISCLOSURE_PROVIDER_LAND_DOMAINS:
        raise SyncWindowError(
            f"disclosure provider land supports only "
            f"{sorted(DISCLOSURE_PROVIDER_LAND_DOMAINS)} "
            "(by_ann_date|by_notice_date full-market); "
            f"domain={domain} requires --from-local-raw "
            "(org_holding by-period mass / no by-date faucet = BLOCKED)"
        )
    part = "".join(ch for ch in str(partition or "") if ch.isdigit())[:8]
    if len(part) != 8:
        raise SyncWindowError(
            f"disclosure land-only requires YYYYMMDD partition; got {partition!r}"
        )
    db_alias = disclosure_target_db_alias(domain)
    path = get_database_manifest().path_for(db_alias)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(path), read_only=False)
    try:
        try:
            batch = land_disclosure_partition_from_provider(
                domain, conn, partition=part, bootstrap=True
            )
        except DisclosureTransportError as exc:
            return {
                "domain": domain,
                "status": "error",
                "batches": 0,
                "rows": 0,
                "failed_batches": 1,
                "error": str(exc)[:500],
                "partition_value": part,
                "publication": "land_only_disclosure_from_provider",
                "transport": "land_only",
                "acquire_mode": "provider",
                "target_db": db_alias,
            }
    finally:
        conn.close()

    return {
        "domain": domain,
        "status": "ok",
        "batches": 1,
        "rows": len(tuple(batch.rows)),
        "failed_batches": 0,
        "batch_id": batch.batch_id,
        "partition_value": batch.partition_value,
        "publication": "land_only_disclosure_from_provider",
        "transport": "land_only",
        "acquire_mode": "provider",
        "target_db": db_alias,
    }


def _disclosure_partition_dates(start: str, end: str) -> list[str]:
    """Inclusive calendar YYYYMMDD range for disclosure axes (≤40d)."""

    from datetime import date, timedelta

    s = "".join(ch for ch in str(start or "") if ch.isdigit())[:8]
    e = "".join(ch for ch in str(end or "") if ch.isdigit())[:8]
    if len(s) != 8 or len(e) != 8:
        raise SyncWindowError(
            "disclosure land-only/--land-then-accept requires "
            "--start/--end as YYYYMMDD"
        )
    if s > e:
        raise SyncWindowError(f"start={s} after end={e}")
    start_d = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    end_d = date(int(e[:4]), int(e[4:6]), int(e[6:8]))
    span = (end_d - start_d).days + 1
    if span > 40:
        raise SyncWindowError(
            f"disclosure transport window exceeds 40 calendar days "
            f"(start={s} end={e} span={span})"
        )
    out: list[str] = []
    cur = start_d
    while cur <= end_d:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def _preflight_disclosure_cli_shape(
    args: argparse.Namespace,
    *,
    transport: str,
    now: datetime | None = None,
) -> None:
    """Registry-orthogonal disclosure CLI gates before writer lock / DB I/O."""

    from zoneinfo import ZoneInfo

    if args.drain or args.backfill or args.resume or args.all_due:
        raise SyncWindowError(
            f"--{transport.replace('_', '-')} cannot combine with "
            "--drain/--backfill/--resume/--all-due"
        )
    if transport == "accept_from_landing":
        if not str(getattr(args, "batch_id", "") or "").strip():
            raise SyncWindowError(
                "disclosure --accept-from-landing requires --batch-id"
            )
        return
    if not getattr(args, "from_local_raw", False):
        from services.data_sources.disclosure_transport import (
            DISCLOSURE_PROVIDER_LAND_DOMAINS,
        )

        domain = str(getattr(args, "domain", "") or "")
        if domain not in DISCLOSURE_PROVIDER_LAND_DOMAINS:
            raise SyncWindowError(
                "disclosure --land-only/--land-then-accept without "
                "--from-local-raw supports only "
                f"{sorted(DISCLOSURE_PROVIDER_LAND_DOMAINS)} "
                "(by_ann_date|by_notice_date full-market); "
                "org_holding requires --from-local-raw "
                "(by-period mass / no by-date faucet = BLOCKED)"
            )
    if args.start is None or args.end is None:
        raise SyncWindowError(
            f"--{transport.replace('_', '-')} requires --start and --end"
        )
    today = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).strftime("%Y%m%d")
    resolve_operation_window(
        DomainEligibility(today, False, "wall_clock_preflight"),
        requested_start=args.start,
        requested_end=args.end,
    )
    _disclosure_partition_dates(str(args.start), str(args.end))


def _run_disclosure_transport_window(
    domain: str,
    *,
    transport: str,
    start: str | None,
    end: str | None,
    batch_id: str | None = None,
    from_local_raw: bool = False,
) -> dict[str, Any]:
    """Thin disclosure land / accept / land-then-accept (local-raw or provider)."""

    from services.data_sources.disclosure_transport import (
        DISCLOSURE_PROVIDER_LAND_DOMAINS,
        DISCLOSURE_TRANSPORT_DOMAINS,
    )

    if domain not in DISCLOSURE_TRANSPORT_DOMAINS:
        raise SyncWindowError(
            f"disclosure transport unsupported domain={domain}"
        )
    if transport == "accept_from_landing":
        return accept_disclosure_from_landing_batch(domain, batch_id=str(batch_id or ""))

    if not from_local_raw and domain not in DISCLOSURE_PROVIDER_LAND_DOMAINS:
        raise SyncWindowError(
            "disclosure --land-only/--land-then-accept without --from-local-raw "
            f"supports only {sorted(DISCLOSURE_PROVIDER_LAND_DOMAINS)}; "
            f"domain={domain} requires --from-local-raw"
        )
    partitions = _disclosure_partition_dates(str(start), str(end))
    day_results: list[dict[str, Any]] = []
    total_rows = 0
    failed = 0
    empty_skips = 0
    last_ok: str | None = None
    completed_ok = 0
    acquire_label = "from_local_raw" if from_local_raw else "from_provider"
    for part in partitions:
        if from_local_raw:
            land_fn = land_disclosure_partition_from_legacy_batch
        else:
            land_fn = land_disclosure_partition_from_provider_batch
        if transport == "land_only":
            result = land_fn(domain, partition=part)
        elif transport == "land_then_accept":
            land_result = land_fn(domain, partition=part)
            if str(land_result.get("status") or "") == "empty_skip":
                result = dict(land_result)
                result["transport"] = "land_then_accept"
            elif int(land_result.get("failed_batches") or 0) != 0:
                result = dict(land_result)
                result["transport"] = "land_then_accept"
            else:
                result = accept_disclosure_from_landing_batch(
                    domain, batch_id=str(land_result["batch_id"])
                )
                result["transport"] = "land_then_accept"
                result["publication"] = (
                    f"land_then_accept_{domain}_{acquire_label}"
                )
        else:
            raise SyncWindowError(
                f"unsupported disclosure transport={transport}"
            )
        day_results.append(result)
        day_failed = int(result.get("failed_batches") or 0)
        failed += day_failed
        if str(result.get("status") or "") == "empty_skip":
            empty_skips += 1
            continue
        if day_failed == 0:
            completed_ok += 1
            last_ok = part
            total_rows += int(result.get("rows") or 0)
        else:
            # Match security-day: stop on first failure (no partial-accept crawl).
            # Local-raw empty_skip continues above; provider empty stays fail-closed.
            break

    status = "ok" if failed == 0 else "error"
    return {
        "domain": domain,
        "status": status,
        "batches": completed_ok,
        "rows": total_rows,
        "failed_batches": failed,
        "empty_skips": empty_skips,
        "last_ok_partition": last_ok,
        "partitions": partitions,
        "day_results": day_results,
        "transport": transport,
        "acquire_mode": (
            "local_legacy_raw_materialize" if from_local_raw else "provider"
        ),
        "window_days_requested": len(partitions),
        "window_days_attempted": len(day_results),
    }


def accept_security_day_from_landing_batch(
    domain: str,
    *,
    batch_id: str,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """S2 CLI surface: accept one LANDED batch. Zero ``_adapter`` / provider fetch."""

    from services.data_sources.disclosure_transport import DISCLOSURE_TRANSPORT_DOMAINS

    if domain in DISCLOSURE_TRANSPORT_DOMAINS:
        return accept_disclosure_from_landing_batch(domain, batch_id=batch_id)

    if domain not in {"daily", "stock_st"}:
        raise SyncWindowError(
            "accept-from-landing supports daily|stock_st|"
            f"{'|'.join(sorted(DISCLOSURE_TRANSPORT_DOMAINS))}; got domain={domain}"
        )
    batch_id = str(batch_id or "").strip()
    if not batch_id:
        raise SyncWindowError("--accept-from-landing requires --batch-id")
    reg = registry if registry is not None else load_registry()
    spec = domain_spec(reg, domain)
    _require_execution_enabled(spec)
    conn = _target_conn(spec)
    try:
        if domain == "daily":
            from services.data_sources.nominal_ohlcv_contract import (
                nominal_ohlcv_contract_for_spec,
            )
            from services.data_sources.nominal_ohlcv_runtime import (
                accept_nominal_ohlcv_from_landing,
            )

            outcome = accept_nominal_ohlcv_from_landing(
                conn,
                batch_id,
                nominal_ohlcv_contract_for_spec(spec),
                bootstrap=True,
            )
            publication = "accepted_nominal_ohlcv_from_landing"
        else:
            from services.data_sources.stock_st_contract import stock_st_contract_for_spec
            from services.data_sources.stock_st_runtime import (
                accept_stock_st_from_landing,
            )

            outcome = accept_stock_st_from_landing(
                conn,
                batch_id,
                stock_st_contract_for_spec(spec),
                bootstrap=True,
            )
            publication = "accepted_stock_st_from_landing"
    finally:
        conn.close()

    accepted = outcome.status == "ACCEPTED"
    return {
        "domain": domain,
        "status": "ok" if accepted else str(outcome.status).lower(),
        "batches": 1,
        "rows": int(outcome.row_count or 0),
        "failed_batches": 0 if accepted else 1,
        "batch_id": outcome.batch_id,
        "partition_value": outcome.partition_value,
        "content_hash": outcome.content_hash,
        "rejection_code": outcome.rejection_code,
        "publication": publication,
        "transport": "accept_from_landing",
    }


def _land_security_day_partition(
    domain: str,
    spec: dict[str, Any],
    *,
    trade_date: str,
    trigger_mode: TriggerMode | str = "manual",
    from_local_raw: bool = False,
) -> dict[str, Any]:
    """S1: land one trade_date. Optional local raw materialize (no provider)."""

    from services.data_sources.security_day_partition import SecurityDayError

    eligibility = eligible_end_date(spec, trigger_mode=trigger_mode)
    operation_window = resolve_operation_window(
        eligibility,
        requested_start=trade_date,
        requested_end=trade_date,
    )
    partition = operation_window.effective_end or trade_date
    conn = _target_conn(spec)
    try:
        from services.data_sources.security_day_acquire import (
            ACQUIRE_MODE_LOCAL_LEGACY_RAW,
            ACQUIRE_MODE_PROVIDER_TUSHARE,
            resolve_security_day_acquire,
        )

        if from_local_raw:
            from services.data_sources.security_day_transport import (
                materialize_security_day_landing_from_legacy_raw_rows,
            )

            if domain == "daily":
                from services.data_sources.nominal_ohlcv_contract import (
                    nominal_ohlcv_contract_for_spec,
                )

                contract = nominal_ohlcv_contract_for_spec(spec)
            elif domain == "stock_st":
                from services.data_sources.stock_st_contract import stock_st_contract_for_spec

                contract = stock_st_contract_for_spec(spec)
            else:
                raise SyncWindowError(
                    f"from-local-raw unsupported domain={domain}"
                )
            try:
                acquired = resolve_security_day_acquire(
                    ACQUIRE_MODE_LOCAL_LEGACY_RAW,
                    domain,
                    trade_date=partition,
                    conn=conn,
                )
            except SecurityDayError as exc:
                return {
                    "domain": domain,
                    "status": "error",
                    "batches": 0,
                    "rows": 0,
                    "failed_batches": 1,
                    "error": str(exc)[:500],
                    "publication": "land_only_from_local_raw",
                    "transport": "land_only",
                    "acquire_mode": ACQUIRE_MODE_LOCAL_LEGACY_RAW,
                }
            if not acquired.rows:
                return {
                    "domain": domain,
                    "status": "error",
                    "batches": 0,
                    "rows": 0,
                    "failed_batches": 1,
                    "error": (
                        f"local_raw_empty trade_date={partition} "
                        f"table={acquired.source_ref}"
                    ),
                    "publication": "land_only_from_local_raw",
                    "transport": "land_only",
                    "acquire_mode": acquired.acquire_mode,
                }
            observed = datetime.now(timezone.utc)
            try:
                batch = materialize_security_day_landing_from_legacy_raw_rows(
                    domain,
                    conn,
                    contract,
                    trade_date=partition,
                    raw_rows=acquired.rows,
                    observed_at=observed,
                    bootstrap=True,
                    lineage_note=acquired.lineage_note,
                )
            except SecurityDayError as exc:
                return {
                    "domain": domain,
                    "status": "error",
                    "batches": 0,
                    "rows": 0,
                    "failed_batches": 1,
                    "error": str(exc)[:500],
                    "publication": "land_only_from_local_raw",
                    "transport": "land_only",
                    "acquire_mode": acquired.acquire_mode,
                }
            return {
                "domain": domain,
                "status": "ok",
                "batches": 1,
                "rows": len(acquired.rows),
                "failed_batches": 0,
                "batch_id": batch.batch_id,
                "partition_value": batch.partition_value,
                "publication": "land_only_from_local_raw",
                "transport": "land_only",
                "acquire_mode": acquired.acquire_mode,
                "eligible_end": eligibility.eligible_end,
                "eligibility_reason": eligibility.reason,
            }

        apply_fetch_socket_timeout(spec)
        adapter = _adapter(str(spec["source"]))

        def _fetch_rows(params: Mapping[str, Any]):
            request = {"trade_date": str(params.get("trade_date") or partition)}
            return _fetch_with_retry(adapter, spec, request)

        from services.data_sources.nominal_ohlcv_runtime import NominalOhlcvRuntimeError
        from services.data_sources.stock_st_runtime import StockStRuntimeError

        try:
            acquired = resolve_security_day_acquire(
                ACQUIRE_MODE_PROVIDER_TUSHARE,
                domain,
                trade_date=partition,
                fetch_rows=_fetch_rows,
            )

            def _acquired_rows(_params: Mapping[str, Any]):
                return list(acquired.rows)

            if domain == "daily":
                from services.data_sources.nominal_ohlcv_contract import (
                    nominal_ohlcv_contract_for_spec,
                )
                from services.data_sources.nominal_ohlcv_runtime import (
                    capture_and_land_authorized_nominal_ohlcv_partition,
                )

                batch = capture_and_land_authorized_nominal_ohlcv_partition(
                    conn,
                    nominal_ohlcv_contract_for_spec(spec),
                    trade_date=partition,
                    fetch_rows=_acquired_rows,
                    bootstrap=True,
                )
                publication = "land_only_nominal_ohlcv"
            elif domain == "stock_st":
                from services.data_sources.stock_st_contract import stock_st_contract_for_spec
                from services.data_sources.stock_st_runtime import (
                    capture_and_land_authorized_stock_st_partition,
                )

                batch = capture_and_land_authorized_stock_st_partition(
                    conn,
                    stock_st_contract_for_spec(spec),
                    trade_date=partition,
                    fetch_rows=_acquired_rows,
                    bootstrap=True,
                )
                publication = "land_only_stock_st"
            else:
                raise SyncWindowError(
                    f"domain={domain} is not a security-day land-only publication"
                )
        except (
            SecurityDayError,
            NominalOhlcvRuntimeError,
            StockStRuntimeError,
        ) as exc:
            return {
                "domain": domain,
                "status": "error",
                "batches": 0,
                "rows": 0,
                "failed_batches": 1,
                "error": str(exc)[:500],
                "publication": "land_only",
                "transport": "land_only",
                "acquire_mode": ACQUIRE_MODE_PROVIDER_TUSHARE,
            }
    finally:
        conn.close()

    return {
        "domain": domain,
        "status": "ok",
        "batches": 1,
        "rows": len(tuple(batch.rows)),
        "failed_batches": 0,
        "batch_id": batch.batch_id,
        "partition_value": batch.partition_value,
        "publication": publication,
        "transport": "land_only",
        "acquire_mode": ACQUIRE_MODE_PROVIDER_TUSHARE,
        "eligible_end": eligibility.eligible_end,
        "eligibility_reason": eligibility.reason,
    }


def _run_security_day_transport_window(
    domain: str,
    *,
    transport: str,
    start: str | None,
    end: str | None,
    batch_id: str | None = None,
    from_local_raw: bool = False,
    registry: dict[str, Any] | None = None,
    trigger_mode: TriggerMode | str = "manual",
) -> dict[str, Any]:
    """Thin land / accept / land-then-accept window runner (≤40d)."""

    reg = registry if registry is not None else load_registry()
    spec = domain_spec(reg, domain)
    _require_execution_enabled(spec)
    mode = _normalize_trigger_mode(trigger_mode)

    if transport == "accept_from_landing":
        if batch_id:
            return accept_security_day_from_landing_batch(
                domain, batch_id=batch_id, registry=reg
            )
        trade_dates = _require_authorized_short_trade_date_window(
            domain,
            backfill=False,
            resume=False,
            start=start,
            end=end,
            max_dates=None,
        )
        if len(trade_dates) != 1:
            raise SyncWindowError(
                "accept-from-landing without --batch-id requires a single-day "
                "--start/--end window to resolve the LANDED batch"
            )
        conn = _target_conn(spec)
        try:
            if domain == "daily":
                from services.data_sources.nominal_ohlcv_schema import DATASET_ID
            else:
                from services.data_sources.stock_st_schema import DATASET_ID
            row = conn.execute(
                """
                SELECT batch_id FROM ingest_batch
                 WHERE dataset_id = ? AND partition_value = ? AND status = 'LANDED'
                 ORDER BY landed_at DESC
                 LIMIT 1
                """,
                [DATASET_ID, trade_dates[0]],
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise SyncWindowError(
                f"no LANDED batch for domain={domain} partition={trade_dates[0]}"
            )
        return accept_security_day_from_landing_batch(
            domain, batch_id=str(row[0]), registry=reg
        )

    trade_dates = _require_authorized_short_trade_date_window(
        domain,
        backfill=False,
        resume=False,
        start=start,
        end=end,
        max_dates=None,
    )
    day_results: list[dict[str, Any]] = []
    total_rows = 0
    failed = 0
    last_ok: str | None = None
    completed_ok = 0
    for trade_date in trade_dates:
        if transport == "land_only":
            result = _land_security_day_partition(
                domain,
                spec,
                trade_date=trade_date,
                trigger_mode=mode,
                from_local_raw=from_local_raw,
            )
        elif transport == "land_then_accept":
            land_result = _land_security_day_partition(
                domain,
                spec,
                trade_date=trade_date,
                trigger_mode=mode,
                from_local_raw=from_local_raw,
            )
            if int(land_result.get("failed_batches") or 0) != 0:
                result = land_result
                result["transport"] = "land_then_accept"
            else:
                result = accept_security_day_from_landing_batch(
                    domain,
                    batch_id=str(land_result["batch_id"]),
                    registry=reg,
                )
                result["transport"] = "land_then_accept"
                result["land_batch_id"] = land_result["batch_id"]
        else:
            raise SyncWindowError(f"unknown security-day transport={transport!r}")
        day_results.append(result)
        total_rows += int(result.get("rows") or 0)
        day_failed = int(result.get("failed_batches") or 0)
        failed += day_failed
        if day_failed == 0:
            last_ok = trade_date
            completed_ok += 1
        else:
            break
    return {
        "domain": domain,
        "status": "ok" if failed == 0 else "partial",
        "batches": len(day_results),
        "rows": total_rows,
        "failed_batches": failed,
        "publication": f"security_day_{transport}",
        "transport": transport,
        "partition_values": [
            str(r.get("partition_value") or "")
            for r in day_results
            if int(r.get("failed_batches") or 0) == 0
        ],
        "trade_dates": list(trade_dates),
        "day_results": day_results,
        "last_date": last_ok,
        "window_days_requested": len(trade_dates),
        "window_days_completed": completed_ok,
    }


def run_domain(domain: str, *, backfill: bool = False, start: str | None = None,
               end: str | None = None, resume: bool = False,
               max_dates: int | None = None,
               registry: dict[str, Any] | None = None,
               trigger_mode: TriggerMode | str = "manual") -> dict[str, Any]:
    """同步单个数据域. 返回 {domain, batches, rows, failed_batches}.

    resume: by_ts_code 域跳过 target 表已有数据的 ts_code (full-history 单股拉断点续拉, 省重拉)。
    trigger_mode: ``manual`` (default; chunkyctl/UI) skips same_day_at clock wait;
    ``automatic`` keeps the consumer publication clock for future scheduled paths.
    """
    mode = _normalize_trigger_mode(trigger_mode)
    reg = registry if registry is not None else load_registry()
    spec = domain_spec(reg, domain)
    _require_execution_enabled(spec)
    if domain == "trade_cal":
        if max_dates is not None:
            raise SyncWindowError("--max-dates is only valid for --drain")
        if backfill or resume or start is not None or end is not None:
            raise SyncWindowError(
                "domain=trade_cal accepted generation is full_refresh only; "
                "refuse --backfill/--resume/--start/--end"
            )
        return _publish_trade_cal_accepted_generation(spec)
    if domain in AUTHORIZED_SHORT_WINDOW_DOMAINS:
        trade_dates = _require_authorized_short_trade_date_window(
            domain,
            backfill=backfill,
            resume=resume,
            start=start,
            end=end,
            max_dates=max_dates,
        )
        if len(trade_dates) == 1:
            return _publish_security_day_accepted_partition(
                domain,
                spec,
                trade_date=trade_dates[0],
                trigger_mode=mode,
            )
        return _publish_security_day_short_window(
            domain,
            spec,
            trade_dates=trade_dates,
            trigger_mode=mode,
        )
    if domain == "margin":
        formal_contract = _formal_dataset_contract_for_spec(spec)
        if formal_contract is None:
            raise PopulationScopeExecutionError(
                "margin",
                reason="invalid_dataset_contract",
                detail="margin catchup requires a formal dataset contract",
            )
        eligibility = eligible_end_date(spec, trigger_mode=mode)
        trade_dates = _require_authorized_margin_catchup_window(
            backfill=backfill,
            resume=resume,
            start=start,
            end=end,
            max_dates=max_dates,
            eligible_end=eligibility.eligible_end,
            coverage_start=str(formal_contract.coverage_start),
        )
        return _publish_margin_bounded_catchup(
            spec,
            trade_dates=trade_dates,
            trigger_mode=mode,
        )
    formal_contract = _formal_dataset_contract_for_spec(spec)
    formal_execution = _require_formal_population_execution(spec, formal_contract)
    if formal_execution is not None:
        return _refuse_formal_domain_runtime(domain, formal_execution)
    # After formal handoff/refusal: never fall into legacy raw for walled domains.
    _refuse_formal_legacy_raw_path(domain)
    if max_dates is not None:
        raise SyncWindowError("--max-dates is only valid for --drain")
    batch_mode = str(spec["batch_mode"])
    if batch_mode == "full_refresh" and (start is not None or end is not None):
        raise SyncWindowError(
            f"domain={domain} batch_mode=full_refresh does not accept date bounds"
        )
    if (
        spec.get("batch_mode") == "by_ts_code"
        and spec.get("sync_policy") == "on_demand"
        and (not start or not end)
    ):
        raise ValueError(
            f"domain={domain} sync_policy=on_demand 要求显式 --start 与 --end，拒绝无界逐股拉取"
        )
    eligibility: DomainEligibility | None = None
    operation_window: OperationWindow | None = None
    replaying_open_failure = False
    if batch_mode != "full_refresh":
        eligibility = eligible_end_date(spec, trigger_mode=mode)
        fixed_bounds = dict(spec.get("fixed_params") or {})
        planned_start = start
        planned_end = end
        if batch_mode in ("by_ts_code", "by_code_list"):
            planned_start = planned_start or fixed_bounds.get("start_date")
            planned_end = planned_end or fixed_bounds.get("end_date")
        operation_window = resolve_operation_window(
            eligibility,
            requested_start=planned_start,
            requested_end=planned_end,
        )

    def _operation_end() -> str | None:
        return operation_window.effective_end if operation_window is not None else None

    if spec["batch_mode"] == "full_refresh":
        batches: list[dict[str, Any]] = [dict(spec.get("fixed_params") or {})]
    elif spec["batch_mode"] == "by_date_range":
        # 小表 (如大盘资金流 1 行/日) 一次调用拉全范围; 单次上限由 API 决定 (registry 选用前确认)
        if backfill:
            start_d = start or spec["data_start"]
        else:
            wm = _last_watermark_date(domain, spec["source"])
            start_d = start or wm or spec["data_start"]
        end_d = _operation_end()
        batches = (
            [{"start_date": start_d, "end_date": end_d}]
            if end_d and str(start_d).replace("-", "") <= end_d
            else []
        )
    elif spec["batch_mode"] == "by_ts_code":
        batches = _by_ts_code_batches(
            spec,
            resume=resume,
            backfill=backfill,
            start=start,
            end=_operation_end(),
        )
    elif spec["batch_mode"] == "by_code_list":
        # 显式代码清单循环 (指数/申万行业等 — code 源非 market 股票表): 每 code 一批,
        # code_param 指定参数名 (ts_code/l1_code...), fixed_params 合并 (如指数日线的 start/end)。
        # 用途: index_daily 基准指数 (无视全市场, 只拉 benchmark 代码) / index_member_all 按申万 l1
        # 循环避开无参 5000 整截断 (2026-06-13 实测无参全拉 = 整 5000 = top_inst/dc_member 同型截断反例)。
        code_param = spec.get("code_param", "ts_code")
        fixed = dict(spec.get("fixed_params") or {})
        # ranged by_code_list (指数日线/指标全史回填): end_date 由域 publication eligibility 动态化，
        # 防 §4.4 红线"钉死日期" (hardcode end_date 致 benchmark 永久 stale, daily_update 推不过去).
        # 仅当 fixed 有 start_date 且未显式 end_date 时注入; --end 覆盖; MERGE on grain 幂等可全史重拉.
        if operation_window is not None and operation_window.requested_start is not None:
            fixed["start_date"] = operation_window.requested_start
        if (
            "start_date" in fixed
            or (
                operation_window is not None
                and operation_window.requested_end is not None
            )
        ):
            end_d = _operation_end()
            if end_d:
                fixed["end_date"] = end_d
        batches = [{code_param: c, **fixed} for c in spec["code_list"]]
    elif spec["batch_mode"] == "by_trade_date":
        wm = None
        if backfill:
            start_d = start or spec["data_start"]
        else:
            wm = _last_watermark_date(domain, spec["source"])
            start_d = start or wm or spec["data_start"]
        end_d = _operation_end()
        days = trading_days(start_d, end_d) if end_d else []
        _warn_if_clamped(domain, start_d, days)
        # Dense trade-date: atomic_skip via shared frontier primitive.
        # Pattern = 存量最新日(wm) + 交易日历应拉日(end_d), 不是 wall-clock「对昨天」。
        # Explicit --start / backfill keep the full requested window (2026-07-06).
        trade_decision = decide_frontier(
            axis="trade_date",
            local_max=wm,
            target_max=end_d,
        )
        days = plan_incremental_days(
            days,
            watermark=wm,
            decision=trade_decision,
            policy="atomic_skip",
            explicit_start=start is not None,
            backfill=backfill,
        )
        # date_param: API 日期参数名 (默认 trade_date; dividend 用 ex_date / report_rc 用
        # report_date — 锚定列同名, raw 表镜像后 drain 也按它扫 gap)
        date_param = spec.get("date_param", "trade_date")
        # fixed_params 合并 (2026-07-04 根因修复, 热榜子榜排查实弹踩出的死配置):
        # 此分支此前只拼 {date_param: d}, 完全丢弃 fixed_params — 任何 by_trade_date+fixed_params
        # 组合域静默失效 (声明 data_type="热基" 却始终请求不到, 只拿到与 date_param 无关的默认返回)。
        # 与 by_code_list 分支 (L790) 同款合并语义, 批参数优先于 fixed (date_param 不应被 fixed 覆盖)。
        fixed = dict(spec.get("fixed_params") or {})
        # split_by (2026-07-08 根因修复, owner=git log --grep gap_root_cause): margin 域
        # 裸调 pro.margin(trade_date=d) 在 2026 年偶发(~0.5%交易日)漏返 BSE/SZSE(vendor 网关对
        # "无过滤条件汇总查询"存在补全遗漏的怪癖), 但显式加 exchange_id=SSE/SZSE/BSE 逐个查询
        # 100% 拿全(含历史已发生的漏返日期实测验证)。这不是分页截断(_fetch_paged 管不了),
        # 是查询形态问题——registry 声明 split_by: {param: 每次显式加的参数名, values: [...]}
        # 一个日期仍只生成一个逻辑批次；_fetch_logical_batch 内部拉齐所有分片，随后一次原子写。
        # 旧版把分片直接展开为独立写批，任一交易所失败都会留下半截日且被 watermark 越过。
        batches = [{**fixed, date_param: d} for d in days]
    elif spec["batch_mode"] == "by_ann_date":
        # 按公告日抓全市场 (十大股东 etc): tushare 支持 ann_date 查全市场, 覆盖季报披露 + ad-hoc 非季末更新
        # (实测 600388 报告期 20231011=非季末 ad-hoc; 全库 1810 非季末期/2902股)。watermark=最新已抓公告日,
        # 增量抓 [watermark, eligible_end] 全日历日 (公告日含周末); 峰值日 6000 截断由 _fetch_paged
        # page_limit 分页。equal/advance 保留 wm 当天全日批重拉 (稀疏晚披露, e040f4889 同型)。
        wm = None
        pending_start = None
        if backfill:
            start_d = start or spec["data_start"]
        else:
            wm = _last_watermark_date(domain, spec["source"])
            pending_start = _pending_failure_start(spec) if start is None else None
            candidates = [d for d in (wm, pending_start) if d]
            start_d = start or (min(candidates) if candidates else spec["data_start"])
            replaying_open_failure = pending_start is not None and start is None
        end_d = _operation_end()
        days = _calendar_days(start_d, end_d) if end_d else []
        # Sparse ann/disclosure domains: ann_reprobe via shared frontier primitive.
        # Equal/advance keeps watermark day (cheap full-day re-pull) — same bug class as
        # holders equal-wm late filers (e040f4889). Never permanent-skip wm day.
        # Explicit --start / backfill still keep full requested window (2026-07-10).
        ann_decision = decide_frontier(
            axis="ann_date",
            local_max=wm,
            target_max=end_d,
        )
        days = plan_incremental_days(
            days,
            watermark=wm,
            decision=ann_decision,
            policy="ann_reprobe",
            explicit_start=start is not None,
            backfill=backfill,
            pending_replay_day=pending_start,
        )
        replaying_open_failure = bool(
            pending_start and pending_start in set(days)
        ) if not backfill else False
        date_param = spec.get("date_param", "ann_date")
        batches = [{date_param: d} for d in days]
    elif spec["batch_mode"] == "by_period":
        # 报告期循环 (财报快报 express_vip: 按报告期整批, ann_date/trade_date 不可批 — 实弹证伪)。
        if backfill:
            start_d = start or spec["data_start"]
        else:
            wm = _last_watermark_date(domain, spec["source"])
            pending_start = _pending_failure_start(spec) if start is None else None
            candidates = [d for d in (wm, pending_start) if d]
            start_d = start or (min(candidates) if candidates else spec["data_start"])
            replaying_open_failure = pending_start is not None and start is None
        end_d = _operation_end()
        date_param = spec.get("date_param", "period")
        periods = _quarter_ends(start_d, end_d) if end_d else []
        replaying_open_failure = bool(
            pending_start and pending_start in set(periods)
        ) if not backfill else False
        batches = [{date_param: p} for p in periods]
    else:
        raise NotImplementedError(f"batch_mode {spec['batch_mode']} 未实现 (by_ts_code/by_month 按需加)")

    # Side-effectful provider/DB boundaries come only after the operation
    # window has been proven within the live publication frontier.
    apply_fetch_socket_timeout(spec)
    adapter = _adapter(spec["source"])
    conn = _target_conn(spec)
    total_rows, failed = 0, []
    pending_publish: list[dict[str, Any]] = []
    successful_dates: set[str] = set()
    failed_dates: set[str] = set()
    min_rows = int(spec.get("min_rows_per_batch", 0))
    quota_halt = False
    # Tip-leap hole repair before tip+1 incremental (shared plan_partition_catchup).
    # Sparse ann domains: raw may hold mid partitions while MAX(wm) already leaped.
    partition_leap_catchup: dict[str, Any] | None = None
    try:
        if domain == "stk_holdertrade" and not backfill:
            from services.data_sources.stk_holdertrade_catchup import (
                catchup_missing_holdertrade_ann_partitions,
            )

            partition_leap_catchup = catchup_missing_holdertrade_ann_partitions(conn)
        for params in batches:
            batch_date = str(params.get(spec.get("date_param", "trade_date"), "") or "").replace("-", "")
            mr_since = str(spec.get("min_rows_since", "") or "").replace("-", "")
            effective_min = min_rows if (not mr_since or not batch_date or batch_date >= mr_since) \
                else int(spec.get("min_rows_before", 1))
            try:
                rows = _fetch_logical_batch(adapter, spec, params)
            except QuotaExhaustedError as exc:
                # 熔断: 配额墙命中, 停止本域剩余批 (已写批由 finally 保留), 上抛停全链
                log.error("配额熔断 domain=%s 已写 %d 行后停止剩余 %d 批: %s",
                          domain, total_rows, len(batches) - len(failed), exc)
                quota_halt = True
                break
            if rows is None:
                # Pre-window same-day vacuum (e.g. HH:MM domain before available_after) → typed
                # pending_publish, not failed_batches / not known_empty tombstone.
                if _is_pre_publish_same_day_zero(spec, params):
                    pending_publish.append(params)
                    log.info(
                        "pending_publish domain=%s params=%s "
                        "(pre-available_after zero_rows; retry after window)",
                        domain,
                        params,
                    )
                    continue
                failed.append(params)
                failed_date = params.get(spec.get("date_param", "trade_date")) or params.get("end_date")
                if failed_date:
                    failed_dates.add(str(failed_date).replace("-", ""))
                continue
            if not rows and spec.get("cross_check_domain"):
                # allow_empty 域 0 行交叉参照门 (R1 根因2): 0 行只有 allow_empty 路径能走到这
                # (否则 _fetch_with_retry 已判终败 None); 交叉域同日有数据 = 可疑空非合法空。
                suspicious, cross_rows, cross_table = _cross_check_suspicious_empty(conn, reg, spec, params)
                if suspicious:
                    log.warning(
                        "suspicious_empty domain=%s params=%s: 本域 0 行但交叉域 %s(%s) 同日 %d 行 "
                        "— 不当合法空, 入 failure_queue + 计失败批",
                        domain, params, spec.get("cross_check_domain"), cross_table, cross_rows)
                    failed.append({**params, "suspect": "suspicious_empty"})
                    failed_dates.add(str(params.get(spec.get("date_param", "trade_date"))).replace("-", ""))
                    _record_suspicious_empty(spec, params, cross_rows, cross_table)
                    continue
            # 时代分段阈值 (与 drain_domain 同一口径, 2026-07-09): 批次日期早于 min_rows_since
            # 的历史回拉批用 min_rows_before 判定, 防止把"早期真实完整但行数低于今日基线"的批
            # 误报 below_min_rows (margin_detail 2019年941行 vs 今日阈值2000 案例)。
            try:
                n = _write_batch(
                    conn,
                    spec,
                    rows,
                    effective_min_rows=effective_min,
                    expected_partition={
                        column: params.get(column)
                        for column in spec.get("partition_by") or []
                    } if spec.get("write_mode") == "replace_partition" else None,
                )
            except BatchCompletenessError as exc:
                # 完整性门在任何 DELETE/INSERT 前执行：partial 批零写入、零 watermark，
                # 但必须进入本轮失败证据，供 failure_queue/drain 后续重放。
                log.warning("batch %s 完整性失败 (拒绝写入): %s", params, exc)
                failed.append({
                    **params,
                    "suspect": "batch_incomplete",
                    "error": str(exc)[:200],
                })
                failed_date = params.get(spec.get("date_param", "trade_date")) or params.get("end_date")
                if failed_date:
                    failed_dates.add(str(failed_date).replace("-", ""))
                continue
            except Exception as exc:  # noqa: BLE001 — 写事务已 rollback；记录后停本域
                log.error("batch %s 写入失败 (已 rollback): %s", params, exc)
                failed.append({
                    **params,
                    "suspect": "write_failed",
                    "error_type": type(exc).__name__,
                })
                failed_date = params.get(spec.get("date_param", "trade_date")) or params.get("end_date")
                if failed_date:
                    failed_dates.add(str(failed_date).replace("-", ""))
                break
            total_rows += n
            date_key = spec.get("date_param", "trade_date")
            if params.get(date_key):
                successful_dates.add(str(params[date_key]).replace("-", ""))
            elif params.get("end_date"):
                successful_dates.add(str(params["end_date"]).replace("-", ""))
            elif spec.get("freshness_date_column"):
                freshness_col = str(spec["freshness_date_column"])
                values = [
                    str(row[freshness_col]).replace("-", "")
                    for row in rows
                    if row.get(freshness_col)
                ]
                if values:
                    successful_dates.add(max(values))
            time.sleep(0.4)  # rule-compliance: ok evidence=vendor-gateway-conn-refused-backoff-2026-06-11
    finally:
        conn.close()

    # 严格判定: 任一批失败即非 ok — 旧宽松口径 (部分成功=True) 使日志 'ok': True
    # 掩盖 29 批失败 (Fable-5 复查 #14 双标问题); 与 _record_outcome 判定统一
    ok = len(failed) == 0 and not quota_halt
    completed_dates = successful_dates - failed_dates
    if failed_dates and spec["batch_mode"] in ("by_ann_date", "by_period"):
        # 这些域没有交易日 gap drain；frontier 只能推进到首个失败之前，确保下轮会重试缺口。
        first_failed = min(failed_dates)
        completed_dates = {date for date in completed_dates if date < first_failed}
    last_ok_date = max(completed_dates) if completed_dates else None
    err_payload = json.dumps(failed[:5]) if failed else None
    failure_type = "sync_batch_failed"
    if quota_halt:
        if failed:
            err_payload = json.dumps({
                "quota_wall_halt": True,
                "failed_batches": failed[:5],
            })
        else:
            # 配额是独立失败类型；不能覆盖 sync_batch_failed 行中的待重放日期。
            err_payload = "quota_wall_halt"
            failure_type = "sync_quota_halt"
    _record_outcome(
        spec,
        ok=ok,
        last_date=last_ok_date,
        rows=total_rows,
        error=err_payload,
        # full-refresh 成功已覆盖整个快照，天然是旧整批失败的完整重放证据；普通增量
        # 仍必须实际覆盖 failure frontier，不能因“今天成功”洗掉历史 gap。
        resolve_failures=ok and (
            replaying_open_failure or spec["batch_mode"] == "full_refresh"
        ),
        failure_type=failure_type,
        provider_succeeded=ok and bool(batches),
    )
    result = {
        "domain": domain,
        "batches": len(batches),
        "rows": total_rows,
        "failed_batches": len(failed),
        "pending_publish_batches": len(pending_publish),
        "last_date": last_ok_date,
        "ok": ok,
    }
    if partition_leap_catchup is not None:
        result["partition_leap_catchup"] = partition_leap_catchup
    if pending_publish:
        result["pending_publish"] = True
        result["pending_publish_reason"] = "pre_available_after_zero_rows"
    if eligibility is not None:
        result.update({
            "eligible_end": eligibility.eligible_end,
            "pending_today": eligibility.pending_today or bool(pending_publish),
            "eligibility_reason": eligibility.reason,
        })
    if operation_window is not None:
        result["operation_end"] = operation_window.effective_end
    if quota_halt:
        result["quota_halt"] = True
    log.info("sync %s", result)
    if quota_halt:
        # 上抛由 main 熔断全链 (已写批与 watermark 已落盘, 可恢复)
        raise QuotaExhaustedError(f"domain={domain} 配额墙停链 (已写 {total_rows} 行)")
    return result


def _parse_available_after(spec: dict[str, Any]) -> tuple[int, int] | str | None:
    raw = str(spec.get("available_after") or "").strip()
    if raw.lower() == "t+1":
        return "t+1"
    try:
        hh, mm = (int(part) for part in raw.split(":"))
    except (ValueError, AttributeError):
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


def _batch_date_yyyymmdd(spec: dict[str, Any], params: dict[str, Any]) -> str:
    date_param = str(spec.get("date_param") or "trade_date")
    return str(
        params.get(date_param) or params.get("end_date") or ""
    ).replace("-", "")


def _is_same_day_partition(
    spec: dict[str, Any],
    params: dict[str, Any],
    *,
    now: Any = None,
) -> bool:
    """True when the batch/partition date equals Asia/Shanghai today."""
    from zoneinfo import ZoneInfo

    now_local = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if getattr(now_local, "tzinfo", None) is None:
        now_local = now_local.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    else:
        now_local = now_local.astimezone(ZoneInfo("Asia/Shanghai"))
    batch_date = _batch_date_yyyymmdd(spec, params)
    today = now_local.strftime("%Y%m%d")
    return bool(batch_date) and batch_date == today


def _is_pre_publish_same_day_zero(
    spec: dict[str, Any],
    params: dict[str, Any],
    *,
    now: Any = None,
) -> bool:
    """Same-day HH:MM domain returned 0 rows before ``available_after``.

    Manual sync may attempt today before the clock (AGENTS: may skip
    ``same_day_at``). Vendor vacuum before publish is typed
    ``pending_publish`` — never ``failed_batches`` and never a
    ``known_empty_days`` tombstone (retryable; gate not loosened).
    """
    from zoneinfo import ZoneInfo

    availability = _parse_available_after(spec)
    if not isinstance(availability, tuple):
        return False
    now_local = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if getattr(now_local, "tzinfo", None) is None:
        now_local = now_local.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    else:
        now_local = now_local.astimezone(ZoneInfo("Asia/Shanghai"))
    if (now_local.hour, now_local.minute) >= availability:
        return False
    return _is_same_day_partition(spec, params, now=now_local)


def _normalize_trigger_mode(trigger_mode: TriggerMode | str | None) -> TriggerMode:
    mode = str(trigger_mode or "automatic").strip()
    if mode not in {"manual", "automatic"}:
        raise SyncWindowError(
            f"trigger_mode must be 'manual' or 'automatic', got {trigger_mode!r}"
        )
    return mode  # type: ignore[return-value]


def eligible_end_date(
    spec: dict[str, Any],
    *,
    now: Any = None,
    trading_day_values: list[str] | None = None,
    trigger_mode: TriggerMode | str = "automatic",
) -> DomainEligibility:
    """Resolve one sync or consumer frontier; formal policy never derives from batch mode.

    ``availability_policy`` is the typed owner for formal datasets.  Entries
    without it retain the legacy ``available_after`` interpretation until each
    domain has provider timing evidence; this prevents a margin migration from
    silently redefining announcement, report-period, or row-field domains.

    Default ``trigger_mode=automatic`` preserves the clocked consumer /
    continuity frontier (``same_day_at`` pending until ``policy.at``).  Human
    sync paths (chunkyctl / UI) pass ``trigger_mode=manual`` so an open trading
    day is calendar-eligible without waiting for that clock; consumer
    ``available_at`` projections must keep the automatic frontier.
    """
    from zoneinfo import ZoneInfo

    mode = _normalize_trigger_mode(trigger_mode)
    now_local = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if getattr(now_local, "tzinfo", None) is not None:
        now_local = now_local.astimezone(ZoneInfo("Asia/Shanghai"))
    today = now_local.strftime("%Y%m%d")
    raw_policy = spec.get("availability_policy")
    policy = (
        availability_policy_from_mapping(
            raw_policy, owner=str(spec.get("domain") or "domain")
        )
        if raw_policy is not None
        else None
    )
    days = trading_day_values
    if policy is None or policy.axis == "trading_day":
        if days is None:
            days = trading_days(str(spec.get("data_start") or "19900101"), today)
        days = sorted(
            {
                str(day).replace("-", "")
                for day in days
                if str(day).replace("-", "") <= today
            }
        )
    else:
        days = []
    if policy is not None:
        return resolve_sync_eligibility_frontier(
            policy,
            now=now_local,
            trading_day_values=days,
            trigger_mode=mode,
        )
    if not days:
        return DomainEligibility(None, False, "calendar_empty")

    # Legacy compatibility only.  The raw token has no typed axis or release
    # clock, so do not extend its semantics while migrating formal margin.
    today_is_trading = days[-1] == today
    availability = _parse_available_after(spec)
    if not today_is_trading:
        return DomainEligibility(days[-1], False, "latest_prior_trading_day")
    if availability == "t+1":
        return DomainEligibility(
            days[-2] if len(days) > 1 else None,
            True,
            "t_plus_one_legacy",
        )
    if mode == "manual" and isinstance(availability, tuple):
        return DomainEligibility(today, False, "manual_calendar_eligible")
    if isinstance(availability, tuple) and (now_local.hour, now_local.minute) >= availability:
        return DomainEligibility(today, False, "published")
    reason = "pending_publish" if isinstance(availability, tuple) else "invalid_available_after"
    return DomainEligibility(days[-2] if len(days) > 1 else None, True, reason)


def drain_domain(domain: str, *, registry: dict[str, Any] | None = None,
                 conn=None, adapter=None, expected_trading_days: list[str] | None = None,
                 max_dates: int | None = None, record: bool = True,
                 trigger_mode: TriggerMode | str = "manual") -> dict[str, Any]:
    """日历 gap 重放；仅用于仍受支持的 legacy by-trade-date 域。

    真相源 = 交易日历 + target_table 本身 (宪法第 1 条), 不依赖 failure_queue 中间
    记录 — queue 按 (域,源,错误类型) 聚合不存日期, 且漏拉/调度漏跑/历史空洞它
    根本看不见。gap 扫描三类全覆盖。

    仅支持 by_trade_date 域 (缺口语义 = 缺整天); 其他 batch_mode 显式返回
    unsupported, 不静默跳过。allow_empty_batch 域的"重查确认空"与终败分开报。
    conn/adapter/trading_days/record 可注入 (单测); 生产路径全走真相源。
    """
    mode = _normalize_trigger_mode(trigger_mode)
    reg = registry if registry is not None else load_registry()
    spec = domain_spec(reg, domain)
    _require_execution_enabled(spec)
    if domain == "trade_cal":
        return {
            "domain": domain,
            "status": "drain_inapplicable",
            "reason": "accepted_calendar_generation_is_full_refresh_only",
        }
    if domain in {"daily", "stock_st"}:
        return {
            "domain": domain,
            "status": "drain_inapplicable",
            "reason": "accepted_partition_is_authorized_short_window_only",
        }
    if domain == "margin":
        return {
            "domain": domain,
            "status": "drain_inapplicable",
            "reason": "accepted_partition_is_bounded_calendar_catchup_only",
        }
    formal_contract = _formal_dataset_contract_for_spec(spec)
    formal_execution = _require_formal_population_execution(spec, formal_contract)
    if formal_execution is not None:
        return _refuse_formal_domain_runtime(domain, formal_execution)
    _refuse_formal_legacy_raw_path(domain)
    if spec.get("batch_mode") != "by_trade_date":
        return {"domain": domain, "status": "unsupported", "batch_mode": spec.get("batch_mode")}
    if spec.get("allow_empty_batch") and not spec.get("cross_check_domain"):
        # 空日合法的域 gap 不可判定: 缺口 vs 真空无法区分, 空日会被每日重查永不收敛
        # (dividend 自 2005 年 ~3500 个无除权日会吃光重拉名额 + 永远 partial 告警疲劳)。
        # 这类域走 watermark 增量 (main --drain 分支 fallback run_domain)。
        return {"domain": domain, "status": "drain_inapplicable", "reason": "allow_empty 域走增量"}
    if expected_trading_days is not None:
        expected = list(expected_trading_days)
    else:
        eligibility = eligible_end_date(spec, trigger_mode=mode)
        expected = (
            trading_days(
                str(spec["data_start"]).replace("-", ""),
                eligibility.eligible_end,
            )
            if eligibility.eligible_end
            else []
        )
    # ``eligible_end_date`` is the single publication owner for incremental and
    # drain paths.  Formal typed policy is exact; legacy domains deliberately
    # retain their old token semantics until migrated with provider evidence.
    # 源端空洞墓碑 (2026-06-28): known_empty_days = 实测探过源端真没数据的交易日 (cyq_perf 06-15 仅1股/
    #   moneyflow_dc 起点前)。排出 expected → 不当 gap → 不每天重探 + 不永久 partial 告警疲劳
    #   (区别于真缺口)。新增前必实测源端确认空 (探测返0/不足且非throttle), 不可拿它掩盖真失败。
    known_empty = {str(d).replace("-", "") for d in (spec.get("known_empty_days") or [])}
    expected = [d for d in expected if d not in known_empty]
    own_conn = conn is None
    conn = conn or _target_conn(spec)
    refilled_rows, still_failed = 0, []
    successful_todo: set[str] = set()
    min_rows = int(spec.get("min_rows_per_batch", 0))
    # "完整日"口径 = 行数达 min_rows 的日; 不足日视同缺口重拉 (MERGE 幂等, 重拉安全)。
    # 复审 HIGH: 旧版 DISTINCT 把 vendor 截断批 (在表但残缺) 当完整, 会洗白 run_domain
    # 标记的 suspect 日且永无重拉机制。
    # 时代分段 (2026-07-09 根因修复, owner=git log --grep gap_root_cause 全审计节):
    # 行数随标的池扩容长期单调增长的域 (margin_detail 2019年941行→2026年3400+行), 静态
    # min_rows 锚定当前基线后, drain 会把早期"真实完整但行数低于今日阈值"的历史日永久判成
    # 缺口反复重拉不收敛 (实测 margin_detail 2000 阈值 → 594 个 2019-2021 真实完整日成幻影
    # 缺口)。registry 可声明 min_rows_since (YYYYMMDD): 该日期(含)之后用 min_rows, 之前用
    # min_rows_before (缺省 1 = 仅防空日)。不声明 min_rows_since 时行为不变 (全历史同一阈值)。
    min_rows_since = str(spec.get("min_rows_since", "") or "").replace("-", "")
    min_rows_before = int(spec.get("min_rows_before", 1))
    try:
        date_col = spec.get("date_param", "trade_date")  # raw 表镜像 api 字段, 锚定列与参数同名
        actual = complete_batch_dates(conn, spec)
        gap = [d for d in expected if d not in actual]
        truncated = max_dates is not None and len(gap) > max_dates
        # 截断取最新优先 (gap 升序取尾部): backlog 超限时昨日数据必须先落地
        # (复审: 最老优先会让永败日卡住头部名额, 最新数据永远轮不到)
        todo = gap if max_dates is None else gap[len(gap) - min(max_dates, len(gap)):]
        if todo:
            adapter = adapter or _adapter(spec["source"])
        for d in todo:
            effective_min = min_rows if (not min_rows_since or d >= min_rows_since) else min_rows_before
            try:
                rows = _fetch_logical_batch(adapter, spec, {date_col: d})
            except QuotaExhaustedError:
                # 熔断: 已修批保留, 未修日留在 gap 下轮补; 上抛停全链 (不逐日续戳反刷量)
                log.error("配额熔断 drain domain=%s 已补 %d 行后停止 (剩 %d 缺口日未修)",
                          domain, refilled_rows, len(todo) - todo.index(d))
                raise
            if not rows:
                if rows == [] and spec.get("cross_check_domain"):
                    suspicious, cross_rows, cross_table = _cross_check_suspicious_empty(
                        conn, reg, spec, {date_col: d}
                    )
                    if suspicious and record:
                        _record_suspicious_empty(
                            spec, {date_col: d}, cross_rows, cross_table
                        )
                still_failed.append(d)
                continue
            try:
                refilled_rows += _write_batch(
                    conn,
                    spec,
                    rows,
                    effective_min_rows=effective_min,
                    expected_partition={date_col: d}
                    if spec.get("write_mode") == "replace_partition" else None,
                )
            except BatchCompletenessError as exc:
                log.warning("drain batch %s 完整性失败 (拒绝写入): %s", d, exc)
                still_failed.append(d)
                continue
            except Exception as exc:  # noqa: BLE001 — 已 rollback；记录当前日并停本域
                log.error("drain batch %s 写入失败 (已 rollback): %s", d, exc)
                still_failed.append(d)
                break
            successful_todo.add(d)
            time.sleep(0.4)  # rule-compliance: ok evidence=同 run_domain 节流口径 vendor-gateway-2026-06-11
    finally:
        if own_conn:
            conn.close()
    status = (
        "partial"
        if still_failed or truncated
        else ("clean" if not gap else "drained")
    )
    _refilled_days = len(successful_todo)
    # 空补告警 (2026-06-28 谄媚死根治, 防 top_list 式静默丢光): 抓到 gap 天却 0 行落库 = 可疑
    #   (universe_filter 漏配/写入静默丢)。源端真空洞 (cyq_perf 单日 0 行) 也会触发, 故 WARN 不硬降级,
    #   但可见 → 不再"drained 成功"假象不留痕。(真 filter-drops-all 已被 _write_batch universe_filter raise 拦成 still_failed。)
    if _refilled_days > 0 and refilled_rows == 0:
        log.warning("[empty-drain] %s 抓到 %d 个 gap 天却 0 行落库 — 查 universe_filter 漏配 or 源端真空洞 (status=%s)",
                    domain, _refilled_days, status)
    result = {"domain": domain, "status": status, "expected_days": len(expected),
              "gap_days": len(gap), "refilled_days": _refilled_days,
              "refilled_rows": refilled_rows,
              "still_failed": still_failed[:20], "truncated": truncated}
    if record:
        _record_outcome(spec, ok=status in ("clean", "drained"),
                        # success watermark 只认本轮真实 provider 写入；历史 actual 只是
                        # gap 审计证据，不能把 0 写入的 clean/partial 扫描伪装成刚成功。
                        last_date=max(successful_todo) if successful_todo else None,
                        rows=refilled_rows,
                        error=json.dumps({"drain_still_failed": still_failed[:10]}) if still_failed else None,
                        resolve_failures=status in ("clean", "drained") and not truncated,
                        provider_succeeded=bool(successful_todo))
    log.info("drain %s", result)
    return result


def _parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--domain", help="sync_registry 域名")
    selector.add_argument("--all-due", action="store_true", help="同步全部注册域 (daily_update 集成入口)")
    parser.add_argument("--backfill", action="store_true", help="从 data_start 全量回填")
    parser.add_argument("--resume", action="store_true", help="by_ts_code 域断点续拉 (跳过 target 已有 ts_code)")
    parser.add_argument("--start", default=None, help="覆盖起始日 YYYYMMDD")
    parser.add_argument("--end", default=None, help="覆盖结束日 YYYYMMDD")
    parser.add_argument("--drain", action="store_true",
                        help="日历 gap 重放 (by_trade_date 域): 应有交易日 − 实有 = 缺口逐日重拉")
    parser.add_argument(
        "--max-dates",
        type=int,
        default=None,
        help="drain 的单次日期上限",
    )
    parser.add_argument(
        "--trigger-mode",
        choices=("manual", "automatic"),
        default="manual",
        help=(
            "sync authorization mode: manual (default; UI/chunkyctl) skips "
            "same_day_at clock wait on open trading days; automatic keeps "
            "the consumer publication clock for scheduled paths"
        ),
    )
    transport = parser.add_mutually_exclusive_group(required=False)
    transport.add_argument(
        "--land-only",
        action="store_true",
        help=(
            "S1: daily|stock_st|holders_top10|org_holding|stk_holdertrade "
            "capture→LANDING only (no canonical/accept); disclosure: "
            "--from-local-raw (all three) or provider for stk_holdertrade only"
        ),
    )
    transport.add_argument(
        "--accept-from-landing",
        action="store_true",
        help="S2: accept from LANDED batch_id (zero provider fetch)",
    )
    transport.add_argument(
        "--land-then-accept",
        action="store_true",
        help=(
            "Thin caller-only S1→S2 composition (not the default fused sync); "
            "disclosure: --from-local-raw or stk_holdertrade provider"
        ),
    )
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Required for --accept-from-landing unless single-day --start/--end",
    )
    parser.add_argument(
        "--from-local-raw",
        action="store_true",
        help=(
            "With --land-only/--land-then-accept: materialize landing from "
            "local legacy/raw tables (explicit acquire→landing; never "
            "raw→canonical). Required for disclosure domains."
        ),
    )
    return parser.parse_args(argv)


def _cli_transport_mode(args: argparse.Namespace) -> str | None:
    if getattr(args, "land_only", False):
        return "land_only"
    if getattr(args, "accept_from_landing", False):
        return "accept_from_landing"
    if getattr(args, "land_then_accept", False):
        return "land_then_accept"
    return None


def _cli_skips_provider_authorization(args: argparse.Namespace) -> bool:
    """Accept-from-landing and local-raw land paths must not probe TuShare."""

    transport = _cli_transport_mode(args)
    if transport == "accept_from_landing":
        return True
    if transport in {"land_only", "land_then_accept"} and getattr(
        args, "from_local_raw", False
    ):
        return True
    return False


def _selected_domains(
    args: argparse.Namespace, registry: dict[str, Any]
) -> list[str]:
    return (
        automatic_domains(registry)
        if args.all_due
        else ([args.domain] if args.domain else [])
    )


def automatic_domains(registry: dict[str, Any]) -> list[str]:
    """Return the exact domain set used by the legacy all-due pipeline."""

    domains = registry.get("domains")
    if not isinstance(domains, Mapping):
        raise ValueError("sync_registry.yaml: domains must be a mapping")
    selected: list[str] = []
    for domain, entry in domains.items():
        if not isinstance(entry, Mapping):
            raise ValueError(
                f"sync_registry.yaml: domain entry {domain!r} must be a mapping"
            )
        if entry.get("sync_policy") != "on_demand":
            selected.append(str(domain))
    return selected


def _preflight_cli_request_shape(
    args: argparse.Namespace,
    registry: dict[str, Any],
    domains: list[str],
    *,
    now: datetime | None = None,
) -> None:
    """Reject impossible explicit requests before locks, adapters or DB probes."""

    transport = _cli_transport_mode(args)
    if transport is not None:
        from services.data_sources.disclosure_transport import (
            DISCLOSURE_TRANSPORT_DOMAINS,
        )

        if args.drain or args.backfill or args.resume or args.all_due:
            raise SyncWindowError(
                f"--{transport.replace('_', '-')} cannot combine with "
                "--drain/--backfill/--resume/--all-due"
            )
        allowed = {"daily", "stock_st"} | set(DISCLOSURE_TRANSPORT_DOMAINS)
        if len(domains) != 1 or domains[0] not in allowed:
            raise SyncWindowError(
                f"--{transport.replace('_', '-')} requires exactly one "
                f"--domain {'|'.join(sorted(allowed))}"
            )
        disclosure = domains[0] in DISCLOSURE_TRANSPORT_DOMAINS
        if getattr(args, "from_local_raw", False) and transport not in {
            "land_only",
            "land_then_accept",
        }:
            raise SyncWindowError(
                "--from-local-raw only valid with --land-only or --land-then-accept"
            )
        if disclosure and transport in {"land_only", "land_then_accept"}:
            if not getattr(args, "from_local_raw", False):
                raise SyncWindowError(
                    "disclosure --land-only/--land-then-accept requires "
                    "--from-local-raw (local legacy acquire; provider mass "
                    "dump banned)"
                )
        if transport == "accept_from_landing":
            if disclosure and not args.batch_id:
                raise SyncWindowError(
                    "disclosure --accept-from-landing requires --batch-id"
                )
            if not args.batch_id and (args.start is None or args.end is None):
                raise SyncWindowError(
                    "--accept-from-landing requires --batch-id or single-day "
                    "--start/--end"
                )
        elif args.start is None or args.end is None:
            raise SyncWindowError(
                f"--{transport.replace('_', '-')} requires --start and --end"
            )

    if args.drain and (
        args.start is not None
        or args.end is not None
        or args.backfill
        or args.resume
    ):
        raise SyncWindowError(
            "--drain cannot be combined with --start/--end/--backfill/--resume"
        )
    for domain in domains:
        spec = domain_spec(registry, domain)
        try:
            fetch_socket_timeout_seconds(spec)
        except ValueError as exc:
            raise SyncWindowError(
                f"domain={domain} provider timeout invalid: {exc}"
            ) from exc
        if (
            not args.drain
            and transport is None
            and str(spec.get("batch_mode")) == "by_ts_code"
            and spec.get("sync_policy") == "on_demand"
            and (args.start is None or args.end is None)
        ):
            raise SyncWindowError(
                f"domain={domain} sync_policy=on_demand requires both --start and --end"
            )
    if args.max_dates is not None and not args.drain:
        raise SyncWindowError("--max-dates is only valid for --drain")
    if args.start is None and args.end is None:
        return
    from zoneinfo import ZoneInfo

    today = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).strftime("%Y%m%d")
    resolve_operation_window(
        DomainEligibility(today, False, "wall_clock_preflight"),
        requested_start=args.start,
        requested_end=args.end,
    )
    for domain in domains:
        spec = domain_spec(registry, domain)
        if str(spec.get("batch_mode")) == "full_refresh":
            raise SyncWindowError(
                f"domain={domain} batch_mode=full_refresh does not accept date bounds"
            )


def _preflight_explicit_operation_windows(
    args: argparse.Namespace,
    registry: dict[str, Any],
    domains: list[str],
) -> None:
    """Resolve explicit horizons once, after the read-only calendar hard gate."""

    if args.start is None and args.end is None:
        return
    mode = _normalize_trigger_mode(getattr(args, "trigger_mode", "manual"))
    for domain in domains:
        spec = domain_spec(registry, domain)
        fixed = dict(spec.get("fixed_params") or {})
        batch_mode = str(spec["batch_mode"])
        planned_start = args.start
        planned_end = args.end
        if batch_mode in ("by_ts_code", "by_code_list"):
            planned_start = planned_start or fixed.get("start_date")
            planned_end = planned_end or fixed.get("end_date")
        resolve_operation_window(
            eligible_end_date(spec, trigger_mode=mode),
            requested_start=planned_start,
            requested_end=planned_end,
        )


def _main_unlocked(
    args: argparse.Namespace | None = None,
    registry: dict[str, Any] | None = None,
    domains: list[str] | None = None,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    args = args or _parse_cli_args()

    reg = registry if registry is not None else load_registry()
    selected = domains or _selected_domains(args, reg)
    preflight_execution_policies(reg, selected)
    preflight_formal_population_scopes(reg, selected)
    if registry is None:
        _calendar_preflight(selected)

    trigger_mode = _normalize_trigger_mode(getattr(args, "trigger_mode", "manual"))
    if args.drain:
        from services.data_sources.sources.tushare import TuShareAuthorizationError

        results = []
        _total = len(selected)
        for _idx, d in enumerate(selected, 1):
            # Live per-domain progress on stderr (stdout stays pure final JSON).
            # The pipeline drain wrapper streams stderr into the parent log so
            # the workbench「数据更新」shows domain-by-domain progress instead of a
            # single static line for the whole ~40 min drain (owner 2026-07-22).
            print(f"[drain {_idx}/{_total}] domain={d} …", file=sys.stderr, flush=True)
            try:
                res = drain_domain(
                    d,
                    registry=reg,
                    max_dates=args.max_dates,
                    trigger_mode=trigger_mode,
                )
                # by_report_period 域 (十大股东/财报 by_ts_code): 不再"归专门调度"漏掉 — 走增量 run_domain,
                # _by_ts_code_batches 按交易日历最新应披露期跳过已最新股, 只抓缺新期的股 (修谄媚死: 日常流停更财报/股东)。
                ts_code_incremental = (
                    res.get("status") == "unsupported"
                    and res.get("batch_mode") == "by_ts_code"
                    and (reg["domains"].get(d) or {}).get("increment_mode") == "by_report_period"
                )
                fallback_incremental = (
                    res.get("status") == "drain_inapplicable"
                    or (res.get("status") == "unsupported"
                        # 2026-06-28: by_code_list (index_dailybasic/index_daily 基准指数清单) drain unsupported,
                        #   缺它则零自动同步 (静默停更同型); 走 run_domain watermark 增量补缺。
                        and res.get("batch_mode") in ("by_date_range", "full_refresh", "by_ann_date", "by_code_list"))
                    or ts_code_incremental
                )
                if fallback_incremental:
                    # 非按日域 + allow_empty 域无 gap 语义 → 增量 run_domain (watermark/报告期 起点)。
                    # 复审 HIGH: drain-only 接线下这些域否则零自动同步 = 静默停更同型复发。
                    # by_ts_code 无 increment_mode (如 stk_factor_pro 日频全市场) 仍 unsupported, 归专门调度。
                    res = run_domain(d, registry=reg, trigger_mode=trigger_mode)
                    res["mode"] = "incremental_fallback"
                results.append(res)
            except QuotaExhaustedError as exc:
                # 熔断: 配额墙 = 账户级, 续跑其余域只会延长冷却 → 停全链 (区别于单域写锁错)
                log.error("配额熔断停链 (drain): %s — 剩 %d 域不跑", exc, len(selected) - selected.index(d) - 1)
                halt = {"domain": d, "status": "quota_halt", "error": str(exc)[:200]}
                results.append(halt)
                break
            except TuShareAuthorizationError:
                # 运行中授权失效仍是账户级硬阻断；必须穿透到 main 稳定返回 exit 3。
                raise
            except Exception as exc:  # noqa: BLE001 — 单域异常 (如写锁被占) 不挡其余域, 显式入结果非静默
                results.append({"domain": d, "status": "error", "error": str(exc)[:200]})
        print(json.dumps(results, ensure_ascii=False, indent=1))
        if any(r.get("status") == "quota_halt" for r in results):
            return 2  # 配额墙专用退出码 (调用方区分'信道墙'与'数据失败')
        bad = any(
            r.get("status") in ("partial", "error", "unsupported")
            or r.get("failed_batches")
            for r in results
        )
        return 1 if bad else 0

    transport = _cli_transport_mode(args)
    results = []
    for d in selected:
        try:
            if transport is not None:
                results.append(
                    _run_security_day_transport_window(
                        d,
                        transport=transport,
                        start=args.start,
                        end=args.end,
                        batch_id=getattr(args, "batch_id", None),
                        from_local_raw=bool(
                            getattr(args, "from_local_raw", False)
                        ),
                        registry=reg,
                        trigger_mode=trigger_mode,
                    )
                )
                continue
            run_kwargs = {
                "backfill": args.backfill,
                "start": args.start,
                "end": args.end,
                "resume": args.resume,
                "registry": reg,
                "trigger_mode": trigger_mode,
            }
            if args.max_dates is not None:
                run_kwargs["max_dates"] = args.max_dates
            results.append(run_domain(d, **run_kwargs))
        except QuotaExhaustedError as exc:
            log.error("配额熔断停链: %s — 剩 %d 域不跑", exc, len(selected) - selected.index(d) - 1)
            halt = {"domain": d, "status": "quota_halt", "error": str(exc)[:200]}
            results.append(halt)
            break
        except SyncWindowError as exc:
            results.append(
                {
                    "domain": d,
                    "status": "error",
                    "failed_batches": 1,
                    "error": str(exc)[:500],
                    "transport": transport,
                }
            )
            break
    print(json.dumps(results, ensure_ascii=False, indent=1))
    if any(r.get("status") == "quota_halt" for r in results):
        return 2
    return 0 if all(r.get("failed_batches") == 0 for r in results) else 1


def main() -> int:
    """CLI 写入口：与 full pipeline/stage/manual API 共用唯一 advisory lock。"""
    from services.data_sources.sources.tushare import TuShareAuthorizationError
    from services.writer_lock import WriterLockBusyError, writer_lock

    # help/参数错误必须在 writer lock 与 provider 探针之前完成。
    args = _parse_cli_args()
    # E0: disclosure transport is registry-orthogonal (holders/org are aif10
    # writers; stk uses tushare_raw). Short-circuit before sync_registry
    # domain_spec / provider authorization probes.
    transport = _cli_transport_mode(args)
    if transport is not None and args.domain:
        from services.data_sources.disclosure_transport import (
            DISCLOSURE_TRANSPORT_DOMAINS,
        )

        if args.domain in DISCLOSURE_TRANSPORT_DOMAINS:
            # Registry-orthogonal shape checks only (no domain_spec / provider).
            # Must reject future windows before writer_lock / DB I/O (Tier0).
            try:
                _preflight_disclosure_cli_shape(args, transport=transport)
            except SyncWindowError as exc:
                print(
                    json.dumps(
                        {
                            "status": "error",
                            "domain": args.domain,
                            "transport": transport,
                            "error": str(exc)[:500],
                        },
                        ensure_ascii=False,
                    )
                )
                return 1
            try:
                with writer_lock("sync_runner"):
                    result = _run_disclosure_transport_window(
                        args.domain,
                        transport=transport,
                        start=args.start,
                        end=args.end,
                        batch_id=getattr(args, "batch_id", None),
                        from_local_raw=bool(
                            getattr(args, "from_local_raw", False)
                        ),
                    )
            except WriterLockBusyError as exc:
                print(
                    json.dumps(
                        {
                            "status": "writer_lock_busy",
                            "error": str(exc)[:300],
                        },
                        ensure_ascii=False,
                    )
                )
                return 5
            except SyncWindowError as exc:
                print(
                    json.dumps(
                        {
                            "status": "error",
                            "domain": args.domain,
                            "transport": transport,
                            "error": str(exc)[:500],
                        },
                        ensure_ascii=False,
                    )
                )
                return 1
            print(json.dumps([result], ensure_ascii=False, indent=1))
            return 0 if int(result.get("failed_batches") or 0) == 0 else 1

    reg = load_registry()
    domains = _selected_domains(args, reg)
    try:
        preflight_execution_policies(reg, domains)
        preflight_formal_population_scopes(reg, domains)
        _preflight_cli_request_shape(args, reg, domains)
        _calendar_preflight(domains)
        _preflight_explicit_operation_windows(args, reg, domains)
    except ExecutionPolicyError as exc:
        print(json.dumps({
            "status": "execution_blocked",
            "domain": exc.domain,
            "mode": exc.mode,
            "reason": exc.reason,
            "error": str(exc),
        }, ensure_ascii=False))
        return 6
    except PopulationScopeExecutionError as exc:
        print(json.dumps({
            "status": "population_scope_blocked",
            "domain": exc.domain,
            "reason": exc.reason,
            "error": str(exc),
        }, ensure_ascii=False))
        return 7
    except SyncWindowError as exc:
        print(json.dumps({
            "status": "operation_window_blocked",
            "reason": str(exc),
        }, ensure_ascii=False))
        return 5
    except CalendarFoundationError as exc:
        print(json.dumps({
            "status": "calendar_blocked",
            "reason": str(exc),
        }, ensure_ascii=False))
        return 4
    try:
        with writer_lock(owner="sync_runner") as lease:
            try:
                if not _cli_skips_provider_authorization(args):
                    _authorization_preflight(lease, registry=reg)
                return _main_unlocked(args, reg, domains)
            except TuShareAuthorizationError as exc:
                payload = {
                    "status": "authorization_blocked",
                    "reason": exc.reason,
                }
                print(json.dumps(payload, ensure_ascii=False))
                return 3
            except CalendarFoundationError as exc:
                print(json.dumps({
                    "status": "calendar_blocked",
                    "reason": str(exc),
                }, ensure_ascii=False))
                return 4
            except SyncWindowError as exc:
                print(json.dumps({
                    "status": "operation_window_blocked",
                    "reason": str(exc),
                }, ensure_ascii=False))
                return 5
    except WriterLockBusyError as exc:
        log.error("%s", exc)
        print(json.dumps({"status": "writer_busy", "error": str(exc)}, ensure_ascii=False))
        return 2


def _authorization_preflight(
    lease, *, registry: dict[str, Any] | None = None
) -> dict[str, Any]:
    """直跑探一次 user()；受验证的父 lease 子进程复用同一授权证明。"""
    from services.data_sources.sync_preconditions import authorization_preflight

    return authorization_preflight(
        lease=lease,
        adapter_factory=_adapter,
        registry=registry if registry is not None else load_registry(),
    )


def _calendar_preflight(domains: list[str]) -> None:
    """所有日期驱动直跑入口复用生产日历硬门；仅 trade_cal 单域可 bootstrap。"""
    from services.data_sources.sync_preconditions import ensure_calendar_foundation

    ensure_calendar_foundation(domains)


if __name__ == "__main__":
    sys.exit(main())
